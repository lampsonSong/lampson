"""Agent 主循环：接收用户输入，调用 LLM，处理 tool calling，返回最终回复。

启动时注入 core memory 到 system prompt；
每轮用户输入前，先用 skills manager 匹配技能并注入当轮 system 上下文。

支持两种工具调用模式：
- 原生 tool calling（supports_native_tool_calling=True）：走 OpenAI tool_calls 字段解析
- prompt-based tool calling（supports_native_tool_calling=False）：解析模型文本输出中的 <tool_call:xxx> 标签
"""

from __future__ import annotations

import json
import re
from typing import Any, TYPE_CHECKING

from src.core.llm import LLMClient
from src.core import tools as tool_registry

if TYPE_CHECKING:
    from src.skills.manager import Skill


MAX_TOOL_ROUNDS = 10

_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call:\s*(\w+)\s*>\s*(.*?)\s*</tool_call:\s*\1\s*>",
    re.DOTALL,
)


class Agent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self._tools = tool_registry.get_all_schemas()
        self.skills: dict[str, "Skill"] = {}
        self._core_memory: str = ""
        self._skills_context: str = ""
        self._tools_prompt_injected: bool = False

    def refresh_tools(self) -> None:
        """重新加载工具列表（外部注册新工具后调用）。"""
        self._tools = tool_registry.get_all_schemas()

    def set_context(self, core_memory: str = "", skills_context: str = "") -> None:
        """设置 system prompt 上下文（启动时调用一次）。"""
        self._core_memory = core_memory
        self._skills_context = skills_context
        self._tools_prompt_injected = False
        self.llm.set_system_context(core_memory=core_memory, skills_context=skills_context)

    def _inject_skill(self, user_input: str) -> str | None:
        """匹配技能并返回技能全文，用于注入本轮上下文。"""
        if not self.skills:
            return None

        from src.skills.manager import match_skill, match_skill_with_llm

        skill = match_skill(user_input, self.skills)
        if skill is None:
            skill = match_skill_with_llm(user_input, self.skills, self.llm)

        return skill.full_content if skill else None

    def _inject_tools_prompt(self) -> None:
        """在 messages 中注入工具描述（prompt-based 模式，只注入一次）。"""
        if self._tools_prompt_injected:
            return
        tools_prompt = LLMClient.format_tools_prompt(self._tools)
        self.llm.messages.append({
            "role": "system",
            "content": tools_prompt,
        })
        self._tools_prompt_injected = True

    def _run_native(self) -> str:
        """原生 tool calling 主循环。"""
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = self.llm.chat(tools=self._tools)
            except RuntimeError as e:
                return f"[LLM 错误] {e}"

            choice = response.choices[0]
            finish_reason = choice.finish_reason
            message = choice.message

            if finish_reason == "stop" or not message.tool_calls:
                return message.content or ""

            if finish_reason in ("tool_calls", "function_call") or message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    arguments = tool_call.function.arguments
                    result = tool_registry.dispatch(tool_name, arguments)
                    self.llm.add_tool_result(tool_call.id, result)

        return "[错误] 工具调用轮次超过限制，请重新提问。"

    def _run_prompt_based(self) -> str:
        """prompt-based tool calling 主循环。"""
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = self.llm.chat(tools=self._tools)
            except RuntimeError as e:
                return f"[LLM 错误] {e}"

            content = response.choices[0].message.content or ""
            match = _TOOL_CALL_PATTERN.search(content)

            if not match:
                return content

            tool_name = match.group(1).strip()
            raw_args = match.group(2).strip()

            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {}

            result = tool_registry.dispatch(tool_name, json.dumps(arguments))
            self.llm.messages.append({
                "role": "user",
                "content": f"<tool_result:{tool_name}>\n{result}\n</tool_result:{tool_name}>",
            })

        return "[错误] 工具调用轮次超过限制，请重新提问。"

    def run(self, user_input: str) -> str:
        """处理一轮用户输入，返回最终回复文本。

        如果匹配到技能，会在本轮 messages 中临时插入技能内容作为 system 提示。
        """
        skill_content = self._inject_skill(user_input)
        if skill_content:
            self.llm.messages.append({
                "role": "system",
                "content": f"[当前激活技能]\n{skill_content}",
            })

        if not self.llm.supports_native_tool_calling:
            self._inject_tools_prompt()

        self.llm.add_user_message(user_input)

        if self.llm.supports_native_tool_calling:
            return self._run_native()
        else:
            return self._run_prompt_based()

    def get_conversation_text(self) -> str:
        """导出当前对话的可读文本，供会话摘要生成使用。"""
        lines = []
        for msg in self.llm.get_history():
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system" or not content:
                continue
            prefix = "用户" if role == "user" else "Lampson"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    def generate_session_summary(self) -> str:
        """让 LLM 生成本次会话摘要，用于写入 sessions/ 目录。"""
        history = self.get_conversation_text()
        if not history.strip():
            return ""

        summary_prompt = (
            "请用 3-5 句话总结以下对话的主要内容和结论，供以后参考：\n\n"
            f"{history}"
        )
        try:
            temp_client = LLMClient(
                api_key=self.llm.client.api_key,
                base_url=str(self.llm.client.base_url),
                model=self.llm.model,
            )
            temp_client.set_system_context()
            temp_client.add_user_message(summary_prompt)
            response = temp_client.chat()
            return response.choices[0].message.content or ""
        except Exception:
            return history[:500]
