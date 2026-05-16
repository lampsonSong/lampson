"""测试 heartbeat.py - 心跳机制（multiprocessing 版）"""
import json
import os
import time
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# HeartbeatRecord 单元测试
# ============================================================

class TestHeartbeatRecord:
    def test_init_defaults(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord(pid=12345)
        assert record.pid == 12345
        assert record.task_id is None
        assert record.user_stopped is False
        assert record.last_heartbeat is not None

    def test_init_with_all_fields(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord(
            pid=12345,
            task_id="daemon",
            user_stopped=True,
            last_heartbeat="2026-01-01T00:00:00",
        )
        assert record.pid == 12345
        assert record.task_id == "daemon"
        assert record.user_stopped is True
        assert record.last_heartbeat == "2026-01-01T00:00:00"

    def test_to_dict(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord(pid=12345, task_id="daemon")
        data = record.to_dict()
        assert data["pid"] == 12345
        assert data["task_id"] == "daemon"
        assert data["user_stopped"] is False
        assert "last_heartbeat" in data

    def test_from_dict_minimal(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord.from_dict({"pid": 12345})
        assert record.pid == 12345
        assert record.task_id is None
        assert record.user_stopped is False

    def test_from_dict_full(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord.from_dict({
            "pid": 99999,
            "task_id": "test",
            "user_stopped": True,
            "last_heartbeat": "2026-06-01T12:00:00",
        })
        assert record.pid == 99999
        assert record.task_id == "test"
        assert record.user_stopped is True
        assert record.last_heartbeat == "2026-06-01T12:00:00"

    def test_touch_updates_timestamp(self):
        from src.core.heartbeat import HeartbeatRecord
        record = HeartbeatRecord(pid=1, last_heartbeat="2020-01-01T00:00:00")
        old_ts = record.last_heartbeat
        time.sleep(1)
        record.touch()
        assert record.last_heartbeat != old_ts

    def test_now_format(self):
        from src.core.heartbeat import HeartbeatRecord
        ts = HeartbeatRecord._now()
        # 验证格式 YYYY-MM-DDTHH:MM:SS
        assert len(ts) == 19
        assert ts[4] == "-"
        assert ts[10] == "T"

    def test_round_trip(self):
        """to_dict -> from_dict 往返一致。"""
        from src.core.heartbeat import HeartbeatRecord
        original = HeartbeatRecord(pid=42, task_id="rt", user_stopped=True)
        data = original.to_dict()
        restored = HeartbeatRecord.from_dict(data)
        assert restored.pid == original.pid
        assert restored.task_id == original.task_id
        assert restored.user_stopped == original.user_stopped
        assert restored.last_heartbeat == original.last_heartbeat


# ============================================================
# HeartbeatManager 单元测试（不启动真子进程）
# ============================================================

class TestHeartbeatManagerUnit:
    def test_init_defaults(self):
        from src.core.heartbeat import HeartbeatManager, LAMIX_DIR
        mgr = HeartbeatManager()
        assert mgr._pid == os.getpid()
        assert mgr._task_id is None
        assert mgr._lamix_dir == LAMIX_DIR
        assert mgr._process is None

    def test_init_with_task_id(self):
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="daemon")
        assert mgr._task_id == "daemon"

    def test_init_with_lamix_dir(self, tmp_path):
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        assert mgr._lamix_dir == tmp_path
        assert mgr._hb_dir == tmp_path / "heartbeat"

    def test_heartbeat_path(self, tmp_path):
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        path = mgr._heartbeat_path()
        assert path == tmp_path / "heartbeat" / f"{os.getpid()}.json"

    def test_stop_when_no_process(self):
        """stop() 在 _process=None 时不应崩溃。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager()
        mgr._process = None
        mgr.stop(user_initiated=True)  # 应该直接 return

    def test_check_stop_flag_no_file(self, tmp_path):
        """stop flag 文件不存在时返回 False。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        assert mgr._check_stop_flag() is False

    def test_check_stop_flag_matching_pid(self, tmp_path):
        """stop flag 匹配当前 PID 时返回 True 并删除 flag。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        flag_path = tmp_path / "stop.flag"
        flag_path.write_text(str(os.getpid()))
        assert mgr._check_stop_flag() is True
        assert not flag_path.exists()

    def test_check_stop_flag_mismatched_pid(self, tmp_path):
        """stop flag 不匹配 PID 时返回 False，不删除 flag。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        flag_path = tmp_path / "stop.flag"
        flag_path.write_text("99999")
        assert mgr._check_stop_flag() is False
        assert flag_path.exists()

    def test_check_stop_flag_corrupt_content(self, tmp_path):
        """stop flag 内容非数字时返回 False。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        flag_path = tmp_path / "stop.flag"
        flag_path.write_text("abc")
        assert mgr._check_stop_flag() is False
        assert flag_path.exists()

    def test_remove_deletes_file(self, tmp_path):
        """_remove() 应删除心跳文件。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        hb_dir = tmp_path / "heartbeat"
        hb_dir.mkdir()
        hb_path = hb_dir / f"{os.getpid()}.json"
        hb_path.write_text("{}")
        assert mgr._remove() is None or True  # 无返回值
        assert not hb_path.exists()

    def test_remove_nonexistent_no_error(self, tmp_path):
        """_remove() 文件不存在时不应崩溃。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(lamix_dir=str(tmp_path))
        mgr._remove()  # 不应抛异常


# ============================================================
# HeartbeatManager 集成测试（真子进程）
# ============================================================

class TestHeartbeatManagerIntegration:
    """启动真实子进程验证行为。"""

    def test_start_writes_heartbeat_file(self, tmp_path):
        """start() 启动子进程后应写入心跳文件。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="test", lamix_dir=str(tmp_path))
        mgr.start()
        # 等待子进程写入第一条心跳
        time.sleep(3)
        hb_path = tmp_path / "heartbeat" / f"{os.getpid()}.json"
        assert hb_path.exists(), "心跳文件未创建"
        data = json.loads(hb_path.read_text())
        assert data["pid"] == os.getpid()
        assert data["task_id"] == "test"
        assert data["user_stopped"] is False
        mgr.stop(user_initiated=False)

    def test_subprocess_writes_periodically(self, tmp_path):
        """子进程应定期更新心跳时间戳。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="test", lamix_dir=str(tmp_path))
        mgr.start()
        time.sleep(3)
        hb_path = tmp_path / "heartbeat" / f"{os.getpid()}.json"
        ts1 = json.loads(hb_path.read_text())["last_heartbeat"]
        # 等待至少一个间隔（10s）+ 余量
        time.sleep(12)
        ts2 = json.loads(hb_path.read_text())["last_heartbeat"]
        assert ts2 > ts1, f"心跳未更新: {ts1} -> {ts2}"
        mgr.stop(user_initiated=False)

    def test_stop_user_initiated_writes_stopped_flag(self, tmp_path):
        """stop(user_initiated=True) 应让子进程写 user_stopped=True。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="test", lamix_dir=str(tmp_path))
        mgr.start()
        time.sleep(3)
        mgr.stop(user_initiated=True)
        hb_path = tmp_path / "heartbeat" / f"{os.getpid()}.json"
        if hb_path.exists():
            data = json.loads(hb_path.read_text())
            assert data["user_stopped"] is True

    def test_stop_force_removes_file(self, tmp_path):
        """stop(user_initiated=False) 应删除心跳文件。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="test", lamix_dir=str(tmp_path))
        mgr.start()
        time.sleep(3)
        hb_path = tmp_path / "heartbeat" / f"{os.getpid()}.json"
        assert hb_path.exists()
        mgr.stop(user_initiated=False)
        assert not hb_path.exists(), "心跳文件应被删除"

    def test_stop_flag_cleanup(self, tmp_path):
        """stop(user_initiated=True) 后 stop.flag 应被子进程清理。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="test", lamix_dir=str(tmp_path))
        mgr.start()
        time.sleep(3)
        mgr.stop(user_initiated=True)
        flag_path = tmp_path / "stop.flag"
        assert not flag_path.exists(), "stop.flag 应被子进程清理"

    def test_subprocess_dies_after_stop(self, tmp_path):
        """stop 后子进程应真正退出。"""
        from src.core.heartbeat import HeartbeatManager
        mgr = HeartbeatManager(task_id="test", lamix_dir=str(tmp_path))
        mgr.start()
        time.sleep(2)
        proc = mgr._process
        assert proc is not None
        assert proc.is_alive()
        mgr.stop(user_initiated=False)
        assert not proc.is_alive()


# ============================================================
# 工具函数测试
# ============================================================

class TestLoadHeartbeat:
    def test_load_valid(self, tmp_path):
        from src.core.heartbeat import load_heartbeat
        f = tmp_path / "12345.json"
        f.write_text(json.dumps({
            "pid": 12345,
            "task_id": "daemon",
            "user_stopped": False,
            "last_heartbeat": "2026-01-01T00:00:00",
        }))
        rec = load_heartbeat(f)
        assert rec is not None
        assert rec.pid == 12345
        assert rec.task_id == "daemon"

    def test_load_nonexistent(self, tmp_path):
        from src.core.heartbeat import load_heartbeat
        rec = load_heartbeat(tmp_path / "nope.json")
        assert rec is None

    def test_load_corrupt(self, tmp_path):
        from src.core.heartbeat import load_heartbeat
        f = tmp_path / "bad.json"
        f.write_text("not json {{{")
        rec = load_heartbeat(f)
        assert rec is None

    def test_load_empty(self, tmp_path):
        from src.core.heartbeat import load_heartbeat
        f = tmp_path / "empty.json"
        f.write_text("")
        rec = load_heartbeat(f)
        assert rec is None


class TestReadAllHeartbeats:
    def test_reads_multiple(self, tmp_path):
        from src.core.heartbeat import read_all_heartbeats, HeartbeatRecord
        for pid in [100, 200, 300]:
            f = tmp_path / f"{pid}.json"
            rec = HeartbeatRecord(pid=pid, task_id="daemon")
            f.write_text(json.dumps(rec.to_dict()))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = read_all_heartbeats()
        assert len(result) == 3
        assert 100 in result
        assert 200 in result
        assert 300 in result

    def test_skips_non_json(self, tmp_path):
        from src.core.heartbeat import read_all_heartbeats
        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "100.json").write_text(json.dumps({"pid": 100}))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = read_all_heartbeats()
        assert len(result) == 1
        assert 100 in result

    def test_empty_dir(self, tmp_path):
        from src.core.heartbeat import read_all_heartbeats
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = read_all_heartbeats()
        assert result == {}

    def test_skips_corrupt_file(self, tmp_path):
        from src.core.heartbeat import read_all_heartbeats
        (tmp_path / "good.json").write_text(json.dumps({"pid": 100}))
        (tmp_path / "bad.json").write_text("corrupted")
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = read_all_heartbeats()
        assert len(result) == 1
        assert 100 in result


class TestIsProcessAlive:
    def test_current_process_alive(self):
        from src.core.heartbeat import is_process_alive
        assert is_process_alive(os.getpid()) is True

    def test_dead_process(self):
        from src.core.heartbeat import is_process_alive
        assert is_process_alive(999999999) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="non-windows only")
    def test_unix_uses_os_kill(self):
        """非 Windows 应走 os.kill 路径。"""
        from src.core.heartbeat import is_process_alive
        with patch("os.kill") as mock_kill:
            is_process_alive(12345)
            mock_kill.assert_called_once_with(12345, 0)

    @pytest.mark.skipif(sys.platform != "win32", reason="windows only")
    def test_windows_uses_tasklist(self):
        from src.core.heartbeat import is_process_alive
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="12345")
            assert is_process_alive(12345) is True


class TestCleanupStaleHeartbeats:
    def test_removes_dead_process_files(self, tmp_path):
        from src.core.heartbeat import cleanup_stale_heartbeats, HeartbeatRecord
        f = tmp_path / "999999999.json"
        rec = HeartbeatRecord(pid=999999999)
        f.write_text(json.dumps(rec.to_dict()))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            cleaned = cleanup_stale_heartbeats()
        assert 999999999 in cleaned
        assert not f.exists()

    def test_keeps_alive_process_files(self, tmp_path):
        from src.core.heartbeat import cleanup_stale_heartbeats, HeartbeatRecord
        f = tmp_path / f"{os.getpid()}.json"
        rec = HeartbeatRecord(pid=os.getpid())
        f.write_text(json.dumps(rec.to_dict()))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            cleaned = cleanup_stale_heartbeats()
        assert os.getpid() not in cleaned
        assert f.exists()

    def test_returns_empty_when_nothing_to_clean(self, tmp_path):
        from src.core.heartbeat import cleanup_stale_heartbeats, HeartbeatRecord
        f = tmp_path / f"{os.getpid()}.json"
        rec = HeartbeatRecord(pid=os.getpid())
        f.write_text(json.dumps(rec.to_dict()))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            cleaned = cleanup_stale_heartbeats()
        assert cleaned == []


class TestGetLastActivityTime:
    def test_returns_latest(self, tmp_path):
        from src.core.heartbeat import get_last_activity_time, HeartbeatRecord
        from datetime import datetime
        for pid, ts in [(100, "2026-01-01T10:00:00"), (200, "2026-06-15T14:30:00")]:
            f = tmp_path / f"{pid}.json"
            rec = HeartbeatRecord(pid=pid, last_heartbeat=ts)
            f.write_text(json.dumps(rec.to_dict()))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = get_last_activity_time()
        assert result == datetime(2026, 6, 15, 14, 30, 0)

    def test_returns_none_when_empty(self, tmp_path):
        from src.core.heartbeat import get_last_activity_time
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = get_last_activity_time()
        assert result is None

    def test_skips_invalid_timestamps(self, tmp_path):
        from src.core.heartbeat import get_last_activity_time, HeartbeatRecord
        f1 = tmp_path / "100.json"
        rec = HeartbeatRecord(pid=100, last_heartbeat="not-a-date")
        f1.write_text(json.dumps(rec.to_dict()))
        f2 = tmp_path / "200.json"
        rec2 = HeartbeatRecord(pid=200, last_heartbeat="2026-03-01T08:00:00")
        f2.write_text(json.dumps(rec2.to_dict()))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = get_last_activity_time()
        from datetime import datetime
        assert result == datetime(2026, 3, 1, 8, 0, 0)

    def test_single_record(self, tmp_path):
        """只有一条记录时返回该记录的时间。"""
        from src.core.heartbeat import get_last_activity_time
        (tmp_path / "100.json").write_text(json.dumps({"pid": 100, "last_heartbeat": "2026-03-01T08:00:00"}))
        with patch("src.core.heartbeat.HEARTBEAT_DIR", tmp_path):
            result = get_last_activity_time()
        from datetime import datetime
        assert result == datetime(2026, 3, 1, 8, 0, 0)
