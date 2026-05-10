"""测试 error_log 模块的基本功能。"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# 在 import 前先 mock 路径
_tmpdir = tempfile.mkdtemp()
_mock_errors_log = Path(_tmpdir) / "errors.jsonl"


@pytest.fixture(autouse=True)
def _use_tmp_log(monkeypatch):
    """每个测试用临时文件。"""
    import src.core.error_log as mod
    monkeypatch.setattr(mod, "ERRORS_LOG", _mock_errors_log)
    _mock_errors_log.unlink(missing_ok=True)
    yield
    _mock_errors_log.unlink(missing_ok=True)


def _read_records():
    """读取所有记录。"""
    if not _mock_errors_log.exists():
        return []
    records = []
    with open(_mock_errors_log) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def test_log_tool_error():
    from src.core.error_log import log_error, SOURCE_TOOL

    record = log_error(
        "ToolExecutionError",
        "工具 shell 执行异常：timeout",
        SOURCE_TOOL,
        session_id="test-session-1",
        tool_name="shell",
        tool_arguments={"command": "sleep 100"},
        tool_result="[错误] 工具 shell 执行异常：timeout",
    )

    assert record["error_type"] == "ToolExecutionError"
    assert record["source"] == "tool"
    assert record["session_id"] == "test-session-1"
    assert record["tool_name"] == "shell"

    records = _read_records()
    assert len(records) == 1
    assert records[0]["error_type"] == "ToolExecutionError"


def test_log_llm_error_with_context():
    from src.core.error_log import log_error, SOURCE_LLM

    messages = [
        {"role": "system", "content": "You are Lampson."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "tc_1", "function": {"name": "shell", "arguments": '{"command":"ls"}'}}
        ]},
        {"role": "tool", "tool_call_id": "tc_1", "content": "file1.txt\nfile2.txt"},
    ]

    try:
        raise RuntimeError("API returned 400: invalid tool_call")
    except RuntimeError as e:
        record = log_error(
            "LLMFatalError",
            "API returned 400: invalid tool_call",
            SOURCE_LLM,
            session_id="test-session-2",
            detail={"model": "gpt-4", "duration_ms": 1234},
            messages_snapshot=messages,
            exception=e,
        )

    assert record["source"] == "llm"
    assert record["detail"]["model"] == "gpt-4"
    assert record["context"]["total_count"] == 4
    assert len(record["context"]["tail"]) == 4
    assert record["context"]["tail"][2]["role"] == "assistant"
    assert len(record["context"]["tail"][2]["tool_calls"]) == 1
    assert "traceback" in record


def test_messages_truncation():
    from src.core.error_log import log_error, SOURCE_LLM

    # 30 条消息，只保留最近 20 条
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(30)]

    record = log_error(
        "TestError", "test", SOURCE_LLM,
        messages_snapshot=messages,
    )

    assert record["context"]["total_count"] == 30
    assert len(record["context"]["tail"]) == 20
    # 最早的是 msg 10（跳过了 0-9）
    assert "msg 10" in record["context"]["tail"][0]["content"]


def test_query_recent_errors():
    from src.core.error_log import log_error, query_recent_errors, SOURCE_TOOL, SOURCE_LLM

    log_error("E1", "tool error", SOURCE_TOOL, session_id="s1")
    log_error("E2", "llm error", SOURCE_LLM, session_id="s1")
    log_error("E3", "tool error 2", SOURCE_TOOL, session_id="s2")

    # 全部
    all_errors = query_recent_errors(limit=10)
    assert len(all_errors) == 3

    # 按 source 过滤
    tool_errors = query_recent_errors(source="tool")
    assert len(tool_errors) == 2

    # 按 session_id 过滤
    s1_errors = query_recent_errors(session_id="s1")
    assert len(s1_errors) == 2

    # 最新在前
    assert all_errors[0]["error_type"] == "E3"
