"""Lamix 顶层命令分发器。

lamix              → 显示帮助
lamix cli [query]  → 交互式 CLI（启动 daemon + REPL）
lamix gateway      → 仅启动 daemon
lamix model        → 模型管理（占位）
lamix update       → 自更新（占位）
lamix config       → 显示当前配置
lamix -V/--version → 版本号
"""

from __future__ import annotations

import argparse
import sys
import textwrap

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


PROMPT_STYLE = Style.from_dict({
    "prompt": "ansigreen bold",
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
    print(text, flush=True)


def _cli_progress_callback(event: dict) -> None:
    """CLI 模式下的工具调用进度回调，实时打印到终端。"""
    if event.get("type") != "tool_progress":
        return
    tool = event.get("tool", "?")
    args_preview = event.get("args_preview", "")
    result_preview = event.get("result_preview", "")
    round_num = event.get("round", "?")
    print(f"  [工具 {round_num}] {tool}({args_preview}) → {result_preview}", flush=True)


def _run_repl(config: dict) -> None:
    """交互式 REPL 循环。"""
    mgr = get_session_manager(config)
    session = mgr.get_or_create("cli", "default")
    # CLI 模式下设置 progress_callback，工具调用时实时打印进度
    session.agent.progress_callback = _cli_progress_callback
    session.partial_sender = _cli_partial_sender
    skill_count = len(session.skills)
    feishu_status = "已连接" if session.feishu_ready else "未配置"
    print(f"Lamix 已启动（技能: {skill_count} 个，飞书: {feishu_status}）。输入 /help 查看命令，Ctrl+C 或 /exit 退出。\n")

    history_file = LAMIX_DIR / ".repl_history"
    prompt_session: PromptSession = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        style=PROMPT_STYLE,
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

            result = session.handle_input(user_input)

            if result.is_exit:
                break

            if result.is_new:
                # 通过 SessionManager 统一重置
                session = mgr.reset_session("cli", "default")
                session.agent.progress_callback = _cli_progress_callback
                session.partial_sender = _cli_partial_sender
                print("\n[新 session 已开始]\n")
                continue

            if result.reply:
                if result.is_command:
                    print(result.reply)
                else:
                    print(f"\nLamix> {result.reply}\n")

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
                            print(f"\nLamix> {exec_result}\n")
                    else:
                        print(f"\nLamix> {session.agent.cancel_plan()}\n")
                    # 与 handle_input 一致：确认/取消后的回合也尝试压缩
                    try:
                        cr = session.agent.maybe_compact(
                            session_store=session_store,
                            session_id=session.session_id or "",
                            progress_callback=_cli_partial_sender,
                        )
                        if cr is not None:
                            if cr.success:
                                print(
                                    f"[上下文压缩] 已完成，归档 {cr.archived_count} 条内容。"
                                )
                            else:
                                print(f"[上下文压缩] 失败: {cr.error}")
                    except Exception:
                        pass

            if result.compaction_msg:
                print(result.compaction_msg)

    finally:
        print("\n正在清理会话...")
        session.cleanup()
        print("再见！")


def _init_platform(config: dict) -> None:
    """初始化 PlatformManager（多平台网关 + 后台任务支持）。"""
    import asyncio
    import threading
    from src.platforms.manager import PlatformManager
    from src.platforms.adapters.cli import CliAdapter

    pm = PlatformManager(config)
    PlatformManager._instance = pm
    _cli_loop = asyncio.new_event_loop()
    pm._loop = _cli_loop
    _cli_loop_thread = threading.Thread(target=_cli_loop.run_forever, daemon=True)
    _cli_loop_thread.start()
    pm.register(CliAdapter())

    # 注册飞书 adapter（如果有配置）
    feishu_cfg = config.get("feishu", {})
    if feishu_cfg.get("app_id") and feishu_cfg.get("app_secret"):
        from src.platforms.adapters.feishu import FeishuAdapter
        pm.register(FeishuAdapter({
            "app_id": feishu_cfg["app_id"],
            "app_secret": feishu_cfg["app_secret"],
        }))


# ── 子命令处理 ────────────────────────────────────────────

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
            print("Lamix 未配置，请先运行 lamix cli 进入交互模式完成配置。")
            sys.exit(1)
        try:
            config = run_setup_wizard()
        except (KeyboardInterrupt, EOFError):
            print("\n配置已取消，退出。")
            sys.exit(0)
        if not is_config_complete(config):
            print("API Key 未填写，无法启动。")
            sys.exit(1)

    _init_platform(config)

    # 非交互模式
    if non_interactive_input is not None:
        mgr = get_session_manager(config)
        session = mgr.get_or_create("cli", "default")
        session.agent.progress_callback = _cli_progress_callback
        session.partial_sender = _cli_partial_sender
        result = session.handle_input(non_interactive_input)
        if result.reply:
            print(result.reply)
        return

    # 交互模式
    _run_repl(config)


def run_gateway(args: argparse.Namespace) -> None:
    """'lamix gateway' 子命令：启动 daemon。"""
    from src.daemon import main as daemon_main
    daemon_main()


def run_model(args: argparse.Namespace) -> None:
    """'lamix model' 子命令：重新配置 LLM 模型。"""
    print("进入模型配置向导...")
    config = load_config()
    if not is_config_complete(config):
        print("Lamix 未配置，将进行首次配置。\n")
    try:
        run_setup_wizard(title="模型配置 - 重新选择 LLM 供应商和模型")
    except (KeyboardInterrupt, EOFError):
        print("\n配置已取消。")
        return
    except SystemExit:
        return
    print("模型配置完成。若 daemon 正在运行，配置将在 30 秒内自动热重载生效。")


def run_update(args: argparse.Namespace) -> None:
    """'lamix update' 子命令：从 GitHub 拉取最新代码并重启 daemon。"""
    import os
    import signal
    import subprocess
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    print("正在从 GitHub 拉取最新代码...")
    try:
        result = subprocess.run(
            ["git", "pull"], cwd=str(project_root),
            capture_output=True, text=True, check=True,
        )
        stdout, stderr = result.stdout.strip(), result.stderr.strip()
        if stdout:
            print(stdout)
        if stderr:
            print(stderr)
    except subprocess.CalledProcessError as e:
        print(f"Git 拉取失败: {e}")
        return
    except FileNotFoundError:
        print("Git 未安装或未在 PATH 中。请手动执行 git pull 更新代码。")
        return

    # 检测 daemon 是否在运行，尝试优雅重启
    daemon_pid_path = Path.home() / ".lamix" / "logs" / "daemon.pid"
    if daemon_pid_path.exists():
        try:
            pid = int(daemon_pid_path.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"已向 daemon (PID={pid}) 发送 SIGTERM，watchdog 将自动重启。")
        except ProcessLookupError:
            print("daemon 进程已不存在，watchdog 会自动拉起。")
        except Exception as exc:
            print(f"检测到 daemon 在运行，但发送信号失败 ({exc})。请手动重启 daemon。")
    else:
        print("未检测到运行中的 daemon。如需后台运行，请执行 lamix gateway。")


def run_config(args: argparse.Namespace) -> None:
    """'lamix config' 子命令：显示当前配置。"""
    config = load_config()
    if not is_config_complete(config):
        print("Lamix 未配置，请先运行 lamix cli 进入交互模式完成配置。")
        sys.exit(1)

    _init_platform(config)

    mgr = get_session_manager(config)
    session = mgr.get_or_create("cli", "default")
    session.agent.progress_callback = _cli_progress_callback
    session.partial_sender = _cli_partial_sender
    result = session.handle_input("/config")
    if result.reply:
        print(result.reply)


# ── 帮助文本 ──────────────────────────────────────────────

HELP_TEXT = textwrap.dedent("""\
    usage: lamix <command> [options]

    Lamix - 自更新的 AI Agent daemon

    Commands:
      cli       启动交互式 CLI（含 daemon）
      gateway   仅启动 daemon（后台常驻）
      model     模型管理（重新配置 LLM）
      update    自更新（拉取代码并重启）
      config    显示当前配置

    Options:
      -V, --version  显示版本号
      -h, --help     显示帮助
""")


# ── 入口 ──────────────────────────────────────────────────

def main() -> None:
    """顶层命令分发器入口。"""
    parser = argparse.ArgumentParser(
        prog="lamix",
        description="Lamix - 自更新的 AI Agent daemon",
        add_help=True,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"lamix {_get_version()}",
    )

    subparsers = parser.add_subparsers(dest="command", title="commands")

    # ── cli ───────────────────────────────────────────────
    cli_parser = subparsers.add_parser(
        "cli", help="启动交互式 CLI（含 daemon）",
    )
    cli_parser.add_argument(
        "query", nargs="?", default=None,
        help="直接对话内容（单条查询模式）",
    )
    cli_parser.add_argument(
        "-c", dest="query_c", default=None, metavar="QUERY",
        help="直接对话内容（显式指定）",
    )
    cli_parser.add_argument(
        "--memory", nargs="+", metavar="SUBCMD",
        help="执行 memory 子命令，如 --memory show",
    )
    cli_parser.add_argument(
        "--skills", nargs="+", metavar="SUBCMD",
        help="执行 skills 子命令，如 --skills list",
    )
    cli_parser.add_argument(
        "--feishu", nargs="+", metavar="SUBCMD",
        help="执行 feishu 子命令",
    )
    cli_parser.add_argument(
        "--update", nargs="+", metavar="SUBCMD",
        help="执行 update 子命令",
    )
    cli_parser.add_argument(
        "--help-cmd", action="store_true", default=False, dest="help_cmd",
        help="显示可用命令帮助",
    )
    cli_parser.set_defaults(func=run_cli)

    # ── gateway ───────────────────────────────────────────
    gw_parser = subparsers.add_parser(
        "gateway", help="仅启动 daemon（后台常驻）",
    )
    gw_parser.set_defaults(func=run_gateway)

    # ── model ─────────────────────────────────────────────
    model_parser = subparsers.add_parser(
        "model", help="重新配置 LLM 模型",
    )
    model_parser.set_defaults(func=run_model)

    # ── update ────────────────────────────────────────────
    update_parser = subparsers.add_parser(
        "update", help="拉取代码并重启 daemon",
    )
    update_parser.set_defaults(func=run_update)

    # ── config ────────────────────────────────────────────
    config_parser = subparsers.add_parser(
        "config", help="显示当前配置",
    )
    config_parser.set_defaults(func=run_config)

    args = parser.parse_args()

    if args.command is None:
        # 双击 exe 或无参数时，默认启动交互式 CLI
        run_cli(argparse.Namespace(
            query=None, query_c=None, memory=None, skills=None,
            feishu=None, update=None, help_cmd=False,
        ))
        return

    args.func(args)


if __name__ == "__main__":
    main()
