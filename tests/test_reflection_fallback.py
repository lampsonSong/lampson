"""反思机制 fallback 降级测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import json

import pytest

from src.core import reflection


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _mock_response(content: str):
    """创建模拟 LLM 响应。"""
    msg = MagicMock()
    msg.content = content
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


def _mock_llm(name: str, response_content: str, raise_error: Exception | None = None):
    """创建模拟 LLM Client。

    - name: 模型名称（用于日志验证）
    - response_content: 返回内容（JSON 字符串）
    - raise_error: 如果设置，chat.completions.create 抛出此异常
    """
    mock = MagicMock()
    mock.model = name
    if raise_error:
        mock.client.chat.completions.create.side_effect = raise_error
    else:
        mock.client.chat.completions.create.return_value = _mock_response(response_content)
    return mock


LEARNINGS_JSON = json.dumps({
    "learnings": [
        {
            "type": "skill_create",
            "target": "test-skill",
            "content": "测试内容",
            "reason": "测试原因",
        }
    ]
})


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    """每个测试前清空全局状态。"""
    reflection._llm_client = None
    reflection._skill_index = None
    reflection._fallback_llms = []
    yield
    reflection._llm_client = None
    reflection._skill_index = None
    reflection._fallback_llms = []


# ── 主模型成功 ────────────────────────────────────────────────────────────────

def test_primary_succeeds(monkeypatch):
    """主模型成功，直接返回 learnings。"""
    primary = _mock_llm("primary-model", LEARNINGS_JSON)

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试目标",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    assert len(result) == 1
    assert result[0]["type"] == "skill_create"
    assert result[0]["target"] == "test-skill"
    # 确认只调用了主模型
    assert primary.client.chat.completions.create.call_count == 1


# ── 主模型失败，fallback 成功 ─────────────────────────────────────────────────

def test_primary_fails_fallback_succeeds(monkeypatch):
    """主模型失败，fallback 模型成功返回 learnings。"""
    primary = _mock_llm("primary-model", "", raise_error=Exception("主模型网络错误"))
    fallback = _mock_llm("fallback-model", LEARNINGS_JSON)

    reflection._fallback_llms = [(fallback, MagicMock())]

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试目标",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    assert len(result) == 1
    assert result[0]["type"] == "skill_create"
    # 主模型失败 1 次，fallback 成功 1 次
    assert primary.client.chat.completions.create.call_count == 1
    assert fallback.client.chat.completions.create.call_count == 1


# ── 多级 fallback 降级 ────────────────────────────────────────────────────────

def test_multiple_fallbacks(monkeypatch):
    """主模型失败，fallback1 失败，fallback2 成功。"""
    primary = _mock_llm("primary", "", raise_error=Exception("主模型错误"))
    fb1 = _mock_llm("fallback-1", "", raise_error=Exception("fallback1 也失败"))
    fb2 = _mock_llm("fallback-2", LEARNINGS_JSON)

    reflection._fallback_llms = [
        (fb1, MagicMock()),
        (fb2, MagicMock()),
    ]

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试目标",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    assert len(result) == 1
    assert result[0]["target"] == "test-skill"
    assert primary.client.chat.completions.create.call_count == 1
    assert fb1.client.chat.completions.create.call_count == 1
    assert fb2.client.chat.completions.create.call_count == 1


# ── 所有模型都失败 ────────────────────────────────────────────────────────────

def test_all_models_fail(monkeypatch):
    """主模型和所有 fallback 都失败，返回空列表。"""
    primary = _mock_llm("primary", "", raise_error=Exception("主模型失败"))
    fb1 = _mock_llm("fallback-1", "", raise_error=Exception("fallback1 失败"))
    fb2 = _mock_llm("fallback-2", "", raise_error=Exception("fallback2 也失败"))

    reflection._fallback_llms = [
        (fb1, MagicMock()),
        (fb2, MagicMock()),
    ]

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试目标",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    assert result == []
    assert primary.client.chat.completions.create.call_count == 1
    assert fb1.client.chat.completions.create.call_count == 1
    assert fb2.client.chat.completions.create.call_count == 1


# ── 无 fallback 配置 ─────────────────────────────────────────────────────────

def test_no_fallback_configured(monkeypatch):
    """没有配置 fallback，主模型失败后直接返回空。"""
    primary = _mock_llm("primary", "", raise_error=Exception("主模型失败"))

    reflection._fallback_llms = []

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试目标",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    assert result == []
    assert primary.client.chat.completions.create.call_count == 1


# ── 响应格式异常（JSON 解析失败）───────────────────────────────

def test_invalid_json_response(monkeypatch):
    """LLM 返回了非 JSON 内容，视为失败并尝试 fallback。"""
    primary = _mock_llm("primary", "这不是 JSON")
    fallback = _mock_llm("fallback", LEARNINGS_JSON)

    reflection._fallback_llms = [(fallback, MagicMock())]

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试目标",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    # JSON 解析失败 → fallback 接管
    assert len(result) == 1
    assert primary.client.chat.completions.create.call_count == 1
    assert fallback.client.chat.completions.create.call_count == 1


# ── set_fallback_llms 与 set_llm_client ──────────────────────────────────────

def test_set_fallback_llms_setters():
    """验证 setter 函数正常工作。"""
    mock_client = MagicMock()
    mock_fb1 = MagicMock()
    mock_fb2 = MagicMock()

    reflection.set_llm_client(mock_client)
    assert reflection._llm_client is mock_client

    reflection.set_fallback_llms([(mock_fb1, MagicMock()), (mock_fb2, MagicMock())])
    assert len(reflection._fallback_llms) == 2
    assert reflection._fallback_llms[0][0] is mock_fb1
    assert reflection._fallback_llms[1][0] is mock_fb2

    # 传入 None / 空列表 → 置为空
    reflection.set_fallback_llms(None)
    assert reflection._fallback_llms == []

    reflection.set_fallback_llms([])
    assert reflection._fallback_llms == []


# ── Thinking 标签 stripping ───────────────────────────────────────────────────

def test_thinking_tag_stripped(monkeypatch):
    """MiniMax 返回的 <think>...</think> 标签在 JSON 解析前被移除。"""
    thinking_response = (
        '<think>'
        '让我分析一下这个任务...'
        '</think>'
        '{"learnings":[{"type":"skill_create","target":"t","content":"c","reason":"r"}]}'
        '<think>'
        '完成思考'
        '</think>'
    )
    primary = _mock_llm("minimax", thinking_response)

    with patch.object(reflection, '_get_existing_skills_summary', return_value=""):
        with patch.object(reflection, '_get_existing_projects_summary', return_value=""):
            with patch.object(reflection, '_get_existing_info_summary', return_value=""):
                result = reflection.reflect_and_learn(
                    goal="测试",
                    execution_summary="测试执行",
                    llm_client=primary,
                )

    assert len(result) == 1
    assert result[0]["type"] == "skill_create"
