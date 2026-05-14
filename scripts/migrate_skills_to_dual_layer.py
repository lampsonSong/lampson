#!/usr/bin/env python3
"""迁移 skills 目录从平铺结构到双层结构。

迁移规则：
- skills/xxx.md → skills/xxx/SKILL.md
- skills/scripts/ → 保持不动（兼容旧路径）
- skills/xxx/ 已存在的跳过（双层优先）

运行：
    python scripts/migrate_skills_to_dual_layer.py [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

LAMIX_DIR = Path.home() / ".lamix"
SKILLS_DIR = LAMIX_DIR / "memory" / "skills"
SCRIPTS_DIR = SKILLS_DIR / "scripts"  # 旧路径


def migrate_skill_file(md_file: Path, dry_run: bool = False) -> tuple[str, str]:
    """将平铺的 skill 文件迁移到双层目录结构。

    skills/xxx.md → skills/xxx/SKILL.md
    """
    skill_name = md_file.stem
    target_dir = SKILLS_DIR / skill_name
    target_file = target_dir / "SKILL.md"

    if target_dir.is_dir() and target_file.is_file():
        return "skip", f"{md_file.name} → 已存在 {target_dir.name}/SKILL.md，跳过"

    if dry_run:
        return "migrate", f"{md_file.name} → {target_dir.name}/SKILL.md"

    # 创建目录
    target_dir.mkdir(parents=True, exist_ok=True)

    # 如果目标文件已存在（同名目录存在但无 SKILL.md），只迁移文件
    if target_file.exists():
        return "skip", f"{md_file.name} → 目标已存在，跳过"

    # 移动文件
    shutil.move(str(md_file), str(target_file))
    return "migrate", f"{md_file.name} → {target_dir.name}/SKILL.md"


def migrate_legacy_scripts_dir(dry_run: bool = False) -> tuple[str, str]:
    """处理旧路径 skills/scripts/ → 不自动迁移（保留向后兼容）。

    旧路径的脚本仍然可以被扫描到（scan_and_register 会同时扫描新旧路径）。
    """
    if not SCRIPTS_DIR.is_dir():
        return "none", "旧 scripts/ 目录不存在，无需处理"

    # 不自动迁移，保留旧路径作为兼容
    return "keep", f"旧 scripts/ 目录保留在 {SCRIPTS_DIR}，向后兼容"


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 skills 目录到双层结构")
    parser.add_argument("--dry-run", action="store_true", help="只打印操作，不实际执行")
    args = parser.parse_args()

    if not SKILLS_DIR.exists():
        print("[错误] skills 目录不存在")
        return 1

    print("=== Skills 双层结构迁移 ===")
    print(f"目标目录: {SKILLS_DIR}")
    print(f"模式: {'预演（dry-run）' if args.dry_run else '实际执行'}")
    print()

    # 1. 扫描所有平铺的 .md 文件
    legacy_files = sorted(
        p for p in SKILLS_DIR.glob("*.md")
        if ".archived" not in str(p) and not p.name.startswith(".")
    )

    # 排除已被双层目录覆盖的
    to_migrate = []
    for md_file in legacy_files:
        skill_name = md_file.stem
        target_dir = SKILLS_DIR / skill_name
        target_file = target_dir / "SKILL.md"
        if target_dir.is_dir() and target_file.is_file():
            continue
        to_migrate.append(md_file)

    print(f"发现 {len(legacy_files)} 个平铺 skill 文件，{len(to_migrate)} 个待迁移")
    print()

    # 2. 迁移
    results = {"migrate": [], "skip": [], "keep": [], "none": []}

    for md_file in to_migrate:
        status, msg = migrate_skill_file(md_file, dry_run=args.dry_run)
        results[status].append(msg)
        print(f"  [{status.upper()}] {msg}")

    # 3. 处理旧 scripts/ 目录
    print()
    status, msg = migrate_legacy_scripts_dir(dry_run=args.dry_run)
    results[status].append(msg)
    print(f"  [{status.upper()}] {msg}")

    # 4. 总结
    print()
    print("=== 迁移总结 ===")
    if args.dry_run:
        print(f"  将迁移: {len(results['migrate'])} 个文件")
        print(f"  将跳过: {len(results['skip'])} 个文件")
    else:
        print(f"  已迁移: {len(results['migrate'])} 个文件")
        print(f"  已跳过: {len(results['skip'])} 个文件")

    if not args.dry_run and results["migrate"]:
        print()
        print("迁移完成！skill 索引已自动刷新（重启 daemon 后生效）。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
