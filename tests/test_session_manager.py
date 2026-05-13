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
        with patch('src.core.session_manager.session_store') as mock_ss:
            mock_ss.close_orphan_sessions = Mock()
            mock_ss.purge_empty_sessions = Mock()
            
            with patch('src.core.constants'):
                from src.core.session_manager import SessionManager
                
                config = {"test": "config"}
                sm = SessionManager(config)
                
                assert sm._config == config
                assert sm._sessions == {}
                assert sm._cli_session is None

    def test_get_or_create_cli_channel(self):
        """测试获取 CLI 渠道的 session（单例）"""
        with patch('src.core.session_manager.session_store') as mock_ss:
            mock_ss.close_orphan_sessions = Mock()
            mock_ss.purge_empty_sessions = Mock()
            
            with patch('src.core.constants'):
                from src.core.session_manager import SessionManager
                
                with patch.object(SessionManager, '_create_session') as mock_create:
                    mock_session = Mock()
                    mock_create.return_value = mock_session
                    
                    sm = SessionManager({})
                    
                    # 首次获取
                    session1 = sm.get_or_create("cli", "default")
                    assert session1 is mock_session
                    assert sm._cli_session is mock_session
                    
                    # 再次获取应该是同一个
                    session2 = sm.get_or_create("cli", "default")
                    assert session2 is session1

    def test_get_or_create_feishu_channel(self):
        """测试获取飞书渠道的 session（每个 sender_id 独立）"""
        with patch('src.core.session_manager.session_store') as mock_ss:
            mock_ss.close_orphan_sessions = Mock()
            mock_ss.purge_empty_sessions = Mock()
            
            with patch('src.core.constants'):
                from src.core.session_manager import SessionManager
                
                with patch.object(SessionManager, '_create_session') as mock_create:
                    mock_session1 = Mock()
                    mock_session2 = Mock()
                    mock_create.side_effect = [mock_session1, mock_session2]
                    
                    sm = SessionManager({})
                    
                    # 不同 sender_id 创建不同 session
                    session1 = sm.get_or_create("feishu", "user1")
                    session2 = sm.get_or_create("feishu", "user2")
                    
                    assert session1 is not session2
                    assert session1 is mock_session1
                    assert session2 is mock_session2

    def test_get_or_create_same_feishu_user(self):
        """测试获取同一飞书用户的 session"""
        with patch('src.core.session_manager.session_store') as mock_ss:
            mock_ss.close_orphan_sessions = Mock()
            mock_ss.purge_empty_sessions = Mock()
            
            with patch('src.core.constants'):
                from src.core.session_manager import SessionManager
                
                with patch.object(SessionManager, '_create_session') as mock_create:
                    mock_session = Mock()
                    mock_create.return_value = mock_session
                    
                    sm = SessionManager({})
                    
                    # 同一用户获取同一个 session
                    session1 = sm.get_or_create("feishu", "user1")
                    session2 = sm.get_or_create("feishu", "user1")
                    
                    assert session1 is session2
                    # 只创建一次
                    assert mock_create.call_count == 1

    def test_reset_session(self):
        """测试重置 session"""
        with patch('src.core.session_manager.session_store') as mock_ss:
            mock_ss.close_orphan_sessions = Mock()
            mock_ss.purge_empty_sessions = Mock()
            
            with patch('src.core.constants'):
                from src.core.session_manager import SessionManager
                
                with patch.object(SessionManager, '_create_session') as mock_create:
                    mock_old = Mock()
                    mock_new = Mock()
                    mock_create.return_value = mock_new
                    
                    sm = SessionManager({})
                    
                    # 先创建一个 session
                    sm._sessions["feishu:user1"] = mock_old
                    original_session = sm.get_or_create("feishu", "user1")
                    
                    # 重置
                    sm.reset_session("feishu", "user1")
                    
                    # 应该创建新的 session
                    assert mock_create.call_count == 1

    def test_thread_safety(self):
        """测试线程安全"""
        with patch('src.core.session_manager.session_store') as mock_ss:
            mock_ss.close_orphan_sessions = Mock()
            mock_ss.purge_empty_sessions = Mock()
            
            with patch('src.core.constants'):
                from src.core.session_manager import SessionManager
                
                with patch.object(SessionManager, '_create_session') as mock_create:
                    mock_session = Mock()
                    mock_create.return_value = mock_session
                    
                    sm = SessionManager({})
                    
                    # 模拟并发获取
                    import threading
                    
                    def get_session():
                        sm.get_or_create("cli", "default")
                    
                    threads = [threading.Thread(target=get_session) for _ in range(5)]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join()
                    
                    # 应该只创建一次
                    assert mock_create.call_count == 1
