"""测试 tools.py - 工具注册与调度"""
import pytest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestToolRegistry:
    """工具注册表测试"""

    def test_shell_tool_registered(self):
        from src.core import tools as tools_module
        assert "shell" in tools_module._REGISTRY

    def test_file_read_tool_registered(self):
        from src.core import tools as tools_module
        assert "file_read" in tools_module._REGISTRY

    def test_file_write_tool_registered(self):
        from src.core import tools as tools_module
        names = list(tools_module._REGISTRY.keys())
        assert "file_write" in names

    def test_feishu_send_tool_registered(self):
        from src.core import tools as tools_module
        assert "feishu_send" in tools_module._REGISTRY

    def test_feishu_read_tool_registered(self):
        from src.core import tools as tools_module
        assert "feishu_read" in tools_module._REGISTRY

    def test_skill_tool_registered(self):
        from src.core import tools as tools_module
        assert "skill" in tools_module._REGISTRY

    def test_info_tool_registered(self):
        from src.core import tools as tools_module
        assert "info" in tools_module._REGISTRY

    def test_project_context_tool_registered(self):
        from src.core import tools as tools_module
        assert "project_context" in tools_module._REGISTRY

    def test_search_projects_tool_registered(self):
        from src.core import tools as tools_module
        assert "search_projects" in tools_module._REGISTRY

    def test_session_tool_registered(self):
        from src.core import tools as tools_module
        assert "session" in tools_module._REGISTRY

    def test_web_search_tool_registered(self):
        from src.core import tools as tools_module
        assert "web_search" in tools_module._REGISTRY

    def test_task_schedule_tool_registered(self):
        from src.core import tools as tools_module
        assert "task_schedule" in tools_module._REGISTRY

    def test_task_list_tool_registered(self):
        from src.core import tools as tools_module
        assert "task_list" in tools_module._REGISTRY

    def test_task_cancel_tool_registered(self):
        from src.core import tools as tools_module
        assert "task_cancel" in tools_module._REGISTRY

    def test_archive_tool_registered(self):
        from src.core import tools as tools_module
        assert "archive" in tools_module._REGISTRY

    def test_list_archived_backward_compat(self):
        from src.core.skills_tools import list_archived
        # 向后兼容别名仍可用
        result = list_archived({})
        assert isinstance(result, str)

    def test_restore_archived_backward_compat(self):
        from src.core.skills_tools import restore_archived
        # 向后兼容别名，错误情况
        result = restore_archived({})
        assert "[错误]" in result

    def test_registry_not_empty(self):
        from src.core import tools as tools_module
        assert len(tools_module._REGISTRY) > 0

    def test_all_have_callable_runner(self):
        from src.core import tools as tools_module
        for name, (schema, runner) in tools_module._REGISTRY.items():
            assert callable(runner)
