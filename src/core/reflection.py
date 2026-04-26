"""任务完成后的反思与知识沉淀模块。

每次任务完成后，自动判断是否有值得持久化的知识：
- 项目事实 → projects/<名>.md
- 新方法论 → skills/<名>/SKILL.md（新建）
- 方法论改进 → skills/<名>/SKILL.md（更新）
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
_MIN_SKILL_CONTENT_LEN = 200
# 新建 skill 至少需要多少个 trigger
_MIN_TRIGGER_COUNT = 3

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

请只输出一个 JSON 对象，不要其他文字。字段说明：
- "should_learn": 布尔。是否有值得记录的内容
- "learnings": 数组。每项含：
  - "type": "project" | "skill_create" | "skill_update"
  - "target": 项目名或技能名
  - "reason": 一句话说明为什么值得记录
  - "content": 要写入的正文内容（markdown 格式）
  - "triggers": 字符串数组（仅 skill_create 时需要，至少 3 个触发词）

判断标准：
- project: 记录具体项目的事实信息（路径、技术栈、配置），如 "hermes 项目源码在 ~/.hermes/hermes-agent/"
- skill_create: 发现了一种可复用的操作方法，当前 skills 里没有覆盖的
- skill_update: 执行过程中发现某个已有 skill 的步骤不够或有错误
- should_learn=false: 简单查询、闲聊、或信息已经记录过

注意：
- 不要重复记录已有信息
- skill 的 content 应该是方法论（通用步骤），不是具体答案（具体路径）
- triggers 应该覆盖用户未来可能的表达方式（中英文都要考虑）
- 新建 skill 的 triggers 至少 3 个

示例：
{{"should_learn": false, "learnings": []}}
{{"should_learn": true, "learnings": [{{"type": "project", "target": "hermes", "reason": "首次探索了 hermes 项目", "content": "源码路径: ~/.hermes/hermes-agent/\\n入口: hermes_cli.main:main", "triggers": []}}]}}"""


# ── 公开接口 ─────────────────────────────────────────────────────────────────

def should_reflect(
    plan: Plan | None = None,
    *,
    is_fast_path: bool = False,
    tool_call_count: int = 0,
    intent: str = "",
) -> bool:
    """判断是否应该触发反思。"""
    import time
    global _last_reflect_time

    now = time.time()
    if now - _last_reflect_time < _REFLECT_COOLDOWN:
        return False

    # Fast Path 且只用了 0-1 个工具 → 跳过
    if is_fast_path and tool_call_count <= 1:
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
) -> list[dict[str, Any]]:
    """执行反思，返回 learnings 列表。调用方负责后续的沉淀执行。"""
    import time
    global _last_reflect_time

    existing_skills = _get_existing_skills_summary()
    existing_projects = _get_existing_projects_summary()

    prompt = REFLECT_PROMPT.format(
        goal=goal,
        execution_summary=execution_summary,
        existing_skills=existing_skills,
        existing_projects=existing_projects,
    )

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

        if not data.get("should_learn"):
            _last_reflect_time = time.time()
            return []

        learnings = data.get("learnings", [])
        _last_reflect_time = time.time()
        return learnings

    except Exception as e:
        logger.warning(f"反思 LLM 调用失败: {e}")
        return []


def execute_learnings(learnings: list[dict[str, Any]]) -> list[str]:
    """执行沉淀操作，返回人类可读的提示列表。"""
    from src.core import tools as tool_registry

    hints: list[str] = []

    for learning in learnings:
        ltype = learning.get("type", "")
        target = learning.get("target", "")
        content = learning.get("content", "")
        reason = learning.get("reason", "")
        triggers = learning.get("triggers", [])

        if ltype == "project":
            hint = _save_to_project(target, content, reason)
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

    return hints


# ── 沉淀执行 ─────────────────────────────────────────────────────────────────

def _save_to_project(target: str, content: str, reason: str) -> str | None:
    """将项目信息追加到 projects/<项目名>.md。"""
    if not target or not content:
        return None

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_file = PROJECTS_DIR / f"{target}.md"

    if project_file.exists():
        existing = project_file.read_text(encoding="utf-8")
        # 去重：如果核心内容已存在则跳过
        if _content_already_exists(existing, content):
            logger.debug(f"项目 {target} 已有相同信息，跳过")
            return None
        updated = existing + f"\n\n## {datetime.now():%Y-%m-%d}\n{content}"
    else:
        updated = f"# {target}\n\n{content}"

    project_file.write_text(updated, encoding="utf-8")
    logger.info(f"已记录项目信息: {target} ({reason})")
    return f"已记录项目信息: {target}"


def _create_skill(
    target: str, content: str, reason: str, triggers: list[str]
) -> str | None:
    """创建新的 skill 文件。"""
    if not target or not content:
        return None

    # 质量门槛
    if len(content.strip()) < _MIN_SKILL_CONTENT_LEN:
        logger.debug(f"Skill {target} 内容太短（{len(content)}字），跳过创建")
        return None
    if len(triggers) < _MIN_TRIGGER_COUNT:
        logger.debug(f"Skill {target} trigger 不足 {_MIN_TRIGGER_COUNT} 个，跳过创建")
        return None

    # 检查是否已存在
    skill_dir = SKILLS_DIR / target
    skill_file = skill_dir / "SKILL.md"
    if skill_file.exists():
        # 已存在，降级为更新
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

    # 自动合并检查
    _auto_consolidate(target)
    return f"已创建技能: {target}（以后遇到类似问题会自动使用）"


def _update_skill(
    target: str, content: str, reason: str, triggers: list[str]
) -> str | None:
    """更新已有 skill。"""
    if not target or not content:
        return None

    skill_dir = SKILLS_DIR / target
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        # 不存在，降级为创建（但可能内容太短不过门槛）
        if len(content.strip()) >= _MIN_SKILL_CONTENT_LEN and len(triggers) >= _MIN_TRIGGER_COUNT:
            return _create_skill(target, content, reason, triggers)
        return None

    existing = skill_file.read_text(encoding="utf-8")

    # 去重
    if _content_already_exists(existing, content):
        logger.debug(f"Skill {target} 已有相同信息，跳过更新")
        return None

    # 追加新内容
    updated = existing + f"\n\n## 更新 ({datetime.now():%Y-%m-%d})\n{content}"

    # 合并 triggers（如果新 triggers 不为空）
    if triggers:
        updated = _merge_triggers(updated, triggers)

    skill_file.write_text(updated, encoding="utf-8")
    logger.info(f"已更新技能: {target} ({reason})")
    return f"已更新技能: {target}（{reason}）"


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _content_already_exists(existing: str, new_content: str) -> bool:
    """简单去重：检查新内容的核心片段是否已存在。"""
    # 取新内容的前 100 字符做模糊匹配
    core = new_content.strip()[:100]
    if not core:
        return True
    return core in existing


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

    # 合并，去重
    merged = list(set(existing_triggers + new_triggers))
    meta["triggers"] = merged

    new_fm = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    body = skill_content[fm_match.end():]

    return f"---\n{new_fm}\n---\n\n{body}"


def _get_existing_skills_summary() -> str:
    """获取已有 skills 的摘要列表。"""
    from src.core.skills_tools import _iter_skills
    skills = _iter_skills()
    if not skills:
        return "（无）"
    lines = []
    for s in skills:
        desc = s.get("description", "") or ""
        lines.append(f"- {s['name']}: {desc}")
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
    # 去掉 <think...</think > 思维链
    cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL)

    # 尝试提取 ```json ... ``` 包裹
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()

    # 尝试直接找 { ... }
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

    # 重新加载所有 skills
    from src.skills.manager import load_all_skills, consolidate_skills, execute_consolidation

    skills = load_all_skills()
    if len(skills) < 2:
        return

    actions, analysis = consolidate_skills(skills, _llm_client)
    if not actions:
        logger.debug(f"自动合并：{analysis}")
        return

    # 直接执行，不需要确认
    result = execute_consolidation(actions)
    logger.info(f"自动合并完成：{result}")
