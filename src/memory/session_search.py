"""
Session Search: 三层混合搜索（BM25 → Embedding → 混合打分）。

设计文档：docs/memory-design.md §4.6, §5.1, §5.2

Layer 1: FTS5 BM25（jieba 预分词，中英文统一）
Layer 2: Embedding 余弦相似度（远程 API，有缓存跳过）
Layer 3: 混合打分 final_score = 0.7 * bm25_norm + 0.3 * cosine

降级：无 embedding 配置时纯 BM25。
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_store import SEARCH_DB, SESSIONS_DIR, _jieba_cut

logger = logging.getLogger(__name__)

# ── 搜索结果 ───────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    session_id: str
    ts: int                    # ms timestamp
    role: str                  # "user" / "assistant"
    snippet: str               # 匹配内容片段（从 raw_json 提取原始内容）
    bm25_score: float | None   # Layer 1 BM25 分数
    cosine_score: float | None # Layer 2 余弦相似度
    final_score: float | None  # Layer 3 混合分数


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
    三层混合搜索历史消息。

    Layer 1: jieba(query) → FTS5 MATCH → top-20 候选
    Layer 2: 对候选调 embedding API（有缓存跳过）→ 余弦打分
    Layer 3: 0.7 * bm25_norm + 0.3 * cosine → 混合排序
    """
    if not query.strip():
        return []

    # Layer 1: BM25
    candidates = _search_bm25(
        query=query,
        top_n=20,
        date_from=date_from,
        date_to=date_to,
        role=role,
        session_id=session_id,
    )
    if not candidates:
        return []

    # 检查 embedding 是否可用
    embed_config = _get_embedding_config()
    if not embed_config or not embed_config.get("api_key") or not embed_config.get("base_url"):
        # 降级：纯 BM25
        for c in candidates:
            c.final_score = c.bm25_score
        return candidates[:limit]

    # Layer 2: Embedding 重排
    _apply_embedding_rerank(candidates, query, embed_config)

    # Layer 3: 混合打分
    _apply_hybrid_score(candidates)

    # 排序并返回 top-limit
    candidates.sort(key=lambda r: r.final_score or 0, reverse=True)
    return candidates[:limit]


# ── Layer 1: BM25 ─────────────────────────────────────────────────────

def _search_bm25(
    query: str,
    top_n: int = 20,
    date_from: str | None = None,
    date_to: str | None = None,
    role: str | None = None,
    session_id: str | None = None,
) -> list[SearchResult]:
    """FTS5 BM25（jieba 预分词，中英文统一）。"""
    # query 也用 jieba 分词，以匹配 jieba 分词后的 content
    segmented_query = _jieba_cut(query)
    # FTS5 MATCH 需要 OR 连接各词
    fts_query = " OR ".join(segmented_query.split())
    if not fts_query.strip():
        return []

    conn = _get_db()
    try:
        sql = """
            SELECT m.id, m.session_id, m.ts, m.role, m.content, m.raw_json,
                   bm25(messages_fts) as score
            FROM messages_fts f
            JOIN messages_index m ON f.rowid = m.id
            WHERE messages_fts MATCH ? AND m.role IS NOT NULL
        """
        params: list[Any] = [fts_query]

        filter_sql, filter_params = _build_filter_clauses(
            "m", date_from, date_to, role, session_id
        )
        sql += filter_sql
        params.extend(filter_params)
        sql += " ORDER BY bm25(messages_fts) LIMIT ?"
        params.append(top_n)

        cur = conn.execute(sql, params)
        results = []
        for row in cur.fetchall():
            snippet = _extract_snippet(row["raw_json"], row["content"])
            results.append(SearchResult(
                session_id=row["session_id"],
                ts=row["ts"],
                role=row["role"] or "",
                snippet=snippet,
                bm25_score=row["score"],
                cosine_score=None,
                final_score=None,
            ))
        return results
    finally:
        conn.close()


# ── Layer 2: Embedding 重排 ────────────────────────────────────────────

def _apply_embedding_rerank(
    candidates: list[SearchResult],
    query: str,
    embed_config: dict[str, str],
) -> None:
    """对候选集调 embedding API（有缓存跳过），计算余弦相似度。"""
    from src.core.indexer import _EmbeddingClient, _cosine_sim

    client = _EmbeddingClient(
        provider=embed_config["provider"],
        model=embed_config["model"],
        api_key=embed_config["api_key"],
        base_url=embed_config["base_url"],
    )

    # query embedding
    q_vec = client.embed(query)
    if not q_vec:
        logger.warning("Query embedding failed, skipping Layer 2")
        return

    # 收集候选内容，查缓存或实时计算
    conn = _get_db()
    try:
        for c in candidates:
            # 从 messages_embedding 查缓存
            row = conn.execute(
                "SELECT embedding FROM messages_embedding WHERE msg_id = ?",
                (f"{c.session_id}:{c.ts}",),
            ).fetchone()
            if row and row["embedding"]:
                vec = _blob_to_vec(row["embedding"])
            else:
                # 实时计算
                vec = client.embed(c.snippet)
            c.cosine_score = _cosine_sim(q_vec, vec) if vec else 0.0
    finally:
        conn.close()


def _blob_to_vec(blob: bytes) -> list[float]:
    """将 BLOB 反序列化为 float 向量。"""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ── Layer 3: 混合打分 ─────────────────────────────────────────────────

def _apply_hybrid_score(candidates: list[SearchResult]) -> None:
    """final_score = 0.7 * bm25_normalized + 0.3 * cosine。"""
    if not candidates:
        return

    # BM25 分数归一化（BM25 分数是负数，越小越好 → 取反后归一化）
    bm25_scores = [abs(c.bm25_score) if c.bm25_score else 0.0 for c in candidates]
    max_bm25 = max(bm25_scores) if bm25_scores else 1.0
    if max_bm25 == 0:
        max_bm25 = 1.0

    for c in candidates:
        bm25_norm = (abs(c.bm25_score) / max_bm25) if c.bm25_score else 0.0
        cosine = c.cosine_score if c.cosine_score is not None else 0.0
        c.final_score = 0.7 * bm25_norm + 0.3 * cosine


# ── 工具函数 ───────────────────────────────────────────────────────────

def _extract_snippet(raw_json: str | None, segmented_content: str | None) -> str:
    """从 raw_json 提取原始 content 作为 snippet，fallback 到 segmented_content。"""
    if raw_json:
        try:
            row = json.loads(raw_json)
            content = row.get("content", "")
            if content:
                return _make_snippet(content, 80)
        except (json.JSONDecodeError, AttributeError):
            pass
    if segmented_content:
        return _make_snippet(segmented_content, 80)
    return ""


def _build_filter_clauses(
    table_alias: str,
    date_from: str | None,
    date_to: str | None,
    role: str | None,
    session_id: str | None,
) -> tuple[str, list]:
    """构建 WHERE 过滤子句，返回 (sql_fragment, params)。"""
    clauses = []
    params: list[Any] = []
    ta = table_alias
    if date_from:
        clauses.append(f"{ta}.ts >= ?")
        params.append(_date_to_ms(date_from, start=True))
    if date_to:
        clauses.append(f"{ta}.ts <= ?")
        params.append(_date_to_ms(date_to, start=False))
    if role:
        clauses.append(f"{ta}.role = ?")
        params.append(role)
    if session_id:
        clauses.append(f"{ta}.session_id = ?")
        params.append(session_id)
    sql = ""
    for cl in clauses:
        sql += f" AND {cl}"
    return sql, params


def _make_snippet(content: str, context_chars: int = 80) -> str:
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


def _get_embedding_config() -> dict[str, str] | None:
    """读取 embedding 配置，无配置返回 None。"""
    try:
        from src.core.config import load_config, get_embedding_config
        config = load_config()
        ec = get_embedding_config(config)
        if ec.get("api_key") and ec.get("base_url"):
            return ec
    except Exception:
        pass
    return None

def get_session_messages(
    session_id: str,
    from_segment: int | None = None,
    before_ts: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    从 JSONL 读取 session 的消息。
    与 session_store.get_session_messages 相同，暴露给工具层。
    """
    from .session_store import get_session_messages as _gsm
    return _gsm(session_id, from_segment=from_segment, before_ts=before_ts, limit=limit)


# ── 内部工具 ─────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(SEARCH_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn
