"""Lamix 顶层命令分发器。

lamix              → 显示帮助（macOS/开发环境）/ 直接启动 CLI（Windows exe）
lamix cli [query]  → 交互式 CLI（启动 daemon + REPL）
lamix gateway      → 仅启动 daemon
lamix model        → 模型管理（占位）
lamix update       → 自更新（源码: git pull / Windows exe: 下载新版本）
lamix config       → 显示当前配置
lamix -V/--version → 版本号

Windows 上双击 lamix.exe（无参数）自动进入交互式 CLI 模式。
macOS/Linux 上无参数时显示帮助信息。
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style

from src.core.config import (
    load_config,
    is_config_complete,
    run_setup_wizard,
    LAMIX_DIR,
)
from src.core.session_manager import get_session_manager
from src.memory import session_store

# 导入 CLI 样式和补全模块
from src.cli.styled import (
    C, print_bot, print_command, print_info, print_success,
    print_warning, print_error, print_divider, print_banner,
    create_progress,
)
from src.cli.completion import LamixCompleter, create_key_bindings

PROMPT_STYLE = Style.from_dict({
    "prompt": "ansigreen bold",
    "command": "ansigreen",
})


def _get_version() -> str:
    """从 pyproject.toml 的 importlib.metadata 读取版本号。"""
    try:
        from importlib.metadata import version
        return version("lamix")
    except Exception:
        return "0.2.0"


def _cli_partial_sender(text: str) -> None:
    """CLI 下 compaction 等进度文案即时打印。"""
    from src.cli.styled import console
    console.print(f"[dim]{text}[/dim]", end="", flush=True)


def _cli_progress_callback(event: dict) -> None:
    """CLI 模式下的工具调用进度回调，实时打印到终端。"""
    if event.get("type") != "tool_progress":
        return
    tool = event.get("tool", "?")
    args_preview = event.get("args_preview", "")
    result_preview = event.get("result_preview", "")
    round_num = event.get("round", "?")
    from src.cli.styled import print_tool, C
    print_tool(f"[{C.TOOL}]  [工具 {round_num}] {tool}({args_preview}) → {result_preview}")


def _maybe_greet_first_run(session) -> None:
    """首次运行时自动发一句问候，不追问个人信息，从自然对话中学习。"""
    user_path = LAMIX_DIR / "USER.md"
    if not user_path.exists():
        return
    try:
        user_content = user_path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    # 判断是否仍是默认内容（只包含"称呼：用户"之类的占位）
    if "称呼：用户" in user_content or len(user_content) < 30:
        result = session.handle_input("[系统] 这是首次运行，请用一句话简短问候用户，不要追问任何个人信息。")
        if result.reply:
            # 去掉可能的 "Lamix>" 前缀
            reply = result.reply
            if reply.startswith("Lamix> "):
                reply = reply[7:]
            print()
            print_bot(reply)


def _run_repl(config: dict) -> None:
    """交互式 REPL 循环。"""
    # 打印 banner
    print_banner()
    print_divider()
    
    mgr = get_session_manager(config)
    session = mgr.get_or_create("cli", "default")
    # CLI 模式下设置 progress_callback，工具调用时实时打印进度
    session.agent.progress_callback = _cli_progress_callback
    session.partial_sender = _cli_partial_sender
    
    skill_count = len(session.skills)
    feishu_status = "已连接" if session.feishu_ready else "未配置"
    
    # 启动信息
    print_info(f"技能: {skill_count} 个  |  飞书: {feishu_status}")
    print_info("输入 /help 查看命令，Ctrl+C 或 /exit 退出。")
    print_divider()
    print()

    # 首次运行检测：USER.md 仍是默认内容时，自动发问候让对话自然开始
    _maybe_greet_first_run(session)

    # 加载历史记录用于补全
    history_file = LAMIX_DIR / ".repl_history"
    history_content = []
    if history_file.exists():
        try:
            history_content = history_file.read_text(encoding="utf-8").strip().splitlines()
        except Exception:
            pass
    
    # 创建补全器
    completer = LamixCompleter(history=history_content)
    key_bindings = create_key_bindings()

    prompt_session: PromptSession = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        style=PROMPT_STYLE,
        completer=completer,
        key_bindings=key_bindings,
        complete_while_typing=True,
        reserve_space_for_menu=8,
    )

    try:
        while True:
            try:
                user_input = prompt_session.prompt(
                    [("class:prompt", "you> ")],
                ).strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue

            # 打印用户输入（带样式）
            from src.cli.styled import console
            console.print(f"[{C.USER_INPUT}]{user_input}[/{C.USER_INPUT}]")

            result = session.handle_input(user_input)

            if result.is_exit:
                break

            if result.is_new:
                # 通过 SessionManager 统一重置
                session = mgr.reset_session("cli", "default")
                session.agent.progress_callback = _cli_progress_callback
                session.partial_sender = _cli_partial_sender
                print_divider()
                print_info("新 session 已开始")
                print_divider()
                print()
                continue

            if result.reply:
                if result.is_command:
                    print_command(result.reply)
                else:
                    print_bot(result.reply)

                # 计划待确认时由用户选择是否执行
                if (
                    not result.is_command
                    and result.reply
                    and "请确认是否执行此计划" in result.reply
                ):
                    try:
                        confirm_input = prompt_session.prompt(
                            [("class:prompt", "确认执行？(y/n): ")],
                        ).strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        confirm_input = "n"
                    if confirm_input in ("y", "yes", "是"):
                        exec_result = session.agent.confirm_and_execute()
                        if exec_result:
                            print()
                            print_bot(exec_result)
                    else:
                        cancel_result = session.agent.cancel_plan()
                        print_info(f"已取消: {cancel_result}")
                    
                    # 与 handle_input 一致：确认/取消后的回合也尝试压缩
                    try:
                        cr = session.agent.maybe_compact(
                            session_store=session_store,
                            session_id=session.session_id or "",
                            progress_callback=_cli_partial_sender,
                        )
                        if cr is not None:
                            if cr.success:
                                print_success(
                                    f"上下文压缩完成：归档 {cr.archived_count} 条内容，"
                                    f"{cr.tokens_before} → {cr.tokens_after} token"
                                )
                            else:
                                print_error(f"上下文压缩失败: {cr.error}")
                    except Exception:
                        pass

            if result.compaction_msg:
                print_info(result.compaction_msg)

    finally:
        print_divider()
        print_info("正在清理会话...")
        session.cleanup()
        print_success("再见！")


def _ensure_daemon_running() -> None:
    """检查 daemon 是否在运行，没有则在后台启动。"""
    import subprocess
    from pathlib import Path

    pid_path = Path.home() / ".lamix" / "logs" / "daemon.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            # 检查进程是否存活
            try:
                if sys.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        print_info(f"daemon 已在运行 (PID={pid})")
                        return
                else:
                    os.kill(pid, 0)  # 不发信号，只检查进程存在
                    print_info(f"daemon 已在运行 (PID={pid})")
                    # Windows 上确保 watchdog 也在运行
                    if sys.platform == "win32" and not getattr(sys, "frozen", False):
                        try:
                            _ensure_watchdog_on_windows()
                        except Exception:
                            pass
                    return
            except (ProcessLookupError, OSError):
                pass  # 进程不存在，继续启动
        except (ValueError, OSError):
            pass

    # 启动 daemon
    print_info("daemon 未运行，正在后台启动...")
    log_dir = Path.home() / ".lamix" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    daemon_log = log_dir / "daemon.log"

    python_exe = sys.executable
    # PyInstaller 打包后 sys.executable 是 lamix.exe，不能用 -m，
    # 直接用 lamix gateway 子命令启动 daemon
    import importlib.util
    _is_frozen = getattr(sys, "frozen", False)

    if _is_frozen:
        daemon_cmd = [python_exe, "gateway"]
    else:
        # Windows 上用 pythonw.exe 避免弹窗
        if sys.platform == "win32":
            pythonw = python_exe.replace("python.exe", "pythonw.exe")
            if Path(pythonw).exists():
                python_exe = pythonw
        daemon_cmd = [python_exe, "-m", "src.daemon"]

    try:
        if sys.platform == "win32" and not _is_frozen:
            subprocess.Popen(
                daemon_cmd,
                stdout=open(daemon_log, "a"),
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                daemon_cmd,
                stdout=open(daemon_log, "a"),
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        import time
        time.sleep(1)
        print_success("daemon 已在后台启动")
    except Exception as e:
        print_error(f"启动 daemon 失败: {e}")


def _ensure_watchdog_on_windows() -> None:
    """Windows 上确保 watchdog 在运行。"""
    import subprocess
    from pathlib import Path

    watchdog_pid_path = Path.home() / ".lamix" / "logs" / "watchdog.pid"
    if watchdog_pid_path.exists():
        try:
            pid = int(watchdog_pid_path.read_text().strip())
            os.kill(pid, 0)
            return
        except (ProcessLookupError, OSError, ValueError):
            pass

    # 启动 watchdog
    python_exe = sys.executable
    log_dir = Path.home() / ".lamix" / "logs"
    watchdog_log = log_dir / "watchdog.log"

    try:
        subprocess.Popen(
            [python_exe, "-m", "src.watchdog"],
            stdout=open(watchdog_log, "a"),
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        import time
        time.sleep(0.5)
    except Exception as e:
        print_error(f"启动 watchdog 失败: {e}")


def _handle_gateway_predicate(predicate: str, config: dict) -> None:
    """处理 gateway 的谓词参数（如 --no-repl, --no-sandbox）。"""
    actions = predicate.split(",")
    feishu_enabled = "no-feishu" not in actions
    repl_enabled = "no-repl" not in actions
    sandbox_enabled = "no-sandbox" not in actions

    if feishu_enabled and config.get("feishu", {}).get("enabled", False):
        mgr = get_session_manager(config)
        session = mgr.get_or_create("gateway", "default")
        session.agent.progress_callback = _cli_progress_callback
        session.partial_sender = _cli_partial_sender

    if repl_enabled:
        print_info("启动交互式 CLI...")
        _run_repl(config)


def run_cli(args: argparse.Namespace) -> None:
    """'lamix cli' 子命令：继承原 main() 的所有逻辑。"""
    # 确定非交互输入
    non_interactive_input: str | None = None
    is_slash_command = False

    if args.help_cmd:
        non_interactive_input, is_slash_command = "/help", True
    elif args.memory:
        non_interactive_input, is_slash_command = "/memory " + " ".join(args.memory), True
    elif args.skills:
        non_interactive_input, is_slash_command = "/skills " + " ".join(args.skills), True
    elif args.feishu:
        non_interactive_input, is_slash_command = "/feishu " + " ".join(args.feishu), True
    elif args.update:
        non_interactive_input, is_slash_command = "/update " + " ".join(args.update), True
    else:
        query = args.query_c or args.query
        if query:
            non_interactive_input = query
        elif not sys.stdin.isatty():
            piped = sys.stdin.read().strip()
            if piped:
                non_interactive_input = piped

    config = load_config()
    if not is_config_complete(config):
        if non_interactive_input is not None:
            print_error("Lamix 未配置，请先运行 lamix cli 进入交互模式完成配置。")
            sys.exit(1)
        try:
            config = run_setup_wizard()
        except (KeyboardInterrupt, EOFError):
            print_info("配置已取消，退出。")
            sys.exit(0)
        if not is_config_complete(config):
            print_error("API Key 未填写，无法启动。")
            sys.exit(1)

    _init_platform(config)

    # 后台启动 daemon
    _ensure_daemon_running()

    # 单条查询模式
    if non_interactive_input:
        mgr = get_session_manager(config)
        session = mgr.get_or_create("cli", "default")
        session.agent.progress_callback = _cli_progress_callback
        session.partial_sender = _cli_partial_sender

        if is_slash_command:
            result = session.handle_input(non_interactive_input)
        else:
            result = session.handle_input(non_interactive_input)
        if result.reply:
            print(result.reply)
        return

    # 交互模式
    _run_repl(config)


def _init_platform(config: dict) -> None:
    """初始化平台相关组件。"""
    # 初始化飞书
    feishu_config = config.get("feishu", {})
    if feishu_config.get("enabled", False):
        try:
            from src.feishu.bot import get_feishu_bot
            bot = get_feishu_bot(feishu_config)
            import threading
            threading.Thread(target=bot.start, daemon=True).start()
        except Exception as e:
            print_error(f"飞书初始化失败: {e}")


def run_gateway(args: argparse.Namespace) -> None:
    """'lamix gateway' 子命令：仅启动 daemon（后台常驻），启动 CLI。"""
    config = load_config()
    if not is_config_complete(config):
        print_info("Lamix 未配置，将进行首次配置。\n")
    try:
        config = run_setup_wizard()
    except (KeyboardInterrupt, EOFError):
        print_info("配置已取消，退出。")
        sys.exit(0)
    if not is_config_complete(config):
        print_error("API Key 未填写，退出。")
        sys.exit(1)

    _init_platform(config)
    print_info("daemon 已就绪，启动交互式 CLI...")
    _run_repl(config)


def run_model(args: argparse.Namespace) -> None:
    """'lamix model' 子命令：重新配置 LLM 模型。"""
    from src.core.config import load_config, is_config_complete, run_setup_wizard

    config = load_config()
    if not is_config_complete(config):
        print_info("Lamix 未配置，将进行首次配置。\n")
    try:
        run_setup_wizard(title="模型配置 - 重新选择 LLM 供应商和模型")
    except (KeyboardInterrupt, EOFError):
        print_info("配置已取消。")
        return
    except SystemExit:
        return
    print_success("模型配置完成。若 daemon 正在运行，配置将在 30 秒内自动热重载生效。")


def run_update(args: argparse.Namespace) -> None:
    """从 GitHub 拉取最新代码/下载最新 exe 并重启。"""
    # Windows exe 模式：走 exe 自更新
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        from src.selfupdate.exe_updater import run_exe_update
        result = run_exe_update()
        print(result)
        return

    import os
    import signal
    import subprocess
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    print_info("正在从 GitHub 拉取最新代码...")
    try:
        result = subprocess.run(
            ["git", "pull"], cwd=str(project_root),
            capture_output=True, text=True, check=True,
        )
        print_success(result.stdout.strip() or "代码已更新")
    except subprocess.CalledProcessError as e:
        print_error(f"git pull 失败: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print_error("未找到 git 命令，请确保已安装 Git。")
        sys.exit(1)

    # 重启 daemon
    print_info("正在重启 daemon...")
    pid_path = Path.home() / ".lamix" / "logs" / "daemon.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError, PermissionError):
            pass

    print_success("更新完成，请重新运行 lamix。")


def run_config(args: argparse.Namespace) -> None:
    """'lamix config' 子命令：显示当前配置。"""
    from src.cli.styled import print_table, C
    from src.core.config import load_config

    config = load_config()
    if not config:
        print_warning("未找到配置文件，请先运行 lamix cli 完成配置。")
        return

    # 基本配置
    basic_info = [
        ["项目", "值"],
        ["API 供应商", config.get("provider", "unknown")],
        ["模型", config.get("model", "unknown")],
        ["API Base URL", config.get("api_base", "-")],
    ]
    print_table("📋 基本配置", basic_info[0], basic_info[1:])

    # 飞书配置
    feishu = config.get("feishu", {})
    if feishu.get("enabled"):
        feishu_info = [
            ["项目", "值"],
            ["启用", "✓"],
            ["App ID", feishu.get("app_id", "-")[:20] + "..." if len(feishu.get("app_id", "")) > 20 else feishu.get("app_id", "-")],
        ]
        print_table("📱 飞书配置", feishu_info[0], feishu_info[1:])
    else:
        print_warning("飞书未配置（使用 --feishu 参数启用）")

    # 目录信息
    print_info(f"数据目录: {LAMIX_DIR}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lamix",
        description="一起认识这个世界的 AI 伙计",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              lamix cli                    # 启动交互式 CLI
              lamix cli 你好                # 单次对话
              lamix gateway                # 仅启动 daemon
              lamix config                 # 显示配置
              lamix -V                     # 显示版本号

            首次运行会自动引导配置。
        """),
    )
    parser.add_argument("-V", "--version", action="version", version=f"lamix {_get_version()}")

    subparsers = parser.add_subparsers(dest="command", title="子命令", description="可用子命令")

    # lamix cli
    cli_parser = subparsers.add_parser("cli", help="启动交互式 CLI（默认模式）")
    cli_parser.add_argument("query", nargs="*", help="直接执行的查询")
    cli_parser.add_argument("-c", dest="query_c", help="单条查询（内部使用）")
    cli_parser.add_argument("--help-cmd", action="store_true", help="显示帮助")
    cli_parser.add_argument("--memory", nargs="*", help="查看/操作记忆")
    cli_parser.add_argument("--skills", nargs="*", help="查看/操作技能")
    cli_parser.add_argument("--feishu", nargs="*", help="飞书命令")
    cli_parser.add_argument("--update", nargs="*", help="自更新命令")

    # lamix gateway
    gateway_parser = subparsers.add_parser("gateway", help="仅启动 daemon")
    gateway_parser.add_argument(
        "-p", "--predicate",
        default="",
        help="控制选项，逗号分隔: no-feishu, no-repl, no-sandbox"
    )

    # lamix model
    model_parser = subparsers.add_parser("model", help="重新配置 LLM 模型")

    # lamix update
    update_parser = subparsers.add_parser("update", help="检查并执行自更新")

    # lamix config
    config_parser = subparsers.add_parser("config", help="显示当前配置")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # 无子命令：Windows exe 或开发环境行为
    if args.command is None:
        _is_frozen = getattr(sys, "frozen", False)

        # Windows exe 双击：无参数直接进 CLI
        if _is_frozen and sys.platform == "win32":
            config = load_config()
            if not is_config_complete(config):
                try:
                    config = run_setup_wizard()
                except (KeyboardInterrupt, EOFError):
                    sys.exit(0)
                if not is_config_complete(config):
                    sys.exit(1)
            _init_platform(config)
            _ensure_daemon_running()
            _run_repl(config)
        else:
            # macOS/Linux 开发环境：显示帮助
            parser.print_help()
        return

    # 分发子命令
    if args.command == "cli":
        run_cli(args)
    elif args.command == "gateway":
        run_gateway(args)
    elif args.command == "model":
        run_model(args)
    elif args.command == "update":
        run_update(args)
    elif args.command == "config":
        run_config(args)


if __name__ == "__main__":
    main()
