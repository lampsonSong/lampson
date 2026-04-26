"""Planner — JSON 解析工具（classify/plan 阶段已移除，由 LLM 工具调用循环取代）。"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


class PlanParseError(Exception):
    """LLM 返回的 plan JSON 无法解析。"""


def extract_json(text: str) -> dict | None:
    """从文本中提取 JSON（处理 markdown 代码块、思维链等包裹）。"""
    # 去掉 <think...</think > 思维链（部分模型如 MiniMax 会输出）
    cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL)

    # 尝试提取 ```json ... ``` 包裹的内容
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()

    # 尝试直接找 { ... }
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 最后尝试整体解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
