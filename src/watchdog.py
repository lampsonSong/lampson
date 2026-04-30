"""Watchdog 进程：监控 daemon 心跳，超时则重启。

启动方式：由 launchd 管理（独立于 daemon 的 service）。
心跳超时（30 秒无心跳）→ 检查是否 user_stopped → 否则重启 daemon。

心跳文件：~/.lampson/heartbeat/<pid>.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from src.core.config import LAMPSON_DIR
from src.core.heartbeat import (
    HEARTBEAT_DIR,
    HEARTBEAT_INTERVAL,
    read_all_heartbeats,
    cleanup_stale_heartbeats,
    load_heartbeat,
    is_process_alive,
)

HEARTBEAT_TIMEOUT = 30  # 秒，无心跳则认为死亡
WATCHDOG_INTERVAL = 10  # 秒，检查频率
LOG_DIR = LAMPSON_DIR / "logs"
DAEMON_LAUNCHCTL_LABEL = "com.lampson.gateway"


def _log(msg: str) -> None:
    """写日志。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "watchdog.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    print(f"[watchdog] {msg}", flush=True)


def _load_config() -> dict:
    """加载配置。"""
    config_path = LAMPSON_DIR / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _restart_daemon() -> None:
    """通过 launchctl 重启 daemon。"""
    _log("尝试重启 daemon...")
    try:
        # kill 旧进程（如果还在的话）
        pid_path = LOG_DIR / "daemon.pid"
        if pid_path.exists():
            try:
                old_pid = int(pid_path.read_text().strip())
                os.kill(old_pid, signal.SIGTERM)
                time.sleep(1)
            except (ValueError, OSError):
                pass

        # launchctl 重启
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{DAEMON_LAUNCHCTL_LABEL}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            _log("daemon 重启请求已发送")
        else:
            _log(f"launchctl kickstart 失败: {result.stderr}")
    except Exception as e:
        _log(f"重启 daemon 失败: {e}")


class Watchdog:
    """看门狗主逻辑。"""

    def __init__(self) -> None:
        self._shutdown = threading.Event()
        self._daemon_pid: int | None = None  # 监控的 daemon pid

    def _find_daemon_pid(self) -> int | None:
        """从进程列表中找到 daemon 的 pid。"""
        # 优先从 pid 文件读
        pid_file = LOG_DIR / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if is_process_alive(pid):
                    return pid
            except (ValueError, OSError):
                pass

        # 通过进程名找
        try:
            result = subprocess.run(
                ["pgrep", "-f", "python.*src.daemon"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                pids = [int(p) for p in result.stdout.strip().split("\n") if p]
                for pid in pids:
                    if pid != os.getpid():
                        return pid
        except Exception:
            pass
        return None

    def _check_daemon(self) -> None:
        """检查 daemon 心跳。"""
        # 先找到 daemon pid
        pid = self._find_daemon_pid()
        if pid is None:
            _log("未找到 daemon 进程，尝试重启")
            _restart_daemon()
            return

        if pid != self._daemon_pid:
            _log(f"daemon pid 变化: {self._daemon_pid} -> {pid}")
            self._daemon_pid = pid

        # 读心跳文件
        heartbeat_path = HEARTBEAT_DIR / f"{pid}.json"
        if not heartbeat_path.exists():
            _log(f"daemon ({pid}) 心跳文件不存在，尝试重启")
            _restart_daemon()
            return

        rec = load_heartbeat(heartbeat_path)
        if rec is None:
            _log(f"daemon ({pid}) 心跳文件损坏，尝试重启")
            _restart_daemon()
            return

        # 检查是否被用户主动停止
        if rec.user_stopped:
            _log(f"daemon ({pid}) 被用户主动停止，不重拉")
            return

        # 检查心跳是否超时
        try:
            last = datetime.fromisoformat(rec.last_heartbeat)
        except ValueError:
            _log(f"daemon ({pid}) 心跳时间格式错误，尝试重启")
            _restart_daemon()
            return

        elapsed = (datetime.now() - last).total_seconds()
        if elapsed > HEARTBEAT_TIMEOUT:
            _log(f"daemon ({pid}) 心跳超时（{elapsed:.0f}s > {HEARTBEAT_TIMEOUT}s），尝试重启")
            _restart_daemon()
        else:
            _log(f"daemon ({pid}) 心跳正常（{elapsed:.0f}s 前）")

    def _cleanup_loop(self) -> None:
        """定期清理已死亡进程的心跳文件。"""
        while not self._shutdown.is_set():
            self._shutdown.wait(WATCHDOG_INTERVAL * 3)
            if self._shutdown.is_set():
                break
            try:
                cleaned = cleanup_stale_heartbeats()
                if cleaned:
                    _log(f"清理了 {len(cleaned)} 个过时心跳文件: {cleaned}")
            except Exception:
                pass

    def _signal_handler(self, signum: int, _frame) -> None:
        _log(f"收到信号 {signum}，退出")
        self._shutdown.set()

    def run(self) -> None:
        """主循环。"""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        _log(f"Watchdog 启动 (PID={os.getpid()})")
        cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        cleanup_thread.start()

        while not self._shutdown.is_set():
            self._shutdown.wait(WATCHDOG_INTERVAL)
            if self._shutdown.is_set():
                break
            try:
                self._check_daemon()
            except Exception as e:
                _log(f"检查 daemon 时异常: {e}")

        _log("Watchdog 退出")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lampson Watchdog")
    parser.parse_args()
    Watchdog().run()


if __name__ == "__main__":
    main()
