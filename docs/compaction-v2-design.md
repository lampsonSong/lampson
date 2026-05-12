# Compaction V2 方案设计

## 1. 现状问题

当前 compaction 实现的核心问题：

1. **逐条 classify 不靠谱** — 每条消息只给 LLM 150 字符，分类质量差；30 条/批 + token 预算限制，343 条消息只处理了 26 条，剩下的"隐式丢弃"
2. **消息序列完整性无保障** — assistant(tool_calls) 和 tool result 可能被拆到不同批次、不同分类，导致 API 报 400
3. **archive 和压缩耦合** — 知识沉淀（archive）和腾空间（compact）是不同的事，不应在压缩流程里做
4. **msg_id 匹配有 bug** — classify 用 `f"msg_{id(msg)}"` 内存地址作为 id，`_build_remaining_messages` 匹配不上，keep 形同虚设

## 2. 新方案核心思路

**以"轮"（turn）为单位，不逐条分类。**

一轮 = 从一条 user query 到下一条 user query 之前的所有消息（含 assistant 回复、tool_calls、tool_results）。

```
原始消息序列（以 10 轮为例）：

Turn 1: [user] → [assistant] → [tool_call] → [tool_result] → [assistant]
Turn 2: [user] → [assistant] → [tool_call] → [tool_result] → ... → [assistant]
...
Turn 9: [user] → [assistant]
Turn 10: [user] → [assistant] → [tool_call] → [tool_result] → [assistant]
```

## 3. 算法流程

### 3.1 分段

以 user query 为锚点，将 messages 切分为若干轮（turns）。

```
def split_into_turns(messages: list) -> list[Turn]:
    """每轮 = 一条 user 消息 + 后续所有非 user 消息。"""
```

### 3.2 计算 tail 占比

```
total_turns = len(turns)                        # 总轮数
tail_count = max(1, ceil(total_turns * 0.2))    # 最后 20% 轮数
tail_len = sum(turn.byte_length for turn in turns[-tail_count:])
total_len = sum(turn.byte_length for turn in turns)
ratio = tail_len / total_len
```

### 3.3 分支决策

```
if ratio > 50%:
    策略 A：尾部逐轮摘要
    - 保留全部轮次结构
    - 前 80% 轮原封不动
    - 只对最后 20% 轮的 assistant 文字回复做 LLM 摘要
    - user query / tool_calls / tool_results 原封不动
else:
    策略 B：前段合并 + 后段保留
    - 前 80% 轮 → 生成一条整体 summary（输入=每轮的 user query + assistant 文字回复）
    - 后 20% 轮 → 原封不动
```

### 3.4 摘要输入

无论哪种策略，给 LLM 做摘要的输入**只取**：
- user query（完整内容）
- assistant 的文字回复（content 中的 text 部分）

**不包含**：tool_calls、tool_results、thinking blocks。

理由：assistant 回复已经消化了 tool result 的信息，tool result 是 context 膨胀的主因，喂给摘要 LLM 反而更贵更长。

### 3.5 策略 A 详细（ratio > 50%）

最后 20% 轮占比大，但膨胀源可能是 user query（贴了大段代码/日志）也可能是 assistant 回复。**每轮按实际占比决定压缩谁**。

```
输入：10 轮对话，最后 2 轮占 60%

处理：
  head_turns = turns[:8]    # 前 80%，原封不动
  tail_turns = turns[8:]    # 后 20%，逐轮判断

  for turn in tail_turns:
    query_len = byte_length(turn.user_query)
    assistant_len = byte_length(turn.assistant_texts)

    if assistant_len > query_len:
      # assistant 回复是大头 → 摘要 assistant
      turn.assistant_texts = llm_summarize(
        "用户问题: " + turn.user_query[:500],
        turn.assistant_texts
      )
    else:
      # user query 是大头 → 摘要 user query
      turn.user_query = llm_summarize(
        "以下是一段用户输入，请精简保留关键信息：",
        turn.user_query
      )
    # tool_calls / tool_results 原封不动

  return head_turns + tail_turns
```

输出：10 轮都保留，前 8 轮原封不动，后 2 轮中每轮按 query/assistant 谁长就压缩谁。

### 3.6 策略 B 详细（ratio ≤ 50%）

```
输入：10 轮对话，最后 2 轮占 30%

处理：
  head_turns = turns[:8]    # 前 80%
  tail_turns = turns[8:]    # 后 20%

  # 对前 80% 轮生成一条 summary
  head_summary_input = [
    f"[Round {i}] User: {t.user_query}\nAssistant: {t.assistant_text}"
    for i, t in enumerate(head_turns)
  ]
  summary_msg = llm_summarize(head_summary_input)  # 一条 summary 消息

  # 组装结果
  return [summary_msg] + tail_turns_flattened
```

输出：1 条 summary + 最后 2 轮完整消息。

## 4. 数据结构

```python
@dataclass
class Turn:
    """一轮对话。"""
    index: int                           # 轮次序号（0-based）
    messages: list[dict[str, Any]]       # 原始消息列表
    user_query: str                      # user query 文本
    user_query_len: int                  # user query 字节长度
    assistant_texts: list[str]           # assistant 文字回复（可能多段）
    assistant_texts_len: int             # assistant 文字回复字节长度
    byte_length: int                     # 整轮原始字节长度（含 tool 层）
```

## 5. 消息序列完整性保障

以轮为单位操作，**天然保证**完整性：
- 不会出现 assistant(tool_calls) 缺少 tool result 的情况
- 不会出现 tool result 前面没有 assistant(tool_calls) 的情况
- 轮内部的消息顺序不变

策略 B 的 summary 消息是一条独立的 user 消息（带 `is_compaction_summary=True` 标记），不会破坏 API 消息格式。

## 6. 与现有系统的关系

### 6.1 保留的部分

- `CompactionConfig`（context_window, trigger_threshold 等） — 不变
- `apply_compaction` 入口（agent 调用方） — 接口不变
- `maybe_compact` / `force_compact`（agent.py）— 接口不变
- `last_prompt_tokens` 重置逻辑（刚修的 BUG 2）— 保留
- segment_boundary 写入 — 保留
- compaction_log — 保留

### 6.2 删除的部分

- `_CLASSIFY_SYSTEM` prompt — 不再逐条分类
- `_classify_messages` — 不再需要
- `_build_remaining_messages` — 不再需要
- `_repair_message_integrity` — 不再需要（以轮为单位天然完整）
- `_write_archive_entries` — archive 独立出去，不走 compaction
- fallback 路径中的 keep_recent_n 递减 — 策略 A/B 天然更简洁

### 6.3 archive 的去向

archive（知识沉淀到 skill/project 文件）与 compaction 解耦，建议：
- 放到独立的"归档"功能中，由 agent 主动触发或定期任务触发
- 不在 context 快满时做，避免增加延迟

## 7. 改动清单

| 文件 | 改动 |
|------|------|
| `src/core/compaction.py` | 重写 Compactor.compact()，新增 split_into_turns / 策略A / 策略B，删除 classify 相关代码 |
| `src/core/compaction.py` | 新增摘要 prompt（单轮摘要 / 多轮整体摘要） |
| `src/core/agent.py` | `maybe_compact` / `force_compact` 接口不变，保持 last_prompt_tokens 重置 |
| `src/core/session.py` | 不变 |
| `tests/test_compaction.py` | 重写测试用例，覆盖策略 A / B / 边界情况 |

## 8. 边界情况

| 场景 | 处理 |
|------|------|
| 总轮数 ≤ 3 轮 | 不压缩（太短，压缩无意义） |
| 只有 1 轮且超长 | 策略 A：对 assistant 回复做摘要 |
| assistant 只有 tool_calls 没有文字 | 该轮 assistant_texts 为空，摘要时跳过或标注为"执行了工具调用" |
| LLM 摘要调用失败 | fallback：保留原始消息，不压缩 |
| compaction 后仍然超阈值 | 由 apply_compaction 的紧急截断兜底（保留最近 2 轮） |
| summary 消息格式 | `{"role": "user", "content": "...", "is_compaction_summary": True}` |

## 9. 验收标准

1. 压缩后消息序列通过 API 校验（无 400 错误）
2. `last_prompt_tokens` 正确重置，context 占比显示准确
3. 策略 A：ratio > 50% 时，前 80% 轮原封不动，最后 20% 轮按每轮 query/assistant 谁长压缩谁
4. 策略 B：ratio ≤ 50% 时，前 80% 变成一条 summary，后 20% 原封不动
5. 摘要输入只包含 user query + assistant 文字回复，不包含 tool_calls/tool_results
6. compaction_log 正常记录
7. segment_boundary 正常写入
