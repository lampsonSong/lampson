"""CLI 入口：基于 prompt_toolkit 的 REPL，处理命令和自然语言输入。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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
from src.core.llm import LLMClient
from src.core.compaction import CompactionConfig, apply_compaction
from src.core.agent import Agent
from src.memory import manager as memory_mgr
from src.skills import manager as skills_mgr


PROMPT_STYLE = Style.from_dict({
    "prompt": "ansigreen bold",
})

HELP_TEXT = """
可用命令：
  /help                          显示此帮助
  /config                        查看当前配置
  /memory show                   查看核心记忆
  /memory add <text>             添加记忆条目
  /memory search <keyword>       搜索记忆
  /memory forget <keyword>       删除含关键词的记忆条目
  /skills list                   列出所有技能
  /skills show <name>            查看技能详情
  /skills create <name>          创建新技能
  /feishu send <id> <msg>        发送飞书消息（需配置 app_id/secret）
  /feishu read <chat_id>         读取飞书消息
  /serve                         启动飞书消息监听服务（长连接 WebSocket）
  /update <需求描述>              触发自更新
  /update rollback               回滚自更新
  /update list                   列出自更新分支
  /exit                          退出

直接输入自然语言即可与 Lampson 对话。
"""


def _init_feishu(config: dict[str, Any]) -> bool:
    """初始化飞书客户端，返回是否成功。"""
    feishu_cfg = config.get("feishu", {})
    app_id = feishu_cfg.get("app_id", "").strip()
    app_secret = feishu_cfg.get("app_secret", "").strip()
    if not app_id or not app_secret:
        return False
    try:
        from src.feishu import client as feishu_client
        feishu_client.init_client(app_id=app_id, app_secret=app_secret)
        return True
    except Exception:
        return False


def _install_default_skills() -> None:
    """将内置技能复制到用户目录（首次运行）。"""
    default_skills_dir = Path(__file__).resolve().parent.parent / "config" / "default_skills"
    try:
        skills_mgr.install_default_skills(default_skills_dir)
    except Exception:
        pass


def _handle_memory_command(parts: list[str]) -> None:
    sub = parts[1] if len(parts) > 1 else "show"

    if sub == "show":
        print(memory_mgr.show_core())

    elif sub == "add":
        if len(parts) < 3:
            print("用法: /memory add <text>")
            return
        text = " ".join(parts[2:])
        print(memory_mgr.add_memory(text))

    elif sub == "search":
        if len(parts) < 3:
            print("用法: /memory search <keyword>")
            return
        keyword = " ".join(parts[2:])
        print(memory_mgr.search_memory(keyword))

    elif sub == "forget":
        if len(parts) < 3:
            print("用法: /memory forget <keyword>")
            return
        keyword = " ".join(parts[2:])
        confirm = input(f"确认删除含 '{keyword}' 的记忆条目？(y/N): ").strip().lower()
        if confirm == "y":
            print(memory_mgr.forget_memory(keyword))
        else:
            print("已取消。")

    else:
        print("用法: /memory [show|add <text>|search <keyword>|forget <keyword>]")


def _handle_skills_command(parts: list[str], skills: dict) -> None:
    sub = parts[1] if len(parts) > 1 else "list"

    if sub == "list":
        print(skills_mgr.list_skills(skills))

    elif sub == "show":
        if len(parts) < 3:
            print("用法: /skills show <name>")
            return
        name = parts[2]
        print(skills_mgr.show_skill(name, skills))

    elif sub == "create":
        if len(parts) < 3:
            print("用法: /skills create <name>")
            return
        name = parts[2]
        desc = " ".join(parts[3:]) if len(parts) > 3 else ""
        result = skills_mgr.create_skill(name, description=desc)
        print(result)
        # 重新加载技能
        skills.clear()
        skills.update(skills_mgr.load_all_skills())

    else:
        print("用法: /skills [list|show <name>|create <name>]")


def _handle_feishu_command(parts: list[str]) -> None:
    if len(parts) < 2:
        print("用法: /feishu [send <id> <msg>|read <chat_id>]")
        return

    try:
        from src.feishu import client as feishu_client
        client = feishu_client.get_client()
    except RuntimeError as e:
        print(f"[飞书] {e}")
        return

    sub = parts[1]

    if sub == "send":
        if len(parts) < 4:
            print("用法: /feishu send <receive_id> <消息内容>")
            return
        receive_id = parts[2]
        text = " ".join(parts[3:])
        result = feishu_client.tool_feishu_send({
            "receive_id": receive_id,
            "text": text,
        })
        print(result)

    elif sub == "read":
        if len(parts) < 3:
            print("用法: /feishu read <chat_id>")
            return
        chat_id = parts[2]
        result = feishu_client.tool_feishu_read({
            "container_id": chat_id,
            "page_size": 10,
        })
        print(result)

    else:
        print("用法: /feishu [send <id> <msg>|read <chat_id>]")


def _handle_update_command(parts: list[str], agent: Agent) -> None:
    from src.selfupdate import updater

    if len(parts) < 2:
        print("用法: /update <需求描述> 或 /update rollback 或 /update list")
        return

    sub = parts[1]

    if sub == "rollback":
        print(updater.run_rollback())

    elif sub == "list":
        print(updater.list_update_branches())

    else:
        description = " ".join(parts[1:])
        result = updater.run_update(description, agent.llm)
        print(result)


def _handle_serve_command(config: dict[str, Any], agent: Agent) -> None:
    """启动飞书长连接消息监听服务（阻塞）。"""
    feishu_cfg = config.get("feishu", {})
    app_id = feishu_cfg.get("app_id", "").strip()
    app_secret = feishu_cfg.get("app_secret", "").strip()

    if not app_id or not app_secret:
        print("[serve] 飞书未配置，请在 ~/.lampson/config.yaml 中填写 feishu.app_id 和 feishu.app_secret。")
        return

    try:
        from src.feishu.listener import FeishuListener
    except ImportError as e:
        print(f"[serve] 导入飞书监听模块失败：{e}")
        return

    from src.core.compaction import CompactionConfig

    compaction_cfg = _build_compaction_config(config)
    enabled = config.get("compaction", {}).get("enabled", True)
    if enabled and compaction_cfg:
        listener = FeishuListener(app_id=app_id, app_secret=app_secret, agent=agent, compaction_config=compaction_cfg)
    else:
        listener = FeishuListener(app_id=app_id, app_secret=app_secret, agent=agent)
    listener.start()


def _handle_command(
    cmd: str,
    config: dict[str, Any],
    agent: Agent,
    skills: dict,
) -> bool:
    """处理 / 开头的命令，返回 True 表示继续，False 表示退出。"""
    parts = cmd.strip().split()
    if not parts:
        return True
    command = parts[0].lower()

    if command == "/exit":
        return False

    elif command == "/help":
        print(HELP_TEXT)

    elif command == "/config":
        import yaml
        safe_config = dict(config)
        llm_cfg = safe_config.get("llm", {})
        if llm_cfg.get("api_key"):
            safe_config["llm"] = dict(llm_cfg)
            key = safe_config["llm"]["api_key"]
            safe_config["llm"]["api_key"] = key[:6] + "..." + key[-4:] if len(key) > 10 else "***"
        feishu_cfg = safe_config.get("feishu", {})
        if feishu_cfg.get("app_secret"):
            safe_config["feishu"] = dict(feishu_cfg)
            safe_config["feishu"]["app_secret"] = "***"
        print(yaml.dump(safe_config, allow_unicode=True, default_flow_style=False))

    elif command == "/memory":
        _handle_memory_command(parts)

    elif command == "/skills":
        _handle_skills_command(parts, skills)

    elif command == "/feishu":
        _handle_feishu_command(parts)

    elif command == "/update":
        _handle_update_command(parts, agent)

    elif command == "/serve":
        _handle_serve_command(config, agent)

    else:
        print(f"未知命令：{command}，输入 /help 查看帮助。")

    return True


def _save_session_summary(agent: Agent) -> None:
    """让 LLM 生成会话摘要并写入 sessions/。"""
    summary = agent.generate_session_summary()
    if summary.strip():
        memory_mgr.save_session_summary(summary)


def _build_compaction_config(config: dict[str, Any]) -> CompactionConfig | None:
    """从配置字典构建 CompactionConfig。"""
    c = config.get("compaction", {})
    if not c:
        return CompactionConfig()  # 使用默认配置
    return CompactionConfig(
        trigger_threshold=c.get("trigger_threshold", 0.8),
        end_threshold=c.get("end_threshold", 0.3),
        context_window=c.get("context_window", 131072),
        max_iterations=c.get("max_iterations", 3),
        enable_archive=c.get("enable_archive", True),
    )


def _parse_args() -> tuple[str | None, bool]:
    """解析命令行参数，返回 (non_interactive_input, is_slash_command)。

    返回 None 表示进入交互式 REPL 模式。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="lampson",
        description="Lampson CLI 智能助手",
        add_help=True,
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="直接对话内容（单条查询模式）",
    )
    parser.add_argument(
        "-c",
        dest="query_c",
        default=None,
        metavar="QUERY",
        help="直接对话内容（显式指定）",
    )
    parser.add_argument(
        "--memory",
        nargs="+",
        metavar="SUBCMD",
        help="执行 memory 子命令，如 --memory show",
    )
    parser.add_argument(
        "--skills",
        nargs="+",
        metavar="SUBCMD",
        help="执行 skills 子命令，如 --skills list, --skills show <name>, --skills create <name> [desc]",
    )
    parser.add_argument(
        "--feishu",
        nargs="+",
        metavar="SUBCMD",
        help="执行 feishu 子命令，如 --feishu send <id> <msg>, --feishu read <chat_id>",
    )
    parser.add_argument(
        "--update",
        nargs="+",
        metavar="SUBCMD",
        help="执行 update 子命令，如 --update rollback, --update list, --update <需求描述>",
    )
    parser.add_argument(
        "--config",
        action="store_true",
        default=False,
        help="显示当前配置",
    )
    parser.add_argument(
        "--help-cmd",
        action="store_true",
        default=False,
        dest="help_cmd",
        help="显示可用命令帮助（不覆盖 -h/--help）",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="启动飞书消息监听服务（长连接 WebSocket 模式）",
    )

    args = parser.parse_args()

    if args.help_cmd:
        return "/help", True

    if args.config:
        return "/config", True

    if args.memory:
        cmd = "/memory " + " ".join(args.memory)
        return cmd, True

    if args.skills:
        cmd = "/skills " + " ".join(args.skills)
        return cmd, True

    if args.feishu:
        cmd = "/feishu " + " ".join(args.feishu)
        return cmd, True

    if args.update:
        cmd = "/update " + " ".join(args.update)
        return cmd, True

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


def _run_non_interactive(user_input: str, is_slash_command: bool) -> None:
    """非交互模式：初始化 agent，执行单次输入后退出。"""
    config = load_config()

    if not is_config_complete(config):
        print("Lampson 未配置，请先运行 lampson 进入交互模式完成配置。")
        sys.exit(1)

    _install_default_skills()

    core_memory = memory_mgr.load_core()
    skills = skills_mgr.load_all_skills()
    skills_context = skills_mgr.get_skills_summary(skills)

    native_tool_calling = config.get("llm", {}).get("native_tool_calling", True)
    llm = LLMClient(
        api_key=config["llm"]["api_key"],
        base_url=config["llm"]["base_url"],
        model=config["llm"]["model"],
        supports_native_tool_calling=native_tool_calling,
    )
    agent = Agent(llm)
    agent.set_context(core_memory=core_memory)
    agent.skills = skills

    _init_feishu(config)

    if is_slash_command:
        _handle_command(user_input, config, agent, skills)
    else:
        try:
            response = agent.run(user_input)
            print(response)
        except Exception as e:
            print(f"[错误] {e}")
            sys.exit(1)


def main() -> None:
    """程序入口：初始化配置、创建 Agent，进入 REPL 或非交互模式。"""
    non_interactive_input, is_slash_command = _parse_args()

    if non_interactive_input is not None:
        _run_non_interactive(non_interactive_input, is_slash_command)
        return

    # 以下为原有交互式 REPL 逻辑
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

    # 安装内置技能
    _install_default_skills()

    # 加载记忆和技能
    core_memory = memory_mgr.load_core()
    skills = skills_mgr.load_all_skills()
    skills_context = skills_mgr.get_skills_summary(skills)

    # 初始化 LLM 和 Agent
    native_tool_calling = config.get("llm", {}).get("native_tool_calling", True)
    llm = LLMClient(
        api_key=config["llm"]["api_key"],
        base_url=config["llm"]["base_url"],
        model=config["llm"]["model"],
        supports_native_tool_calling=native_tool_calling,
    )
    agent = Agent(llm)
    agent.set_context(core_memory=core_memory)
    agent.skills = skills

    # 初始化飞书（可选）
    feishu_ok = _init_feishu(config)

    history_file = LAMPSON_DIR / ".repl_history"
    session: PromptSession = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        style=PROMPT_STYLE,
    )

    skill_count = len(skills)
    feishu_status = "已连接" if feishu_ok else "未配置"
    print(f"Lampson 已启动（技能: {skill_count} 个，飞书: {feishu_status}）。输入 /help 查看命令，Ctrl+C 或 /exit 退出。\n")

    try:
        while True:
            try:
                user_input = session.prompt(
                    [("class:prompt", "you> ")],
                ).strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                should_continue = _handle_command(user_input, config, agent, skills)
                if not should_continue:
                    break
            else:
                try:
                    response = agent.run(user_input)
                    print(f"\nLampson> {response}\n")
                except Exception as e:
                    print(f"\n[错误] {e}\n")

                # 上下文压缩检查
                compaction_cfg = _build_compaction_config(config)
                if compaction_cfg and config.get("compaction", {}).get("enabled", True):
                    try:
                        cr = apply_compaction(agent.llm, compaction_cfg, agent.last_total_tokens, agent.last_stop_reason)
                        if cr is not None:
                            if cr.success:
                                print(f"[上下文压缩] 已完成，归档 {cr.archived_count} 条内容。")
                            else:
                                print(f"[上下文压缩] 失败: {cr.error}")
                    except Exception:
                        pass  # 压缩失败不影响正常对话

    finally:
        print("\n正在保存会话摘要...")
        try:
            _save_session_summary(agent)
        except Exception:
            pass
        print("再见！")


if __name__ == "__main__":
    main()
