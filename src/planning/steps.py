"""Task Planning 核心数据类。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class PlanStatus(str, Enum):
    """Plan 生命周期状态。"""

    created = "created"  # 已规划，未确认
    confirmed = "confirmed"  # 用户已确认，准备执行
    executing = "executing"  # 正在执行中
    completed = "completed"  # 全部步骤完成
    failed = "failed"  # 某步骤失败且未恢复
    cancelled = "cancelled"  # 用户取消


class StepStatus(str, Enum):
    """单个步骤的执行状态。"""

    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


@dataclass
class Step:
    """一个可执行步骤。"""

    id: int
    thought: str  # 为什么这一步要做
    action: str  # 工具名
    args: dict  # 工具参数（支持 $prev.result 等引用）
    status: StepStatus = StepStatus.pending
    result: str | None = None  # 执行结果（完成后填充）
    error: str | None = None  # 错误信息（失败时填充）
    reasoning: str = ""  # 参数是怎么确定的

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thought": self.thought,
            "action": self.action,
            "args": self.args,
            "status": self.status.value if isinstance(self.status, StepStatus) else self.status,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class StepResult:
    """步骤执行结果。"""

    step_id: int
    observation: str  # 执行结果文本
    status: str  # success | error
    is_final: bool  # 是否最后一步


@dataclass
class Plan:
    """完整执行计划。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    goal: str = ""  # 用户原始目标
    steps: list[Step] = field(default_factory=list)
    status: PlanStatus = PlanStatus.created
    plan_summary: str = ""  # 一句话描述计划
    created_at: float = field(default_factory=time.time)
    current_step_index: int = 0  # 执行到第几步

    # ── 状态转换 ──

    def confirm(self) -> None:
        if self.status != PlanStatus.created:
            raise ValueError(f"Cannot confirm plan in {self.status.value} state")
        self.status = PlanStatus.confirmed

    def start(self) -> None:
        if self.status not in (PlanStatus.confirmed, PlanStatus.created):
            raise ValueError(f"Cannot start plan in {self.status.value} state")
        self.status = PlanStatus.executing

    def complete(self) -> None:
        if self.status != PlanStatus.executing:
            raise ValueError(f"Cannot complete plan in {self.status.value} state")
        self.status = PlanStatus.completed

    def fail(self) -> None:
        self.status = PlanStatus.failed

    def cancel(self) -> None:
        self.status = PlanStatus.cancelled

    # ── 查询 ──

    @property
    def is_single_step(self) -> bool:
        """是否只有一个步骤（可退化为直接执行）。"""
        return len(self.steps) <= 1

    @property
    def done_steps(self) -> list[Step]:
        return [s for s in self.steps if s.status == StepStatus.done]

    @property
    def failed_steps(self) -> list[Step]:
        return [s for s in self.steps if s.status == StepStatus.failed]

    @property
    def pending_steps(self) -> list[Step]:
        return [s for s in self.steps if s.status == StepStatus.pending]

    def get_step_by_id(self, step_id: int) -> Step | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def format_for_display(self) -> str:
        """格式化为用户可见的执行计划。"""
        lines = [f"📋 执行计划：{self.plan_summary}", ""]
        for step in self.steps:
            icon = {
                StepStatus.pending: "⏳",
                StepStatus.running: "▶️",
                StepStatus.done: "✅",
                StepStatus.failed: "❌",
                StepStatus.skipped: "⏭️",
            }.get(step.status, "⏳")
            lines.append(f"  {icon} 步骤{step.id}: {step.thought}")
            lines.append(f"      工具: {step.action}({self._format_args(step.args)})")
            if step.result:
                preview = step.result[:200] + ("..." if len(step.result) > 200 else "")
                lines.append(f"      结果: {preview}")
            if step.error:
                lines.append(f"      错误: {step.error}")
        return "\n".join(lines)

    @staticmethod
    def _format_args(args: dict) -> str:
        parts = []
        for k, v in args.items():
            val_str = str(v)
            if len(val_str) > 60:
                val_str = val_str[:60] + "..."
            parts.append(f"{k}={val_str}")
        return ", ".join(parts)
