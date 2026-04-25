"""Agent 主循环：接收用户输入，调用 LLM，处理 tool calling，返回最终回复。

Skills 使用索引模式（skills index 已在 system prompt 中）。
LLM 需要某 skill 时，通过 skill_view(name) 工具按需加载。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

from src.core.llm import LLMClient
from src.core import tools as tool_registry
from src.core.compaction import CompactionConfig, CompactionResult, apply_compaction
from src.planning.planner import Planner, PlanParseError
from src.planning.executor import Executor
from src.planning.steps import IntentResult, Plan, PlanStatus
from src.planning.prompts import build_context_from_history

if TYPE_CHECKING:
    from src.skills.manager import Skill

logger = logging.getLogger(__name__)


_DEFAULT_MAX_TOOL_ROUNDS = 30

_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call:\s*(\w+)\s*>\s*(.*?)\s*</tool_call:\s*\1\s*>",
    re.DOTALL,
)


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        compaction_config: CompactionConfig | None = None,
        max_tool_rounds: int | None = None,
    ) -> None:
        self.llm = llm
        self._tools = tool_registry.get_all_schemas()
        self.skills: dict[str, "Skill"] = {}
        self._core_memory: str = ""
        self._skills_context: str = ""
        self._tools_prompt_injected: bool = False
        self.last_total_tokens: int = 0  # 最近一次 LLM 调用的 total_tokens
        self.last_stop_reason: str | None = None  # 最近一次 LLM 的 stop reason

        # 压缩配置（由外部注入，Agent 自己不读配置文件）
        self._compaction_config: CompactionConfig | None = compaction_config

        # 工具调用最大轮次
        self.max_tool_rounds: int = max_tool_rounds or _DEFAULT_MAX_TOOL_ROUNDS

        # 规划状态
        self.current_plan: Plan | None = None
        self._planner = Planner(llm=llm, tool_schemas=self._tools)
        self._executor = Executor(llm=llm, planner=self._planner)

    def refresh_tools(self) -> None:
        """重新加载工具列表（外部注册新工具后调用）。"""
        self._tools = tool_registry.get_all_schemas()

    def set_context(self, core_memory: str = "") -> None:
        """设置 system prompt 上下文（启动时调用一次）。"""
        self._core_memory = core_memory
        self._tools_prompt_injected = False
        self.llm.set_system_context(core_memory=core_memory)

    def switch_llm(self, new_llm: LLMClient) -> None:
        """切换底层 LLM 客户端，同步更新所有内部引用。

        迁移当前对话历史到新 client（保留 system prompt 之外的消息），
        并同步更新 planner 和 executor 的 llm 引用。
        """
        old_llm = self.llm
        # 新 client 需要先设置自己的 system prompt（不同模型可能有不同适配层）
        new_llm.set_system_context(core_memory=self._core_memory)
        # 迁移对话历史
        new_llm.migrate_from(old_llm)
        # 更新所有引用
        self.llm = new_llm
        self._planner.llm = new_llm
        self._executor.llm = new_llm
        # 工具 prompt 需要重新注入（因为新 messages 里没有 tools prompt）
        self._tools_prompt_injected = False

    def _inject_skill(self, user_input: str) -> str | None:
        """匹配技能并返回技能全文（已弃用，改用 skill_view 工具按需加载）。"""
        return None  # 不再自动注入，LLM 通过 skill_view 按需加载

    def _inject_tools_prompt(self) -> None:
        """在 messages 中注入工具描述（prompt-based 模式，只注入一次）。"""
        if self._tools_prompt_injected:
            return
        tools_prompt = LLMClient.format_tools_prompt(self._tools)
        self.llm.messages.append({
            "role": "system",
            "content": tools_prompt,
        })
        self._tools_prompt_injected = True

    def _run_native(self) -> str:
        """原生 tool calling 主循环。"""
        for _ in range(self.max_tool_rounds):
            try:
                response = self.llm.chat(tools=self._tools)
            except RuntimeError as e:
                return f"[LLM 错误] {e}"

            # 记录 token 用量
            if response.usage:
                self.last_total_tokens = response.usage.total_tokens

            choice = response.choices[0]
            finish_reason = choice.finish_reason
            self.last_stop_reason = finish_reason
            message = choice.message

            if finish_reason == "stop" or not message.tool_calls:
                return message.content or ""

            if finish_reason in ("tool_calls", "function_call") or message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    arguments = tool_call.function.arguments
                    result = tool_registry.dispatch(tool_name, arguments)
                    self.llm.add_tool_result(tool_call.id, result)

        return "[错误] 工具调用轮次超过限制，请重新提问。"

    def _run_prompt_based(self) -> str:
        """prompt-based tool calling 主循环。"""
        for _ in range(self.max_tool_rounds):
            try:
                response = self.llm.chat(tools=self._tools)
            except RuntimeError as e:
                return f"[LLM 错误] {e}"

            # 记录 token 用量
            if response.usage:
                self.last_total_tokens = response.usage.total_tokens

            content = response.choices[0].message.content or ""
            match = _TOOL_CALL_PATTERN.search(content)


            if not match:
                self.last_stop_reason = "stop"
                return content

            tool_name = match.group(1).strip()
            raw_args = match.group(2).strip()

            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {}

            result = tool_registry.dispatch(tool_name, arguments)
            self.llm.messages.append({
                "role": "user",
                "content": f"<tool_result:{tool_name}>\n{result}\n</tool_result:{tool_name}>",
            })

        return "[错误] 工具调用轮次超过限制，请重新提问。"

    def _reply_without_tools(self, user_input: str, phase1: IntentResult) -> str:
        """阶段一判断无需工具时，用直接回复或主 LLM 生成自然语言回答。"""
        if phase1.direct_reply and phase1.direct_reply.strip():
            text = phase1.direct_reply.strip()
            self.llm.messages.append({"role": "assistant", "content": text})
            return text
        try:
            response = self.llm.chat(tools=None)
        except RuntimeError as e:
            return f"[LLM 错误] {e}"
        if response.usage:
            self.last_total_tokens = response.usage.total_tokens
        return response.choices[0].message.content or ""

    def confirm_and_execute(self) -> str:
        """用户确认后执行当前计划。若取消计划请用 cancel_plan()。"""
        # // FIX-2
        if self.current_plan is None:
            return "[错误] 没有待执行的计划"
        plan = self.current_plan
        if plan.status == PlanStatus.created:
            plan.confirm()
        result = self._executor.execute(plan)
        self.current_plan = None
        return result

    def cancel_plan(self) -> str:
        """取消当前计划。"""
        # // FIX-2
        if self.current_plan is None:
            return "[提示] 没有待取消的计划"
        summary = self.current_plan.plan_summary
        self.current_plan.cancel()
        msg = f"已取消计划：{summary}"
        self.current_plan = None
        return msg

    def run(self, user_input: str) -> str:
        """处理一轮用户输入，返回最终回复文本。

        v2：先阶段一分类 → 不需要工具则直接答；需要工具时可选信息收集 →
        阶段二 plan_v2 → 执行器（含重试与 replan）。
        若分类或规划解析失败，回退到 v1/单轮模式。
        """
        if not self.llm.supports_native_tool_calling:
            self._inject_tools_prompt()

        self.llm.add_user_message(user_input)

        # 构建上下文（供规划器使用，不污染主对话）
        context = build_context_from_history(self.llm.get_history(), max_chars=1500)

        try:
            intent = self._planner.classify(goal=user_input, context=context)

            if not intent.needs_tools:
                self.current_plan = None
                return self._reply_without_tools(user_input, intent)

            # // FIX-1: Fast path — 高置信度且不缺信息时直接走原生 / prompt 工具循环，跳过 plan_v2
            if intent.confidence >= 0.8 and not intent.missing_info:
                logger.debug(
                    f"Fast path: intent={intent.intent}, confidence={intent.confidence}"
                )
                self.current_plan = None
                if self.llm.supports_native_tool_calling:
                    return self._run_native()
                else:
                    return self._run_prompt_based()

            exploration_results = ""
            if (
                intent.missing_info
                and intent.initial_plan is not None
                and len(intent.initial_plan.steps) > 0
            ):
                ip = intent.initial_plan
                ip.goal = user_input
                ex_out = self._executor.execute(ip, synthesize=False, record_to_history=False)
                exploration_results = ex_out

            plan = self._planner.plan_v2(
                goal=user_input,
                context=context,
                phase1_result=intent,
                exploration_results=exploration_results,
            )
            self.current_plan = plan

            if plan.is_single_step:
                logger.debug(f"1-step plan: {plan.steps[0].action}")
            else:
                logger.info(
                    f"计划生成: {plan.plan_summary} ({len(plan.steps)} 步)"
                )

            # // FIX-2: 展示计划并等待用户确认后再执行（由 CLI 调 confirm_and_execute / cancel_plan）
            plan_display = plan.format_for_display()
            return f"{plan_display}\n\n请确认是否执行此计划？"

        except PlanParseError as e:
            logger.warning(f"规划失败，回退到单轮模式: {e}")
            self.current_plan = None
            # 回退：用原有逻辑
            if self.llm.supports_native_tool_calling:
                return self._run_native()
            else:
                return self._run_prompt_based()

        except Exception as e:
            logger.exception(f"规划异常，回退到单轮模式")
            self.current_plan = None
            if self.llm.supports_native_tool_calling:
                return self._run_native()
            else:
                return self._run_prompt_based()

    def get_conversation_text(self) -> str:
        """导出当前对话的可读文本，供会话摘要生成使用。"""
        lines = []
        for msg in self.llm.get_history():
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system" or not content:
                continue
            prefix = "用户" if role == "user" else "Lampson"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    def _estimate_context_tokens(self) -> int:
        """估算当前 messages 的 token 总数（粗略：UTF-8 字节数 ÷ 4）。"""
        try:
            serialized = json.dumps(self.llm.messages, ensure_ascii=False)
            return len(serialized.encode("utf-8")) // 4
        except Exception:
            # 估算失败时保守返回 0（不触发压缩）
            return 0

    def maybe_compact(self) -> CompactionResult | None:
        """检查并执行上下文压缩。

        由外部调用方（cli.py / listener.py）在每轮 run() 之后调用。
        内部处理所有判断逻辑：
        - 压缩未配置 → 跳过
        - plan 正在执行中 → 跳过
        - token 未达阈值 → 跳过

        Returns:
            CompactionResult 如果执行了压缩，None 如果跳过。
        """
        if self._compaction_config is None:
            return None

        # 规划执行中不触发压缩
        if (
            self.current_plan is not None
            and self.current_plan.status == PlanStatus.executing
        ):
            return None

        # 用实际 context 大小判断是否触发压缩，而非依赖 last_total_tokens
        # （last_total_tokens 只记录最后一次 LLM 调用的用量，不反映整个 context size）
        actual_tokens = self._estimate_context_tokens()

        try:
            return apply_compaction(
                agent_llm=self.llm,
                config=self._compaction_config,
                last_total_tokens=actual_tokens,
                stop_reason=self.last_stop_reason,
            )
        except Exception as e:
            logger.warning(f"压缩异常: {e}")
            return None

    def generate_session_summary(self) -> str:
        """让 LLM 生成本次会话摘要，用于写入 sessions/ 目录。"""
        history = self.get_conversation_text()
        if not history.strip():
            return ""

        summary_prompt = (
            "请用 3-5 句话总结以下对话的主要内容和结论，供以后参考：\n\n"
            f"{history}"
        )
        try:
            temp_client = LLMClient(
                api_key=self.llm.client.api_key,
                base_url=str(self.llm.client.base_url),
                model=self.llm.model,
            )
            temp_client.set_system_context()
            temp_client.add_user_message(summary_prompt)
            response = temp_client.chat()
            return response.choices[0].message.content or ""
        except Exception:
            return history[:500]
