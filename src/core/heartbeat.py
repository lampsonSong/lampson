"""心跳机制模块：进程存活检测。

职责：
1. HeartbeatManager：进程内线程，定期写心跳文件
2. 心跳文件：~/.lamix/heartbeat/<pid>.json
3. 收到 kill 信号时标记 user_stopped，避免 watchdog 误重拉
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from src.core.config import LAMIX_DIR

HEARTBEAT_DIR = LAMIX_DIR / "heartbeat"
from src.core.constants import HEARTBEAT_INTERVAL


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

    def _check_stop_flag(self) -> bool:
        """检查外部停止信号（Windows 优雅终止机制）。

        Returns:
            True 表示检测到停止信号
        """
        flag_path = HEARTBEAT_DIR.parent / "stop.flag"
        if flag_path.exists():
            try:
                # 检查 flag 文件中的 pid 是否匹配当前进程
                content = flag_path.read_text(encoding="utf-8").strip()
                if content.isdigit() and int(content) == self._pid:
                    flag_path.unlink()
                    return True
            except (OSError, ValueError):
                # 如果读取失败或 pid 不匹配，忽略
                pass
        return False

    def _loop(self) -> None:
        """心跳线程主循环。"""
        while not self._stopped.wait(HEARTBEAT_INTERVAL):
            # 检查外部停止信号（Windows 优雅终止）
            if self._check_stop_flag():
                self.stop(user_initiated=False)
                break

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
    """跨平台检查进程是否存活。
    
    Windows 上用 tasklist（os.kill(pid, 0) 在该平台不可靠，
    进程已死仍可能返回 True），其他平台用 os.kill(pid, 0)。
    """
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        except (subprocess.SubprocessError, OSError):
            return False
    else:
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


def get_last_activity_time() -> datetime | None:
    """获取用户最后一次活跃时间（所有心跳中最新的 last_heartbeat）。

    用于审计判断：24小时未使用 / 最后使用后1小时。
    """
    records = read_all_heartbeats()
    latest = None
    for rec in records.values():
        if rec.last_heartbeat:
            try:
                t = datetime.fromisoformat(rec.last_heartbeat)
                if latest is None or t > latest:
                    latest = t
            except (ValueError, TypeError):
                pass
    return latest