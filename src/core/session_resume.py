"""Session Resume：生成进度总结 + 跨 session 上下文注入。

设计：
- Session 结束（idle 超时）时，调用 LLM 生成简短 progress summary
- 新 Session 创建时，把上一条 summary 注入 system prompt

文档：docs/session-resume.md
"""

from __future__ import annotations

import time
from typing import Any


# ── Prompt 模板 ──────────────────────────────────────────────────────────

_SUMMARY_SYSTEM = """你是一个项目进度总结助手。你的任务是根据对话历史，生成一段简短的项目进展描述。

要求：
1. 简洁明了，控制在 200 字以内
2. 包含三点：当前任务/问题是什么、已完成了哪些步骤、下一步要做什么
3. 直接写正文，不要前缀（如"总结："）、不要标题、不要换行符
4. 如果对话历史为空或无实质内容，返回空字符串
"""

_SUMMARY_USER_TEMPLATE = """请根据以下对话历史，生成一段项目进展描述：

{dialogue}

进展描述（200字以内，包含：任务目标、已完成、下一步）："""


# ── 核心函数 ─────────────────────────────────────────────────────────────

def generate_session_summary(messages: list[dict[str, Any]], llm_client: Any) -> str:
    """调用 LLM 生成 session 进度总结。

    Args:
        messages: 当前 session 的对话历史（JSONL 中读出的消息列表）
        llm_client: LLMClient 实例，用于发起 API 调用

    Returns:
        进度总结字符串（失败时返回空字符串）
    """
    # 过滤出 user 和 assistant 消息，组合成摘要文本
    turns: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content or role not in ("user", "assistant"):
            continue
        # tool_calls 也算内容
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                turns.append(f"[assistant] 调用 {fn.get('name', '?')}({_truncate(str(fn.get('arguments', '')), 100)})")
        else:
            turns.append(f"[{role}] {_truncate(content, 300)}")

    if not turns:
        return ""

    dialogue_text = "\n".join(turns[-20:])  # 最多取最近20轮

    user_prompt = _SUMMARY_USER_TEMPLATE.format(dialogue=dialogue_text)

    # 独立发起一次 API 调用，不污染 llm_client.messages
    try:
        response = llm_client.client.chat.completions.create(
            model=llm_client.model,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        summary = response.choices[0].message.content or ""
        return summary.strip()
    except Exception as e:
        print(f"[session_resume] 生成 summary 失败: {e}", flush=True)
        return ""


def _truncate(text: str, max_len: int) -> str:
    """截断字符串到 max_len 字符。"""
    text = text.replace("\n", " ").replace("\r", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ── 注入相关 ─────────────────────────────────────────────────────────────

def build_resume_injection(summary: str) -> str:
    """把上一条 summary 构建成交叉 session 注入文本。"""
    if not summary:
        return ""
    return (
        f"\n\n## 上一轮会话进展\n\n"
        f"上一轮会话因超过 3 小时无活动而结束，以下是当时的进展：\n\n"
        f"{summary}\n\n"
        f"请继续推进上述任务。如果任务已完成或有新需求，请告知用户。\n"
    )
