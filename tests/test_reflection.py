"""反思与知识沉淀模块的单元测试。"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.reflection import (
    _content_already_exists,
    _create_project,
    _create_skill,
    _update_project,
    _update_skill,
    execute_learnings,
    format_execution_summary,
    reflect_and_learn,
    should_reflect,
)
from src.planning.steps import Plan, Step, StepStatus


# ── should_reflect ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_cooldown():
    """每个测试前重置反思冷却时间。"""
    import src.core.reflection as ref_mod
    ref_mod._last_reflect_time = 0.0
    yield
    ref_mod._last_reflect_time = 0.0


def _make_plan(n_steps: int, status: StepStatus = StepStatus.done) -> Plan:
    steps = []
    for i in range(n_steps):
        s = Step(id=i + 1, thought="", action=f"step {i+1}", args={}, status=status)
        steps.append(s)
    p = Plan(goal="test", steps=steps)
    p.status = status
    return p


def test_should_reflect_cooldown():
    import src.core.reflection as ref_mod
    ref_mod._last_reflect_time = time.time()
    plan = _make_plan(5)
    assert should_reflect(plan) is False


def test_should_reflect_fast_path_no_tools():
    """Fast Path + 0 工具调用 → 不反思。"""
    assert should_reflect(None, is_fast_path=True, tool_call_count=0) is False


def test_should_reflect_fast_path_with_tools():
    """Fast Path + 1 工具调用 → 反思。"""
    assert should_reflect(None, is_fast_path=True, tool_call_count=1) is True


def test_should_reflect_chat_intent():
    """闲聊意图 → 不反思。"""
    assert should_reflect(None, is_fast_path=True, tool_call_count=3, intent="chat") is False


def test_should_reflect_info_query_intent():
    """信息查询意图 → 不反思。"""
    assert should_reflect(None, is_fast_path=True, tool_call_count=3, intent="info_query") is False


def test_should_reflect_plan_3steps():
    plan = _make_plan(3)
    assert should_reflect(plan) is True


def test_should_reflect_plan_1step():
    plan = _make_plan(1)
    assert should_reflect(plan) is False


def test_should_reflect_skill_activated():
    """Skill 激活 → 反思。"""
    assert should_reflect(None, skill_activated="code-review") is True


def test_should_reflect_cooldown_takes_priority():
    """冷却期内即使满足条件也不反思。"""
    import src.core.reflection as ref_mod
    ref_mod._last_reflect_time = time.time()
    plan = _make_plan(5)
    assert should_reflect(plan) is False


# ── Project 沉淀 ───────────────────────────────────────────────────────────


@pytest.fixture
def temp_projects(tmp_path: Path) -> Path:
    return tmp_path / "projects"


@pytest.fixture
def temp_skills(tmp_path: Path) -> Path:
    return tmp_path / "skills"


def test_create_project_new(temp_projects: Path):
    """project_create：新建项目文件。"""
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        hint = _create_project("myproj", "源码路径: /foo/bar", "首次探索")
    assert hint is not None
    assert "myproj" in hint
    assert (temp_projects / "myproj.md").exists()
    content = (temp_projects / "myproj.md").read_text()
    assert "源码路径: /foo/bar" in content
    assert "创建于" in content


def test_create_project_downgrade_to_update(temp_projects: Path):
    """project_create 遇已存在 → 降级为 update。"""
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        _create_project("myproj", "源码路径: /foo/bar", "首次")
        hint = _create_project("myproj", "新增了 cronjob 模块", "发现新模块")
    assert hint is not None
    assert "更新" in hint
    content = (temp_projects / "myproj.md").read_text()
    assert "cronjob" in content
    assert "更新" in content


def test_update_project_append(temp_projects: Path):
    """project_update：追加日期分节。"""
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        _create_project("myproj", "源码路径: /foo/bar", "首次")
        hint = _update_project("myproj", "新增模块: cronjob", "补充")
    assert hint is not None
    assert "更新" in hint
    content = (temp_projects / "myproj.md").read_text()
    assert "cronjob" in content


def test_update_project_duplicate(temp_projects: Path):
    """project_update：重复内容不写入。"""
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        _create_project("myproj", "源码路径: /foo/bar", "首次")
        hint = _update_project("myproj", "源码路径: /foo/bar", "重复")
    assert hint is None


def test_update_project_downgrade_to_create(temp_projects: Path):
    """project_update 遇不存在 → 降级为 create。"""
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        hint = _update_project("myproj", "源码路径: /foo/bar", "降级创建")
    assert hint is not None
    assert "myproj" in hint
    content = (temp_projects / "myproj.md").read_text()
    assert "源码路径: /foo/bar" in content


# ── Skill 沉淀 ─────────────────────────────────────────────────────────────


def test_create_skill(temp_skills: Path):
    with patch("src.core.reflection.SKILLS_DIR", temp_skills):
        hint = _create_skill("my-skill", "x" * 200, "测试创建")
    assert hint is not None
    assert (temp_skills / "my-skill" / "SKILL.md").exists()


def test_create_skill_empty_content(temp_skills: Path):
    with patch("src.core.reflection.SKILLS_DIR", temp_skills):
        hint = _create_skill("empty", "", "空内容")
    assert hint is None


def test_update_skill_append(temp_skills: Path):
    skill_dir = temp_skills / "existing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: existing\n---\n# Existing\nOld content.",
        encoding="utf-8",
    )
    with patch("src.core.reflection.SKILLS_DIR", temp_skills):
        hint = _update_skill("existing", "New findings here.", "补充")
    assert hint is not None
    updated = (skill_dir / "SKILL.md").read_text()
    assert "New findings" in updated


def test_update_skill_duplicate(temp_skills: Path):
    skill_dir = temp_skills / "existing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: existing\n---\n# Existing\nOld content.",
        encoding="utf-8",
    )
    with patch("src.core.reflection.SKILLS_DIR", temp_skills):
        hint = _update_skill("existing", "Old content.", "重复")
    assert hint is None


# ── 去重 ─────────────────────────────────────────────────────────────────────


def test_content_already_exists():
    existing = "源码路径: /foo/bar\n其他信息"
    assert _content_already_exists(existing, "源码路径: /foo/bar") is True
    assert _content_already_exists(existing, "完全不同的内容") is False


# ── execute_learnings ───────────────────────────────────────────────────────


def test_execute_learnings_project_create(temp_projects: Path):
    learnings = [{
        "type": "project_create",
        "target": "test-proj",
        "content": "Some project info " + "x" * 100,
        "reason": "test",
        "triggers": [],
    }]
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        hints = execute_learnings(learnings)
    assert len(hints) == 1
    assert "test-proj" in hints[0]


def test_execute_learnings_project_update(temp_projects: Path):
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        execute_learnings([{
            "type": "project_create",
            "target": "test-proj",
            "content": "Initial info " + "x" * 100,
            "reason": "首次",
            "triggers": [],
        }])
        hints = execute_learnings([{
            "type": "project_update",
            "target": "test-proj",
            "content": "New module found " + "y" * 100,
            "reason": "发现新模块",
            "triggers": [],
        }])
    assert len(hints) == 1
    assert "更新" in hints[0]
    content = (temp_projects / "test-proj.md").read_text()
    assert "New module" in content


def test_execute_learnings_unknown_type(temp_projects: Path):
    """未知 type 不影响其他 learning。"""
    learnings = [
        {"type": "unknown_type", "target": "x", "content": "y", "reason": "z", "triggers": []},
        {
            "type": "project_create",
            "target": "valid-proj",
            "content": "Valid content " + "x" * 100,
            "reason": "test",
            "triggers": [],
        },
    ]
    with patch("src.core.reflection.PROJECTS_DIR", temp_projects):
        hints = execute_learnings(learnings)
    assert len(hints) == 1
    assert "valid-proj" in hints[0]


def test_execute_learnings_empty():
    assert execute_learnings([]) == []


# ── reflect_and_learn (mock LLM) ───────────────────────────────────────────


def test_reflect_and_learn_no_learning():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"learnings": []}'
    mock_client.client.chat.completions.create.return_value = mock_response
    mock_client.model = "test-model"

    result = reflect_and_learn("test goal", "test summary", mock_client)
    assert result == []


def test_reflect_and_learn_with_project_create():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "learnings": [{
            "type": "project_create",
            "target": "myproj",
            "reason": "首次探索",
            "content": "项目路径: /foo/bar",
            "triggers": [],
        }],
    })
    mock_client.client.chat.completions.create.return_value = mock_response
    mock_client.model = "test-model"

    with patch("src.core.reflection._get_existing_skills_summary", return_value="（无）"), \
         patch("src.core.reflection._get_existing_projects_summary", return_value="（无）"):
        result = reflect_and_learn("探索 myproj", "执行了3步", mock_client)
    assert len(result) == 1
    assert result[0]["type"] == "project_create"


def test_reflect_and_learn_ignores_should_learn():
    """should_learn 已移除，以 learnings 为准。"""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"should_learn": true, "learnings": []}'
    mock_client.client.chat.completions.create.return_value = mock_response
    mock_client.model = "test-model"

    with patch("src.core.reflection._get_existing_skills_summary", return_value="（无）"), \
         patch("src.core.reflection._get_existing_projects_summary", return_value="（无）"):
        result = reflect_and_learn("test", "test", mock_client)
    assert result == []


# ── format_execution_summary ────────────────────────────────────────────────


def test_format_execution_summary():
    steps = [
        Step(id=1, thought="", action="find code", args={"pattern": "main"}, status=StepStatus.done, result="found"),
        Step(id=2, thought="", action="read file", args={"path": "/tmp/a.py"}, status=StepStatus.done, result="content"),
    ]
    plan = Plan(goal="test", steps=steps)
    plan.status = StepStatus.done
    summary = format_execution_summary(plan)
    assert "find code" in summary
    assert "done" in summary
