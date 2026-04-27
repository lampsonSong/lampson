#!/usr/bin/env python3
"""历史数据迁移脚本：将旧版 sessions/*.md 转换为 JSONL 格式。

旧版格式：每个 .md 文件是一次会话的 LLM 摘要。
新版格式：JSONL（session_start + 消息 + session_end）。

用法：
    python scripts/migrate_sessions.py [--dry-run] [--source-dir DIR]

选项：
    --dry-run      只打印转换计划，不写文件
    --source-dir   旧版 sessions 目录（默认 ~/.lampson/sessions/）
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import json


def parse_md_session(content: str) -> dict:
    """从 .md 文件内容提取 session 信息。"""
    # 尝试提取日期（文件名或内容第一行）
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", content[:200])
    date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

    # 提取内容（去掉标题行）
    lines = content.strip().split("\n")
    body_lines = []
    for line in lines:
        # 跳过 markdown 标题（# 开头）
        if re.match(r"^#{1,6}\s", line):
            continue
        body_lines.append(line)

    body = "\n".join(body_lines).strip()
    if not body:
        return {}

    return {
        "date": date_str,
        "content": body,
    }


def migrate_file(md_path: Path, output_dir: Path, dry_run: bool = False) -> None:
    """将单个 .md 文件转换为 JSONL。"""
    content = md_path.read_text(encoding="utf-8")
    parsed = parse_md_session(content)
    if not parsed:
        print(f"  [SKIP] {md_path.name}: 空内容")
        return

    date_str = parsed["date"]
    session_id = md_path.stem  # 用文件名作为 session_id

    # 目标目录
    target_dir = output_dir / date_str
    target_path = target_dir / f"{session_id}.jsonl"

    # 解析日期为时间戳
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start_ts = int(dt.timestamp() * 1000)
        # 假设 session 在同一天结束
        end_ts = start_ts + 8 * 3600 * 1000  # +8h
    except ValueError:
        start_ts = int(datetime.now().timestamp() * 1000)
        end_ts = start_ts

    # 构造 JSONL 内容
    jsonl_lines = [
        json.dumps({
            "ts": start_ts,
            "type": "session_start",
            "session_id": session_id,
            "source": "cli",  # 旧版只有 CLI
        }, ensure_ascii=False),
        json.dumps({
            "ts": start_ts + 1000,
            "session_id": session_id,
            "segment": 0,
            "role": "assistant",
            "content": f"[迁移自旧版摘要]\n\n{parsed['content']}",
        }, ensure_ascii=False),
        json.dumps({
            "ts": end_ts,
            "session_id": session_id,
            "type": "session_end",
        }, ensure_ascii=False),
    ]

    if dry_run:
        print(f"  [DRY-RUN] {md_path} -> {target_path}")
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")
    print(f"  [OK] {md_path.name} -> {target_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移旧版 sessions/*.md 到 JSONL")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划不写文件")
    parser.add_argument("--source-dir", type=str, default=None, help="旧版 sessions 目录")
    args = parser.parse_args()

    # 默认旧版目录
    if args.source_dir:
        source_dir = Path(args.source_dir)
    else:
        source_dir = Path.home() / ".lampson" / "sessions"

    output_dir = Path.home() / ".lampson" / "memory" / "sessions"

    if not source_dir.exists():
        print(f"源目录不存在: {source_dir}")
        return

    md_files = list(source_dir.rglob("*.md"))
    if not md_files:
        print(f"没有找到 .md 文件: {source_dir}")
        return

    print(f"找到 {len(md_files)} 个 .md 文件，{'[DRY-RUN] ' if args.dry_run else ''}开始迁移...\n")
    for md_path in md_files:
        migrate_file(md_path, output_dir, dry_run=args.dry_run)

    print(f"\n完成。迁移后请运行 rebuild_index 重建搜索索引。")


if __name__ == "__main__":
    main()
