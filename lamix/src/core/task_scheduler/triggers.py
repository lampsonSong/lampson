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
    """任务配置。

    支持两种执行模式（二选一）：
    - prompt: 自然语言提示，触发时注入 agent session，由 LLM 用工具执行
    - func: Python 函数引用（如内置的自我审计）
    """
    task_id: str                              # 全局唯一 ID
    task_type: TaskType                       # 触发类型

    # 执行方式（二选一）
    func: Callable[..., Any] | None = None    # Python 函数
    prompt: str = ""                          # 自然语言 prompt（触发时注入 session）

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

    def __post_init__(self):
        if not self.func and not self.prompt:
            raise ValueError("TaskConfig 必须指定 func 或 prompt（二选一）")
