"""skill_audit 模块测试。"""

import pytest
from src.core.skill_audit import (
    start_audit,
    end_audit,
    record_tool_call,
    record_llm_output,
    clear_audit,
    get_active_audit,
)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    clear_audit()


class TestStartAudit:
    def test_parse_numbered_steps(self):
        body = """## 步骤

1. **语法检查**：python -m py_compile
2. **测试用例**：编写并运行测试
3. **模拟场景**：端到端验证
"""
        audit = start_audit("test-skill", body)
        assert audit is not None
        assert len(audit.steps) == 3
        assert audit.steps[0].title == "语法检查"
        assert audit.steps[1].title == "测试用例"
        assert audit.steps[2].title == "模拟场景"

    def test_no_steps_returns_none(self):
        body = "这是一个简单的描述，没有编号步骤。"
        audit = start_audit("no-steps", body)
        assert audit is None

    def test_mixed_content_only_parses_numbered(self):
        body = """一些描述文字

1. **第一步**：做这个

- 不是编号步骤
- 也不是

2. **第二步**：做那个
"""
        audit = start_audit("mixed", body)
        assert audit is not None
        assert len(audit.steps) == 2


class TestAuditCompletion:
    def test_all_steps_completed(self):
        body = """1. **语法检查**：检查语法
2. **测试用例**：写测试"""
        audit = start_audit("full", body)
        assert audit is not None

        record_tool_call("shell", '{"command": "python -m py_compile test.py"}')
        record_tool_call("shell", '{"command": "pytest test.py"}')

        reminder = end_audit()
        assert reminder is None  # 全部完成，无提醒

    def test_missing_step_generates_reminder(self):
        body = """1. **语法检查**：检查语法
2. **测试用例**：写测试
3. **模拟场景**：端到端验证"""
        audit = start_audit("partial", body)
        assert audit is not None

        record_tool_call("shell", '{"command": "python -m py_compile test.py"}')
        # 只做了语法检查，没做测试和模拟

        reminder = end_audit()
        assert reminder is not None
        assert "测试用例" in reminder
        assert "模拟场景" in reminder
        assert "语法检查" not in reminder  # 已完成的不再出现

    def test_unknown_step_auto_passes(self):
        body = """1. **自定义步骤**：没有预定义关键词"""
        audit = start_audit("custom", body)
        assert audit is not None
        # 没有任何 tool call
        reminder = end_audit()
        assert reminder is None  # 未知步骤不误报


class TestClearAudit:
    def test_clear_resets_state(self):
        body = """1. **语法检查**：检查"""
        start_audit("clear-test", body)
        assert get_active_audit() is not None

        clear_audit()
        assert get_active_audit() is None

    def test_end_after_clear_returns_none(self):
        body = """1. **语法检查**：检查"""
        start_audit("clear-test2", body)
        clear_audit()
        reminder = end_audit()
        assert reminder is None


class TestRecordFunctions:
    def test_record_without_audit_is_noop(self):
        # 不启动审计也能正常调用，不报错
        record_tool_call("shell", "some args")
        record_llm_output("some output")

    def test_records_are_used_for_evaluation(self):
        body = """1. **语法检查**：检查"""
        audit = start_audit("record-test", body)
        assert audit is not None

        record_tool_call("shell", "python -m py_compile foo.py")
        record_llm_output("语法检查通过了")

        reminder = end_audit()
        assert reminder is None  # tool_call 中的 py_compile 触发了完成
