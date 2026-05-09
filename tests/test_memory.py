"""记忆管理器测试：模块级函数操作 MEMORY.md。"""

from pathlib import Path
from unittest.mock import patch

import pytest

import src.memory.manager as mm


@pytest.fixture(autouse=True)
def _isolate_memory(tmp_path: Path):
    """每个测试用独立的 MEMORY.md 和 SESSIONS_DIR。"""
    mem_file = tmp_path / "MEMORY.md"
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    with patch.object(mm, "MEMORY_FILE", mem_file), \
         patch.object(mm, "SESSIONS_DIR", sessions_dir):
        yield mem_file, sessions_dir


class TestLoadMemory:
    """测试 load_memory。"""

    def test_load_empty(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        assert mm.load_memory() == ""

    def test_load_existing(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mem_file.write_text("- [2024-01-01 00:00] test entry", encoding="utf-8")
        result = mm.load_memory()
        assert "test entry" in result

    def test_load_nonexistent(self, _isolate_memory):
        # tmp_path 下没创建文件
        assert mm.load_memory() == ""


class TestShowMemory:
    """测试 show_memory。"""

    def test_show_empty(self, _isolate_memory):
        assert mm.show_memory() == "长期记忆为空。"

    def test_show_existing(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mem_file.write_text("- [2024-01-01] hello", encoding="utf-8")
        result = mm.show_memory()
        assert "hello" in result
        assert "chars" in result


class TestAddMemory:
    """测试 add_memory。"""

    def test_add_first_memory(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        result = mm.add_memory("first memory")
        assert "已添加" in result
        assert mem_file.exists()
        content = mem_file.read_text(encoding="utf-8")
        assert "first memory" in content

    def test_add_multiple_memories(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mm.add_memory("first")
        mm.add_memory("second")
        content = mem_file.read_text(encoding="utf-8")
        assert "first" in content
        assert "second" in content

    def test_add_memory_size_warning(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        # 预填充超过 500 字符
        mem_file.write_text("x" * 500, encoding="utf-8")
        result = mm.add_memory("new entry")
        assert "⚠️" in result or "超过" in result


class TestSearchMemory:
    """测试 search_memory。"""

    def test_search_found(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mem_file.write_text("- [2024-01-01] Python is great\n- [2024-01-02] Rust is fast", encoding="utf-8")
        result = mm.search_memory("Python")
        assert "Python" in result

    def test_search_not_found(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mem_file.write_text("- [2024-01-01] hello world", encoding="utf-8")
        result = mm.search_memory("nonexistent_keyword_xyz")
        assert "未找到" in result


class TestForgetMemory:
    """测试 forget_memory。"""

    def test_forget_existing(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mem_file.write_text("- [2024-01-01] keep this\n- [2024-01-02] delete me please", encoding="utf-8")
        result = mm.forget_memory("delete me")
        assert "已删除" in result
        content = mem_file.read_text(encoding="utf-8")
        assert "keep this" in content
        assert "delete me" not in content

    def test_forget_not_found(self, _isolate_memory):
        mem_file, _ = _isolate_memory
        mem_file.write_text("- [2024-01-01] hello", encoding="utf-8")
        result = mm.forget_memory("nonexistent_xyz")
        assert "未找到" in result
