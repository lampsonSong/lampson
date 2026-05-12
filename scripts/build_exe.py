"""构建 Windows exe 可执行文件。

使用 PyInstaller 将 lamix 和卸载程序打包为单文件 .exe。
用户双击 lamix.exe 启动配置向导和交互式 CLI。
双击 lamix-uninstall.exe 执行卸载。

依赖：pip install pyinstaller

用法：
    python scripts/build_exe.py
    # 产出在 dist/lamix.exe 和 dist/lamix-uninstall.exe
"""

import os
import subprocess
import sys
from pathlib import Path


def _print(msg: str) -> None:
    """Print with UTF-8 encoding (Windows console fix)."""
    print(msg, flush=True)


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

    _print(f"\n>>> Building {name}.exe...")
    subprocess.run(cmd, cwd=str(project_root), check=True, encoding="utf-8", errors="replace")

    exe_path = project_root / "dist" / f"{name}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / 1024 / 1024
        _print(f"  [OK] {exe_path} ({size_mb:.1f} MB)")
        return exe_path
    else:
        _print(f"  [FAIL] {name}.exe build failed")
        return None


def build_uninstall_entry(project_root: Path) -> Path:
    """生成卸载程序的入口脚本（内联卸载逻辑，不依赖外部 import）。"""
    uninstall_script = project_root / "scripts" / "_uninstall_entry.py"
    uninstall_script.write_text(
        """
import subprocess
import shutil
from pathlib import Path

def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def uninstall():
    print("=" * 50)
    print(" Lamix Uninstall")
    print("=" * 50)

    if not is_admin():
        print("Note: Some operations may require admin rights")
    print()

    # Delete scheduled task
    task_name = "Lamix"
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            print(f"Deleted task: {task_name}")
        elif "not found" in result.stderr.lower():
            print(f"Task {task_name} not found, skipping")
        else:
            print(f"Failed to delete task: {result.stderr.strip()}")
    except Exception as e:
        print(f"Error deleting task: {e}")

    # Ask about config directory
    print()
    data_dir = Path.home() / ".lamix"
    if data_dir.exists():
        print(f"Config dir: {data_dir}")
        print("  Keep: Next install will reuse config, memory, skills.")
        print("  Delete: Remove all personal data.")
        choice = input("\\nDelete config dir? (y/N): ").strip().lower()
        if choice in ("y", "yes"):
            try:
                shutil.rmtree(data_dir)
                print(f"Deleted: {data_dir}")
            except Exception as e:
                print(f"Delete failed: {e}")
                print(f"Please delete manually: {data_dir}")
        else:
            print(f"Kept: {data_dir}")
    else:
        print("Config dir not found, nothing to clean.")

    print()
    print("Uninstall complete. Please delete project code manually.")

if __name__ == "__main__":
    try:
        uninstall()
    except Exception as e:
        print(f"Error: {e}")
    input("\\nPress Enter to exit...")
""",
        encoding="utf-8",
    )
    return uninstall_script

def main():
    project_root = Path(__file__).resolve().parent.parent

    # Check PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _print("PyInstaller not installed, installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )

    results = []

    # 1. Build lamix.exe
    cli_entry = str(project_root / "src" / "cli.py")
    results.append(build_one("lamix", cli_entry, project_root))

    # 2. Build lamix-uninstall.exe
    uninstall_entry = build_uninstall_entry(project_root)
    results.append(build_one("lamix-uninstall", str(uninstall_entry), project_root))

    # Summary
    _print("\n" + "=" * 50)
    successes = [r for r in results if r is not None]
    if successes:
        _print(f"Build complete! {len(successes)} file(s):")
        for p in successes:
            _print(f"  {p}")
        _print("\nUsage:")
        _print("  Double-click lamix.exe -> Start config wizard and CLI")
        _print("  Double-click lamix-uninstall.exe -> Uninstall Lamix")
    else:
        _print("Build failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
