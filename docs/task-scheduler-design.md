# 任务调度器设计文档

## 1. 背景与目标

**现状问题：**
- `SelfAuditScheduler` 用 `time.sleep` 轮询硬编码，只支持单一定时任务
- 未来需要：一次性延迟任务（"5分钟后提醒"）、间隔任务（"每分钟查 agent 状态"）、Cron 任务（"每天4点审计"）
- 每个需求单独写线程 → 代码重复、无法统一管理

**目标：**
- 统一的任务调度接口，支持一次性/间隔/Cron 三种触发类型
- 任务持久化，daemon 重启后不丢失
- 任务可取消、可查询
- 结果回调通知（飞书消息、内部事件等）

## 2. 技术选型

| 选项 | 结论 |
|------|------|
| 自己写定时器 | 放弃，重复造轮子 |
| cron 命令行 | 放弃，无法程序化添加/取消任务 |
| APScheduler | 采用 |

**选择 APScheduler 原因：**
- 零业务逻辑，纯调度引擎，稳定可靠
- 支持 date（一次性）/ interval（间隔）/ cron（定时）三种 trigger
- 内置持久化（SQLAlchemy + SQLite）
- BackgroundScheduler 不阻塞主线程
- 安装量小（~10KB），无复杂依赖

## 3. 架构设计

### 3.1 核心模块

```
src/core/task_scheduler/
├── __init__.py          # 导出统一接口
├── scheduler.py          # APScheduler 封装 + 任务生命周期管理
├── triggers.py           # 触发器类型定义（date / interval / cron）
├── persistence.py         # SQLite 持久化配置
└── callbacks.py          # 回调机制（飞书通知、事件触发）
```

### 3.2 任务类型

```python
from src.core.task_scheduler import TaskType, TaskConfig, schedule

# 一次性延迟：5分钟后提醒
schedule(
    TaskConfig(
        task_id="remind_123",
        task_type=TaskType.DELAYED,      # 一次性，到期执行一次
        trigger_seconds=300,              # 300 秒后执行
        func=send_reminder,
        func_args={"msg": "记得开会"},
        on_done=notify_feishu,
    )
)

# 间隔任务：每分钟查一次状态
schedule(
    TaskConfig(
        task_id="check_agent_abc",
        task_type=TaskType.INTERVAL,
        interval_seconds=60,
        func=check_agent_status,
        func_args={"agent_id": "abc"},
    )
)

# Cron 任务：每天凌晨4点
schedule(
    TaskConfig(
        task_id="daily_audit",
        task_type=TaskType.CRON,
        cron_hour=4,
        cron_minute=0,
        func=run_self_audit,
    )
)
```

### 3.3 任务生命周期

```
注册任务
    ↓
APScheduler 接收，存入 SQLite
    ↓
等待触发
    ↓
执行 func
    ↓
DELAYED 类型：任务结束，标记完成
INTERVAL/CRON 类型：等待下一次触发
    ↓
on_done / on_error 回调（如有）
```

### 3.4 持久化策略

- 使用 APScheduler 内置 SQLAlchemy + SQLite
- 数据库路径：`~/.lampson/task_scheduler.db`
- daemon 重启后 scheduler.start() 自动从数据库恢复所有 INTERVAL/CRON 任务
- DELAYED 任务：执行完毕后自动从数据库删除

## 4. API 设计

### 4.1 任务配置

```python
@dataclass
class TaskConfig:
    task_id: str              # 全局唯一 ID，用于取消和去重
    task_type: TaskType       # DELAYED | INTERVAL | CRON
    description: str = ""     # 任务描述（用于日志和飞书通知）

    # DELAYED
    trigger_seconds: int = 0   # 延迟秒数

    # INTERVAL
    interval_seconds: int = 0

    # CRON
    cron_hour: int | None = None
    cron_minute: int | None = None

    # 执行体
    func: Callable[..., Any]
    func_args: dict | None = None

    # 回调
    on_done: Callable[[Any], None] | None = None   # 执行成功后调用
    on_error: Callable[[Exception], None] | None = None
```

### 4.2 调度器接口

```python
class TaskScheduler:
    def schedule(self, config: TaskConfig) -> str:
        """注册任务，返回 task_id。"""

    def cancel(self, task_id: str) -> bool:
        """取消任务，返回是否成功。"""

    def list(self) -> list[dict]:
        """列出所有任务（含状态）。"""

    def start(self) -> None:
        """启动调度器（daemon 启动时调用）。"""

    def stop(self) -> None:
        """停止调度器（daemon 退出时调用）。"""
```

## 5. 与现有模块的关系

### 5.1 替换 SelfAuditScheduler

当前：
```python
SelfAuditScheduler(hour=4, minute=0).start()
```

重构后：
```python
from src.core.task_scheduler import schedule, TaskType

schedule(TaskConfig(
    task_id="daily_self_audit",
    task_type=TaskType.CRON,
    cron_hour=4,
    cron_minute=0,
    func=run_self_audit,
    func_args={},
))
```

### 5.2 延迟任务示例：5分钟后提醒

```python
def on_reminder_done(result):
    # 飞书通知
    ...

schedule(TaskConfig(
    task_id=f"remind_{message_id}",
    task_type=TaskType.DELAYED,
    trigger_seconds=300,
    func=do_remind,
    func_args={"user_id": user_id, "msg": msg},
    on_done=on_reminder_done,
))
```

### 5.3 间隔任务示例：监控 Hermes 任务状态

```python
schedule(TaskConfig(
    task_id=f"monitor_{hermes_task_id}",
    task_type=TaskType.INTERVAL,
    interval_seconds=30,
    func=check_hermes_task,
    func_args={"task_id": hermes_task_id},
))
```

## 6. 实现计划

**Phase 1：基础调度器（独立可测试）**
- `src/core/task_scheduler/triggers.py` — 触发器类型定义
- `src/core/task_scheduler/scheduler.py` — APScheduler 封装
- `src/core/task_scheduler/persistence.py` — SQLite 配置
- `tests/test_task_scheduler.py` — 核心测试

**Phase 2：回调机制**
- `src/core/task_scheduler/callbacks.py` — 回调基类
- 飞书回调实现

**Phase 3：集成**
- daemon.py 集成 TaskScheduler（替换 SelfAuditScheduler）
- session.py 提供 `/task list` 和 `/task cancel` 命令
- 清理废弃的 `src/core/self_audit.py` 里的 SelfAuditScheduler

**Phase 4：延迟任务支持**
- session.py 中调用 schedule() 处理 "X分钟后提醒" 场景
- 与 Hermes 任务监控对接

## 7. 风险与边界

- **APScheduler 依赖**：需在 requirements.txt 添加 `APScheduler`，首次安装后即可移除硬编码的轮询实现
- **SQLite 并发**：APScheduler 默认单线程调度，不存在并发写入问题
- **任务超时**：func 执行时间过长不中断，后续可加 timeout 参数
- **daemon 崩溃恢复**：scheduler 重启后自动从 DB 恢复任务，不丢 INTERVAL/CRON 任务
