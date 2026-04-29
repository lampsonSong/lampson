# Trace Log 设计文档

**目标**：一套 JSONL，同时满足「摘要」和「完整复现 bug」。

## 核心设计

**一套存储，分层使用**：
- `sessions/*.jsonl` — 所有行，按 ts 顺序
- `tool_bodies/{hash}.json` — 大型 tool result（>2KB），按 SHA256 hash 去重

**JSONL 行类型**：

| type | 触发时机 | 关键字段 |
|------|----------|----------|
| `session_start` | session 创建 | session_id、source |
| `user` | 用户发消息 | content |
| `assistant` | LLM 回复 | content、**model**、**input_tokens**、**output_tokens**、**stop_reason**、**tool_calls** |
| `segment_boundary` | compaction | segment、next_segment_started_at、**archive**（归档路径） |
| `session_end` | session 退出 | ts |
| `system_prompt` | 每次 LLM 调用 | prompt_hash、content（hash 已存在时 content=null） |
| `llm_call` | 每次 LLM 调用 | model、input_tokens、output_tokens、duration_ms、stop_reason |
| `llm_error` | LLM 调用失败 | model、error_type、detail（前500字）、duration_ms |
| `tool_call` | 每次工具调用 | id、name、arguments（完整 JSON，序列化后内联） |
| `tool_result` | 工具执行结果 | id、result_ref（hash）或 result_inline（≤2KB）、result_size、error |

## assistant vs llm_call 边界

**两者不冗余，各有职责**：

- `assistant` 行：**对话摘要用**，记录最终回复内容（role=assistant 的 content），供人类快速浏览。只写一次（最终回复），不记录重试。
- `llm_call` 行：**调试/计费用**，记录每次实际 LLM 调用（含重试），含 model、tokens、duration。一次调用链可能有多次 llm_call（如 fallback 重试），但只有一次 assistant（最终回复写入 JSONL）。

## 数据示例

**assistant（对话摘要）**：
```jsonl
{"ts": 1745800002000, "type": "assistant", "session_id": "abc123", "role": "assistant", "content": "好的，我来帮你查看...", "model": "glm-5-flash", "input_tokens": 1500, "output_tokens": 320, "stop_reason": "stop", "tool_calls": null}
```

**llm_call（调试/计费）**：
```jsonl
{"ts": 1745800002000, "type": "llm_call", "session_id": "abc123", "model": "glm-5-flash", "input_tokens": 1500, "output_tokens": 320, "duration_ms": 1200, "stop_reason": "stop"}
```

**llm_error**：
```jsonl
{"ts": 1745800002000, "type": "llm_error", "session_id": "abc123", "model": "glm-5-flash", "error_type": "APITimeoutError", "detail": "Request timed out after 45s", "duration_ms": 45000}
```

**tool_call**：
```jsonl
{"ts": 1745800003000, "type": "tool_call", "session_id": "abc123", "id": "call_001", "name": "file_read", "arguments": "{\"path\": \"~/lampson/src/core/agent.py\"}"}
```
注：arguments 是序列化后的 JSON 字符串（用 `json.dumps()`），避免嵌入对象时的换行问题。

**tool_result（大型结果，存 hash）**：
```jsonl
{"ts": 1745800004000, "type": "tool_result", "session_id": "abc123", "id": "call_001", "result_size": 15360, "result_ref": "sha256:def456", "error": null}
```

**tool_result（小型结果，内联）**：
```jsonl
{"ts": 1745800004000, "type": "tool_result", "session_id": "abc123", "id": "call_002", "result_size": 320, "result_inline": "文件共 320 行...", "error": null}
```

**tool_result（异常情况）**：
```jsonl
{"ts": 1745800004000, "type": "tool_result", "session_id": "abc123", "id": "call_003", "result_size": 0, "result_inline": null, "error": {"type": "TimeoutError", "message": "工具执行超时，已被 kill"}}
```
注：工具执行失败时 error 字段为结构化对象 `{type, message}`，而非字符串。

**tool_bodies/{hash}.json**：
```json
{"hash": "sha256:def456", "size": 15360, "content": "<完整 tool result 内容>"}
```

## 去重策略

| 内容 | 去重方式 |
|------|----------|
| system_prompt | prompt_hash 相同则 content=null（行仍写入，省的是磁盘而非 I/O） |
| tool_result >2KB | SHA256（**写死算法，不留扩展口**）→ `tool_bodies/{hash}.json`，**只写不检查** |
| tool_result ≤2KB | result_inline 内联，不创建文件 |

**只写不检查**：直接计算 hash 并写入，不检查文件是否存在。相同内容重复写入但内容相同，覆盖无影响。优点：避免每次写入前的 I/O 检查。

**时序信息不丢失**：`tool_result` 行本身有 ts 和 id，通过 id 与 `tool_call` 关联。即使 result 内容相同（hash 相同），时序和调用链信息已在 JSONL 行中保留。

## 存储结构

```
~/.lampson/memory/
├── sessions/
│   └── {date}/{source}/{session_id}.jsonl
└── tool_bodies/          # 所有 session 共享
    └── {hash}.json
```

## 查询方式

**直接扫 JSONL**（不做额外索引）：
- 按 session_id 过滤 → 获取某个 session 的完整 trace
- 按 type 过滤 → 只看 llm_call、tool_call 等特定行
- 用 jq/grep 处理：`jq 'select(.type == "llm_error")' sessions/.../*.jsonl`

**未来扩展方向**（暂不做）：
- 按 tool_call name 搜索 → 建 `tool_calls_index` 表
- 按 llm_error type 搜索 → 建 `llm_errors_index` 表

## GC / 清理策略

tool_bodies 只用**时间窗口**清理，不做引用计数。

### 时间窗口（默认 60 天）

- 每次写入 tool_bodies 时记录 `mtime`
- GC 时删除 mtime 早于 60 天的文件
- 配置项：`trace.tool_bodies.ttl_days = 60`

### 触发时机

- 定时任务（如每天一次）做全量时间窗口清理
- session 结束时不做额外检查（依赖定时 GC）

## 时间戳说明

- 所有 ts 字段为**毫秒级 Unix epoch**
- 时区：UTC（JSONL 内容无关时区，解析时自行转换）

## 改动范围

1. **session_store.py**：新增 `append_trace()`、`write_tool_result()`
2. **agent.py**：在 `_chat_with_fallback()` 和 `_run_tool_loop()` 注入 trace 写入
3. **session.py**：扩展 assistant 行字段（model、tokens、stop_reason）

## 兼容性

- 历史 JSONL 不变，新增行类型向后兼容
- SQLite FTS5 索引不受影响（新增行不入索引）
