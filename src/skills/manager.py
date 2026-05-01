"""技能管理器：扫描 ~/.lampson/skills/，解析 SKILL.md，匹配并注入技能上下文。

SKILL.md 格式：
    ---
    name: skill-name
    description: 简短描述（用于匹配）
    triggers:
      - 触发关键词1
      - 触发关键词2
    ---
    ## 技能正文
    具体步骤和说明...
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


SKILLS_DIR = Path.home() / ".lampson" / "skills"

# SKILL.md frontmatter 解析正则
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class Skill:
    """代表一个已加载的技能。"""

    def __init__(self, name: str, path: Path, meta: dict[str, Any], body: str) -> None:
        self.name = name
        self.path = path
        self.description: str = meta.get("description", "")
        self.triggers: list[str] = meta.get("triggers", [])
        self.body = body
        self.full_content = path.read_text(encoding="utf-8")

    def __repr__(self) -> str:
        return f"<Skill name={self.name!r}>"


def _parse_skill_md(path: Path) -> Skill | None:
    """解析单个 SKILL.md，返回 Skill 对象；解析失败返回 None。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if not match:
        name = path.parent.name
        return Skill(name=name, path=path, meta={"description": "", "triggers": []}, body=content)

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}

    body = content[match.end():]
    name = meta.get("name", path.parent.name)
    return Skill(name=name, path=path, meta=meta, body=body)


def load_all_skills() -> dict[str, Skill]:
    """扫描 SKILLS_DIR，加载并返回所有技能，key 为技能名。"""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills: dict[str, Skill] = {}
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        skill = _parse_skill_md(skill_md)
        if skill:
            skills[skill.name] = skill
    return skills


def get_skills_summary(skills: dict[str, Skill]) -> str:
    """生成技能概要，供 system prompt 注入。"""
    if not skills:
        return ""
    lines = ["已加载技能："]
    for skill in skills.values():
        desc = skill.description or "(无描述)"
        triggers = ", ".join(skill.triggers[:3]) if skill.triggers else ""
        trigger_str = f"  触发词: {triggers}" if triggers else ""
        lines.append(f"- **{skill.name}**: {desc}{trigger_str}")
    return "\n".join(lines)


def match_skill(user_input: str, skills: dict[str, Skill]) -> Skill | None:
    """简单关键词匹配：检查用户输入是否触发某个技能。

    注意：只返回第一个匹配。若需要多匹配判断，用 match_skills()。
    """
    matched = match_skills(user_input, skills)
    return matched[0] if matched else None


def match_skills(user_input: str, skills: dict[str, Skill]) -> list[Skill]:
    """收集所有被触发词匹配的 skill，供上层决策。

    当匹配到多个 skill 时，由调用方用 LLM 或规则决定用哪个。
    """
    if not skills:
        return []
    user_lower = user_input.lower()
    matched: list[Skill] = []
    for skill in skills.values():
        for trigger in skill.triggers:
            if trigger.lower() in user_lower:
                if skill not in matched:
                    matched.append(skill)
                break
    return matched




def decide_best_skill(user_input: str, matched_skills: list[Skill], llm_client: Any | None = None) -> Skill | None:
    """从多个匹配的 skill 中选最合适的一个。

    策略：
    1. 只有 1 个匹配 → 直接用
    2. 多个匹配 → 优先用 LLM 语义判断；无 LLM 时用触发词长度 heuristics
    """
    if not matched_skills:
        return None
    if len(matched_skills) == 1:
        return matched_skills[0]

    # 多个匹配时，用 LLM 语义判断
    if llm_client is not None:
        try:
            lines = []
            for s in matched_skills:
                triggers_str = ", ".join(s.triggers) if s.triggers else "(无)"
                lines.append("- name: " + s.name + ", description: " + s.description + ", triggers: " + triggers_str)
            skill_list = "\n".join(lines)
            prompt = (
                "用户输入：" + repr(user_input) + "\n\n"
                "以下技能都被触发了，请判断哪个最合适：\n" + skill_list + "\n\n"
                "只回复技能的 name（原文），不要其他内容。如果都不合适，回复 none。"
            )
            from src.core.llm import LLMClient
            client = LLMClient(
                api_key=llm_client.client.api_key,
                base_url=str(llm_client.client.base_url),
                model=llm_client.model,
            )
            client.set_system_context()
            client.add_user_message(prompt)
            response = client.chat()
            answer = (response.choices[0].message.content or "").strip()
            if answer and answer.lower() != "none":
                for s in matched_skills:
                    if s.name.lower() == answer.lower():
                        return s
        except Exception:
            pass

    # Fallback：触发词最长的最具体，优先用
    return max(matched_skills, key=lambda s: max((len(t) for t in s.triggers), default=0))


def match_skill_with_llm(user_input: str, skills: dict[str, Skill], llm_client: Any) -> Skill | None:
    """用 LLM 做语义匹配，判断用户输入是否触发某个技能。"""
    if not skills:
        return None

    skill_list = "\n".join(
        f"- name: {s.name}\n  description: {s.description}\n  triggers: {s.triggers}"
        for s in skills.values()
    )
    prompt = (
        f"用户输入：{user_input!r}\n\n"
        f"以下是可用技能列表：\n{skill_list}\n\n"
        "判断是否有相关技能需要激活。如果有，只回复技能的 name（原文）；如果没有，只回复 none。"
    )

    try:
        from src.core.llm import LLMClient
        temp = LLMClient(
            api_key=llm_client.client.api_key,
            base_url=str(llm_client.client.base_url),
            model=llm_client.model,
        )
        temp.set_system_context()
        temp.add_user_message(prompt)
        response = temp.chat()
        answer = (response.choices[0].message.content or "").strip().lower()
        if answer and answer != "none":
            for name, skill in skills.items():
                if name.lower() == answer or skill.name.lower() == answer:
                    return skill
    except Exception:
        pass

    return None


def list_skills(skills: dict[str, Skill]) -> str:
    """返回技能列表文本，供 /skills list 展示。"""
    if not skills:
        return "暂无已安装的技能。"
    lines = ["已安装技能："]
    for skill in skills.values():
        desc = skill.description or "(无描述)"
        lines.append(f"  - {skill.name}: {desc}")
    return "\n".join(lines)


def show_skill(name: str, skills: dict[str, Skill]) -> str:
    """返回技能详情，供 /skills show <name> 展示。"""
    skill = skills.get(name)
    if not skill:
        return f"未找到技能：{name}"
    return skill.full_content


def create_skill(name: str, description: str = "", triggers: list[str] | None = None) -> str:
    """在 SKILLS_DIR 中创建新技能目录和 SKILL.md 模板。"""
    skill_dir = SKILLS_DIR / name
    if skill_dir.exists():
        return f"技能 '{name}' 已存在：{skill_dir}"

    skill_dir.mkdir(parents=True, exist_ok=True)
    trigger_list = triggers or [name]
    trigger_yaml = "\n".join(f"  - {t}" for t in trigger_list)

    content = f"""---
name: {name}
description: {description or name + ' 技能'}
triggers:
{trigger_yaml}
---

## {name}

### 描述
{description or '在此填写技能描述。'}

### 步骤
1. 步骤一
2. 步骤二
3. 步骤三

### 注意事项
- 注意事项一
"""
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return f"已创建技能 '{name}'：{skill_md}"


def install_default_skills(default_skills_dir: Path) -> None:
    """将 config/default_skills/ 中的技能复制到 SKILLS_DIR（如果不存在）。"""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if not default_skills_dir.exists():
        return
    for skill_dir in default_skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        dest = SKILLS_DIR / skill_dir.name
        if not dest.exists():
            shutil.copytree(str(skill_dir), str(dest))


# ── Consolidation ─────────────────────────────────────────────────────────────

@dataclass
class ConsolidationAction:
    """一次合并操作。"""

    keep: str  # 保留的 skill 名称
    delete: list[str]  # 要删除的 skill 名称
    merged_body: str  # 合并后的正文
    keep_invocation_count: int  # 取要合并的 skills 中最大的 invocation_count


def consolidate_skills(skills: dict[str, Skill], llm_client: Any) -> tuple[list[ConsolidationAction], str]:
    """让 LLM 分析所有 skills 的重复/耦合，返回合并建议列表和原始分析文本。

    返回 (actions, analysis)：actions 是可执行的合并操作列表；analysis 是 LLM 的分析文本。
    如果 skills 数量 < 2，直接返回空列表。
    """
    if len(skills) < 2:
        return [], ""

    # 构造 skill 摘要供 LLM 分析
    lines: list[str] = []
    for name, s in skills.items():
        triggers_str = ", ".join(s.triggers) if s.triggers else "(无)"
        lines.append(
            f"### {name}\n"
            f"description: {s.description}\n"
            f"triggers: {triggers_str}\n"
            f"invocation_count: {s.path.exists() and _get_invocation_count(s.path) or 0}\n"
            f"---\n"
            f"{s.body[:500]}"
        )

    skills_text = "\n\n".join(lines)

    prompt = (
        "你是一个技能库管理员，负责找出重复或功能重叠的技能并提出合并方案。\n\n"
        "## 判断标准\n"
        "- **A 包含 B**：如果 skill A 的正文几乎覆盖了 skill B 的所有功能点，B 应该被删除，A 保留并补充 B 的独特部分到正文。\n"
        "- **同功能不同实现**：如果 A 和 B 做的是同一件事但实现方式/触发词不同，合并成一个，用更好的描述和触发词。\n"
        "- **各自独立**：如果两个 skill 关注点完全不同，保留各自。\n\n"
        "## 当前 Skills\n\n"
        f"{skills_text}\n\n"
        "## 输出格式\n"
        "请仔细分析，输出以下 JSON 格式（只输出 JSON，不要其他文字）：\n\n"
        "```json\n"
        "{\n"
        '  "analysis": "你的分析说明（2-5句话），解释为什么这些技能需要或不需要合并",\n'
        '  "actions": [\n'
        '    {\n'
        '      "keep": "保留的 skill 名称",\n'
        '      "delete": ["要删除的 skill 名称列表"],\n'
        '      "merged_body": "合并后的完整正文（包含被删除 skill 的有价值内容）",\n'
        '      "keep_invocation_count": 保留的 invocation_count 值（取被合并 skills 中最大的）\n'
        '    }\n'
        "  ]\n"
        "}\n"
        "```\n\n"
        "如果不需要任何合并，输出：\n"
        "```json\n"
        '{"analysis": "...", "actions": []}\n'
        "```"
    )

    try:
        from src.core.llm import LLMClient
        client = LLMClient(
            api_key=llm_client.client.api_key,
            base_url=str(llm_client.client.base_url),
            model=llm_client.model,
        )
        client.set_system_context()
        client.add_user_message(prompt)
        response = client.chat()
    except Exception as e:
        return [], f"[错误] 调用 LLM 失败：{e}"

    raw = (response.choices[0].message.content or "").strip()

    # 提取 JSON
    try:
        import json

        # 找 ```json ... ``` 或直接 {...}
        json_start = raw.find("```json")
        if json_start != -1:
            json_end = raw.find("```", json_start + 6)
            json_str = raw[json_start + 7 : json_end].strip()
        else:
            json_start = raw.find("{")
            json_end = raw.rfind("}") + 1
            json_str = raw[json_start:json_end]

        data = json.loads(json_str)
    except Exception:
        return [], f"[错误] 解析 LLM 返回失败：\n{raw}"

    analysis = data.get("analysis", "")
    raw_actions: list[dict] = data.get("actions", [])
    actions: list[ConsolidationAction] = []

    for raw_a in raw_actions:
        keep = str(raw_a.get("keep", ""))
        delete = [str(d) for d in raw_a.get("delete", [])]
        merged_body = str(raw_a.get("merged_body", ""))
        keep_ic = int(raw_a.get("keep_invocation_count", 0))

        if keep and keep in skills:
            actions.append(
                ConsolidationAction(
                    keep=keep,
                    delete=delete,
                    merged_body=merged_body,
                    keep_invocation_count=keep_ic,
                )
            )

    return actions, analysis


def _get_invocation_count(path: Path) -> int:
    """读取 SKILL.md 的 invocation_count。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return 0
    try:
        meta = yaml.safe_load(match.group(1)) or {}
        return int(meta.get("invocation_count", 0))
    except (yaml.YAMLError, ValueError, TypeError):
        return 0


def _write_skill(path: Path, name: str, body: str, invocation_count: int, description: str, triggers: list[str]) -> None:
    """写回 SKILL.md（保留 frontmatter，更新 body 和 invocation_count）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raw = ""

    match = _FRONTMATTER_RE.match(raw)
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        old_body = raw[match.end() :]
    else:
        meta = {}
        old_body = raw

    # 更新 meta
    meta["name"] = name
    meta["description"] = description
    meta["triggers"] = triggers
    meta["invocation_count"] = invocation_count
    if "created_at" not in meta:
        meta["created_at"] = date.today().isoformat()

    dump = yaml.dump(meta, allow_unicode=True, default_flow_style=False).strip()
    path.write_text(f"---\n{dump}\n---\n{body}", encoding="utf-8")


def execute_consolidation(actions: list[ConsolidationAction]) -> str:
    """执行合并操作：删被合并的 skill 目录，更新保留的 SKILL.md。"""
    if not actions:
        return "没有需要合并的技能。"

    lines: list[str] = []
    for action in actions:
        keep_path = SKILLS_DIR / action.keep / "SKILL.md"
        if not keep_path.is_file():
            lines.append(f"[跳过] 保留的 skill '{action.keep}' 文件不存在")
            continue

        # 获取当前 skill 的 meta
        try:
            raw = keep_path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        match = _FRONTMATTER_RE.match(raw)
        if match:
            try:
                meta = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                meta = {}
        else:
            meta = {}

        description = meta.get("description", "")
        triggers: list[str] = meta.get("triggers", [])

        # 收集被合并 skill 的 triggers（去重追加）
        for del_name in action.delete:
            del_path = SKILLS_DIR / del_name / "SKILL.md"
            if del_path.is_file():
                try:
                    del_raw = del_path.read_text(encoding="utf-8")
                except OSError:
                    continue
                del_match = _FRONTMATTER_RE.match(del_raw)
                if del_match:
                    try:
                        del_meta = yaml.safe_load(del_match.group(1)) or {}
                    except yaml.YAMLError:
                        del_meta = {}
                    del_triggers: list[str] = del_meta.get("triggers", [])
                    for t in del_triggers:
                        if t not in triggers:
                            triggers.append(t)
                    del_desc = del_meta.get("description", "")
                    if del_desc and del_desc != description:
                        description = f"{description} / {del_desc}"

        # 写回
        _write_skill(
            keep_path,
            name=action.keep,
            body=action.merged_body,
            invocation_count=action.keep_invocation_count,
            description=description,
            triggers=triggers,
        )

        # 删除被合并的目录
        deleted_names: list[str] = []
        for del_name in action.delete:
            del_dir = SKILLS_DIR / del_name
            if del_dir.is_dir():
                shutil.rmtree(del_dir)
                deleted_names.append(del_name)

        lines.append(f"合并完成：保留 '{action.keep}'，删除 {deleted_names}，invocation_count={action.keep_invocation_count}")

    return "\n".join(lines)
