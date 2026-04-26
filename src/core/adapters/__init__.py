"""按模型名选择 Model Adapter。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.adapters.base import BaseModelAdapter, LLMResponse, ToolCall
from src.core.adapters.minimax import MiniMaxAdapter
from src.core.adapters.openai_compat import OpenAICompatAdapter

if TYPE_CHECKING:
    from src.core.llm import LLMClient

_MODEL_PATTERNS: dict[str, type[BaseModelAdapter]] = {}


def register_adapter(pattern: str, adapter_cls: type[BaseModelAdapter]) -> None:
    """注册子串 pattern（小写）到适配器类。"""
    _MODEL_PATTERNS[pattern.lower()] = adapter_cls


def create_adapter(llm_client: LLMClient) -> BaseModelAdapter:
    """根据 llm_client.model 名称选择适配器。"""
    model_lower = llm_client.model.lower()
    for pattern, cls in _MODEL_PATTERNS.items():
        if pattern in model_lower:
            return cls(llm_client)
    return OpenAICompatAdapter(llm_client)


register_adapter("minimax", MiniMaxAdapter)

__all__ = [
    "BaseModelAdapter",
    "LLMResponse",
    "MiniMaxAdapter",
    "OpenAICompatAdapter",
    "ToolCall",
    "create_adapter",
    "register_adapter",
]
