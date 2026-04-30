# 心跳机制设计方案

## 目标

监控 Lampson 各工作进程的存活状态，进程异常退出时自动重新拉起，除非用户主动 kill。

## 概念

- **心跳**：进程定期向 watchdog 报告自己还活着
- **Watchdog**：监控心跳，超时则判定进程死亡并重新拉起
- **用户主动 kill**：用户显式停止任务或进程，标记为"不重拉"

## 设计

### 心跳上报

进程每 10 秒向 watchdog 发一次心跳：

```python
# 进程内部
def heartbeat():
    watchdog.report_alive(pid=os.getpid(), task_id=current_task_id)
```

### Watchdog 监控

```python
class Watchdog:
    def monitor(self):
        """
        循环检查心跳
        - 30 秒内无心跳 → 进程异常，标记为 crash
        - 用户未主动 kill → 重新拉起进程
        - 用户主动 kill → 不重拉
        """
```

### 用户主动 kill 的标记

- 用户停止任务 / kill 进程时，标记 `user_stopped = True`
- Watchdog 看到这个标记，不重拉
- 进程自己也需要在收到 kill 信号时，向 watchdog 报备"我是被用户停的"

### 进程与 Watchdog 通信

- 共享状态文件：`~/.lampson/heartbeat/<pid>.json`
  ```json
  {
    "pid": 12345,
    "task_id": "task-001",
    "last_heartbeat": "2026-04-29T22:00:00",
    "user_stopped": false
  }
  ```
- watchdog 每 10 秒扫描所有心跳文件，检查超时

### 异常处理

- 进程崩溃（SIGSEGV 等）→ 无法发送心跳 → watchdog 检测到超时 → 重拉
- 进程卡死（LLM 调用卡住）→ 还在发心跳（heartbeat 在独立线程）→ watchdog 认为活着 → 不重拉
- 进程 hang 在系统调用 → 心跳线程可能也 hang → 依赖外部超时兜底

## 与任务的关系

- 每个 Task 对应一个进程
- Task 取消时，进程先收到 kill 信号，标记 `user_stopped = True`，然后退出
- Task 完成时，进程正常退出，不需要重拉

## 不在本文讨论范围

- 具体编码实现细节
- 进程间通信的具体方式（共享文件 / Unix socket / ...）
- LLM 调用卡死的兜底超时机制
