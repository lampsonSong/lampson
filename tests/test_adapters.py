"""Model Adapter 单元测试。"""

from unittest.mock import MagicMock

from src.core.adapters import (
    LLMResponse,
    MiniMaxAdapter,
    OpenAICompatAdapter,
    ToolCall,
    create_adapter,
    register_adapter,
)
from src.core.adapters.base import BaseModelAdapter
from src.core.llm import LLMClient


class TestDataclasses:
    def test_tool_call_fields(self):
        tc = ToolCall(
            id="1",
            name="shell",
            arguments={"command": "ls"},
            raw_arguments='{"command": "ls"}',
        )
        assert tc.name == "shell"
        assert tc.arguments["command"] == "ls"

    def test_llm_response_fields(self):
        tc = ToolCall("1", "x", {}, "{}")
        r = LLMResponse(
            content="hi",
            tool_calls=[tc],
            finish_reason="tool_calls",
            usage=None,
            raw_response=None,
        )
        assert r.content == "hi"
        assert len(r.tool_calls) == 1


class TestCreateAdapter:
    def test_minimax_pattern(self):
        llm = MagicMock(spec=LLMClient)
        llm.model = "MiniMax-M2.7"
        adapter = create_adapter(llm)
        assert isinstance(adapter, MiniMaxAdapter)

    def test_default_openai_compat(self):
        llm = MagicMock(spec=LLMClient)
        llm.model = "glm-4-flash"
        adapter = create_adapter(llm)
        assert isinstance(adapter, OpenAICompatAdapter)

    def test_register_custom(self):
        class DummyAdapter(BaseModelAdapter):
            @property
            def supports_native_tools(self) -> bool:
                return True

            def parse_response(self, response):
                raise NotImplementedError

        register_adapter("dummytestmodel", DummyAdapter)
        llm = MagicMock(spec=LLMClient)
        llm.model = "my-dummytestmodel-v1"
        try:
            adapter = create_adapter(llm)
            assert isinstance(adapter, DummyAdapter)
        finally:
            import src.core.adapters as adapters_pkg

            adapters_pkg._MODEL_PATTERNS.pop("dummytestmodel", None)


class TestMiniMaxAdapter:
    def test_parse_xml_tool_call(self):
        llm = MagicMock(spec=LLMClient)
        adapter = MiniMaxAdapter(llm)
        xml = (
            'pre <minimax:tool_call>'
            '<invoke name="shell">'
            '<parameter name="command">ls -la</parameter>'
            "</invoke>"
            "</minimax:tool_call> tail"
        )
        msg = MagicMock()
        msg.content = xml
        msg.tool_calls = None
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = MagicMock()

        parsed = adapter.parse_response(resp)
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].name == "shell"
        assert parsed.tool_calls[0].arguments["command"] == "ls -la"
        assert parsed.finish_reason == "tool_calls"
        assert "minimax:tool_call" not in (parsed.content or "")

    def test_prefers_standard_tool_calls_when_present(self):
        llm = MagicMock(spec=LLMClient)
        adapter = MiniMaxAdapter(llm)

        fn = MagicMock()
        fn.name = "shell"
        fn.arguments = '{"command": "pwd"}'
        tc = MagicMock()
        tc.id = "call_std"
        tc.function = fn

        msg = MagicMock()
        msg.content = "ignored"
        msg.tool_calls = [tc]
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "tool_calls"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None

        parsed = adapter.parse_response(resp)
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].id == "call_std"
        assert parsed.tool_calls[0].name == "shell"

    def test_format_tool_result_default_openai(self):
        """MiniMax 使用基类默认的 tool role，不自定义格式。"""
        llm = MagicMock(spec=LLMClient)
        adapter = MiniMaxAdapter(llm)
        d = adapter.format_tool_result("minimax_0", "ok")
        assert d == {
            "role": "tool",
            "tool_call_id": "minimax_0",
            "content": "ok",
        }

    def test_strip_think_tags(self):
        """MiniMax 返回的 <think ...>...</think > 标签应被清除。"""
        llm = MagicMock(spec=LLMClient)
        adapter = MiniMaxAdapter(llm)
        content = "<think\n先分析一下...\n</think\n\n实际回复内容"
        msg = MagicMock()
        msg.content = content
        msg.tool_calls = None

        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None

        result = adapter.parse_response(resp)
        assert result.content == "实际回复内容"
        assert "<think" not in (result.content or "")

    def test_strip_think_only_returns_none(self):
        """content 只有 think 标签时应返回 None。"""
        llm = MagicMock(spec=LLMClient)
        adapter = MiniMaxAdapter(llm)
        msg = MagicMock()
        msg.content = "<think\nthinking\n</think\n"
        msg.tool_calls = None

        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None

        result = adapter.parse_response(resp)
        assert result.content is None


class TestOpenAICompatAdapter:
    def test_parse_standard_tool_calls(self):
        llm = MagicMock(spec=LLMClient)
        adapter = OpenAICompatAdapter(llm)

        fn = MagicMock()
        fn.name = "file_read"
        fn.arguments = '{"path": "/tmp/a"}'
        tc = MagicMock()
        tc.id = "c1"
        tc.function = fn

        msg = MagicMock()
        msg.content = "done"
        msg.tool_calls = [tc]
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "tool_calls"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None

        parsed = adapter.parse_response(resp)
        assert parsed.content == "done"
        assert len(parsed.tool_calls) == 1
        assert parsed.tool_calls[0].arguments["path"] == "/tmp/a"

    def test_malformed_arguments_become_empty_dict(self):
        llm = MagicMock(spec=LLMClient)
        adapter = OpenAICompatAdapter(llm)
        fn = MagicMock()
        fn.name = "shell"
        fn.arguments = "not-json{"
        tc = MagicMock()
        tc.id = "c2"
        tc.function = fn
        msg = MagicMock()
        msg.content = None
        msg.tool_calls = [tc]
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "tool_calls"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None

        parsed = adapter.parse_response(resp)
        assert parsed.tool_calls[0].arguments == {}
