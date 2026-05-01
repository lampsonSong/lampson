"""触发器类型定义：任务配置数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class TaskType(Enum):
    """任务触发类型。"""
    DELAYED = "delayed"      # 一次性延迟
    INTERVAL = "interval"    # 固定间隔
    CRON = "cron"            # Cron 表达式


@dataclass
class TaskConfig:
    """任务配置。"""
    task_id: str                              # 全局唯一 ID
    task_type: TaskType                       # 触发类型
    func: Callable[..., Any]                  # 执行函数
    func_args: dict[str, Any] = field(default_factory=dict)
    description: str = ""                     # 任务描述（日志/通知用）

    # DELAYED 参数
    trigger_seconds: int = 0

    # INTERVAL 参数
    interval_seconds: int = 0

    # CRON 参数
    cron_hour: int | None = None
    cron_minute: int | None = None
    cron_day_of_week: str | None = None       # e.g. "mon-fri"

    # 回调
    on_done: Callable[[Any], None] | None = None
    on_error: Callable[[Exception], None] | None = None
