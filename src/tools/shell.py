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

_DANGER_RE = [re.compile(p) for p in DANGEROUS_PATTERNS]


def is_dangerous(command: str) -> bool:
    for pattern in _DANGER_RE:
        if pattern.search(command):
            return True
    return False


def execute_shell(command: str, timeout: int = 60) -> str:
    """执行 shell 命令，返回 stdout + stderr 合并字符串。"""
    if is_dangerous(command):
        return f"[拒绝执行] 该命令被识别为危险操作，已拦截：{command}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
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
        "description": "在终端执行 shell 命令，返回输出结果。适用于查看文件列表、运行脚本、安装包等操作。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数，默认 60",
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
}


def run(params: dict[str, Any]) -> str:
    command = params.get("command", "")
    timeout = int(params.get("timeout", 60))
    if not command:
        return "[错误] command 参数不能为空"
    return execute_shell(command, timeout=timeout)
