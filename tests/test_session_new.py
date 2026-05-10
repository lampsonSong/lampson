"""测试 /new 命令（SessionManager.reset_session）和 is_new 处理。"""

import threading
from unittest.mock import MagicMock, patch
import time

import pytest


def _make_mock_session(session_id: str = "test-session-1") -> MagicMock:
    """创建一个 mock Session 对象。"""
    session = MagicMock()
    session.session_id = session_id
    session.last_activity_at = time.time()
    return session


class TestSessionManagerResetSession:
    """测试 SessionManager.reset_session() 公开方法。"""

    def _make_mgr(self) -> "SessionManager":
        """构造一个跳过 __init__ 的 SessionManager。"""
        from src.core.session_manager import SessionManager
        mgr = SessionManager.__new__(SessionManager)
        mgr._config = {}
        mgr._sessions = {}
        mgr._cli_session = None
        mgr._lock = threading.Lock()
        return mgr

    def test_reset_returns_new_session_object(self):
        """reset_session 应返回新创建的 session，不是旧的。"""
        mgr = self._make_mgr()
        old_session = _make_mock_session("old-1")
        new_session = _make_mock_session("new-1")
        mgr._sessions["feishu:ou_123"] = old_session

        with patch.object(mgr, "_create_session", return_value=new_session), \
             patch("src.memory.session_store.end_session"):
            result = mgr.reset_session("feishu", "ou_123")

        assert result is new_session
        assert result.session_id == "new-1"
        assert mgr._sessions["feishu:ou_123"] is new_session

    def test_reset_cli_returns_new_session(self):
        """CLI 渠道的 reset_session 也应返回新 session。"""
        mgr = self._make_mgr()
        old_session = _make_mock_session("old-cli")
        new_session = _make_mock_session("new-cli")
        mgr._cli_session = old_session

        with patch.object(mgr, "_create_session", return_value=new_session), \
             patch("src.memory.session_store.end_session"):
            result = mgr.reset_session("cli", "default")

        assert result is new_session
        assert result.session_id == "new-cli"
        assert mgr._cli_session is new_session

    def test_reset_ends_old_session(self):
        """reset_session 应调用 session_store.end_session 结束旧 session。"""
        mgr = self._make_mgr()
        old_session = _make_mock_session("to-end")
        new_session = _make_mock_session("fresh")
        mgr._sessions["feishu:ou_abc"] = old_session

        with patch.object(mgr, "_create_session", return_value=new_session) as mock_create, \
             patch("src.memory.session_store.end_session") as mock_end:
            mgr.reset_session("feishu", "ou_abc")

        mock_end.assert_called_once_with("to-end")

    def test_reset_no_old_session_no_error(self):
        """没有旧 session 时 reset 不应报错。"""
        mgr = self._make_mgr()
        new_session = _make_mock_session("fresh")

        with patch.object(mgr, "_create_session", return_value=new_session), \
             patch("src.memory.session_store.end_session") as mock_end:
            result = mgr.reset_session("feishu", "ou_new")

        assert result is new_session
        mock_end.assert_not_called()

    def test_reset_end_session_failure_does_not_crash(self):
        """session_store.end_session 抛异常时不应崩溃。"""
        mgr = self._make_mgr()
        old_session = _make_mock_session("old")
        new_session = _make_mock_session("new")
        mgr._sessions["feishu:ou_x"] = old_session

        with patch.object(mgr, "_create_session", return_value=new_session), \
             patch("src.memory.session_store.end_session", side_effect=RuntimeError("db error")):
            result = mgr.reset_session("feishu", "ou_x")

        assert result is new_session

    def test_reset_twice_returns_different_objects(self):
        """连续两次 reset 应返回不同对象。"""
        mgr = self._make_mgr()
        s1 = _make_mock_session("s1")
        s2 = _make_mock_session("s2")

        with patch.object(mgr, "_create_session", side_effect=[s1, s2]), \
             patch("src.memory.session_store.end_session"):
            r1 = mgr.reset_session("feishu", "ou_repeat")
            r2 = mgr.reset_session("feishu", "ou_repeat")

        assert r1 is not r2
        assert mgr._sessions["feishu:ou_repeat"] is s2


class TestSessionHandleCommandNew:
    """测试 session._handle_command("/new") 返回 is_new=True。"""

    def test_new_command_returns_is_new(self):
        from src.core.session import Session, HandleResult

        session = Session.__new__(Session)
        session.agent = MagicMock()
        session._session_manager = None
        session.session_id = "test"

        result = session._handle_command("/new")
        assert result.is_new is True
        assert result.is_command is True


class TestPromptBuilderNoContinuityGuidance:
    """确认 SESSION_CONTINUITY_GUIDANCE 已从 prompt_builder 中删除。"""

    def test_no_continuity_guidance_in_module(self):
        from src.core import prompt_builder as pb
        assert not hasattr(pb, "SESSION_CONTINUITY_GUIDANCE")

    def test_build_does_not_contain_continuity(self):
        from src.core.prompt_builder import PromptBuilder
        pb = PromptBuilder("glm")
        prompt = pb.build()
        assert "SESSION_CONTINUITY" not in prompt
        assert "暗示延续旧对话" not in prompt
        assert "泛泛提问" not in prompt
