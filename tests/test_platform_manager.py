"""测试 PlatformManager - 多平台消息网关核心调度器"""
import pytest
from unittest.mock import Mock, MagicMock, patch
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPlatformManager:
    """PlatformManager 测试"""

    def test_init_creates_instance(self):
        """测试初始化创建实例"""
        with patch('src.platforms.manager.get_session_manager') as mock_sm:
            with patch('src.platforms.background.BackgroundTaskManager') as mock_btm:
                mock_sm.return_value = Mock()
                mock_btm_instance = Mock()
                mock_btm_instance.instance.return_value = mock_btm_instance
                mock_btm.return_value = mock_btm_instance
                
                from src.platforms.manager import PlatformManager
                
                config = {"test": "config"}
                pm = PlatformManager(config)
                
                assert pm._config == config
                assert pm._running is False
                assert pm._adapters == {}

    def test_register_adapter(self):
        """测试注册 adapter"""
        with patch('src.platforms.manager.get_session_manager') as mock_sm:
            with patch('src.platforms.background.BackgroundTaskManager') as mock_btm:
                mock_sm.return_value = Mock()
                mock_btm_instance = Mock()
                mock_btm_instance.instance.return_value = mock_btm_instance
                mock_btm.return_value = mock_btm_instance
                
                from src.platforms.manager import PlatformManager
                
                pm = PlatformManager({})
                
                adapter = Mock()
                adapter.platform = "feishu"
                adapter.session_manager = None
                
                pm.register(adapter)
                
                assert "feishu" in pm._adapters
                assert pm._adapters["feishu"] == adapter
                assert adapter.session_manager == pm._session_manager

    def test_dispatch_to_unknown_platform(self):
        """测试路由到未知平台"""
        with patch('src.platforms.manager.get_session_manager') as mock_sm:
            with patch('src.platforms.background.BackgroundTaskManager') as mock_btm:
                mock_sm.return_value = Mock()
                mock_btm_instance = Mock()
                mock_btm_instance.instance.return_value = mock_btm_instance
                mock_btm.return_value = mock_btm_instance
                
                from src.platforms.manager import PlatformManager
                
                pm = PlatformManager({})
                
                # 模拟日志检查
                with patch('src.platforms.manager.logger') as mock_logger:
                    from src.platforms.base import PlatformMessage
                    msg = PlatformMessage(
                        platform="unknown",
                        sender_id="test",
                        chat_id="test",
                        message_id="1",
                        text="hello",
                    )
                    pm.dispatch(msg)
                    
                    mock_logger.warning.assert_called_once()
                    assert "无 unknown adapter" in mock_logger.warning.call_args[0][0]

    def test_dispatch_to_registered_adapter(self):
        """测试路由到已注册的 adapter"""
        with patch('src.platforms.manager.get_session_manager') as mock_sm:
            with patch('src.platforms.background.BackgroundTaskManager') as mock_btm:
                mock_sm.return_value = Mock()
                mock_btm_instance = Mock()
                mock_btm_instance.instance.return_value = mock_btm_instance
                mock_btm.return_value = mock_btm_instance
                
                from src.platforms.manager import PlatformManager
                
                pm = PlatformManager({})
                
                adapter = Mock()
                adapter.platform = "feishu"
                adapter._handle_dispatch = Mock()
                pm.register(adapter)
                
                from src.platforms.base import PlatformMessage
                msg = PlatformMessage(
                    platform="feishu",
                    sender_id="user123",
                    chat_id="chat456",
                    message_id="msg789",
                    text="hello",
                )
                pm.dispatch(msg)
                
                adapter._handle_dispatch.assert_called_once_with(
                    open_id="user123",
                    chat_id="chat456",
                    message_id="msg789",
                    text="hello",
                    reaction_id=None,
                )

    def test_singleton_instance(self):
        """测试单例模式"""
        with patch('src.platforms.manager.get_session_manager') as mock_sm:
            with patch('src.platforms.background.BackgroundTaskManager') as mock_btm:
                mock_sm.return_value = Mock()
                mock_btm_instance = Mock()
                mock_btm_instance.instance.return_value = mock_btm_instance
                mock_btm.return_value = mock_btm_instance
                
                from src.platforms.manager import PlatformManager
                
                # 重置单例
                PlatformManager._instance = None
                
                config = {}
                pm1 = PlatformManager(config)
                PlatformManager._instance = pm1
                
                pm2 = PlatformManager.instance()
                assert pm1 is pm2
                
                # 测试未初始化时抛出异常
                PlatformManager._instance = None
                with pytest.raises(RuntimeError, match="未初始化"):
                    PlatformManager.instance()
