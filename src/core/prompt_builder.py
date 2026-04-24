"""分层 System Prompt 构建器。

参考 Hermes 的分层设计，Lampson 的 system prompt 分 8 层：

Layer 1  Identity         - SOUL.md 文件内容
Layer 2  Tool Guidance    - Memory/Skills/Tool-use 指引
Layer 3  Memory Block     - 核心记忆结构化文本
Layer 4  Project Index   - 项目索引（按需加载项目上下文）
Layer 5  Skills Index     - 技能索引（全文按需加载）
Layer 6  Context Files    - .lampson.md / AGENTS.md
Layer 7  Model Guidance   - 模型适配指引
Layer 8  Platform Hints   - CLI 环境提示
Layer 9  Timestamp        - 对话开始时间
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import yaml


LAMPSON_DIR = Path.home() / ".lampson"
SOUL_PATH = LAMPSON_DIR / "SOUL.md"
SKILLS_DIR = LAMPSON_DIR / "skills"
# 项目目录（哥哥的工作区）
PROJECTS_DIR = Path.home() / ".openclaw" / "workspace" / "projects"
PROJECTS_INDEX = LAMPSON_DIR / "projects_index.md"

# ── Tool Guidance 常量 ────────────────────────────────────────────────────────

MEMORY_GUIDANCE = (
    "你拥有跨会话的持久记忆。使用 memory 工具保存关键事实：用户偏好、环境细节、工具特性、项目约定。\n"
    "记忆在每轮都会注入，请保持简洁，只记值得关注很久的事情。\n"
    "优先保存能减少未来重复沟通的内容——用户偏好和常见纠正比任务进展更重要。\n"
    "不要记录任务进度、已完成工作日志或临时状态；用 session_search 工具从历史记录里找。\n"
    "用 declarative 事实风格写记忆，不要写给自己的指令。\n"
    "✓ '用户喜欢简洁回复'  ✗ '永远简洁回复'\n"
    "✓ '项目用 pytest xdist'  ✗ '用 pytest -n 4 运行测试'\n"
    "祈使句会被当作指令重新阅读，可能覆盖用户的当前请求。\n"
    "工作流程写在 skills 里，不是 memory 里。"
)

SKILLS_GUIDANCE = (
    "完成复杂任务（5+ 工具调用）、修复疑难错误或发现重要工作流后，\n"
    "用 skill_manage 工具将其保存为技能，以便下次复用。\n"
    "如果发现某个 skill 过期、不完整或错误，立即用 skill_manage(action='patch') 修补，不要等被问到。\n"
    "不维护的 skills 迟早会成为负担。"
)

TOOL_USE_ENFORCEMENT = (
    "# 工具使用强制要求\n"
    "你必须使用工具采取行动——不能只描述你要做什么而不实际执行。\n"
    "当你说要执行某个操作时，必须在同一回复中立即调用相应工具。\n"
    "不要用总结'下一步计划'来结束回合——立即执行。\n"
    "每轮回复必须：(a) 包含推进任务的工具调用，或 (b) 向用户交付最终结果。\n"
    "只描述意图而不行动是不可接受的。"
)

SESSION_SEARCH_GUIDANCE = (
    "当用户提到过去对话的内容，或你怀疑存在跨会话的相关上下文，\n"
    "使用 session_search 工具搜索历史记录，不要让用户重复自己。"
)

# ── Frontmatter 解析 ─────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter，返回 (meta_dict, body)。"""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    body = content[match.end():]
    return meta, body


# ── Skills 索引构建 ──────────────────────────────────────────────────────────

def _iter_skill_files() -> list[Path]:
    """遍历 skills 目录，返回所有 SKILL.md 路径。"""
    if not SKILLS_DIR.exists():
        return []
    return list(SKILLS_DIR.rglob("SKILL.md"))


def build_skills_index() -> str:
    """生成紧凑的 skills 索引（全文不注入）。"""
    skill_files = _iter_skill_files()
    if not skill_files:
        return ""

    categories: dict[str, list[tuple[str, str]]] = {}

    for sf in skill_files:
        try:
            content = sf.read_text(encoding="utf-8")
        except OSError:
            continue

        meta, _ = _parse_frontmatter(content)
        name = meta.get("name", "") or sf.parent.name
        desc = meta.get("description", "")
        triggers = meta.get("triggers", [])

        # 从文件名推断 category（相对于 SKILLS_DIR 的路径）
        try:
            rel = sf.relative_to(SKILLS_DIR)
            parts = rel.parts
            if len(parts) >= 2:
                category = parts[-2]
            else:
                category = "general"
        except ValueError:
            category = "general"

        # 用 triggers 作为描述兜底
        if not desc and triggers:
            desc = "、".join(triggers[:3])

        categories.setdefault(category, []).append((name, desc))

    if not categories:
        return ""

    lines = []
    for cat in sorted(categories.keys()):
        items = sorted(categories[cat], key=lambda x: x[0])
        names = [n for n, _ in items]
        count = len(names)
        names_str = ", ".join(names[:5])
        if count > 5:
            names_str += f" ... ({count} skills)"
        lines.append(f"  {cat}: {names_str}")

    return (
        "## Skills (mandatory)\n"
        "Before replying, review relevant skills below. Use skills_list(category=\"xxx\") to expand.\n"
        "If you need a skill's full content, use skill_view(name=\"xxx\") to load it.\n"
        "\n"
        "<available_skills>\n"
        + "\n".join(lines) + "\n"
        "</available_skills>\n"
        "\n"
        "Only proceed without loading a skill if genuinely none are relevant."
    )


# ── Project Index & Context ───────────────────────────────────────────────────

def build_project_index() -> str:
    """生成项目索引，告诉 LLM 有哪些项目及其一句话描述。"""
    if not PROJECTS_INDEX.exists():
        return ""
    try:
        content = PROJECTS_INDEX.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        return (
            "## Projects (项目上下文，按需加载)\n\n"
            "当用户提到或暗示与某个项目相关的内容时，\n"
            "使用 project_context(name=\"项目名\") 加载该项目的完整上下文。\n\n"
            + content
        )
    except OSError:
        return ""


def load_project_context(name: str) -> str:
    """加载指定项目的完整上下文（projects/xxx.md 内容）。"""
    if not name:
        return "project_context 需要 name 参数，例如：project_context(name=\"Lampson\")"

    # 支持模糊匹配：找第一个名字包含 keyword 的项目文件
    if not PROJECTS_DIR.exists():
        return f"[项目目录不存在：{PROJECTS_DIR}]"

    # 尝试精确匹配 projects/xxx.md
    for md_file in PROJECTS_DIR.rglob("*.md"):
        if md_file.stem.lower() == name.lower():
            try:
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    return f"# {md_file.stem}\n\n{content}"
            except OSError:
                pass

    # 模糊匹配：文件名包含 keyword
    for md_file in PROJECTS_DIR.rglob("*.md"):
        if name.lower() in md_file.stem.lower():
            try:
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    return f"# {md_file.stem}\n\n{content}"
            except OSError:
                pass

    # 列出可用项目
    available = [f.stem for f in PROJECTS_DIR.rglob("*.md")]
    avail_str = ", ".join(available) if available else "(none)"
    return f"[项目 '{name}' not found]\n\nAvailable projects: {avail_str}"


# ── Identity 加载 ─────────────────────────────────────────────────────────────

DEFAULT_IDENTITY = (
    "你是 Lampson，一个运行在终端的 CLI 智能助手。你可以：\n"
    "- 通过工具执行 shell 命令\n"
    "- 读写本地文件\n"
    "- 搜索网页\n"
    "- 发送和接收飞书消息\n\n"
    "在回复时请简洁、直接，优先使用工具完成任务。如果不确定用户意图，先确认再行动。\n"
    "危险操作（删除文件、修改系统配置等）执行前必须让用户确认。"
)


def load_identity() -> str:
    """加载 ~/.lampson/SOUL.md，不存在则用 DEFAULT_IDENTITY。"""
    if SOUL_PATH.exists():
        try:
            content = SOUL_PATH.read_text(encoding="utf-8").strip()
            if content:
                return content
        except OSError:
            pass
    return DEFAULT_IDENTITY


# ── Context Files 加载 ────────────────────────────────────────────────────────

def load_context_file(cwd: str | None = None) -> str:
    """加载项目目录的 .lampson.md / AGENTS.md（优先 .lampson.md）。"""
    if cwd is None:
        cwd = os.getcwd()

    for name in (".lampson.md", "AGENTS.md"):
        p = Path(cwd) / name
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    meta, body = _parse_frontmatter(content)
                    # 去掉 frontmatter 后输出
                    return f"## {name}\n\n{body or content}"
            except OSError:
                pass
    return ""


# ── Model Guidance ────────────────────────────────────────────────────────────

def build_model_guidance(model: str) -> list[str]:
    """根据模型类型返回对应的行为指引。"""
    lower = model.lower()
    layers = []

    if "glm" in lower:
        layers.append(
            "你正在使用 GLM 模型。请直接使用工具调用，"
            "GLM 对 tool_calls 支持良好，不要尝试用文本描述工具调用。"
        )

    return layers


# ── Platform Hints ───────────────────────────────────────────────────────────

PLATFORM_HINTS = """\
# 运行环境
你运行在 CLI 终端环境中。
回复应简洁，优先使用单行命令。
路径处理：~ 展开为 /Users/songyuhao/"""


# ── Timestamp ────────────────────────────────────────────────────────────────

def build_timestamp() -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"# Session Info\n\nConversation started: {now}"


# ── PromptBuilder ────────────────────────────────────────────────────────────

class PromptBuilder:
    """分层构建 system prompt。"""

    def __init__(self, model: str = "") -> None:
        self.model = model

    def build(
        self,
        core_memory: str = "",
        cwd: str | None = None,
    ) -> str:
        """按层级拼装 system prompt。"""
        layers: list[str] = []

        # L1: Identity
        layers.append(load_identity())

        # L2: Tool guidance
        layers.extend([
            MEMORY_GUIDANCE,
            SESSION_SEARCH_GUIDANCE,
            SKILLS_GUIDANCE,
            TOOL_USE_ENFORCEMENT,
        ])

        # L3: Memory block
        if core_memory.strip():
            layers.append(f"## 记忆\n\n{core_memory.strip()}")

        # L4: Project index
        project_index = build_project_index()
        if project_index:
            layers.append(project_index)

        # L5: Skills index
        skills_index = build_skills_index()
        if skills_index:
            layers.append(skills_index)

        # L6: Context files
        ctx = load_context_file(cwd)
        if ctx:
            layers.append(ctx)

        # L6: Model guidance
        layers.extend(build_model_guidance(self.model))

        # L7: Platform hints
        layers.append(PLATFORM_HINTS)

        # L8: Timestamp
        layers.append(build_timestamp())

        return "\n\n".join(layer for layer in layers if layer.strip())
