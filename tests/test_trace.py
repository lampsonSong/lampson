"""测试 trace log 相关函数：session_store.py 中的 trace 写入和 GC。

覆盖：
- write_system_prompt_trace（hash 去重）
- write_llm_call_trace
- write_llm_error_trace
- write_tool_call_trace（arguments 序列化）
- write_tool_result_trace（inline vs hash 文件、error 处理）
- gc_tool_bodies（时间窗口清理）
- append_trace
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_memory_dir(tmp_path: Path) -> Path:
    """创建一个临时的 .lamix/memory 目录。"""
    memory = tmp_path / ".lamix" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    return memory


@pytest.fixture
def mock_session_store(temp_memory_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """替换 session_store 的路径常量，指向临时目录。"""
    from src.memory import session_store as ss
    monkeypatch.setattr(ss, "LAMIX_DIR", temp_memory_dir.parent)
    monkeypatch.setattr(ss, "SESSIONS_DIR", temp_memory_dir / "sessions")
    monkeypatch.setattr(ss, "TOOL_BODIES_DIR", temp_memory_dir / "tool_bodies")
    # 清缓存
    ss._sid_path_cache.clear()
    ss._sid_source_cache.clear()


class TestWriteSystemPromptTrace:
    """测试 write_system_prompt_trace：hash 去重行为。"""

    def test_writes_row_with_content(self, mock_session_store) -> None:
        """首次写入应有 content 字段。"""
        from src.memory import session_store as ss

        sid = "test-sys-prompt-1"
        # 创建 session 目录
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row = ss.write_system_prompt_trace(
            session_id=sid,
            content="你是一个有帮助的助手。",
        )

        assert row["type"] == "system_prompt"
        assert row["session_id"] == sid
        assert row["content"] == "你是一个有帮助的助手。"
        assert row["prompt_hash"].startswith("sha256:")
        # 验证 JSONL 写入
        jsonl_path = ss._jsonl_path(sid)
        assert jsonl_path.exists()
        with open(jsonl_path) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "system_prompt"
        assert parsed["prompt_hash"] == row["prompt_hash"]

    def test_different_content_different_hash(self, mock_session_store) -> None:
        """不同内容应产生不同 hash。"""
        from src.memory import session_store as ss

        sid = "test-sys-prompt-2"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row1 = ss.write_system_prompt_trace(session_id=sid, content="prompt A")
        row2 = ss.write_system_prompt_trace(session_id=sid, content="prompt B")

        assert row1["prompt_hash"] != row2["prompt_hash"]


class TestWriteLlmCallTrace:
    """测试 write_llm_call_trace。"""

    def test_writes_llm_call_row(self, mock_session_store) -> None:
        from src.memory import session_store as ss

        sid = "test-llm-call-1"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row = ss.write_llm_call_trace(
            session_id=sid,
            model="glm-5-flash",
            input_tokens=1500,
            output_tokens=320,
            duration_ms=1200,
            stop_reason="tool_calls",
        )

        assert row["type"] == "llm_call"
        assert row["model"] == "glm-5-flash"
        assert row["input_tokens"] == 1500
        assert row["output_tokens"] == 320
        assert row["duration_ms"] == 1200
        assert row["stop_reason"] == "tool_calls"


class TestWriteLlmErrorTrace:
    """测试 write_llm_error_trace。"""

    def test_writes_error_row(self, mock_session_store) -> None:
        from src.memory import session_store as ss

        sid = "test-llm-error-1"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row = ss.write_llm_error_trace(
            session_id=sid,
            model="glm-5-flash",
            error_type="APITimeoutError",
            detail="Request timed out after 45s",
            duration_ms=45000,
        )

        assert row["type"] == "llm_error"
        assert row["error_type"] == "APITimeoutError"
        assert row["detail"] == "Request timed out after 45s"
        assert row["duration_ms"] == 45000

    def test_detail_truncated_at_500(self, mock_session_store) -> None:
        """detail 超过 500 字应被截断。"""
        from src.memory import session_store as ss

        sid = "test-llm-error-2"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        long_detail = "x" * 600
        row = ss.write_llm_error_trace(
            session_id=sid,
            model="glm-5-flash",
            error_type="LongError",
            detail=long_detail,
            duration_ms=1000,
        )

        assert len(row["detail"]) == 500


class TestWriteToolCallTrace:
    """测试 write_tool_call_trace：arguments 序列化。"""

    def test_writes_tool_call_with_dict_args(self, mock_session_store) -> None:
        from src.memory import session_store as ss

        sid = "test-tool-call-1"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row = ss.write_tool_call_trace(
            session_id=sid,
            tool_call_id="call_001",
            name="file_read",
            arguments={"path": "~/lamix/src/core/agent.py", "offset": 0, "limit": 50},
        )

        assert row["type"] == "tool_call"
        assert row["id"] == "call_001"
        assert row["name"] == "file_read"
        # arguments 应为 JSON 字符串
        args_parsed = json.loads(row["arguments"])
        assert args_parsed["path"] == "~/lamix/src/core/agent.py"
        assert args_parsed["limit"] == 50

    def test_arguments_with_special_chars(self, mock_session_store) -> None:
        """arguments 含换行符、引号等特殊字符时序列化正确。"""
        from src.memory import session_store as ss

        sid = "test-tool-call-2"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row = ss.write_tool_call_trace(
            session_id=sid,
            tool_call_id="call_002",
            name="shell",
            arguments={"command": 'grep -r "hello\nworld" .'},
        )

        args_parsed = json.loads(row["arguments"])
        assert "\n" in args_parsed["command"]


class TestWriteToolResultTrace:
    """测试 write_tool_result_trace：inline vs hash 文件、error 处理。"""

    def test_small_result_inline(self, mock_session_store) -> None:
        """≤2KB 结果应内联，不创建文件。"""
        from src.memory import session_store as ss

        sid = "test-tool-result-1"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        small_result = "文件共 320 行..."  # 小于 2KB
        row = ss.write_tool_result_trace(
            session_id=sid,
            tool_call_id="call_001",
            result=small_result,
        )

        assert row["type"] == "tool_result"
        assert row["id"] == "call_001"
        assert row["result_inline"] == small_result
        assert "result_ref" not in row
        assert row["error"] is None
        # 不应创建 tool_bodies 文件
        assert not (ss.TOOL_BODIES_DIR).exists() or not list((ss.TOOL_BODIES_DIR).iterdir())

    def test_large_result_writes_hash_file(self, mock_session_store) -> None:
        """>2KB 结果应写 hash 文件，row 含 result_ref。"""
        from src.memory import session_store as ss

        sid = "test-tool-result-2"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)
        ss.TOOL_BODIES_DIR.mkdir(parents=True, exist_ok=True)

        large_result = "x" * 3000  # 大于 2KB
        row = ss.write_tool_result_trace(
            session_id=sid,
            tool_call_id="call_002",
            result=large_result,
        )

        assert row["type"] == "tool_result"
        assert "result_ref" in row
        assert row["result_ref"].startswith("sha256:")
        assert "result_inline" not in row
        # 验证 hash 文件存在
        hash_filename = row["result_ref"].split(":")[1] + ".json"
        hash_file = ss.TOOL_BODIES_DIR / hash_filename
        assert hash_file.exists()
        # 验证文件内容
        with open(hash_file) as f:
            body = json.load(f)
        assert body["hash"] == row["result_ref"]
        assert body["size"] == len(large_result.encode("utf-8"))

    def test_error_field_structured(self, mock_session_store) -> None:
        """工具执行失败时 error 应为结构化对象。"""
        from src.memory import session_store as ss

        sid = "test-tool-result-3"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        error_info = {"type": "TimeoutError", "message": "工具执行超时，已被 kill"}
        row = ss.write_tool_result_trace(
            session_id=sid,
            tool_call_id="call_003",
            result="[错误] 工具执行超时",
            error=error_info,
        )

        assert row["error"]["type"] == "TimeoutError"
        assert "kill" in row["error"]["message"]


class TestAppendTrace:
    """测试 append_trace：通用追加。"""

    def test_append_any_row(self, mock_session_store) -> None:
        from src.memory import session_store as ss

        sid = "test-append-1"
        (ss.SESSIONS_DIR / "2026-04-29" / "cli").mkdir(parents=True, exist_ok=True)

        row = {"ts": 1745800001000, "type": "custom_type", "session_id": sid, "foo": "bar"}
        ss.append_trace(sid, row)

        jsonl_path = ss._jsonl_path(sid)
        with open(jsonl_path) as f:
            lines = [l for l in f if l.strip()]
        parsed = json.loads(lines[0])
        assert parsed["type"] == "custom_type"
        assert parsed["foo"] == "bar"


class TestGcToolBodies:
    """测试 gc_tool_bodies：时间窗口清理。"""

    def test_deletes_expired_files(self, mock_session_store) -> None:
        """mtime 早于 ttl 的文件应被删除。"""
        from src.memory import session_store as ss

        ss.TOOL_BODIES_DIR.mkdir(parents=True, exist_ok=True)

        # 创建一个假的 hash 文件
        fake_hash_file = ss.TOOL_BODIES_DIR / "abc123def456.json"
        fake_hash_file.write_text(
            json.dumps({"hash": "sha256:abc123def456", "size": 100, "content": "test"}),
            encoding="utf-8",
        )
        # 把 mtime 改成 100 天前
        old_mtime = time.time() - (100 * 86400)
        import os; os.utime(fake_hash_file, (old_mtime, old_mtime))

        # GC（ttl=60天）
        result = ss.gc_tool_bodies(ttl_days=60)

        assert result["deleted"] == 1
        assert not fake_hash_file.exists()

    def test_keeps_recent_files(self, mock_session_store) -> None:
        """mtime 在 ttl 之内的文件应保留。"""
        from src.memory import session_store as ss

        ss.TOOL_BODIES_DIR.mkdir(parents=True, exist_ok=True)

        # 创建一个近期的 hash 文件
        recent_file = ss.TOOL_BODIES_DIR / "recent123456.json"
        recent_file.write_text(
            json.dumps({"hash": "sha256:recent123456", "size": 200, "content": "recent"}),
            encoding="utf-8",
        )

        # GC（ttl=60天）
        result = ss.gc_tool_bodies(ttl_days=60)

        assert result["deleted"] == 0
        assert recent_file.exists()
        # 清理
        recent_file.unlink()

    def test_returns_freed_bytes(self, mock_session_store) -> None:
        """返回值应包含清理的字节数。"""
        from src.memory import session_store as ss

        ss.TOOL_BODIES_DIR.mkdir(parents=True, exist_ok=True)

        fake_file = ss.TOOL_BODIES_DIR / "old789xyz.json"
        content = "x" * 1024  # 1KB
        fake_file.write_text(
            json.dumps({"hash": "sha256:old789xyz", "size": len(content), "content": content}),
            encoding="utf-8",
        )
        old_mtime = time.time() - (100 * 86400)
        import os; os.utime(fake_file, (old_mtime, old_mtime))

        result = ss.gc_tool_bodies(ttl_days=60)

        assert result["total_freed_bytes"] > 0
        assert result["deleted"] == 1
