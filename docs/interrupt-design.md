# 新消息抢占中断机制 — 设计文档

> 日期：2026-04-28
> 状态：设计中

## 一、背景与问题

当前 Lampson 飞书监听器（`listener.py`）在 WebSocket 回调线程中同步调用 `session.handle_input(text)`。如果同一 session 的上一条消息还在处理（LLM 推理中 / 工具调用中），新消息会在另一个线程中并行进入 `handle_input`，导致：

1. 两个 LLM 调用并发竞争同一个 `agent.llm.messages`，无锁保护
2. 两个请求的回复交叉发送
3. 对话历史可能因并发写入而乱序

**已有的基础设施**：代码中已实现中断的基础组件但未被使用：

- `AgentInterrupted(Exception)` — 中断异常（不继承 Exception，需显式 catch）
- `Agent.request_interrupt()` — 设置中断标志位
- `Agent.check_interrupt()` — 在 `_run_tool_loop` 的多个检查点检测标志，抛出中断异常
- `Agent._build_interrupted_summary()` — 从当前 messages 构建中断进度摘要

## 二、需求

当正在处理一个任务，但同一 session 有新消息来了：

1. **新消息入队**：将新消息放入 per-session 的待处理队列
2. **立即中断当前任务**：停止当前的工具调用循环和 LLM 请求
3. **合并状态处理新消息**：将当前进度（已完成的 tool 调用、原始计划、历史对话）和新 query 合并，作为一次正常对话交给 LLM
4. **恢复被中断的任务**：新消息处理完毕后，从队列继续处理被中断的任务

## 三、设计

### 3.1 整体流程

```
WebSocket 回调线程收到新消息
       │
       ▼
  _handle_message()
       │
       ├─ 消息去重 / 过期检查（不变）
       │
       ▼
  session.handle_input(text)  ←─── 改为异步接口或内部排队
       │
       ├─ 检查当前是否正在处理？
       │   ├─ 否 → 直接处理（和现在一样）
       │   └─ 是 → 中断当前 + 入队
       │
       ▼
  Agent.run(user_input)
       │
       ├─ LLM 调用 / 工具调用循环
       │   └─ 在每个检查点 check_interrupt()
       │       ├─ 未中断 → 继续
       │       └─ 已中断 → 抛出 AgentInterrupted
       │
       ▼
  handle_input 捕获 AgentInterrupted
       ├─ 保存进度摘要到 _interrupted_summary
       ├─ 从队列取下一条消息
       ├─ 将摘要 + 新 query 合并发给 LLM
       └─ 完成后恢复处理被中断的任务
```

### 3.2 核心改动

#### 3.2.1 Session 层：添加消息队列和处理锁

**文件**：`src/core/session.py`

```python
class Session:
    def __init__(self, ...):
        # 新增：消息队列和处理状态
        self._input_queue: queue.Queue[str] = queue.Queue()  # 待处理消息队列
        self._processing: bool = False                         # 是否正在处理
        self._processing_lock: threading.Lock = threading.Lock()
        self._pending_task_summary: str = ""                  # 被中断任务的摘要
        self._pending_task_messages_snapshot: list[dict] = [] # 被中断时的 messages 快照
```

**改造 `handle_input`**：

```python
def handle_input(self, user_input: str) -> HandleResult:
    if self._processing:
        # 当前正在处理 → 入队 + 请求中断
        self._input_queue.put(user_input)
        self.agent.request_interrupt()
        return HandleResult(reply="", compaction_msg="")  # 不回复，新消息会在循环中处理

    # 正常处理（加锁保证串行）
    with self._processing_lock:
        self._processing = True
        try:
            return self._process_with_interrupt(user_input)
        finally:
            self._processing = False

def _process_with_interrupt(self, user_input: str) -> HandleResult:
    """处理消息，支持被新消息中断。"""
    while True:
        try:
            reply = self.agent.run(user_input)
            # 成功完成当前消息的处理
            # ... 正常的 JSONL 写入、压缩等逻辑 ...

            # 检查队列中是否有被中断的待恢复任务
            if self._pending_task_summary:
                user_input = self._resume_interrupted_task()
                continue  # 继续循环处理恢复的任务

            # 检查队列中是否有新消息等待处理
            if not self._input_queue.empty():
                user_input = self._input_queue.get_nowait()
                continue  # 继续循环处理新消息

            return HandleResult(reply=reply, ...)

        except AgentInterrupted as e:
            # 当前任务被新消息中断
            # 保存当前 messages 快照和摘要
            self._pending_task_summary = e.progress_summary
            self._pending_task_messages_snapshot = list(self.agent.llm.messages)

            # 从队列取新消息
            if not self._input_queue.empty():
                user_input = self._input_queue.get_nowait()
            else:
                # 没有新消息（竞态情况：中断信号到了但队列空了）
                return HandleResult(reply="[任务被中断，但无新消息待处理]", ...)

            # 将中断摘要注入，让 LLM 知道刚才在做什么
            if self._pending_task_summary:
                user_input = f"{self._pending_task_summary}\n\n---\n\n**新消息**：{user_input}"
```

#### 3.2.2 Agent 层：已有机制基本够用，微调

**文件**：`src/core/agent.py`

已有机制基本完善，需要调整的点：

1. **`run()` 方法**：不再在内部 catch `AgentInterrupted`，直接上抛给 Session 层处理

```python
def run(self, user_input: str) -> str:
    self.llm.add_user_message(user_input)
    result = self._run_tool_loop()  # AgentInterrupted 会从这里冒出来
    # ... reflection ...
    return result
```

2. **`check_interrupt()` 的调用位置**（已在代码中）：
   - LLM 调用前
   - 收到 LLM 响应后、解析 tool_calls 前
   - 每个工具调用完成后
   - 达到 max_tool_rounds 后

3. **新增**：`clear_interrupt_state()` 在每条消息处理前调用，确保状态干净

#### 3.2.3 Listener 层：_handle_message 需要支持阻塞等待

**文件**：`src/feishu/listener.py`

当前的 `_handle_message` 在 WebSocket 回调线程中同步调用 `session.handle_input()`。改造后：

- 如果 session 没有在处理，`handle_input` 正常返回（和现在一样）
- 如果 session 正在处理，`handle_input` 立即返回空结果（消息已入队）
- 新消息会在原处理线程的 `_process_with_interrupt` 循环中被处理并回复

**关键问题**：新消息的回复需要在正确的线程中发送。当前 listener 在 `_handle_message` 中设置 `session.partial_sender`，但新消息可能在另一个线程中处理。

**解决方案**：让 listener 为每个 session 维护一个持久的 `partial_sender`，而不是每次 handle_message 临时设置。

```python
# 在 session 层维护回调
class Session:
    def set_reply_callback(self, callback: Callable[[str], None]) -> None:
        """由 listener 在首次获取 session 时设置，后续复用。"""
        self._reply_callback = callback
```

或者更简单：在 `_process_with_interrupt` 中，通过 listener 的 `send_reply` 发送回复，而不是通过 `partial_sender`。

#### 3.2.4 被中断任务的恢复

当新消息处理完后，需要恢复被中断的任务：

```python
def _resume_interrupted_task(self) -> str:
    """构建恢复提示，让 LLM 继续被中断的任务。"""
    summary = self._pending_task_summary
    self._pending_task_summary = ""  # 清除，防止重复恢复

    resume_prompt = f"""{summary}

--- 任务被新消息中断，现在继续 ---

请根据上述进度，继续完成原来的任务。如果任务已经完成或不需要继续，请告知用户。"""

    # 恢复 messages 到中断前的状态
    # 注意：如果中断后处理了新消息，messages 已经包含了新消息的上下文
    # 不需要手动恢复 messages，LLM 有足够的上下文继续
    return resume_prompt
```

### 3.3 消息回复的线程模型

```
线程 A（WebSocket 回调）：消息 1 → handle_input() → 进入 _process_with_interrupt() 循环
                                                              │
                                                              ├─ agent.run(消息1) ←── 正常执行
                                                              │       │
                                                              │       ▼ 回复消息1 ✓
                                                              │
                                                              ├─ agent.run(消息1) ←── 被中断
                                                              │       │ AgentInterrupted!
                                                              │       ├─ 保存进度摘要
                                                              │       ├─ 取消息2 from queue
                                                              │       └─ agent.run(消息2) → 回复消息2 ✓
                                                              │
                                                              ├─ _resume_interrupted_task() → 恢复消息1
                                                              │       └─ agent.run(恢复提示) → 回复 ✓
                                                              │
                                                              └─ 队列空 → 返回

线程 B（WebSocket 回调）：消息 2 → handle_input() → 入队 + request_interrupt() → 立即返回
                                                                    │
                                                                    └─ 消息2 的回复由线程A的循环发送
```

**好处**：同一条消息的处理始终在同一线程中完成，不存在并发写 `llm.messages` 的问题。

### 3.4 安全性考量

1. **工具调用无法中途取消**：Python 中同步函数调用无法被中断。`check_interrupt()` 只在工具调用**之间**执行，不会在工具调用**执行过程中**中断。这意味着：
   - 文件写入不会被写到一半
   - Shell 命令会执行完毕
   - 这是安全的——我们只中断 LLM 的推理决策链，不中断具体的副作用操作

2. **LLM 请求的浪费**：如果中断发生时 LLM 正在推理，已花的 token 无法回收。这是可接受的代价。

3. **progress_worker 线程**：当前 `_handle_message` 为每条消息创建一个 `_progress_worker` 线程来更新进度卡片。如果处理被中断，需要：
   - 设置 `_progress_done` 事件，让 worker 线程退出
   - worker 线程退出后，为后续消息创建新的 worker

4. **reaction 表情**：每条消息有独立的 `reaction_id`。被中断的消息的 reaction 不需要撤销（任务后续会恢复）。

### 3.5 边界情况

| 场景 | 处理方式 |
|------|----------|
| 两条新消息几乎同时到达 | 第一条入队，第二条也入队，request_interrupt 只需触发一次 |
| 中断时队列为空 | 不会发生（只有新消息入队后才触发中断） |
| 恢复任务时又有新消息 | 恢复任务也会被中断，形成链式抢占 |
| /new 或 /exit 命令 | 命令处理不受中断机制影响（在 handle_input 中优先判断） |
| 压缩发生时被中断 | 压缩在 run() 返回后执行，不会被中断 |
| CLI 渠道 | CLI 是单线程 REPL，不存在并发问题，不需要队列机制 |

### 3.6 实现步骤

1. **Session 层改造**：添加队列、处理锁、`_process_with_interrupt` 逻辑
2. **Agent 层微调**：`run()` 确保不吞 `AgentInterrupted`
3. **Listener 层适配**：
   - `_handle_message` 处理入队场景的立即返回
   - 持久化 reply_callback 而非每次临时设置
   - progress_worker 的生命周期管理
4. **恢复逻辑**：`_resume_interrupted_task()` 实现
5. **测试**：模拟并发消息场景

## 四、不需要改动的部分

- `AgentInterrupted` 异常类 — 已完善
- `request_interrupt()` / `check_interrupt()` — 已完善
- `_build_interrupted_summary()` — 已完善
- `_run_tool_loop()` 中的检查点 — 已完善
- `MessageDeduplicator` — 无需改动
- SessionManager — 无需改动
