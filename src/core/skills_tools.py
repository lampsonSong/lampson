"""Skills / Project 相关工具：skill（合并 view+search）、search_projects、project_context；skill 解析供 indexer 等复用。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

LAMIX_DIR = Path.home() / ".lamix"
SKILLS_DIR = LAMIX_DIR / "memory" / "skills"
PROJECTS_DIR = LAMIX_DIR / "memory" / "projects"
INFO_DIR = LAMIX_DIR / "memory" / "info"

# Session 在启动时通过 set_retrieval_indices 注入，供 skill search/search_projects/info 使用
_active_skill_index: Any = None
_active_project_index: Any = None
_active_info_index: Any = None

# 双层结构入口文件名
_SKILL_ENTRY_FILE = "SKILL.md"


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
                    "description": "项目名称，例如 'Lamix'、'hermes'"
                }
            },
            "required": ["name"]
        }
    }
}

INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "info",
        "description": "加载知识性信息文件的内容，例如项目规范、API 文档、使用说明等。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "文件名（不含 .md），例如 'api-reference'、'deployment-guide'"
                }
            },
            "required": ["name"]
        }
    }
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def set_retrieval_indices(skill_index: Any, project_index: Any, info_index: Any = None) -> None:
    """由 Session 在索引构建后调用，供 skill search/search_projects/info 使用。"""
    global _active_skill_index, _active_project_index, _active_info_index
    _active_skill_index = skill_index
    _active_project_index = project_index
    _active_info_index = info_index



def _parse_skill(path: Path) -> dict[str, Any] | None:
    """解析 skill 文件。"""
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

    name = meta.get("name", "") or path.stem
    return {
        "name": name,
        "description": meta.get("description", ""),
        "body": body,
        "full_content": content,
    }


def project_context(params: dict[str, Any]) -> str:
    """加载指定项目的完整上下文。"""
    from src.core.prompt_builder import load_project_context as _load
    name = params.get("name", "")
    if not name:
        return "project_context 需要 name 参数，例如：project_context(name=\"Lamix\")"
    return _load(name)



def _get_skill_entry_by_name(name: str) -> dict[str, Any] | None:
    """根据 skill 名查找条目（支持双层路径）。返回条目或 None。

    双层结构优先：skills/<name>/SKILL.md
    向后兼容：skills/<name>.md
    """
    from src.core.prompt_builder import _scan_skills_dir

    entries = _scan_skills_dir()
    for e in entries:
        skill_name = str(e.get("name", "")).lower()
        if skill_name == name.lower():
            return e
        # 模糊匹配
        if skill_name == name.lower():
            return e
    return None


def _resolve_skill_path(name: str) -> tuple[Path | None, bool]:
    """解析 skill 名称，返回 (文件路径, 是否子项)。

    - name="code-writing" → skills/code-writing/SKILL.md 或 skills/code-writing.md
    - name="code-writing/references/python-patterns" → skills/code-writing/references/python-patterns.md

    Returns: (path, is_sub_item)
    """
    if "/" in name:
        skill_name, sub_part = name.split("/", 1)
    else:
        skill_name, sub_part = name, ""

    if not SKILLS_DIR.exists():
        return None, False

    skill_dir = SKILLS_DIR / skill_name
    entry_file = skill_dir / _SKILL_ENTRY_FILE

    if sub_part:
        # 子项：skills/<name>/<sub_part>.md
        sub_path = skill_dir / (sub_part + ".md")
        if sub_path.is_file():
            return sub_path, True
        # 尝试不带扩展名（子目录形式）
        sub_dir = skill_dir / sub_part
        if sub_dir.is_dir():
            # 返回目录下的第一个 .md 文件
            md_files = list(sub_dir.glob("*.md"))
            if md_files:
                return md_files[0], True
        return None, True

    # 主入口：先尝试 SKILL.md，再尝试平铺 .md
    if entry_file.is_file():
        return entry_file, False
    legacy_file = SKILLS_DIR / f"{skill_name}.md"
    if legacy_file.is_file():
        return legacy_file, False
    return None, False


def _increment_invocation(skill_path: Path) -> int:
    """递增 skill 文件的 invocation_count 和 last_used_at，保留正文；返回新计数。"""
    from datetime import date
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
    meta["last_used_at"] = str(date.today())
    write_skill_with_frontmatter(skill_path, meta, body)
    return int(meta["invocation_count"])


def _run_skill_view(params: dict[str, Any]) -> str:
    """按名称加载 skill 文件全文，并递增 invocation_count。

    支持双层路径：
    - name="code-writing" → 加载 skills/code-writing/SKILL.md
    - name="code-writing/references/python-patterns" → 加载子项
    """
    name = str(params.get("name", "")).strip()
    if not name:
        return "[错误] name 参数不能为空"

    path, is_sub_item = _resolve_skill_path(name)
    if path is None or not path.is_file():
        # 提供可用的 skill 列表
        from src.core.prompt_builder import _scan_skills_dir
        entries = _scan_skills_dir()
        available = [str(e["name"]) for e in entries]
        avail_str = ", ".join(available) if available else "(none)"
        return f"[错误] 未找到名为「{name}」的技能\n\n可用技能: {avail_str}"

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        return f"[错误] 读取技能文件失败：{ex}"

    # 只对主入口文件递增 invocation_count
    if not is_sub_item:
        new_c = _increment_invocation(path)
        # 同步更新 index 中的计数
        from src.core.prompt_builder import _scan_skills_dir
        entries = _scan_skills_dir()
        for e in entries:
            if str(e.get("path", "")) == str(path.resolve()):
                e["invocation_count"] = new_c
                break

        # 启动 skill 执行审计
        try:
            from src.core.skill_audit import start_audit
            body = text
            fm_match = __import__("re").match(r"^---\s*\n.*?\n---\s*\n", text, __import__("re").DOTALL)
            if fm_match:
                body = text[fm_match.end():]
            start_audit(name, body)
        except Exception as e:
            __import__("logging").getLogger(__name__).debug(f"审计启动失败: {e}")

    return text


def _run_skill_search(params: dict[str, Any]) -> str:
    """在 skill 内容中做关键词匹配，返回匹配的 skill 文件全文。

    搜索范围：SKILL.md + references/*.md + templates/*.md
    """
    query = params.get("query", "").strip()
    top_k = int(params.get("top_k", 3))
    if not query:
        return "[错误] query 参数不能为空"

    from src.core.prompt_builder import _scan_skills_dir, _parse_frontmatter

    entries = _scan_skills_dir()
    if not entries:
        return "[提示] 技能索引为空"

    q_lower = query.lower()
    results: list[tuple[float, str]] = []  # (score, content)

    for e in entries:
        skill_dir = e["path"].parent
        skill_name = str(e.get("name", ""))

        # 搜索所有相关文件
        files_to_search: list[Path] = [e["path"]]  # SKILL.md

        # references/*.md, templates/*.md
        for subdir in ("references", "templates"):
            subdir_path = skill_dir / subdir
            if subdir_path.is_dir():
                files_to_search.extend(subdir_path.glob("*.md"))

        for fp in files_to_search:
            try:
                content = fp.read_text(encoding="utf-8")
            except OSError:
                continue
            if not content.strip():
                continue

            # 在 name + description + body 中匹配
            meta, body = _parse_frontmatter(content)
            name = str(meta.get("name", "")) or fp.stem
            desc = str(meta.get("description", ""))
            blob = f"{name}\n{desc}\n{body}"
            blob_lower = blob.lower()

            if q_lower not in blob_lower:
                continue

            # 命中出现在靠前位置时加分
            pos = blob_lower.index(q_lower)
            score = 1.0 - (pos / max(len(blob_lower), 1))
            results.append((score, content))

    results.sort(key=lambda x: x[0], reverse=True)
    top_results = [content for _, content in results[:top_k]]

    if not top_results:
        return "未找到匹配的技能。"
    return "\n\n---\n\n".join(top_results)


def skill(params: dict[str, Any]) -> str:
    """统一 skill 工具入口。"""
    action = (params.get("action") or "").strip()
    if action == "view":
        return _run_skill_view(params)
    elif action == "search":
        return _run_skill_search(params)
    else:
        return "[错误] action 参数必须为 'view' 或 'search'"


def info(params: dict[str, Any]) -> str:
    """加载 info 知识文件内容。"""
    name = params.get("name", "").strip()
    if not name:
        return "[错误] info 需要 name 参数，例如：info(name=\"api-reference\")"
    from src.core.prompt_builder import load_info as _load_info
    return _load_info(name)


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


# ── 归档查询与恢复 ──────────────────────────────────────────────────────────

ARCHIVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "archive",
        "description": "归档管理。action='list' 列出归档内容；action='restore' 从归档恢复指定项。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "restore"],
                    "description": "操作类型",
                },
                "category": {
                    "type": "string",
                    "enum": ["skill", "info", "project", "all"],
                    "description": "要查看或恢复的类别（list 时默认 all，restore 时必填）",
                },
                "name": {
                    "type": "string",
                    "description": "要恢复的名称（restore 时必填）",
                },
            },
            "required": ["action"],
        },
    },
}


def _list_archived_impl(category: str) -> str:
    """列出所有已归档的 skills/info/projects。"""
    from src.core.config import LAMIX_DIR

    results = []

    if category in ("skill", "all"):
        archive_dir = SKILLS_DIR / ".archived"
        if archive_dir.exists():
            for f in sorted(archive_dir.glob("*.md")):
                try:
                    raw = f.read_text(encoding="utf-8")
                    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
                    desc = ""
                    if fm:
                        meta = yaml.safe_load(fm.group(1)) or {}
                        desc = meta.get("description", "")[:80]
                    results.append(f"  📦 skill/{f.stem}: {desc}")
                except OSError:
                    results.append(f"  📦 skill/{f.stem}")

    if category in ("info", "all"):
        info_archive = LAMIX_DIR / "memory" / "info" / ".archived"
        if info_archive.exists():
            for f in sorted(info_archive.glob("*.md")):
                results.append(f"  📦 info/{f.stem}")

    if category in ("project", "all"):
        proj_archive = LAMIX_DIR / "memory" / "projects" / ".archived"
        if proj_archive.exists():
            for f in sorted(proj_archive.glob("*.md")):
                results.append(f"  📦 project/{f.stem}")

    if not results:
        return "没有归档内容。"

    return "归档列表：\n" + "\n".join(results)


def _restore_archived_impl(category: str, name: str) -> str:
    """从归档中恢复指定 skill/info/project。"""
    import shutil
    from src.core.config import LAMIX_DIR

    if category == "skill":
        archive_dir = SKILLS_DIR / ".archived"
        candidates = list(archive_dir.glob(f"{name}*.md")) if archive_dir.exists() else []
        target_path = SKILLS_DIR / f"{name}.md"
    elif category == "info":
        archive_dir = LAMIX_DIR / "memory" / "info" / ".archived"
        target_dir = LAMIX_DIR / "memory" / "info"
        candidates = [f for f in archive_dir.glob(f"{name}*.md")] if archive_dir.exists() else []
    elif category == "project":
        archive_dir = LAMIX_DIR / "memory" / "projects" / ".archived"
        target_dir = LAMIX_DIR / "memory" / "projects"
        candidates = [f for f in archive_dir.glob(f"{name}*.md")] if archive_dir.exists() else []
    else:
        return f"[错误] 不支持的类别: {category}"

    if not archive_dir.exists():
        return f"[错误] {category} 归档目录不存在"

    if not candidates:
        return f"[错误] 归档中未找到: {category}/{name}"

    if len(candidates) > 1:
        names = [c.name for c in candidates]
        return f"[错误] 匹配到多个: {names}，请更精确指定"

    src = candidates[0]
    if category == "skill":
        dest = target_path
    else:
        dest = target_dir / src.name
    if dest.exists():
        return f"[错误] 目标已存在: {dest}，请先处理冲突"

    shutil.move(str(src), str(dest))
    return f"✓ 已恢复: {category}/{name} → {dest}"


def archive(params: dict[str, Any]) -> str:
    """归档管理：list / restore。"""
    action = params.get("action", "")
    if action == "list":
        category = params.get("category", "all")
        return _list_archived_impl(category)
    elif action == "restore":
        category = params.get("category", "")
        name = params.get("name", "").strip()
        if not category or not name:
            return "[错误] restore 需要 category 和 name"
        return _restore_archived_impl(category, name)
    else:
        return "[错误] action 必填，可选: list, restore"


# ── 向后兼容别名 ──────────────────────────────────────────────────────────
def list_archived(params: dict[str, Any]) -> str:
    return _list_archived_impl(params.get("category", "all"))


def restore_archived(params: dict[str, Any]) -> str:
    return _restore_archived_impl(params.get("category", ""), params.get("name", "").strip())
