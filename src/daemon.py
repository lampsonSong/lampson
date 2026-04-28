"""Lampson Daemon 主进程。

职责：
1. 加载配置、初始化 SessionManager
2. 启动飞书 WebSocket 长连接监听
3. 启动后写 boot_task，让 LLM 通过 system prompt 通知 owner
4. 检查并执行 boot_tasks（重启前指定的待办）
5. 主线程阻塞（signal 驱动优雅退出）
6. 退出时先 join 监听线程，再保存 session

CLI（src.cli）为独立人机交互入口，不承载飞书常驻监听。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
from pathlib import Path

from src.core.config import load_config, is_config_complete
from src.core.session_manager import get_session_manager

LAMPSON_DIR = Path.home() / ".lampson"
_BOOT_TASKS_PATH = LAMPSON_DIR / "boot_tasks.json"
_shutdown = threading.Event()

# boot_tasks 限制
_MAX_TASKS = 20
_MAX_TOTAL_BYTES = 10 * 1024  # 10KB


def _signal_handler(signum: int, _frame: object | None) -> None:
    print(f"\n[daemon] 收到信号 {signum}，准备退出...", flush=True)
    _shutdown.set()


def _send_boot_notification(config: dict, pid: int) -> None:
    """常驻上线通知：直接调飞书 HTTP API 发消息，不走 LLM。"""
    owner_chat_id = config.get("feishu", {}).get("owner_chat_id", "").strip()
    app_id = config.get("feishu", {}).get("app_id", "").strip()
    app_secret = config.get("feishu", {}).get("app_secret", "").strip()

    if not owner_chat_id:
        print("[daemon] 未配置 feishu.owner_chat_id，跳过上线通知", flush=True)
        return
    if not app_id or not app_secret:
        print("[daemon] 飞书凭证未配置，跳过上线通知", flush=True)
        return

    try:
        from src.feishu.client import FeishuClient
        client = FeishuClient(app_id=app_id, app_secret=app_secret)
        text = f"Lampson 已上线 (PID={pid})"

        for attempt in range(2):
            try:
                client.send_message(
                    receive_id=owner_chat_id,
                    text=text,
                    receive_id_type="chat_id",
                )
                print(f"[daemon] 上线通知已发送", flush=True)
                return
            except Exception as e:
                if attempt == 0:
                    print(f"[daemon] 上线通知发送失败，重试: {e}", flush=True)
                else:
                    print(f"[daemon] 上线通知发送失败: {e}", flush=True)
    except Exception as e:
        print(f"[daemon] 上线通知异常: {e}", flush=True)


def _write_boot_task(task: dict) -> None:
    """追加一条 boot_task 到 boot_tasks.json（原子写入）。"""
    tasks = []
    if _BOOT_TASKS_PATH.exists():
        try:
            raw = _BOOT_TASKS_PATH.read_text(encoding="utf-8").strip()
            if raw:
                tasks = json.loads(raw)
        except Exception:
            tasks = []
    tasks.append(task)
    # 原子写入
    fd, tmp = tempfile.mkstemp(dir=str(_BOOT_TASKS_PATH.parent), prefix=".boot_tasks_")
    with os.fdopen(fd, "w") as f:
        json.dump(tasks, f, ensure_ascii=False)
    os.replace(tmp, str(_BOOT_TASKS_PATH))


def _load_and_clear_boot_tasks() -> list[dict] | None:
    """读取 boot_tasks.json，清空文件，返回任务列表。

    原子清空：先写临时文件再 os.replace。
    JSON 损坏时备份为 .bad，返回 None。
    """
    tasks_path = _BOOT_TASKS_PATH
    if not tasks_path.exists():
        return None

    raw = tasks_path.read_text(encoding="utf-8").strip()
    if not raw or raw == "[]":
        return None

    try:
        tasks = json.loads(raw)
    except json.JSONDecodeError:
        bad_path = tasks_path.with_suffix(".json.bad")
        shutil.move(str(tasks_path), str(bad_path))
        print(f"[daemon] boot_tasks.json 损坏，已备份到 {bad_path.name}", flush=True)
        return None

    if not isinstance(tasks, list) or not tasks:
        return None

    # 上限检查
    if len(tasks) > _MAX_TASKS:
        print(f"[daemon] boot_tasks 共 {len(tasks)} 条，截断为 {_MAX_TASKS} 条", flush=True)
        tasks = tasks[:_MAX_TASKS]

    total = len(json.dumps(tasks, ensure_ascii=False).encode("utf-8"))
    if total > _MAX_TOTAL_BYTES:
        print(f"[daemon] boot_tasks 总长 {total}B 超过 {_MAX_TOTAL_BYTES}B，截断", flush=True)
        while tasks and len(json.dumps(tasks, ensure_ascii=False).encode("utf-8")) > _MAX_TOTAL_BYTES:
            tasks.pop()

    # 原子清空
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(tasks_path.parent), prefix=".boot_tasks_")
        with os.fdopen(fd, "w") as f:
            f.write("[]")
        os.replace(tmp_path, str(tasks_path))
    except Exception as e:
        print(f"[daemon] 清空 boot_tasks.json 失败: {e}", flush=True)

    return tasks


def _inject_boot_tasks(session, tasks: list[dict]) -> None:
    """将 boot_tasks 注入 session 并主动执行一轮 agent。"""
    lines = ["[系统] 你刚完成重启，有以下待办任务需要执行："]
    for i, t in enumerate(tasks, 1):
        desc = t.get("task", str(t))
        lines.append(f"{i}. {desc}")
    lines.append("请逐一通过飞书通知 owner。")

    prompt = "\n".join(lines)
    try:
        result = session.handle_input(prompt)
        if result.reply:
            print(f"[daemon] boot_tasks 执行完成: {result.reply[:100]}", flush=True)
    except Exception as e:
        print(f"[daemon] boot_tasks 执行失败: {e}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.daemon",
        description=(
            "Lampson 常驻 daemon：加载配置、监听飞书长连接。"
            " 通常由 macOS launchd 拉起。"
        ),
    )
    parser.parse_args()

    config = load_config()
    if not is_config_complete(config):
        print("[daemon] 配置不完整，无法启动。", file=sys.stderr)
        sys.exit(1)

    mgr = get_session_manager(config)
    session = mgr.get_or_create("cli", "default")

    try:
        session.start_feishu_listener()
    except Exception as e:
        print(f"[daemon] 飞书监听启动失败: {e}", file=sys.stderr)
        sys.exit(1)

    pid = os.getpid()
    print(f"[daemon] Lampson daemon 已启动 (PID={pid})", flush=True)

    # 常驻上线通知：daemon 直接调 HTTP API 发送，不走 LLM
    _send_boot_notification(config, pid)

    # 检查并执行 boot_tasks
    tasks = _load_and_clear_boot_tasks()
    if tasks:
        print(f"[daemon] 发现 {len(tasks)} 条 boot_tasks，开始执行", flush=True)
        _inject_boot_tasks(session, tasks)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    while not _shutdown.is_set():
        signal.pause()

    listener = getattr(session, "_feishu_listener", None)
    if listener is not None:
        try:
            listener.shutdown()
        except Exception as e:
            print(f"[daemon] 停止飞书监听时出错: {e}", flush=True)

    try:
        mgr.close_all()
    except Exception as e:
        print(f"[daemon] 保存会话时出错: {e}", flush=True)
    print("[daemon] 已退出。", flush=True)


if __name__ == "__main__":
    main()
