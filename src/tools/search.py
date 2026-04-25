"""按文件名与内容搜索：底层使用 ripgrep (rg)。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

# rg 在 PATH 中解析（不硬编码 /opt/homebrew 等路径）
_RG_PATH: str | None = shutil.which("rg")

# ReDoS 简单防护：嵌套量词
_REDOS_RE = re.compile(r"(\([^)]*[+*][^)]*\))[+*]")

# 视为「可能为正则」的元字符（出现则不用 --fixed-strings）
_RE_META = re.compile(r"[.^$*+?()[\]{}|\\]")

_RG_TIMEOUT_SEC = 30

SEARCH_FILES_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": (
            "按文件名或 glob 模式搜索文件。底层用 ripgrep，自动尊重 .gitignore，"
            "自动排除 .git 目录。用于替代 find 命令。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "文件名 glob 模式，如 '*.py'、'test_*'、'README*'。支持 rg glob 语法。"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录，默认当前目录 '.'",
                    "default": ".",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "最大搜索深度，默认 5，最大 10",
                    "default": 5,
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回多少条结果，默认 50，最大 200",
                    "default": 50,
                },
            },
            "required": ["pattern"],
        },
    },
}

SEARCH_CONTENT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_content",
        "description": (
            "在文件内容中搜索匹配的文本或正则表达式。底层用 ripgrep，自动尊重 .gitignore。"
            "用于替代 grep 命令。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "搜索模式，支持正则表达式（rg 语法）",
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录，默认当前目录 '.'",
                    "default": ".",
                },
                "file_glob": {
                    "type": "string",
                    "description": "只在匹配此 glob 的文件中搜索，如 '*.py'、'*.{js,ts}'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回多少条匹配，默认 50，最大 200",
                    "default": 50,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "每个匹配前后显示多少行上下文，默认 2，最大 5",
                    "default": 2,
                },
            },
            "required": ["pattern"],
        },
    },
}


def _check_rg() -> str | None:
    """返回 rg 可执行路径，不可用时为 None。"""
    return _RG_PATH


def _rg_missing_msg() -> str:
    return (
        "[错误] ripgrep (rg) 未安装，无法使用搜索功能。"
        "请运行 brew install ripgrep 安装。"
    )


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def _as_int(
    value: object,
    default: int,
    lo: int,
    hi: int,
) -> int:
    if value is None:
        v = default
    else:
        v = int(value)
    return _clamp(v, lo, hi)


def _validate_content_pattern(pattern: str) -> str | None:
    """返回错误信息，None 表示合法。"""
    if len(pattern) > 500:
        return "搜索模式过长（上限 500 字符），请简化。"
    if _REDOS_RE.search(pattern):
        return "搜索模式可能触发 ReDoS，请简化正则表达式。"
    return None


def _expand_dir(path: str) -> str | None:
    """展开 ~ 并解析为绝对路径；若不存在或不是目录则返回 None。"""
    expanded = os.path.expanduser(path or ".")
    abs_path = os.path.abspath(expanded)
    if not os.path.isdir(abs_path):
        return None
    return abs_path


def _run_rg(args: list[str], path: str) -> tuple[list[str] | None, str | None]:
    """
    执行 rg。
    - 成功：(stdout 行列表, None)；无匹配时为 ([], None)。
    - 失败：(None, 错误信息)。
    """
    if not _check_rg():
        return None, _rg_missing_msg()

    base = _expand_dir(path)
    if base is None:
        return None, f"[错误] 路径不存在或不是目录：{os.path.expanduser(path or '.')}"

    if _RG_PATH is None:
        return None, _rg_missing_msg()

    cmd = [_RG_PATH] + args + [base]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_RG_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return None, f"[错误] ripgrep 执行超过 {_RG_TIMEOUT_SEC} 秒，已终止。"
    except OSError as e:
        return None, f"[错误] 无法执行 rg：{e}"

    if result.returncode == 1:
        return [], None
    if result.returncode != 0:
        err = (result.stderr or "").strip() or "未知错误"
        return None, f"[错误] rg 执行失败：{err}"

    lines = result.stdout.splitlines()
    return lines, None


def _truncate_lines(lines: list[str], max_results: int) -> tuple[list[str], bool]:
    truncated = len(lines) > max_results
    if truncated:
        return lines[:max_results], True
    return lines, False


def _rg_output_match_count(lines: list[str]) -> int:
    """从 rg 文本输出行估计匹配条数（heading 下列行以「数字:」起头；否则 path:行号: 形式）。"""
    n = 0
    for ln in lines:
        if re.match(r"^\d+:", ln):
            n += 1
        elif re.search(r"^[^:\n]+?:\d+?:", ln):
            n += 1
    return n


def run_search_files(params: dict[str, Any]) -> str:
    """按文件名 / glob 列出文件。"""
    pattern = (params.get("pattern") or "").strip()
    if not pattern:
        return "[错误] pattern 参数不能为空"

    if not _check_rg():
        return _rg_missing_msg()

    path = (params.get("path") or ".") or "."
    max_depth = _as_int(params.get("max_depth"), 5, 1, 10)
    max_results = _as_int(params.get("max_results"), 50, 1, 200)

    args = [
        "--files",
        "--glob",
        pattern,
        "--max-depth",
        str(max_depth),
        "--max-count",
        str(max_results),
    ]

    lines, err = _run_rg(args, path)
    if err is not None:
        return err
    if not lines:
        return "未找到匹配结果。"

    out_lines, truncated = _truncate_lines(lines, max_results)
    display_base = _expand_dir(path) or os.path.abspath(os.path.expanduser(path))

    n = len(out_lines)
    head = f"找到 {n} 个文件（搜索路径：{display_base}，深度 ≤ {max_depth}）"
    text = f"{head}：\n\n" + "\n".join(out_lines)
    if truncated:
        text += (
            f"\n\n...（结果过多，已截断到 {max_results} 条，请缩小搜索范围）"
        )
    return text


def _use_fixed_strings(pattern: str) -> bool:
    """无正则元字符时可使用 -F 加速。"""
    return not bool(_RE_META.search(pattern))


def _content_rg_args(
    pattern: str,
    max_results: int,
    context_lines: int,
    file_glob: str | None,
) -> list[str]:
    args: list[str] = [
        "--line-number",
        "--max-count",
        str(max_results),
    ]
    if context_lines > 0:
        args.extend(["--context", str(context_lines)])
    if file_glob:
        args.extend(["--glob", file_glob])
    if _use_fixed_strings(pattern):
        args.append("--fixed-strings")
    args.append(pattern)
    return args


def _format_content_output(
    pattern: str,
    path: str,
    lines: list[str],
    max_results: int,
    context_lines: int,
) -> str:
    line_cap = min(2000, max(100, max_results * (2 * max(context_lines, 0) + 4)))
    out_lines, truncated = _truncate_lines(lines, line_cap)
    display_base = _expand_dir(path) or os.path.abspath(os.path.expanduser(path))
    mcount = min(_rg_output_match_count(out_lines) or 1, max_results)
    head = f'在 {display_base} 中搜索 "{pattern}"（共 {mcount} 处匹配）'
    text = f"{head}：\n\n" + "\n".join(out_lines)
    if truncated:
        text += f"\n\n...（结果过多，已截断到 {line_cap} 行，请缩小搜索范围）"
    return text


def run_search_content(params: dict[str, Any]) -> str:
    """在文件内容中搜索。"""
    pattern = params.get("pattern")
    if pattern is None or (isinstance(pattern, str) and not pattern.strip()):
        return "[错误] pattern 参数不能为空"
    if not isinstance(pattern, str):
        return "[错误] pattern 须为字符串"

    bad = _validate_content_pattern(pattern)
    if bad:
        return f"[错误] {bad}"

    if not _check_rg():
        return _rg_missing_msg()

    path = (params.get("path") or ".") or "."
    max_results = _as_int(params.get("max_results"), 50, 1, 200)
    context_lines = _as_int(params.get("context_lines"), 2, 0, 5)
    raw_glob = params.get("file_glob")
    file_glob: str | None
    if isinstance(raw_glob, str) and (raw_glob := raw_glob.strip()):
        file_glob = raw_glob
    else:
        file_glob = None

    args = _content_rg_args(pattern, max_results, context_lines, file_glob)
    lines, err = _run_rg(args, path)
    if err is not None:
        return err
    if not lines:
        return "未找到匹配结果。"

    return _format_content_output(pattern, path, lines, max_results, context_lines)
