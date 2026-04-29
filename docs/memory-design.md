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
├── core.md                     ← 核心记忆（Agent 启动时全量加载）
├── memory/
│   └── sessions/               ← 原始消息 JSONL（source of truth）
│       ├── 2026-04-26/
│       │   ├── cli/            ← 按 source 分子目录（cli/feishu/telegram 等）
│       │   │   └── 2026-04-26_{session_id}.jsonl
│       │   └── feishu/
│       │       └── 2026-04-26_{session_id}.jsonl
│       └── 2026-04-27/
│           └── cli/
│               └── 2026-04-27_{session_id}.jsonl
└── search.db                   ← SQLite（FTS5 + sessions + segments + messages_embedding）
    ├── sessions 表              ← session 索引
    ├── segments 表              ← segment 边界
    ├── messages_index 表        ← content + raw_json（FTS5 + 双向重建）
    ├── messages_fts 表          ← FTS5 全文索引（jieba 预分词）
    └── messages_embedding 表    ← Embedding 向量（智谱 API，异步写入）
```

### 2.2 职责分工

| 存储层 | 格式 | 用途 | 可丢失？ |
|--------|------|------|----------|
| JSONL | 文本 | 原始消息存档，人可读，source of truth | **不可丢**（SQLite 可从 JSONL 重建） |
| SQLite | 数据库 | sessions/segments 查询、FTS5 BM25 搜索、Embedding 向量检索 | **可丢**（从 JSONL 重建） |

**原则**：JSONL 和 SQLite **双向可重建**。任一层丢失，均可从另一层完整恢复。

### 2.3 跨天 session 处理

session 以**开始日期**为目录，生命周期内始终在同一文件，不因跨天拆分。

```
sessions/
├── 2026-04-26/                   ← session 于 4/26 开始
│   └── cli/
│       └── 2026-04-26_abc123.jsonl  ← 跨 26、27、28 号，不移动
└── 2026-04-28/                   ← session 于 4/28 开始（独立 session）
    └── feishu/
        └── 2026-04-28_xyz789.jsonl
```

### 2.4 JSONL 行类型汇总

JSONL 中共四种行类型：

| type | 出现时机 | 说明 |
|------|----------|------|
| `session_start` | session 创建时 | 写入 sessions 表（started_at, source） |
| 普通消息行 | 每条消息 | user/assistant，含 content、tool_calls、tool_result_summary、referenced_tool_results |
| `segment_boundary` | compaction 触发时 | 标记 segment 结束，含 next_segment_started_at |
| `session_end` | session 退出时 | 标记 session 结束。不再生成或注入 summary（见 session-continuity-design.md）。 |

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
| `session_end` | session 退出 | 标记 session 正常结束。不再生成 summary（session summary 机制已移除）。 |

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

**session_end 只标记结束，不生成 summary**。summary 补生成机制已移除（见 session-continuity-design.md）。

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
    summary TEXT                 -- （已废弃）session summary 机制已移除，不再使用此字段
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

### 4.3 messages_index + messages_fts（FTS5 + jieba 预分词）+ messages_embedding

```sql
-- 消息索引表（冗余存储 content + raw_json）
-- raw_json 存完整 JSONL 行，支持从 SQLite 反向重建 JSONL
CREATE TABLE messages_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    role TEXT,
    content TEXT,                      -- jieba 预分词后的文本（空格分隔），供 FTS5 索引
    raw_json TEXT NOT NULL             -- 完整 JSONL 原始行，用于双向重建
);

-- FTS5 全文索引
-- 不注册自定义 tokenizer，用默认 unicode61 按空格分词
-- 中文分词在写入时由 jieba 预处理完成
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

-- Embedding 向量（远程 API 计算，异步批量写入）
-- 单一 provider，不可 fallback（不同模型向量空间不同）
CREATE TABLE messages_embedding (
    msg_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    embedding BLOB NOT NULL,           -- 智谱 embedding-3: 2048维 × 4 bytes = 8192 bytes
    provider TEXT NOT NULL DEFAULT 'zhipu',  -- 标记使用的 provider，切换后旧缓存作废
    indexed_at INTEGER NOT NULL
);
CREATE INDEX idx_embedding_session ON messages_embedding(session_id);
```

> **不设 UPDATE 触发器**：消息只可追加不可修改，UPDATE 视为 bug，不支持。

**jieba 预分词机制**：
- **写入时**：`content` 字段存 `jieba.lcut(text)` 用空格 join 后的结果（如 `"帮 我 查 一下 需求 文档"`）
- **查询时**：`query` 也用 `jieba.lcut(query)` 预分词后做 FTS5 MATCH
- FTS5 只需按空格切分，不需要自定义 tokenizer，实现极简单
### 4.4 写入时机

| 事件 | 操作 |
|------|------|
| session_start | INSERT sessions 表 |
| 新消息写入 JSONL | INSERT messages_index（content 经 jieba 预分词，raw_json 存原始行）+ 触发器同步 FTS5 |
| Embedding | append_message() 末尾 enqueue 到 EmbeddingIndexer 队列（异步，不阻塞） |
| segment_boundary | INSERT segments 新行 |
| session_end | UPDATE sessions(ended_at) + UPDATE segments(ended_at) |

### 4.5 双向重建（rebuild_index / rebuild_jsonl）

JSONL 和 SQLite 互为备份，任一层丢失均可从另一层完整恢复。

#### 4.5.1 JSONL → SQLite（rebuild_index）

```python
def rebuild_index(sessions_dir: Path, db_path: Path) -> None:
    """
    从 JSONL 重建 SQLite 索引。

    步骤：
    1. 清空 messages_fts / messages_index / segments / sessions 表
    2. 流式解析 JSONL（逐行读取，不全量 load）
    3. jieba 预分词后 INSERT messages_index（content）+ raw_json
    4. 批量 INSERT（每 1000 条 commit 一次）
    """
    import jieba
    conn = sqlite3.connect(db_path)
    # ... 清空表 ...
    for jsonl_path in _iterJsonlFiles(sessions_dir):
        for line in jsonl_path:
            msg = json.loads(line)
            if msg.get("role") in ("user", "assistant"):
                raw = json.dumps(msg, ensure_ascii=False)
                segmented = " ".join(jieba.lcut(msg.get("content", "")))
                conn.execute(
                    "INSERT INTO messages_index(session_id, ts, role, content, raw_json) VALUES(?, ?, ?, ?, ?)",
                    (msg["session_id"], msg["ts"], msg["role"], segmented, raw),
                )
            # ... session_start / session_end / segment_boundary 处理 ...
    conn.commit()
    conn.close()
```

#### 4.5.2 SQLite → JSONL（rebuild_jsonl）

```python
def rebuild_jsonl(db_path: Path, sessions_dir: Path) -> None:
    """
    从 SQLite 重建 JSONL 文件。

    依赖 raw_json 字段（完整 JSONL 行），可无损恢复所有行类型。

    步骤：
    1. 创建 sessions_dir（如不存在）
    2. 按 session_id 分组，SELECT raw_json ORDER BY ts
    3. session 日期从 sessions.started_at 推算目录名
    4. 按 ts 排序后写入对应的 {date}_{session_id}.jsonl
    """
```

**特殊行存储约定**：session_start / segment_boundary / session_end 等特殊行也 INSERT 到 messages_index，`role` 为 NULL，`content` 为空（不进 FTS5），`raw_json` 存完整 JSON。这样反向重建时 `SELECT raw_json ORDER BY ts` 即可还原完整 JSONL。

**双向重建保证**：

| 丢失场景 | 恢复方式 | 数据完整性 |
|----------|----------|-----------|
| JSONL 丢失 | `rebuild_jsonl()` 从 SQLite 恢复 | 完整（raw_json 存原始行） |
| SQLite 丢失 | `rebuild_index()` 从 JSONL 恢复 | 完整（JSONL 是原始数据） |
| 两者都丢失 | 无法恢复 | — |

### 4.6 三层搜索架构

```
用户搜索
    |
    |-- Layer 1: BM25 (FTS5 + jieba 预分词)
    |       query → jieba.lcut(query) → FTS5 MATCH
    |       中英文统一，BM25 排序
    |       取 top-N 候选（如 20 条）
    |
    |-- Layer 2: Embedding (远程 API)
    |       对 top-N 候选调 embedding API 算向量（已有缓存则跳过）
    |       余弦相似度打分
    |
    \-- Layer 3: 混合打分
            final_score = 0.7 x bm25 + 0.3 x cosine
            按 final_score 排序返回
```

**为什么不预计算 embedding？**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 写时生成 | 搜索零延迟 | 写入慢；每次调 API 有成本 |
| **搜索时实时算（采纳）** | 不占写入路径；按需算 | 搜索多一次 API 调用（~100ms，可接受） |

**Embedding 配置**：config.yaml 中可配置，不配则不启用 embedding，降级为纯关键词搜索：

```yaml
embedding:
  provider: "zhipu"                          # zhipu（默认）/ 其他 OpenAI 兼容 provider
  model: "embedding-3"                       # 智谱 embedding-3（2048 维）
  base_url: "https://open.bigmodel.cn/api/paas/v4/"  # 必须显式配置
  api_key: ""                                # 必须显式配置
```

**必须显式配置**：base_url 和 api_key 不继承 llm 段，不配则 embedding 功能不启用（降级为纯关键词搜索）。

### 4.7 Embedding 写入：异步批量队列

EmbeddingIndexer 在后台线程运行，攒 batch（默认 32 条或每 5 秒）后调远程 API 批量算 embedding：

```python
class EmbeddingIndexer:
    def __init__(self, provider: str, model: str, api_key: str, base_url: str, batch_size: int = 32):
        self._provider = provider
        self._model = model
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._batch_size = batch_size
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def enqueue(self, msg_id: str, session_id: str, ts: int, content: str) -> None:
        self._queue.put({"msg_id": msg_id, "session_id": session_id, "ts": ts, "content": content})

    def _run(self) -> None:
        batch = []
        while not self._stop.wait(5.0):  # 每 5 秒醒来 flush 一次
            while len(batch) < self._batch_size and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            if batch:
                self._flush(batch)
                batch = []
        # 退出前 flush 剩余
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if batch:
            self._flush(batch)

    def _flush(self, batch: list[dict]) -> None:
        texts = [b["content"] for b in batch]
        vectors = self._encode(texts)  # 远程 API 调用
        conn = _get_db()
        now = int(time.time() * 1000)
        for item, vec in zip(batch, vectors):
            blob = struct.pack(f"{len(vec)}f", *vec)
            conn.execute(
                "INSERT OR REPLACE INTO messages_embedding VALUES(?, ?, ?, ?, ?, ?)",
                (item["msg_id"], item["session_id"], item["ts"], blob, self._provider, now)
            )
        conn.commit()

    def _encode(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]
```

> **为什么不用 sentence-transformers（本地）？** 本地模型需要 PyTorch 运行时（~2GB+），包体积大、首次加载慢。智谱 embedding-3 通过 OpenAI 兼容 API 调用，Lampson 已有 `openai` SDK 和 api_key，零额外依赖。

## 5. 搜索能力

### 5.1 三层搜索流程

```
search_sessions(query)
    |
    |-- Layer 1: FTS5 BM25（jieba 预分词）
    |       query → jieba.lcut(query) → FTS5 MATCH
    |       取 top-N 候选（如 20 条）
    |
    |-- Layer 2: Embedding 语义重排（远程 API）
    |       对 top-N 候选调 embedding API 算向量（已有缓存则跳过）
    |
    \-- Layer 3: 混合打分
            final_score = 0.7 x bm25_normalized + 0.3 x cosine
```

**为什么三层而不是只用 Embedding？** Embedding 丢失精确关键词（如 `splunk`），BM25 保留精确匹配，两者结合互补。

### 5.2 搜索 API

```python
@dataclass
class SearchResult:
    session_id: str
    ts: int
    role: str
    snippet: str
    bm25_score: float | None
    cosine_score: float | None
    final_score: float | None


def search_sessions(
    query: str,
    limit: int = 5,
    date_from: str = None,
    date_to: str = None,
    role: str = None,
    session_id: str = None,
) -> list[SearchResult]:
    # Layer 1: jieba预分词 → FTS5 MATCH -> top-N候选
    # Layer 2: Embedding余弦重排
    # Layer 3: 混合打分
```

### 5.3 召回路径（完整生命周期）

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
│       │   └── cli/
│       │       └── 2026-04-23_abc123.jsonl    ← 完整 session，含所有 segment
│       └── 2026-04-26/
│           └── feishu/
│               └── 2026-04-26_def456.jsonl
├── skills/                     ← 技能知识（compaction 归档沉淀）
├── projects/                   ← 项目知识（compaction 归档沉淀）
└── search.db                   ← SQLite（messages_index + FTS5 + sessions + segments + messages_embedding）
```

---

## 7. 改动范围

### 7.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/memory/session_store.py` | JSONL 写入 + SQLite 索引同步 |
| `src/memory/session_search.py` | FTS5 搜索（jieba 分词）+ 召回 API |
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
- [x] **FTS5 索引方式**：jieba 预分词 + FTS5 unicode61（不注册自定义 tokenizer）
- [x] **messages_index 表**：冗余存储 content（jieba 分词后）+ raw_json（完整 JSONL 行）
- [x] **双向重建**：JSONL ↔ SQLite 双向可重建（rebuild_index / rebuild_jsonl）
- [x] **segment 边界 timestamp**：`segment_boundary` 行的 `ts` 即为结束时间
- [x] **历史数据迁移**：现有 sessions/*.md 总结文件转成 JSONL
- [x] **session_end 标记**：session 退出时写入 `session_end` 行，明确 session 结束
- [x] **next_segment_started_at**：segment_boundary 行记录下一段开始时间
- [x] **message_count / segment_count**：不存冗余字段，实时 COUNT() 算
- [x] **source 白名单**：`["cli", "feishu", "api"]`，写入时校验
- [x] **消息不可修改**：不设 UPDATE 触发器
- [x] **Embedding provider**：单一 provider（不做 fallback，不同模型向量空间不同）
- [x] **Embedding 模型**：智谱 embedding-3（2048 维），OpenAI 兼容接口，零额外依赖
- [x] **Embedding 配置**：base_url/api_key 必须显式配置，不继承 llm 段，不配则降级为纯关键词搜索
- [x] **Embedding 缓存**：messages_embedding 表加 `provider` 字段，切换 provider 后旧缓存作废
- [x] **Embedding 存储**：SQLite BLOB 列（2048维 × 4 bytes = 8192 bytes），不单独用向量数据库
- [x] **三层搜索**：FTS5 BM25（jieba 分词）→ Embedding API（缓存优先）→ 混合打分（0.7/0.3）
- [x] **不用 ripgrep**：统一用 FTS5，不引入 ripgrep 依赖
- [x] **不用 sentence-transformers**：去掉本地 PyTorch 依赖，全部走远程 API

---

## 9. Trace Log（session_store.py 已实现）

> 代码：`src/memory/session_store.py` 中的 `append_trace()` / `write_*_trace()` / `gc_tool_bodies()`
> 测试：`tests/test_trace.py`

JSONL 中除基础消息行外，还有用于调试/计费的 trace 行：

### 行类型

| type | 触发时机 | 关键字段 | 用途 |
|------|----------|----------|------|
| `system_prompt` | 每次 LLM 调用 | prompt_hash、content（hash 已存在时 content=null） | 跟踪 system prompt 变化 |
| `llm_call` | 每次 LLM 调用（含重试） | model、input_tokens、output_tokens、duration_ms、stop_reason | 调试/计费 |
| `llm_error` | LLM 调用失败 | model、error_type、detail（前500字）、duration_ms | 错误追踪 |
| `tool_call` | 每次工具调用 | id、name、arguments（完整 JSON） | 调用链追踪 |
| `tool_result` | 工具执行结果 | id、result_inline（≤2KB）或 result_ref（hash）、error | 结果追踪 |

### assistant vs llm_call 边界

- `assistant`：**对话摘要用**，记录最终回复，只写一次
- `llm_call`：**调试/计费用**，记录每次实际调用（含 fallback 重试），一次调用链可能有多条

### 大型 tool_result 存储分离

- ≤2KB：`result_inline` 内联在 JSONL 行中
- \>2KB：SHA256 hash 写入 `tool_bodies/{hash}.json`，JSONL 行只存 `result_ref`（只写不检查，覆盖无影响）

### GC

`gc_tool_bodies(ttl_days=60)` 按 mtime 时间窗口清理过期文件，不做引用计数。

### 查询方式

直接扫 JSONL（用 jq/grep），暂不做额外索引。
