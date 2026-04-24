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
    """简单关键词匹配：检查用户输入是否触发某个技能。"""
    if not skills:
        return None
    user_lower = user_input.lower()
    for skill in skills.values():
        for trigger in skill.triggers:
            if trigger.lower() in user_lower:
                return skill
        if skill.description and skill.description.lower() in user_lower:
            return skill
    return None


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
