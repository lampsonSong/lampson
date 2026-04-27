"""
Memory 模块：管理 Lampson 的记忆系统。

JSONL + SQLite FTS5 架构：
- session_store: JSONL 写入 + SQLite 索引同步
- session_search: FTS5 搜索 + LIKE 降级 + 召回 API
- manager: core.md 核心记忆读写（load_core, add_memory, search_memory 等）
"""

from __future__ import annotations

from . import manager as manager
from .session_store import (
    create_session,
    end_session,
    get_session,
    append_message,
    append,
    write_segment_boundary,
    get_session_messages,
    get_latest_segment_boundary,
    load_resume_context,
    rebuild_index,
    rebuild_jsonl,
    SEARCH_DB,
    SESSIONS_DIR,
)
from .session_search import (
    search_sessions,
    get_session_messages,
    SearchResult,
)

__all__ = [
    # manager 模块（保留兼容）
    "manager",
    # 新接口
    "create_session",
    "end_session",
    "get_session",
    "append_message",
    "append",
    "write_segment_boundary",
    "get_session_messages",
    "get_latest_segment_boundary",
    "load_resume_context",
    "rebuild_index",
    "rebuild_jsonl",
    "search_sessions",
    "SearchResult",
    "SEARCH_DB",
    "SESSIONS_DIR",
]
