# Cursor Agent 实现任务：Skills 系统核心改动

## 任务概述
基于设计文档 `docs/skills-system-design.md`，实现 skills 系统的 P1 核心改动和 P0 测试。

## 文件清单

### 必读文件
- `docs/skills-system-design.md` — 完整设计文档（重点看第 3、4、7 章）
- `src/core/skills_tools.py` — 现有 skill 工具函数
- `src/core/prompt_builder.py` — 现有 prompt 构建
- `src/planning/prompts.py` — 现有规划 prompt
- `src/planning/planner.py` — 现有规划器
- `src/core/agent.py` — 现有 agent 逻辑
- `src/core/tools.py` — 工具注册表
- `tests/test_planning.py` — 现有测试（参考风格）

### 需要修改的文件
1. `src/planning/prompts.py`
2. `src/planning/planner.py`
3. `src/core/agent.py`

### 需要新建的文件
1. `tests/test_skills.py`

## 具体任务

### 任务 1：classify prompt 增加 trigger 匹配 + matched_skill 输出

**文件**: `src/planning/prompts.py`

**改动 1**: 在 `build_classify_prompt()` 中注入 skills 的 triggers 列表。

在 `## 工具能力` 之前加入一个新 section：

```
## 可用技能触发词
{skills_triggers_block}
```

`skills_triggers_block` 由调用方传入（从 `~/.lampson/skills/*/SKILL.md` 的 frontmatter triggers 字段收集）。

函数签名改为：
```python
def build_classify_prompt(goal: str, context: str, tools_desc: str, skills_triggers: str = "") -> str:
```

**改动 2**: 在 classify prompt 的 JSON 输出格式中增加一个字段：

在 `"initial_plan"` 字段说明之后加：
```
- "matched_skill": 字符串或 null。如果用户目标匹配到上面某个技能的触发词，填写技能名称；否则 null
```

在示例结构中也加入：`"matched_skill": "reverse-tracking"` 或 `"matched_skill": null`

**改动 3**: 在决策指引部分加入：
```
- 如果用户请求匹配某个 skill 的触发词（如"找代码"匹配 reverse-tracking），设置 matched_skill 为对应技能名
- matched_skill 不影响 needs_tools 和 confidence 的判断，它是一个附加信号
```

### 任务 2：planner.py 中提取 matched_skill 并传递

**文件**: `src/planning/planner.py`

**改动 1**: `IntentResult` 数据类增加 `matched_skill` 字段：

```python
@dataclass
class IntentResult:
    intent: str
    needs_tools: bool
    intent_detail: str
    confidence: float
    missing_info: list[str]
    direct_reply: str | None = None
    initial_plan: Plan | None = None
    matched_skill: str | None = None  # ← 新增
```

**改动 2**: `classify()` 方法中构建 skills_triggers_block 并传入 prompt：

```python
def classify(self, goal: str, context: str) -> IntentResult:
    # 收集所有 skill 的 triggers
    skills_triggers = self._build_skills_triggers()
    prompt = build_classify_prompt(goal, context, tools_desc, skills_triggers)
    ...
    # 解析 LLM 输出时提取 matched_skill
    result.matched_skill = data.get("matched_skill")
    return result
```

**改动 3**: 新增 `_build_skills_triggers()` 方法：

```python
def _build_skills_triggers(self) -> str:
    """从 skills 目录收集所有 trigger 词，格式化为紧凑文本。"""
    from src.core.skills_tools import _iter_skills
    skills = _iter_skills()
    if not skills:
        return ""
    lines = []
    for s in skills:
        triggers = s.get("triggers", [])
        if triggers:
            lines.append(f"- {s['name']}: {', '.join(triggers)}")
    return "\n".join(lines) if lines else ""
```

### 任务 3：plan_v2 自动注入 skill_view step0

**文件**: `src/planning/planner.py`

**改动**: 在 `plan_v2()` 方法中，如果 `phase1_result.matched_skill` 非空，在调用 `build_plan_prompt_v2` 之前，把 `skill_view` 作为 step0 注入到 exploration_results 中：

```python
def plan_v2(self, goal, context, phase1_result, exploration_results):
    # 如果 classify 匹配到 skill，先加载 skill 内容
    skill_context = ""
    if phase1_result.matched_skill:
        from src.core import tools as tool_registry
        skill_content = tool_registry.dispatch("skill_view", {"name": phase1_result.matched_skill})
        if not skill_content.startswith("[Skill"):
            skill_context = f"\n## 已加载技能：{phase1_result.matched_skill}\n{skill_content}\n"
    
    exploration_with_skill = skill_context + exploration_results
    
    prompt = build_plan_prompt_v2(
        goal=goal,
        context=context,
        tools_desc=...,
        phase1_result=...,
        exploration_results=exploration_with_skill,
    )
    ...
```

这样 LLM 在制定计划时已经能看到 skill 的全文内容。

### 任务 4：Fast Path 处理 matched_skill

**文件**: `src/core/agent.py`

**改动**: 在 `run()` 方法中，Fast Path 分支里处理 matched_skill：

```python
# Fast Path
if intent.confidence >= 0.8 and not intent.missing_info:
    # 如果匹配到 skill，先加载 skill 内容注入到对话中
    if intent.matched_skill:
        from src.core import tools as tool_registry
        skill_content = tool_registry.dispatch("skill_view", {"name": intent.matched_skill})
        if not skill_content.startswith("[Skill"):
            self.llm.add_assistant_message(
                f"[系统] 已加载技能 {intent.matched_skill}，请按照技能指导执行。"
            )
            self.llm.add_user_message(skill_content)
    
    self.current_plan = None
    if self.llm.supports_native_tool_calling:
        return self._run_native()
    else:
        return self._run_prompt_based()
```

注意：`add_assistant_message` 和 `add_user_message` 需要确认 LLMClient 有这些方法。如果没有，可以用 `add_system_message` 替代。检查 LLMClient 的接口后决定。

### 任务 5：写 test_skills.py

**文件**: `tests/test_skills.py`

覆盖以下测试（用 tmp_path 做 fixtures，不依赖真实的 ~/.lampson）：

```python
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
from src.core.skills_tools import skill_view, skills_list, _parse_skill
from src.core.prompt_builder import build_skills_index

# --- Fixtures ---

@pytest.fixture
def skills_dir(tmp_path):
    """创建临时 skills 目录，包含测试用 skill 文件。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    
    # 创建测试 skill 1
    s1 = sd / "test-skill" 
    s1.mkdir()
    (s1 / "SKILL.md").write_text("""---
name: test-skill
description: A test skill for unit testing
triggers:
  - test
  - 测试
---
# Test Skill
## Steps
1. Do something
2. Check result
""")
    
    # 创建测试 skill 2
    s2 = sd / "another-skill"
    s2.mkdir()
    (s2 / "SKILL.md").write_text("""---
name: another-skill  
description: Another test skill
triggers:
  - another
---
# Another Skill
Content here.
""")
    
    # 创建无 frontmatter 的 skill
    s3 = sd / "no-meta"
    s3.mkdir()
    (s3 / "SKILL.md").write_text("# No Meta Skill\nJust content.")
    
    return sd


# --- 1. _parse_skill 测试 ---

def test_parse_skill_with_frontmatter(skills_dir):
    path = skills_dir / "test-skill" / "SKILL.md"
    result = _parse_skill(path)
    assert result is not None
    assert result["name"] == "test-skill"
    assert result["description"] == "A test skill for unit testing"
    assert "test" in result["triggers"]
    assert "测试" in result["triggers"]
    assert "Steps" in result["body"]


def test_parse_skill_without_frontmatter(skills_dir):
    path = skills_dir / "no-meta" / "SKILL.md"
    result = _parse_skill(path)
    assert result is not None
    assert result["name"] == "no-meta"  # 默认用目录名
    assert result["description"] == ""
    assert result["triggers"] == []


def test_parse_skill_invalid_yaml(skills_dir):
    bad_dir = skills_dir / "bad-yaml"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text("---\nname: [\n---\n# Bad")
    result = _parse_skill(bad_dir / "SKILL.md")
    assert result is not None  # 不应该 crash，fallback 即可


def test_parse_skill_nonexistent():
    result = _parse_skill(Path("/nonexistent/SKILL.md"))
    assert result is None


# --- 2. skills_list 测试 ---

@patch("src.core.skills_tools.SKILLS_DIR", new_callable=lambda: PropertyMock)
def test_skills_list_all(mock_dir, skills_dir):
    with patch("src.core.skills_tools.SKILLS_DIR", skills_dir):
        with patch("src.core.skills_tools._iter_skills") as mock_iter:
            from src.core.skills_tools import _parse_skill
            skills = []
            for sf in skills_dir.rglob("SKILL.md"):
                s = _parse_skill(sf)
                if s:
                    skills.append(s)
            mock_iter.return_value = skills
            result = skills_list({})
    assert "test-skill" in result
    assert "another-skill" in result
    assert "no-meta" in result


@patch("src.core.skills_tools._iter_skills")
def test_skills_list_by_query(mock_iter, skills_dir):
    from src.core.skills_tools import _parse_skill
    skills = []
    for sf in skills_dir.rglob("SKILL.md"):
        s = _parse_skill(sf)
        if s:
            skills.append(s)
    mock_iter.return_value = skills
    result = skills_list({"query": "test"})
    assert "test-skill" in result
    assert "another-skill" not in result


@patch("src.core.skills_tools._iter_skills")
def test_skills_list_empty(mock_iter):
    mock_iter.return_value = []
    result = skills_list({})
    assert "No skills found" in result


# --- 3. skill_view 测试 ---

@patch("src.core.skills_tools._iter_skills")
def test_skill_view_found(mock_iter, skills_dir):
    from src.core.skills_tools import _parse_skill
    skills = []
    for sf in skills_dir.rglob("SKILL.md"):
        s = _parse_skill(sf)
        if s:
            skills.append(s)
    mock_iter.return_value = skills
    result = skill_view({"name": "test-skill"})
    assert "Test Skill" in result
    assert "Steps" in result


@patch("src.core.skills_tools._iter_skills")
def test_skill_view_not_found(mock_iter, skills_dir):
    from src.core.skills_tools import _parse_skill
    skills = []
    for sf in skills_dir.rglob("SKILL.md"):
        s = _parse_skill(sf)
        if s:
            skills.append(s)
    mock_iter.return_value = skills
    result = skill_view({"name": "nonexistent"})
    assert "not found" in result.lower()
    assert "test-skill" in result  # 列出可用的


def test_skill_view_empty_name():
    result = skill_view({})
    assert "需要 name 参数" in result


# --- 4. build_skills_index 测试 ---

@patch("src.core.prompt_builder._iter_skill_files")
def test_build_skills_index(mock_iter, skills_dir):
    files = list(skills_dir.rglob("SKILL.md"))
    mock_iter.return_value = files
    result = build_skills_index()
    assert "test-skill" in result
    assert "another-skill" in result
    assert "Skills" in result


@patch("src.core.prompt_builder._iter_skill_files")
def test_build_skills_index_empty(mock_iter):
    mock_iter.return_value = []
    result = build_skills_index()
    assert result == ""


# --- 5. trigger 匹配测试 ---

@patch("src.core.skills_tools._iter_skills")
def test_trigger_search_match(mock_iter, skills_dir):
    from src.core.skills_tools import _parse_skill
    skills = []
    for sf in skills_dir.rglob("SKILL.md"):
        s = _parse_skill(sf)
        if s:
            skills.append(s)
    mock_iter.return_value = skills
    # 搜索 trigger "test"
    result = skills_list({"query": "test"})
    assert "test-skill" in result
    # 搜索 trigger "测试"（中文）
    result = skills_list({"query": "测试"})
    assert "test-skill" in result
```

注意：测试要用 mock 来隔离，不要依赖真实的 `~/.lampson/skills/` 目录。用 `@patch` 替换 `_iter_skills` 和 `_iter_skill_files`。

## 约束
- 不要创建 .bak 备份文件
- 不要 git commit
- 先读设计文档和所有相关源文件，理解上下文后再改
- 改完后确保 `cd /Users/songyuhao/lampson && source .venv/bin/activate && python -m pytest tests/test_skills.py -v` 全部通过
- 改完后确保 `cd /Users/songyuhao/lampson && source .venv/bin/activate && python -m pytest tests/ -v` 也全部通过
