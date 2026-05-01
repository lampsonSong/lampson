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

LAMPSON_DIR = Path.home() / ".lampson"
SKILLS_DIR = LAMPSON_DIR / "skills"
PROJECTS_DIR = LAMPSON_DIR / "projects"

# 反思冷却时间（秒）：距上次反思不足此间隔则跳过
_REFLECT_COOLDOWN = 300  # 5 分钟
_last_reflect_time: float = 0.0

# Skill 内容最短长度（字符），低于此不创建
_MIN_SKILL_CONTENT_LEN = 80
# 新建 skill 至少需要多少个 trigger
_MIN_TRIGGER_COUNT = 1

# 全局 LLM Client（由 Session 初始化时注入，供 _auto_consolidate 使用）
_llm_client: Any = None


def set_llm_client(client: Any) -> None:
    """由 Session 初始化时调用，注入当前 LLM Client。"""
    global _llm_client
    _llm_client = client


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

## 已有自我学习模块
{existing_modules}

请只输出一个 JSON 对象，不要其他文字。字段说明：
- "learnings": 数组。每项含：
  - "type": "project_create" | "project_update" | "skill_create" | "skill_update" | "module_create" | "module_update"
  - "target": 项目名、技能名或模块名（模块名用 snake_case）
  - "reason": 一句话说明为什么值得记录
  - "content": 要写入的正文内容
  - "triggers": 字符串数组（skill_create 和 skill_update 需提供；module 类型填空数组）

判断标准：
- project_create: 首次发现某个项目，记录基本信息（路径、技术栈、入口、配置）。仅当已有 Projects 列表中无该项目时使用
- project_update: 在已有项目中发现了新信息（新模块、新配置）或需要修正过时内容。仅当已有 Projects 列表中已有该项目时使用
- skill_create: 发现了一种可复用的操作方法，当前 skills 里没有覆盖的
- skill_update: 执行过程中发现某个已有 skill 的步骤不够、有错误，或者用户用了一种新表达方式触发了该 skill
- module_create: 发现了一段可复用的代码逻辑（如数据转换、日志解析、格式化、自动化脚本等），可作为独立 Python 模块沉淀。内容为完整的 Python 代码，包含：
  * TOOL_SCHEMA: OpenAI function calling schema（name 必须以 learned_ 开头）
  * TOOL_RUNNER: 执行函数，签名为 (params: dict) -> str
  * 禁止 import src 内部模块，只能用标准库和已安装的第三方库
- module_update: 现有模块的代码有 bug、可以优化、或需要新增功能。仅当已有 Modules 列表中有该模块时使用
- 空数组: 简单查询、闲聊、或信息已经记录过

注意：
- 不要重复记录已有信息
- skill 的 content 是方法论（通用步骤），不是具体答案
- project_update 的 content 是增量信息，不是整个文件重写
- module 的 content 是完整的、可运行的 Python 代码
- 新建 skill 的 triggers 至少 1 个
- 种子模式：如果知识值得记录但内容还不够丰富，可以只写简短描述 + 1 个触发词

示例：
{{"learnings": []}}
{{"learnings": [{{"type": "project_create", "target": "hermes", "reason": "首次探索了 hermes 项目", "content": "源码路径: ~/.hermes/hermes-agent/\\n入口: hermes_cli.main:main", "triggers": []}}]}}
{{"learnings": [{{"type": "module_create", "target": "log_parser", "reason": "连续手动写 awk 命令解析日志", "content": "# Log Parser\\n\\nTOOL_SCHEMA = {{\\n  'function': {{\\n    'name': 'learned_log_parser',\\n    'description': '解析日志文件，支持过滤级别和关键词',\\n    'parameters': {{\\n      'type': 'object',\\n      'properties': {{\\n        'path': {{'type': 'string', 'description': '日志文件路径'}},\\n        'level': {{'type': 'string', 'description': '日志级别，如 ERROR/WARN/INFO'}},\\n        'keyword': {{'type': 'string', 'description': '过滤关键词'}},\\n        'limit': {{'type': 'integer', 'description': '最多返回行数'}}\\n      }},\\n      'required': ['path']\\n    }}\\n  }}\\n}}\\n\\n\\ndef TOOL_RUNNER(params: dict) -> str:\\n    ...\\n", "triggers": []}}]}}"""


# ── 公开接口 ─────────────────────────────────────────────────────────────────

# 用户纠正信号关键词
_CORRECTION_SIGNALS = (
    "不对", "错了", "不是这样的", "应该是", "不应该", "不是", "你搞错",
    "说错了", "理解错了", "搞反了", "反了", "为什么不是", "我想说的是",
    "我的意思是", "不对吧", "弄错了", "搞混了",
)


def _detect_user_correction(user_input: str) -> bool:
    """检测用户输入中是否包含纠正信号。"""
    text = user_input.lower()
    return any(sig in text for sig in _CORRECTION_SIGNALS)


def should_reflect(
    plan: Plan | None = None,
    *,
    is_fast_path: bool = False,
    tool_call_count: int = 0,
    intent: str = "",
    skill_activated: str | None = None,
    user_input: str = "",
) -> bool:
    """判断是否应该触发反思。"""
    import time
    global _last_reflect_time

    now = time.time()
    if now - _last_reflect_time < _REFLECT_COOLDOWN:
        return False

    # 用户纠正信号：始终触发反思（最高优先级）
    if user_input and _detect_user_correction(user_input):
        return True

    # Skill 被激活时：用户可能在使用或纠正 skill，始终值得反思
    if skill_activated:
        return True

    # Fast Path 且没有工具调用 → 跳过（1 个工具也值得反思）
    if is_fast_path and tool_call_count == 0:
        return False

    # 闲聊/简单查询 → 跳过
    if intent in ("chat", "info_query"):
        return False

    # plan 为空（说明不是计划模式）→ 用 tool_call_count 判断
    if plan is None:
        return tool_call_count >= 3

    # 计划未完成 → 跳过
    if plan.status != StepStatus.done:
        # 但如果有步骤失败了并且有步骤成功了，仍然值得反思踩坑
        done_steps = [s for s in plan.steps if s.status == StepStatus.done]
        failed_steps = [s for s in plan.steps if s.status == StepStatus.failed]
        if not (done_steps and failed_steps):
            return False

    # 计划 3 步以上 → 必须反思
    if len(plan.steps) >= 3:
        return True

    # 有失败步骤且部分成功 → 值得反思（踩坑经验）
    done_steps = [s for s in plan.steps if s.status == StepStatus.done]
    failed_steps = [s for s in plan.steps if s.status == StepStatus.failed]
    if done_steps and failed_steps:
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
    import time
    global _last_reflect_time

    existing_skills = _get_existing_skills_summary()
    existing_projects = _get_existing_projects_summary()
    existing_modules = _get_existing_modules_summary()

    # 构建反思上下文
    extra_context = ""
    # 1. 如果有 skill 被激活，补充 skill 全文
    if skill_activated:
        skill_summary = _get_skill_full_content(skill_activated)
        if skill_summary:
            extra_context += "\n## 本轮激活的技能 [{}]\n{}".format(skill_activated, skill_summary)
    # 2. 补充最近对话上下文（让 LLM 看到用户反馈）
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
        existing_modules=existing_modules,
    ) + extra_context

    try:
        resp = llm_client.client.chat.completions.create(
            model=llm_client.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = resp.choices[0].message.content or ""
        data = _extract_json(raw)
        if data is None:
            logger.debug("反思结果无法解析 JSON，跳过")
            return []

        learnings = data.get("learnings", [])
        _last_reflect_time = time.time()
        return learnings

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
        triggers = learning.get("triggers", [])

        if ltype == "project_create":
            hint = _create_project(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "project_update":
            hint = _update_project(target, content, reason)
            if hint:
                hints.append(hint)

        elif ltype == "skill_create":
            hint = _create_skill(target, content, reason, triggers)
            if hint:
                hints.append(hint)

        elif ltype == "skill_update":
            hint = _update_skill(target, content, reason, triggers)
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


# ── 沉淀执行 ─────────────────────────────────────────────────────────────────

def _create_project(target: str, content: str, reason: str) -> str | None:
    """创建新的项目文件。如果已存在则降级为 update。"""
    if not target or not content:
        return None

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_file = PROJECTS_DIR / f"{target}.md"

    if project_file.exists():
        logger.debug(f"项目 {target} 已存在，降级为 update")
        return _update_project(target, content, reason)

    updated = f"# {target}\n\n{content}"
    project_file.write_text(updated, encoding="utf-8")
    logger.info(f"已创建项目信息: {target} ({reason})")
    return f"已记录项目信息: {target}"


def _update_project(target: str, content: str, reason: str) -> str | None:
    """更新已有项目文件：追加日期分节的增量内容。如果不存在则降级为 create。"""
    if not target or not content:
        return None

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_file = PROJECTS_DIR / f"{target}.md"

    if not project_file.exists():
        logger.debug(f"项目 {target} 不存在，降级为 create")
        return _create_project(target, content, reason)

    existing = project_file.read_text(encoding="utf-8")
    if _content_already_exists(existing, content):
        logger.debug(f"项目 {target} 已有相同信息，跳过")
        return None

    updated = existing + f"\n\n## {datetime.now():%Y-%m-%d}\n{content}"
    project_file.write_text(updated, encoding="utf-8")
    logger.info(f"已更新项目信息: {target} ({reason})")
    return f"已更新项目信息: {target}（{reason}）"


def _create_skill(
    target: str, content: str, reason: str, triggers: list[str]
) -> str | None:
    """创建新的 skill 文件。"""
    if not target or not content:
        return None

    if len(content.strip()) < _MIN_SKILL_CONTENT_LEN:
        logger.debug(f"Skill {target} 内容太短，跳过创建")
        return None
    if len(triggers) < _MIN_TRIGGER_COUNT:
        logger.debug(f"Skill {target} trigger 不足，跳过创建")
        return None

    skill_dir = SKILLS_DIR / target
    skill_file = skill_dir / "SKILL.md"
    if skill_file.exists():
        return _update_skill(target, content, reason, triggers)

    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = yaml.dump(
        {"name": target, "description": reason, "triggers": triggers},
        allow_unicode=True,
        default_flow_style=False,
    ).strip()

    skill_content = f"---\n{frontmatter}\n---\n\n{content}"
    skill_file.write_text(skill_content, encoding="utf-8")
    logger.info(f"已创建技能: {target} ({reason})")

    _auto_consolidate(target)
    return f"已创建技能: {target}（以后遇到类似问题会自动使用）"


def _update_skill(
    target: str, content: str, reason: str, triggers: list[str]
) -> str | None:
    """更新已有 skill。支持只追加 triggers（content 可为空）。"""
    if not target:
        return None

    skill_dir = SKILLS_DIR / target
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        if content and len(content.strip()) >= _MIN_SKILL_CONTENT_LEN and len(triggers) >= _MIN_TRIGGER_COUNT:
            return _create_skill(target, content, reason, triggers)
        return None

    existing = skill_file.read_text(encoding="utf-8")

    if content and _content_already_exists(existing, content):
        logger.debug(f"Skill {target} 已有相同信息，跳过更新")
        if triggers:
            updated = _merge_triggers(existing, triggers)
            if updated != existing:
                skill_file.write_text(updated, encoding="utf-8")
                return f"已更新技能触发词: {target}"
        return None

    updated = existing
    if content:
        updated = existing + f"\n\n## 更新 ({datetime.now():%Y-%m-%d})\n{content}"

    if triggers:
        updated = _merge_triggers(updated, triggers)

    if updated == existing:
        return None

    skill_file.write_text(updated, encoding="utf-8")
    logger.info(f"已更新技能: {target} ({reason})")
    return f"已更新技能: {target}（{reason}）"


def _create_module(target: str, content: str, reason: str) -> str | None:
    """创建新的 learned module。"""
    if not target or not content:
        return None

    # 名称合法性
    safe_name = _sanitize_module_name(target)
    if not safe_name:
        logger.warning(f"模块名非法: {target}，跳过")
        return None

    # 安全校验：禁止 import src
    if _contains_blocked_import(content):
        logger.warning(f"模块 {target} 包含禁止的 import，跳过")
        return None

    try:
        from src.tools.learned_modules import write_module
        result = write_module(safe_name, content)
        if result.startswith("[错误]"):
            logger.warning(f"模块 {target} 写入失败: {result}")
            return None

        # 注册为工具
        from src.tools import learned_modules
        registered = learned_modules.scan_and_register()
        tool_names = [s["function"]["name"] for s in registered if safe_name in s["function"]["name"]]
        tool_hint = f"，已注册为工具 {tool_names[0]}" if tool_names else ""

        logger.info(f"已创建自我学习模块: {safe_name} ({reason})")
        return f"已创建自我学习模块: {safe_name}（{reason}）{tool_hint}"
    except Exception as e:
        logger.warning(f"模块 {target} 创建失败: {e}")
        return None


def _update_module(target: str, content: str, reason: str) -> str | None:
    """更新已有的 learned module。"""
    if not target:
        return None

    safe_name = _sanitize_module_name(target)
    if not safe_name:
        return None

    try:
        from src.tools import learned_modules
        from src.core import tools as tool_registry

        # 读取现有内容（用于去重）
        existing_code = learned_modules.get_module_code(safe_name)
        if not existing_code:
            logger.debug(f"模块 {target} 不存在，降级为 create")
            return _create_module(target, content, reason)

        if _content_already_exists(existing_code, content):
            logger.debug(f"模块 {target} 内容相同，跳过更新")
            return None

        # 安全校验
        if _contains_blocked_import(content):
            logger.warning(f"模块 {target} 包含禁止的 import，跳过")
            return None

        result = learned_modules.write_module(safe_name, content)
        if result.startswith("[错误]"):
            logger.warning(f"模块 {target} 更新失败: {result}")
            return None

        # 重新注册（清除旧工具再注册新工具）
        # 先移除旧的（同名会被覆盖，tool_registry.register_external 是幂等的）
        learned_modules.scan_and_register()

        logger.info(f"已更新自我学习模块: {safe_name} ({reason})")
        return f"已更新自我学习模块: {safe_name}（{reason}）"
    except Exception as e:
        logger.warning(f"模块 {target} 更新失败: {e}")
        return None


def _sanitize_module_name(name: str) -> str | None:
    """将用户提供的名称转换为合法的 snake_case 模块名。"""
    import re
    # 只保留字母数字下划线
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip())
    # 必须以字母开头
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    # 至少一个有效字符
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", cleaned):
        return None
    return cleaned


def _contains_blocked_import(code: str) -> bool:
    """检查代码是否包含禁止的 import。"""
    import re
    BLOCKED = frozenset({"src", "src.core", "src.tools", "src.feishu",
                          "src.skills", "src.memory", "src.platforms",
                          "src.selfupdate", "src.planning"})
    for line in code.splitlines():
        stripped = line.strip()
        m = re.match(r"^from\s+(\S+)", stripped)
        if m and m.group(1).split(".")[0] in BLOCKED:
            return True
        m = re.match(r"^import\s+(\S+)", stripped)
        if m and m.group(1).split(".")[0] in BLOCKED:
            return True
    return False


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _get_skill_full_content(skill_name: str) -> str:
    """读取指定 skill 的完整内容（用于反思上下文）。"""
    skill_file = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_file.exists():
        return ""
    try:
        return skill_file.read_text(encoding="utf-8")[:3000]
    except OSError:
        return ""


def _get_existing_modules_summary() -> str:
    """获取已有 learned modules 的摘要列表。"""
    try:
        from src.tools import learned_modules
        modules = learned_modules.list_modules()
    except Exception:
        return "（无）"

    if not modules:
        return "（无）"
    lines = []
    for m in modules:
        tool_flag = " [工具]" if m["registered_as_tool"] == "True" else ""
        lines.append(f"- {m['name']}{tool_flag}")
    return "\n".join(lines) if lines else "（无）"


def _extract_keywords(text: str) -> set[str]:
    """从文本中提取关键词集合（用于去重比较）。"""
    import re as _re
    cleaned = _re.sub(r"[#|\-*\n\r]", " ", text)
    words = {w.strip() for w in cleaned.split() if len(w.strip()) >= 2}
    return words


def _content_already_exists(existing: str, new_content: str) -> bool:
    """去重检查：基于关键词集合相似度判断新内容是否已存在。"""
    core = new_content.strip()[:100]
    if not core:
        return True
    if core in existing:
        return True
    existing_kw = _extract_keywords(existing)
    new_kw = _extract_keywords(new_content)
    if not new_kw:
        return True
    intersection = existing_kw & new_kw
    union = existing_kw | new_kw
    if not union:
        return True
    similarity = len(intersection) / len(union)
    return similarity >= 0.6


def _merge_triggers(skill_content: str, new_triggers: list[str]) -> str:
    """合并新 triggers 到已有的 SKILL.md frontmatter。"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_content, re.DOTALL)
    if not fm_match:
        return skill_content

    try:
        meta = yaml.safe_load(fm_match.group(1)) or {}
    except yaml.YAMLError:
        return skill_content

    existing_triggers = meta.get("triggers", [])
    if isinstance(existing_triggers, str):
        existing_triggers = [existing_triggers]

    merged = list(set(existing_triggers + new_triggers))
    meta["triggers"] = merged

    new_fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    body = skill_content[fm_match.end():]

    return f"---\n{new_fm}\n---\n\n{body}"


def _get_existing_skills_summary() -> str:
    """获取已有 skills 的摘要列表。"""
    from src.skills.manager import load_all_skills

    try:
        skills = load_all_skills()
    except Exception:
        return "（无）"

    if not skills:
        return "（无）"
    lines = []
    for name, skill in skills.items():
        desc = getattr(skill, 'description', '') or ""
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


def _get_existing_projects_summary() -> str:
    """获取已有 projects 的摘要列表。"""
    if not PROJECTS_DIR.exists():
        return "（无）"
    files = list(PROJECTS_DIR.rglob("*.md"))
    if not files:
        return "（无）"
    lines = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8").strip()
            title = f.stem
            preview = content[:100].replace("\n", " ")
            lines.append(f"- {title}: {preview}...")
        except OSError:
            pass
    return "\n".join(lines) if lines else "（无）"


def format_execution_summary(plan: Plan) -> str:
    """从 Plan 对象构建执行摘要文本。"""
    lines = []
    for step in plan.steps:
        if step.status == StepStatus.skipped:
            lines.append(f"步骤{step.id}: [已跳过]")
            continue
        status = "完成" if step.status == StepStatus.done else "失败"
        lines.append(f"步骤{step.id} ({status}): {step.action}")
        if step.args:
            args_str = ", ".join(f"{k}={v}" for k, v in step.args.items())
            lines.append(f"  参数: {args_str}")
        if step.result:
            result_preview = step.result[:500]
            lines.append(f"  结果: {result_preview}")
    return "\n".join(lines)


def format_fast_path_summary(user_input: str, tool_history: list[dict]) -> str:
    """从 Fast Path 工具调用历史构建执行摘要。"""
    lines = [f"用户请求: {user_input}"]
    for i, tc in enumerate(tool_history):
        name = tc.get("name", "?")
        args = tc.get("args", {})
        result = tc.get("result", "")
        result_preview = result[:500] if result else "(空)"
        lines.append(f"工具调用{i+1}: {name}({args}) → {result_preview}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    """从文本中提取 JSON。"""
    cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL)

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


# ── 自动合并 ──────────────────────────────────────────────────────────────

def _auto_consolidate(new_skill_name: str) -> None:
    """新建 skill 后自动用 LLM 分析并合并重复/耦合的 skill。"""
    global _llm_client
    if _llm_client is None:
        logger.debug("自动合并跳过：LLM Client 未注入")
        return

    from src.skills.manager import load_all_skills, consolidate_skills, execute_consolidation

    skills = load_all_skills()
    if len(skills) < 2:
        return

    actions, analysis = consolidate_skills(skills, _llm_client)
    if not actions:
        logger.debug(f"自动合并：{analysis}")
        return

    result = execute_consolidation(actions)
    logger.info(f"自动合并完成：{result}")
