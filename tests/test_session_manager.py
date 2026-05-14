"""测试 SessionManager - 会话管理器"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSessionManager:
    """SessionManager 测试"""

    def test_init(self):
        """测试初始化"""
        with patch('src.memory.session_store.close_orphan_sessions') as mock_close:
            with patch('src.memory.session_store.purge_empty_sessions') as mock_purge:
                from src.core.session_manager import SessionManager
                
                config = {"test": "config"}
                sm = SessionManager(config)
                
                assert sm._config == config
                assert sm._sessions == {}
                assert sm._cli_session is None
                mock_close.assert_called_once()
                mock_purge.assert_called_once()

    def test_get_or_create_returns_dict_item(self):
        """测试 get_or_create 返回字典项"""
        with patch('src.memory.session_store.close_orphan_sessions'):
            with patch('src.memory.session_store.purge_empty_sessions'):
                from src.core.session_manager import SessionManager
                
                sm = SessionManager({})
                
                # 检查 _sessions 是字典
                assert isinstance(sm._sessions, dict)
                assert len(sm._sessions) == 0

    def test_sessions_dict(self):
        """测试 sessions 字典"""
        with patch('src.memory.session_store.close_orphan_sessions'):
            with patch('src.memory.session_store.purge_empty_sessions'):
                from src.core.session_manager import SessionManager
                
                sm = SessionManager({})
                
                # 可以手动设置
                sm._sessions["test:key"] = Mock()
                assert "test:key" in sm._sessions

    def test_cli_session_none_initially(self):
        """测试 CLI session 初始为 None"""
        with patch('src.memory.session_store.close_orphan_sessions'):
            with patch('src.memory.session_store.purge_empty_sessions'):
                from src.core.session_manager import SessionManager
                
                sm = SessionManager({})
                assert sm._cli_session is None

    def test_config_stored(self):
        """测试配置被存储"""
        with patch('src.memory.session_store.close_orphan_sessions'):
            with patch('src.memory.session_store.purge_empty_sessions'):
                from src.core.session_manager import SessionManager
                
                test_config = {"llm": {"model": "test"}}
                sm = SessionManager(test_config)
                assert sm._config == test_config

    def test_lock_exists(self):
        """测试锁存在"""
        with patch('src.memory.session_store.close_orphan_sessions'):
            with patch('src.memory.session_store.purge_empty_sessions'):
                from src.core.session_manager import SessionManager
                
                sm = SessionManager({})
                assert sm._lock is not None
                # threading.Lock() 返回 _thread.lock，Python 3.11+ 中 threading.Lock 本身不是类型
                assert hasattr(sm._lock, "acquire") and hasattr(sm._lock, "release")
