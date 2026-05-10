"""测试已注册的工具名和 schema 正确性。"""

from src.core.tools import get_all_schemas, dispatch, validate_tool_schema


class TestToolRegistry:
    """测试工具注册表。"""

    def test_skill_registered(self):
        """skill 工具已注册。"""
        names = [s["function"]["name"] for s in get_all_schemas()]
        assert "skill" in names

    def test_info_registered(self):
        """info 工具已注册。"""
        names = [s["function"]["name"] for s in get_all_schemas()]
        assert "info" in names

    def test_all_schemas_have_name(self):
        """所有 schema 都有 function.name。"""
        for schema in get_all_schemas():
            assert "function" in schema
            assert "name" in schema["function"]

    def test_dispatch_unknown_tool(self):
        """调用不存在的工具返回错误。"""
        result = dispatch("nonexistent_tool_xyz", {})
        assert "未知工具" in result

    def test_get_all_schemas_returns_list(self):
        """get_all_schemas 返回列表。"""
        schemas = get_all_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) > 0


class TestValidateToolSchema:
    """测试 schema 校验函数。"""

    def test_valid_schema(self):
        """合法 schema 通过校验。"""
        schema = {
            "type": "function",
            "function": {
                "name": "test_tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        errors = validate_tool_schema(schema)
        assert errors == []

    def test_missing_type(self):
        """缺少 type 字段报错。"""
        schema = {"function": {"name": "x", "parameters": {}}}
        errors = validate_tool_schema(schema)
        assert len(errors) > 0

    def test_missing_function(self):
        """缺少 function 字段报错。"""
        schema = {"type": "function"}
        errors = validate_tool_schema(schema)
        assert len(errors) > 0
