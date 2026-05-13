"""模型适配器基类与统一响应结构。

异常分类：
- LLMRetryableError：可重试（超时、网络断开、服务端 5xx）→ 尝试 fallback
- LLMRateLimitError：频率限制 / 余额不足 → 尝试 fallback（同供应商其他模型可能可用）
- LLMFatalError：不可重试（认证失败、参数错误）→ 直接报错
- LLMContextTooLongError：prompt 超长 → 触发压缩，不 fallback
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from openai.types.chat import ChatCompletion

if TYPE_CHECKING:
    from src.core.llm import LLMClient

logger = logging.getLogger(__name__)


# ── 自定义异常 ───────────────────────────────────────────────────────────


class LLMError(Exception):
    """LLM 调用失败的基类。保留原始异常和状态码。"""

    def __init__(
        self,
        message: str,
        original_error: Exception | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.status_code = status_code


class LLMRetryableError(LLMError):
    """可重试的错误（超时、网络断开、服务端 5xx）。"""


class LLMRateLimitError(LLMError):
    """频率限制 / 余额不足——可以 fallback 到其他模型重试。"""


class LLMFatalError(LLMError):
    """不可重试的错误（认证失败、参数错误）。"""


class LLMContextTooLongError(LLMError):
    """prompt 超长——需要触发压缩，而不是 fallback。"""


# ── 数据类 ───────────────────────────────────────────────────────────────


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
        timeout: float | None = None,
    ) -> ChatCompletion:
        """调用 chat.completions.create；不修改 messages。

        每次调用前自动检测 MEMORY.md / USER.md 是否变更，若是则刷新 system prompt。
        抛出自定义异常，保留原始错误信息供上层决策。
        """
        # 自动检测 identity 文件变更并刷新 system prompt
        self.llm.auto_refresh_if_needed()

        kwargs: dict[str, Any] = {
            "model": self.llm.model,
            "messages": messages,
        }
        if tools and self.supports_native_tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            return self.llm.client.chat.completions.create(**kwargs)
        except APITimeoutError as e:
            raise LLMRetryableError(
                f"LLM 请求超时（{self.llm.model}）",
                original_error=e,
            )
        except APIConnectionError as e:
            raise LLMRetryableError(
                f"无法连接到 LLM API（{self.llm.model}）：{e}",
                original_error=e,
            )
        except RateLimitError as e:
            # 频率限制 / 余额不足 → 可 fallback 到同供应商其他模型
            body = ""
            if hasattr(e, "response") and hasattr(e.response, "text"):
                body = e.response.text[:200]
            logger.warning(f"RateLimitError ({self.llm.model}): {body}")
            raise LLMRateLimitError(
                f"API 频率限制或余额不足（{self.llm.model}）：{body}",
                original_error=e,
                status_code=429,
            )
        except APIStatusError as e:
            # OpenAI SDK 统一异常：覆盖 400/401/403/404/500 等所有 HTTP 错误
            status_code = e.status_code
            body = str(e.message or "")[:300]
            return self._handle_http_status_error(status_code, body, e)
        except httpx.HTTPStatusError as e:
            # 兜底：httpx 直接抛出的 HTTP 错误（非 OpenAI SDK 路径）
            status_code = e.response.status_code
            body = ""
            if hasattr(e.response, "text"):
                body = e.response.text[:300]
            return self._handle_http_status_error(status_code, body, e)
        except httpx.HTTPError as e:
            # 其他网络错误（DNS、连接重置等）
            raise LLMRetryableError(
                f"网络错误（{self.llm.model}）：{e}",
                original_error=e,
            )

    def _handle_http_status_error(
        self,
        status_code: int,
        body: str,
        original_error: Exception,
    ) -> ChatCompletion:
        """根据 HTTP 状态码分类抛出不同异常。"""
        model = self.llm.model
        if status_code == 401:
            raise LLMFatalError(
                f"认证失败（{model}）：{body}",
                original_error=original_error,
                status_code=status_code,
            )
        elif status_code == 400:
            # 检查是否是 prompt 超长
            body_lower = body.lower()
            if any(kw in body_lower for kw in ("context", "token", "length", "max_tokens", "too many")):
                raise LLMContextTooLongError(
                    f"Prompt 超长（{model}）：{body[:200]}",
                    original_error=original_error,
                    status_code=status_code,
                )
            raise LLMFatalError(
                f"请求参数错误（{model}）：{body[:200]}",
                original_error=original_error,
                status_code=status_code,
            )
        elif status_code == 403:
            raise LLMFatalError(
                f"权限不足（{model}）：{body}",
                original_error=original_error,
                status_code=status_code,
            )
        elif status_code == 404:
            raise LLMFatalError(
                f"模型不存在（{model}）：{body}",
                original_error=original_error,
                status_code=status_code,
            )
        elif 500 <= status_code < 600:
            raise LLMRetryableError(
                f"服务端错误 {status_code}（{model}）：{body[:200]}",
                original_error=original_error,
                status_code=status_code,
            )
        else:
            raise LLMFatalError(
                f"HTTP {status_code}（{model}）：{body[:200]}",
                original_error=original_error,
                status_code=status_code,
            )

    def build_system_prompt_guidance(self) -> str:
        """可由子类追加到 system 的模型专属说明；默认无。"""
        return ""
