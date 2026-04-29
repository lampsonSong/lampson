# LLM 熔断机制改动清单

## 问题

当 LLM 服务大面积不可用（速率限制、超时）时：
1. `_chat_with_fallback` 每轮最多等 4x60s=240s（4个模型各60s超时）
2. fallback 全失败后 `_run_tool_loop` 返回错误，但外层 `while True` 会进入下一轮30轮循环（30x240s=2小时）
3. 进度卡片发送 400 时 `_send_progress_card` 返回 None，`_progress_worker` 不断重试发新卡片

## 改动清单

### 改动1: fallback 模型超时递减

**文件**: `src/core/agent.py`
**函数**: `_chat_with_fallback`（约第188-225行）

**改动**: 在尝试 fallback 模型时，通过 `extra_headers` 或直接在调用时传更短的 timeout。

因为 `BaseModelAdapter.chat()` 内部调用 `self.llm.client.chat.completions.create(**kwargs)`，而 OpenAI SDK 支持在 `create()` 调用时传 `timeout` 参数覆盖默认值。

具体做法：
1. 在 `_chat_with_fallback` 中，主模型用默认60s不变
2. 给 `BaseModelAdapter.chat()` 方法加一个可选参数 `timeout: float | None = None`
3. 在 `chat()` 方法的 `kwargs` 中，如果传了 timeout 就加入：`if timeout: kwargs["timeout"] = timeout`
4. `_chat_with_fallback` 调用 fallback 时按序递减超时：第1个fallback 30s，第2个 20s，第3个 15s

**改动后的 `_chat_with_fallback` 逻辑**:
```python
def _chat_with_fallback(self, tools=None):
    self.check_interrupt()

    # 1. 先试主模型（默认60s）
    try:
        return self.adapter.chat(self.llm.messages, tools=tools)
    except LLMContextTooLongError:
        raise
    except (LLMFatalError, LLMRateLimitError, LLMRetryableError) as e:
        if not self.fallback_models:
            raise
        logger.warning(f"主模型 {self.llm.model} 调用失败（{type(e).__name__}），尝试 fallback: {e}")
        self._on_model_switch(f"主模型 {self.llm.model} 失败，切换 fallback...")

    # 2. 依次试 fallback，超时递减：30s, 20s, 15s, 15s...
    for i, (fb_llm, fb_adapter) in enumerate(self.fallback_models):
        fb_timeout = max(15, 30 - i * 10)  # 30, 20, 15, 15, ...
        logger.warning(f"尝试 fallback: {fb_llm.model} (timeout={fb_timeout}s)")
        self._on_model_switch(f"尝试 {fb_llm.model}...")
        try:
            fb_llm.messages = list(self.llm.messages)
            result = fb_adapter.chat(fb_llm.messages, tools=tools, timeout=fb_timeout)
            self._on_model_switch(f"已切换到 {fb_llm.model}")
            return result
        except LLMContextTooLongError:
            raise
        except (LLMFatalError, LLMRateLimitError, LLMRetryableError) as e:
            logger.warning(f"fallback {fb_llm.model} 也失败（{type(e).__name__}）: {e}")
            continue

    raise LLMFatalError("所有模型（含 fallback）均调用失败")
```

**文件**: `src/core/adapters/base.py`
**函数**: `BaseModelAdapter.chat()`（约第115行）

**改动**: 加 `timeout` 可选参数，传入 `create()` 调用：
```python
def chat(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    timeout: float | None = None,  # 新增
) -> ChatCompletion:
    kwargs: dict[str, Any] = {
        "model": self.llm.model,
        "messages": messages,
    }
    if tools and self.supports_native_tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if timeout is not None:
        kwargs["timeout"] = timeout
    # ... 后面的 try/except 不变
```

---

### 改动2: tool_loop 连续 LLM 失败熔断

**文件**: `src/core/agent.py`
**函数**: `_run_tool_loop`（约第227行）

**改动**: 加 `_consecutive_llm_failures` 计数器，连续3次 LLM 调用失败后直接退出整个 while True 循环（不只是当前 for 循环），给用户返回错误信息。

在 `__init__` 中新增实例变量：
```python
self._consecutive_llm_failures: int = 0
```

在 `_run_tool_loop` 中，LLM 错误时累加计数器并判断：
```python
# 替换原来的:
#   except LLMError as e:
#       return f"[LLM 错误] {e}"

# 改为:
                except LLMError as e:
                    self._consecutive_llm_failures += 1
                    if self._consecutive_llm_failures >= 3:
                        logger.error(f"连续 {self._consecutive_llm_failures} 次 LLM 调用失败，熔断退出")
                        return f"[LLM 错误] 连续多次调用失败，LLM 服务暂时不可用。请稍后再试。\n最后一次错误: {e}"
                    return f"[LLM 错误] {e}"
```

LLM 调用成功时重置计数器（在 `response = self._chat_with_fallback(...)` 成功后加一行）：
```python
                try:
                    response = self._chat_with_fallback(tools=self._tools)
                    self._consecutive_llm_failures = 0  # 新增：成功则重置
                except AgentInterrupted:
```

同时在 `run()` 方法开头重置计数器：
```python
def run(self, user_input: str) -> str:
    self._consecutive_llm_failures = 0  # 新增
    self.llm.add_user_message(user_input)
```

---

### 改动3: 进度卡片发送熔断

**文件**: `src/feishu/listener.py`
**函数**: `_progress_worker`（约第303行，在 `_handle_feishu_message` 内部定义的闭包）

**改动**: 加 `_card_fail_count` 计数器，连续3次发送/更新卡片失败后停止尝试发卡片（保留日志），等最终回复时直接发文本消息。

在 `_progress_worker` 函数开头新增变量：
```python
_card_fail_count = 0
_MAX_CARD_FAILS = 3
```

在所有调用 `_send_progress_card` 和 `_update_progress_card` 的位置，加入熔断判断。

发送/更新卡片时检查：
```python
if _card_fail_count < _MAX_CARD_FAILS:
    if progress_msg_id is None:
        progress_msg_id = self._send_progress_card(chat_id, progress_lines)
        if progress_msg_id is None:
            _card_fail_count += 1
    else:
        try:
            self._update_progress_card(progress_msg_id, progress_lines)
        except Exception:
            _card_fail_count += 1
```

同样，结束时（`_progress_done.is_set()`）也加判断：
```python
if _progress_done.is_set():
    if progress_lines and _card_fail_count < _MAX_CARD_FAILS:
        if progress_msg_id is None:
            self._send_progress_card(chat_id, progress_lines, finished=True)
        else:
            self._update_progress_card(progress_msg_id, progress_lines, finished=True)
    return
```

---

## 验收标准

1. 所有模型超时/限流时，单次 `_chat_with_fallback` 最多等 60+30+20+15=125s（原来240s）
2. 连续3次所有模型都失败后，tool_loop 立即退出，用户收到明确的错误提示
3. 进度卡片发送连续3次400错误后不再重试，避免无限循环打日志
4. 正常使用（LLM可用时）行为不变
5. `python -m pytest` 测试通过（如果有的话）
