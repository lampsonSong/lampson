"""reflection 路径校验测试：确保 project 沉淀不会串项目。"""

import pytest
from src.core.reflection import _check_project_path_mismatch


class TestProjectPathMismatch:
    def test_no_path_in_content(self):
        """content 里没有路径，不拦截。"""
        assert _check_project_path_mismatch("hermes", "新增了定时任务模块") is None

    def test_matching_path(self):
        """content 引用的路径与 target 匹配，不拦截。"""
        content = "源码路径: /Users/songyuhao/hermes/src/tools/cron.py"
        assert _check_project_path_mismatch("hermes", content) is None

    def test_mismatched_path(self):
        """content 引用了其他项目的路径，拦截。"""
        content = "修改了 /Users/songyuhao/lampson/src/core/reflection.py"
        result = _check_project_path_mismatch("hermes", content)
        assert result is not None
        assert "lampson" in result
        assert "hermes" in result

    def test_case_insensitive(self):
        """项目名大小写不敏感。"""
        content = "路径: /Users/songyuhao/Lampson/src/tools/test.py"
        result = _check_project_path_mismatch("lampson", content)
        assert result is None  # 匹配，不拦截
