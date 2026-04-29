# 中断抢占功能修复

## 问题

飞书渠道的中断抢占功能不生效。用户在 agent 处理任务 A 时发送新消息 B，agent 不会停止 A，也不会处理 B。

## 根因分析

### 调用链路

```
lark_oapi WS SDK (asyncio event loop)
  → _handle_message()   ← asyncio task
    → session.handle_input(text)   ← 同步阻塞，会跑整个 tool loop（可能几十秒）
```

### 根因

lark_oapi WS SDK 使用 `asyncio.create_task()` 为每条消息创建独立 task，
但 `_handle_message()` 内部调用 `session.handle_input()` 是**同步阻塞**的（整个 tool loop 在同步线程中执行）。

**同步阻塞会冻结整个 asyncio event loop**，导致：
1. 第二条消息的 task 被创建但无法被调度执行
2. 即使 `request_interrupt()` 被调用，也要等第一条消息的 `handle_input` 返回后 event loop 才能调度第二条消息
3. 中断检查点 `check_interrupt()` 只在 tool loop 内的几个位置触发，而 interrupt flag 是由第二条消息的 `handle_input` 调用设置的——但这条消息根本没有机会执行

### 时序图

```
时间 →
WS Task 1: [handle_input("任务A") ← 同步阻塞 30s+ ──────────────────────── 返回]
WS Task 2:                                                    [被创建但无法运行 → 直到 Task 1 返回后才执行]
```

## 修复方案

**核心改动：将 `session.handle_input()` 放入独立线程，不阻塞 asyncio event loop。**

### 改动 1: listener.py — `_handle_message` 用线程池执行 handle_input

```python
# 改前（阻塞 event loop）:
result = session.handle_input(text)

# 改后（线程池，不阻塞 event loop）:
import concurrent.futures
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

future = _executor.submit(session.handle_input, text)
# future.result() 会在后台线程中等待，不阻塞 event loop
```

但这里有个问题：`_handle_message` 本身是 async 的，需要后续发送回复。
方案：用 `asyncio.get_event_loop().run_in_executor()` 包装同步调用。

### 改动 2: 确保线程安全

`handle_input` 内部的 `_process_with_interrupt` 已经有线程安全设计：
- `_processing` 标志 + `_processing_lock` 互斥锁
- `_input_queue` 线程安全队列
- `request_interrupt()` 通过标志位通知

放入线程池后，多个 `_handle_message` task 可以并发进入 `handle_input`，
但只有一个能获得锁进入 `_process_with_interrupt`，其他的会入队。

### 改动 3: listener.py — 线程池中处理回复发送

回复发送（`_send_reply` 等）使用 lark SDK 的同步 API，需要确保在正确上下文中调用。

## 文件变更

1. `src/feishu/listener.py`
   - `_handle_message()`: 用 `loop.run_in_executor()` 将 `session.handle_input()` 放入线程池
   - 新增 `_process_in_thread()`: 封装同步调用 + 回复发送逻辑
