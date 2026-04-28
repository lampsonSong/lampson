# Session 连续性设计文档

> 替代旧的 summary 注入方案。当前方案：不再主动注入任何上下文，LLM 按需使用 session_search/session_load 工具。

## 1. 背景

旧方案的问题：
- session 结束时生成 progress summary，新 session 启动时注入 summary 到 system prompt
- summary 只有 200 字，信息密度极低，无法真正延续上下文
- idle 超时自动触发 summary 生成，额外消耗一次 LLM 调用

中间方案（已废弃）的问题：
- SESSION_CONTINUITY_GUIDANCE 指令让 LLM 在泛泛提问时也自动调 session_load
- 实际效果：新 session 开始时，用户说"重启吧"这种无明确对象的指令，LLM 也会错误触发 session_load → session_search 链
- 结论：LLM 不应该被指令驱动去恢复上下文，应该由用户显式请求

当前方案核心思路：**不主动恢复，LLM 自己决定何时使用 session_search/session_load**。

## 2. 当前设计

### 2.1 Prompt 改动

#### TOOL_USE_ENFORCEMENT（已改）

```
# 工具使用指引
执行具体任务时（写代码、查文件、改配置等），必须立即使用工具行动，不许只描述意图。
回答提问、聊天、确认等场景直接回复即可，不需要硬塞工具调用。
```

#### SESSION_CONTINUITY_GUIDANCE（已删除）

不再向 LLM 注入任何会话延续指引。LLM 自己决定何时需要搜索/加载历史。

### 2.2 现有工具

**session_search**：搜索跨多个 session 的历史内容（FTS + 语义搜索）。LLM 在需要时自行调用。

**session_load**：加载指定 session 的完整对话历史到当前对话。LLM 在需要时自行调用，或用户通过 `/resume` 命令触发。

### 2.3 命令

```
/new              → 结束当前 session，创建空白 session
/resume           → 列出最近 5 个 session（ID、时间、消息数）
/resume <id>      → 加载指定 session 到当前对话
```

### 2.4 /new 命令实现

`/new` 通过 `SessionManager.reset_session()` 统一实现，所有入口（CLI、飞书 listener）都走同一路径：

1. `session.handle_input("/new")` 返回 `HandleResult(is_new=True, is_command=True)`
2. 调用方检测 `result.is_new`
3. 调用 `mgr.reset_session(channel, sender_id)`
4. `reset_session()` 内部：结束旧 session → 创建新空白 session → 更新缓存 → 返回新 session

**入口处理**：

| 入口 | 文件 | 处理方式 |
|------|------|---------|
| CLI | `src/cli.py` | 检测 `result.is_new` → `mgr.reset_session("cli", "default")` |
| 飞书 | `src/feishu/listener.py` | 检测 `result.is_new` → `mgr.reset_session("feishu", open_id)` |

### 2.5 SessionManager

**保留**：
- idle 超时重置 session（清空对话历史，避免 token 无限增长）
- `end_session()` 写入结束标记（不写 summary）
- `close_all()` 做清理
- `reset_session()` 公开方法（线程安全，供 `/new` 使用）

**简化后的 idle 超时流程**：
1. 检测到 session idle 超时
2. 调用 `session_store.end_session(old_id)`（无 summary）
3. 创建新 session（空白，不注入任何旧信息）

### 2.6 删除清单

| 文件 | 操作 | 状态 |
|------|------|------|
| `src/core/session_resume.py` | 整个文件删除 | ✅ |
| `src/core/session_manager.py` | 删除 summary 生成/注入逻辑，简化 idle 重置 | ✅ |
| `src/core/session.py` | 删除 `save_summary()`、`_inject_resume_summary()` | ✅ |
| `src/memory/session_store.py` | 删除 summary 相关函数，`end_session()` 移除 summary 参数 | ✅ |
| `src/core/prompt_builder.py` | 删除 `SESSION_CONTINUITY_GUIDANCE`、旧 `SESSION_SEARCH_GUIDANCE` | ✅ |

## 3. 数据流

```
用户发消息："继续上次那个任务"
  → LLM 自主判断需要历史上下文
  → LLM 调用 session_load()（不填 session_id，加载最近的）
  → session_load 从 JSONL 读取最近 session 的消息
  → 消息追加到 llm.messages
  → LLM 现在拥有完整上下文，自然延续对话
```

```
用户发消息："什么是 GIL"
  → LLM 直接回答，不调任何工具
```

```
用户发消息："帮我看看日志"
  → LLM 调用 shell 工具执行命令
```

```
用户发消息："重启吧"（无明确对象）
  → LLM 不确定，直接问用户"重启什么？"
  → 不再错误触发 session_load/session_search
```

## 4. 测试

`tests/test_session_new.py` 覆盖：
- `reset_session()` 返回新 session（feishu/CLI 渠道）
- `reset_session()` 调用 `end_session` 结束旧 session
- 无旧 session 时不报错
- `end_session` 异常时不崩溃
- 连续 reset 返回不同对象
- `/new` 命令返回 `is_new=True`
- `SESSION_CONTINUITY_GUIDANCE` 已从代码中删除
