"""Skills 工具：skill_view、skills_list、project_context，供 Agent 调用。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


LAMPSON_DIR = Path.home() / ".lampson"
SKILLS_DIR = LAMPSON_DIR / "skills"
PROJECTS_DIR = LAMPSON_DIR / "projects"

SKILL_VIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "skill_view",
        "description": "加载指定 skill 的全文内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名称，例如 '文件搜索' 或 'lampson/文件搜索'"
                }
            },
            "required": ["name"]
        }
    }
}

SKILLS_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "skills_list",
        "description": "列出或搜索 skills。",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "按 category 过滤（可选）"
                },
                "query": {
                    "type": "string",
                    "description": "按 keyword 搜索（可选）"
                }
            }
        }
    }
}

MEMORY_SHOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory_show",
        "description": (
            "展示你记住的所有内容，包括：核心记忆(core.md)、项目列表及摘要、"
            "技能列表、最近会话摘要。"
            "当用户问'你都记了啥'、'你记住了什么'、'看看你的记忆'、"
            "'show me your memory'等查看记忆类问题时使用此工具。"
            "这是一个聚合查询，一次性返回所有已存储的内容。"
        ),
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
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


def _iter_skills() -> list[dict[str, Any]]:
    if not SKILLS_DIR.exists():
        return []
    results = []
    for sf in SKILLS_DIR.rglob("SKILL.md"):
        s = _parse_skill(sf)
        if s:
            results.append(s)
    return results


def skill_view(params: dict[str, Any]) -> str:
    """加载指定 skill 的全文内容。"""
    name = params.get("name", "")
    if not name:
        return "skill_view 需要 name 参数，例如：skill_view(name=\"文件搜索\")"

    skills = _iter_skills()
    for s in skills:
        if s["name"] == name:
            return s["full_content"]

    available = ", ".join(s["name"] for s in skills)
    return f"[Skill '{name}' not found]\n\nAvailable skills: {available or '(none)'}"


def skills_list(params: dict[str, Any]) -> str:
    """列出或搜索 skills。"""
    category = params.get("category")
    query = params.get("query")

    skills = _iter_skills()

    if query:
        q = query.lower()
        skills = [s for s in skills
                  if q in s["name"].lower()
                  or q in s["description"].lower()
                  or any(q in t.lower() for t in s["triggers"])]

    if category:
        skills = [s for s in skills
                  if s["name"].startswith(f"{category}/")
                  or category in s["name"]]

    if not skills:
        return "[No skills found]"

    lines = []
    for s in skills:
        desc = s["description"] or ""
        triggers = s["triggers"]
        trigger_str = f" (触发: {', '.join(triggers)})" if triggers else ""
        lines.append(f"- **{s['name']}**{trigger_str}\n  {desc}")

    total = len(skills)
    header = f"Skills ({total} found)"
    if category:
        header += f", category={category}"
    if query:
        header += f", query={query}"

    return f"# {header}\n\n" + "\n".join(lines)


def project_context(params: dict[str, Any]) -> str:
    """加载指定项目的完整上下文。"""
    from src.core.prompt_builder import load_project_context as _load
    name = params.get("name", "")
    if not name:
        return "project_context 需要 name 参数，例如：project_context(name=\"Lampson\")"
    return _load(name)


def memory_show(params: dict[str, Any]) -> str:
    """展示所有已存储的记忆内容（核心记忆 + 项目 + 技能 + 会话摘要）。"""
    sections: list[str] = []

    # 1. 核心记忆
    core_path = LAMPSON_DIR / "memory" / "core.md"
    if core_path.exists():
        try:
            content = core_path.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"## 核心记忆\n\n{content}")
        except OSError:
            pass

    # 2. 项目列表
    if PROJECTS_DIR.exists():
        projects: list[str] = []
        for md_file in sorted(PROJECTS_DIR.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                preview = content[:300] + "\n..." if len(content) > 300 else content
                projects.append(f"### {md_file.stem}\n\n{preview}")
            except OSError:
                pass
        if projects:
            sections.append(
                f"## 项目（{len(projects)} 个）\n\n" + "\n\n".join(projects)
            )

    # 3. 技能列表
    skills = _iter_skills()
    if skills:
        skill_lines = []
        for s in skills:
            desc = s["description"] or ""
            triggers = s.get("triggers", [])
            trigger_str = f"（触发: {', '.join(triggers[:3])}）" if triggers else ""
            skill_lines.append(f"- **{s['name']}**{trigger_str}: {desc}")
        sections.append(
            f"## 技能（{len(skills)} 个）\n\n" + "\n".join(skill_lines)
        )

    # 4. 最近会话摘要
    sessions_dir = LAMPSON_DIR / "memory" / "sessions"
    if sessions_dir.exists():
        session_files = sorted(sessions_dir.glob("*.md"), reverse=True)[:3]
        if session_files:
            session_lines = []
            for sf in session_files:
                try:
                    content = sf.read_text(encoding="utf-8").strip()
                    if not content:
                        continue
                    preview = content[:500] + "\n..." if len(content) > 500 else content
                    session_lines.append(f"### {sf.stem}\n\n{preview}")
                except OSError:
                    pass
            if session_lines:
                sections.append(
                    f"## 最近会话摘要\n\n" + "\n\n".join(session_lines)
                )

    if not sections:
        return "目前还没有存储任何记忆内容。"

    return "# 我的记忆\n\n" + "\n\n".join(sections)
