# 新消息抢占中断机制 — 设计文档

> 日期：2026-04-28
> 状态：**已实现**

## 一、问题

飞书渠道中，同一 session 的上一条消息还在处理时，新消息会在另一个线程中并行进入 `handle_input`，导致 LLM messages 并发竞争。

## 二、设计

### 核心机制

- **Session 层消息队列**：`_input_queue` + `_processing_lock`，同一时刻只有一个消息在处理
- **Agent 中断标志**：`request_interrupt()` 设置标志，`check_interrupt()` 在工具调用间隙检查并抛出 `AgentInterrupted`
- **中断恢复**：被中断任务保存进度摘要 `_interrupted_summary`，新消息处理完后自动恢复

### 数据流

```
WebSocket 回调收到新消息
       │
       ▼
  _handle_message()
       │
       ├─ 未在处理 → 直接 handle_input()
       └─ 正在处理 → 入队 + request_interrupt()
                              │
                              ▼
                    _process_with_interrupt() 捕获 AgentInterrupted
                       ├─ 保存进度摘要
                       ├─ 取新消息 from queue
                       └─ 处理新消息 → 恢复原任务
```

### 关键文件

| 文件 | 改动 |
|------|------|
| `src/core/interrupt.py` | `AgentInterrupted` 异常类 |
| `src/core/agent.py` | `request_interrupt()` / `check_interrupt()` / `_build_interrupted_summary()` |
| `src/core/session.py` | `_input_queue` + `_processing_lock` + `_process_with_interrupt()` |
| `src/feishu/listener.py` | `_handle_message` 用线程池执行 handle_input，释放 WebSocket event loop |

### 安全性

- 工具调用**执行过程中**不会被中断（Python 同步函数不可打断），只在调用**之间**检查
- LLM 推理中的 token 消耗不可回收，属可接受代价

详细设计见代码中的 docstring。
