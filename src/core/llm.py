"""LLM 调用封装：使用 OpenAI SDK，维护 messages 多轮对话。

具体 tools 传参与响应解析由 Model Adapter 负责；本类仅提供 API 调用与消息管理。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError
from openai.types.chat import ChatCompletion

from src.core.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


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

    def set_system_context(self) -> None:
        """设置 system prompt（通过 PromptBuilder 分层构建）。"""
        content = self._prompt_builder.build()
        self.messages = [{"role": "system", "content": content}]

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

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, tool_call_id: str, result: str) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        })

    def chat(
        self,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        """发送当前 messages；将 assistant 回复追加到 messages。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**kwargs)
        except APITimeoutError:
            raise RuntimeError("LLM 请求超时，请检查网络连接后重试。")
        except APIConnectionError as e:
            raise RuntimeError(f"无法连接到 LLM API：{e}")
        except RateLimitError:
            raise RuntimeError("API 调用频率超限，请稍后再试。")

        assistant_msg = response.choices[0].message
        self.messages.append(assistant_msg.model_dump(exclude_none=True))
        return response

    def reset_history(self) -> None:
        """清除对话历史（保留 system prompt）。"""
        system = self.messages[0] if self.messages else None
        self.messages = [system] if system else []

    def get_history(self) -> list[dict[str, Any]]:
        return list(self.messages)

    def set_model(self, model: str) -> None:
        """切换当前模型（不影响 messages 历史）。"""
        self.model = model
        self._prompt_builder = PromptBuilder(model=model, channel=self.channel)

    def clone_for_inference(self) -> "LLMClient":
        """新建实例，仅带 system prompt（用于 /model all 等）。"""
        new_client = LLMClient(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            channel=self.channel,
            timeout=self.timeout,
        )
        if self.messages:
            new_client.messages = [self.messages[0]]
        return new_client

    def migrate_from(self, source: "LLMClient") -> None:
        """迁移对话历史（保留本 client 的 system prompt）。"""
        old_non_system = [msg for msg in source.messages if msg.get("role") != "system"]
        self.messages.extend(copy.deepcopy(old_non_system))
