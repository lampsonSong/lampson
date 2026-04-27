"""
Session Search: FTS5 搜索 + LIKE 降级 + 召回 API。

设计文档：docs/memory-design.md §4.6
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .session_store import SEARCH_DB, SESSIONS_DIR

# ── 搜索结果 ───────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    session_id: str
    ts: int          # ms timestamp
    role: str        # "user" / "assistant"
    snippet: str     # 匹配内容片段
    score: float | None  # BM25 分数（FTS5 模式有，LIKE 模式为 None）


# ── 搜索 API ──────────────────────────────────────────────────────────

def search_sessions(
    query: str,
    limit: int = 5,
    date_from: str | None = None,
    date_to: str | None = None,
    role: str | None = None,
    session_id: str | None = None,
) -> list[SearchResult]:
    """
    搜索历史消息。

    策略：query 含中文 → LIKE；纯英文 → FTS5 MATCH + BM25
    """
    if not query.strip():
        return []

    has_chinese = bool(_HAS_CN_RE.search(query))

    if has_chinese:
        return _search_like(
            query=query,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            role=role,
            session_id=session_id,
        )
    else:
        return _search_fts(
            query=query,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            role=role,
            session_id=session_id,
        )


def _search_fts(
    query: str,
    limit: int,
    date_from: str | None,
    date_to: str | None,
    role: str | None,
    session_id: str | None,
) -> list[SearchResult]:
    """FTS5 MATCH + BM25 排序（英文友好）。"""
    conn = _get_db()
    try:
        sql = """
            SELECT m.session_id, m.ts, m.role, m.content, bm25(messages_fts) as score
            FROM messages_fts f
            JOIN messages_index m ON f.rowid = m.id
            WHERE messages_fts MATCH ?
        """
        params: list = [query]

        _add_filters(sql, params, date_from, date_to, role, session_id)
        sql += " ORDER BY bm25(messages_fts) LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        return [_row_to_result(row, score=True) for row in cur.fetchall()]
    finally:
        conn.close()


def _search_like(
    query: str,
    limit: int,
    date_from: str | None,
    date_to: str | None,
    role: str | None,
    session_id: str | None,
) -> list[SearchResult]:
    """LIKE 搜索（中文支持）。"""
    conn = _get_db()
    try:
        sql = """
            SELECT session_id, ts, role, content
            FROM messages_index
            WHERE content LIKE ?
        """
        params: list = [f"%{query}%"]

        _add_filters(sql, params, date_from, date_to, role, session_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        return [_row_to_result(row, score=False) for row in cur.fetchall()]
    finally:
        conn.close()


def _add_filters(
    sql: str,
    params: list,
    date_from: str | None,
    date_to: str | None,
    role: str | None,
    session_id: str | None,
) -> None:
    """往 SQL WHERE 追加过滤条件。"""
    if date_from:
        sql += " AND m.ts >= ?"
        params.append(_date_to_ms(date_from, start=True))
    if date_to:
        sql += " AND m.ts <= ?"
        params.append(_date_to_ms(date_to, start=False))
    if role:
        sql += " AND m.role = ?"
        params.append(role)
    if session_id:
        sql += " AND m.session_id = ?"
        params.append(session_id)


def _row_to_result(row: sqlite3.Row, score: bool) -> SearchResult:
    content = row["content"] or ""
    snippet = _make_snippet(content, 50)
    return SearchResult(
        session_id=row["session_id"],
        ts=row["ts"],
        role=row["role"] or "",
        snippet=snippet,
        score=row["score"] if score else None,
    )


def _make_snippet(content: str, context_chars: int = 50) -> str:
    """截取内容片段。"""
    if len(content) <= context_chars * 2 + 10:
        return content
    return content[:context_chars] + "..." + content[-context_chars:]


def _date_to_ms(date_str: str, start: bool) -> int:
    """YYYY-MM-DD → ms timestamp。"""
    from datetime import datetime, timezone
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt = dt.replace(tzinfo=timezone.utc)
    if start:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return int(dt.timestamp() * 1000)


# ── 召回 API ─────────────────────────────────────────────────────────

def get_session_messages(
    session_id: str,
    from_segment: int | None = None,
    before_ts: int | None = None,
) -> list[dict]:
    """
    从 JSONL 读取 session 的消息。
    与 session_store.get_session_messages 相同，暴露给工具层。
    """
    from .session_store import get_session_messages as _gsm
    return _gsm(session_id, from_segment=from_segment, before_ts=before_ts)


# ── 内部工具 ─────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(SEARCH_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


_HAS_CN_RE = re.compile(r"[\u4e00-\u9fff]")
