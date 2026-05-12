"""统一搜索工具：合并 search_files + search_content，通过 mode 参数区分。
优先使用 ripgrep (rg)，不可用时 fallback 到纯 Python 实现。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


# ── ripgrep 查找 ─────────────────────────────────────────────────────────────

def _find_rg() -> str | None:
    """查找 ripgrep 可执行文件路径（跨平台）。"""
    import sys

    # 1. PATH 查找（最优先）
    path = shutil.which("rg")
    if path:
        return path

    # 2. 平台特定常见路径
    if sys.platform == "win32":
        common_dirs = [
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
            os.path.expandvars(r"%ProgramFiles%\ripgrep"),
            os.path.expanduser("~\\scoop\\shims"),
        ]
        for d in common_dirs:
            candidate = os.path.join(d, "rg.exe")
            if os.path.isfile(candidate):
                return candidate
    else:
        for p in ["/opt/homebrew/bin/rg", "/usr/local/bin/rg", "/usr/bin/rg"]:
            if os.path.isfile(p):
                return p

    return None


# rg 路径解析：优先 PATH 查找，兜底常见安装位置（launchd 不继承 shell PATH）
_RG_PATH: str | None = _find_rg()
_has_rg: bool = _RG_PATH is not None

# ReDoS 简单防护：嵌套量词
_REDOS_RE = re.compile(r"(\([^)]*[+*][^)]*\))[+*]")

# 视为「可能为正则」的元字符（出现则不用 --fixed-strings）
_RE_META = re.compile(r"[.^$*+?()[\]{}|\\]")

# 搜索超时
_SEARCH_TIMEOUT = 30

# ── 统一 Schema ──────────────────────────────────────────────────────────────

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "按文件名或内容搜索。mode='files' 按文件名/glob 搜索（替代 find），"
            "mode='content' 按文本/正则搜索（替代 grep）。"
            "优先使用 ripgrep (rg)，未安装时自动 fallback 到纯 Python 实现。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["files", "content"],
                    "description": "搜索模式：files 按文件名搜索，content 按内容搜索",
                },
                "pattern": {
                    "type": "string",
                    "description": (
                        "搜索模式。files 模式下为文件名 glob（如 '*.py'、'test_*'）；"
                        "content 模式下为文本或正则表达式（rg 语法）"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录，默认当前目录 '.'",
                    "default": ".",
                },
                "file_glob": {
                    "type": "string",
                    "description": "content 模式下，只在匹配此 glob 的文件中搜索，如 '*.py'、'*.{js,ts}'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回多少条结果，默认 50，最大 200",
                    "default": 50,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "files 模式下最大搜索深度，默认 10，最大 20",
                    "default": 10,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "content 模式下每个匹配前后显示多少行上下文，默认 2，最大 5",
                    "default": 2,
                },
            },
            "required": ["mode", "pattern"],
        },
    },
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _rg_missing_msg() -> str:
    import sys
    if _has_rg:
        return ""  # 有 rg，不会调用这个
    if sys.platform == "win32":
        return (
            "[提示] 当前使用 Python 搜索实现（ripgrep 未安装）。"
            "安装 rg 可提升搜索性能：winget install BurntSushi.ripgrep"
        )
    return (
        "[提示] 当前使用 Python 搜索实现（ripgrep 未安装）。"
        "安装 rg 可提升搜索性能：brew install ripgrep"
    )


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def _as_int(value: object, default: int, lo: int, hi: int) -> int:
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


def _resolve_path(path: str) -> tuple[str, str | None]:
    """展开路径。返回 (abs_path, error_msg)。只检查路径是否存在。"""
    expanded = os.path.expanduser(path or ".")
    abs_path = os.path.abspath(expanded)
    if not os.path.isdir(abs_path):
        return abs_path, f"[错误] 路径不存在或不是目录：{expanded}"
    return abs_path, None


def _use_fixed_strings(pattern: str) -> bool:
    """无正则元字符时可使用 -F 加速。"""
    return not bool(_RE_META.search(pattern))


def _run_rg(args: list[str], abs_path: str) -> tuple[list[str] | None, str | None]:
    """
    执行 rg 命令。
    - 成功有结果：(行列表, None)
    - 无匹配：([], None)
    - 超时：(None, 超时信息)
    - 其他执行错误：(None, 错误信息)
    """
    if _RG_PATH is None:
        return None, None  # caller 负责 fallback

    cmd = [_RG_PATH, "--no-messages"] + args + [abs_path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SEARCH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f"[超时] 搜索超过 {_SEARCH_TIMEOUT} 秒，已终止。"
    except OSError as e:
        return None, f"[错误] 无法执行 rg：{e}"

    # rg 退出码：0=有匹配，1=无匹配，2=错误(权限等)
    if result.returncode == 1:
        return [], None
    if result.returncode != 0:
        if result.stdout.strip():
            return result.stdout.splitlines(), None
        return [], None

    return result.stdout.splitlines(), None


def _truncate_lines(lines: list[str], max_results: int) -> tuple[list[str], bool]:
    truncated = len(lines) > max_results
    if truncated:
        return lines[:max_results], True
    return lines, False


def _rg_output_match_count(lines: list[str]) -> int:
    """从 rg 文本输出行估计匹配条数。"""
    n = 0
    for ln in lines:
        if re.match(r"^\d+:", ln):
            n += 1
        elif re.search(r"[^:\n]+?:\d+?:", ln):
            n += 1
    return n


# ---------------------------------------------------------------------------
# Python Fallback 实现
# ---------------------------------------------------------------------------

def _load_gitignore(root: Path) -> set[str]:
    """读取 .gitignore 模式，返回需要忽略的目录/文件集合。"""
    gi_path = root / ".gitignore"
    if not gi_path.is_file():
        return set()

    ignored = set()
    try:
        for line in gi_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 简单处理：只处理目录级模式
            if line.endswith("/"):
                ignored.add(line.rstrip("/"))
            else:
                ignored.add(line)
    except Exception:
        pass
    return ignored


def _should_ignore(rel_path: str, gitignore: set[str]) -> bool:
    """简单判断路径是否应该被忽略。"""
    parts = rel_path.split(os.sep)
    for part in parts:
        if part in gitignore:
            return True
        if part == ".git":
            return True
    return False


def _py_search_files(
    pattern: str,
    abs_path: str,
    max_depth: int,
    max_results: int,
) -> str:
    """Python 实现：按文件名 glob 搜索。"""
    root = Path(abs_path)
    gitignore = _load_gitignore(root)

    results = []
    depth_limit = max_depth

    try:
        # 使用 Path.glob 搜索
        for p in root.rglob(pattern):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue

            # 检查深度
            depth = len(rel.parts) - 1
            if depth > depth_limit:
                continue

            # 检查 gitignore
            rel_str = str(rel)
            if _should_ignore(rel_str, gitignore):
                continue

            results.append(rel_str)
            if len(results) >= max_results:
                break
    except Exception as e:
        return f"[错误] 搜索失败：{e}"

    n = len(results)
    if n == 0:
        hint = _rg_missing_msg()
        return f"未找到匹配 '{pattern}' 的文件（搜索路径：{abs_path}）。{hint}"

    out_lines, truncated = _truncate_lines(sorted(results), max_results)
    text = f"找到 {n} 个文件（搜索路径：{abs_path}）：\n\n" + "\n".join(out_lines)
    if truncated:
        text += f"\n\n...（结果过多，已截断到 {max_results} 条，请缩小搜索范围）"
    return text


def _py_search_content(
    pattern: str,
    abs_path: str,
    max_results: int,
    context_lines: int,
    file_glob: str | None,
) -> str:
    """Python 实现：按内容正则搜索。"""
    root = Path(abs_path)
    gitignore = _load_gitignore(root)

    # 编译正则
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"[错误] 正则表达式错误：{e}"

    # 确定是否使用固定字符串（无正则元字符）
    use_fixed = not bool(_RE_META.search(pattern))
    if use_fixed:
        search_func = lambda line: pattern in line
    else:
        search_func = regex.search

    results: list[str] = []
    total_matches = 0
    file_count = 0
    line_cap = min(2000, max(100, max_results * (2 * max(context_lines, 0) + 4)))

    try:
        # 确定搜索范围
        if file_glob:
            files = list(root.rglob(file_glob))
        else:
            files = list(root.rglob("*"))

        for fpath in files:
            if not fpath.is_file():
                continue

            try:
                rel = fpath.relative_to(root)
            except ValueError:
                continue

            rel_str = str(rel)
            if _should_ignore(rel_str, gitignore):
                continue

            # 跳过二进制文件
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            file_count += 1
            if file_count > 1000:  # 限制文件数量防止太慢
                break

            lines = content.splitlines()
            for i, line in enumerate(lines):
                if search_func(line):
                    total_matches += 1
                    # 生成类似 rg 的输出格式
                    for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
                        line_no = j + 1
                        prefix = ">" if j == i else " "
                        results.append(f"{rel_str}:{line_no}:{prefix}{lines[j]}")
                        if len(results) >= line_cap:
                            break
                    results.append("---")
                    if total_matches >= max_results:
                        break
            if total_matches >= max_results or len(results) >= line_cap:
                break

    except Exception as e:
        return f"[错误] 搜索失败：{e}"

    hint = _rg_missing_msg()
    if not results:
        return f"未找到包含 '{pattern}' 的内容（搜索路径：{abs_path}）。{hint}"

    mcount = min(total_matches, max_results)
    head = f'在 {abs_path} 中搜索 "{pattern}"（共 {mcount} 处匹配）'
    text = f"{head}（Python 实现）\n\n" + "\n".join(results[:line_cap])
    if len(results) >= line_cap or total_matches > max_results:
        text += f"\n\n...（结果过多，已截断，请缩小搜索范围）"
    return text


# ---------------------------------------------------------------------------
# 搜索核心
# ---------------------------------------------------------------------------

def _search_files(
    pattern: str,
    abs_path: str,
    max_depth: int,
    max_results: int,
) -> str:
    """按文件名搜索。优先用 rg，不可用时 fallback 到 Python。"""
    if _has_rg:
        args = [
            "--files",
            "--glob", pattern,
            "--max-depth", str(max_depth),
            "--max-count", str(max_results),
        ]
        lines, err = _run_rg(args, abs_path)

        if err is not None:
            return err

        if lines:
            out_lines, truncated = _truncate_lines(lines, max_results)
            n = len(out_lines)
            text = f"找到 {n} 个文件（搜索路径：{abs_path}）：\n\n" + "\n".join(out_lines)
            if truncated:
                text += f"\n\n...（结果过多，已截断到 {max_results} 条，请缩小搜索范围）"
            return text

        return f"未找到匹配 '{pattern}' 的文件（搜索路径：{abs_path}）。"

    # Fallback 到 Python 实现
    return _py_search_files(pattern, abs_path, max_depth, max_results)


def _search_content(
    pattern: str,
    abs_path: str,
    max_results: int,
    context_lines: int,
    file_glob: str | None,
) -> str:
    """按内容搜索。优先用 rg，不可用时 fallback 到 Python。"""
    if _has_rg:
        args: list[str] = [
            "--line-number",
            "--max-depth", "10",
            "--max-count", str(max_results),
        ]
        if context_lines > 0:
            args.extend(["--context", str(context_lines)])
        if file_glob:
            args.extend(["--glob", file_glob])
        if _use_fixed_strings(pattern):
            args.append("--fixed-strings")
        args.append(pattern)

        lines, err = _run_rg(args, abs_path)

        if err is not None:
            return err

        if lines:
            return _format_content_output(pattern, abs_path, lines, max_results, context_lines)

        return f"未找到包含 '{pattern}' 的内容（搜索路径：{abs_path}）。"

    # Fallback 到 Python 实现
    return _py_search_content(pattern, abs_path, max_results, context_lines, file_glob)


def _format_content_output(
    pattern: str,
    abs_path: str,
    lines: list[str],
    max_results: int,
    context_lines: int,
) -> str:
    line_cap = min(2000, max(100, max_results * (2 * max(context_lines, 0) + 4)))
    out_lines, truncated = _truncate_lines(lines, line_cap)
    mcount = min(_rg_output_match_count(out_lines) or 1, max_results)
    head = f'在 {abs_path} 中搜索 "{pattern}"（共 {mcount} 处匹配）'
    text = f"{head}：\n\n" + "\n".join(out_lines)
    if truncated:
        text += f"\n\n...（结果过多，已截断到 {line_cap} 行，请缩小搜索范围）"
    return text


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def run(params: dict[str, Any]) -> str:
    """统一搜索入口，通过 mode 分发。"""
    mode = (params.get("mode") or "").strip()
    if mode not in ("files", "content"):
        return "[错误] mode 参数必须为 'files' 或 'content'"

    pattern = params.get("pattern")
    if not pattern or (isinstance(pattern, str) and not pattern.strip()):
        return "[错误] pattern 参数不能为空"

    path = (params.get("path") or ".") or "."
    abs_path, path_err = _resolve_path(path)
    if path_err is not None:
        return path_err

    max_results = _as_int(params.get("max_results"), 50, 1, 200)

    if mode == "files":
        max_depth = _as_int(params.get("max_depth"), 10, 1, 20)
        return _search_files(pattern.strip(), abs_path, max_depth, max_results)
    else:
        bad = _validate_content_pattern(pattern)
        if bad:
            return f"[错误] {bad}"
        context_lines = _as_int(params.get("context_lines"), 2, 0, 5)
        raw_glob = params.get("file_glob")
        file_glob: str | None
        if isinstance(raw_glob, str) and (raw_glob := raw_glob.strip()):
            file_glob = raw_glob
        else:
            file_glob = None
        return _search_content(pattern, abs_path, max_results, context_lines, file_glob)
