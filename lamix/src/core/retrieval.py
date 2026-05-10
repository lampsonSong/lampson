"""将语义检索结果格式化为可注入 plan / Fast Path 的文本。"""

from __future__ import annotations

from typing import Any


def format_retrieved_context(
    matched_skills: list[str],
    matched_projects: list[str],
) -> str:
    """将 skill / project 全文块拼成一段上下文。"""
    parts: list[str] = []
    if matched_skills:
        block = "\n\n---\n\n".join(matched_skills)
        parts.append(f"## 匹配的技能\n\n{block}")
    if matched_projects:
        block = "\n\n---\n\n".join(matched_projects)
        parts.append(f"## 匹配的项目上下文\n\n{block}")
    return "\n\n".join(parts)


def retrieve_for_plan(
    skill_needs: str,
    project_needs: str,
    skill_index: Any,
    project_index: Any,
    retrieval: dict[str, Any],
) -> str:
    """根据分类阶段的需求描述做检索，返回可注入的 Markdown 文本。检索失败或为空则返回空串。"""
    if not isinstance(retrieval, dict):
        retrieval = {}
    th = float(retrieval.get("similarity_threshold", 0.3))
    k_skill = int(retrieval.get("skill_top_k", 3))
    k_proj = int(retrieval.get("project_top_k", 2))
    skills: list[str] = []
    projects: list[str] = []
    try:
        if (skill_needs or "").strip() and skill_index is not None:
            skills = skill_index.search(  # type: ignore[union-attr]
                (skill_needs or "").strip(),
                top_k=k_skill,
                similarity_threshold=th,
            )
    except Exception:
        skills = []
    try:
        if (project_needs or "").strip() and project_index is not None:
            projects = project_index.search(  # type: ignore[union-attr]
                (project_needs or "").strip(),
                top_k=k_proj,
                similarity_threshold=th,
            )
    except Exception:
        projects = []
    return format_retrieved_context(skills, projects)
