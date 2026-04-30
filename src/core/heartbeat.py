"""心跳机制模块：进程存活检测。

职责：
1. HeartbeatManager：进程内线程，定期写心跳文件
2. 心跳文件：~/.lampson/heartbeat/<pid>.json
3. 收到 kill 信号时标记 user_stopped，避免 watchdog 误重拉
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from src.core.config import LAMPSON_DIR

HEARTBEAT_DIR = LAMPSON_DIR / "heartbeat"
HEARTBEAT_INTERVAL = 10  # 秒


class HeartbeatRecord:
    """心跳记录文件内容。"""

    def __init__(
        self,
        pid: int,
        task_id: str | None = None,
        user_stopped: bool = False,
        last_heartbeat: str | None = None,
    ) -> None:
        self.pid = pid
        self.task_id = task_id
        self.user_stopped = user_stopped
        self.last_heartbeat = last_heartbeat or self._now()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "task_id": self.task_id,
            "user_stopped": self.user_stopped,
            "last_heartbeat": self.last_heartbeat,
        }

    def touch(self) -> None:
        self.last_heartbeat = self._now()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HeartbeatRecord":
        return cls(
            pid=data["pid"],
            task_id=data.get("task_id"),
            user_stopped=data.get("user_stopped", False),
            last_heartbeat=data.get("last_heartbeat"),
        )


class HeartbeatManager:
    """进程内心跳管理器。

    在独立线程中定期向心跳文件写入记录。
    收到 SIGTERM/SIGINT 时标记 user_stopped。
    """

    def __init__(self, task_id: str | None = None) -> None:
        self._pid = os.getpid()
        self._task_id = task_id
        self._record = HeartbeatRecord(pid=self._pid, task_id=task_id)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat_file: Path | None = None

    def _heartbeat_path(self) -> Path:
        return HEARTBEAT_DIR / f"{self._pid}.json"

    def _write(self) -> None:
        """原子写入心跳文件。"""
        HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        path = self._heartbeat_path()
        tmp = path.with_suffix(".tmp")
        with self._lock:
            self._record.touch()
            data = self._record.to_dict()
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        self._heartbeat_file = path

    def _loop(self) -> None:
        """心跳线程主循环。"""
        while not self._stopped.wait(HEARTBEAT_INTERVAL):
            try:
                self._write()
            except Exception:
                pass  # 心跳写入失败不崩溃

    def start(self) -> None:
        """启动心跳线程，写入第一条记录。"""
        self._write()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="HeartbeatThread")
        self._thread.start()

    def stop(self, user_initiated: bool = False) -> None:
        """停止心跳线程。

        Args:
            user_initiated: True 表示用户主动停止（不删除心跳文件，供 watchdog 识别）
        """
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if user_initiated:
            with self._lock:
                self._record.user_stopped = True
            try:
                self._write()
            except Exception:
                pass
        else:
            # 非用户主动退出，删除心跳文件
            self._remove()

    def _remove(self) -> None:
        """删除心跳文件。"""
        path = self._heartbeat_path()
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def update_task_id(self, task_id: str | None) -> None:
        """更新当前任务 ID。"""
        with self._lock:
            self._record.task_id = task_id
        self._write()


def load_heartbeat(path: Path) -> HeartbeatRecord | None:
    """读取心跳文件，返回记录或 None（文件不存在或损坏）。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return HeartbeatRecord.from_dict(data)
    except Exception:
        return None


def read_all_heartbeats() -> dict[int, HeartbeatRecord]:
    """读取所有心跳文件，返回 {pid: record}。"""
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    result: dict[int, HeartbeatRecord] = {}
    for p in HEARTBEAT_DIR.iterdir():
        if p.suffix != ".json":
            continue
        rec = load_heartbeat(p)
        if rec is not None:
            result[rec.pid] = rec
    return result


def is_process_alive(pid: int) -> bool:
    """检查进程是否存活（通过 os.kill 试探）。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_stale_heartbeats() -> list[int]:
    """清理已死亡进程的心跳文件，返回被清理的 pid 列表。"""
    cleaned: list[int] = []
    for pid, rec in read_all_heartbeats().items():
        if not is_process_alive(pid):
            path = HEARTBEAT_DIR / f"{pid}.json"
            try:
                path.unlink()
                cleaned.append(pid)
            except OSError:
                pass
    return cleaned
