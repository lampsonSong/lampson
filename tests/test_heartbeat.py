"""测试 heartbeat.py - 心跳机制"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestHeartbeatRecord:
    """心跳记录测试"""

    def test_init(self):
        """测试初始化"""
        from src.core.heartbeat import HeartbeatRecord
        
        record = HeartbeatRecord(pid=12345)
        
        assert record.pid == 12345
        assert record.task_id is None
        assert record.user_stopped is False
        assert record.last_heartbeat is not None

    def test_init_with_task_id(self):
        """测试带 task_id 初始化"""
        from src.core.heartbeat import HeartbeatRecord
        
        record = HeartbeatRecord(pid=12345, task_id="test_task")
        
        assert record.pid == 12345
        assert record.task_id == "test_task"

    def test_init_with_user_stopped(self):
        """测试带 user_stopped 初始化"""
        from src.core.heartbeat import HeartbeatRecord
        
        record = HeartbeatRecord(pid=12345, user_stopped=True)
        
        assert record.user_stopped is True

    def test_to_dict(self):
        """测试转换为 dict"""
        from src.core.heartbeat import HeartbeatRecord
        
        record = HeartbeatRecord(pid=12345, task_id="test", user_stopped=True)
        data = record.to_dict()
        
        assert data["pid"] == 12345
        assert data["task_id"] == "test"
        assert data["user_stopped"] is True
        assert "last_heartbeat" in data

    def test_touch(self):
        """测试更新心跳时间"""
        from src.core.heartbeat import HeartbeatRecord
        import time
        
        record = HeartbeatRecord(pid=12345)
        original = record.last_heartbeat
        
        time.sleep(0.01)
        record.touch()
        
        assert record.last_heartbeat != original

    def test_from_dict(self):
        """测试从 dict 创建"""
        from src.core.heartbeat import HeartbeatRecord
        
        data = {
            "pid": 12345,
            "task_id": "test",
            "user_stopped": True,
            "last_heartbeat": "2024-01-01T00:00:00",
        }
        
        record = HeartbeatRecord.from_dict(data)
        
        assert record.pid == 12345
        assert record.task_id == "test"
        assert record.user_stopped is True


class TestHeartbeatManager:
    """心跳管理器测试"""

    def test_init(self):
        """测试初始化"""
        import os
        from src.core.heartbeat import HeartbeatManager
        
        with patch('src.core.heartbeat.LAMIX_DIR', Path(tempfile.mkdtemp())):
            manager = HeartbeatManager()
            
            assert manager._pid == os.getpid()
            assert manager._stopped.is_set() is False

    def test_init_with_task_id(self):
        """测试带 task_id 初始化"""
        from src.core.heartbeat import HeartbeatManager
        
        with patch('src.core.heartbeat.LAMIX_DIR', Path(tempfile.mkdtemp())):
            manager = HeartbeatManager(task_id="test_task")
            
            assert manager._task_id == "test_task"
            assert manager._record.task_id == "test_task"

    def test_stopped_initially_false(self):
        """测试初始状态不是停止"""
        from src.core.heartbeat import HeartbeatManager
        
        with patch('src.core.heartbeat.LAMIX_DIR', Path(tempfile.mkdtemp())):
            manager = HeartbeatManager()
            
            assert manager._stopped.is_set() is False

    def test_stop(self):
        """测试停止"""
        from src.core.heartbeat import HeartbeatManager
        
        with patch('src.core.heartbeat.LAMIX_DIR', Path(tempfile.mkdtemp())):
            manager = HeartbeatManager()
            manager.stop()
            
            assert manager._stopped.is_set() is True


class TestHeartbeatFunctions:
    """心跳函数测试"""

    def test_read_all_heartbeats(self):
        """测试读取所有心跳"""
        from src.core.heartbeat import read_all_heartbeats
        
        with patch('src.core.heartbeat.HEARTBEAT_DIR', Path(tempfile.mkdtemp())):
            heartbeats = read_all_heartbeats()
            
            assert isinstance(heartbeats, list)

    def test_load_heartbeat_not_found(self):
        """测试加载不存在的心跳"""
        from src.core.heartbeat import load_heartbeat
        
        with patch('src.core.heartbeat.HEARTBEAT_DIR', Path(tempfile.mkdtemp())):
            result = load_heartbeat(99999)
            
            assert result is None

    def test_cleanup_stale_heartbeats(self):
        """测试清理过期心跳"""
        from src.core.heartbeat import cleanup_stale_heartbeats
        
        with patch('src.core.heartbeat.HEARTBEAT_DIR', Path(tempfile.mkdtemp())):
            with patch('src.core.heartbeat.HEARTBEAT_TIMEOUT', 0):
                count = cleanup_stale_heartbeats()
                
                # 临时目录应该没有过期心跳
                assert count == 0
