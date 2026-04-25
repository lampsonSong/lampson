"""工具注册与调度：统一管理所有工具的 schema 和执行函数。"""

from __future__ import annotations

import json
from typing import Any, Callable

from src.tools import shell as shell_tool
from src.tools import fileops as fileops_tool
from src.tools import search as search_tool
from src.tools import web as web_tool
from src.feishu import client as feishu_client
from src.core import skills_tools


ToolRunner = Callable[[dict[str, Any]], str]

_REGISTRY: dict[str, tuple[dict[str, Any], ToolRunner]] = {}


def _register(schema: dict[str, Any], runner: ToolRunner) -> None:
    name = schema["function"]["name"]
    _REGISTRY[name] = (schema, runner)


_register(shell_tool.SCHEMA, shell_tool.run)
_register(search_tool.SEARCH_FILES_SCHEMA, search_tool.run_search_files)
_register(search_tool.SEARCH_CONTENT_SCHEMA, search_tool.run_search_content)
_register(fileops_tool.FILE_READ_SCHEMA, fileops_tool.run_file_read)
_register(fileops_tool.FILE_WRITE_SCHEMA, fileops_tool.run_file_write)
_register(web_tool.SCHEMA, web_tool.run)
_register(feishu_client.FEISHU_SEND_SCHEMA, feishu_client.tool_feishu_send)
_register(feishu_client.FEISHU_READ_SCHEMA, feishu_client.tool_feishu_read)
_register(skills_tools.SKILL_VIEW_SCHEMA, skills_tools.skill_view)
_register(skills_tools.SKILLS_LIST_SCHEMA, skills_tools.skills_list)
_register(skills_tools.PROJECT_CONTEXT_SCHEMA, skills_tools.project_context)
_register(skills_tools.MEMORY_SHOW_SCHEMA, skills_tools.memory_show)


def get_all_schemas() -> list[dict[str, Any]]:
    """返回所有工具的 OpenAI function calling schema 列表。"""
    return [schema for schema, _ in _REGISTRY.values()]


def dispatch(tool_name: str, arguments_raw: str | dict[str, Any]) -> str:
    """根据工具名分发执行，arguments_raw 可以是 JSON 字符串或字典。"""
    if tool_name not in _REGISTRY:
        return f"[错误] 未知工具：{tool_name}"

    if isinstance(arguments_raw, str):
        try:
            params = json.loads(arguments_raw)
        except json.JSONDecodeError as e:
            return f"[错误] 工具参数解析失败：{e}"
    else:
        params = arguments_raw

    _, runner = _REGISTRY[tool_name]
    try:
        return runner(params)
    except Exception as e:
        return f"[错误] 工具 {tool_name} 执行异常：{e}"


def register_external(schema: dict[str, Any], runner: ToolRunner) -> None:
    """注册外部工具（供飞书、自更新等模块动态注册）。"""
    _register(schema, runner)
