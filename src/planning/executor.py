"""Executor — 按顺序执行 Plan 的每个步骤，并在每步后做启发式评估与重试 / replan。"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Callable

from src.core import tools as tool_registry
from src.core.llm import LLMClient
from src.planning.steps import (
    FailedAttempt,
    Plan,
    PlanStatus,
    Step,
    StepEvaluation,
    StepResult,
    StepStatus,
)
from src.planning.prompts import build_synthesize_prompt

if TYPE_CHECKING:
    from src.planning.planner import Planner

logger = logging.getLogger(__name__)

# // FIX-4: $prev.result 等引用可保留足够长度，避免只取首行
_MAX_REF_LENGTH = 2000

# 同一步内「参数类」失败时最多重试次数（不含首次执行）
_MAX_STEP_RETRIES = 2
# 因计划不合理触发的全局 replan 次数上限
_MAX_REPLAN = 3


class StepExecutionError(Exception):
    """步骤执行失败。"""

    def __init__(self, step_id: int, message: str):
        self.step_id = step_id
        super().__init__(f"步骤{step_id}执行失败: {message}")


class Executor:
    """计划执行器：遍历 steps，解析引用，调工具，评估结果，必要时重试或 replan。"""

    def __init__(
        self,
        llm: LLMClient,
        max_retries: int = 3,
        on_step_end: Callable[[Step, StepResult], None] | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.llm = llm
        self.max_retries = max_retries
        self.on_step_end = on_step_end
        self.planner = planner

    @staticmethod
    def _evaluate_step_result(step: Step, result: str) -> StepEvaluation:
        """评估单步执行返回文本是否正常。"""
        r = result or ""
        if "[错误]" in r or "[拒绝]" in r or "Traceback" in r or "[超时]" in r:
            return StepEvaluation(
                ok=False,
                reason="工具执行报错或环境拒绝",
                should_retry=True,
                is_plan_flawed=False,
            )
        lower = r.lower()
        if (
            "no such file" in lower
            or "not found" in lower
            or "不存在" in r
        ):
            return StepEvaluation(
                ok=False,
                reason="路径或资源不存在",
                should_retry=False,
                is_plan_flawed=True,
            )
        if not r.strip() and step.action not in ("file_write", "feishu_send"):
            return StepEvaluation(
                ok=False,
                reason="结果为空，可能步骤不可行",
                should_retry=False,
                is_plan_flawed=True,
            )
        return StepEvaluation(ok=True, reason="ok")

    def execute(
        self,
        plan: Plan,
        *,
        synthesize: bool = True,
        record_to_history: bool = True,
    ) -> str:
        """执行完整计划，返回最终汇总或中止说明。

        Args:
            plan: 已确认的 Plan 对象。
            synthesize: 为 False 时多步也不调用汇总 LLM，仅返回各步结果的格式化文本。
            record_to_history: 为 False 时工具结果不写入主对话历史（用于 exploration 阶段）。
        """
        plan.start()
        replan_count = 0
        i = 0

        try:
            while i < len(plan.steps):
                step = plan.steps[i]
                plan.current_step_index = i

                if step.status == StepStatus.skipped:
                    i += 1
                    continue

                eval_ok, end_reason = self._execute_one_step_with_react(
                    plan, step, i, replan_count, record_to_history
                )
                if end_reason == "replan":
                    if self.planner is None or replan_count >= _MAX_REPLAN:
                        plan.fail()
                        return self._aborted_message(plan, last_error=step.error)
                    replan_count += 1
                    completed = [s for s in plan.steps[:i] if s.status == StepStatus.done]
                    try:
                        new_plan = self.planner.replan(
                            goal=plan.goal,
                            context="",
                            failed_step=step,
                            completed_steps=completed,
                            failure_context=plan.get_failure_context(),
                        )
                    except Exception as e:
                        logger.exception("replan 调用失败")
                        plan.fail()
                        return f"重新规划失败：{e}"
                    # 用新计划替换自当前索引起的剩余步骤
                    merged = self._merge_replan_steps(plan, i, new_plan)
                    plan.steps = merged
                    plan.plan_summary = new_plan.plan_summary or plan.plan_summary
                    if new_plan.expected_result:
                        plan.expected_result = new_plan.expected_result
                    continue

                if not eval_ok:
                    plan.fail()
                    return f"计划执行失败：步骤{step.id} ({step.action}) 失败。\n{step.error or '未知错误'}"

                i += 1

            plan.complete()
            if synthesize:
                return self._synthesize(plan)
            return self._format_step_results(plan)

        except Exception as e:
            logger.exception("计划执行异常中断")
            plan.fail()
            return f"计划执行异常中断: {e}"

    def _aborted_message(self, plan: Plan, last_error: str | None) -> str:
        summary = self._format_step_results(plan)
        return (
            f"[中止] 已达最大 replan 次数仍无法继续。\n"
            f"已完成步骤摘要：\n{summary}\n"
            f"最近错误：{last_error or '未知'}"
        )

    @staticmethod
    def _merge_replan_steps(plan: Plan, from_index: int, new_plan: Plan) -> list[Step]:
        """保留 from_index 之前已完成的步，用 new_plan 替换剩余部分并顺延 id。"""
        prefix = list(plan.steps[:from_index])
        start_id = len(prefix) + 1
        new_steps: list[Step] = []
        for j, s in enumerate(new_plan.steps):
            ns = Step(
                id=start_id + j,
                thought=s.thought,
                action=s.action,
                args=s.args,
                status=StepStatus.pending,
                reasoning=s.reasoning,
            )
            new_steps.append(ns)
        return prefix + new_steps

    def _execute_one_step_with_react(
        self,
        plan: Plan,
        step: Step,
        step_index: int,
        replan_count: int,
        record_to_history: bool = True,
    ) -> tuple[bool, str | None]:
        """执行单步：成功返回 (True, None)；需 replan 返回 (False, 'replan')；硬失败 (False, None)。"""
        step.status = StepStatus.running
        last_raw = ""
        param_retry = 0
        overall_attempt = 0

        while overall_attempt < self.max_retries * (_MAX_STEP_RETRIES + 2):
            overall_attempt += 1
            last_err = ""
            try:
                resolved_args = self._resolve_args(plan, step, step_index)
                last_raw = tool_registry.dispatch(step.action, resolved_args)
            except Exception as e:
                last_err = str(e)
                last_raw = f"[错误] {last_err}"

            ev = self._evaluate_step_result(step, last_raw)
            if ev.ok:
                step.status = StepStatus.done
                step.result = last_raw
                step.error = None
                if record_to_history:
                    self.llm.messages.append({
                        "role": "tool",
                        "tool_call_id": f"step_{step.id}",
                        "content": last_raw,
                    })
                res = StepResult(
                    step_id=step.id,
                    observation=last_raw,
                    status="success",
                    is_final=(step_index == len(plan.steps) - 1),
                )
                if self.on_step_end:
                    self.on_step_end(step, res)
                logger.info(
                    f"步骤{step.id} ({step.action}) 执行成功"
                    + (f" (第{overall_attempt}次执行)" if overall_attempt > 1 else "")
                )
                return (True, None)

            err_text = last_err or last_raw
            tried_note = f"第{param_retry + 1}轮执行" if param_retry else "首次执行"
            plan.add_failure(
                FailedAttempt(
                    step_id=step.id,
                    action=step.action,
                    args=dict(step.args) if isinstance(step.args, dict) else {},
                    error=err_text,
                    tried_solutions=[tried_note],
                )
            )
            step.error = err_text

            if ev.should_retry and param_retry < _MAX_STEP_RETRIES:
                param_retry += 1
                time.sleep(min(param_retry * 0.4, 1.5))
                continue

            if ev.is_plan_flawed and self.planner is not None and replan_count < _MAX_REPLAN:
                step.status = StepStatus.failed
                if self.on_step_end:
                    self.on_step_end(
                        step,
                        StepResult(
                            step_id=step.id,
                            observation=last_raw,
                            status="error",
                            is_final=False,
                        ),
                    )
                return (False, "replan")

            break

        step.status = StepStatus.failed
        step.error = last_raw or "未知错误"
        if self.on_step_end:
            self.on_step_end(
                step,
                StepResult(
                    step_id=step.id,
                    observation=step.error or "",
                    status="error",
                    is_final=False,
                ),
            )
        return (False, None)

    def _resolve_args(self, plan: Plan, step: Step, step_index: int) -> dict:
        """解析步骤参数中的引用（$prev.result, $step[N].result, $goal）。"""
        resolved: dict = {}
        for key, value in step.args.items():
            if isinstance(value, str):
                value = self._resolve_refs(value, plan, step_index)
            elif isinstance(value, dict):
                value = {
                    k: self._resolve_refs(v, plan, step_index) if isinstance(v, str) else v
                    for k, v in value.items()
                }
            resolved[key] = value
        return resolved

    @staticmethod
    def _safe_replace_value(result: str) -> str:
        # // FIX-4: 截断为前 2000 字符而非仅第一行
        if not result:
            return ""
        if len(result) <= _MAX_REF_LENGTH:
            return result
        return result[:_MAX_REF_LENGTH] + "\n...（结果过长已截断）"

    def _resolve_refs(self, text: str, plan: Plan, step_index: int) -> str:
        """替换文本中的所有引用。"""
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
            text = text.replace("$prev.result", self._safe_replace_value(prev.result))

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
            text = text.replace(
                match.group(0), self._safe_replace_value(ref_step.result)
            )

        text = text.replace("$goal", plan.goal)

        return text

    def _synthesize(self, plan: Plan) -> str:
        """汇总所有步骤结果，统一经 LLM 整理后返回给用户。

        无论单步还是多步，工具的原始输出对用户来说都是"数据"而非"回答"。
        LLM 会根据用户意图提炼要点、组织语言。
        """

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
            logger.warning(f"汇总 LLM 调用失败，回退到原始结果: {e}")
            # 单步时直接返回该步原始结果（用户可读），多步返回格式化文本
            if plan.is_single_step and plan.steps[0].result:
                return plan.steps[0].result
            return step_results

    @staticmethod
    def _format_step_results(plan: Plan) -> str:
        """格式化所有步骤结果为可读文本。"""
        lines: list[str] = []
        for st in plan.steps:
            if st.status == StepStatus.skipped:
                lines.append(f"步骤{st.id}: [已跳过]")
                continue
            status_icon = "✅" if st.status == StepStatus.done else "❌"
            lines.append(f"步骤{st.id} {status_icon}: {st.action}")
            if st.args:
                args_str = ", ".join(f"{k}={v}" for k, v in st.args.items())
                lines.append(f"  参数: {args_str}")
            if st.result:
                result = st.result
                if len(result) > 1000:
                    result = result[:1000] + "\n...（结果已截断）"
                lines.append(f"  结果: {result}")
            if st.error:
                lines.append(f"  错误: {st.error}")
        return "\n".join(lines)
