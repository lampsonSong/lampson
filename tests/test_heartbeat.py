"""测试 heartbeat.py - 心跳机制"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestHeartbeatRecord:
    def test_init(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord(pid=12345)
        assert record.pid == 12345

    def test_to_dict(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord(pid=12345)
        data = record.to_dict()
        assert data["pid"] == 12345

    def test_from_dict(self):
        from src.core.heartbeat import HeartbeatRecord
        data = {"pid": 12345}
        record = HeartbeatRecord.from_dict(data)
        assert record.pid == 12345


class TestHeartbeatManager:
    def test_init(self):
        import os
        from src.core.heartbeat import HeartbeatManager
        manager = HeartbeatManager()
        assert manager._pid == os.getpid()
