"""记忆管理器：长期记忆（MEMORY.md）读写。"""

from __future__ import annotations

from datetime import datetime

from src.core.config import LAMIX_DIR
from src.core.constants import MEMORY_SIZE_LIMIT

MEMORY_FILE = LAMIX_DIR / "MEMORY.md"
SESSIONS_DIR = LAMIX_DIR / "memory" / "sessions"


def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def load_memory() -> str:
    """读取长期记忆全文，启动时注入 system prompt。"""
    _ensure_dirs()
    if not MEMORY_FILE.exists():
        return ""
    return MEMORY_FILE.read_text(encoding="utf-8").strip()


def show_memory() -> str:
    """返回长期记忆内容，供 /memory show 展示。"""
    content = load_memory()
    if not content:
        return "长期记忆为空。"
    char_count = len(content)
    return f"[长期记忆 {char_count} chars / {MEMORY_SIZE_LIMIT} chars]\n\n{content}"


def add_memory(text: str) -> str:
    """向长期记忆追加一条新条目。"""
    _ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n- [{timestamp}] {text.strip()}"

    existing = MEMORY_FILE.read_text(encoding="utf-8") if MEMORY_FILE.exists() else ""
    MEMORY_FILE.write_text(existing + entry, encoding="utf-8")

    char_count = len(MEMORY_FILE.read_text(encoding="utf-8"))
    warning = f"\n⚠️ 长期记忆已超过 {MEMORY_SIZE_LIMIT} 字符，建议整理。" if char_count > MEMORY_SIZE_LIMIT else ""
    return f"已添加记忆条目。{warning}"


def search_memory(keyword: str) -> str:
    """在长期记忆和历史会话中搜索关键词（大小写不敏感）。"""
    _ensure_dirs()
    keyword_lower = keyword.lower()
    results: list[str] = []

    # 搜索长期记忆
    if MEMORY_FILE.exists():
        mem_text = MEMORY_FILE.read_text(encoding="utf-8")
        matched_lines = [
            line for line in mem_text.splitlines()
            if keyword_lower in line.lower()
        ]
        if matched_lines:
            results.append("**[长期记忆]**")
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
    """删除长期记忆中包含关键词的条目，需外部调用前确认。"""
    _ensure_dirs()
    if not MEMORY_FILE.exists():
        return "长期记忆为空，无需删除。"

    keyword_lower = keyword.lower()
    lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()
    to_keep = [line for line in lines if keyword_lower not in line.lower()]
    deleted = len(lines) - len(to_keep)

    if deleted == 0:
        return f"未找到包含 '{keyword}' 的记忆条目。"

    MEMORY_FILE.write_text("\n".join(to_keep), encoding="utf-8")
    return f"已删除 {deleted} 条包含 '{keyword}' 的记忆条目。"
