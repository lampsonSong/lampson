"""工具注册与调度：统一管理所有工具的 schema 和执行函数。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from src.tools import shell as shell_tool
from src.tools import fileops as fileops_tool
from src.tools import search as search_tool
from src.tools import web as web_tool
from src.tools import session as session_tool
from src.tools import task_scheduler_tool
from src.feishu import client as feishu_client
from src.core import skills_tools

logger = logging.getLogger(__name__)

ToolRunner = Callable[[dict[str, Any]], str]

_REGISTRY: dict[str, tuple[dict[str, Any], ToolRunner]] = {}


def _register(schema: dict[str, Any], runner: ToolRunner) -> None:
    name = schema["function"]["name"]
    _REGISTRY[name] = (schema, runner)


_register(shell_tool.SCHEMA, shell_tool.run)
_register(search_tool.SEARCH_SCHEMA, search_tool.run)
_register(fileops_tool.FILE_READ_SCHEMA, fileops_tool.run_file_read)
_register(fileops_tool.FILE_WRITE_SCHEMA, fileops_tool.run_file_write)
_register(web_tool.SCHEMA, web_tool.run)
_register(feishu_client.FEISHU_SEND_SCHEMA, feishu_client.tool_feishu_send)
_register(feishu_client.FEISHU_READ_SCHEMA, feishu_client.tool_feishu_read)
_register(skills_tools.PROJECT_CONTEXT_SCHEMA, skills_tools.project_context)
_register(skills_tools.SKILL_SCHEMA, skills_tools.skill)
_register(skills_tools.SEARCH_PROJECTS_SCHEMA, skills_tools.search_projects)
_register(session_tool.SESSION_SCHEMA, session_tool.run)

# 定时任务管理工具（3 个 schema 共用同一模块）
_register(task_scheduler_tool.SCHEDULE_SCHEMA, task_scheduler_tool.run_dispatch)
_register(task_scheduler_tool.LIST_TASKS_SCHEMA, task_scheduler_tool.run_list_tool)
_register(task_scheduler_tool.CANCEL_TASK_SCHEMA, task_scheduler_tool.run_cancel_tool)


# ── learned_modules 延迟加载 ──────────────────────────────────────────────

def load_learned_modules() -> None:
    """扫描 ~/.lampson/learned_modules/，注册所有包含 TOOL_SCHEMA 的模块为工具。

    必须在 daemon 启动完成后调用，不能在模块初始化时调用，否则会产生循环导入：
    tools.py → learned_modules.py → tools.py（tools 模块还未初始化完成）。
    """
    try:
        from src.tools import learned_modules
        registered = learned_modules.scan_and_register()
        if registered:
            logger.info(f"已加载 {len(registered)} 个 learned_modules 工具: "
                        f"{[s['function']['name'] for s in registered]}")
        else:
            logger.debug("未发现 learned_modules 工具（learned_modules/ 目录为空）")
    except Exception as e:
        logger.warning(f"加载 learned_modules 失败: {e}")


# ─── 飞书客户端懒加载初始化 ────────────────────────────────────────────────

_feishu_initialized = False


def _ensure_feishu_client() -> bool:
    """确保飞书客户端已初始化（懒加载）。"""
    global _feishu_initialized
    if _feishu_initialized:
        return True

    config_paths = [
        Path("~/.lampson/config.yaml").expanduser(),
        Path("config/default.yaml").expanduser(),
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                import yaml
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                feishu_cfg = config.get("feishu", {})
                app_id = feishu_cfg.get("app_id", "").strip()
                app_secret = feishu_cfg.get("app_secret", "").strip()
                if app_id and app_secret:
                    feishu_client.init_client(app_id=app_id, app_secret=app_secret)
                    _feishu_initialized = True
                    return True
            except Exception:
                pass

    return False


def validate_tool_schema(schema: dict[str, Any]) -> list[str]:
    """校验工具 schema 格式，返回错误列表（空列表 = 通过）。

    OpenAI function calling 要求:
      - type: "function"
      - function: { name: str, parameters: dict, ... }
    """
    errors: list[str] = []
    if schema.get("type") != "function":
        errors.append(f"缺少或错误的 type 字段: {schema.get('type')!r}, 期望 'function'")
    func = schema.get("function")
    if not isinstance(func, dict):
        errors.append("缺少 function 字段或类型不是 dict")
    else:
        if not func.get("name"):
            errors.append("缺少 function.name")
        if "parameters" not in func:
            errors.append("缺少 function.parameters")
    return errors


def get_all_schemas() -> list[dict[str, Any]]:
    """返回所有工具的 OpenAI function calling schema 列表。"""
    return [schema for schema, _ in _REGISTRY.values()]


def dispatch(tool_name: str, arguments_raw: str | dict[str, Any]) -> str:
    """根据工具名分发执行，arguments_raw 可以是 JSON 字符串或字典。"""
    if tool_name not in _REGISTRY:
        return f"[错误] 未知工具：{tool_name}"

    if tool_name.startswith("feishu_"):
        _ensure_feishu_client()

    if isinstance(arguments_raw, str):
        try:
            params = json.loads(arguments_raw)
        except json.DecodeError as e:
            return f"[错误] 工具参数解析失败：{e}"
    else:
        params = arguments_raw

    _, runner = _REGISTRY[tool_name]
    try:
        return runner(params)
    except Exception as e:
        return f"[错误] 工具 {tool_name} 执行异常：{e}"


def register_external(schema: dict[str, Any], runner: ToolRunner) -> bool:
    """注册外部工具（供飞书、自更新等模块动态注册）。

    Returns:
        True 注册成功，False schema 校验失败被跳过。
    """
    errors = validate_tool_schema(schema)
    if errors:
        name_hint = schema.get("function", {}).get("name", "<未知>")
        logger.warning(f"工具 {name_hint} schema 校验失败，跳过注册: {'; '.join(errors)}")
        return False
    _register(schema, runner)
    return True