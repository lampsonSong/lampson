# Lampson 记忆管理系统设计

## 1. 背景与目标

### 现状问题

1. **原始对话不保留**：每次退出只写 LLM 总结，原始消息全部丢弃
2. **无法跨会话追溯**：只能靠关键词匹配 sessions/ 的摘要，找不到细节
3. **压缩破坏历史**：context compaction 触发时消息被截断，历史内容丢失

### 改造目标

1. **存原始消息**：每条消息（user / assistant / tool_call）都持久化，不依赖 LLM 总结
2. **可搜索**：跨 session 按内容搜索，不只是关键词匹配摘要
3. **压缩友好**：压缩不丢历史，segment 边界可追溯

---

## 2. 存储架构

### 2.1 分层存储

```
~/.lampson/
├── sessions/                    ← 原始消息（JSONL，source of truth）
│   ├── 2026-04-26/
│   │   └── {session_id}.jsonl  ← 一个 session 一个文件
│   └── 2026-04-27/
│       └── {session_id}.jsonl
└── search.db                   ← SQLite（FTS5 索引 + 元数据）
    ├── sessions 表              ← session 索引
    ├── messages_index 表        ← 冗余存 content（FTS5 JOIN 用）
    └── messages_fts 表          ← FTS5 全文索引（英文友好）
```

### 2.2 职责分工

| 存储层 | 格式 | 用途 | 可丢失？ |
|--------|------|------|----------|
| JSONL | 文本 | 原始消息存档，人可读，灾后可重建 | **不可丢**（source of truth） |
| SQLite | 数据库 | FTS5 搜索、元数据查询 | **可丢**（重建即可） |

**原则**：JSONL 是 source of truth，SQLite 是加速层。

### 2.3 跨天 session 处理

session 以**开始日期**为目录，生命周期内始终在同一文件，不因跨天拆分。

```
sessions/
├── 2026-04-26/           ← session 于 4/26 开始
│   └── abc123.jsonl       ← 跨 26、27、28 号，不移动
└── 2026-04-28/           ← session 于 4/28 开始（独立 session）
    └── xyz789.jsonl
```

### 2.4 JSONL 行类型汇总

JSONL 中共四种行类型：

| type | 出现时机 | 说明 |
|------|----------|------|
| `session_start` | session 创建时 | 写入 sessions 表（started_at, source） |
| 普通消息行 | 每条消息 | user/assistant，含 content、tool_calls、tool_result_summary、referenced_tool_results |
| `segment_boundary` | compaction 触发时 | 标记 segment 结束，含 next_segment_started_at |
| `session_end` | session 退出时 | 标记 session 结束，**可选包含 summary（idle 超时重置时由 LLM 生成）** |

---

## 3. JSONL 文件格式

### 3.1 文件命名

```
{year}-{month}-{day}_{session_id}.jsonl
```

示例：`2026-04-26_abc123def456.jsonl`

### 3.2 消息格式

每行一条 JSON，记录一条消息或一个特殊标记：

```jsonl
{"ts": 1745800000000, "session_id": "abc123", "segment": 0, "role": "user", "content": "帮我查一下 SPL 的需求文档"}
{"ts": 1745800001000, "session_id": "abc123", "segment": 0, "role": "assistant", "content": "好的，让我先搜一下历史记录...", "tool_calls": [{"id": "call_001", "name": "session_search", "arguments": "..."}]}
{"ts": 1745800005000, "session_id": "abc123", "segment": 0, "type": "segment_boundary", "next_segment_started_at": 1745801000000, "archive": [{"target": "skill:python-patterns", "entry_count": 3}]}
{"ts": 1745801000000, "session_id": "abc123", "segment": 1, "role": "user", "content": "继续说"}
{"ts": 1745809999000, "session_id": "abc123", "segment": 1, "type": "session_end"}
```

#### 字段定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ts` | int | 是 | 毫秒时间戳 |
| `session_id` | str | 是 | session 唯一 ID |
| `segment` | int | 是 | 压缩段号，从 0 开始 |
| `role` | str | 否 | user / assistant |
| `content` | str | 否 | 消息内容 |
| `tool_calls` | list | 否 | 工具调用列表（assistant 角色时） |
| `tool_result` | str | 否 | tool 返回结果摘要（前 500 字，见 3.3） |
| `referenced_tool_results` | list[str] | 否 | assistant 消息引用了哪些 tool_call id（如 `["call_001", "call_002"]`），compaction 据此判断 tool_result 是否有上下文价值 |
| `type` | str | 否 | 特殊行类型：session_start / segment_boundary / session_end |

#### 特殊行类型

| type | 触发时机 | 说明 |
|------|----------|------|
| `session_start` | session 创建时 | 写入 sessions 表（started_at, source），格式见下方 |
| `segment_boundary` | compaction 触发时 | 标记 segment 结束，含 next_segment_started_at 和 archive（归档目标列表，供 resume 重建上下文） |
| `session_end` | session 退出 | 标记 session 正常结束，无内容体 |

**session_start 示例**：

```jsonl
{"ts": 1745800000000, "type": "session_start", "session_id": "abc123", "source": "cli"}
```

**segment_boundary 示例**：

```jsonl
{"ts": 1745800005000, "session_id": "abc123", "segment": 0, "type": "segment_boundary", "next_segment_started_at": 1745801000000, "archive": [{"target": "skill:python-patterns", "entry_count": 3}, {"target": "project:my-cli", "entry_count": 1}]}
```

> **archive 字段的作用**：compaction 结果在 JSONL 和 skill/project 两处持久化，但两者没有直接关联。程序崩溃后 resume 时，仅靠 segment_boundary 只能知道"压缩发生过"，但不知道归档到了哪里。archive 字段让 resume 逻辑可以准确注入相关 skill/project，无需再查 skill/project 文件内容。

**session_end 示例（无 summary）**：

```jsonl
{"ts": 1745809999000, "session_id": "abc123", "segment": 1, "type": "session_end"}
```

**session_end 示例（idle 超时重置，含 summary）**：

```jsonl
{"ts": 1745809999000, "session_id": "abc123", "segment": 1, "type": "session_end", "summary": "用户正在实现 SessionManager 的 idle 超时重置机制，已完成超时检测和重置流程，下一步需要集成前端页面。"}
```

### 3.3 存储范围

**存储**：
- user 消息：role、content
- assistant 消息：role、content、tool_calls（完整 arguments JSON 字符串）
- tool 返回结果：**前 500 字摘要**（见下方理由）
- segment_boundary / session_end 标记

**不存储**：
- tool 角色的完整返回（tool_result 摘要替代）
- system 消息（由 core.md 管理）

**关于 tool_result 摘要**：

"可复现"不等于"不需要"。以下场景返回结果会变化，存摘要才有价值：

- `session_search` 返回的摘要是历史某个时刻的快照，之后历史变了，摘要就丢了
- `browser_tool` 抓取的页面内容可能随时变化
- `code_execution` 的输出是不可复现的（时间、环境相关）

只存前 500 字：在保障上下文和节省空间之间取平衡，足够实用。

**关于 referenced_tool_results**：compaction 的 Classify 阶段需要判断哪些 tool_result 被 assistant 回复引用过（有上下文价值）。该字段在 assistant 消息写入时由 Agent 生成（分析回复内容是否提及 tool 返回中的关键信息），随 assistant 消息行一起写入 JSONL，持久化以供 compaction 重跑时使用。

### 3.4 写入时机

| 事件 | 写入内容 |
|------|----------|
| Session 创建 | `session_start` 行（写入 sessions 表） |
| 用户发送消息 | user 行（含 tool_result 摘要、referenced_tool_results） |
| Assistant 回复 | assistant 行（含 tool_calls、referenced_tool_results） |
| 工具调用返回 | **不单独写入**（referenced_tool_results 由 Agent 分析回复内容后生成，随 assistant 行一起存） |
| 运行时触发压缩 | segment_boundary 行（含 next_segment_started_at 和 archive），由 Agent 的 `maybe_compact()` 调用 |
| Session 退出 | `session_end` 行 + 检查 core.md 是否需要更新 |
| Session 退出时 | core.md 更新（累计 archive 次数 > 5 或距上次更新 > N 小时，由 LLM 抽取 skill/project 精华） |

> **注意**：tool 返回结果的 `referenced_by` 信息通过 assistant 消息的 `referenced_tool_results` 字段持久化（见 3.2 节）。该字段由 Agent 在写入 assistant 消息时生成，compaction 读取进行分类决策。

---

## 4. SQLite 索引设计

### 4.1 sessions 表

记录 session 级别的元数据：

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    started_at INTEGER,           -- 毫秒时间戳，session_start 行的 ts
    ended_at INTEGER,            -- 毫秒时间戳，session_end 行的 ts
    source TEXT NOT NULL,        -- 启动来源，不做白名单强制校验
    summary TEXT                 -- session 结束时的进度总结（idle 超时重置时由 LLM 生成）
);
```

> **已废弃字段**：message_count、segment_count 不存，用 `COUNT()` 实时算，保证一致性。
> **source 字段**：只做运行时 warn，不做强制校验。Lampson channel 会持续扩展，白名单会不断过时。已知合法值参照：`cli`, `feishu`, `api`, `telegram`, `discord`, `slack`（不代表完整列表）。

### 4.2 segments 表

记录每个 segment 的边界信息：

```sql
CREATE TABLE segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    segment INTEGER NOT NULL,     -- 段号，从 0 开始
    started_at INTEGER NOT NULL,  -- 该 segment 第一条消息的时间戳
    ended_at INTEGER,            -- 该 segment 结束时间（segment_boundary 的 ts）
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    UNIQUE(session_id, segment)
);
```

> **started_at**：segment=0 的 started_at 是 session 第一条消息的 ts，不需要从别处推导。
> **ended_at**：segment_boundary 行写入时，同时 UPDATE segments 表补上 ended_at。

### 4.3 messages_index 表 + messages_fts 表（FTS5）

**采用冗余存储方案**：SQLite 中同时存储 content 和 FTS5 索引，JSONL 是 source of truth，SQLite 是加速层。

> Lampson 是个人 CLI 工具，消息量远小于 Hermes（Hermes 运行数月约 22,000 条消息）。按每年约 50,000 条消息估算，SQLite 总大小约 110 MB，对现代磁盘可忽略。选择冗余方案的好处是 FTS JOIN SQL 过滤可以无缝结合，代码简单。

```sql
-- 消息索引表（冗余存储 content）
CREATE TABLE messages_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    role TEXT,
    content TEXT
);

-- FTS5 全文索引（冗余存储 content）
-- 注意：unicode61 对中文支持有限，见 4.6 节
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    tokenize='unicode61'
);

-- 触发器：INSERT / DELETE 时自动同步 FTS5
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages_index BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages_index BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
```

> **不设 UPDATE 触发器**：消息只可追加不可修改，UPDATE 视为 bug，不支持。

### 4.4 索引写入时机

| 事件 | 索引操作 |
|------|----------|
| session_start | INSERT sessions 表 |
| 新消息写入 JSONL | INSERT messages_index + 触发器同步 FTS5 |
| segment_boundary | INSERT segments 新行 |
| session_end | UPDATE sessions(ended_at) + UPDATE segments(ended_at) |

### 4.5 索引重建（rebuild_index）

```python
def rebuild_index(sessions_dir: Path, db_path: Path) -> None:
    """
    从 JSONL 重建 SQLite 索引。

    步骤：
    1. 加写锁（防止重建期间新写入）
    2. 清空 sessions / segments / messages_index 表
    3. 流式解析 JSONL（逐行读取，不全量 load）
    4. 批量 INSERT（每 1000 条 commit 一次，防止事务太大）
    5. 释放写锁
    """
    lock_path = db_path.with_suffix(".lock")
    if lock_path.exists():
        raise RuntimeError("索引正在重建中，请稍后再试")

    try:
        # Step 1: 创建锁文件
        lock_path.write_text("")
        _rebuildUnsafe(sessions_dir, db_path)
    finally:
        lock_path.unlink(missing_ok=True)


def _rebuildUnsafe(sessions_dir: Path, db_path: Path) -> None:
    """无锁版本，供内部调用。"""
    import sqlite3, json
    from pathlib import Path

    conn = sqlite3.connect(db_path)

    # 清空表
    conn.execute("DELETE FROM messages_fts")
    conn.execute("DELETE FROM messages_index")
    conn.execute("DELETE FROM segments")
    conn.execute("DELETE FROM sessions")
    conn.commit()

    BATCH_SIZE = 1000
    batch = []

    # 流式解析所有 JSONL
    for jsonl_path in _iterJsonlFiles(sessions_dir):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                batch.append(msg)

                if len(batch) >= BATCH_SIZE:
                    _flushBatch(conn, batch)
                    batch = []

    if batch:
        _flushBatch(conn, batch)

    conn.close()


def _flushBatch(conn: sqlite3.Connection, batch: list[dict]) -> None:
    """批量插入，事务提交。"""
    for msg in batch:
        t = msg.get("type")
        sid = msg.get("session_id", "")
        seg = msg.get("segment", 0)

        if t == "session_start":
            conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, started_at, source) VALUES(?, ?, ?)",
                (sid, msg["ts"], msg.get("source", "cli")),
            )
        elif t == "session_end":
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (msg["ts"], sid),
            )
        elif t == "segment_boundary":
            # segment N 的 started_at = segment N-1 的 ended_at（来自 next_segment_started_at）
            started_at = msg.get("next_segment_started_at") or _getSegmentStartedAt(conn, sid, seg)
            conn.execute(
                "INSERT OR IGNORE INTO segments(session_id, segment, started_at, ended_at) VALUES(?, ?, ?, ?)",
                (sid, seg, started_at, msg.get("ts")),
            )
        elif msg.get("role") in ("user", "assistant"):
            conn.execute(
                "INSERT INTO messages_index(session_id, ts, role, content) VALUES(?, ?, ?, ?)",
                (sid, msg["ts"], msg["role"], msg.get("content", "")),
            )
    conn.commit()


def _getSegmentStartedAt(conn: sqlite3.Connection, session_id: str, segment: int) -> int:
    """推算 segment 的 started_at：segment=0 查 sessions 表；其他查上一 segment 的 ended_at。"""
    if segment == 0:
        row = conn.execute(
            "SELECT started_at FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else 0
    row = conn.execute(
        "SELECT ended_at FROM segments WHERE session_id = ? AND segment = ?",
        (session_id, segment - 1),
    ).fetchone()
    return row[0] if row else 0
```

### 4.6 FTS5 中文分词问题

> **已知限制**：SQLite FTS5 的 `unicode61` 分词器对中文支持有严重问题。

**实测结论**（SQLite 3.42.0）：

| 查询类型 | 结果 |
|----------|------|
| FTS5 MATCH `hello`（英文） | 命中 |
| FTS5 MATCH `Python`（英文，大小写） | 命中 |
| FTS5 MATCH `py*`（前缀） | 命中 |
| FTS5 MATCH `中文`（中文整词） | **不命中** |
| FTS5 MATCH `中`（中文单字） | **不命中** |
| LIKE `%中文%`（中文） | 命中 |

**原因**：`unicode61` 按 Unicode 单词边界分词，中文没有空格分隔，被当成整词处理。MATCH 查询中文时无法匹配。

**应对策略**：

| 场景 | 策略 |
|------|------|
| 英文查询 | FTS5 MATCH（高效，支持前缀、BM25 排序） |
| 纯中文查询 | 降级到 SQLite LIKE（功能正确） |
| 中英混合查询 | 分拆处理：英文用 FTS5，中文部分用 LIKE，结果取并集 |
| 代码/命令搜索 | FTS5 MATCH（如变量名、函数名） |

```python
import re

def search(query: str, limit: int = 5) -> list[dict]:
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', query))

    if has_chinese:
        # 降级到 LIKE（功能正确，但无 BM25 排序）
        pattern = f"%{query}%"
        cur = conn.execute(
            """SELECT m.session_id, m.ts, m.role, m.content
               FROM messages_index m WHERE m.content LIKE ? LIMIT ?""",
            (pattern, limit)
        )
    else:
        # FTS5 MATCH + BM25 排序
        cur = conn.execute(
            """SELECT m.session_id, m.ts, m.role, m.content, bm25(messages_fts)
               FROM messages_fts f JOIN messages_index m ON f.rowid = m.id
               WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts) LIMIT ?""",
            (query, limit)
        )
    return [_rowToDict(r) for r in cur]
```

> **长期方案**：如果中文搜索需求增加，可考虑接入 `jieba` 分词，在写入前将中文文本预先分词存入单独的 `tokens` 列，再对 `tokens` 列建 FTS5。当前先跑 LIKE 降级。

---

## 5. 搜索能力

### 5.1 搜索 API

```python
@dataclass
class SearchResult:
    session_id: str
    ts: int
    role: str
    snippet: str        # 匹配内容片段（前后各 50 字）
    score: float | None # BM25 分数（仅 FTS5 模式有）


def search_sessions(
    query: str,
    limit: int = 5,
    date_from: str = None,     # "2026-04-01"
    date_to: str = None,      # "2026-04-30"
    role: str = None,          # "user" / "assistant"
    session_id: str = None,
) -> list[SearchResult]:
    """
    搜索历史消息。
    
    策略：query 含中文 → LIKE；纯英文 → FTS5 MATCH + BM25
    """
```

### 5.2 召回路径（完整生命周期）

```
用户触发搜索 或 LLM 主动调用 session_search
    │
    ▼
search_sessions(query) → SearchResult[]
    │
    ├── session_id + snippet → 直接展示给用户（轻量场景）
    │
    └── 需要完整上下文时 → get_session_messages(session_id)
                              │
                              ▼
                         从 JSONL 读取该 session 所有消息
                              │
                              ▼
                         传给 LLM 生成总结
                              │
                              ▼
                         总结结果注入当前对话上下文
```

**调用方是谁**：

| 调用方 | 触发方式 | 返回内容 |
|--------|----------|----------|
| 用户（手动搜索） | 命令行 `/search` | snippet 列表，用户选择后进入完整上下文 |
| LLM | tool_calls 里的 `session_search` | 匹配消息片段，LLM 决定是否进一步加载完整 session |
| Compaction | 在归档阶段引用历史 | 加载指定 segment 的原始消息，用于判断是否需要更新已有 skill |

---

## 6. 目录结构总览

```
~/.lampson/
├── core.md                     ← 核心记忆（Agent 启动时全量加载）
├── memory/
│   └── sessions/               ← 原始聊天记录
│       ├── 2026-04-23/
│       │   └── abc123.jsonl    ← 完整 session，含所有 segment
│       └── 2026-04-26/
│           └── def456.jsonl
├── skills/                     ← 技能知识（compaction 归档沉淀）
├── projects/                   ← 项目知识（compaction 归档沉淀）
└── search.db                   ← SQLite（FTS5 + sessions + segments）
```

---

## 7. 改动范围

### 7.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/memory/session_store.py` | JSONL 写入 + SQLite 索引同步 |
| `src/memory/session_search.py` | FTS5 搜索 + LIKE 降级 + 召回 API |
| `src/memory/rebuild_index.py` | 索引重建（流式解析 + 批量 INSERT） |
| `search.db` | SQLite 数据库（首次运行时自动创建） |

### 7.2 修改文件

| 文件 | 改动 |
|------|------|
| `src/core/session.py` | 注入 session store；在 handle_input 中调用写入 |
| `src/core/agent.py` | 在 assistant 回复时写入 tool_result 摘要 |
| `src/core/compaction.py` | 在压缩触发时写入 segment_boundary；session 退出时写入 session_end |

### 7.3 历史数据迁移

现有 `memory/sessions/` 目录下的 `.md` 总结文件迁移为 JSONL 格式：

```python
# migrate_sessions_md_to_jsonl.py
# 读取 memory/sessions/*.md
# 提取 session_id、日期、消息内容
# 转换为 {date}_{session_id}.jsonl 格式
# 写入 memory/sessions/YYYY-MM-DD/{session_id}.jsonl
```

迁移后的 `.md` 文件可保留，待确认迁移完整后再清理。

### 7.4 兼容性

- 新系统独立于现有机制，不影响 core.md / skills / projects
- 迁移完成前，两套存储并存；迁移完成后，旧 `.md` 文件可删除

---

## 8. 已确认决策

- [x] **tool_calls 的 arguments**：完整存储 arguments JSON 字符串
- [x] **tool_result**：存前 500 字摘要（理由：可复现不等于不需要，摘要保留了历史快照价值）
- [x] **FTS5 索引方式**：SQLite 冗余存储 content（Lampson 体量小，冗余影响可忽略）
- [x] **segment 边界 timestamp**：`segment_boundary` 行的 `ts` 即为结束时间，`segments` 表无需 `ended_at` 字段（但 segments 表本身保留）
- [x] **历史数据迁移**：现有 sessions/*.md 总结文件转成 JSONL
- [x] **session_end 标记**：session 退出时写入 `session_end` 行，明确 session 结束
- [x] **next_segment_started_at**：segment_boundary 行记录下一段开始时间，segments 表可准确填充 started_at
- [x] **message_count / segment_count**：不存冗余字段，实时 COUNT() 算
- [x] **source 白名单**：`["cli", "feishu", "api"]`，写入时校验
- [x] **消息不可修改**：不设 UPDATE 触发器
- [x] **FTS5 中文降级**：含中文的查询降级到 LIKE，纯英文用 FTS5 MATCH + BM25
