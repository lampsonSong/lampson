"""Agent 主循环：接收用户输入，调用 LLM，处理 tool calling，返回最终回复。

需要技能/项目时由语义检索在规划或 Fast Path 中注入匹配全文，不预载 skill 目录。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

from src.core.adapters import BaseModelAdapter
from src.core.llm import LLMClient
from src.core import tools as tool_registry
from src.core.compaction import CompactionConfig, CompactionResult, apply_compaction
from src.planning.steps import Plan, PlanStatus

if TYPE_CHECKING:
    from src.skills.manager import Skill

logger = logging.getLogger(__name__)


_DEFAULT_MAX_TOOL_ROUNDS = 30


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        adapter: BaseModelAdapter,
        compaction_config: CompactionConfig | None = None,
        max_tool_rounds: int | None = None,
    ) -> None:
        self.llm = llm
        self.adapter = adapter
        self._tools = tool_registry.get_all_schemas()
        self.skills: dict[str, "Skill"] = {}
        self._core_memory: str = ""
        self._skills_context: str = ""
        self.skill_index: Any = None
        self.project_index: Any = None
        self.retrieval_config: dict[str, Any] = {}
        self.last_total_tokens: int = 0
        self.last_stop_reason: str | None = None
        self._fast_path_tool_count: int = 0

        self._compaction_config: CompactionConfig | None = compaction_config
        self.max_tool_rounds: int = max_tool_rounds or _DEFAULT_MAX_TOOL_ROUNDS
        self.current_plan: Plan | None = None

        # 中间过程回调：由 listener 注入，用于实时发送工具调用状态
        self.progress_callback: Callable[[str], None] | None = None
        # 工具调用计数器（跨多次 tool_loop 累计，用于判断是否应继续循环）
        self._total_tool_calls: int = 0

    def refresh_tools(self) -> None:
        """重新加载工具列表（外部注册新工具后调用）。"""
        self._tools = tool_registry.get_all_schemas()

    def set_context(self, core_memory: str = "") -> None:
        """设置 system prompt 上下文（启动时调用一次）。"""
        self._core_memory = core_memory
        self.llm.set_system_context(core_memory=core_memory)

    def switch_llm(
        self,
        new_llm: LLMClient,
        new_adapter: BaseModelAdapter,
        compaction_config: CompactionConfig | None = None,
    ) -> None:
        """切换底层 LLM 与适配器，并迁移对话历史。

        Args:
            new_llm: 新的 LLM 客户端。
            new_adapter: 新的模型适配器。
            compaction_config: 新模型的压缩配置（不同模型 context_window 不同）。
        """
        old_llm = self.llm
        new_llm.set_system_context(core_memory=self._core_memory)
        new_llm.migrate_from(old_llm)
        self.llm = new_llm
        self.adapter = new_adapter
        if compaction_config is not None:
            self._compaction_config = compaction_config

    def _inject_skill(self, user_input: str) -> str | None:
        """历史兼容占位；技能全文由 retrieve_for_plan 注入。"""
        return None

    def _run_tool_loop(self) -> str:
        self._fast_path_tool_count = 0

        while True:
            for round_num in range(self.max_tool_rounds):
                try:
                    response = self.adapter.chat(self.llm.messages, tools=self._tools)
                except RuntimeError as e:
                    return f"[LLM 错误] {e}"

                if response.usage:
                    self.last_total_tokens = response.usage.total_tokens

                self.llm.messages.append(
                    response.choices[0].message.model_dump(exclude_none=True)
                )

                parsed = self.adapter.parse_response(response)
                self.last_stop_reason = parsed.finish_reason

                if not parsed.tool_calls:
                    logger.info(f"tool_loop round {round_num+1}: finish (no tool_calls), content_len={len(parsed.content or '')}")
                    return parsed.content or ""

                for tc in parsed.tool_calls:
                    logger.info(f"tool_loop round {round_num+1}: dispatch {tc.name}({tc.raw_arguments[:200]})")
                    result = tool_registry.dispatch(tc.name, tc.raw_arguments)
                    self._fast_path_tool_count += 1

                    # 实时通知 listener：一个工具调用完成
                    self._on_tool_progress(round_num + 1, tc.name, tc.raw_arguments, result)

                    tool_msg = self.adapter.format_tool_result(tc.id, result)
                    self.llm.messages.append(tool_msg)

            # ── 达到最大轮数：总结现状，清空计数器，继续解决 ──
            logger.info(f"tool_loop: reached max_tool_rounds ({self.max_tool_rounds}), summarizing and continuing")
            summary_prompt = (
                "你已达到本轮工具调用上限（"
                + str(self.max_tool_rounds)
                + " 轮）。请简洁总结当前进展："
                "1) 已经完成了什么；2) 还在尝试什么；3) 下一步计划。"
                "直接回复内容，不要调用任何工具。"
            )
            self.llm.messages.append({"role": "user", "content": summary_prompt})
            try:
                response = self.adapter.chat(self.llm.messages, tools=None)
                self.llm.messages.append(
                    response.choices[0].message.model_dump(exclude_none=True)
                )
                # 检查 LLM 是否认为任务已完成（回复中包含"完成了"、"结束"等意图）
                content = (response.choices[0].message.content or "").strip().lower()
                done_indicators = ["已完成", "任务完成", "搞定了", "完成了所有", "all done", "done!", "completed"]
                if any(ind in content for ind in done_indicators):
                    logger.info("tool_loop: LLM indicated task done after summary")
                    return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"summary call failed after max iterations: {e}")

            # 追加继续提示，让 LLM 继续解决
            self.llm.messages.append({
                "role": "user",
                "content": "请继续解决上述问题。如果已解决请直接回复结论，不需要再调用工具。",
            })

    def _on_tool_progress(self, round_num: int, tool_name: str, args: str, result: str) -> None:
        """每个工具调用完成后实时通知 listener 更新进度卡片。"""
        if not self.progress_callback:
            return
        try:
            args_preview = args[:80] + ("..." if len(args) > 80 else "")
            result_preview = result[:120] + ("..." if len(result) > 120 else "")
            self.progress_callback({
                "type": "tool_progress",
                "round": round_num,
                "tool": tool_name,
                "args_preview": args_preview,
                "result_preview": result_preview,
            })
        except Exception:
            pass

    def run(self, user_input: str) -> str:
        """处理一轮用户输入，返回最终回复文本。"""
        self.llm.add_user_message(user_input)
        result = self._run_tool_loop()

        tool_count = getattr(self, "_fast_path_tool_count", 0)
        reflection_hints = self._maybe_reflect(
            goal=user_input,
            is_fast_path=True,
            tool_call_count=tool_count,
        )
        if reflection_hints:
            result += "\n\n" + "\n".join(reflection_hints)
        return result

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
        """估算当前 messages 的 token 总数（粗略：UTF-8 字节数 / 4）。"""
        try:
            serialized = json.dumps(self.llm.messages, ensure_ascii=False)
            return len(serialized.encode("utf-8")) // 4
        except Exception:
            return 0

    def maybe_compact(
        self,
        session_store: Any = None,
        session_id: str = "",
    ) -> CompactionResult | None:
        """检查并执行上下文压缩。

        Args:
            session_store: session_store 模块，用于写入 segment_boundary。
            session_id: 当前会话 id，需与 JSONL 一致；空字符串则仍执行压缩逻辑但不落 segment 边界。
        """
        if self._compaction_config is None:
            return None

        if (
            self.current_plan is not None
            and self.current_plan.status == PlanStatus.executing
        ):
            return None

        msg_count = sum(1 for m in self.llm.messages if m.get("role") != "system")
        estimated_tokens = self._estimate_context_tokens()

        try:
            return apply_compaction(
                agent_llm=self.llm,
                config=self._compaction_config,
                message_count=msg_count,
                estimated_tokens=estimated_tokens,
                stop_reason=self.last_stop_reason,
                session_id=session_id,
                session_store=session_store,
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

    def _maybe_reflect(
        self,
        goal: str = "",
        plan: Plan | None = None,
        is_fast_path: bool = False,
        tool_call_count: int = 0,
        intent: str = "",
    ) -> list[str]:
        """任务完成后触发反思，自动沉淀 skill 或 project 信息。"""
        from src.core.reflection import (
            should_reflect,
            reflect_and_learn,
            execute_learnings,
            format_execution_summary,
        )

        if not should_reflect(
            plan=plan,
            is_fast_path=is_fast_path,
            tool_call_count=tool_call_count,
            intent=intent,
        ):
            return []

        if plan is not None:
            exec_summary = format_execution_summary(plan)
        else:
            exec_summary = f"Fast Path 任务，调用了 {tool_call_count} 个工具"

        try:
            learnings = reflect_and_learn(
                goal=goal,
                execution_summary=exec_summary,
                llm_client=self.llm,
            )
            if not learnings:
                return []
            return execute_learnings(learnings)
        except Exception as e:
            logger.warning(f"反思过程异常: {e}")
            return []
