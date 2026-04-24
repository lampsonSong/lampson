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
    LAMPSON_DIR,
)
from src.core.session import Session


PROMPT_STYLE = Style.from_dict({
    "prompt": "ansigreen bold",
})


def _parse_args() -> tuple[str | None, bool]:
    """解析命令行参数，返回 (input_text, is_slash_command)。

    返回 None 表示进入交互式 REPL 模式。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="lampson",
        description="Lampson CLI 智能助手",
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
    parser.add_argument(
        "--serve", action="store_true", default=False,
        help="启动飞书消息监听服务",
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
    if args.serve:
        return "/serve", True

    query = args.query_c or args.query
    if query:
        return query, False

    # 检测管道输入
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped, False

    return None, False


def _run_repl(session: Session) -> None:
    """交互式 REPL 循环。"""
    skill_count = len(session.skills)
    feishu_status = "已连接" if session.feishu_ready else "未配置"
    print(f"Lampson 已启动（技能: {skill_count} 个，飞书: {feishu_status}）。输入 /help 查看命令，Ctrl+C 或 /exit 退出。\n")

    history_file = LAMPSON_DIR / ".repl_history"
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

            # /serve 特殊处理：在 gateway 层阻塞启动监听
            if result.reply == "__SERVE__":
                try:
                    session.start_feishu_listener()
                except Exception as e:
                    print(f"\n[serve] {e}\n")
                continue

            if result.reply:
                if result.is_command:
                    print(result.reply)
                else:
                    print(f"\nLampson> {result.reply}\n")

            if result.compaction_msg:
                print(result.compaction_msg)

    finally:
        print("\n正在保存会话摘要...")
        session.save_summary()
        print("再见！")


def main() -> None:
    """程序入口。"""
    non_interactive_input, is_slash_command = _parse_args()

    # 非交互模式
    if non_interactive_input is not None:
        config = load_config()
        if not is_config_complete(config):
            print("Lampson 未配置，请先运行 lampson 进入交互模式完成配置。")
            sys.exit(1)

        session = Session.from_config(config)
        result = session.handle_input(non_interactive_input)

        if result.reply == "__SERVE__":
            try:
                session.start_feishu_listener()
            except Exception as e:
                print(f"[serve] {e}")
        elif result.reply:
            print(result.reply)
        return

    # 交互模式
    config = load_config()
    if not is_config_complete(config):
        try:
            config = run_setup_wizard()
        except (KeyboardInterrupt, EOFError):
            print("\n配置已取消，退出。")
            sys.exit(0)
        if not is_config_complete(config):
            print("API Key 未填写，无法启动。")
            sys.exit(1)

    session = Session.from_config(config)
    _run_repl(session)


if __name__ == "__main__":
    main()
