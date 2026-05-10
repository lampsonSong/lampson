"""CLI 入口：基于 prompt_toolkit 的 REPL。

职责仅限：参数解析 → 构建 Session → REPL 循环。
所有业务逻辑在 core/session.py，LLM/工具/规划在 core/agent.py。
"""

from __future__ import annotations

import sys

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


def _parse_args() -> tuple[str | None, bool]:
    """解析命令行参数，返回 (input_text, is_slash_command)。

    返回 None 表示进入交互式 REPL 模式。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="lamix",
        description="Lamix CLI 智能助手",
        add_help=True,
    )
    parser.add_argument(
        "query", nargs="?", default=None,
        help="直接对话内容（单条查询模式）",
    )
    parser.add_argument(
        "-c", dest="query_c", default=None, metavar="QUERY",
        help="直接对话内容（显式指定）",
    )
    parser.add_argument(
        "--memory", nargs="+", metavar="SUBCMD",
        help="执行 memory 子命令，如 --memory show",
    )
    parser.add_argument(
        "--skills", nargs="+", metavar="SUBCMD",
        help="执行 skills 子命令，如 --skills list",
    )
    parser.add_argument(
        "--feishu", nargs="+", metavar="SUBCMD",
        help="执行 feishu 子命令",
    )
    parser.add_argument(
        "--update", nargs="+", metavar="SUBCMD",
        help="执行 update 子命令",
    )
    parser.add_argument(
        "--config", action="store_true", default=False,
        help="显示当前配置",
    )
    parser.add_argument(
        "--help-cmd", action="store_true", default=False, dest="help_cmd",
        help="显示可用命令帮助",
    )

    args = parser.parse_args()

    if args.help_cmd:
        return "/help", True
    if args.config:
        return "/config", True
    if args.memory:
        return "/memory " + " ".join(args.memory), True
    if args.skills:
        return "/skills " + " ".join(args.skills), True
    if args.feishu:
        return "/feishu " + " ".join(args.feishu), True
    if args.update:
        return "/update " + " ".join(args.update), True

    query = args.query_c or args.query
    if query:
        return query, False

    # 检测管道输入
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped, False

    return None, False


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


def main() -> None:
    """程序入口。"""
    non_interactive_input, is_slash_command = _parse_args()

    config = load_config()
    if not is_config_complete(config):
        if non_interactive_input is not None:
            print("Lamix 未配置，请先运行 lamix 进入交互模式完成配置。")
            sys.exit(1)
        try:
            config = run_setup_wizard()
        except (KeyboardInterrupt, EOFError):
            print("\n配置已取消，退出。")
            sys.exit(0)
        if not is_config_complete(config):
            print("API Key 未填写，无法启动。")
            sys.exit(1)

    mgr = get_session_manager(config)

    # 初始化 PlatformManager（多平台网关 + 后台任务支持）
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

    # 非交互模式
    if non_interactive_input is not None:
        session = mgr.get_or_create("cli", "default")
        session.agent.progress_callback = _cli_progress_callback
        session.partial_sender = _cli_partial_sender
        result = session.handle_input(non_interactive_input)

        if result.reply:
            print(result.reply)
        return

    # 交互模式
    _run_repl(config)


if __name__ == "__main__":
    main()
