"""记忆管理器：核心记忆（core.md）读写。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


MEMORY_DIR = Path.home() / ".lampson" / "memory"
CORE_FILE = MEMORY_DIR / "core.md"
SESSIONS_DIR = MEMORY_DIR / "sessions"
CORE_SIZE_LIMIT = 5 * 1024  # 5KB


def _ensure_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def load_core() -> str:
    """读取核心记忆全文，启动时注入 system prompt。"""
    _ensure_dirs()
    if not CORE_FILE.exists():
        return ""
    return CORE_FILE.read_text(encoding="utf-8").strip()


def show_core() -> str:
    """返回核心记忆内容，供 /memory show 展示。"""
    content = load_core()
    if not content:
        return "核心记忆为空。"
    size = CORE_FILE.stat().st_size
    return f"[核心记忆 {size} bytes / {CORE_SIZE_LIMIT} bytes]\n\n{content}"


def add_memory(text: str) -> str:
    """向核心记忆追加一条新条目。"""
    _ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n- [{timestamp}] {text.strip()}"

    existing = CORE_FILE.read_text(encoding="utf-8") if CORE_FILE.exists() else ""
    CORE_FILE.write_text(existing + entry, encoding="utf-8")

    size = CORE_FILE.stat().st_size
    warning = f"\n⚠️ 核心记忆已超过 {CORE_SIZE_LIMIT} bytes，建议整理。" if size > CORE_SIZE_LIMIT else ""
    return f"已添加记忆条目。{warning}"


def search_memory(keyword: str) -> str:
    """在核心记忆和历史会话中搜索关键词（大小写不敏感）。"""
    _ensure_dirs()
    keyword_lower = keyword.lower()
    results: list[str] = []

    # 搜索核心记忆
    if CORE_FILE.exists():
        core_text = CORE_FILE.read_text(encoding="utf-8")
        matched_lines = [
            line for line in core_text.splitlines()
            if keyword_lower in line.lower()
        ]
        if matched_lines:
            results.append("**[核心记忆]**")
            results.extend(f"  {line}" for line in matched_lines)

    # 搜索会话记忆
    if SESSIONS_DIR.exists():
        for session_file in sorted(SESSIONS_DIR.glob("*.md"), reverse=True):
            content = session_file.read_text(encoding="utf-8")
            if keyword_lower in content.lower():
                matched_lines = [
                    line for line in content.splitlines()
                    if keyword_lower in line.lower()
                ]
                results.append(f"\n**[{session_file.stem}]**")
                results.extend(f"  {line}" for line in matched_lines[:5])

    if not results:
        return f"未找到包含 '{keyword}' 的记忆。"
    return "\n".join(results)


def forget_memory(keyword: str) -> str:
    """删除核心记忆中包含关键词的条目，需外部调用前确认。"""
    _ensure_dirs()
    if not CORE_FILE.exists():
        return "核心记忆为空，无需删除。"

    keyword_lower = keyword.lower()
    lines = CORE_FILE.read_text(encoding="utf-8").splitlines()
    to_keep = [line for line in lines if keyword_lower not in line.lower()]
    deleted = len(lines) - len(to_keep)

    if deleted == 0:
        return f"未找到包含 '{keyword}' 的记忆条目。"

    CORE_FILE.write_text("\n".join(to_keep), encoding="utf-8")
    return f"已删除 {deleted} 条包含 '{keyword}' 的记忆条目。"



