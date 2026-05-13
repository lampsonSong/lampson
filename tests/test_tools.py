"""测试 tools.py - 工具注册与调度"""
import pytest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestToolRegistry:
    """工具注册表测试"""

    def test_shell_tool_registered(self):
        """测试 shell 工具已注册"""
        from src.core import tools as tools_module
        
        assert "shell" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["shell"]
        assert schema["function"]["name"] == "shell"
        assert callable(runner)

    def test_file_read_tool_registered(self):
        """测试 file_read 工具已注册"""
        from src.core import tools as tools_module
        
        assert "file_read" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["file_read"]
        assert schema["function"]["name"] == "file_read"

    def test_file_write_tool_registered(self):
        """测试 file_write 工具已注册"""
        from src.core import tools as tools_module
        
        assert "file_write" in tools_module._REGISTRY["file_write"]
        schema, runner = tools_module._REGISTRY["file_write"]
        assert schema["function"]["name"] == "file_write"

    def test_feishu_send_tool_registered(self):
        """测试 feishu_send 工具已注册"""
        from src.core import tools as tools_module
        
        assert "feishu_send" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["feishu_send"]
        assert schema["function"]["name"] == "feishu_send"

    def test_feishu_read_tool_registered(self):
        """测试 feishu_read 工具已注册"""
        from src.core import tools as tools_module
        
        assert "feishu_read" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["feishu_read"]
        assert schema["function"]["name"] == "feishu_read"

    def test_skill_tool_registered(self):
        """测试 skill 工具已注册"""
        from src.core import tools as tools_module
        
        assert "skill" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["skill"]
        assert schema["function"]["name"] == "skill"

    def test_info_tool_registered(self):
        """测试 info 工具已注册"""
        from src.core import tools as tools_module
        
        assert "info" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["info"]
        assert schema["function"]["name"] == "info"

    def test_project_context_tool_registered(self):
        """测试 project_context 工具已注册"""
        from src.core import tools as tools_module
        
        assert "project_context" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["project_context"]
        assert schema["function"]["name"] == "project_context"

    def test_search_projects_tool_registered(self):
        """测试 search_projects 工具已注册"""
        from src.core import tools as tools_module
        
        assert "search_projects" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["search_projects"]
        assert schema["function"]["name"] == "search_projects"

    def test_session_tool_registered(self):
        """测试 session 工具已注册"""
        from src.core import tools as tools_module
        
        assert "session" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["session"]
        assert schema["function"]["name"] == "session"

    def test_web_search_tool_registered(self):
        """测试 web_search 工具已注册"""
        from src.core import tools as tools_module
        
        assert "web_search" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["web_search"]
        assert schema["function"]["name"] == "web_search"

    def test_task_schedule_tool_registered(self):
        """测试 task_schedule 工具已注册"""
        from src.core import tools as tools_module
        
        assert "task_schedule" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["task_schedule"]
        assert schema["function"]["name"] == "task_schedule"

    def test_task_list_tool_registered(self):
        """测试 task_list 工具已注册"""
        from src.core import tools as tools_module
        
        assert "task_list" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["task_list"]
        assert schema["function"]["name"] == "task_list"

    def test_task_cancel_tool_registered(self):
        """测试 task_cancel 工具已注册"""
        from src.core import tools as tools_module
        
        assert "task_cancel" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["task_cancel"]
        assert schema["function"]["name"] == "task_cancel"

    def test_reflect_tool_registered(self):
        """测试 reflect_and_learn 工具已注册"""
        from src.core import tools as tools_module
        
        assert "reflect_and_learn" in tools_module._REGISTRY
        schema, runner = tools_module._REGISTRY["reflect_and_learn"]
        assert schema["function"]["name"] == "reflect_and_learn"


class TestToolDispatch:
    """工具调度测试"""

    def test_get_tools_returns_schemas(self):
        """测试 get_tools 返回所有 schema"""
        from src.core.tools import get_tools
        
        tools = get_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0
        
        # 检查返回的是 schema
        for tool in tools:
            assert "function" in tool
            assert "name" in tool["function"]

    def test_get_tool_schema(self):
        """测试 get_tool_schema 获取单个 schema"""
        from src.core.tools import get_tool_schema
        
        schema = get_tool_schema("shell")
        assert schema is not None
        assert schema["function"]["name"] == "shell"

    def test_get_tool_schema_not_found(self):
        """测试获取不存在的工具返回 None"""
        from src.core.tools import get_tool_schema
        
        schema = get_tool_schema("nonexistent_tool_xyz")
        assert schema is None
