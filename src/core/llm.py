"""LLM 调用封装：使用 OpenAI SDK 对接智谱 GLM，支持 tool calling 和多轮对话。

支持两种工具调用模式：
- supports_native_tool_calling=True：走 OpenAI 原生 tool_calls（默认）
- supports_native_tool_calling=False：走 prompt-based tool calling，工具描述注入 system prompt
"""

from __future__ import annotations

import copy
import json
from typing import Any

from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError
from openai.types.chat import ChatCompletion

from src.core.prompt_builder import PromptBuilder


SYSTEM_PROMPT = """你是 Lampson，一个运行在终端的 CLI 智能助手。你可以：
- 通过工具执行 shell 命令
- 读写本地文件
- 搜索网页
- 发送和接收飞书消息

在回复时请简洁、直接，优先使用工具完成任务。如果不确定用户意图，先确认再行动。
危险操作（删除文件、修改系统配置等）执行前必须让用户确认。"""


class LLMClient:
    """封装对 GLM 的调用，维护 messages 列表实现多轮对话。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        supports_native_tool_calling: bool = True,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.supports_native_tool_calling = supports_native_tool_calling
        self.client = OpenAI(
            api_key=api_key if api_key else "not-needed",
            base_url=base_url,
            timeout=60.0,
        )
        self.messages: list[dict[str, Any]] = []
        self._prompt_builder = PromptBuilder(model=model)
        self._pending_tools: list[dict[str, Any]] = []

    def set_system_context(self, core_memory: str = "") -> None:
        """设置 system prompt（通过 PromptBuilder 分层构建）。

        Skills 全文不再注入，改为 skills index + 按需加载。
        """
        content = self._prompt_builder.build(core_memory=core_memory)
        self.messages = [{"role": "system", "content": content}]

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, tool_call_id: str, result: str) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        })

    def get_pending_tools(self) -> list[dict[str, Any]]:
        """返回当前待注入的 tools schema（prompt-based 模式下使用）。"""
        return list(self._pending_tools)

    @staticmethod
    def format_tools_prompt(tools: list[dict[str, Any]]) -> str:
        """将工具 schema 列表格式化为 prompt-based tool calling 的文本说明。"""
        lines = [
            "你可以使用以下工具来完成任务。当你需要调用工具时，请严格按照以下格式输出，不要输出其他内容：",
            "",
            "<tool_call:工具名>",
            '{"参数名": "参数值", ...}',
            "</tool_call:工具名>",
            "",
            "可用工具列表：",
        ]

        for tool in tools:
            fn = tool.get("function", tool)
            name = fn.get("name", "")
            description = fn.get("description", "")
            parameters = fn.get("parameters", {})
            props = parameters.get("properties", {})
            required_fields = parameters.get("required", [])

            lines.append(f"\n### {name}")
            lines.append(f"描述：{description}")

            if props:
                lines.append("参数：")
                for param_name, param_info in props.items():
                    param_type = param_info.get("type", "string")
                    param_desc = param_info.get("description", "")
                    default_val = param_info.get("default")
                    is_required = param_name in required_fields

                    required_str = "必填" if is_required else "可选"
                    default_str = f", 默认{default_val}" if default_val is not None and not is_required else ""
                    lines.append(f"- {param_name} ({param_type}, {required_str}{default_str}): {param_desc}")

        lines.extend([
            "",
            "注意：",
            "1. 一次只能调用一个工具",
            "2. 调用工具后等待结果，再决定下一步",
            "3. 如果不需要工具，直接回复用户",
        ])

        return "\n".join(lines)

    def chat(
        self,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        """发送当前 messages 并返回 completion，处理常见异常。

        原生模式：tools 参数直接传给 SDK。
        prompt-based 模式：不传 tools 给 SDK，将 tools 暂存到 _pending_tools 供外部获取。
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
        }

        if tools and self.supports_native_tool_calling:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        elif tools and not self.supports_native_tool_calling:
            self._pending_tools = list(tools)

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
        self._prompt_builder = PromptBuilder(model=model)

    def clone_for_inference(self) -> "LLMClient":
        """创建一个新实例，仅带 system prompt（用于 /model all 临时查询）。

        messages[0]（system prompt）直接引用，不拷贝；其余消息为空。
        既避免并发竞态（独立 client 实例），又避免无用的深拷贝。
        """
        new_client = LLMClient(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            supports_native_tool_calling=self.supports_native_tool_calling,
        )
        # 只保留 system prompt，不拷贝历史
        if self.messages:
            new_client.messages = [self.messages[0]]
        return new_client

    def migrate_from(self, source: "LLMClient") -> None:
        """从另一个 LLMClient 迁移对话历史（保留 system prompt 之外的记录）。

        切换模型时调用：新 client 保留自己的 system prompt（因为不同模型可能
        需要 PromptBuilder 生成不同的适配层），但继承用户的对话历史。
        """
        # 保留新 client 的 system prompt（messages[0]），追加旧 client 的非 system 消息
        old_non_system = [msg for msg in source.messages if msg.get("role") != "system"]
        self.messages.extend(copy.deepcopy(old_non_system))
