# Compaction 进度显示 + Timeout 修复

## 问题

1. `/compaction` 命令执行时用户完全看不到进度，只看到 thinking 表情然后干等
2. 临时 LLM 客户端默认 60s timeout，compaction 上下文很大时超时失败

## 改动清单

### 1. `src/core/compaction.py` — Compactor.compact() 增加进度回调

**改动**：`compact()` 方法增加 `progress_callback: Callable[[str], None] | None = None` 参数

在各步骤之间调用 callback 发进度消息：

```
[1/6] 正在分析对话内容...
[2/6] 正在写入会话边界...
[3/6] 正在读取已有归档文件...
[4/6] 正在写入归档文件...
[5/6] 正在写入压缩日志...
[6/6] 正在构建剩余上下文...
```

如果触发了阶段二摘要，额外显示：`[摘要] 正在生成对话摘要...`

最后显示结果：`[完成] 归档 N 条内容，保留 M 条消息`

**具体位置**：
- `Compactor.compact()` ~L139: 加 `progress_callback` 参数
- Step 1 (L165 `_classify_messages` 前): `[1/6] 正在分析对话内容...`
- Step 2 (L199 `_write_segment_boundary` 前): `[2/6] 正在写入会话边界...`
- Step 3-4 (L205 归档写入前): `[3/6] 正在读取已有归档文件...` + `[4/6] 正在写入归档文件...`
- Step 5 (L218 `_log_compaction` 前): `[5/6] 正在写入压缩日志...`
- Step 6 (L230 `_build_remaining_messages` 前): `[6/6] 正在构建剩余上下文...`
- 阶段二 (L245 `_summarize_keep_messages` 前): `[摘要] 正在生成对话摘要...`

### 2. `src/core/compaction.py` — `_make_temp_client` timeout 600s

**改动**：`_make_temp_client()` 创建 `LLMClient` 时传入 `timeout=600.0`

**位置**：~L348

```python
return LLMClient(
    api_key=...,
    base_url=...,
    model=...,
    timeout=600.0,  # 新增
)
```

### 3. `src/core/compaction.py` — `apply_compaction` 透传 progress_callback

**改动**：`apply_compaction()` 增加 `progress_callback` 参数，透传给 `Compactor.compact()`

**位置**：~L668

### 4. `src/core/agent.py` — `force_compact` / `maybe_compact` 透传 progress_callback

**改动**：
- `force_compact()` ~L511: 增加 `progress_callback` 参数
- `maybe_compact()` ~L476: 增加 `progress_callback` 参数
- 透传给 `apply_compaction()`

### 5. `src/core/session.py` — `_handle_compaction` 传入进度回调

**改动**：`_handle_compaction()` ~L754 传入 `self.partial_sender` 作为进度回调

```python
cr = self.agent.force_compact(
    session_store=session_store,
    session_id=self.session_id or "",
    progress_callback=self.partial_sender,  # 新增
)
```

同样在自动 compaction 触发点（~L391, ~L494）也传入 `partial_sender`。

### 6. `src/core/llm.py` — LLMClient 支持 timeout 参数（如果还不支持）

检查 `LLMClient.__init__` 是否接受 timeout 参数并传给 httpx client。如果不支持则加上。

## 验收标准

1. `/compaction` 命令后用户看到 `[1/6]...` 到 `[6/6]...` 的进度消息
2. 不再出现 "LLM 请求超时" 错误
3. 压缩完成后显示归档数量
4. 压缩失败时显示失败原因，不卡死
