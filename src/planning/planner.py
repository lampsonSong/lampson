"""Planner — 调 LLM 生成步骤列表（v1 单轮与 v2 两阶段 + replan）。"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from src.planning.steps import (
    IntentResult,
    Plan,
    PlanStatus,
    Step,
    StepStatus,
)
from src.planning.prompts import (
    build_classify_prompt,
    build_plan_prompt,
    build_plan_prompt_v2,
    build_replan_prompt,
    _format_tool_schemas,
)

if TYPE_CHECKING:
    from src.core.llm import LLMClient

logger = logging.getLogger(__name__)


class PlanParseError(Exception):
    """LLM 返回的 plan JSON 无法解析。"""


# 置信度低于此阈值时保守地认为需要工具（减少误判闲聊）
_INTENT_CONFIDENCE_TOOL_FLOOR = 0.4


class Planner:
    """任务规划器：v1 单轮 plan()；v2 的 classify() + plan_v2()；以及 replan()。"""

    def __init__(self, llm: LLMClient, tool_schemas: list[dict]) -> None:
        self.llm = llm
        self.tool_schemas = tool_schemas
        self._valid_actions = self._extract_valid_actions()

    def classify(self, goal: str, context: str = "") -> IntentResult:
        """阶段一：理解意图、是否需要工具、缺省信息与可选的信息收集子计划。"""
        tools_desc = _format_tool_schemas(self.tool_schemas)
        prompt = build_classify_prompt(goal=goal, context=context, tools_desc=tools_desc)
        raw = self._call_llm(prompt)
        result = self._parse_intent(raw, goal=goal)
        if not result.needs_tools and result.confidence < _INTENT_CONFIDENCE_TOOL_FLOOR:
            # 没把握时保守走工具链
            result.needs_tools = True
        return result

    def plan_v2(
        self,
        goal: str,
        context: str,
        phase1_result: "IntentResult | str",
        exploration_results: str = "",
    ) -> Plan:
        """阶段二：在分类与探测结果之上生成可执行计划。"""
        tools_desc = _format_tool_schemas(self.tool_schemas)
        if isinstance(phase1_result, IntentResult):
            p1 = json.dumps(
                {
                    "intent": phase1_result.intent,
                    "needs_tools": phase1_result.needs_tools,
                    "intent_detail": phase1_result.intent_detail,
                    "confidence": phase1_result.confidence,
                    "missing_info": phase1_result.missing_info,
                },
                ensure_ascii=False,
            )
        else:
            p1 = str(phase1_result)
        ex = exploration_results.strip() or "（未执行信息收集步骤或尚无情境结果。）"
        prompt = build_plan_prompt_v2(
            goal=goal,
            context=context,
            tools_desc=tools_desc,
            phase1_result=p1,
            exploration_results=ex,
        )
        raw = self._call_llm(prompt)
        plan = self._parse_plan(raw, goal)
        self._validate_plan(plan)
        return plan

    def plan(self, goal: str, context: str = "") -> Plan:
        """给定目标 + 上下文，返回 Plan 对象。

        Args:
            goal: 用户原始目标。
            context: 当前对话上下文。

        Returns:
            包含步骤列表的 Plan 对象。

        Raises:
            PlanParseError: LLM 返回无法解析。
            RuntimeError: LLM 调用失败。
        """
        prompt = build_plan_prompt(
            goal=goal,
            context=context,
            tool_schemas=self.tool_schemas,
        )

        raw = self._call_llm(prompt)
        plan = self._parse_plan(raw, goal)
        self._validate_plan(plan)
        return plan

    def replan(
        self,
        goal: str,
        context: str,
        failed_step: Step,
        completed_steps: list[Step],
        failure_context: str = "",
    ) -> Plan:
        """带着失败信息重新规划。

        Args:
            goal: 用户原始目标。
            context: 当前上下文。
            failed_step: 失败的步骤。
            completed_steps: 已完成的步骤。
            failure_context: 由 Plan.get_failure_context() 等提供的失败历史。

        Returns:
            新的 Plan 对象。
        """
        failed_desc = f"步骤{failed_step.id}: {failed_step.action}({failed_step.args})"
        error_msg = failed_step.error or "未知错误"
        completed_desc = "\n".join(
            f"步骤{s.id}: {s.action}({s.args}) → 结果: {s.result}"
            for s in completed_steps
        )

        prompt = build_replan_prompt(
            goal=goal,
            context=context,
            tool_schemas=self.tool_schemas,
            failed_step=failed_desc,
            error_message=error_msg,
            completed_steps=completed_desc,
            failure_context=failure_context,
        )

        raw = self._call_llm(prompt)
        plan = self._parse_plan(raw, goal)
        self._validate_plan(plan)
        return plan

    # ── LLM 调用 ──

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM 获取规划结果。用临时消息列表，不污染主对话。"""
        messages = [
            {"role": "user", "content": prompt},
        ]
        # 直接用底层 client 发送，不走 agent 的消息管理
        resp = self.llm.client.chat.completions.create(
            model=self.llm.model,
            messages=messages,
            temperature=0.1,  # 低温度，规划需要确定性
        )
        return resp.choices[0].message.content or ""

    def _parse_intent(self, raw: str, goal: str) -> IntentResult:
        """从阶段一 LLM 回复中解析 IntentResult（含 initial_plan 子计划）。"""
        data = self._extract_json(raw)
        if data is None:
            raise PlanParseError(f"无法从阶段一 LLM 回复中解析 JSON: {raw[:200]}")

        needs_tools = bool(data.get("needs_tools", True))
        intent = str(data.get("intent", "unknown"))
        intent_detail = str(data.get("intent_detail", ""))
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        missing = data.get("missing_info") or []
        if not isinstance(missing, list):
            missing = [str(missing)]
        else:
            missing = [str(x) for x in missing]

        direct: str | None = data.get("direct_reply")
        if direct is not None and not (isinstance(direct, str) and direct.strip()):
            direct = None
        elif isinstance(direct, str):
            pass
        else:
            direct = None

        initial: Plan | None = None
        ip = data.get("initial_plan")
        if isinstance(ip, dict) and ip.get("steps"):
            try:
                initial = self._parse_plan(json.dumps(ip), goal=goal)
                initial.plan_summary = "信息收集"
            except PlanParseError:
                initial = None

        return IntentResult(
            intent=intent,
            needs_tools=needs_tools,
            intent_detail=intent_detail,
            confidence=confidence,
            missing_info=missing,
            direct_reply=direct,
            initial_plan=initial,
        )

    # ── 解析 ──

    def _parse_plan(self, raw: str, goal: str) -> Plan:
        """从 LLM 回复中提取 Plan 对象。"""
        data = self._extract_json(raw)
        if data is None:
            raise PlanParseError(f"无法从 LLM 回复中解析 JSON: {raw[:200]}")

        steps_data = data.get("steps", [])
        if not steps_data:
            raise PlanParseError("LLM 返回的计划没有步骤")

        steps = []
        for i, s in enumerate(steps_data):
            step = Step(
                id=s.get("id", i + 1),
                thought=s.get("thought", ""),
                action=s.get("action", ""),
                args=s.get("args", {}),
                reasoning=s.get("reasoning", ""),
                status=StepStatus.pending,
            )
            steps.append(step)

        return Plan(
            goal=goal,
            steps=steps,
            status=PlanStatus.created,
            plan_summary=data.get("plan_summary", ""),
            expected_result=str(data.get("expected_result", "") or ""),
        )

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从文本中提取 JSON（处理 markdown 代码块、思维链等包裹）。"""
        # 去掉 <think...</think > 思维链（部分模型如 MiniMax 会输出）
        cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL)

        # 尝试提取 ```json ... ``` 包裹的内容
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

        # 尝试直接找 { ... }
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # 最后尝试整体解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None

    # ── 校验 ──

    def _validate_plan(self, plan: Plan) -> None:
        """校验计划的合理性，修正可自动修复的问题。"""
        for step in plan.steps:
            # 校验 action 是否在可用工具列表中
            if step.action not in self._valid_actions:
                # 尝试模糊匹配：工具名是被提交 action 的子串
                matches = [
                    a for a in self._valid_actions if a.lower() in step.action.lower()
                ]
                if len(matches) == 1:
                    logger.warning(
                        f"步骤{step.id}: action '{step.action}' 自动修正为 '{matches[0]}'"
                    )
                    step.action = matches[0]
                else:
                    logger.warning(
                        f"步骤{step.id}: action '{step.action}' 不在可用工具列表中"
                    )
                    # 不抛异常，让执行时自然失败并触发重试/replan

            # 确保 id 从 1 开始连续
        for i, step in enumerate(plan.steps):
            step.id = i + 1

    def _extract_valid_actions(self) -> set[str]:
        """从 tool_schemas 中提取所有有效的工具名。"""
        actions = set()
        for schema in self.tool_schemas:
            func = schema.get("function", schema)
            name = func.get("name", "")
            if name:
                actions.add(name)
        return actions
