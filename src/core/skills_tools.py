"""Skills / Project 相关工具：skill（合并 view+search）、search_projects、project_context；skill 解析供 indexer 等复用。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

LAMPSON_DIR = Path.home() / ".lampson"
SKILLS_DIR = LAMPSON_DIR / "skills"
PROJECTS_DIR = LAMPSON_DIR / "projects"

# Session 在启动时通过 set_retrieval_indices 注入，供 skill search/search_projects 使用
_active_skill_index: Any = None
_active_project_index: Any = None


# ── 统一 Skill Schema ────────────────────────────────────────────────────────

SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "skill",
        "description": (
            "操作技能。action='view' 按名称加载技能全文，"
            "action='search' 在技能名称与描述中做关键词匹配查找。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["view", "search"],
                    "description": "操作类型：view 按名称加载技能，search 按关键词搜索技能",
                },
                "name": {
                    "type": "string",
                    "description": "技能名称（action='view' 时使用），例如 'code-writing', 'reverse-tracking'",
                },
                "query": {
                    "type": "string",
                    "description": "搜索关键词（action='search' 时使用），用自然语言描述需要的能力或工作流类型",
                },
                "top_k": {
                    "type": "integer",
                    "description": "search 时返回最多几个结果，默认 3",
                    "default": 3,
                },
            },
            "required": ["action"],
        },
    },
}

SEARCH_PROJECTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_projects",
        "description": (
            "根据自然语言描述搜索匹配的项目上下文。"
            "当你需要查找某个项目或仓库的背景信息时使用此工具。"
            "用自然语言描述你需要什么项目的什么信息，例如 '模型平台的工程目录'、'hermes 代码结构'。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用自然语言描述你需要哪类项目/仓库背景",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回最多几个结果，默认 2",
                    "default": 2,
                },
            },
            "required": ["query"],
        },
    },
}


PROJECT_CONTEXT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "project_context",
        "description": "加载指定项目的完整上下文（项目信息、状态、约定）。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "项目名称，例如 'Lampson'、'hermes'"
                }
            },
            "required": ["name"]
        }
    }
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def set_retrieval_indices(skill_index: Any, project_index: Any) -> None:
    """由 Session 在索引构建后调用，供 skill search/search_projects 使用。"""
    global _active_skill_index, _active_project_index
    _active_skill_index = skill_index
    _active_project_index = project_index



def _parse_skill(path: Path) -> dict[str, Any] | None:
    """解析 SKILL.md。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        body = content[match.end():]
    else:
        meta = {}
        body = content

    name = meta.get("name", "") or path.parent.name
    return {
        "name": name,
        "description": meta.get("description", ""),
        "triggers": meta.get("triggers", []),
        "body": body,
        "full_content": content,
    }


def project_context(params: dict[str, Any]) -> str:
    """加载指定项目的完整上下文。"""
    from src.core.prompt_builder import load_project_context as _load
    name = params.get("name", "")
    if not name:
        return "project_context 需要 name 参数，例如：project_context(name=\"Lampson\")"
    return _load(name)



def _increment_invocation(skill_path: Path) -> int:
    """递增 SKILL.md 的 invocation_count，保留正文；返回新计数。"""
    from src.core.prompt_builder import _parse_frontmatter, write_skill_with_frontmatter

    try:
        raw = skill_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    meta, body = _parse_frontmatter(raw)
    try:
        ic = int(meta.get("invocation_count", 0))
    except (TypeError, ValueError):
        ic = 0
    meta["invocation_count"] = ic + 1
    write_skill_with_frontmatter(skill_path, meta, body)
    return int(meta["invocation_count"])


def _run_skill_view(params: dict[str, Any]) -> str:
    """按名称加载 SKILL.md 全文，并递增 invocation_count。"""
    name = str(params.get("name", "")).strip()
    if not name:
        return "[错误] name 参数不能为空"
    global _active_skill_index
    if _active_skill_index is None:
        return "[提示] 技能索引未初始化"
    entries = getattr(_active_skill_index, "_entries", None)
    if not entries:
        return f"[错误] 未找到名为「{name}」的技能"
    path: Path | None = None
    for e in entries:
        if str(e.get("name", "")) == name:
            path = Path(str(e["path"]))
            break
    if path is None:
        low = name.lower()
        for e in entries:
            if str(e.get("name", "")).lower() == low:
                path = Path(str(e["path"]))
                break
    if path is None or not path.is_file():
        return f"[错误] 未找到名为「{name}」的技能"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        return f"[错误] 读取技能文件失败：{ex}"
    new_c = _increment_invocation(path)
    for e in entries:
        if str(e.get("path", "")) == str(path.resolve()):
            e["invocation_count"] = new_c
            break
    return text


def _run_skill_search(params: dict[str, Any]) -> str:
    """在 name / description 中做子串匹配，返回匹配的 SKILL.md 全文。"""
    query = params.get("query", "").strip()
    top_k = int(params.get("top_k", 3))
    if not query:
        return "[错误] query 参数不能为空"
    global _active_skill_index
    if _active_skill_index is None:
        return "[提示] 技能索引未初始化"
    q_lower = query.lower()
    results: list[str] = []
    for e in _active_skill_index._entries:  # type: ignore[union-attr]
        name = str(e.get("name", ""))
        desc = str(e.get("description", ""))
        blob = f"{name}\n{desc}"
        if q_lower not in blob.lower():
            continue
        p = str(e.get("path", ""))
        if not p:
            continue
        try:
            content = Path(p).read_text(encoding="utf-8")
        except OSError:
            continue
        if content.strip():
            results.append(content)
        if len(results) >= top_k:
            break
    if not results:
        return "未找到匹配的技能。"
    return "\n\n---\n\n".join(results)


def skill(params: dict[str, Any]) -> str:
    """统一 skill 工具入口。"""
    action = (params.get("action") or "").strip()
    if action == "view":
        return _run_skill_view(params)
    elif action == "search":
        return _run_skill_search(params)
    else:
        return "[错误] action 参数必须为 'view' 或 'search'"


def search_projects(params: dict[str, Any]) -> str:
    """语义搜索项目，返回匹配的项目全文。"""
    query = params.get("query", "").strip()
    top_k = int(params.get("top_k", 2))
    if not query:
        return "[错误] query 参数不能为空"
    global _active_project_index
    if _active_project_index is None:
        return "[提示] 项目索引未初始化"
    try:
        results = _active_project_index.search(query, top_k=top_k)  # type: ignore[union-attr]
    except Exception as e:
        return f"[错误] 搜索项目失败：{e}"
    if not results:
        return "未找到匹配的项目。"
    return "\n\n---\n\n".join(results)
