# Session 连续性设计文档

> 替代旧的 summary 注入方案。新方案：按需恢复完整 session，不再生成/注入 summary。

## 1. 背景

旧方案的问题：
- session 结束时生成 progress summary，新 session 启动时注入 summary 到 system prompt
- summary 只有 200 字，信息密度极低，无法真正延续上下文
- LLM 面临"上次干了啥"的问题时只能靠 session_search 工具猜测，而不是加载真实对话
- idle 超时自动触发 summary 生成，额外消耗一次 LLM 调用

新方案核心思路：**直接恢复完整对话历史，而不是用一段 summary 代替**。

## 2. 新设计

### 2.1 Prompt 改动

#### TOOL_USE_ENFORCEMENT（改）

旧版过于激进，"每轮必须用工具"导致日常问答也被迫调工具。

```
# 工具使用指引
执行具体任务时（写代码、查文件、改配置等），必须立即使用工具行动，不许只描述意图。
回答提问、聊天、确认等场景直接回复即可，不需要硬塞工具调用。
```

#### SESSION_CONTINUITY_GUIDANCE（新增，替代 SESSION_SEARCH_GUIDANCE）

```
当用户提到"上次"、"继续"、"之前那个"等暗示延续旧对话时，使用 session_load 恢复上一次对话历史。
session_load 会把旧 session 的消息加载到当前对话中，你就能自然延续上下文。
如果用户只是泛泛提问（如"上次让我干啥"），先调 session_load 加载最近 session，再回答。
用 session_search 搜索跨多个 session 的历史内容。
```

### 2.2 新增工具：session_load

```python
SCHEMA = {
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
```

**行为**：
- 从 JSONL 读取指定 session 的消息
- 追加到当前 `llm.messages`（system prompt 之后）
- 返回加载结果摘要（加载了几条、时间范围等）

### 2.3 新增命令：/resume

```
/resume          → 列出最近 5 个 session（ID、时间、消息数）
/resume <id>     → 加载指定 session 到当前对话
```

### 2.4 SessionManager 改动

**删除**：
- `_maybe_backfill_prev_session_summary()` — 删
- `_reset_session()` 中的 summary 生成逻辑 — 简化
- `_inject_resume_summary()` — 删
- idle 超时不再自动注入 summary

**保留**：
- idle 超时重置 session（清空对话历史，避免 token 无限增长）
- `end_session()` 写入结束标记（不写 summary）
- `close_all()` 做清理

**简化后的 idle 超时流程**：
1. 检测到 session idle 超时
2. 调用 `session_store.end_session(old_id)`（无 summary）
3. 创建新 session（空白，不注入任何旧信息）
4. LLM 收到用户消息后，根据上下文判断是否需要调 `session_load` 恢复

### 2.5 删除清单

| 文件 | 操作 |
|------|------|
| `src/core/session_resume.py` | 整个文件删除 |
| `src/core/session_manager.py` | 删除 summary 生成/注入逻辑，简化 idle 重置 |
| `src/core/session.py` | 删除 `save_summary()`、`_inject_resume_summary()` |
| `src/memory/session_store.py` | 删除 `get_last_session_summary()`、`update_session_summary()`、`get_prev_session_id()`，`end_session()` 移除 summary 参数 |
| `docs/PROJECT.md` | 删除 summary/resume 相关章节 |
| `src/core/prompt_builder.py` | 删除旧 `SESSION_SEARCH_GUIDANCE`，改为新的 `SESSION_CONTINUITY_GUIDANCE` |

### 2.6 新增清单

| 文件 | 操作 |
|------|------|
| `src/tools/session_load.py` | 新增 session_load 工具 |
| `src/core/tools.py` | 注册 session_load |
| `src/core/prompt_builder.py` | 新增 `SESSION_CONTINUITY_GUIDANCE`，改写 `TOOL_USE_ENFORCEMENT` |
| `src/core/session.py` | 新增 `load_session()` 方法和 `/resume` 命令处理 |

## 3. 数据流

```
用户发消息："继续上次那个任务"
  → LLM 收到消息，匹配 SESSION_CONTINUITY_GUIDANCE 中的触发词
  → LLM 调用 session_load()（不填 session_id，加载最近的）
  → session_load 从 JSONL 读取最近 session 的消息
  → 消息追加到 llm.messages
  → LLM 现在拥有完整上下文，自然延续对话
```

```
用户发消息："什么是 GIL"
  → LLM 直接回答，不调任何工具（因为 TOOL_USE_ENFORCEMENT 不再强制）
```

```
用户发消息："帮我看看日志"
  → LLM 调用 shell 工具执行命令（因为这是具体任务）
```

## 4. session_load 实现细节

```python
# src/tools/session_load.py

def run(params: dict) -> str:
    session_id = params.get("session_id", "")
    limit = int(params.get("limit", 50))

    if not session_id:
        # 找最近一个已结束的 session
        sessions = session_store.list_recent_sessions(limit=1, source=current_source)
        if not sessions:
            return "没有找到历史 session。"
        session_id = sessions[0].session_id

    messages = session_store.get_session_messages(session_id, limit=limit)
    if not messages:
        return f"Session {session_id} 没有消息记录。"

    # 追加到当前对话
    # ... 注入到 llm.messages ...

    return f"已加载 session {session_id} 的最近 {len(messages)} 条消息。"
```

## 5. 与 session_search 的关系

- `session_search`：搜索多个 session 中的特定内容（FTS + 语义搜索）
- `session_load`：恢复特定 session 的完整对话历史
- 两者互补，不冲突

## 6. 验收标准

1. 用户日常提问（"什么是 GIL"）→ LLM 直接回答，不调工具
2. 用户说"继续上次"→ LLM 调用 session_load 恢复历史
3. `/resume` 命令列出最近 sessions
4. `/resume <id>` 加载指定 session
5. idle 超时重置 session 时不再生成 summary
6. 旧 session_resume.py 已删除
7. session_store 中 summary 相关函数已删除
