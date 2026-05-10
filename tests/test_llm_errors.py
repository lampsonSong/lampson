"""LLM 错误分类测试：验证异常类型、fallback 逻辑、prompt 超长处理。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.adapters.base import (
    LLMError,
    LLMRetryableError,
    LLMRateLimitError,
    LLMFatalError,
    LLMContextTooLongError,
    BaseModelAdapter,
)


class MockAdapter(BaseModelAdapter):
    """测试用适配器。"""

    @property
    def supports_native_tools(self) -> bool:
        return True

    def parse_response(self, response):
        return MagicMock()


def _make_adapter() -> MockAdapter:
    """创建一个带 mock LLM client 的适配器。"""
    mock_llm = MagicMock()
    mock_llm.model = "test-model"
    mock_llm.client = MagicMock()
    return MockAdapter(llm_client=mock_llm)


class TestExceptionHierarchy(unittest.TestCase):
    """异常继承关系。"""

    def test_all_inherit_from_llm_error(self):
        for cls in (LLMRetryableError, LLMRateLimitError, LLMFatalError, LLMContextTooLongError):
            self.assertTrue(issubclass(cls, LLMError))

    def test_preserves_original_error(self):
        original = ValueError("原始错误")
        err = LLMRetryableError("包装", original_error=original)
        self.assertIs(err.original_error, original)

    def test_preserves_status_code(self):
        err = LLMFatalError("test", status_code=401)
        self.assertEqual(err.status_code, 401)

    print("✅ 异常继承和属性保留正确")


class TestChatExceptionClassification(unittest.TestCase):
    """chat() 方法的异常分类。"""

    def test_timeout_raises_retryable(self):
        """超时 → LLMRetryableError"""
        from openai import APITimeoutError
        adapter = _make_adapter()
        adapter.llm.client.chat.completions.create.side_effect = APITimeoutError("timeout")

        with self.assertRaises(LLMRetryableError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertIn("超时", str(ctx.exception))
        print("✅ 超时 → LLMRetryableError")

    def test_connection_error_raises_retryable(self):
        """网络连接错误 → LLMRetryableError"""
        from openai import APIConnectionError
        adapter = _make_adapter()
        adapter.llm.client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())

        with self.assertRaises(LLMRetryableError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertIn("无法连接", str(ctx.exception))
        print("✅ 网络错误 → LLMRetryableError")

    def test_rate_limit_raises_rate_limit_error(self):
        """频率限制/余额不足 → LLMRateLimitError"""
        from openai import RateLimitError
        adapter = _make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = '{"error": {"code": "1113", "message": "余额不足"}}'
        adapter.llm.client.chat.completions.create.side_effect = RateLimitError(
            "rate limited", response=mock_response, body=None
        )

        with self.assertRaises(LLMRateLimitError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status_code, 429)
        print("✅ 频率限制 → LLMRateLimitError")

    def test_context_too_long_raises_special_error(self):
        """prompt 超长 → LLMContextTooLongError"""
        import httpx
        adapter = _make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "context_length_exceeded: maximum context length is 128000 tokens"}'
        http_error = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_response
        )
        adapter.llm.client.chat.completions.create.side_effect = http_error

        with self.assertRaises(LLMContextTooLongError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertIn("超长", str(ctx.exception))
        print("✅ Prompt 超长 → LLMContextTooLongError")

    def test_auth_error_raises_fatal(self):
        """认证失败 → LLMFatalError"""
        import httpx
        adapter = _make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"error": "unauthorized"}'
        http_error = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_response
        )
        adapter.llm.client.chat.completions.create.side_effect = http_error

        with self.assertRaises(LLMFatalError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status_code, 401)
        print("✅ 认证失败 → LLMFatalError")

    def test_500_raises_retryable(self):
        """服务端 5xx → LLMRetryableError"""
        import httpx
        adapter = _make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = '{"error": "service unavailable"}'
        http_error = httpx.HTTPStatusError(
            "Service Unavailable", request=MagicMock(), response=mock_response
        )
        adapter.llm.client.chat.completions.create.side_effect = http_error

        with self.assertRaises(LLMRetryableError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status_code, 503)
        print("✅ 服务端 5xx → LLMRetryableError")

    def test_model_not_found_raises_fatal(self):
        """模型不存在 404 → LLMFatalError"""
        import httpx
        adapter = _make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"error": "model not found"}'
        http_error = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )
        adapter.llm.client.chat.completions.create.side_effect = http_error

        with self.assertRaises(LLMFatalError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status_code, 404)
        print("✅ 模型不存在 404 → LLMFatalError")


class TestCompactionSetSystemContext(unittest.TestCase):
    """验证 compaction 不再使用 set_system_context(core_memory=...)。"""

    def test_no_set_system_context_with_args(self):
        """compaction.py 中不应出现 set_system_context(core_memory=..."""
        with open("src/core/compaction.py", "r") as f:
            content = f.read()
        self.assertNotIn("set_system_context(core_memory=", content)
        self.assertNotIn("set_system_context(", content)
        print("✅ compaction.py 不再调用 set_system_context()")

    def test_uses_direct_messages_assignment(self):
        """compaction.py 应直接设置 messages[0]"""
        with open("src/core/compaction.py", "r") as f:
            content = f.read()
        self.assertIn('[{"role": "system", "content": _CLASSIFY_SYSTEM}]', content)
        print("✅ compaction.py 直接设置 messages")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("LLM 错误分类 + Compaction 修复测试")
    print("=" * 60 + "\n")

    unittest.main(verbosity=2)
