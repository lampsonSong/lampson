"""OpenAI 标准 tool_calls 格式的适配器（GLM、DeepSeek、Qwen 等）。"""

from __future__ import annotations

from openai.types.chat import ChatCompletion

from src.core.adapters.base import BaseModelAdapter, LLMResponse, tool_calls_from_openai_message


class OpenAICompatAdapter(BaseModelAdapter):
    """解析标准 OpenAI Chat Completions 的 message.tool_calls。"""

    @property
    def supports_native_tools(self) -> bool:
        return True

    def parse_response(self, response: ChatCompletion) -> LLMResponse:
        message = response.choices[0].message
        tool_calls = tool_calls_from_openai_message(message)
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=response.choices[0].finish_reason or "stop",
            usage=response.usage,
            raw_response=response,
        )
