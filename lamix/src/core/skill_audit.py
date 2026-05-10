"""Skill 执行审计器：确保 LLM 实际执行了 skill 中定义的必须步骤。

工作原理：
1. LLM 调用 skill(action='view') 加载技能时，审计器解析 skill body 中的编号步骤
2. 在 tool loop 中记录所有已执行的工具调用和 LLM 行为
3. 任务结束时（tool loop 返回结果前），对比 skill 步骤 vs 实际执行
4. 如果有遗漏步骤，生成提醒追加到结果中，让 LLM 补上

步骤解析规则：
- 匹配 "数字. **名称**" 格式的编号步骤（如 "1. **语法检查**"）
- 每个编号步骤视为一个 required checkpoint
- 通过关键词匹配判断步骤是否已完成
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AuditStep:
    """一个需要审计的步骤。"""
    index: int          # 步骤编号（从 1 开始）
    title: str          # 步骤标题（如 "语法检查"）
    full_line: str      # 原始行
    completed: bool = False
    evidence: str = ""  # 证明步骤已完成的证据


@dataclass
class SkillAudit:
    """一次 skill 执行的审计记录。"""
    skill_name: str
    steps: list[AuditStep] = field(default_factory=list)
    tool_calls: list[dict[str, str]] = field(default_factory=list)  # {name, args_preview}
    llm_outputs: list[str] = field(default_factory=list)  # LLM 文本输出的摘要

    @property
    def incomplete_steps(self) -> list[AuditStep]:
        return [s for s in self.steps if not s.completed]


# 模块级状态
_active_audit: SkillAudit | None = None

# 步骤编号匹配：1. **标题** 或 1. **标题**：描述
_STEP_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*")

# 每个步骤的关键词映射（用于匹配 tool_calls 和 llm_outputs）
# key 是步骤标题关键词，value 是能证明该步骤已完成的工具/行为关键词
_STEP_KEYWORDS: dict[str, list[str]] = {
    # code-writing skill
    "语法检查": ["py_compile", "python -m py_compile", "--check", "syntax", "语法"],
    "测试用例": ["pytest", "test_", "unittest", "测试", "test case"],
    "模拟场景": ["模拟", "端到端", "e2e", "场景", "scenario"],
    # debug skill
    "复现": ["复现", "reproduce", "跑一遍", "运行"],
    "分析": ["分析", "analyze", "traceback", "定位"],
    # 通用
    "理清需求": ["需求", "确认", "理解"],
}


def get_active_audit() -> SkillAudit | None:
    """返回当前活跃的审计记录。"""
    return _active_audit


def start_audit(skill_name: str, skill_body: str) -> SkillAudit | None:
    """解析 skill body，启动审计跟踪。

    Args:
        skill_name: 技能名称
        skill_body: SKILL.md 的正文部分（不含 frontmatter）

    Returns:
        SkillAudit 对象，如果没有可解析的步骤则返回 None
    """
    global _active_audit

    steps = _parse_steps(skill_body)
    if not steps:
        logger.debug(f"Skill {skill_name} 没有编号步骤，跳过审计")
        return None

    _active_audit = SkillAudit(
        skill_name=skill_name,
        steps=steps,
    )
    logger.info(f"审计启动: {skill_name}，共 {len(steps)} 个步骤")
    return _active_audit


def end_audit() -> str | None:
    """结束审计，检查是否有遗漏步骤。

    Returns:
        如果有遗漏步骤，返回提醒文本；否则返回 None。
        调用方应将提醒文本追加到 LLM 的结果中。
    """
    global _active_audit

    if _active_audit is None:
        return None

    audit = _active_audit

    # 基于已收集的 tool_calls 和 llm_outputs 判断步骤完成情况
    _evaluate_completion(audit)

    incomplete = audit.incomplete_steps
    if not incomplete:
        logger.info(f"审计通过: {audit.skill_name}，所有步骤已完成")
        _active_audit = None
        return None

    # 生成提醒
    step_list = "\n".join(
        f"  {s.index}. {s.title}"
        for s in incomplete
    )
    reminder = (
        f"⚠️ 技能 [{audit.skill_name}] 审计：以下步骤未完成：\n{step_list}\n"
        f"请补上这些步骤后再结束任务。"
    )
    logger.warning(f"审计未通过: {audit.skill_name}，遗漏步骤: {[s.title for s in incomplete]}")

    _active_audit = None
    return reminder


def record_tool_call(name: str, args_preview: str) -> None:
    """记录一次工具调用。"""
    if _active_audit is None:
        return
    _active_audit.tool_calls.append({"name": name, "args_preview": args_preview[:200]})


def record_llm_output(text: str) -> None:
    """记录一次 LLM 文本输出。"""
    if _active_audit is None:
        return
    # 只保留摘要
    _active_audit.llm_outputs.append(text[:500])


def clear_audit() -> None:
    """清除当前审计状态（任务中断或结束时调用）。"""
    global _active_audit
    _active_audit = None


# ── 内部函数 ─────────────────────────────────────────────────────────────────

def _parse_steps(body: str) -> list[AuditStep]:
    """从 skill body 中解析编号步骤。"""
    steps: list[AuditStep] = []
    for line in body.splitlines():
        m = _STEP_RE.match(line.strip())
        if m:
            idx = int(m.group(1))
            title = m.group(2)
            steps.append(AuditStep(
                index=idx,
                title=title,
                full_line=line.strip(),
            ))
    return steps


def _evaluate_completion(audit: SkillAudit) -> None:
    """根据 tool_calls 和 llm_outputs 评估每个步骤是否完成。"""
    # 构建所有证据文本
    all_evidence = []
    for tc in audit.tool_calls:
        all_evidence.append(f"{tc['name']} {tc['args_preview']}")
    for output in audit.llm_outputs:
        all_evidence.append(output)
    evidence_text = " ".join(all_evidence).lower()

    for step in audit.steps:
        # 获取该步骤的关键词
        keywords = _get_step_keywords(step.title)
        if not keywords:
            # 没有预定义关键词，跳过该步骤的自动检测
            # 标记为已完成（宁可漏检不可误报）
            step.completed = True
            continue

        # 检查是否有任何关键词出现在证据中
        for kw in keywords:
            if kw.lower() in evidence_text:
                step.completed = True
                step.evidence = f"匹配关键词: {kw}"
                break

        if not step.completed:
            logger.debug(f"步骤 {step.index}. {step.title} 未检测到完成证据")


def _get_step_keywords(title: str) -> list[str]:
    """获取步骤标题对应的关键词列表。"""
    title_lower = title.lower()

    # 精确匹配
    for key, keywords in _STEP_KEYWORDS.items():
        if key in title_lower:
            return keywords

    # 模糊匹配：标题中包含关键词的子串
    for key, keywords in _STEP_KEYWORDS.items():
        if key in title_lower or title_lower in key:
            return keywords

    return []
