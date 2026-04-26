"""模型适配器基类与统一响应结构。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openai import APIConnectionError, APITimeoutError, RateLimitError
from openai.types.chat import ChatCompletion

if TYPE_CHECKING:
    from src.core.llm import LLMClient


@dataclass
class ToolCall:
    """统一的工具调用表示。"""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass
class LLMResponse:
    """统一的 LLM 响应表示。"""

    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    usage: Any
    raw_response: Any


def tool_calls_from_openai_message(message: Any) -> list[ToolCall]:
    """从 OpenAI ChatCompletionMessage 解析标准 tool_calls。"""
    out: list[ToolCall] = []
    if not getattr(message, "tool_calls", None):
        return out
    for tc in message.tool_calls:
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            args = {}
        out.append(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=args,
                raw_arguments=tc.function.arguments,
            )
        )
    return out


class BaseModelAdapter(ABC):
    """模型适配器基类：统一 chat / 解析 / 工具结果格式。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    @property
    @abstractmethod
    def supports_native_tools(self) -> bool:
        """是否向 API 传入 tools + tool_choice（由适配器决定）。"""

    @abstractmethod
    def parse_response(self, response: ChatCompletion) -> LLMResponse:
        """从原始 API 响应解析为 LLMResponse。"""

    def format_tool_result(self, tool_call_id: str, result: str) -> dict[str, Any]:
        """默认 OpenAI 兼容：tool 角色消息。"""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        """调用 chat.completions.create；不修改 messages。"""
        kwargs: dict[str, Any] = {
            "model": self.llm.model,
            "messages": messages,
        }
        if tools and self.supports_native_tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            return self.llm.client.chat.completions.create(**kwargs)
        except APITimeoutError:
            raise RuntimeError("LLM 请求超时，请检查网络连接后重试。")
        except APIConnectionError as e:
            raise RuntimeError(f"无法连接到 LLM API：{e}")
        except RateLimitError:
            raise RuntimeError("API 调用频率超限，请稍后再试。")

    def build_system_prompt_guidance(self) -> str:
        """可由子类追加到 system 的模型专属说明；默认无。"""
        return ""
