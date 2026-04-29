# Lampson - Compaction 设计文档

## 目标

对话持续进行，context window 有限。每次对话结束后，对历史消息做**归档**（Archive），而不是丢弃。

核心原则：
- **归档而不是丢弃** — 有价值的内容沉淀到 skill / project 文件
- **保留原始消息** — 只对必须压缩的部分做摘要，不反复摘要造成信息损耗
- **可迭代** — 未达标自动继续压缩，最多 max_iterations 轮

---

## 触发条件

| 条件 | 阈值 | 说明 |
|------|------|------|
| Token 估算 | `tokens / context_window >= trigger_threshold`（默认 80%） | 保护 context 不溢出 |
| stopReason 白名单 | `end_turn` 或 `aborted` | 防止工具调用中途被打断时触发 |

触发由 Session 层统一管理（`agent.maybe_compact()`），不在 gateway 层调用。

---

## 三阶段流程

### Phase 1：分类（Classify）

LLM 分析对话历史，逐条分类消息：
- `keep`：当前问题核心上下文，后续还要继续
- `archive`：可沉淀到 skill/project 文件的内容（技术方案、决策、踩坑记录）
- `discard`：闲聊、礼貌性回复、无效内容

同时分析 tool_call 结果：被 assistant 回复引用过的保留（keep），未被引用的丢弃（discard）。

### Phase 2：归档（Archive）

1. 读取已有 skill/project 文件内容
2. LLM 将新归档内容与已有内容整合（merge/update/evict/append）
3. 写回 skill/project 文件（写前备份）
4. 写 `segment_boundary` 到 session JSONL（供 resume 重建上下文）

### Phase 3：摘要（Summarize）

对剩余 keep 消息生成结构化摘要（问题/约束/进度/决策/关键文件），替换对话历史。

---

## 两阶段压缩

### 阶段一：分类归档

保留 keep 列表 + 最近 N 条消息（保障上下文连贯，N 默认 3）。

### 阶段二：摘要压缩（条件触发）

如果阶段一后 token 仍 > 原始的 50%，触发第二层：
- 分离最近 N 轮原文（不动） vs 其余 keep 消息
- 对其余 keep 消息生成分组摘要（带 `is_compaction_summary` 标记，后续压缩时跳过）
- 最终 messages = [system] + [summary] + [最近 N 轮原文]

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `keep_recent_n` | 3 | 保留最近 N 轮对话不做摘要 |
| `summary_trigger_ratio` | 0.5 | 阶段一后仍超过此比例则触发阶段二 |

---

## 进度回调

`compact()` 方法接受 `progress_callback` 参数，在各步骤间发送进度消息：

```
[1/6] 正在分析对话内容...
[2/6] 正在写入会话边界...
[3/6] 正在读取已有归档文件...
[4/6] 正在写入归档文件...
[5/6] 正在写入压缩日志...
[6/6] 正在构建剩余上下文...
```

临时 LLM 客户端使用 600s timeout，避免大上下文压缩超时。

---

## 召回路径

归档写进 skill/project 后的召回：

1. **skill injection**：每次对话开始时，relevant skills 自动注入 context
2. **session_search**：用户或 LLM 主动搜索历史 JSONL
3. **project_context()**：LLM 在规划任务时加载相关 project 内容

---

## 配置项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | true | 是否启用自动压缩 |
| `trigger_threshold` | 0.8 | 触发压缩的 token 占比 |
| `end_threshold` | 0.3 | 压缩后目标 token 占比 |
| `context_window` | 131072 | 模型上下文窗口大小 |
| `max_iterations` | 3 | 单次压缩最大迭代轮数 |
| `enable_archive` | true | 是否启用归档阶段 |

---

## Session 生命周期时序

| 阶段 | 触发时机 | 操作 |
|------|----------|------|
| **运行时** | Token 占比 >= trigger_threshold | `run_compaction()` → 写 segment_boundary + skill/project |
| **运行时** | `/compaction` 命令 | `force_compact()` → 同上，带进度回调 |
| **退出时** | 用户结束对话 / 断连 | core.md 更新检查（累计 archive > 5 次或距上次更新 > N 小时） |
| **退出时** | 同上 | 写入 `session_end` 行到 JSONL |
