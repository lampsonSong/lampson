"""Shell 命令执行工具：通过 subprocess 执行终端命令，内置危险命令拦截。"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import select
import subprocess
import shlex
import signal
import time
from typing import Any


DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-fr\s+/",
    r"mkfs",
    r"dd\s+if=",
    r":\(\)\{.*\}",        # fork bomb
    r">\s*/dev/sd",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R.*\s+/",
]

# 禁止 launchctl 操作 Lamix 自己的 plist（unload 会把自己从 launchd 移除，KeepAlive 失效）
_LAMIX_PLIST_PATTERNS = [
    r"launchctl\s+(unload|load)\s+.*com\.lamix",
    r"launchctl\s+(unload|load)\s+.*lamix\.gateway",
    r"launchctl\s+(unload|load)\s+.*LaunchAgents.*lamix",
]

_DANGER_RE = [re.compile(p) for p in DANGEROUS_PATTERNS]

# 命令行长度上限（与文件读取 100KB 量级一致，且远低于系统 ARG_MAX）
MAX_COMMAND_LENGTH = 100_000

_LAMIX_PLIST_RE = [re.compile(p) for p in _LAMIX_PLIST_PATTERNS]

# cat/rm 等后接通配（避免 cat *.py、cat src/* 等滥用）
_GLOB_ABUSE_RE = re.compile(
    r"\b(cat|rm|mv|cp|less|head|tail)\b[^\n#;]*?[\*]"
)

# PTY 输出上限，防止 openconnect 等长时间命令撑爆内存
MAX_OUTPUT_BYTES = 500_000


def _hits_lamix_plist(command: str) -> bool:
    for pattern in _LAMIX_PLIST_RE:
        if pattern.search(command):
            return True
    return False


def is_dangerous(command: str) -> bool:
    for pattern in _DANGER_RE:
        if pattern.search(command):
            return True
    return False


def _has_glob_abuse(command: str) -> bool:
    return bool(_GLOB_ABUSE_RE.search(command))


def _is_cli_interrupted() -> bool:
    """检查 CLI 是否收到中断信号（Ctrl+C）。"""
    try:
        from src.cli import _check_interrupt
        return _check_interrupt()
    except (ImportError, AttributeError):
        return False


def execute_shell(command: str, timeout: int = 30) -> str:
    """执行 shell 命令，返回 stdout + stderr 合并字符串。

    使用 PTY 模拟真实终端，支持交互式命令（openconnect/ssh/expect 等）。
    支持 Ctrl+C 中断：在命令执行期间按 Ctrl+C 可终止进程并返回中断提示。
    """
    if len(command) > MAX_COMMAND_LENGTH:
        return (
            f"[拒绝执行] 命令过长（{len(command)} 字符），上限为 {MAX_COMMAND_LENGTH}，"
            "请缩短或拆成多步/分批执行。"
        )
    if is_dangerous(command):
        return f"[拒绝执行] 该命令被识别为危险操作，已拦截：{command}"
    if _has_glob_abuse(command):
        return (
            "[拒绝执行] 检测到对 cat/rm 等使用通配符（如 *.py、src/*），"
            "请改为明确路径、使用 `search` 工具或分文件读取，"
            "避免一次展开大量文件。"
        )

    try:
        # 创建 PTY（pseudo-terminal），让交互式命令认为在真实终端中运行
        master_fd, slave_fd = os.openpty()

        # 设置 master 为非阻塞，以便 select 可用
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # 启动进程，stdin/stdout/stderr 都连到 PTY slave
        # setsid 让进程有独立的 session，TIOCSCTTY 获取控制终端
        process = subprocess.Popen(
            command,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            pass_fds=(slave_fd,),
            close_fds=True,
        )

        # 关闭 slave fd（子进程已 dup2 到 0/1/2）
        os.close(slave_fd)

        start_time = time.time()
        output_parts = []
        total_bytes = 0
        closed = False

        while True:
            # 检查是否被 Ctrl+C 中断
            if _is_cli_interrupted():
                os.close(master_fd)
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        process.wait()
                    except (ProcessLookupError, OSError):
                        pass
                return "[中断] 命令已被 Ctrl+C 终止。"

            # 检查超时
            if timeout and (time.time() - start_time) > timeout:
                if not closed:
                    os.close(master_fd)
                    closed = True
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        process.wait()
                    except (ProcessLookupError, OSError):
                        pass
                # 超时后收集已读输出
                if output_parts:
                    collected = "".join(output_parts)
                    return (
                        f"[超时] 命令执行超过 {timeout} 秒，已终止。\n"
                        f"已收集输出（最后 {total_bytes} 字节）：\n{collected[-2000:]}"
                    )
                return f"[超时] 命令执行超过 {timeout} 秒，已终止。"

            # 检查进程是否结束
            if process.poll() is not None and closed:
                break

            # 从 PTY master 读取可用数据
            ready, _, _ = select.select([master_fd], [], [], 0.1)

            if ready:
                try:
                    chunk = os.read(master_fd, 8192)
                    if chunk:
                        output_parts.append(chunk.decode("utf-8", errors="replace"))
                        total_bytes += len(chunk)
                        # 输出过多时截断（防止 openconnect 长时间运行撑爆内存）
                        if total_bytes > MAX_OUTPUT_BYTES:
                            output_parts.append(f"\n[输出截断，超过 {MAX_OUTPUT_BYTES} 字节]")
                            if not closed:
                                os.close(master_fd)
                                closed = True
                            try:
                                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            except (ProcessLookupError, OSError):
                                pass
                            break
                except OSError as e:
                    if e.errno == errno.EIO:
                        # EIO: PTY slave 端关闭，只剩 master 可读，进程已退出
                        pass
                    else:
                        break

            # 进程结束了，尝试最后一次读
            if process.poll() is not None and not closed:
                # 给一点时间让输出缓冲区排空
                time.sleep(0.2)
                try:
                    while True:
                        chunk = os.read(master_fd, 8192)
                        if not chunk:
                            break
                        output_parts.append(chunk.decode("utf-8", errors="replace"))
                except OSError:
                    pass
                os.close(master_fd)
                closed = True
                break

        # 组装最终输出
        combined = "".join(output_parts)
        if not combined.strip():
            rc = process.poll()
            if rc is None:
                rc = process.wait()
            combined = f"[命令执行完毕，退出码 {rc}]"

        return combined.strip()

    except Exception as e:
        return f"[错误] 命令执行失败：{e}"


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": (
            "在终端执行 shell 命令，返回输出结果。"
            "适用于运行脚本、安装包、启动进程等。"
            "支持 Ctrl+C 中断正在执行的命令。"
            "支持交互式命令（openconnect/ssh/expect 等）。"
            "禁止用此工具执行 find/grep/rg 搜索文件或内容，请改用 search 工具。"
            "禁止用 cat/head/tail 读取文件，请改用 file_read。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数，默认 30，最长 120",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
    },
}


def run(params: dict[str, Any]) -> str:
    command = params.get("command", "")
    timeout = int(params.get("timeout", 30))
    # 上限 120 秒，防止 LLM 设置过长的超时
    timeout = min(timeout, 120)
    if not command:
        return "[错误] command 参数不能为空"
    return execute_shell(command, timeout=timeout)
