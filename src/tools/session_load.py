"""session_load 工具：加载指定或最近的 session 对话历史到当前对话。

设计文档：docs/session-continuity-design.md §2.2
"""

from __future__ import annotations

from src.memory import session_store

# ── Schema ───────────────────────────────────────────────────────────────────

SESSION_LOAD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session_load",
        "description": "加载指定或最近的 session 对话历史到当前对话。恢复后你拥有完整上下文，可以自然延续之前的任务。",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "要加载的 session ID。不填则加载最近一个已结束的 session。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多加载最近 N 条消息，默认 50。",
                    "default": 50,
                },
            },
            "required": [],
        },
    },
}


# ── 当前 Session 引用（由 Session.startup 注入） ────────────────────────────

_current_session: object | None = None


def set_current_session(session: object) -> None:
    """设置当前 Session 引用（供 session_load 注入消息时使用）。"""
    global _current_session
    _current_session = session


def run(params: dict) -> str:
    """执行 session_load。"""
    session_id = params.get("session_id", "")
    limit = int(params.get("limit", 50))

    # 委托给 Session.load_session()
    if _current_session is not None and hasattr(_current_session, "load_session"):
        return _current_session.load_session(session_id=session_id, limit=limit)  # type: ignore[union-attr]

    # Fallback：没有 session 引用时，只查询不注入
    if not session_id:
        sessions = session_store.list_recent_sessions(limit=1)
        if not sessions:
            return "没有找到历史 session。"
        session_id = sessions[0]["session_id"]

    messages = session_store.get_session_messages(session_id, limit=limit)
    if not messages:
        return f"Session {session_id} 没有消息记录。"

    return f"找到 session {session_id}，共 {len(messages)} 条消息。但无法注入到当前对话（缺少 Session 引用）。"
