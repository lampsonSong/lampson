# 新消息抢占中断机制 — 设计文档

> 日期：2026-04-28（更新：2026-05-01）
> 状态：**已实现**

## 一、问题

飞书渠道中，同一 session 的上一条消息还在处理时，新消息会在另一个线程中并行进入 `handle_input`，导致 LLM messages 并发竞争。

## 二、设计

### 核心机制

- **Session 层消息队列**：`_input_queue` + `_processing_lock`，同一时刻只有一个消息在处理
- **Agent 中断标志**：`request_interrupt()` 设置标志，`check_interrupt()` 在工具调用间隙检查并抛出 `AgentInterrupted`
- **中断合并重新规划**：被中断任务不再独立恢复，而是将 A 任务进度与 B 新消息合并为一次请求，让 LLM 重新规划并执行

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
                       ├─ 补全未闭合的 tool_call 序列（_sanitize_tool_messages）
                       ├─ 取新消息 from queue（含连续多条场景）
                       └─ 合并 A 进度 + B 新消息 → 重新规划
```

### 关键文件

| 文件 | 改动 |
|------|------|
| `src/core/interrupt.py` | `AgentInterrupted` 异常类 |
| `src/core/agent.py` | `request_interrupt()` / `check_interrupt()` / `_build_interrupted_summary()` / `_sanitize_tool_messages()` |
| `src/core/session.py` | `_input_queue` + `_processing_lock` + `_process_with_interrupt()` |
| `src/feishu/listener.py` | `_handle_message` 用线程池执行 handle_input，释放 WebSocket event loop |

### 中断合并策略

当任务 A 被新消息 B 中断时：

1. 调用 `_sanitize_tool_messages()` 补全未闭合的 tool_call 序列，确保 messages 状态一致
2. 从队列取出新消息 B（如果队列中还有更多消息也一并取出）
3. 构建合并输入：A 的进度摘要 + B 的新消息内容 + 重新规划指令
4. 以合并输入作为新一轮 `agent.run()` 的输入，让 LLM 自行判断是合并处理还是优先处理新消息

### 安全性

- 工具调用**执行过程中**不会被中断（Python 同步函数不可打断），只在调用**之间**检查
- LLM 推理中的 token 消耗不可回收，属可接受代价
- 中断后通过 `_sanitize_tool_messages()` 确保 tool_call / tool_result 消息序列完整，避免 API 报错

详细设计见代码中的 docstring。
