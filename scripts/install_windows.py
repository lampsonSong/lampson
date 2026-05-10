"""Lamix Windows 安装脚本

功能：
1. 检查 Python 版本 >= 3.11
2. 检测 Python 环境类型（python.org、Microsoft Store、Anaconda）
3. 安装项目依赖
4. 注册 Windows 任务计划（开机自启）
5. 首次拉起 daemon 守护进程
6. 支持卸载功能（--uninstall）
"""

import argparse
import ctypes
import os
import subprocess
import sys
from pathlib import Path


def check_python_version() -> None:
    """检查 Python 版本是否 >= 3.11"""
    if sys.version_info < (3, 11):
        print(f"❌ 错误：需要 Python 3.11 或更高版本")
        print(f"   当前版本：{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        sys.exit(1)
    print(f"✓ Python 版本检查通过：{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


def detect_python_environment() -> dict[str, str]:
    """检测 Python 环境类型，给用户明确指引

    Returns:
        包含环境信息的字典：type, path, recommendations
    """
    python_path = sys.executable
    env_info = {
        "path": python_path,
        "type": "Unknown",
        "recommendations": ""
    }

    # 检测 Microsoft Store Python
    if "WindowsApps" in python_path:
        env_info["type"] = "Microsoft Store"
        env_info["recommendations"] = (
            "检测到 Microsoft Store 版本的 Python。\n"
            "  建议：如遇权限问题，请使用 python.org 官方安装版。"
        )

    # 检测 Anaconda/Miniconda
    elif "anaconda" in python_path.lower() or "miniconda" in python_path.lower():
        env_info["type"] = "Anaconda/Miniconda"
        env_info["recommendations"] = (
            "检测到 Anaconda 环境。\n"
            "  建议：使用 conda 环境时，确保已激活正确的环境。"
        )

    # 检测 python.org 官方版本
    elif "Python" in python_path and "AppData" not in python_path:
        env_info["type"] = "python.org"
        env_info["recommendations"] = "检测到 python.org 官方版本（推荐）。"

    # 其他情况，尝试用 where 和 py -0 提供更多信息
    else:
        env_info["recommendations"] = "检测到其他 Python 安装。"

    print(f"✓ Python 环境：{env_info['type']}")
    print(f"  路径：{env_info['path']}")
    if env_info["recommendations"]:
        print(f"  {env_info['recommendations']}")

    # 显示所有可用的 Python 版本
    try:
        result = subprocess.run(
            ["where", "python"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            print("\n  系统中所有 Python 路径：")
            for line in result.stdout.strip().split("\n"):
                print(f"    - {line}")
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["py", "-0"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            print("\n  可用 Python 版本（通过 py launcher）：")
            for line in result.stdout.strip().split("\n"):
                print(f"    {line}")
    except Exception:
        pass

    print()
    return env_info


def install_dependencies() -> None:
    """安装项目依赖：pip install -e ."""
    print("正在安装项目依赖...")
    project_root = Path(__file__).parent.parent

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True
        )
        print("✓ 依赖安装成功")
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        print(f"❌ 依赖安装失败：{e}")
        if e.stderr:
            print(f"   错误信息：{e.stderr}")
        sys.exit(1)


def is_admin() -> bool:
    """检测当前进程是否具有管理员权限

    Returns:
        True 如果是管理员，False 否则
    """
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def register_scheduled_task() -> bool:
    """注册 Windows 任务计划（开机自启）

    使用 schtasks /create 创建任务，需要管理员权限。
    如果失败，返回 False 并给出 fallback 提示。

    Returns:
        True 如果成功，False 如果失败
    """
    print("\n正在注册开机自启动任务...")

    # 检查管理员权限
    if not is_admin():
        print("⚠ 警告：当前不是管理员权限，无法注册开机自启动任务")
        print("  请以管理员身份运行此脚本，或手动设置开机自启动：")
        print(f"    1. Win+R 打开运行窗口")
        print(f"    2. 输入：taskschd.msc")
        print(f"    3. 创建基本任务：触发器选择'登录时'")
        print(f"    4. 操作：启动程序 {sys.executable}")
        print(f"    5. 参数：-m src.daemon")
        print()
        return False

    task_name = "Lamix"
    # 用 pythonw.exe 避免开机启动时弹出控制台窗口
    python_exe = sys.executable
    pythonw_exe = python_exe.replace("python.exe", "pythonw.exe")
    if not Path(pythonw_exe).exists():
        pythonw_exe = python_exe
    daemon_command = f'"{pythonw_exe}" -m src.daemon'

    # 先尝试删除已存在的任务
    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
            text=True,
            timeout=10
        )
    except Exception:
        pass

    # 创建新任务
    try:
        result = subprocess.run(
            [
                "schtasks", "/create",
                "/tn", task_name,
                "/tr", daemon_command,
                "/sc", "onlogon",
                "/rl", "limited",
                "/f"
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        print(f"✓ 开机自启动任务注册成功：{task_name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 任务计划注册失败：{e}")
        if e.stderr:
            print(f"   错误信息：{e.stderr}")
        print("\n  Fallback 方案：请手动设置开机自启动")
        print(f"    命令：schtasks /create /tn Lamix /tr \"{daemon_command}\" /sc onlogon /rl limited")
        print()
        return False
    except Exception as e:
        print(f"❌ 任务计划注册遇到未知错误：{e}")
        return False


def ensure_log_directory() -> Path:
    """确保日志目录存在

    Returns:
        日志目录路径
    """
    log_dir = Path.home() / ".lamix" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def start_daemon() -> None:
    """首次拉起 daemon 守护进程

    使用 CREATE_NEW_PROCESS_GROUP 和 DETACHED_PROCESS 标志，
    确保 daemon 进程独立运行，不随安装脚本退出而终止。
    """
    print("\n正在启动 Lamix daemon...")

    log_dir = ensure_log_directory()
    daemon_log = log_dir / "daemon.log"

    # Windows 进程创建标志
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    # 用 pythonw.exe 启动 daemon（无控制台窗口）
    python_exe = sys.executable
    pythonw_exe = python_exe.replace("python.exe", "pythonw.exe")
    if not Path(pythonw_exe).exists():
        pythonw_exe = python_exe  # fallback

    try:
        with open(daemon_log, "a", encoding="utf-8") as log_file:
            subprocess.Popen(
                [pythonw_exe, "-m", "src.daemon"],
                stdout=log_file,
                stderr=log_file,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                cwd=Path(__file__).parent.parent
            )
        print(f"✓ Daemon 已启动（后台运行，无弹窗）")
        print(f"  日志文件：{daemon_log}")
    except Exception as e:
        print(f"❌ Daemon 启动失败：{e}")
        print(f"  请手动运行：{sys.executable} -m src.daemon")


def uninstall() -> None:
    """卸载 Lamix：删除任务计划"""
    print("正在卸载 Lamix...")

    # 检查管理员权限
    if not is_admin():
        print("⚠ 警告：当前不是管理员权限")
        print("  某些卸载操作可能需要管理员权限")
        print()

    task_name = "Lamix"

    # 删除任务计划
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print(f"✓ 已删除开机自启动任务：{task_name}")
        else:
            if "找不到" in result.stderr or "not found" in result.stderr.lower():
                print(f"  任务 {task_name} 不存在，跳过")
            else:
                print(f"⚠ 删除任务失败：{result.stderr}")
    except Exception as e:
        print(f"❌ 删除任务时出错：{e}")

    # 用户数据清理选择
    print()
    data_dir = Path.home() / ".lamix"
    if data_dir.exists():
        print(f"配置目录 {data_dir} 仍存在。")
        print("  保留：下次安装可直接使用，配置、记忆、技能全部保留。")
        print("  删除：彻底清除所有个人数据。")
        choice = input("\n是否删除配置目录？(y/N): ").strip().lower()
        if choice in ("y", "yes", "是"):
            import shutil
            try:
                shutil.rmtree(data_dir)
                print(f"✓ 已删除配置目录：{data_dir}")
            except Exception as e:
                print(f"❌ 删除失败：{e}")
                print(f"  请手动删除：{data_dir}")
        else:
            print(f"  已保留配置目录：{data_dir}")

    print("\n卸载完成。")
    project_dir = Path(__file__).parent.parent
    print(f"项目代码需手动删除：{project_dir}")
    print()


def main() -> None:
    """主入口"""
    parser = argparse.ArgumentParser(description="Lamix Windows 安装/卸载脚本")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="卸载 Lamix（删除任务计划）"
    )
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
        return

    print("=" * 60)
    print("Lamix Windows 安装程序")
    print("=" * 60)
    print()

    # 1. 检查 Python 版本
    check_python_version()

    # 2. 检测 Python 环境
    env_info = detect_python_environment()

    # 3. 安装依赖
    install_dependencies()

    # 4. 注册任务计划（开机自启）
    task_registered = register_scheduled_task()

    # 5. 启动 daemon
    start_daemon()

    # 完成提示
    print("\n" + "=" * 60)
    print("✓ 安装完成！")
    print("=" * 60)
    print()
    print("下一步：")
    print("  1. 运行 'lamix-cli --config' 进行初始配置")
    print("  2. 运行 'lamix-cli' 开始使用")
    print()

    if not task_registered:
        print("注意：开机自启动任务未成功注册，请参考上面的手动设置说明。")
        print()

    print(f"日志路径：{Path.home() / '.lamix' / 'logs'}")
    print(f"配置路径：{Path.home() / '.lamix' / 'config.yaml'}")
    print()


if __name__ == "__main__":
    main()
