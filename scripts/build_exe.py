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
    print(" Lamix 卸载程序")
    print("=" * 50)

    if not is_admin():
        print("注意：某些操作可能需要管理员权限")
    print()

    # 删除任务计划
    task_name = "Lamix"
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            print(f"已删除开机自启动任务：{task_name}")
        elif "找不到" in result.stderr or "not found" in result.stderr.lower():
            print(f"任务 {task_name} 不存在，跳过")
        else:
            print(f"删除任务失败：{result.stderr.strip()}")
    except Exception as e:
        print(f"删除任务时出错：{e}")

    # 询问是否删除配置目录
    print()
    data_dir = Path.home() / ".lamix"
    if data_dir.exists():
        print(f"配置目录：{data_dir}")
        print("  保留：下次安装可直接使用，配置、记忆、技能全部保留。")
        print("  删除：彻底清除所有个人数据。")
        choice = input("\\n是否删除配置目录？(y/N): ").strip().lower()
        if choice in ("y", "yes"):
            try:
                shutil.rmtree(data_dir)
                print(f"已删除配置目录：{data_dir}")
            except Exception as e:
                print(f"删除失败：{e}")
                print(f"请手动删除：{data_dir}")
        else:
            print(f"已保留配置目录：{data_dir}")
    else:
        print("配置目录不存在，无需清理。")

    print()
    print("卸载完成。项目代码需手动删除。")

if __name__ == "__main__":
    try:
        uninstall()
    except Exception as e:
        print(f"卸载出错：{e}")
    input("\\n按回车键退出...")
""",
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

    # 1. 构建 lamix.exe
    cli_entry = str(project_root / "src" / "cli.py")
    results.append(build_one("lamix", cli_entry, project_root))

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
        print("  双击 lamix.exe -> 启动配置向导和交互式 CLI")
        print("  双击 lamix-uninstall.exe -> 卸载 Lamix")
    else:
        print("构建失败。")
        sys.exit(1)


if __name__ == "__main__":
    main()
