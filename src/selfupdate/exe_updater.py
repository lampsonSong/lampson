"""Windows exe 自更新模块。"""
import os
import shutil
import sys
from pathlib import Path

GITHUB_API_URL = "https://api.github.com/repos/lampsonSong/lamix/releases/latest"
ASSET_NAME = "lamix.exe"  # GitHub Actions 上传的 exe 文件名


def _current_version() -> str:
    try:
        from importlib.metadata import version
        return version("lamix")
    except Exception:
        return "0.0.0"


def _version_tuple(v: str):
    try:
        from packaging.version import Version
        return Version(v)
    except ImportError:
        return tuple(int(x) for x in v.split(".") if x.isdigit())


def check_latest_version() -> "tuple[str, str] | None":
    """返回 (tag_name, download_url) 或 None（已是最新或失败）。"""
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "lamix-updater"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        tag_name: str = data.get("tag_name", "")
        latest_ver = tag_name.lstrip("v")
        current_ver = _current_version()

        if _version_tuple(latest_ver) <= _version_tuple(current_ver):
            return None

        # 优先匹配主程序 exe
        for asset in data.get("assets", []):
            name: str = asset.get("name", "")
            if name.lower() == ASSET_NAME:
                return tag_name, asset["browser_download_url"]

        # 回退：取任意的 .exe（排除卸载程序）
        for asset in data.get("assets", []):
            name: str = asset.get("name", "")
            if name.endswith(".exe") and "uninstall" not in name.lower():
                return tag_name, asset["browser_download_url"]

        return None
    except Exception:
        return None


def download_exe(url: str, target_path: str) -> bool:
    """下载 exe 到 target_path，成功返回 True。"""
    tmp_path = target_path + ".tmp"
    try:
        try:
            import httpx
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
        except ImportError:
            import urllib.request
            urllib.request.urlretrieve(url, tmp_path)

        if os.path.getsize(tmp_path) < 1024 * 1024:
            os.remove(tmp_path)
            return False

        os.replace(tmp_path, target_path)
        return True
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def create_updater_script(exe_path: str, new_exe_path: str, pid: int) -> str:
    """生成替换+重启的 .bat 脚本，返回脚本路径。"""
    script_path = str(Path(exe_path).parent / "_lamix_update.bat")
    bat = f"""@echo off
setlocal
set /a _timeout=30
:wait
tasklist /FI "PID eq {pid}" 2>nul | find /I "{pid}" >nul 2>&1
if errorlevel 1 goto do_update
set /a _timeout-=1
if %_timeout% leq 0 goto do_update
timeout /t 1 /nobreak >nul
goto wait
:do_update
del /f /q "{exe_path}"
move /y "{new_exe_path}" "{exe_path}"
start "" "{exe_path}"
del "%~f0"
"""
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(bat)
    return script_path


def run_exe_update() -> str:
    """主入口：检查、下载、准备更新脚本，返回状态信息。"""
    result = check_latest_version()
    if result is None:
        return "已是最新版本。"

    tag_name, download_url = result
    latest_ver = tag_name.lstrip("v")
    current_ver = _current_version()
    print(f"发现新版本: {tag_name}（当前: v{current_ver}）")
    print(f"下载地址: {download_url}")

    # 交互确认
    if sys.stdin.isatty():
        try:
            answer = input("是否立即更新？[Y/n] ").strip().lower()
            if answer not in ("", "y", "yes"):
                return "已取消更新。"
        except (EOFError, KeyboardInterrupt):
            return "已取消更新。"

    # 备份目录
    backup_dir = Path.home() / ".lamix" / "update"
    backup_dir.mkdir(parents=True, exist_ok=True)
    new_exe_path = str(backup_dir / "lamix_new.exe")

    print("正在下载新版本...")
    if not download_exe(download_url, new_exe_path):
        return "下载失败，请检查网络后重试。"

    exe_path = sys.executable
    pid = os.getpid()

    # 备份当前 exe
    bak_path = str(backup_dir / "lamix.exe.bak")
    try:
        shutil.copy2(exe_path, bak_path)
    except Exception:
        pass

    script_path = create_updater_script(exe_path, new_exe_path, pid)

    import subprocess
    subprocess.Popen(
        ["cmd.exe", "/c", script_path],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )

    print("更新将在退出后执行...")
    return f"新版本 {tag_name} 已准备好，请退出程序以完成更新。"
