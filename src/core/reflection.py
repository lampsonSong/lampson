"""任务完成后的反思与知识沉淀模块。

每次任务完成后，自动判断是否有值得持久化的知识：
- 项目事实 → projects/<名>.md（新建或更新）
- 新方法论 → skills/<名>/SKILL.md（新建或更新）
- 可复用代码 → learned_modules/<名>.py（新建或更新）
- 无价值 → 跳过
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from src.planning.steps import Plan, StepStatus

logger = logging.getLogger(__name__)

LAMIX_DIR = Path.home() / ".lamix"
SKILLS_DIR = LAMIX_DIR / "memory" / "skills"
PROJECTS_DIR = LAMIX_DIR / "memory" / "projects"
INFO_DIR = LAMIX_DIR / "memory" / "info"

# 反思冷却时间（秒）：距上次反思不足此间隔则跳过
_REFLECT_COOLDOWN = 300  # 5 分钟
_last_reflect_time: float = 0.0

# Skill 内容最短长度（字符），低于此不创建
_MIN_SKILL_CONTENT_LEN = 80

# 全局 LLM Client（由 Session 初始化时注入）
_llm_client: Any = None

# 全局 SkillIndex（由 Session 初始化时注入）
_skill_index: Any = None


def set_llm_client(client: Any) -> None:
    """由 Session 初始化时调用，注入当前 LLM Client。"""
    global _llm_client
    _llm_client = client


def set_skill_index(index: Any) -> None:
    """由 Session 初始化时调用，注入当前 SkillIndex，skill 变更后自动刷新索引。"""
    global _skill_index
    _skill_index = index


def _refresh_skill_index() -> None:
    """skill 文件变更后重建索引 + 刷新 tools 注册。静默失败。"""
    global _skill_index
    if _skill_index is None:
        return
    try:
        _skill_index.load_or_build()
        from src.core import skills_tools as skills_tools_reg
        from src.tools import session as session_tool
        current_session = session_tool.get_current_session()
        if current_session:
            skills_tools_reg.set_retrieval_indices(
                _skill_index, current_session.project_index
            )
            current_session.skill_index = _skill_index
            if current_session.agent:
                current_session.agent.skill_index = _skill_index
        logger.info('[反思] Skill 索引已重建')
    except Exception as e:
        logger.warning('[反思] Skill 索引重建失败: %s', e)


# ── 工具注册 ────────────────────────────────────────────────────────────────

REFLECT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reflect_and_learn",
        "description": (
            "任务完成后反思沉淀知识。分析本轮对话内容，判断是否有值得持久化的知识"
            "（skill、info、project、learned_module），并自动执行沉淀。"
            "适用场景：(1) 任务完成后 (2) 激活了某个技能并发现可改进之处 "
            "(3) 解决了复杂问题，过程中产生了可复用的方法或代码 "
            "(4) 探索了新项目，发现了项目信息"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "本轮任务的简要描述（例如：'帮用户调试API接口'、'优化数据库查询性能'）",
                },
                "execution_summary": {
                    "type": "string",
                    "description": "执行过程摘要（包括主要步骤、遇到的问题、解决方案等）",
                },
                "skill_activated": {
                    "type": "string",
                    "description": "本轮激活的技能名（如果有）",
                },
            },
            "required": ["goal", "execution_summary"],
        },
    },
}


def tool_reflect_runner(params: dict[str, Any]) -> str:
    """反思工具的执行函数，供 tools.py 调用。

    Args:
        params: 包含 goal, execution_summary, skill_activated 的参数字典

    Returns:
        沉淀结果摘要
    """
    global _llm_client

    if _llm_client is None:
        return "[提示] 反思功能未初始化（LLM Client 未注入），跳过沉淀"

    goal = params.get("goal", "")
    execution_summary = params.get("execution_summary", "")
    skill_activated = params.get("skill_activated")

    if not goal or not execution_summary:
        return "[提示] 反思参数不完整（goal 和 execution_summary 为必填），跳过沉淀"

    try:
        # 获取当前项目上下文（如果有）
        active_project = ""
        try:
            from src.tools import session as session_tool
            current_session = session_tool.get_current_session()
            if current_session and hasattr(current_session, 'active_project'):
                active_project = current_session.active_project or ""
        except Exception:
            pass

        # 调用反思逻辑
        learnings = reflect_and_learn(
            goal=goal,
            execution_summary=execution_summary,
            llm_client=_llm_client,
            skill_activated=skill_activated,
            recent_context="",
            active_project=active_project,
        )

        if not learnings:
            return "✓ 反思完成：本轮任务无需沉淀新知识"

        # 执行沉淀
        hints = execute_learnings(learnings)

        if hints:
            summary = "\n".join(f"  - {h}" for h in hints)
            return f"✓ 反思沉淀完成：\n{summary}"
        else:
            return "✓ 反思完成：沉淀操作已跳过（可能因为内容重复或不符合要求）"

    except Exception as e:
        logger.exception("反思工具执行异常")
        return f"[错误] 反思沉淀失败: {e}"


# ── 反思 Prompt ──────────────────────────────────────────────────────────────

REFLECT_PROMPT = """你是一个知识管理助手。请分析这次任务执行过程，判断是否有值得持久化的知识。

## 用户目标
{goal}

## 执行过程
{execution_summary}

## 已有 Skills
{existing_skills}

## 已有 Projects
{existing_projects}

## 已有 Info
{existing_info}

## 已有自我学习模块
{existing_modules}

请只输出一个 JSON 对象，不要其他文字。字段说明：
- "learnings": 数组。每项含：
  - "type": "project_create" | "project_update" | "skill_create" | "skill_update" | "info_create" | "info_update" | "module_create" | "module_update"
  - "target": 项目名、技能名、信息名或模块名（模块名用 snake_case）
  - "reason": 一句话说明为什么值得记录
  - "content": 要写入的正文内容

判断标准：
- project_create: 首次发现某个项目，记录基本信息（路径、技术栈、入口、配置）。仅当已有 Projects 列表中无该项目时使用
- project_update: 在已有项目中发现了新信息（新模块、新配置）或需要修正过时内容。仅当已有 Projects 列表中已有该项目时使用
- skill_create: 发现了一种可复用的操作方法，当前 skills 里没有覆盖的
- skill_update: 执行过程中发现某个已有 skill 的步骤不够、有错误，需要修正或补充
- info_create: 发现了通用的、项目无关的知识信息（如服务地址、工具用法、API文档），当前 info 中没有的
- info_update: 已有 info 需要修正或补充
- module_create: 发现了一段可复用的代码逻辑（如数据转换、日志解析、格式化、自动化脚本等），可作为独立 Python 模块沉淀。内容为完整的、可运行的 Python 代码
- module_update: 现有模块的代码有 bug、可以优化、或需要新增功能。仅当已有 Modules 列表中有该模块时使用
- 空数组: 简单查询、闲聊、或信息已经记录过

注意：
- 不要重复记录已有信息
- skill 的 content 是方法论（通用步骤），不是具体答案
- project_update 的 content 是增量信息，不是整个文件重写
- info 的 content 是项目无关的通用知识（如服务地址、工具配置、API说明等）
- module 的 content 是完整的、可运行的 Python 代码，禁止 import src 内部模块

示例：
{{"learnings": []}}
{{"learnings": [{{"type": "project_create", "target": "hermes", "reason": "首次探索了 hermes 项目", "content": "源码路径: ~/.hermes/hermes-agent/\\n入口: hermes_cli.main:main"}}]}}
{{"learnings": [{{"type": "module_create", "target": "log_parser", "reason": "连续手动写 awk 命令解析日志", "content": "# Log Parser\\n\\nTOOL_SCHEMA = {{\\n  'function': {{\\n    'name': 'learned_log_parser',\\n    'description': '解析日志文件，支持过滤级别和关键词',\\n    'parameters': {{\\n      'type': 'object',\\n      'properties': {{\\n        'path': {{'type': 'string', 'description': '日志文件路径'}},\\n        'level': {{'type': 'string', 'description': '日志级别，如 ERROR/WARN/INFO'}},\\n        'keyword': {{'type': 'string', 'description': '过滤关键词'}},\\n        'limit': {{'type': 'integer', 'description': '最多返回行数'}}\\n      }},\\n      'required': ['path']\\n    }}\\n  }}\\n}}\\n\\n\\ndef TOOL_RUNNER(params: dict) -> str:\\n    ...\\n"}}]}}"""


# ── 是否需要反思（LLM 判断）─────────────────────────────────────────────────



# ── 公开接口 ─────────────────────────────────────────────────────────────────


def should_reflect(
    plan: Plan | None = None,
    *,
    is_fast_path: bool = False,
    tool_call_count: int = 0,
    intent: str = "",
    skill_activated: str | None = None,
    user_input: str = "",
    llm_client: Any | None = None,
    recent_context: str = "",
) -> bool:
    """启发式判断本次任务是否值得反思。

    不额外调用 LLM，由 reflect_and_learn 内部的 LLM 调用自行判断是否沉淀。
    """
    import time
    global _last_reflect_time

    now = time.time()
    if now - _last_reflect_time < _REFLECT_COOLDOWN:
        return False

    # Fast Path 且没有工具调用 → 跳过（闲聊、简单查询）
    if is_fast_path and tool_call_count == 0:
        return False

    # 闲聊/简单查询 → 跳过
    if intent in ("chat", "info_query"):
        return False

    # Skill 被激活 → 值得反思
    if skill_activated:
        _last_reflect_time = now
        return True

    # 有工具调用 → 值得反思
    if tool_call_count >= 1:
        _last_reflect_time = now
        return True

    # 计划模式 3 步以上 → 值得反思
    if plan is not None and len(plan.steps) >= 3:
        _last_reflect_time = now
        return True

    return False



def reflect_and_learn(
    goal: str,
    execution_summary: str,
    llm_client: Any,
    skill_activated: str | None = None,
    recent_context: str = "",
    active_project: str = "",
) -> list[dict[str, Any]]:
    """执行反思，返回 learnings 列表。调用方负责后续的沉淀执行。"""
    existing_skills = _get_existing_skills_summary()
    existing_projects = _get_existing_projects_summary()
    existing_info = _get_existing_info_summary()
    existing_modules = _get_existing_modules_summary()

    # 构建反思上下文
    extra_context = ""
    # 如果有 skill 被激活，补充 skill 全文
    if skill_activated:
        skill_summary = _get_skill_full_content(skill_activated)
        if skill_summary:
            extra_context += "\n## 本轮激活的技能 [{}]\n{}".format(skill_activated, skill_summary)
    # 补充最近对话上下文（让 LLM 看到用户反馈）
    if recent_context and recent_context != "（无对话记录）":
        extra_context += "\n## 最近对话\n{}".format(recent_context)

    # 注入当前操作的项目（防止沉淀到错误的项目）
    if active_project:
        extra_context += "\n## 当前操作的项目\n{}（内容应沉淀到该项目的 project 文件，不要串项目）".format(active_project)

    prompt = REFLECT_PROMPT.format(
        goal=goal,
        execution_summary=execution_summary,
        existing_skills=existing_skills,
        existing_projects=existing_projects,
        existing_info=existing_info,
        existing_modules=existing_modules,
    ) + extra_context

    try:
        resp = llm_client.client.chat.completions.create(
            model=getattr(llm_client, "model", "glm-5"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2048,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return result.get("learnings", [])
    except Exception as e:
        logger.warning(f"反思 LLM 调用失败: {e}")
        return []


def execute_learnings(learnings: list[dict[str, Any]]) -> list[str]:
    """执行沉淀操作，返回人类可读的提示列表。"""
    hints: list[str] = []

    for learning in learnings:
        ltype = learning.get("type", "")
        target = learning.get("target", "")
        content = learning.get("content", "")
        reason = learning.get("reason", "")

        if ltype == "project_create":
            hint = _create_project(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "project_update":
            hint = _update_project(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "skill_create":
            hint = _create_skill(target, content, reason)
            if hint:
                hints.append(hint)
                _notify_user(f"📝 **Skill 新建**\n\n- 名称：{target}\n- 原因：{reason}")
                _refresh_skill_index()

        elif ltype == "skill_update":
            hint = _update_skill(target, content, reason)
            if hint:
                hints.append(hint)
                _notify_user(f"🔧 **Skill 更新**\n\n- 名称：{target}\n- 原因：{reason}")
                _refresh_skill_index()

        elif ltype == "info_create":
            hint = _create_info(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "info_update":
            hint = _update_info(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "module_create":
            hint = _create_module(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "module_update":
            hint = _update_module(target, content, reason)
            if hint:
                hints.append(hint)

        else:
            logger.warning(f"未知的学习类型: {ltype}，跳过")

    return hints


# ── 飞书通知 ────────────────────────────────────────────────────────────────


def _notify_user(message: str) -> None:
    """通过用户当前渠道发送通知（skill 变更时调用）。静默失败，不阻塞主流程。"""
    try:
        from src.tools import session as session_tool
        current_session = session_tool.get_current_session()
        if current_session and current_session.partial_sender:
            current_session.partial_sender(message)
            logger.info("[反思] 通知已通过当前渠道发送")
            return
    except Exception:
        pass
    # Fallback: print
    print(f"[反思] {message}", flush=True)


# ── 沉淀执行 ─────────────────────────────────────────────────────────────────


def _create_project(target: str, content: str, reason: str) -> str | None:
    """创建新的项目文件。如果已存在则降级为 update。"""
    if not target or not content:
        return None

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_file = PROJECTS_DIR / f"{target}.md"

    if project_file.exists():
        return _update_project(target, content, reason)

    project_file.write_text(content + f"\n\n> 创建于 {date.today()}\n", encoding="utf-8")
    logger.info(f"已创建项目: {target} ({reason})")
    return f"已创建项目: {target}（{reason}）"


def _update_project(target: str, content: str, reason: str) -> str | None:
    """追加内容到已有项目文件。"""
    if not target or not content:
        return None

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_file = PROJECTS_DIR / f"{target}.md"

    if not project_file.exists():
        return _create_project(target, content, reason)

    existing = project_file.read_text(encoding="utf-8")
    # 简单追加：如果已有内容里已经包含这段新内容，跳过
    if content.strip() in existing:
        return None

    updated = existing.rstrip() + f"\n\n## 更新 {date.today()}\n" + content.strip()
    project_file.write_text(updated + "\n", encoding="utf-8")
    logger.info(f"已更新项目: {target} ({reason})")
    return f"已更新项目: {target}（{reason}）"


def _get_existing_info_summary() -> str:
    """获取已有 info 列表摘要。"""
    if not INFO_DIR.exists():
        return "(无)"
    lines = []
    for info_file in sorted(INFO_DIR.glob("*.md")):
        content = info_file.read_text(encoding="utf-8")
        first_line = content.split("\n")[0].lstrip("# ").strip()
        lines.append(f"- {info_file.stem}: {first_line}")
    return "\n".join(lines) if lines else "(无)"


def _create_info(target: str, content: str, reason: str) -> str | None:
    """创建新的 info 文件。如果已存在则降级为 update。"""
    if not target or not content:
        return None

    INFO_DIR.mkdir(parents=True, exist_ok=True)
    info_file = INFO_DIR / f"{target}.md"

    if info_file.exists():
        return _update_info(target, content, reason)

    info_file.write_text(content + f"\n\n> 创建于 {date.today()}\n", encoding="utf-8")
    logger.info(f"已创建 Info: {target} ({reason})")
    return f"已创建 Info: {target}（{reason}）"


def _update_info(target: str, content: str, reason: str) -> str | None:
    """追加内容到已有 info 文件。"""
    if not target or not content:
        return None

    INFO_DIR.mkdir(parents=True, exist_ok=True)
    info_file = INFO_DIR / f"{target}.md"

    if not info_file.exists():
        return _create_info(target, content, reason)

    existing = info_file.read_text(encoding="utf-8")
    # 简单追加：如果已有内容里已经包含这段新内容，跳过
    if content.strip() in existing:
        return None

    updated = existing.rstrip() + f"\n\n## 更新 {date.today()}\n" + content.strip()
    info_file.write_text(updated + "\n", encoding="utf-8")
    logger.info(f"已更新 Info: {target} ({reason})")
    return f"已更新 Info: {target}（{reason}）"


def _create_skill(
    target: str, content: str, reason: str
) -> str | None:
    """创建新的 skill 目录和 SKILL.md。"""
    if not target or not content:
        return None

    skill_dir = SKILLS_DIR / target
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = f"---\ncreated_at: '{date.today()}'\ndescription: {content[:200]}\n---\n\n{content}"
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(frontmatter, encoding="utf-8")
    logger.info(f"已创建技能: {target} ({reason})")

    return f"已创建技能: {target}（以后遇到类似问题会自动使用）"


def _update_skill(
    target: str, content: str, reason: str
) -> str | None:
    """更新已有 skill。"""
    if not target:
        return None

    skill_dir = SKILLS_DIR / target
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        if content and len(content.strip()) >= _MIN_SKILL_CONTENT_LEN:
            return _create_skill(target, content, reason)
        return None

    existing = skill_file.read_text(encoding="utf-8")

    if content and _content_already_exists(existing, content):
        logger.debug(f"Skill {target} 已有相同信息，跳过更新")
        return None

    updated = existing
    if content:
        updated = existing + f"\n\n## 更新 ({datetime.now():%Y-%m-%d})\n{content}"

    if updated == existing:
        return None

    skill_file.write_text(updated, encoding="utf-8")
    logger.info(f"已更新技能: {target} ({reason})")
    return f"已更新技能: {target}（{reason}）"


def _create_module(target: str, content: str, reason: str) -> str | None:
    """创建新的 learned module。"""
    if not target or not content:
        return None

    safe_name = _sanitize_module_name(target)
    if not safe_name:
        logger.warning(f"模块名非法: {target}，跳过")
        return None

    MODULES_DIR = LAMIX_DIR / "learned_modules"
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    module_file = MODULES_DIR / f"{safe_name}.py"

    if module_file.exists():
        return _update_module(safe_name, content, reason)

    if _contains_blocked_import(content):
        logger.warning(f"模块 {target} 包含禁止的 import，跳过")
        return None

    module_file.write_text(content + "\n", encoding="utf-8")
    logger.info(f"已创建模块: {safe_name} ({reason})")
    return f"已创建模块: {safe_name}（{reason}）"


def _update_module(target: str, content: str, reason: str) -> str | None:
    """更新已有 learned module。"""
    if not target or not content:
        return None

    safe_name = _sanitize_module_name(target)
    if not safe_name:
        return None

    MODULES_DIR = LAMIX_DIR / "learned_modules"
    module_file = MODULES_DIR / f"{safe_name}.py"

    if not module_file.exists():
        return _create_module(safe_name, content, reason)

    if _contains_blocked_import(content):
        logger.warning(f"模块 {target} 包含禁止的 import，跳过")
        return None

    module_file.write_text(content + "\n", encoding="utf-8")
    logger.info(f"已更新模块: {safe_name} ({reason})")
    return f"已更新模块: {safe_name}（{reason}）"


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _sanitize_module_name(name: str) -> str:
    """将名称转换为合法的 Python 模块名。"""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    name = re.sub(r"^[^a-zA-Z]+", "", name)
    return name[:64] or "learned_module"


def _contains_blocked_import(code: str) -> bool:
    """检查模块代码中是否包含禁止的 import。"""
    BLOCKED_PREFIXES = ("from src", "import src")
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for prefix in BLOCKED_PREFIXES:
            if stripped.startswith(prefix):
                return True
    return False


def _content_already_exists(existing: str, new_content: str) -> bool:
    """检查新内容是否已在现有 skill 中。"""
    stripped = new_content.strip()
    # 简单检测：是否完全包含（允许空格差异）
    normalized_new = re.sub(r"\s+", "", stripped)
    normalized_existing = re.sub(r"\s+", "", existing)
    return normalized_new in normalized_existing


def _get_existing_skills_summary() -> str:
    """获取已有 skills 列表摘要。"""
    if not SKILLS_DIR.exists():
        return "(无)"
    lines = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if skill_dir.is_dir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                # 提取前两行作为摘要
                first_lines = skill_file.read_text(encoding="utf-8").split("\n")[:4]
                desc = " ".join(l.lstrip("-# ") for l in first_lines if l.strip())[:120]
                lines.append(f"- {skill_dir.name}: {desc}")
    return "\n".join(lines) if lines else "(无)"


def _get_skill_full_content(skill_name: str) -> str:
    """获取指定 skill 的完整内容。"""
    skill_file = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_file.exists():
        return ""
    return skill_file.read_text(encoding="utf-8")


def _get_existing_projects_summary() -> str:
    """获取已有 projects 列表摘要。"""
    if not PROJECTS_DIR.exists():
        return "(无)"
    lines = []
    for proj_file in sorted(PROJECTS_DIR.glob("*.md")):
        content = proj_file.read_text(encoding="utf-8")
        first_line = content.split("\n")[0].lstrip("# ").strip()
        lines.append(f"- {proj_file.stem}: {first_line}")
    return "\n".join(lines) if lines else "(无)"
def _get_existing_modules_summary() -> str:
    """获取已有 learned_modules 列表摘要。"""
    MODULES_DIR = LAMIX_DIR / "learned_modules"
    if not MODULES_DIR.exists():
        return "(无)"
    lines = []
    for mod_file in sorted(MODULES_DIR.glob("*.py")):
        # 提取 docstring 或第一段注释
        content = mod_file.read_text(encoding="utf-8")
        desc = content.split('"""')[1].split("\n")[0].strip() if '"""' in content else content.split("\n")[0].lstrip("# ").strip()
        lines.append(f"- {mod_file.stem}: {desc[:80]}")
    return "\n".join(lines) if lines else "(无)"


def format_execution_summary(plan: Plan) -> str:
    """将 Plan 对象格式化为文字摘要。"""
    if plan is None:
        return "(无计划，纯 Fast Path)"
    lines = [f"计划: {plan.goal} (状态: {plan.status.value})"]
    for step in plan.steps:
        icon = "✓" if step.status == StepStatus.done else "✗" if step.status == StepStatus.failed else "○"
        lines.append(f"  {icon} {step.action}")
    return "\n".join(lines)


# ── Skill 自动合并 ─────────────────────────────────────────────────────────

