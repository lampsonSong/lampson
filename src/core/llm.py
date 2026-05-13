"""LLM 调用封装：使用 OpenAI SDK，维护 messages 多轮对话。

具体 tools 传参与响应解析由 Model Adapter 负责；本类仅提供 API 调用与消息管理。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError
from openai.types.chat import ChatCompletion

from src.core.prompt_builder import MEMORY_PATH, USER_PATH, PromptBuilder

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.core.llm import LLMClient


def _get_identity_mtimes() -> tuple[float, float]:
    """获取 MEMORY.md 和 USER.md 的当前 mtime，用于检测变更。"""
    memory_mtime = MEMORY_PATH.stat().st_mtime if MEMORY_PATH.exists() else 0.0
    user_mtime = USER_PATH.stat().st_mtime if USER_PATH.exists() else 0.0
    return (memory_mtime, user_mtime)


class LLMClient:
    """封装对兼容 OpenAI API 的调用，维护 messages 列表。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        channel: str = "cli",
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.channel = channel
        self.timeout = timeout
        self.client = OpenAI(
            api_key=api_key if api_key else "not-needed",
            base_url=base_url,
            timeout=timeout,
        )
        self.messages: list[dict[str, Any]] = []
        self._prompt_builder = PromptBuilder(model=model, channel=channel)
        self._identity_mtimes: tuple[float, float] = _get_identity_mtimes()

    def set_system_context(self) -> None:
        """设置 system prompt（通过 PromptBuilder 分层构建，含 MEMORY.md）。"""
        content = self._prompt_builder.build()
        self.messages = [{"role": "system", "content": content}]
        self._identity_mtimes = _get_identity_mtimes()

    def refresh_system_prompt(self) -> None:
        """原地刷新 system prompt 内容（不丢弃对话历史）。

        适用于 skills/projects 在对话中途被修改后，需要让后续轮次感知到变更。
        """
        new_content = self._prompt_builder.build()
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = new_content
            logger.debug("已刷新 system prompt（skills/projects 索引已更新）")
        else:
            # 没有 system message（异常情况），插入到开头
            self.messages.insert(0, {"role": "system", "content": new_content})

    def auto_refresh_if_needed(self) -> None:
        """检测 MEMORY.md / USER.md 是否变化，若是则刷新 system prompt。

        每次 LLM API 调用前由 Adapter.chat() 触发。
        """
        current = _get_identity_mtimes()
        if current != self._identity_mtimes:
            self.refresh_system_prompt()
            self._identity_mtimes = current
            logger.debug("检测到 identity 文件变更，已刷新 system prompt")

    def migrate_from(self, source: "LLMClient") -> None:
        """从另一个 LLMClient 迁移对话历史（保留自身的 system prompt）。

        用于模型切换时将对话历史迁移到新模型实例。
        只迁移 source 的非 system 消息，保持自身的 system prompt。
        """
        source_history = [m for m in source.messages if m.get("role") != "system"]
        if self.messages and self.messages[0].get("role") == "system":
            self.messages = [self.messages[0]] + source_history
        else:
            self.messages = list(source.messages)

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, tool_call_id: str, result: str) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        })

    def chat(self, timeout: float | None = None) -> ChatCompletion:
        """直接调用 chat.completions.create，返回原始响应。

        适用于不需要 adapter 解析的简单调用场景（如压缩、skill 分析等）。
        注意：不会更新 self.messages，调用方需自行管理消息状态。
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self.client.chat.completions.create(**kwargs)
