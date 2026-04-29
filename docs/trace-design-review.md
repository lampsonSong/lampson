# Trace Design Review

审阅文件: docs/trace-design.md
审阅时间: 2026-04-29

---

## 整体评价

设计清晰，JSONL + hash 分离大体的思路没问题。但有几个地方值得商榷或需要补充。

---

## 1. assistant 和 llm_call 职责重叠

`assistant` 行已经有 model、input_tokens、output_tokens、stop_reason，而 `llm_call` 也有几乎相同的字段。一次 LLM 调用会同时写两行吗？

- 如果是，那数据冗余
- 如果不是，那两者的边界在哪需要说清楚

建议：要么合并成一种行类型，要么明确说明 `assistant` 是给对话摘要用的（只记录最终回复）、`llm_call` 是给调试/计费用（记录每次调用含重试）。如果是一次调用两行，写清楚理由。

---

## 2. system_prompt 的存储粒度

文档说"每次 LLM 调用"都写 system_prompt，用 prompt_hash 去重。但没说清楚：

- 去重是"相同 hash 就不写这行"还是"写了行但省略 content 字段"？
- 如果是后者，那每行都还是有 I/O，去重省的是磁盘空间而不是行数

建议：明确 `system_prompt` 行的 content 字段，hash 已存在时省略还是写 null。

---

## 3. tool_call 和 tool_result 的关联

通过 `id` 字段关联，没问题。但文档没有说明异常路径：

- 工具调用失败时，`tool_result` 的 error 字段是字符串还是结构化对象？
- 如果工具超时被 kill，是写 tool_result(error=...) 还是干脆不写？

建议加一行说明异常路径下的行为。

---

## 4. tool_bodies "只写不检查" 的隐患

设计说直接写入不检查是否存在，理由是"相同内容覆盖无影响"。这有一个前提假设：hash 碰撞概率可忽略。

- SHA256 没问题，但如果未来有人换 hash 算法，这个假设可能不成立。建议文档里写死用 SHA256，不要留扩展口。
- 另外，如果 tool result 内容完全相同但语义不同（比如两次读同一个文件，文件没变），hash 一样是正确行为吗？看场景——如果只是看结果内容，没问题；如果需要区分"这是第几次调用的结果"，那 hash 相同会丢失时序信息。JSONL 行里的 result_ref 本身已经和 tool_call 通过 id 关联了，所以实际上不会丢信息，但值得在文档里提一句。

---

## 5. 缺少 rotation/清理策略

sessions 按日期分目录，但 tool_bodies 是全局共享的、只增不减。长期运行后 tool_bodies 会膨胀。文档应该说明：

- 是否有 GC 策略（比如引用计数，无 session 引用的 body 可清理）
- 还是说先不做，后续再补

---

## 6. 缺少索引/查询说明

文档提到"SQLite FTS5 索引不受影响（新增行不入索引）"，但没说：

- 未来这些新行类型要不要入索引？
- 如果要，打算怎么索引（比如按 tool_call name 搜、按 llm_error 搜）？
- 如果不要，那查询 trace 的主要方式是什么？直接扫 JSONL？

---

## 7. 小问题

- `tool_call` 的 arguments 字段示例是 JSON 对象，但 JSONL 行里直接嵌对象没问题，需确认序列化时不会出问题（比如 arguments 里包含换行符）
- `segment_boundary` 的 archive 字段含义不明，建议补一句
- 时间戳用毫秒级 epoch，没问题，但建议统一说明时区处理（或者明确不需要）

---

## 总结

核心架构合理，主要需要补充的是：

1. assistant vs llm_call 边界
2. 异常路径行为
3. tool_bodies 清理策略
4. 查询方案

建议先补齐这四点再动手写代码。
