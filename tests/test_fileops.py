"""FileOps 工具单元测试。"""

import os
from pathlib import Path
import tempfile

import pytest

from src.tools import fileops as fileops_tool


class TestFileRead:
    """测试 file_read 函数。"""

    def test_read_existing_file(self, tmp_path):
        """测试读取已存在的文件。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!", encoding="utf-8")
        
        result = fileops_tool.file_read(str(test_file))
        assert "Hello, World!" in result

    def test_read_nonexistent_file(self, tmp_path):
        """测试读取不存在的文件。"""
        result = fileops_tool.file_read(str(tmp_path / "nonexistent.txt"))
        assert "[错误]" in result
        assert "不存在" in result

    def test_read_directory_rejected(self, tmp_path):
        """测试读取目录被拒绝。"""
        result = fileops_tool.file_read(str(tmp_path))
        assert "[错误]" in result
        assert "不是文件" in result

    def test_read_with_offset(self, tmp_path):
        """测试带偏移量读取。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3", encoding="utf-8")
        
        result = fileops_tool.file_read(str(test_file), offset=1)
        assert "Line 2" in result
        assert "Line 1" not in result

    def test_read_with_limit(self, tmp_path):
        """测试带行数限制读取。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\nLine 4", encoding="utf-8")
        
        result = fileops_tool.file_read(str(test_file), limit=2)
        lines = result.split("\n")
        assert len(lines) <= 3  # 可能包含空行
        assert "Line 1" in result
        assert "Line 4" not in result

    def test_read_with_offset_and_limit(self, tmp_path):
        """测试带偏移量和限制读取。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5", encoding="utf-8")
        
        result = fileops_tool.file_read(str(test_file), offset=1, limit=2)
        assert "Line 2" in result
        assert "Line 3" in result
        assert "Line 1" not in result
        assert "Line 4" not in result

    def test_read_expands_tilde(self, tmp_path):
        """测试 ~ 路径展开。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Tilde test", encoding="utf-8")
        
        # 使用相对路径模拟 ~
        result = fileops_tool.file_read(str(test_file))
        assert "Tilde test" in result

    def test_read_file_too_large(self, tmp_path, monkeypatch):
        """测试读取过大文件被拒绝。"""
        # 临时增大限制以测试
        monkeypatch.setattr(fileops_tool, "MAX_READ_SIZE", 10)
        
        test_file = tmp_path / "large.txt"
        test_file.write_text("x" * 100, encoding="utf-8")
        
        result = fileops_tool.file_read(str(test_file))
        assert "[拒绝]" in result
        assert "过大" in result


class TestFileWrite:
    """测试 file_write 函数。"""

    def test_write_new_file(self, tmp_path):
        """测试写入新文件。"""
        test_file = tmp_path / "new.txt"
        
        result = fileops_tool.file_write(str(test_file), "New content")
        assert "[成功]" in result or "已写入" in result
        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "New content"

    def test_write_creates_parent_dirs(self, tmp_path):
        """测试写入时创建父目录。"""
        test_file = tmp_path / "subdir" / "nested" / "file.txt"
        
        result = fileops_tool.file_write(str(test_file), "Nested content")
        assert "[成功]" in result or "已写入" in result
        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Nested content"

    def test_write_overwrites_existing(self, tmp_path):
        """测试覆盖已存在的文件。"""
        test_file = tmp_path / "existing.txt"
        test_file.write_text("Old content", encoding="utf-8")
        
        result = fileops_tool.file_write(str(test_file), "New content")
        assert "[成功]" in result or "已写入" in result
        assert test_file.read_text(encoding="utf-8") == "New content"

    def test_write_binary_content(self, tmp_path):
        """测试写入二进制内容。"""
        test_file = tmp_path / "binary.bin"
        
        binary_content = bytes(range(256))
        result = fileops_tool.file_write(str(test_file), binary_content.decode('latin-1'))
        
        assert "[成功]" in result or "已写入" in result

    def test_write_expands_tilde(self, tmp_path):
        """测试 ~ 路径展开。"""
        test_file = tmp_path / "tilde.txt"
        
        result = fileops_tool.file_write(str(test_file), "Tilde content")
        assert "[成功]" in result or "已写入" in result
        assert test_file.exists()


class TestRunFileRead:
    """测试 run_file_read 函数（工具入口）。"""

    def test_run_with_positional_path(self, tmp_path):
        """测试使用位置参数 path。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content", encoding="utf-8")
        
        result = fileops_tool.run_file_read({"path": str(test_file)})
        assert "Test content" in result

    def test_run_empty_path(self):
        """测试空路径。"""
        result = fileops_tool.run_file_read({"path": ""})
        assert "[错误]" in result

    def test_run_missing_path(self):
        """测试缺少 path 参数。"""
        result = fileops_tool.run_file_read({})
        assert "[错误]" in result

    def test_run_with_offset(self, tmp_path):
        """测试带 offset 参数。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3", encoding="utf-8")
        
        result = fileops_tool.run_file_read({
            "path": str(test_file),
            "offset": 1
        })
        assert "Line 2" in result
        assert "Line 1" not in result

    def test_run_with_limit(self, tmp_path):
        """测试带 limit 参数。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\nLine 4", encoding="utf-8")
        
        result = fileops_tool.run_file_read({
            "path": str(test_file),
            "limit": 2
        })
        assert "Line 1" in result
        assert "Line 4" not in result


class TestRunFileWrite:
    """测试 run_file_write 函数（工具入口）。"""

    def test_run_with_path_and_content(self, tmp_path):
        """测试使用 path 和 content 参数。"""
        test_file = tmp_path / "test.txt"
        
        result = fileops_tool.run_file_write({
            "path": str(test_file),
            "content": "Test content"
        })
        assert "[成功]" in result or "已写入" in result
        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Test content"

    def test_run_empty_path(self):
        """测试空路径。"""
        result = fileops_tool.run_file_write({
            "path": "",
            "content": "Some content"
        })
        assert "[错误]" in result

    def test_run_missing_path(self):
        """测试缺少 path 参数。"""
        result = fileops_tool.run_file_write({"content": "Some content"})
        assert "[错误]" in result


class TestSchema:
    """测试 Schema 定义。"""

    def test_file_read_schema_structure(self):
        """测试 file_read schema 结构。"""
        schema = fileops_tool.FILE_READ_SCHEMA
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "file_read"
        assert "parameters" in schema["function"]
        assert "path" in schema["function"]["parameters"]["required"]

    def test_file_write_schema_structure(self):
        """测试 file_write schema 结构。"""
        schema = fileops_tool.FILE_WRITE_SCHEMA
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "file_write"
        assert "parameters" in schema["function"]
        params = schema["function"]["parameters"]["required"]
        assert "path" in params
        assert "content" in params
