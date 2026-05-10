"""构建 Windows exe 可执行文件。

使用 PyInstaller 将 lamix-cli 打包为单文件 .exe。
用户双击即可启动配置向导和交互式 CLI。

依赖：pip install pyinstaller

用法：
    python scripts/build_exe.py
    # 产出在 dist/lamix-cli.exe
"""

import os
import subprocess
import sys
from pathlib import Path


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

    # 构建
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        "--name", "lamix-cli",
        "--clean",
        "--noconfirm",
        # 包含 config 目录（默认 skills、identity 等模板）
        "--add-data", f"{project_root / 'config'}{os.pathsep}config",
        str(project_root / "src" / "cli.py"),
    ]

    print("正在构建 lamix-cli.exe...")
    print(f"命令: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(project_root), check=True)

    exe_path = project_root / "dist" / "lamix-cli.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / 1024 / 1024
        print("\n构建成功！")
        print(f"  产出: {exe_path}")
        print(f"  大小: {size_mb:.1f} MB")
        print("\n用户双击 lamix-cli.exe 即可启动。")
    else:
        print("构建失败，未找到 dist/lamix-cli.exe")
        sys.exit(1)


if __name__ == "__main__":
    main()
