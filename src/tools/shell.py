"""Shell 命令执行工具：通过 subprocess 执行终端命令，内置危险命令拦截。"""

from __future__ import annotations

import subprocess
import shlex
import re
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


def execute_shell(command: str, timeout: int = 30) -> str:
    """执行 shell 命令，返回 stdout + stderr 合并字符串。"""
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
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.append(f"[stderr]\n{result.stderr}")
        if not output_parts:
            output_parts.append(f"[命令执行完毕，退出码 {result.returncode}]")
        return "\n".join(output_parts).strip()
    except subprocess.TimeoutExpired:
        return f"[超时] 命令执行超过 {timeout} 秒，已终止。"
    except Exception as e:
        return f"[错误] 命令执行失败：{e}"


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": (
            "在终端执行 shell 命令，返回输出结果。"
            "适用于运行脚本、安装包、启动进程等。"
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
