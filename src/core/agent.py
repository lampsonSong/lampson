"""Agent 主循环：接收用户输入，调用 LLM，处理 tool calling，返回最终回复。

需要技能/项目时由语义检索在规划或 Fast Path 中注入匹配全文，不预载 skill 目录。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

from src.core.adapters import BaseModelAdapter
from src.core.interrupt import AgentInterrupted
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
        fallback_models: list[tuple[LLMClient, BaseModelAdapter]] | None = None,
    ) -> None:
        self.llm = llm
        self.adapter = adapter
        self._tools = tool_registry.get_all_schemas()
        self.skills: dict[str, "Skill"] = {}
        self.skill_index: Any = None
        self.project_index: Any = None
        self.retrieval_config: dict[str, Any] = {}
        self.last_total_tokens: int = 0
        self.last_stop_reason: str | None = None
        self._fast_path_tool_count: int = 0

        self._compaction_config: CompactionConfig | None = compaction_config
        self.max_tool_rounds: int = max_tool_rounds or _DEFAULT_MAX_TOOL_ROUNDS
        self.current_plan: Plan | None = None
        self.fallback_models: list[tuple[LLMClient, BaseModelAdapter]] = fallback_models or []
        # 压缩操作锁：防止飞书等多线程场景下并发执行压缩
        self._compaction_lock = threading.Lock()

        # ── 中断机制 ───────────────────────────────────────────────────
        # volatile 标志：true = 被新消息抢占，需停止
        self._interrupted: bool = False
        # 中断锁：防止 check_interrupt 并发写 progress_summary
        self._interrupt_lock = threading.Lock()
        # 中断时的进度摘要（供 Session 保存）
        self._interrupted_summary: str = ""

        # 中间过程回调：由 listener 注入，用于实时发送工具调用状态
        self.progress_callback: Callable[[str], None] | None = None
        # 中间文本回调：由 listener 注入，用于向用户发送阶段性总结等中间内容
        self.interim_sender: Callable[[str], None] | None = None
        # 工具调用计数器（跨多次 tool_loop 累计，用于判断是否应继续循环）
        self._total_tool_calls: int = 0

    def refresh_tools(self) -> None:
        """重新加载工具列表（外部注册新工具后调用）。"""
        self._tools = tool_registry.get_all_schemas()

    def set_context(self) -> None:
        """设置 system prompt 上下文（启动时调用一次）。"""
        self.llm.set_system_context()

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
        new_llm.set_system_context()
        new_llm.migrate_from(old_llm)
        self.llm = new_llm
        self.adapter = new_adapter
        if compaction_config is not None:
            self._compaction_config = compaction_config

    def _inject_skill(self, user_input: str) -> str | None:
        """历史兼容占位；技能全文由 retrieve_for_plan 注入。"""
        return None

    # ── 中断检查 ───────────────────────────────────────────────────────

    def request_interrupt(self) -> None:
        """由 Session 调用，请求中断当前任务（设置标志位）。"""
        with self._interrupt_lock:
            self._interrupted = True

    def check_interrupt(self) -> None:
        """在 tool_loop 的检查点调用；若被抢占则抛出 AgentInterrupted。"""
        if not self._interrupted:
            return
        with self._interrupt_lock:
            if not self._interrupted:
                return
            # 标记已处理，防止重复抛出
            self._interrupted = False

        # 构建中断摘要
        summary = self._build_interrupted_summary()
        with self._interrupt_lock:
            self._interrupted_summary = summary
        raise AgentInterrupted(progress_summary=summary)

    def _build_interrupted_summary(self) -> str:
        """从当前 messages 构建中断进度摘要。"""
        try:
            lines = ["[任务被中断，以下是已完成的进度]\n"]
            tool_calls_found: list[str] = []
            last_user_query = ""

            # 找最后一条 user 消息作为原始任务
            for msg in self.llm.messages:
                role = msg.get("role", "")
                if role == "user":
                    content = msg.get("content", "")
                    # 跳过飞书 context meta
                    if "[feishu_context" in content:
                        content = content.split("]", 1)[-1].strip()
                    if content and not content.startswith("[任务被中断"):
                        last_user_query = content[:300]

            if last_user_query:
                lines.append(f"**原任务**：{last_user_query}\n")

            # 收集 tool 调用
            for msg in self.llm.messages:
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        fname = tc.get("function", {}).get("name", "")
                        args_str = tc.get("function", {}).get("arguments", "")
                        try:
                            args = json.loads(args_str)
                            # 过滤敏感/大字段
                            safe_args = {k: v for k, v in args.items()
                                         if k not in ("password", "token", "secret", "key")}
                            args_display = json.dumps(safe_args, ensure_ascii=False)[:150]
                        except Exception:
                            args_display = args_str[:150]
                        tool_calls_found.append(f"  - `{fname}`({args_display})")

            if tool_calls_found:
                lines.append(f"**已调用 {len(tool_calls_found)} 个工具**：")
                lines.extend(tool_calls_found[:10])

            plan = self.current_plan
            if plan is not None:
                lines.append(f"\n**当前计划**：{plan.description[:200]}")

            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"构建中断摘要失败: {e}")
            return "[任务被中断，进度摘要生成失败]"

    def clear_interrupt_state(self) -> None:
        """由 Session 在处理完一条消息后调用，重置中断标志。"""
        with self._interrupt_lock:
            self._interrupted = False
            self._interrupted_summary = ""

    # ── LLM 调用 ───────────────────────────────────────────────────────

    def _chat_with_fallback(self, tools=None):
        """每次调用都从主模型开始，失败时按顺序尝试 fallback。

        不会永久切换模型——fallback 成功后响应仍写回主模型的 messages，
        下次调用再次从主模型开始尝试。
        """
        # 检查中断（LLM 调用前）
        self.check_interrupt()

        # 1. 先试主模型
        try:
            return self.adapter.chat(self.llm.messages, tools=tools)
        except RuntimeError:
            if not self.fallback_models:
                raise
            logger.warning(f"主模型 {self.llm.model} 调用失败，尝试 fallback")
            self._on_model_switch(f"主模型 {self.llm.model} 失败，切换 fallback...")

        # 2. 依次试 fallback，成功即返回
        for fb_llm, fb_adapter in self.fallback_models:
            logger.warning(f"尝试 fallback: {fb_llm.model}")
            self._on_model_switch(f"尝试 {fb_llm.model}...")
            try:
                fb_llm.messages = list(self.llm.messages)
                result = fb_adapter.chat(fb_llm.messages, tools=tools)
                self._on_model_switch(f"已切换到 {fb_llm.model}")
                return result
            except Exception as e:
                logger.warning(f"fallback {fb_llm.model} 也失败: {e}")
                continue

        raise RuntimeError("所有模型（含 fallback）均调用失败")

    def _run_tool_loop(self) -> str:
        self._fast_path_tool_count = 0

        while True:
            for round_num in range(self.max_tool_rounds):
                try:
                    response = self._chat_with_fallback(tools=self._tools)
                except AgentInterrupted:
                    raise  # 直接上抛，不吞掉
                except RuntimeError as e:
                    return f"[LLM 错误] {e}"

                # 检查中断（收到 LLM 响应后、解析 tool_calls 前）
                self.check_interrupt()

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

                    # 检查中断（每个工具调用完成后）
                    self.check_interrupt()

            # ── 达到最大轮数：总结现状，清空计数器，继续解决 ──
            # 先检查中断
            self.check_interrupt()

            logger.info(f"tool_loop: reached max_tool_rounds ({self.max_tool_rounds}), summarizing and continuing")
            summary_prompt = (
                "你已达到本轮工具调用上限（"
                + str(self.max_tool_rounds)
                + " 轮）。请简洁总结当前进展："
                "1) 已经完成了什么；2) 还在尝试什么；3) 下一步计划。\n\n"
                "总结的最后一行必须是以下两行之一（只写这一行，不要加其他内容）：\n"
                "[继续] — 如果任务还没完成，需要继续调用工具\n"
                "[完成] — 如果任务已经全部完成，不再需要调用工具\n\n"
                "直接回复内容，不要调用任何工具。"
            )
            self.llm.messages.append({"role": "user", "content": summary_prompt})
            try:
                response = self._chat_with_fallback(tools=None)
                self.llm.messages.append(
                    response.choices[0].message.model_dump(exclude_none=True)
                )
                content = (response.choices[0].message.content or "").strip()
                # 检查最后一行是否标记为完成
                last_line = content.strip().split("\n")[-1].strip()
                # 把总结发给用户（去掉标记行）
                display_lines = content.strip().split("\n")
                if display_lines[-1].strip() in ("[继续]", "[完成]"):
                    display_content = "\n".join(display_lines[:-1]).strip()
                else:
                    display_content = content
                if display_content and self.interim_sender:
                    try:
                        self.interim_sender(display_content)
                    except Exception:
                        pass
                if last_line == "[完成]":
                    logger.info("tool_loop: LLM indicated [完成] after summary")
                    # 去掉标记行，返回总结内容
                    summary_content = "\n".join(content.strip().split("\n")[:-1]).strip()
                    return summary_content or content
                logger.info("tool_loop: LLM indicated [继续], resuming tool loop")
            except AgentInterrupted:
                raise
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

    def _on_model_switch(self, message: str) -> None:
        """模型切换时通知 listener 实时展示状态。"""
        if not self.progress_callback:
            return
        try:
            self.progress_callback({
                "type": "model_switch",
                "message": message,
            })
        except Exception:
            pass

    def run(self, user_input: str) -> str:
        """处理一轮用户输入，返回最终回复文本。"""
        self.llm.add_user_message(user_input)
        try:
            result = self._run_tool_loop()
        except AgentInterrupted:
            # 标记中断摘要已由 Session 读取，后续由 Session 处理队列
            raise

        tool_count = getattr(self, "_fast_path_tool_count", 0)
        reflection_hints = self._maybe_reflect(
            goal=user_input,
            is_fast_path=True,
            tool_call_count=tool_count,
        )
        if reflection_hints:
            result += "\n\n" + "\n".join(reflection_hints)
        return result

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

        estimated_tokens = self._estimate_context_tokens()

        try:
            return apply_compaction(
                agent_llm=self.llm,
                config=self._compaction_config,
                estimated_tokens=estimated_tokens,
                stop_reason=self.last_stop_reason,
                session_id=session_id,
                session_store=session_store,
            )
        except Exception as e:
            logger.warning(f"压缩异常: {e}")
            return None

    def force_compact(
        self,
        session_store: Any = None,
        session_id: str = "",
    ) -> CompactionResult | None:
        """手动触发上下文压缩（/compaction 命令调用，无视 token 阈值）。

        使用 threading.Lock 保证多线程安全（飞书 WebSocket 回调可能并发触发）。
        如果另一条压缩正在执行，返回 None。
        """
        if not self._compaction_lock.acquire(blocking=False):
            logger.info("force_compact: 另一次压缩正在执行，跳过")
            return None

        try:
            if self._compaction_config is None:
                return None

            if (
                self.current_plan is not None
                and self.current_plan.status == PlanStatus.executing
            ):
                return None

            try:
                return apply_compaction(
                    agent_llm=self.llm,
                    config=self._compaction_config,
                    estimated_tokens=0,
                    stop_reason="end_turn",
                    session_id=session_id,
                    session_store=session_store,
                    force=True,
                )
            except Exception as e:
                logger.warning(f"手动压缩异常: {e}")
                return None
        finally:
            self._compaction_lock.release()

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
