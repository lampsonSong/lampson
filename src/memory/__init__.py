"""
Memory 模块：管理 Lampson 的两层记忆系统。

新架构（JSONL + SQLite FTS5）：
- session_store: JSONL 写入 + SQLite 索引同步
- session_search: FTS5 搜索 + LIKE 降级 + 召回 API

旧架构（摘要写 .md 文件）：
- manager: load_core, add_memory, search_memory, save_session_summary 等
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
    SEARCH_DB,
    SESSIONS_DIR,
)
from .session_search import (
    search_sessions,
    get_session_messages,
    SearchResult,
)

__all__ = [
    # 旧接口（保留兼容）
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
    "search_sessions",
    "SearchResult",
    "SEARCH_DB",
    "SESSIONS_DIR",
]
