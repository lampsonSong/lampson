"""Planner — 调 LLM 生成步骤列表。"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from src.planning.steps import Plan, PlanStatus, Step, StepStatus
from src.planning.prompts import (
    build_plan_prompt,
    build_replan_prompt,
    build_context_from_history,
)

if TYPE_CHECKING:
    from src.core.llm import LLMClient

logger = logging.getLogger(__name__)


class PlanParseError(Exception):
    """LLM 返回的 plan JSON 无法解析。"""


class Planner:
    """任务规划器：调一次 LLM 把用户目标分解成步骤列表。"""

    def __init__(self, llm: LLMClient, tool_schemas: list[dict]) -> None:
        self.llm = llm
        self.tool_schemas = tool_schemas
        self._valid_actions = self._extract_valid_actions()

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
    ) -> Plan:
        """带着失败信息重新规划。

        Args:
            goal: 用户原始目标。
            context: 当前上下文。
            failed_step: 失败的步骤。
            completed_steps: 已完成的步骤。

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
        )

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从文本中提取 JSON（处理 markdown 代码块包裹）。"""
        # 尝试提取 ```json ... ``` 包裹的内容
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        # 尝试直接找 { ... }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # 最后尝试整体解析
        try:
            return json.loads(text)
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
