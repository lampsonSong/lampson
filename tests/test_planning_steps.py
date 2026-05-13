"""测试 planning/steps.py - 任务规划核心数据类"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPlanStatus:
    def test_plan_status_values(self):
        from src.planning.steps import PlanStatus
        assert PlanStatus.created.value == "created"
        assert PlanStatus.completed.value == "completed"


class TestStepStatus:
    def test_step_status_values(self):
        from src.planning.steps import StepStatus
        assert StepStatus.pending.value == "pending"


class TestStep:
    def test_init(self):
        from src.planning.steps import Step
        step = Step(id=1, thought="test", action="shell", args={"cmd": "ls"})
        assert step.id == 1

    def test_to_dict(self):
        from src.planning.steps import Step
        step = Step(id=1, thought="test", action="shell", args={})
        d = step.to_dict()
        assert d["id"] == 1


class TestStepResult:
    def test_init(self):
        from src.planning.steps import StepResult
        result = StepResult(step_id=1, observation="ok", status="success", is_final=True)
        assert result.step_id == 1
