"""Task Planning 模块的单元测试。"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.planning.steps import IntentResult, Plan, PlanStatus, Step, StepResult, StepStatus
from src.planning.planner import PlanParseError
from src.planning.executor import Executor, StepExecutionError
from src.planning.prompts import (
    build_context_from_history,
    _format_tool_schemas,
)


# ── Steps 数据类测试 ──


class TestStep:
    def test_default_status(self):
        step = Step(id=1, thought="test", action="shell", args={"command": "ls"})
        assert step.status == StepStatus.pending
        assert step.result is None
        assert step.error is None

    def test_to_dict(self):
        step = Step(id=1, thought="test", action="shell", args={"command": "ls"})
        d = step.to_dict()
        assert d["id"] == 1
        assert d["action"] == "shell"
        assert d["status"] == "pending"


class TestPlan:
    def test_default_values(self):
        plan = Plan()
        assert plan.status == PlanStatus.created
        assert plan.steps == []
        assert plan.is_single_step is True

    def test_is_single_step(self):
        plan = Plan(steps=[Step(id=1, thought="t", action="shell", args={})])
        assert plan.is_single_step is True

        plan2 = Plan(steps=[
            Step(id=1, thought="t", action="shell", args={}),
            Step(id=2, thought="t", action="file_read", args={}),
        ])
        assert plan2.is_single_step is False

    def test_state_transitions(self):
        plan = Plan()
        plan.confirm()
        assert plan.status == PlanStatus.confirmed

        plan.start()
        assert plan.status == PlanStatus.executing

        plan.complete()
        assert plan.status == PlanStatus.completed

    def test_invalid_transition(self):
        plan = Plan()
        with pytest.raises(ValueError):
            plan.complete()  # 从 created 直接 complete 会报错

    def test_cancel(self):
        plan = Plan()
        plan.cancel()
        assert plan.status == PlanStatus.cancelled

    def test_fail(self):
        plan = Plan()
        plan.fail()
        assert plan.status == PlanStatus.failed

    def test_done_steps(self):
        plan = Plan(steps=[
            Step(id=1, thought="t", action="shell", args={}, status=StepStatus.done),
            Step(id=2, thought="t", action="file_read", args={}, status=StepStatus.pending),
            Step(id=3, thought="t", action="file_write", args={}, status=StepStatus.done),
        ])
        assert len(plan.done_steps) == 2
        assert len(plan.pending_steps) == 1
        assert len(plan.failed_steps) == 0

    def test_get_step_by_id(self):
        plan = Plan(steps=[
            Step(id=1, thought="t", action="shell", args={}),
            Step(id=2, thought="t", action="file_read", args={}),
        ])
        assert plan.get_step_by_id(1) is not None
        assert plan.get_step_by_id(1).action == "shell"
        assert plan.get_step_by_id(99) is None

    def test_format_for_display(self):
        plan = Plan(
            plan_summary="测试计划",
            steps=[
                Step(id=1, thought="查看文件", action="file_read", args={"path": "/tmp/test"}),
            ],
        )
        text = plan.format_for_display()
        assert "测试计划" in text
        assert "file_read" in text


# ── Executor 测试 ──


class TestExecutor:
    def _make_mock_llm(self):
        llm = MagicMock()
        llm.model = "test-model"
        llm.messages = []
        llm.client = MagicMock()

        mock_choice = MagicMock()
        mock_choice.message.content = "执行完毕的汇总"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        llm.client.chat.completions.create.return_value = mock_response

        return llm

    @patch("src.planning.executor.tool_registry.dispatch")
    def test_execute_single_step(self, mock_dispatch):
        mock_dispatch.return_value = "文件内容：hello"

        llm = self._make_mock_llm()
        executor = Executor(llm=llm)
        plan = Plan(
            goal="读文件",
            steps=[Step(id=1, thought="读文件", action="file_read", args={"path": "/tmp/test"})],
        )

        result = executor.execute(plan)
        assert result == "文件内容：hello"
        assert plan.status == PlanStatus.completed
        assert plan.steps[0].status == StepStatus.done

    @patch("src.planning.executor.tool_registry.dispatch")
    def test_execute_multi_step(self, mock_dispatch):
        mock_dispatch.side_effect = ["192.168.1.40", "登录成功"]

        llm = self._make_mock_llm()
        executor = Executor(llm=llm)
        plan = Plan(
            goal="SSH登录",
            steps=[
                Step(id=1, thought="查IP", action="file_read", args={"path": "/hosts"}),
                Step(id=2, thought="SSH", action="shell", args={"command": "ssh root@$prev.result"}),
            ],
        )

        result = executor.execute(plan)
        assert plan.status == PlanStatus.completed
        assert plan.steps[0].result == "192.168.1.40"
        # 第二步的 $prev.result 应该被替换
        call_args = mock_dispatch.call_args_list[1]
        assert "192.168.1.40" in str(call_args)

    @patch("src.planning.executor.tool_registry.dispatch")
    def test_execute_with_failure(self, mock_dispatch):
        """步骤失败重试后仍失败，计划标记为 failed。"""
        mock_dispatch.side_effect = RuntimeError("连接超时")

        llm = self._make_mock_llm()
        executor = Executor(llm=llm, max_retries=2)
        plan = Plan(
            goal="测试失败",
            steps=[Step(id=1, thought="会失败的步骤", action="shell", args={"command": "bad"})],
        )

        result = executor.execute(plan)
        assert "失败" in result
        assert plan.status == PlanStatus.failed

    @patch("src.planning.executor.tool_registry.dispatch")
    def test_resolve_goal_ref(self, mock_dispatch):
        """$goal 引用应被替换为用户原始目标。"""
        mock_dispatch.return_value = "搜索结果"

        llm = self._make_mock_llm()
        executor = Executor(llm=llm)
        plan = Plan(
            goal="查找Python教程",
            steps=[Step(id=1, thought="搜索", action="web_search", args={"query": "$goal"})],
        )

        executor.execute(plan)
        call_args = mock_dispatch.call_args_list[0]
        # 确认 $goal 被替换
        args = call_args[0][1] if isinstance(call_args[0][1], dict) else json.loads(call_args[0][1])
        assert args["query"] == "查找Python教程"

    @patch("src.planning.executor.tool_registry.dispatch")
    def test_step_prev_ref_no_previous(self, mock_dispatch):
        """第一步引用 $prev.result 应该失败。"""
        llm = self._make_mock_llm()
        executor = Executor(llm=llm, max_retries=1)
        plan = Plan(
            goal="test",
            steps=[Step(id=1, thought="test", action="shell", args={"command": "$prev.result"})],
        )

        result = executor.execute(plan)
        assert "失败" in result or plan.status == PlanStatus.failed

    @patch("src.planning.executor.tool_registry.dispatch")
    def test_on_step_end_callback(self, mock_dispatch):
        """回调函数应该被调用。"""
        mock_dispatch.return_value = "结果"

        llm = self._make_mock_llm()
        callback_results = []

        def on_step_end(step, result):
            callback_results.append((step.id, result.status))

        executor = Executor(llm=llm, on_step_end=on_step_end)
        plan = Plan(
            goal="test",
            steps=[Step(id=1, thought="test", action="shell", args={"command": "echo hi"})],
        )

        executor.execute(plan)
        assert len(callback_results) == 1
        assert callback_results[0] == (1, "success")


# ── Prompts 测试 ──


class TestPrompts:
    def test_build_context_from_history(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]
        ctx = build_context_from_history(messages)
        assert "system prompt" not in ctx
        assert "用户: 你好" in ctx
        assert "助手: 你好呀" in ctx

    def test_build_context_no_truncation(self):
        """build_context_from_history 不再截断，由 compaction 统一处理长度。"""
        messages = [
            {"role": "user", "content": "x" * 5000},
        ]
        ctx = build_context_from_history(messages)
        assert len(ctx) > 5000  # 不截断，完整返回
        assert "x" * 5000 in ctx

    def test_format_tool_schemas(self):
        schemas = [
            {"function": {"name": "shell", "description": "执行命令", "parameters": {"properties": {"command": {"type": "string", "description": "命令"}}, "required": ["command"]}}},
        ]
        text = _format_tool_schemas(schemas)
        assert "shell" in text
        assert "必填" in text
