"""测试 planning/steps.py - 任务规划核心数据类"""
import pytest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPlanStatus:
    """Plan 状态枚举测试"""

    def test_plan_status_values(self):
        """测试 PlanStatus 枚举值"""
        from src.planning.steps import PlanStatus
        
        assert PlanStatus.created.value == "created"
        assert PlanStatus.confirmed.value == "confirmed"
        assert PlanStatus.executing.value == "executing"
        assert PlanStatus.completed.value == "completed"
        assert PlanStatus.failed.value == "failed"
        assert PlanStatus.cancelled.value == "cancelled"

    def test_plan_status_is_string(self):
        """测试 PlanStatus 是字符串枚举"""
        from src.planning.steps import PlanStatus
        
        for status in PlanStatus:
            assert isinstance(status.value, str)


class TestStepStatus:
    """Step 状态枚举测试"""

    def test_step_status_values(self):
        """测试 StepStatus 枚举值"""
        from src.planning.steps import StepStatus
        
        assert StepStatus.pending.value == "pending"
        assert StepStatus.running.value == "running"
        assert StepStatus.done.value == "done"
        assert StepStatus.failed.value == "failed"
        assert StepStatus.skipped.value == "skipped"

    def test_step_status_is_string(self):
        """测试 StepStatus 是字符串枚举"""
        from src.planning.steps import StepStatus
        
        for status in StepStatus:
            assert isinstance(status.value, str)


class TestStep:
    """步骤测试"""

    def test_init(self):
        """测试初始化"""
        from src.planning.steps import Step, StepStatus
        
        step = Step(
            id=1,
            thought="需要执行某个操作",
            action="shell",
            args={"command": "ls -la"},
        )
        
        assert step.id == 1
        assert step.thought == "需要执行某个操作"
        assert step.action == "shell"
        assert step.args == {"command": "ls -la"}
        assert step.status == StepStatus.pending
        assert step.result is None
        assert step.error is None

    def test_init_with_all_fields(self):
        """测试带所有字段初始化"""
        from src.planning.steps import Step, StepStatus
        
        step = Step(
            id=2,
            thought="reasoning",
            action="file_read",
            args={"path": "/test"},
            status=StepStatus.done,
            result="file content",
            error=None,
            reasoning="I determined the path",
        )
        
        assert step.id == 2
        assert step.status == StepStatus.done
        assert step.result == "file content"
        assert step.reasoning == "I determined the path"

    def test_to_dict(self):
        """测试转换为 dict"""
        from src.planning.steps import Step, StepStatus
        
        step = Step(
            id=1,
            thought="test",
            action="shell",
            args={"cmd": "echo hi"},
            status=StepStatus.completed,
            result="hi",
        )
        
        d = step.to_dict()
        
        assert d["id"] == 1
        assert d["thought"] == "test"
        assert d["action"] == "shell"
        assert d["args"] == {"cmd": "echo hi"}
        assert d["status"] == "done"
        assert d["result"] == "hi"


class TestStepResult:
    """步骤结果测试"""

    def test_init(self):
        """测试初始化"""
        from src.planning.steps import StepResult
        
        result = StepResult(
            step_id=1,
            observation="命令执行成功",
            status="success",
            is_final=True,
        )
        
        assert result.step_id == 1
        assert result.observation == "命令执行成功"
        assert result.status == "success"
        assert result.is_final is True

    def test_final_step(self):
        """测试最后一步"""
        from src.planning.steps import StepResult
        
        result = StepResult(
            step_id=5,
            observation="all done",
            status="success",
            is_final=True,
        )
        
        assert result.is_final is True

    def test_non_final_step(self):
        """测试非最后一步"""
        from src.planning.steps import StepResult
        
        result = StepResult(
            step_id=2,
            observation="partial result",
            status="success",
            is_final=False,
        )
        
        assert result.is_final is False


class TestStepEvaluation:
    """步骤评估测试"""

    def test_init_success(self):
        """测试成功评估"""
        from src.planning.steps import StepEvaluation
        
        eval_result = StepEvaluation(ok=True, reason="步骤执行成功")
        
        assert eval_result.ok is True
        assert eval_result.reason == "步骤执行成功"
        assert eval_result.should_retry is False
        assert eval_result.is_plan_flawed is False

    def test_init_failure_with_retry(self):
        """测试需要重试的评估"""
        from src.planning.steps import StepEvaluation
        
        eval_result = StepEvaluation(
            ok=False,
            reason="临时错误",
            should_retry=True,
        )
        
        assert eval_result.ok is False
        assert eval_result.should_retry is True

    def test_init_plan_flawed(self):
        """测试计划有缺陷的评估"""
        from src.planning.steps import StepEvaluation
        
        eval_result = StepEvaluation(
            ok=False,
            reason="计划逻辑错误",
            should_retry=False,
            is_plan_flawed=True,
        )
        
        assert eval_result.ok is False
        assert eval_result.is_plan_flawed is True


class TestFailedAttempt:
    """失败尝试测试"""

    def test_init(self):
        """测试初始化"""
        from src.planning.steps import FailedAttempt
        
        attempt = FailedAttempt(
            step_id=1,
            error="command failed",
            observation="exit code 1",
        )
        
        assert attempt.step_id == 1
        assert attempt.error == "command failed"
        assert attempt.observation == "exit code 1"

    def test_init_with_attempts(self):
        """测试带尝试次数的初始化"""
        from src.planning.steps import FailedAttempt
        
        attempt = FailedAttempt(
            step_id=1,
            error="timeout",
            observation="command timed out",
            attempts=3,
        )
        
        assert attempt.attempts == 3
