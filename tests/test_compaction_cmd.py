"""/compact 命令测试。

覆盖路径：
Session._handle_compaction → Agent.force_compact → apply_compaction

场景：
- 正常触发（成功）
- 未配置 CompactionConfig
- 计划执行中
- 命令成功 / 失败
- 连续调用
- 异常兜底
- 命令路由（handle_input → /compact）
"""

from typing import Optional, List
from unittest.mock import MagicMock, patch

import pytest

from src.core.agent import Agent
from src.core.compaction import CompactionConfig, CompactionResult
from src.core.session import HandleResult, Session


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_agent(
    compaction_config: Optional[CompactionConfig] = None,
    messages: Optional[List] = None,
) -> Agent:
    """构造一个 mock LLM + mock adapter 的 Agent。"""
    mock_llm = MagicMock()
    mock_llm.messages = messages or [{"role": "system", "content": "system"}]
    mock_adapter = MagicMock()
    agent = Agent(llm=mock_llm, adapter=mock_adapter, compaction_config=compaction_config)
    return agent


def _make_session(
    compaction_config: Optional[CompactionConfig] = None,
    messages: Optional[List] = None,
) -> Session:
    """构造最小可测的 Session（不经过 from_config）。"""
    agent = _make_agent(compaction_config=compaction_config, messages=messages)
    session = Session(agent=agent, config={})
    session.session_id = "test-session-001"
    session._current_segment = 0
    return session


def _success_result(archived_count: int = 3) -> CompactionResult:
    return CompactionResult(
        success=True,
        summary="",
        messages_kept=[{"role": "user", "content": "kept"}],
        archived_count=archived_count,
        archive_details="[archive] test reason",
        archive_targets=[{"target": "skill:test", "entry_count": 1}],
    )


def _failure_result(error: str = "分类失败") -> CompactionResult:
    return CompactionResult(success=False, error=error)


# ══════════════════════════════════════════════════════════════════════════════
# Agent.force_compact 单元测试
# ══════════════════════════════════════════════════════════════════════════════


class TestAgentForceCompact:
    """直接测试 Agent.force_compact 方法。"""

    def test_returns_none_when_no_config(self):
        """未配置 CompactionConfig 时返回 None。"""
        agent = _make_agent(compaction_config=None)
        assert agent.force_compact() is None

    def test_returns_none_when_plan_executing(self):
        """有正在执行的计划时返回 None。"""
        from src.planning.steps import Plan, PlanStatus

        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        agent = _make_agent(compaction_config=config)

        plan = Plan(goal="do something")
        plan.status = PlanStatus.executing
        agent.current_plan = plan

        assert agent.force_compact() is None

    def test_returns_result_on_success(self):
        """正常压缩返回 CompactionResult。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        agent = _make_agent(compaction_config=config)
        agent.last_stop_reason = "end_turn"

        mock_result = _success_result()
        with patch("src.core.agent.apply_compaction", return_value=mock_result) as mock_apply:
            cr = agent.force_compact()

        assert cr is not None
        assert cr.success is True
        assert cr.archived_count == 3
        # force_compact 使用 force=True 绕过阈值检查
        call_kwargs = mock_apply.call_args
        assert call_kwargs.kwargs.get("force") is True
        assert call_kwargs.kwargs.get("stop_reason") == "end_turn"

    def test_returns_none_on_exception(self):
        """apply_compaction 抛异常时不外泄，返回 None。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        agent = _make_agent(compaction_config=config)

        with patch("src.core.agent.apply_compaction", side_effect=RuntimeError("LLM 挂了")):
            cr = agent.force_compact()

        assert cr is None

    def test_plan_not_executing_does_not_block(self):
        """计划存在但非 executing 状态时不阻止压缩。"""
        from src.planning.steps import Plan, PlanStatus

        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        agent = _make_agent(compaction_config=config)

        plan = Plan(goal="done task")
        plan.status = PlanStatus.completed  # 非 executing
        agent.current_plan = plan

        mock_result = _success_result()
        with patch("src.core.agent.apply_compaction", return_value=mock_result):
            cr = agent.force_compact()

        assert cr is not None
        assert cr.success is True


# ══════════════════════════════════════════════════════════════════════════════
# Session._handle_compaction 单元测试
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionHandleCompaction:
    """测试 Session._handle_compaction 方法的各种分支。"""

    def test_success(self):
        """压缩成功时返回正确文案。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        mock_result = _success_result(archived_count=5)
        with patch.object(session.agent, "force_compact", return_value=mock_result):
            result = session._handle_compaction()

        assert isinstance(result, HandleResult)
        assert result.is_command is True
        assert "已完成" in result.reply
        assert "5" in result.reply
        assert session._current_segment == 1

    def test_not_configured(self):
        """未配置压缩时返回不可用提示。"""
        session = _make_session(compaction_config=None)

        with patch.object(session.agent, "force_compact", return_value=None):
            result = session._handle_compaction()

        assert "不可用" in result.reply
        assert "未配置" in result.reply
        assert session._current_segment == 0  # segment 不变

    def test_plan_executing(self):
        """计划执行中时返回不可用提示（force_compact 返回 None）。"""
        from src.planning.steps import Plan, PlanStatus

        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        plan = Plan(goal="working")
        plan.status = PlanStatus.executing
        session.agent.current_plan = plan

        with patch.object(session.agent, "force_compact", return_value=None):
            result = session._handle_compaction()

        assert "不可用" in result.reply
        assert session._current_segment == 0

    def test_compaction_failure(self):
        """压缩失败时返回失败原因。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        mock_result = _failure_result(error="LLM 调用超时")
        with patch.object(session.agent, "force_compact", return_value=mock_result):
            result = session._handle_compaction()

        assert "失败" in result.reply
        assert "LLM 调用超时" in result.reply
        assert session._current_segment == 0  # 失败不递增 segment

    def test_exception_caught(self):
        """force_compact 抛异常时兜底返回异常信息。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        with patch.object(
            session.agent, "force_compact", side_effect=RuntimeError("unexpected")
        ):
            result = session._handle_compaction()

        assert "异常" in result.reply
        assert "unexpected" in result.reply
        assert session._current_segment == 0

    def test_consecutive_calls_segment_increment(self):
        """连续调用成功时 segment 递增。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        mock_result = _success_result(archived_count=2)

        with patch.object(session.agent, "force_compact", return_value=mock_result):
            r1 = session._handle_compaction()
        assert session._current_segment == 1
        assert "2" in r1.reply

        with patch.object(session.agent, "force_compact", return_value=mock_result):
            r2 = session._handle_compaction()
        assert session._current_segment == 2
        assert "2" in r2.reply

    def test_consecutive_first_fail_then_succeed(self):
        """连续调用：第一次失败，第二次成功，segment 只在成功时递增。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        fail_result = _failure_result(error="临时错误")
        success_result = _success_result(archived_count=1)

        with patch.object(session.agent, "force_compact", return_value=fail_result):
            session._handle_compaction()
        assert session._current_segment == 0

        with patch.object(session.agent, "force_compact", return_value=success_result):
            session._handle_compaction()
        assert session._current_segment == 1

    def test_success_with_zero_archived(self):
        """压缩成功但归档 0 条内容（边界值）。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        mock_result = _success_result(archived_count=0)
        with patch.object(session.agent, "force_compact", return_value=mock_result):
            result = session._handle_compaction()

        assert "已完成" in result.reply
        assert "0" in result.reply
        assert session._current_segment == 1

    def test_session_id_passed_to_force_compact(self):
        """session_id 和 session_store 正确传递到 force_compact。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)
        session.session_id = "my-session-42"

        with patch.object(session.agent, "force_compact", return_value=_success_result()) as mock_fc:
            session._handle_compaction()

        mock_fc.assert_called_once()
        call_kwargs = mock_fc.call_args.kwargs
        assert call_kwargs.get("session_id") == "my-session-42"
        # session_store 应被传入（模块级引用）
        assert call_kwargs.get("session_store") is not None


# ══════════════════════════════════════════════════════════════════════════════
# 命令路由测试
# ══════════════════════════════════════════════════════════════════════════════


class TestCompactionCommandRouting:
    """测试 /compact 命令从 handle_input 正确路由到 _handle_compaction。"""

    def test_handle_input_routes_compaction(self):
        """handle_input('/compact') 路由到 _handle_compaction。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        with patch.object(session.agent, "force_compact", return_value=_success_result()):
            result = session.handle_input("/compact")

        assert isinstance(result, HandleResult)
        assert result.is_command is True
        assert "已完成" in result.reply

    def test_handle_input_routes_compaction_case_insensitive(self):
        """/COMPACT 大写也能路由（command 已 lower()）。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        with patch.object(session.agent, "force_compact", return_value=_success_result()):
            result = session.handle_input("/COMPACT")

        assert result.is_command is True
        assert "已完成" in result.reply

    def test_handle_input_compaction_with_trailing_spaces(self):
        """/compact 后面有空格也能路由（前导空格走自然语言分支，只有尾部空格是命令）。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)

        # 尾部空格：strip() 后 parts[0]=="/compact"，能路由
        with patch.object(session.agent, "force_compact", return_value=_success_result()):
            result = session.handle_input("/compact   ")

        assert result.is_command is True
        assert "已完成" in result.reply

        # 前导空格：不走命令分支（startswith("/") 为 False），属于自然语言
        # 不测这个分支，它不是 /compact 命令的职责

    def test_handle_input_does_not_write_jsonl(self):
        """命令路由不写入 JSONL（不走自然语言分支）。"""
        config = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        session = _make_session(compaction_config=config)
        session.session_id = "test-jsonl"

        with patch.object(session.agent, "force_compact", return_value=_success_result()):
            with patch("src.core.session.session_store") as mock_ss:
                session.handle_input("/compact")
                # 不应调用 append_message（JSONL 写入）
                mock_ss.append_message.assert_not_called()
