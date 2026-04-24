"""Executor — 按顺序执行 Plan 的每个步骤。"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Callable

from src.core import tools as tool_registry
from src.planning.steps import Plan, PlanStatus, Step, StepResult, StepStatus
from src.planning.prompts import build_synthesize_prompt

if TYPE_CHECKING:
    from src.core.llm import LLMClient

logger = logging.getLogger(__name__)


class StepExecutionError(Exception):
    """步骤执行失败。"""

    def __init__(self, step_id: int, message: str):
        self.step_id = step_id
        super().__init__(f"步骤{step_id}执行失败: {message}")


class Executor:
    """计划执行器：遍历 steps → 调工具 → 拿结果 → 解析下一步参数。

    Executor 只做编排，不做执行。实际的工具调用委托给 tools.dispatch()。
    工具结果追加到 llm.messages，保持主对话历史完整（供压缩使用）。
    """

    def __init__(
        self,
        llm: LLMClient,
        max_retries: int = 3,
        on_step_end: Callable[[Step, StepResult], None] | None = None,
    ) -> None:
        self.llm = llm
        self.max_retries = max_retries
        self.on_step_end = on_step_end  # 回调：每步执行完通知外部

    def execute(self, plan: Plan) -> str:
        """执行完整计划，返回最终汇总结果。

        Args:
            plan: 已确认的 Plan 对象。

        Returns:
            最终汇总文本。
        """
        plan.start()

        try:
            for i in range(len(plan.steps)):
                step = plan.steps[i]
                plan.current_step_index = i

                if step.status == StepStatus.skipped:
                    continue

                # 执行单步（含重试）
                result = self._execute_step_with_retry(plan, step, i)

                if result.status == "error":
                    # 重试耗尽，中止
                    plan.fail()
                    return f"计划执行失败：步骤{step.id} ({step.action}) 失败。\n错误：{result.observation}"

            # 全部完成
            plan.complete()
            return self._synthesize(plan)

        except Exception as e:
            logger.exception("计划执行异常中断")
            plan.fail()
            return f"计划执行异常中断: {e}"

    def _execute_step_with_retry(
        self, plan: Plan, step: Step, step_index: int
    ) -> StepResult:
        """执行单步，失败时重试。"""
        step.status = StepStatus.running
        last_error = ""

        for attempt in range(1, self.max_retries + 1):
            try:
                # 解析参数引用
                resolved_args = self._resolve_args(plan, step, step_index)

                # 调用工具
                raw_result = tool_registry.dispatch(step.action, resolved_args)

                # 成功
                step.status = StepStatus.done
                step.result = raw_result
                step.error = None

                # 写回 messages（保持主对话历史完整，供压缩使用）
                # 使用 tool 格式追加，让 llm.messages 保持 OpenAI 兼容格式
                self.llm.messages.append({
                    "role": "tool",
                    "tool_call_id": f"step_{step.id}",
                    "content": raw_result,
                })

                result = StepResult(
                    step_id=step.id,
                    observation=raw_result,
                    status="success",
                    is_final=(step_index == len(plan.steps) - 1),
                )

                if self.on_step_end:
                    self.on_step_end(step, result)

                logger.info(
                    f"步骤{step.id} ({step.action}) 执行成功"
                    + (f" (第{attempt}次尝试)" if attempt > 1 else "")
                )
                return result

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"步骤{step.id} ({step.action}) 第{attempt}次尝试失败: {last_error}"
                )
                if attempt < self.max_retries:
                    # 短暂等待后重试（简单指数退避）
                    import time

                    time.sleep(min(attempt * 0.5, 2.0))

        # 重试耗尽
        step.status = StepStatus.failed
        step.error = last_error
        result = StepResult(
            step_id=step.id,
            observation=last_error,
            status="error",
            is_final=False,
        )
        if self.on_step_end:
            self.on_step_end(step, result)
        return result

    def _resolve_args(self, plan: Plan, step: Step, step_index: int) -> dict:
        """解析步骤参数中的引用（$prev.result, $step[N].result, $goal）。

        Args:
            plan: 当前计划。
            step: 要执行的步骤。
            step_index: 步骤在列表中的索引。

        Returns:
            解析后的参数字典。

        Raises:
            StepExecutionError: 引用解析失败。
        """
        resolved = {}
        for key, value in step.args.items():
            if isinstance(value, str):
                value = self._resolve_refs(value, plan, step_index)
            elif isinstance(value, dict):
                value = {
                    k: self._resolve_refs(v, plan, step_index)
                    if isinstance(v, str)
                    else v
                    for k, v in value.items()
                }
            resolved[key] = value
        return resolved

    def _resolve_refs(self, text: str, plan: Plan, step_index: int) -> str:
        """替换文本中的所有引用。"""
        # $prev.result → 上一步的结果
        if "$prev.result" in text:
            if step_index == 0:
                raise StepExecutionError(
                    step_index + 1, "$prev.result 引用了不存在的上一步"
                )
            prev = plan.steps[step_index - 1]
            if prev.result is None:
                raise StepExecutionError(
                    step_index + 1,
                    f"$prev.result 引用的步骤{prev.id}尚未执行完成",
                )
            text = text.replace("$prev.result", prev.result)

        # $step[N].result → 第 N 步的结果
        for match in re.finditer(r"\$step\[(\d+)\]\.result", text):
            ref_id = int(match.group(1))
            ref_step = plan.get_step_by_id(ref_id)
            if ref_step is None:
                raise StepExecutionError(
                    step_index + 1, f"$step[{ref_id}].result 引用的步骤不存在"
                )
            if ref_step.result is None:
                raise StepExecutionError(
                    step_index + 1,
                    f"$step[{ref_id}].result 引用的步骤尚未执行完成",
                )
            text = text.replace(match.group(0), ref_step.result)

        # $goal → 用户原始目标
        text = text.replace("$goal", plan.goal)

        return text

    def _synthesize(self, plan: Plan) -> str:
        """汇总所有步骤结果，生成最终回答。

        如果只有 1 步，直接返回该步结果（不需要再调 LLM）。
        如果有多步，调一次 LLM 汇总。
        """
        if plan.is_single_step and plan.steps[0].result:
            return plan.steps[0].result

        # 拼接所有步骤结果
        step_results = self._format_step_results(plan)

        prompt = build_synthesize_prompt(
            goal=plan.goal,
            step_results=step_results,
        )

        try:
            temp_client = LLMClient(
                api_key=self.llm.client.api_key,
                base_url=str(self.llm.client.base_url),
                model=self.llm.model,
            )
            temp_client.set_system_context()
            temp_client.add_user_message(prompt)
            resp = temp_client.chat()
            content = resp.choices[0].message.content or ""
            return content
        except Exception as e:
            logger.warning(f"汇总 LLM 调用失败，返回原始结果: {e}")
            return step_results

    @staticmethod
    def _format_step_results(plan: Plan) -> str:
        """格式化所有步骤结果为可读文本。"""
        lines = []
        for step in plan.steps:
            if step.status == StepStatus.skipped:
                lines.append(f"步骤{step.id}: [已跳过]")
                continue
            status_icon = "✅" if step.status == StepStatus.done else "❌"
            lines.append(f"步骤{step.id} {status_icon}: {step.action}")
            if step.args:
                args_str = ", ".join(f"{k}={v}" for k, v in step.args.items())
                lines.append(f"  参数: {args_str}")
            if step.result:
                # 截断过长的结果
                result = step.result
                if len(result) > 1000:
                    result = result[:1000] + "\n...（结果已截断）"
                lines.append(f"  结果: {result}")
            if step.error:
                lines.append(f"  错误: {step.error}")
        return "\n".join(lines)
