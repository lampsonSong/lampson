"""定时任务管理工具：通过 LLM 动态注册、取消、查看定时任务。

支持的触发类型：
- interval: 固定间隔（秒）
- cron: Cron 表达式（hour/minute/day_of_week）
- delayed: 一次性延迟（秒）

任务函数来源：
1. learned_module: 引用 ~/.lampson/learned_modules/ 下的模块，调用指定函数
2. shell: 执行 shell 命令（简单任务）

示例：
  schedule(task_type="interval", interval_seconds=1800, module="training_reporter", func_name="run")
  schedule(task_type="cron", cron_hour=4, cron_minute=0, module="self_audit", func_name="run")
  cancel(task_id="training_reporter")
  list_tasks()
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

logger = logging.getLogger(__name__)

SCHEDULE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_schedule",
        "description": (
            "动态注册定时任务。支持 interval（固定间隔）、cron（定时）、delayed（一次性延迟）。"
            "任务函数来源：learned_modules 下的模块（指定 module + func_name）。"
            "注册后立即生效，无需重启。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["schedule", "cancel", "list"],
                    "description": "操作类型：schedule 注册任务、cancel 取消任务、list 查看所有任务",
                },
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（schedule 时必填，cancel 时必填）",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["interval", "cron", "delayed"],
                    "description": "触发类型（schedule 时必填）",
                },
                "interval_seconds": {
                    "type": "integer",
                    "description": "interval 模式的间隔秒数",
                },
                "cron_hour": {
                    "type": "integer",
                    "description": "cron 模式的小时（0-23）",
                },
                "cron_minute": {
                    "type": "integer",
                    "description": "cron 模式的分钟（0-59）",
                },
                "cron_day_of_week": {
                    "type": "string",
                    "description": "cron 模式的星期（如 'mon-fri'）",
                },
                "delay_seconds": {
                    "type": "integer",
                    "description": "delayed 模式的延迟秒数",
                },
                "module": {
                    "type": "string",
                    "description": "learned_modules 下的模块名（如 'training_reporter'）",
                },
                "func_name": {
                    "type": "string",
                    "description": "模块中要调用的函数名（默认 'run'）",
                },
                "func_args": {
                    "type": "object",
                    "description": "传给函数的额外参数（可选）",
                },
                "description": {
                    "type": "string",
                    "description": "任务描述（显示在 list 中）",
                },
            },
            "required": ["action"],
        },
    },
}

LIST_TASKS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_list",
        "description": "查看当前所有已注册的定时任务，包括下次触发时间。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

CANCEL_TASK_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_cancel",
        "description": "取消一个已注册的定时任务。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要取消的任务 ID",
                },
            },
            "required": ["task_id"],
        },
    },
}


def _resolve_runner(module_name: str, func_name: str) -> tuple[Any, str]:
    """解析 learned_module 中的函数，返回 (func, error_msg)。"""
    from src.tools.learned_modules import get_module

    mod = get_module(module_name)
    if mod is None:
        return None, f"模块 '{module_name}' 未加载。请确认 ~/.lampson/learned_modules/{module_name}.py 存在且已注册。"

    func = getattr(mod, func_name, None)
    if func is None or not callable(func):
        return None, f"模块 '{module_name}' 中没有可调用函数 '{func_name}'"

    return func, ""


def run_schedule(params: dict[str, Any]) -> str:
    """注册定时任务。"""
    from src.core.task_scheduler import schedule, TaskConfig, TaskType

    task_id = params.get("task_id", "").strip()
    task_type_str = params.get("task_type", "").strip()
    module_name = params.get("module", "").strip()
    func_name = params.get("func_name", "run").strip()
    func_args = params.get("func_args", {})
    description = params.get("description", "")

    if not task_id:
        return "[错误] task_id 不能为空"
    if not task_type_str:
        return "[错误] task_type 不能为空（interval/cron/delayed）"
    if not module_name:
        return "[错误] module 不能为空，需要指定 learned_module 名称"

    # 解析任务类型
    try:
        task_type = TaskType(task_type_str)
    except ValueError:
        return f"[错误] 不支持的 task_type: {task_type_str}"

    # 解析函数
    func, err = _resolve_runner(module_name, func_name)
    if err:
        return f"[错误] {err}"

    # 构建 config
    config_kwargs: dict[str, Any] = {
        "task_id": task_id,
        "task_type": task_type,
        "func": func,
        "func_args": func_args,
        "description": description or f"{module_name}.{func_name}",
    }

    if task_type == TaskType.INTERVAL:
        interval = params.get("interval_seconds")
        if not interval or interval <= 0:
            return "[错误] interval 模式需要 interval_seconds > 0"
        config_kwargs["interval_seconds"] = interval

    elif task_type == TaskType.CRON:
        cron_hour = params.get("cron_hour")
        cron_minute = params.get("cron_minute")
        if cron_hour is None and cron_minute is None:
            return "[错误] cron 模式需要至少指定 cron_hour 或 cron_minute"
        if cron_hour is not None:
            config_kwargs["cron_hour"] = cron_hour
        if cron_minute is not None:
            config_kwargs["cron_minute"] = cron_minute
        if params.get("cron_day_of_week"):
            config_kwargs["cron_day_of_week"] = params["cron_day_of_week"]

    elif task_type == TaskType.DELAYED:
        delay = params.get("delay_seconds")
        if not delay or delay <= 0:
            return "[错误] delayed 模式需要 delay_seconds > 0"
        config_kwargs["trigger_seconds"] = delay

    config = TaskConfig(**config_kwargs)

    try:
        schedule(config)
        return f"✅ 定时任务已注册: {task_id} ({task_type_str})\n下次触发: 查看 task_list"
    except Exception as e:
        return f"[错误] 注册失败: {e}\n{traceback.format_exc()}"


def run_cancel(params: dict[str, Any]) -> str:
    """取消定时任务。"""
    from src.core.task_scheduler import cancel

    task_id = params.get("task_id", "").strip()
    if not task_id:
        return "[错误] task_id 不能为空"

    ok = cancel(task_id)
    if ok:
        return f"✅ 已取消任务: {task_id}"
    else:
        return f"[错误] 任务 '{task_id}' 不存在或取消失败"


def run_list(params: dict[str, Any]) -> str:
    """列出所有定时任务。"""
    from src.core.task_scheduler import list_tasks

    tasks = list_tasks()
    if not tasks:
        return "当前没有已注册的定时任务。"

    lines = ["当前定时任务："]
    for t in tasks:
        lines.append(f"  - {t['task_id']}: {t['name']} | 下次触发: {t['next_run']} | 触发器: {t['trigger']}")
    return "\n".join(lines)


def run_dispatch(params: dict[str, Any]) -> str:
    """统一调度入口（task_schedule 工具）。"""
    action = params.get("action", "").strip()
    if action == "schedule":
        return run_schedule(params)
    elif action == "cancel":
        return run_cancel(params)
    elif action == "list":
        return run_list(params)
    else:
        return f"[错误] 未知 action: {action}，支持 schedule/cancel/list"


def run_list_tool(params: dict[str, Any]) -> str:
    """task_list 工具入口。"""
    return run_list(params)


def run_cancel_tool(params: dict[str, Any]) -> str:
    """task_cancel 工具入口。"""
    return run_cancel(params)
