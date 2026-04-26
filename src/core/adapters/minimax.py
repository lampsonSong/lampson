"""MiniMax：content 内嵌 <minimax:tool_call> XML 时的解析适配。"""

from __future__ import annotations

import json
import re

from openai.types.chat import ChatCompletion

from src.core.adapters.base import (
    BaseModelAdapter,
    LLMResponse,
    ToolCall,
    tool_calls_from_openai_message,
)


class MiniMaxAdapter(BaseModelAdapter):
    """先尝试标准 tool_calls，否则解析 content 中的 MiniMax XML。"""

    _TOOL_CALL_RE = re.compile(
        r"<minimax:tool_call>(.*?)</minimax:tool_call>",
        re.DOTALL,
    )
    _INVOKE_RE = re.compile(
        r'<invoke\s+name="(\w+)">(.*?)</invoke>',
        re.DOTALL,
    )
    _PARAM_RE = re.compile(
        r'<parameter\s+name="(\w+)">(.*?)</parameter>',
        re.DOTALL,
    )
    # MiniMax 的思考过程标签（<think\n...\n</think\n 格式，无闭合尖括号）
    _THINK_RE = re.compile(
        r"<think\b.*?</think\b[> \t]*\n?",
        re.DOTALL,
    )

    @property
    def supports_native_tools(self) -> bool:
        return True

    def parse_response(self, response: ChatCompletion) -> LLMResponse:
        message = response.choices[0].message
        content = message.content or ""

        if message.tool_calls:
            clean = self._strip_think(content) or None
            return LLMResponse(
                content=clean,
                tool_calls=tool_calls_from_openai_message(message),
                finish_reason=response.choices[0].finish_reason or "stop",
                usage=response.usage,
                raw_response=response,
            )

        tool_calls = self._parse_minimax_xml(content)
        finish = "tool_calls" if tool_calls else (response.choices[0].finish_reason or "stop")
        clean_content = (
            self._strip_tool_call_xml(content) if tool_calls else content
        )
        clean_content = self._strip_think(clean_content)

        return LLMResponse(
            content=clean_content.strip() or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=response.usage,
            raw_response=response,
        )

    def _parse_minimax_xml(self, content: str) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for tc_match in self._TOOL_CALL_RE.finditer(content):
            tc_body = tc_match.group(1)
            invoke = self._INVOKE_RE.search(tc_body)
            if not invoke:
                continue
            name = invoke.group(1)
            args: dict[str, str] = {}
            for pm in self._PARAM_RE.finditer(invoke.group(2)):
                args[pm.group(1)] = pm.group(2).strip()
            raw = json.dumps(args, ensure_ascii=False)
            calls.append(
                ToolCall(
                    id=f"minimax_{len(calls)}",
                    name=name,
                    arguments=args,
                    raw_arguments=raw,
                )
            )
        return calls

    def _strip_tool_call_xml(self, content: str) -> str:
        return self._TOOL_CALL_RE.sub("", content)

    @classmethod
    def _strip_think(cls, text: str | None) -> str | None:
        """移除 MiniMax 返回的 <think ...>...</think > 思考过程标签。"""
        if not text:
            return text
        return cls._THINK_RE.sub("", text).strip()
