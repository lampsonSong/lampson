"""LLMClient 单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.llm import LLMClient


class TestLLMClient:
    """测试 LLMClient 的基本功能。"""

    def test_init_with_api_key(self):
        """测试初始化。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        assert client.model == "test-model"
        assert client.api_key == "test-key"
        assert client.base_url == "https://api.test.com"
        assert client.messages == []

    def test_set_system_context(self):
        """测试设置 system prompt。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.set_system_context()

        assert len(client.messages) == 1
        assert client.messages[0]["role"] == "system"
        assert len(client.messages[0]["content"]) > 0

    def test_add_user_message(self):
        """测试添加用户消息。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.add_user_message("Hello")

        assert len(client.messages) == 1
        assert client.messages[0]["role"] == "user"
        assert client.messages[0]["content"] == "Hello"

    def test_add_tool_result(self):
        """测试添加工具结果。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.add_tool_result("call_123", "tool output")

        assert len(client.messages) == 1
        assert client.messages[0]["role"] == "tool"
        assert client.messages[0]["tool_call_id"] == "call_123"
        assert client.messages[0]["content"] == "tool output"

    def test_messages_is_list(self):
        """测试 messages 是列表可直接访问。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.add_user_message("Hello")
        client.add_user_message("World")

        # messages 是公开属性，可直接访问
        assert len(client.messages) == 2
        assert client.messages[0]["content"] == "Hello"
        assert client.messages[1]["content"] == "World"

    def test_migrate_from(self):
        """测试迁移对话历史。"""
        source = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        source.set_system_context()
        source.add_user_message("Source message 1")
        source.add_user_message("Source message 2")

        target = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        target.set_system_context()

        target.migrate_from(source)

        # target 应该有: system + source 的非 system 消息
        assert len(target.messages) == 3
        assert target.messages[0]["role"] == "system"
        assert target.messages[1]["content"] == "Source message 1"
        assert target.messages[2]["content"] == "Source message 2"


class TestLLMClientChat:
    """测试 LLMClient.chat 方法。"""

    def test_chat_without_tools(self):
        """测试 chat 调用返回响应。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.set_system_context()
        client.add_user_message("Hello")

        # Mock OpenAI SDK 实例
        mock_response = MagicMock()
        mock_response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "content": "Hi there!",
        }
        mock_response.usage = None

        # 直接 patch client.chat.completions
        with patch.object(client.client.chat.completions, "create", return_value=mock_response) as mock_create:
            response = client.chat()

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["model"] == "test-model"
            assert "messages" in call_kwargs
            assert len(call_kwargs["messages"]) == 2  # system + user

    def test_chat_with_timeout(self):
        """测试带 timeout 参数的 chat 调用。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.set_system_context()
        client.add_user_message("Hello")

        mock_response = MagicMock()
        mock_response.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "content": "Response",
        }
        mock_response.usage = None

        with patch.object(client.client.chat.completions, "create", return_value=mock_response) as mock_create:
            response = client.chat(timeout=120.0)

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["timeout"] == 120.0

    def test_chat_returns_chatcompletion(self):
        """测试 chat 返回 ChatCompletion 对象。"""
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com",
            model="test-model",
        )
        client.messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Hi there!"
        mock_response.usage = MagicMock(total_tokens=50)

        with patch.object(client.client.chat.completions, "create", return_value=mock_response):
            response = client.chat()

            assert response == mock_response
            assert response.choices[0].message.content == "Hi there!"
            assert response.usage.total_tokens == 50
