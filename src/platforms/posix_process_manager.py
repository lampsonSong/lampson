"""POSIX (macOS/Linux) 进程管理实现。

macOS 使用 launchd 管理 daemon，Linux 使用直接进程拉起。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from src.platforms.process_manager import ProcessManager

logger = logging.getLogger(__name__)

# macOS launchd 服务标签
_DAEMON_LAUNCHCTL_LABEL = "com.lamix.gateway"


class PosixProcessManager(ProcessManager):
    """macOS / Linux 进程管理。"""

    def find_process(self, command_pattern: str) -> int | None:
        """通过 pgrep 查找匹配命令行模式的进程。"""
        # 优先从 pid 文件读取
        from src.core.config import LAMIX_DIR

        pid_file = LAMIX_DIR / "logs" / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                if self.is_alive(pid):
                    return pid
            except (ValueError, OSError):
                pass

        # 通过 pgrep 查找
        try:
            result = subprocess.run(
                ["pgrep", "-f", command_pattern],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
                for pid in pids:
                    if pid != os.getpid():
                        return pid
        except Exception:
            pass
        return None

    def is_alive(self, pid: int) -> bool:
        """通过 os.kill(pid, 0) 检查进程存活。"""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def kill_process(self, pid: int, graceful: bool = True) -> bool:
        """通过 SIGTERM/SIGKILL 终止进程。"""
        if not self.is_alive(pid):
            # 进程已死，清理 PID 文件
            from src.core.config import LAMIX_DIR
            pid_file = LAMIX_DIR / "logs" / "daemon.pid"
            pid_file.unlink(missing_ok=True)
            return True

        try:
            if graceful:
                os.kill(pid, signal.SIGTERM)
                # 等待最多 5 秒
                for _ in range(50):
                    if not self.is_alive(pid):
                        return True
                    time.sleep(0.1)
                logger.warning("进程 %d 未在 5s 内退出，强杀", pid)

            os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)
            return not self.is_alive(pid)
        except OSError:
            return not self.is_alive(pid)

    def restart_daemon(
        self,
        daemon_command: list[str],
        pid_file: Path,
        log_dir: Path,
        cwd: Path | None = None,
    ) -> bool:
        """重启 daemon：通过 kill 旧进程 + Popen 拉起新进程（macOS/Linux 通用）。

        不使用 launchctl kickstart，因为 macOS 上它可能找不到 service 导致失败。
        """
        # 先尝试终止旧进程
        old_pid = None
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text(encoding="utf-8").strip())
                self.kill_process(old_pid, graceful=True)
            except (ValueError, OSError):
                pass

        return self._restart_via_popen(daemon_command, pid_file, log_dir, cwd)

    def _restart_via_launchctl(self) -> bool:
        """macOS: 通过 launchctl kickstart 重启。"""
        try:
            result = subprocess.run(
                [
                    "launchctl",
                    "kickstart",
                    "-k",
                    f"gui/{os.getuid()}/{_DAEMON_LAUNCHCTL_LABEL}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info("launchctl kickstart 成功")
                return True
            else:
                logger.error("launchctl kickstart 失败: %s", result.stderr)
                return False
        except Exception as e:
            logger.error("launchctl kickstart 异常: %s", e)
            return False

    def _restart_via_popen(
        self,
        daemon_command: list[str],
        pid_file: Path,
        log_dir: Path,
        cwd: Path | None,
    ) -> bool:
        """Linux: 通过 Popen 拉起 daemon 进程。"""
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_log = open(log_dir / "daemon.log", "a", encoding="utf-8")
            stderr_log = open(log_dir / "daemon_error.log", "a", encoding="utf-8")

            proc = subprocess.Popen(
                daemon_command,
                cwd=str(cwd) if cwd else None,
                stdout=stdout_log,
                stderr=stderr_log,
                start_new_session=True,  # detach from parent process group
            )

            # 写入 pid 文件
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(proc.pid), encoding="utf-8")

            logger.info("daemon 已启动 (PID=%d)", proc.pid)
            return True
        except Exception as e:
            logger.error("Popen 启动 daemon 失败: %s", e)
            return False
