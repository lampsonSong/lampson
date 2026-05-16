"""心跳机制模块：进程存活检测。

职责：
1. HeartbeatManager：管理独立子进程，定期写心跳文件
2. 心跳文件：~/.lamix/heartbeat/<pid>.json
3. 收到 kill 信号时标记 user_stopped，避免 watchdog 误重拉
4. 使用 multiprocessing.Process 绕过 GIL，确保心跳不受主进程阻塞影响
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
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


def _heartbeat_worker(pid: int, task_id: str | None, interval: int,
                      lamix_dir: str | None = None) -> None:
    """子进程心跳写入循环，独立于主进程 GIL。

    通过 stop flag 文件接收停止信号。
    Args:
        lamix_dir: 覆盖 LAMIX_DIR（测试用），None 则使用默认值。
    """
    _base = Path(lamix_dir) if lamix_dir else LAMIX_DIR
    heartbeat_dir = _base / "heartbeat"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_path = heartbeat_dir / f"{pid}.json"
    stop_flag_path = heartbeat_dir.parent / "stop.flag"

    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

    def _write(user_stopped: bool = False) -> None:
        data = {
            "pid": pid,
            "task_id": task_id,
            "user_stopped": user_stopped,
            "last_heartbeat": _now(),
        }
        tmp = heartbeat_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(heartbeat_path)

    # 写入第一条
    try:
        _write()
    except Exception:
        pass

    while True:
        time.sleep(interval)

        # 检查停止信号
        if stop_flag_path.exists():
            try:
                content = stop_flag_path.read_text(encoding="utf-8").strip()
                if content.isdigit() and int(content) == pid:
                    stop_flag_path.unlink()
                    # 写入 user_stopped 标记后退出
                    try:
                        _write(user_stopped=True)
                    except Exception:
                        pass
                    return
            except (OSError, ValueError):
                pass

        # 写心跳
        try:
            _write()
        except Exception:
            pass


class HeartbeatManager:
    """心跳管理器，使用独立子进程绕过 GIL。

    子进程不受主进程 GIL 影响，即使主进程在执行耗时同步操作
    （如 LLM 调用、文件搜索等），心跳也能正常写入。
    """

    def __init__(self, task_id: str | None = None, lamix_dir: str | None = None) -> None:
        self._pid = os.getpid()
        self._task_id = task_id
        self._lamix_dir = Path(lamix_dir) if lamix_dir else LAMIX_DIR
        self._hb_dir = self._lamix_dir / "heartbeat"
        self._process: subprocess.Popen | None = None

    def _heartbeat_path(self) -> Path:
        return self._hb_dir / f"{self._pid}.json"

    def _check_stop_flag(self) -> bool:
        """检查外部停止信号（Windows 优雅终止机制）。

        Returns:
            True 表示检测到停止信号
        """
        flag_path = self._hb_dir.parent / "stop.flag"
        if flag_path.exists():
            try:
                content = flag_path.read_text(encoding="utf-8").strip()
                if content.isdigit() and int(content) == self._pid:
                    flag_path.unlink()
                    return True
            except (OSError, ValueError):
                pass
        return False

    def start(self) -> None:
        """启动心跳子进程。"""
        import multiprocessing as mp

        self._process = mp.Process(
            target=_heartbeat_worker,
            args=(self._pid, self._task_id, HEARTBEAT_INTERVAL, str(self._lamix_dir)),
            name="HeartbeatProcess",
            daemon=True,
        )
        self._process.start()

    def stop(self, user_initiated: bool = False) -> None:
        """停止心跳子进程。

        Args:
            user_initiated: True 表示用户主动停止（写 user_stopped 标记，供 watchdog 识别）
        """
        if self._process is None:
            return

        if user_initiated:
            # 通过 stop flag 文件通知子进程优雅退出（会写 user_stopped=True）
            flag_path = self._hb_dir.parent / "stop.flag"
            try:
                self._hb_dir.mkdir(parents=True, exist_ok=True)
                flag_path.write_text(str(self._pid), encoding="utf-8")
            except OSError:
                pass
            # 等待子进程处理 stop flag 并退出
            self._process.join(timeout=HEARTBEAT_INTERVAL + 3)
        else:
            # 非用户主动退出，直接终止子进程
            self._process.terminate()
            self._process.join(timeout=5)

        # 如果子进程还活着，强制杀掉
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=3)

        self._process = None

        if not user_initiated:
            self._remove()

    def _remove(self) -> None:
        """删除心跳文件。"""
        path = self._heartbeat_path()
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


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
