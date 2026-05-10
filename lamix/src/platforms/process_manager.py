"""平台无关的进程管理抽象层。

Windows 移植核心：将 watchdog 中的 macOS/Linux 特有调用抽象为统一接口，
由各平台实现类提供具体行为。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ProcessManager(ABC):
    """平台无关的进程管理接口。

    所有涉及进程查找、存活检测、终止、重启的操作都通过此接口，
    watchdog 和其他模块不直接调用平台特有命令。
    """

    @abstractmethod
    def find_process(self, command_pattern: str) -> int | None:
        """根据命令行模式找到 pid，不存在返回 None。

        Args:
            command_pattern: 命令行匹配模式（如 "src.daemon"）
        """
        ...

    @abstractmethod
    def is_alive(self, pid: int) -> bool:
        """检查进程是否存活。"""
        ...

    @abstractmethod
    def kill_process(self, pid: int, graceful: bool = True) -> bool:
        """终止进程。

        Args:
            pid: 目标进程 ID
            graceful: True 优先优雅终止（写 stop.flag / SIGTERM），
                      False 强制终止

        Returns:
            True 表示终止成功（或进程已不存在）
        """
        ...

    @abstractmethod
    def restart_daemon(
        self,
        daemon_command: list[str],
        pid_file: Path,
        log_dir: Path,
        cwd: Path | None = None,
    ) -> bool:
        """重启 daemon 进程。

        Args:
            daemon_command: 启动命令，如 [sys.executable, "-m", "src.daemon"]
            pid_file: pid 文件路径
            log_dir: 日志目录（daemon stdout/stderr 重定向目标）
            cwd: 工作目录（默认项目根目录）

        Returns:
            True 表示重启成功
        """
        ...


def get_process_manager() -> ProcessManager:
    """工厂函数：根据当前平台返回 ProcessManager 实例。"""
    import sys

    if sys.platform == "win32":
        from src.platforms.windows.process_manager import WindowsProcessManager

        return WindowsProcessManager()
    else:
        from src.platforms.posix_process_manager import PosixProcessManager

        return PosixProcessManager()
