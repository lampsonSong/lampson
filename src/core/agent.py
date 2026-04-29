"""Agent 主循环：接收用户输入，调用 LLM，处理 tool calling，返回最终回复。

需要技能/项目时由语义检索在规划或 Fast Path 中注入匹配全文，不预载 skill 目录。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

from src.core.adapters import BaseModelAdapter
from src.core.adapters.base import (
    LLMError, LLMRetryableError, LLMRateLimitError, LLMFatalError, LLMContextTooLongError,
)
from src.core.interrupt import AgentInterrupted
from src.core.llm import LLMClient
from src.core import tools as tool_registry
from src.core.compaction import CompactionConfig, CompactionResult, apply_compaction
from src.planning.steps import Plan, PlanStatus
from src.memory import session_store as _session_store
from src.core.error_log import log_error as _log_error, SOURCE_LLM as _SRC_LLM, SOURCE_TOOL as _SRC_TOOL

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
        # 连续 LLM 调用失败计数：连续3次失败后触发熔断
        self._consecutive_llm_failures: int = 0

        # ── trace / 完整复现 ───────────────────────────────────────────
        # session_id：由 Session 注入，用于写 trace 行到 JSONL
        self.session_id: str = ""
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

    def _inject_skill(self, user_input: str) -> None:
        """预匹配 skills，单个匹配时直接注入 skill body 到上下文。

        多个匹配时不注入，让 LLM 通过 skill 工具自行选择。
        无匹配时也不注入。
        """
        from src.skills.manager import match_skills

        matched = match_skills(user_input, self.skills)
        if len(matched) != 1:
            return

        skill = matched[0]
        inject = (
            f"[技能激活: {skill.name}]\n"
            f"{skill.body[:2000]}"
            + ("...(已截断)" if len(skill.body) > 2000 else "")
        )
        # 插在 user message 之前
        self.llm.messages.append({"role": "user", "content": inject})

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

    def _order_fallbacks(self, primary_base: str) -> list[tuple[LLMClient, BaseModelAdapter]]:
        """重排 fallback 模型：优先不同供应商，再排同供应商。

        当主模型因限流/超时失败时，同供应商的模型大概率也有问题，
        优先尝试不同 base_url 的 fallback 可以更快找到可用模型。
        """
        diff_vendor: list[tuple[LLMClient, BaseModelAdapter]] = []
        same_vendor: list[tuple[LLMClient, BaseModelAdapter]] = []
        for fb in self.fallback_models:
            if fb[0].base_url != primary_base:
                diff_vendor.append(fb)
            else:
                same_vendor.append(fb)
        return diff_vendor + same_vendor

    def _sanitize_tool_messages(self) -> None:
        """清理 messages 中所有不完整的 tool 调用序列。

        遍历整个 messages 列表，找出所有 assistant 消息的 tool_calls，
        验证每个 tool_call 都有对应 ID 的 tool_result。
        如果某个 tool_call 没有对应 result，补上错误占位 result。
        这确保发给任何 LLM 的 messages 都是完整干净的。
        """
        msgs = self.llm.messages
        if not msgs:
            return

        # 建立所有 tool_call_id 的集合
        tool_call_ids: set[str] = set()
        # 建立所有已有 tool_result 的 tool_call_id 集合
        result_ids: set[str] = set()

        for msg in msgs:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id") or ""
                    if tc_id:
                        tool_call_ids.add(tc_id)
            elif msg.get("role") == "tool":
                result_ids.add(msg.get("tool_call_id") or "")

        # 找出缺失的 tool_call_id
        missing_ids = tool_call_ids - result_ids
        if not missing_ids:
            logger.info(f"[_sanitize_tool_messages] 无需清理（tool_call_ids={tool_call_ids}, result_ids={result_ids}）")
            return

        # 为每个缺失的 ID 追加错误占位 result
        for tc_id in missing_ids:
            msgs.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": "[错误] 工具执行失败或被中断，结果未获取",
            })

        logger.info(f"[_sanitize_tool_messages] 补全了 {len(missing_ids)} 个缺失的 tool_result，IDs: {missing_ids}")
        logger.info(f"[_sanitize_tool_messages] 消息列表状态：")
        for i, m in enumerate(msgs):
            if m.get("role") in ("assistant", "tool", "user"):
                tc_ids = [tc.get("id") for tc in m.get("tool_calls", [])]
                logger.info(f"  [{i}] role={m.get('role')}, tool_calls={tc_ids}, tool_call_id={m.get('tool_call_id')}")

    def _chat_with_fallback(self, tools=None):
        """每次调用都从主模型开始，失败时按顺序尝试 fallback。

        不会永久切换模型——fallback 成功后响应仍写回主模型的 messages，
        下次调用再次从主模型开始尝试。
        """
        # 防御性清理 messages：保证 tool_call 和 tool_result 完整匹配
        self._sanitize_tool_messages()

        # 检查中断（LLM 调用前）
        self.check_interrupt()

        # 1. 先试主模型（默认60s）
        _no_fallback = False
        _fatal_error: Exception | None = None
        # 写 system_prompt trace（hash 去重）
        if self.session_id and self.llm.messages and self.llm.messages[0].get("role") == "system":
            system_content = self.llm.messages[0].get("content", "")
            if system_content:
                _session_store.write_system_prompt_trace(self.session_id, system_content)

        # 记录 LLM 调用开始时间（用于计算 duration_ms）
        _call_start = _session_store._now_ms()
        _call_model = self.llm.model

        try:
            result = self.adapter.chat(self.llm.messages, tools=tools)
            # 写 llm_call trace（成功）
            _call_end = _session_store._now_ms()
            parsed = self.adapter.parse_response(result)
            input_tokens = getattr(result.usage, 'prompt_tokens', 0) if result.usage else 0
            output_tokens = getattr(result.usage, 'completion_tokens', 0) if result.usage else 0
            _session_store.write_llm_call_trace(
                self.session_id,
                model=_call_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=_call_end - _call_start,
                stop_reason=parsed.finish_reason or "stop",
            )
            return result
        except LLMContextTooLongError:
            raise
        except (LLMFatalError, LLMRateLimitError, LLMRetryableError) as e:
            # 写 llm_error trace + 错误日志
            if self.session_id:
                _session_store.write_llm_error_trace(
                    self.session_id,
                    model=self.llm.model,
                    error_type=type(e).__name__,
                    detail=str(e)[:500],
                    duration_ms=_session_store._now_ms() - _call_start,
                )
                _log_error(
                    type(e).__name__, str(e)[:500], _SRC_LLM,
                    session_id=self.session_id,
                    detail={'model': self.llm.model, 'duration_ms': _session_store._now_ms() - _call_start},
                    messages_snapshot=self.llm.messages,
                    exception=e,
                )
            if not self.fallback_models:
                raise
            # 如果是参数错误（400），说明 messages 有问题，不 fallback
            if isinstance(e, LLMFatalError) and "400" in str(e) and "tool_call" in str(e):
                logger.warning(f"主模型 {self.llm.model} 返回参数错误（疑似 messages 不完整），不 fallback: {e}")
                _no_fallback = True
                _fatal_error = e
            else:
                logger.warning(f"主模型 {self.llm.model} 调用失败（{type(e).__name__}），尝试 fallback: {e}")
                self._on_model_switch(f"主模型 {self.llm.model} 失败，切换 fallback...")

        # 如果是 400+tool_call 错误，直接上抛，不走 fallback
        if _no_fallback:
            raise _fatal_error or LLMFatalError("主模型参数错误")

        # 2. 按供应商分组重排 fallback：优先尝试不同供应商的模型
        primary_base = self.llm.base_url
        ordered = self._order_fallbacks(primary_base)

        # 3. 依次试 fallback，统一 90s 超时（复杂 prompt 需要更长推理时间）
        FALLBACK_TIMEOUT = 90
        for fb_llm, fb_adapter in ordered:
            logger.warning(f"尝试 fallback: {fb_llm.model} (timeout={FALLBACK_TIMEOUT}s)")
            self._on_model_switch(f"尝试 {fb_llm.model}...")
            try:
                fb_llm.messages = list(self.llm.messages)
                _fb_start = _session_store._now_ms()
                result = fb_adapter.chat(fb_llm.messages, tools=tools, timeout=FALLBACK_TIMEOUT)
                _fb_end = _session_store._now_ms()
                self._on_model_switch(f"已切换到 {fb_llm.model}")
                # 写 llm_call trace（fallback 成功）
                parsed = self.adapter.parse_response(result)
                input_tokens = getattr(result.usage, 'prompt_tokens', 0) if result.usage else 0
                output_tokens = getattr(result.usage, 'completion_tokens', 0) if result.usage else 0
                _session_store.write_llm_call_trace(
                    self.session_id,
                    model=fb_llm.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=_fb_end - _fb_start,
                    stop_reason=parsed.finish_reason or "stop",
                )
                return result
            except LLMContextTooLongError:
                # prompt 超长换模型也没用，直接上抛
                raise
            except (LLMFatalError, LLMRateLimitError, LLMRetryableError) as e:
                logger.warning(f"fallback {fb_llm.model} 也失败（{type(e).__name__}）: {e}")
                # 写 llm_error trace + 错误日志
                if self.session_id:
                    _session_store.write_llm_error_trace(
                        self.session_id,
                        model=fb_llm.model,
                        error_type=type(e).__name__,
                        detail=str(e)[:500],
                        duration_ms=_session_store._now_ms() - _fb_start,
                    )
                    _log_error(
                        type(e).__name__, str(e)[:500], _SRC_LLM,
                        session_id=self.session_id,
                        detail={'model': fb_llm.model, 'duration_ms': _session_store._now_ms() - _fb_start, 'is_fallback': True},
                        messages_snapshot=self.llm.messages,
                        exception=e,
                    )
                continue

        raise LLMFatalError("所有模型（含 fallback）均调用失败")

    def _run_tool_loop(self) -> str:
        self._fast_path_tool_count = 0

        while True:
            for round_num in range(self.max_tool_rounds):
                try:
                    response = self._chat_with_fallback(tools=self._tools)
                    self._consecutive_llm_failures = 0  # 新增：成功则重置
                except AgentInterrupted:
                    raise  # 直接上抛，不吞掉
                except LLMContextTooLongError as e:
                    logger.warning(f"Prompt 超长: {e}")
                    return "[上下文过长，请使用 /compaction 手动压缩后重试]"
                except LLMError as e:
                    self._consecutive_llm_failures += 1
                    if self._consecutive_llm_failures >= 3:
                        logger.error(f"连续 {self._consecutive_llm_failures} 次 LLM 调用失败，熔断退出")
                        return f"[LLM 错误] 连续多次调用失败，LLM 服务暂时不可用。请稍后再试。\n最后一次错误: {e}"
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

                    # 写 tool_call trace
                    if self.session_id:
                        try:
                            args_dict = json.loads(tc.raw_arguments) if isinstance(tc.raw_arguments, str) else tc.raw_arguments
                        except Exception:
                            args_dict = {"raw": tc.raw_arguments}
                        _session_store.write_tool_call_trace(
                            self.session_id,
                            tool_call_id=tc.id,
                            name=tc.name,
                            arguments=args_dict,
                        )

                    result = tool_registry.dispatch(tc.name, tc.raw_arguments)
                    self._fast_path_tool_count += 1

                    # 写 tool_result trace + 错误日志
                    if self.session_id:
                        error_info = None
                        if result.startswith("[错误]") or result.startswith("[Exception"):
                            error_info = {"type": "ToolError", "message": result[:200]}
                            _log_error(
                                "ToolExecutionError", result[:500], _SRC_TOOL,
                                session_id=self.session_id,
                                tool_name=tc.name,
                                tool_arguments=args_dict,
                                tool_result=result,
                                messages_snapshot=self.llm.messages,
                            )
                        _session_store.write_tool_result_trace(
                            self.session_id,
                            tool_call_id=tc.id,
                            result=result,
                            error=error_info,
                        )

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
                # 通知进度系统重置：结束旧卡片，后续工具调用会新开卡片
                if self.progress_callback:
                    try:
                        self.progress_callback({"type": "progress_reset"})
                    except Exception:
                        pass
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
        self._consecutive_llm_failures = 0  # 新增：每次新任务开始时重置

        # 预匹配 skills，注入匹配的 skill 上下文
        self._inject_skill(user_input)

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
        progress_callback: Callable[[str], None] | None = None,
    ) -> CompactionResult | None:
        """检查并执行上下文压缩。

        Args:
            session_store: session_store 模块，用于写入 segment_boundary。
            session_id: 当前会话 id，需与 JSONL 一致；空字符串则仍执行压缩逻辑但不落 segment 边界。
            progress_callback: 可选，Compaction 各阶段进度文案（发往 UI）。
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
                progress_callback=progress_callback,
            )
        except Exception as e:
            logger.warning(f"压缩异常: {e}")
            return None

    def force_compact(
        self,
        session_store: Any = None,
        session_id: str = "",
        progress_callback: Callable[[str], None] | None = None,
    ) -> CompactionResult | None:
        """手动触发上下文压缩（/compaction 命令调用，无视 token 阈值）。

        使用 threading.Lock 保证多线程安全（飞书 WebSocket 回调可能并发触发）。
        如果另一条压缩正在执行，返回 None。

        Args:
            progress_callback: 可选，Compaction 各阶段进度文案（发往 UI）。
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
                    progress_callback=progress_callback,
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
