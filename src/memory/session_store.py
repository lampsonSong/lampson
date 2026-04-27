"""
Session Store: JSONL 写入 + SQLite 索引同步。

JSONL 是 source of truth（~/.lampson/memory/sessions/YYYY-MM-DD/{session_id}.jsonl）；
SQLite search.db 是加速层（FTS5 + sessions/segments 表）。

设计文档：docs/memory-design.md
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

# ── 路径配置 ────────────────────────────────────────────────────────────

LAMPSON_DIR = Path.home() / ".lampson"
SESSIONS_DIR = LAMPSON_DIR / "memory" / "sessions"
SEARCH_DB = LAMPSON_DIR / "memory" / "search.db"

# ── Schema ──────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,   -- 毫秒时间戳
    ended_at INTEGER,              -- 毫秒时间戳，session_end 时写入
    source TEXT NOT NULL DEFAULT 'cli'
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    segment INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    UNIQUE(session_id, segment)
);

CREATE TABLE IF NOT EXISTS messages_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    role TEXT,
    content TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert
AFTER INSERT ON messages_index BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete
AFTER DELETE ON messages_index BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages_index(session_id);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id);
"""

# ── 初始化 ──────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    LAMPSON_DIR.mkdir(parents=True, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    """获取 SQLite 连接（自动建表）。"""
    _ensure_dirs()
    db_path = SEARCH_DB
    conn = sqlite3.connect(db_path, timeout=10)
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


# ── Session 管理 ────────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    session_id: str
    started_at: int          # ms timestamp
    ended_at: int | None
    source: str = "cli"


def create_session(source: str = "cli") -> SessionInfo:
    """创建新 session，写入 sessions 表和 JSONL。"""
    _ensure_dirs()
    sid = _gen_session_id()
    now_ms = _now_ms()

    info = SessionInfo(session_id=sid, started_at=now_ms, ended_at=None, source=source)

    # 写 JSONL
    _jsonl_append(sid, {
        "ts": now_ms,
        "type": "session_start",
        "session_id": sid,
        "source": source,
    })

    # 写 SQLite
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO sessions(session_id, started_at, source) VALUES(?, ?, ?)",
            (sid, now_ms, source),
        )
        conn.commit()
    finally:
        conn.close()

    return info


def _gen_session_id() -> str:
    """生成简短可读的 session ID（前8位）。"""
    return uuid.uuid4().hex[:8]


def end_session(session_id: str) -> None:
    """标记 session 结束，写入 session_end 行。"""
    now_ms = _now_ms()

    # 写 JSONL
    _jsonl_append(session_id, {
        "ts": now_ms,
        "session_id": session_id,
        "type": "session_end",
    })

    # 写 SQLite
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
            (now_ms, session_id),
        )
        # 同时更新最后一个 segment 的 ended_at
        row = conn.execute(
            "SELECT id FROM segments WHERE session_id = ? ORDER BY segment DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE segments SET ended_at = ? WHERE id = ?",
                (now_ms, row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: str) -> SessionInfo | None:
    """从 sessions 表读取 session 元数据。"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT session_id, started_at, ended_at, source FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return SessionInfo(
            session_id=row["session_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            source=row["source"],
        )
    finally:
        conn.close()


# ── JSONL 写入 ──────────────────────────────────────────────────────────

def append_message(
    session_id: str,
    role: Literal["user", "assistant"],
    content: str,
    tool_calls: list[dict] | None = None,
    tool_result: str | None = None,
    referenced_tool_results: list[str] | None = None,
    segment: int = 0,
) -> None:
    """追加一条消息到 JSONL 文件，同时更新 SQLite 索引。"""
    now_ms = _now_ms()

    row = {
        "ts": now_ms,
        "session_id": session_id,
        "segment": segment,
        "role": role,
        "content": content,
    }
    if tool_calls:
        row["tool_calls"] = tool_calls
    if tool_result:
        row["tool_result"] = tool_result
    if referenced_tool_results:
        row["referenced_tool_results"] = referenced_tool_results

    _jsonl_append(session_id, row)

    # 更新 SQLite 索引
    conn = _get_db()
    try:
        # 查当前 session 的 latest segment
        latest_seg = conn.execute(
            "SELECT MAX(segment) FROM segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        if latest_seg is None:
            # 第一次有消息，建立 segment 0
            conn.execute(
                "INSERT INTO segments(session_id, segment, started_at) VALUES(?, ?, ?)",
                (session_id, 0, now_ms),
            )

        # 写入消息索引
        conn.execute(
            "INSERT INTO messages_index(session_id, ts, role, content) VALUES(?, ?, ?, ?)",
            (session_id, now_ms, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def write_segment_boundary(
    session_id: str,
    segment: int,
    next_segment_started_at: int,
    archive: list[dict] | None = None,
) -> None:
    """
    写入 segment_boundary 行到 JSONL，并更新 segments 表的 ended_at。
    archive: [{"target": "skill:xxx", "entry_count": 3}, ...]
    """
    now_ms = _now_ms()

    row = {
        "ts": now_ms,
        "session_id": session_id,
        "segment": segment,
        "type": "segment_boundary",
        "next_segment_started_at": next_segment_started_at,
    }
    if archive:
        row["archive"] = archive

    _jsonl_append(session_id, row)

    # 更新 segments 表的 ended_at
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE segments SET ended_at = ? WHERE session_id = ? AND segment = ?",
            (now_ms, session_id, segment),
        )
        # 建立下一个 segment 的 started_at
        conn.execute(
            "INSERT OR IGNORE INTO segments(session_id, segment, started_at) VALUES(?, ?, ?)",
            (session_id, segment + 1, next_segment_started_at),
        )
        conn.commit()
    finally:
        conn.close()


# ── JSONL 读取 ─────────────────────────────────────────────────────────

def get_session_messages(
    session_id: str,
    from_segment: int | None = None,
    before_ts: int | None = None,
) -> list[dict]:
    """
    从 JSONL 读取 session 的消息。
    可指定 from_segment（返回指定 segment 及之后的消息）和 before_ts。
    """
    path = _find_jsonl(session_id)
    if not path:
        return []

    messages = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("type") in ("session_start", "session_end"):
                continue
            if from_segment is not None and msg.get("segment", 0) < from_segment:
                continue
            if before_ts is not None and msg.get("ts", 0) >= before_ts:
                continue
            messages.append(msg)
    return messages


def get_latest_segment_boundary(session_id: str) -> dict | None:
    """从 JSONL 读取最后一个 segment_boundary 行。"""
    path = _find_jsonl(session_id)
    if not path:
        return None

    boundary = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("type") == "segment_boundary":
                boundary = msg
    return boundary


# ── 内部工具 ───────────────────────────────────────────────────────────

def _jsonl_path(session_id: str) -> Path:
    """根据 session_id 找 JSONL 路径（需要扫描日期目录）。"""
    today = date.today()
    for days_ago in range(30):  # 最多回溯30天
        d = today - __import__("datetime").timedelta(days=days_ago)
        root = SESSIONS_DIR / d.isoformat()
        if root.exists():
            matches = list(root.glob(f"*{session_id}*.jsonl"))
            if matches:
                return matches[0]
    # session_id 不在已知目录，可能是今天的 session 还没创建
    # 写新文件到今天目录
    today_path = SESSIONS_DIR / today.isoformat()
    today_path.mkdir(parents=True, exist_ok=True)
    return today_path / f"{session_id}.jsonl"


def _find_jsonl(session_id: str) -> Path | None:
    """根据 session_id 找 JSONL 路径。"""
    for path in SESSIONS_DIR.rglob(f"*{session_id}*.jsonl"):
        return path
    return None


def _jsonl_append(session_id: str, row: dict) -> None:
    """追加一行到 JSONL 文件。"""
    path = _jsonl_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


# ── 通用追加 & Resume ──────────────────────────────────────────────────


def append(session_id: str, row: dict) -> None:
    """通用追加：写一行 JSON 到 JSONL 文件（不更新 SQLite 索引）。

    供 compaction 写 segment_boundary 等特殊行使用。
    需要同步 SQLite 的场景请用 append_message / write_segment_boundary。
    """
    _jsonl_append(session_id, row)


def load_resume_context(session_id: str) -> str | None:
    """读取最后一个 segment_boundary 的 archive 字段，加载 skill/project 内容。

    返回格式化的上下文字符串，或 None（没有可恢复的上下文）。
    """
    boundary = get_latest_segment_boundary(session_id)
    if not boundary:
        return None

    archive = boundary.get("archive")
    if not archive:
        return None

    parts: list[str] = []
    for item in archive:
        target = item.get("target", "")
        if not target:
            continue
        path = _resolve_archive_target(target)
        if path and path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## 归档内容: {target}\n\n{content}")
            except OSError:
                pass

    if not parts:
        return None

    segment = boundary.get("segment", "?")
    header = f"# Context Resume（从 segment {segment} 恢复）\n\n以下内容来自 compaction 归档，供参考：\n"
    return header + "\n\n---\n\n".join(parts)


def _resolve_archive_target(target: str) -> Path | None:
    """将 archive target 解析为文件路径。"""
    if target.startswith("skill:"):
        return LAMPSON_DIR / "skills" / f"{target[6:]}.md"
    elif target.startswith("project:"):
        return LAMPSON_DIR / "projects" / f"{target[8:]}.md"
    return None


# ── rebuild_index ──────────────────────────────────────────────────────

def rebuild_index(sessions_dir: Path | None = None, db_path: Path | None = None) -> None:
    """
    从 JSONL 重建 SQLite 索引。

    步骤：
    1. 加写锁
    2. 清空 sessions/segments/messages_index 表
    3. 流式解析 JSONL（逐行读取）
    4. 批量 INSERT（每 1000 条 commit 一次）
    5. 释放写锁
    """
    sessions_dir = sessions_dir or SESSIONS_DIR
    db_path = db_path or SEARCH_DB

    lock_path = db_path.with_suffix(".lock")
    if lock_path.exists():
        raise RuntimeError("索引正在重建中，请稍后再试")

    try:
        lock_path.write_text("")
        _rebuild_unsafe(sessions_dir, db_path)
    finally:
        lock_path.unlink(missing_ok=True)


def _rebuild_unsafe(sessions_dir: Path, db_path: Path) -> None:
    """无锁重建，供内部调用。"""
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)  # 确保表存在
    conn.row_factory = sqlite3.Row

    # 清空表（FTS 用 DELETE 而非 TRUNCATE）
    conn.execute("DELETE FROM messages_fts")
    conn.execute("DELETE FROM messages_index")
    conn.execute("DELETE FROM segments")
    conn.execute("DELETE FROM sessions")
    conn.commit()

    BATCH_SIZE = 1000
    batch: list[dict] = []

    for jsonl_path in _iter_jsonl_files(sessions_dir):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                batch.append(msg)
                if len(batch) >= BATCH_SIZE:
                    _flush_batch(conn, batch)
                    batch = []

    if batch:
        _flush_batch(conn, batch)

    conn.close()


def _iter_jsonl_files(sessions_dir: Path):
    """按时间顺序遍历所有 JSONL 文件。"""
    if not sessions_dir.exists():
        return
    for date_dir in sorted(sessions_dir.iterdir()):
        if date_dir.is_dir():
            for jsonl_file in sorted(date_dir.glob("*.jsonl")):
                yield jsonl_file


def _flush_batch(conn: sqlite3.Connection, batch: list[dict]) -> None:
    """批量写入，事务提交。"""
    pending_segment: dict | None = None  # (session_id, segment) -> started_at

    for msg in batch:
        t = msg.get("type")
        sid = msg.get("session_id", "")
        seg = msg.get("segment", 0)

        if t == "session_start":
            conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, started_at, source) VALUES(?, ?, ?)",
                (sid, msg["ts"], msg.get("source", "cli")),
            )
            # 建立 segment 0
            pending_segment = {"session_id": sid, "segment": 0, "started_at": msg["ts"]}

        elif t == "session_end":
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (msg["ts"], sid),
            )
            # 更新最后一个 segment
            row = conn.execute(
                "SELECT id FROM segments WHERE session_id = ? ORDER BY segment DESC LIMIT 1",
                (sid,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE segments SET ended_at = ? WHERE id = ?",
                    (msg["ts"], row["id"]),
                )

        elif t == "segment_boundary":
            # 上一 segment 结束
            conn.execute(
                "UPDATE segments SET ended_at = ? WHERE session_id = ? AND segment = ?",
                (msg["ts"], sid, seg),
            )
            # 建立下一 segment
            next_seg = seg + 1
            next_started = msg.get("next_segment_started_at", msg["ts"])
            conn.execute(
                "INSERT OR IGNORE INTO segments(session_id, segment, started_at) VALUES(?, ?, ?)",
                (sid, next_seg, next_started),
            )

        elif msg.get("role") in ("user", "assistant"):
            # 确保 segment 存在
            if pending_segment is None or pending_segment["session_id"] != sid:
                row = conn.execute(
                    "SELECT segment FROM segments WHERE session_id = ? ORDER BY segment DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                curr_seg = row["segment"] if row else 0
                if seg <= curr_seg:
                    pending_segment = {"session_id": sid, "segment": seg, "started_at": msg["ts"]}
            if pending_segment:
                try:
                    conn.execute(
                        "INSERT INTO segments(session_id, segment, started_at) VALUES(?, ?, ?)",
                        (pending_segment["session_id"], pending_segment["segment"], pending_segment["started_at"]),
                    )
                except sqlite3.IntegrityError:
                    pass  # 已存在
                pending_segment = None

            conn.execute(
                "INSERT INTO messages_index(session_id, ts, role, content) VALUES(?, ?, ?, ?)",
                (sid, msg["ts"], msg["role"], msg.get("content", "")),
            )

    conn.commit()
