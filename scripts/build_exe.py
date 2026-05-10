"""构建 Windows exe 可执行文件。

使用 PyInstaller 将 lamix-cli 和卸载程序打包为单文件 .exe。
用户双击 lamix-cli.exe 启动配置向导和交互式 CLI。
双击 lamix-uninstall.exe 执行卸载。

依赖：pip install pyinstaller

用法：
    python scripts/build_exe.py
    # 产出在 dist/lamix-cli.exe 和 dist/lamix-uninstall.exe
"""

import os
import subprocess
import sys
from pathlib import Path


def build_one(name: str, entry_script: str, project_root: Path) -> Path | None:
    """构建单个 exe。"""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        "--name", name,
        "--clean",
        "--noconfirm",
        "--add-data", f"{project_root / 'config'}{os.pathsep}config",
        entry_script,
    ]

    print(f"\n正在构建 {name}.exe...")
    subprocess.run(cmd, cwd=str(project_root), check=True)

    exe_path = project_root / "dist" / f"{name}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / 1024 / 1024
        print(f"  ✓ {exe_path} ({size_mb:.1f} MB)")
        return exe_path
    else:
        print(f"  ✗ {name}.exe 构建失败")
        return None


def build_uninstall_entry(project_root: Path) -> Path:
    """生成卸载程序的入口脚本。"""
    uninstall_script = project_root / "scripts" / "_uninstall_entry.py"
    uninstall_script.write_text(
        '''"""Lamix 卸载程序入口。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.install_windows import uninstall
if __name__ == "__main__":
    uninstall()
    input("\\n按回车键退出...")
''',
        encoding="utf-8",
    )
    return uninstall_script


def main():
    project_root = Path(__file__).resolve().parent.parent

    # 检查 PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller 未安装，正在安装...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )

    results = []

    # 1. 构建 lamix-cli.exe
    cli_entry = str(project_root / "src" / "cli.py")
    results.append(build_one("lamix-cli", cli_entry, project_root))

    # 2. 构建 lamix-uninstall.exe
    uninstall_entry = build_uninstall_entry(project_root)
    results.append(build_one("lamix-uninstall", str(uninstall_entry), project_root))

    # 汇总
    print("\n" + "=" * 50)
    successes = [r for r in results if r is not None]
    if successes:
        print(f"构建完成！产出 {len(successes)} 个文件：")
        for p in successes:
            print(f"  {p}")
        print("\n用户使用方式：")
        print("  双击 lamix-cli.exe -> 启动配置向导和交互式 CLI")
        print("  双击 lamix-uninstall.exe -> 卸载 Lamix")
    else:
        print("构建失败。")
        sys.exit(1)


if __name__ == "__main__":
    main()
