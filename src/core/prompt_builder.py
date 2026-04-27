"""分层 System Prompt 构建器。

参考 Hermes 的分层设计，Lampson 的 system prompt 分 8 层：

Layer 1  Identity         - SOUL.md 文件内容
Layer 2  Tool Guidance    - Memory、~/.lampson/skills 目录块、session_search、Skills 维护指引、Tool-use
Layer 3  Memory Block     - 核心记忆结构化文本
Layer 4  Project Index   - 项目索引（projects_index + project_context）
Layer 5  (reserved)
Layer 6  Context Files   - .lampson.md / AGENTS.md
Layer 7  Model Guidance   - 模型适配指引
Layer 8  Platform Hints   - CLI 环境提示
Layer 9  Timestamp        - 对话开始时间
"""

from __future__ import annotations

import os
import re
import time
from datetime import date
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
    "考虑将工作流记录到 ~/.lampson/skills/ 目录下以便复用。\n"
    "如果发现某个 skill 过时或错误，及时更新它。\n"
    "不维护的 skills 迟早会成为负担。"
)

TOOL_USE_ENFORCEMENT = (
    "执行具体任务时（写代码、查文件、改配置等），必须立即使用工具行动，不许只描述意图。"
    "回答提问、聊天、确认等场景直接回复即可，不需要硬塞工具调用。"
)

SESSION_CONTINUITY_GUIDANCE = (
    "当用户提到\"上次\"、\"继续\"、\"之前那个\"等暗示延续旧对话时，使用 session_load 恢复上一次对话历史。\n"
    "session_load 会把旧 session 的消息加载到当前对话中，你就能自然延续上下文。\n"
    "如果用户只是泛泛提问（如\"上次让我干啥\"），先调 session_load 加载最近 session，再回答。\n"
    "用 session_search 搜索跨多个 session 的历史内容。"
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

_skills_index_cache: tuple[frozenset[tuple[str, float]], str] | None = None


def write_skill_with_frontmatter(path: Path, meta: dict[str, Any], body: str) -> None:
    """将 YAML frontmatter + 正文写回 SKILL.md（正文不变，仅更新 meta 时用）。"""
    dump = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    path.write_text(f"---\n{dump}\n---\n{body}", encoding="utf-8")


def _ensure_skill_index_fields(path: Path) -> None:
    """若缺少 created_at / invocation_count，补填并写回（已有字段不覆盖）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    if not raw.strip():
        return
    meta, body = _parse_frontmatter(raw)
    changed = False
    today = date.today().isoformat()
    if "created_at" not in meta or meta.get("created_at") in (None, ""):
        meta["created_at"] = today
        changed = True
    if "invocation_count" not in meta:
        meta["invocation_count"] = 0
        changed = True
    if not changed:
        return
    write_skill_with_frontmatter(path, meta, body)


def _skill_md_paths_under_skills() -> list[Path]:
    if not SKILLS_DIR.exists():
        return []
    out: list[Path] = []
    for p in sorted(SKILLS_DIR.rglob("SKILL.md")):
        if ".archived" in p.parts:
            continue
        out.append(p)
    return out


def _skills_mtime_fingerprint(paths: list[Path]) -> frozenset[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for p in paths:
        try:
            items.append((str(p.resolve()), p.stat().st_mtime))
        except OSError:
            items.append((str(p), 0.0))
    return frozenset(items)


def build_skills_index() -> str:
    """扫描 ~/.lampson/skills 下 SKILL.md，生成注入 system prompt 的技能目录块。"""
    global _skills_index_cache
    paths = _skill_md_paths_under_skills()
    if not paths:
        _skills_index_cache = (frozenset(), "")
        return ""
    key_before = _skills_mtime_fingerprint(paths)
    if _skills_index_cache is not None and _skills_index_cache[0] == key_before:
        return _skills_index_cache[1]
    for p in paths:
        _ensure_skill_index_fields(p)
    paths = _skill_md_paths_under_skills()
    key = _skills_mtime_fingerprint(paths)
    lines: list[str] = [
        "## Skills（按需加载）",
        "以下是你已掌握的技能目录，每项包含触发词。",
        "**规则**：当用户输入匹配某个 skill 的触发词时，你必须在回复之前先调用 skill_view(name=\"技能名\") 加载全文，然后按 skill 指导执行任务。",
        "如果没有 skill 的触发词与当前任务相关，直接回答即可。",
        "",
    ]
    for path in paths:
        try:
            raw_file = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _body = _parse_frontmatter(raw_file)
        if meta:
            name = str(meta.get("name", "") or path.parent.name)
            desc = str(meta.get("description", ""))
            triggers = meta.get("triggers", [])
        else:
            name = path.parent.name
            desc = ""
            triggers = []
        if isinstance(triggers, str):
            tr_list = [triggers] if triggers.strip() else []
        else:
            tr_list = [str(t) for t in triggers] if isinstance(triggers, list) else []
        trig_part = f"（触发: {', '.join(tr_list)}）" if tr_list else ""
        lines.append(f"- **{name}**: {desc}{trig_part}")
    text = "\n".join(lines)
    _skills_index_cache = (key, text)
    return text


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
路径处理：~ 展开为 /Users/songyuhao/

# 远程机器
当用户提到"训练机器"、"远程机器"或特定机器名时，你需要通过 SSH 连接到远程机器执行命令。
本机 ~/.ssh/config 已配置好所有机器的 SSH 别名。在连接远程机器之前，**必须先用 project_context 工具加载 machines 项目**获取正确的 SSH 别名，不要猜测别名。
在远程机器上执行 find 命令时，务必加 -maxdepth 限制深度（如 -maxdepth 5），避免搜索 NFS/NAS 大目录导致超时。

# 技能与项目
复杂任务中若需要特定工作流，你会在单轮/规划阶段收到「匹配的技能」等检索注入内容，以注入文本为准，勿声称「未列出 skill 目录」。"""


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

        # L2: Tool guidance（技能目录插在记忆指引与 session_search 之间）
        l2: list[str] = [MEMORY_GUIDANCE]
        skills_block = build_skills_index()
        if skills_block.strip():
            l2.append(skills_block)
        l2.extend([
            SESSION_CONTINUITY_GUIDANCE,
            SKILLS_GUIDANCE,
            TOOL_USE_ENFORCEMENT,
        ])
        layers.extend(l2)

        # L3: Memory block
        if core_memory.strip():
            layers.append(f"## 记忆\n\n{core_memory.strip()}")

        # L4: Project index
        project_index = build_project_index()
        if project_index:
            layers.append(project_index)

        # L5: Context files
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
