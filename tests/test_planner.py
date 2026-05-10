"""Planner 模块单元测试。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.planning.steps import (
    Step,
    StepStatus,
    Plan,
    PlanStatus,
    IntentResult,
)
from src.planning.planner import extract_json, PlanParseError


class TestExtractJson:
    """测试 extract_json 函数。"""

    def test_extract_simple_json(self):
        """测试提取简单 JSON。"""
        result = extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_extract_json_in_code_block(self):
        """测试从代码块中提取 JSON。"""
        result = extract_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_extract_json_in_text(self):
        """测试从文本中提取 JSON。"""
        result = extract_json('Here is the result: {"key": "value"}')
        assert result == {"key": "value"}

    def test_extract_json_with_think_tags(self):
        """测试提取带思维链标签的 JSON。"""
        text = '<think>Thinking...</think>\n{"key": "value"}\n'
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_extract_invalid_json(self):
        """测试提取无效 JSON。"""
        result = extract_json("not json at all")
        assert result is None

    def test_extract_nested_json(self):
        """测试提取嵌套 JSON。"""
        text = '''
        {
            "name": "test",
            "nested": {
                "key": "value"
            },
            "array": [1, 2, 3]
        }
        '''
        result = extract_json(text)
        assert result["name"] == "test"
        assert result["nested"]["key"] == "value"
        assert result["array"] == [1, 2, 3]


class TestPlanParseError:
    """测试 PlanParseError 异常。"""

    def test_plan_parse_error_creation(self):
        """测试创建异常。"""
        error = PlanParseError("Parse failed")
        assert str(error) == "Parse failed"

    def test_plan_parse_error_inheritance(self):
        """测试异常继承。"""
        error = PlanParseError("Test")
        assert isinstance(error, Exception)


class TestStep:
    """测试 Step 类。"""

    def test_create_step(self):
        """测试创建步骤。"""
        step = Step(id=1, thought="Think", action="action", args={"key": "value"})
        
        assert step.id == 1
        assert step.thought == "Think"
        assert step.action == "action"
        assert step.args == {"key": "value"}
        assert step.status == StepStatus.pending
        assert step.result is None

    def test_step_with_default_values(self):
        """测试带默认值的步骤。"""
        step = Step(id=1, thought="Think", action="test", args={})
        assert step.result is None
        assert step.error is None
        assert step.reasoning == ""

    def test_step_to_dict(self):
        """测试步骤转字典。"""
        step = Step(id=1, thought="Think", action="test", args={"k": "v"})
        result = step.to_dict()
        
        assert result["id"] == 1
        assert result["thought"] == "Think"
        assert result["action"] == "test"
        assert result["args"] == {"k": "v"}
        assert result["status"] == "pending"

    def test_step_status_transitions(self):
        """测试步骤状态转换。"""
        step = Step(id=1, thought="", action="test", args={})
        
        step.status = StepStatus.running
        assert step.status == StepStatus.running
        
        step.status = StepStatus.done
        assert step.status == StepStatus.done

    def test_step_set_result(self):
        """测试设置步骤结果。"""
        step = Step(id=1, thought="", action="test", args={})
        step.result = "Step completed"
        assert step.result == "Step completed"

    def test_step_set_error(self):
        """测试设置步骤错误。"""
        step = Step(id=1, thought="", action="test", args={})
        step.error = "Something went wrong"
        assert step.error == "Something went wrong"


class TestStepStatus:
    """测试 StepStatus 枚举。"""

    def test_all_statuses_defined(self):
        """测试所有状态都已定义。"""
        assert StepStatus.pending is not None
        assert StepStatus.running is not None
        assert StepStatus.done is not None
        assert StepStatus.failed is not None
        assert StepStatus.skipped is not None


class TestPlan:
    """测试 Plan 类。"""

    def test_create_plan(self):
        """测试创建计划。"""
        steps = [
            Step(id=1, thought="", action="step1", args={}),
            Step(id=2, thought="", action="step2", args={}),
        ]
        plan = Plan(goal="Test goal", steps=steps)
        
        assert plan.goal == "Test goal"
        assert len(plan.steps) == 2
        assert plan.status == PlanStatus.created

    def test_plan_default_values(self):
        """测试计划默认值。"""
        plan = Plan(goal="Test")
        assert plan.goal == "Test"
        assert plan.steps == []
        assert plan.status == PlanStatus.created
        assert plan.current_step_index == 0

    def test_plan_is_single_step(self):
        """测试单步计划判断（属性，不是方法）。"""
        single = Plan(goal="Test", steps=[Step(id=1, thought="", action="one", args={})])
        multi = Plan(goal="Test", steps=[
            Step(id=1, thought="", action="one", args={}),
            Step(id=2, thought="", action="two", args={}),
        ])
        
        assert single.is_single_step is True
        assert multi.is_single_step is False

    def test_plan_state_transitions(self):
        """测试计划状态转换。"""
        plan = Plan(goal="Test", steps=[Step(id=1, thought="", action="test", args={})])
        
        plan.status = PlanStatus.executing
        assert plan.status == PlanStatus.executing
        
        plan.status = PlanStatus.completed
        assert plan.status == PlanStatus.completed

    def test_plan_done_steps(self):
        """测试已完成步骤（属性，不是方法）。"""
        steps = [
            Step(id=1, thought="", action="step1", args={}, status=StepStatus.done),
            Step(id=2, thought="", action="step2", args={}, status=StepStatus.done),
            Step(id=3, thought="", action="step3", args={}, status=StepStatus.pending),
        ]
        plan = Plan(goal="Test", steps=steps)
        
        done = plan.done_steps
        assert len(done) == 2

    def test_plan_failed_steps(self):
        """测试失败步骤（属性）。"""
        steps = [
            Step(id=1, thought="", action="step1", args={}, status=StepStatus.done),
            Step(id=2, thought="", action="step2", args={}, status=StepStatus.failed),
        ]
        plan = Plan(goal="Test", steps=steps)
        
        failed = plan.failed_steps
        assert len(failed) == 1
        assert failed[0].action == "step2"

    def test_plan_format_for_display(self):
        """测试计划格式化显示。"""
        steps = [
            Step(id=1, thought="", action="step1", args={}, status=StepStatus.done),
            Step(id=2, thought="", action="step2", args={}, status=StepStatus.running),
        ]
        plan = Plan(goal="Test plan", steps=steps)
        
        display = plan.format_for_display()
        assert "执行计划" in display

    def test_plan_confirm(self):
        """测试计划确认。"""
        plan = Plan(goal="Test")
        plan.confirm()
        assert plan.status == PlanStatus.confirmed

    def test_plan_start(self):
        """测试计划开始。"""
        plan = Plan(goal="Test")
        plan.start()
        assert plan.status == PlanStatus.executing

    def test_plan_complete(self):
        """测试计划完成。"""
        plan = Plan(goal="Test", steps=[Step(id=1, thought="", action="test", args={})])
        plan.start()
        plan.complete()
        assert plan.status == PlanStatus.completed


class TestIntentResult:
    """测试 IntentResult 数据类。"""

    def test_intent_result_creation(self):
        """测试创建 IntentResult。"""
        result = IntentResult(
            intent="code",
            needs_tools=True,
            intent_detail="Write code",
            confidence=0.9
        )
        
        assert result.intent == "code"
        assert result.needs_tools is True
        assert result.intent_detail == "Write code"
        assert result.confidence == 0.9

    def test_intent_result_with_missing_info(self):
        """测试带缺失信息的 IntentResult。"""
        result = IntentResult(
            intent="search",
            needs_tools=False,
            intent_detail="Info query",
            confidence=0.7,
            missing_info=["需要用户名"]
        )
        
        assert result.missing_info == ["需要用户名"]

    def test_intent_result_direct_reply(self):
        """测试带直接回复的 IntentResult。"""
        result = IntentResult(
            intent="chat",
            needs_tools=False,
            intent_detail="Chat",
            confidence=0.9,
            direct_reply="Hello!"
        )
        
        assert result.direct_reply == "Hello!"

    def test_intent_result_matched_skill(self):
        """测试匹配技能的 IntentResult。"""
        result = IntentResult(
            intent="code",
            needs_tools=True,
            intent_detail="Write Python",
            confidence=0.95,
            matched_skill="code-writing"
        )
        
        assert result.matched_skill == "code-writing"


class TestPlanStatus:
    """测试 PlanStatus 枚举。"""

    def test_all_statuses_defined(self):
        """测试所有状态都已定义。"""
        assert PlanStatus.created is not None
        assert PlanStatus.confirmed is not None
        assert PlanStatus.executing is not None
        assert PlanStatus.completed is not None
        assert PlanStatus.failed is not None
        assert PlanStatus.cancelled is not None
