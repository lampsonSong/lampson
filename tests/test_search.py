"""搜索工具测试：run(params) 统一入口。"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.tools.search import run


class TestSearchFiles:
    """测试 files 模式。"""

    def test_search_files_finds_match(self, tmp_path: Path):
        """文件名搜索能找到匹配。"""
        (tmp_path / "hello.py").write_text("print('hi')", encoding="utf-8")
        result = run({"mode": "files", "pattern": "*.py", "path": str(tmp_path)})
        assert "hello.py" in result

    def test_search_files_no_match(self, tmp_path: Path):
        """文件名搜索无匹配。"""
        (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
        result = run({"mode": "files", "pattern": "*.xyz", "path": str(tmp_path)})
        # 无匹配时不报错
        assert isinstance(result, str)

    def test_search_files_invalid_path(self):
        """无效路径报错。"""
        result = run({"mode": "files", "pattern": "*.py", "path": "/nonexistent/path/xyz"})
        assert "错误" in result


class TestSearchContent:
    """测试 content 模式。"""

    def test_search_content_finds_match(self, tmp_path: Path):
        """内容搜索能找到匹配。"""
        (tmp_path / "test.py").write_text("def hello_world():\n    pass\n", encoding="utf-8")
        result = run({"mode": "content", "pattern": "hello_world", "path": str(tmp_path)})
        assert "hello_world" in result

    def test_search_content_no_match(self, tmp_path: Path):
        """内容搜索无匹配。"""
        (tmp_path / "test.py").write_text("nothing here", encoding="utf-8")
        result = run({"mode": "content", "pattern": "nonexistent_pattern_xyz", "path": str(tmp_path)})
        assert isinstance(result, str)

    def test_search_content_invalid_mode(self):
        """无效 mode 报错。"""
        result = run({"mode": "invalid", "pattern": "test"})
        assert "错误" in result

    def test_search_content_empty_pattern(self):
        """空 pattern 报错。"""
        result = run({"mode": "content", "pattern": ""})
        assert "错误" in result

    def test_search_content_long_pattern(self):
        """超长 pattern 报错。"""
        result = run({"mode": "content", "pattern": "x" * 2000})
        assert "错误" in result

    def test_search_content_with_file_glob(self, tmp_path: Path):
        """带 file_glob 过滤。"""
        (tmp_path / "test.py").write_text("findme", encoding="utf-8")
        (tmp_path / "test.js").write_text("findme", encoding="utf-8")
        result = run({"mode": "content", "pattern": "findme", "path": str(tmp_path), "file_glob": "*.py"})
        assert "test.py" in result


class TestSearchEdgeCases:
    """测试边界情况。"""

    def test_missing_mode(self):
        """缺少 mode 报错。"""
        result = run({"pattern": "test"})
        assert "错误" in result

    def test_missing_pattern(self):
        """缺少 pattern 报错。"""
        result = run({"mode": "files"})
        assert "错误" in result
