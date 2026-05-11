"""Lamix Daemon 主进程。

职责：
1. 加载配置、初始化 SessionManager
2. 启动多平台消息网关（PlatformManager）
3. 启动心跳（HeartbeatManager）
4. 启动任务调度器（TaskScheduler）：自我审计
5. 启动后执行 boot_tasks（重启前指定的待办）
6. 主线程阻塞（signal 驱动优雅退出）
7. 退出时保存 session
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from pathlib import Path

from src.core.config import load_config, is_config_complete, LAMIX_DIR
from src.core.heartbeat import HeartbeatManager
from src.core.session_manager import get_session_manager
from src.core.self_audit import (
    run_audit,
    format_report_detail,
    DEFAULT_AUDIT_HOUR,
    DEFAULT_AUDIT_MINUTE,
    _audit_log,
)
from src.core.task_scheduler import TaskScheduler, TaskType, TaskConfig, schedule, start as scheduler_start, shutdown as scheduler_shutdown
from src.core.tools import load_learned_modules

LOG_DIR = LAMIX_DIR / "logs"
_BOOT_TASKS_PATH = LAMIX_DIR / "boot_tasks.json"
_DAEMON_PID_PATH = LOG_DIR / "daemon.pid"
_shutdown = threading.Event()
_heartbeat_mgr: HeartbeatManager | None = None
_scheduler: TaskScheduler | None = None
SAFE_MODE_SCRIPT = Path(__file__).resolve().parent / "safe_mode.py"
DAEMON_ENTRY = f"{sys.executable} -m src.daemon"

# boot_tasks 限制
_MAX_TASKS = 20
_MAX_TOTAL_BYTES = 10 * 1024  # 10KB



def _notify_user(text: str, config: dict | None = None) -> None:
    """通过用户当前渠道发送通知。

    优先使用 session 的 partial_sender（自动匹配渠道），
    fallback 到 print（CLI 模式或无 session 时）。
    """
    # 优先走 session partial_sender
    try:
        from src.tools import session as session_tool
        current_session = session_tool.get_current_session()
        if current_session and current_session.partial_sender:
            current_session.partial_sender(text)
            return
    except Exception:
        pass
    # Fallback: daemon 启动早期可能没有 session，直接 print
    print(f"[daemon] {text}", flush=True)


# ── 任务回调 ────────────────────────────────────────────────────────────────


def _self_audit_callback() -> None:
    """审计任务：根据用户活跃度判断是否触发。

    触发逻辑：
    - 用户 24 小时没有使用 → 触发审计
    - 用户有使用 → 等最后一次使用 1 小时后触发
    - 发过一次审计后，重新开始计时
    """
    from datetime import datetime, timedelta
    from src.core.heartbeat import get_last_activity_time

    now = datetime.now()
    last_activity = get_last_activity_time()
    last_audit_file = LAMIX_DIR / "logs" / ".last_audit_time"

    # 读取上次审计时间
    last_audit_time = None
    try:
        if last_audit_file.exists():
            ts = last_audit_file.read_text().strip()
            last_audit_time = datetime.fromisoformat(ts)
    except Exception:
        pass

    # 判断是否应该触发
    should_run = False
    reason = ""

    if last_activity is None:
        # 没有活动记录（刚启动），跳过
        return
    elif now - last_activity >= timedelta(hours=24):
        # 24 小时没有使用
        if last_audit_time and now - last_audit_time < timedelta(hours=24):
            return  # 24小时内已审计过，跳过
        should_run = True
        reason = "用户24小时未使用"
    elif now - last_activity >= timedelta(hours=1):
        # 有使用，但最后使用已过 1 小时
        if last_audit_time and last_audit_time > last_activity:
            return  # 上次审计在最后活动之后，跳过
        should_run = True
        reason = "用户最后使用后1小时"

    if not should_run:
        return

    try:
        report = run_audit()
        report_content = format_report_detail(report)
        if len(report_content) > 4000:
            report_content = report_content[:4000] + "\n\n...（报告过长已截断）"
        _audit_log(f"[self_audit] 审计完成（{reason}），开始发送报告")
        _notify_user(f"🕐 Lamix 自我审计报告\n\n{report_content}")
        print(f"[self_audit] 审计完成（{reason}）", flush=True)
        # 记录审计时间
        last_audit_file.parent.mkdir(parents=True, exist_ok=True)
        last_audit_file.write_text(now.isoformat(), encoding="utf-8")
    except Exception as e:
        print(f"[self_audit] 执行失败: {e}", flush=True)


def _register_tasks(session=None) -> None:
    """注册所有定时任务。"""
    global _scheduler
    _scheduler = TaskScheduler()
    if session is not None:
        from src.core.task_scheduler import set_session
        set_session(session)
    scheduler_start()

    # 自我审计：每 4 小时检查一次是否需要执行审计
    # 审计触发逻辑在 _self_audit_callback 内部判断：
    #   - 用户 24 小时没有使用 → 触发审计
    #   - 用户有使用 → 等最后一次使用 1 小时后触发
    schedule(TaskConfig(
        task_id="self_audit_check",
        task_type=TaskType.INTERVAL,
        interval_seconds=14400,  # 4 小时检查一次
        func=_self_audit_callback,
        description="审计检查（每4小时）",
    ))

    print("[daemon] 任务调度器已启动（审计检查）", flush=True)


# ── 信号与退出 ───────────────────────────────────────────────────────────────


def _signal_handler(signum: int, _frame: object | None) -> None:
    print(f"\n[daemon] 收到信号 {signum}，准备退出...", flush=True)
    _shutdown.set()


def _write_daemon_pid() -> None:
    """写 daemon pid 到文件，供 watchdog 查找。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _DAEMON_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")


def _send_boot_notification(config: dict, pid: int) -> None:
    """常驻上线通知：优先发 open_id 私聊，失败则发 owner_chat_id 群聊。"""
    owner_chat_id = config.get("feishu", {}).get("owner_chat_id", "").strip()
    user_open_id = config.get("feishu", {}).get("user_open_id", "").strip()
    app_id = config.get("feishu", {}).get("app_id", "").strip()
    app_secret = config.get("feishu", {}).get("app_secret", "").strip()

    if not app_id or not app_secret:
        print("[daemon] 飞书凭证未配置，跳过上线通知", flush=True)
        return

    try:
        from src.feishu.client import FeishuClient
        client = FeishuClient(app_id=app_id, app_secret=app_secret)
        text = f"Lamix 已上线 (PID={pid})"

        # 优先尝试 user_open_id 私聊（一定可以发），再试 owner_chat_id 群聊
        targets = []
        if user_open_id:
            targets.append((user_open_id, "open_id"))
        if owner_chat_id:
            targets.append((owner_chat_id, "chat_id"))

        if not targets:
            print("[daemon] 未配置 feishu.user_open_id 或 feishu.owner_chat_id，跳过上线通知", flush=True)
            return

        for receive_id, receive_id_type in targets:
            for attempt in range(2):
                try:
                    client.send_message(
                        receive_id=receive_id,
                        text=text,
                        receive_id_type=receive_id_type,
                    )
                    print(f"[daemon] 上线通知已发送 (via {receive_id_type})", flush=True)
                    return
                except Exception as e:
                    if attempt == 0:
                        print(f"[daemon] 上线通知发送失败（{receive_id_type}），重试: {e}", flush=True)
                    else:
                        print(f"[daemon] 上线通知发送失败（{receive_id_type}）: {e}", flush=True)
    except Exception as e:
        print(f"[daemon] 上线通知异常: {e}", flush=True)


def _notify_boot_tasks_running(config: dict, tasks: list[dict]) -> None:
    """boot_tasks 执行前发飞书提示。"""
    lines = [f"⚡ 正在执行 {len(tasks)} 条启动待办任务："]
    for i, t in enumerate(tasks, 1):
        desc = t.get("task", str(t))
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"{i}. {desc}")

    message = "\n".join(lines)

    # 优先直接用飞书 API 发送（解决 daemon 启动早期没有 session 的问题）
    feishu_cfg = (config or {}).get("feishu", {}) or {}
    owner_chat_id = feishu_cfg.get("owner_chat_id", "").strip()
    user_open_id = feishu_cfg.get("user_open_id", "").strip()
    app_id = feishu_cfg.get("app_id", "").strip()
    app_secret = feishu_cfg.get("app_secret", "").strip()

    if app_id and app_secret:
        try:
            from src.feishu.client import FeishuClient
            client = FeishuClient(app_id=app_id, app_secret=app_secret)
            # 优先 open_id 私聊
            sent = False
            for receive_id, receive_id_type in [
                (user_open_id, "open_id"),
                (owner_chat_id, "chat_id"),
            ]:
                if not receive_id:
                    continue
                try:
                    client.send_message(
                        receive_id=receive_id,
                        text=message,
                        receive_id_type=receive_id_type,
                    )
                    print(f"[daemon] boot_tasks 运行提示已发送到飞书 (via {receive_id_type})", flush=True)
                    sent = True
                    break
                except Exception:
                    continue
            if sent:
                return
        except Exception as e:
            print(f"[daemon] 飞书发送失败，fallback 到 _notify_user: {e}", flush=True)

    # Fallback: session 可能已经存在的情况
    _notify_user(message)


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
    fd, tmp = tempfile.mkstemp(dir=str(_BOOT_TASKS_PATH.parent), prefix=".boot_tasks_")
    with os.fdopen(fd, "w") as f:
        json.dump(tasks, f, ensure_ascii=False)
    os.replace(tmp, str(_BOOT_TASKS_PATH))


def _load_and_clear_boot_tasks() -> list[dict] | None:
    """读取 boot_tasks.json，清空文件，返回任务列表。"""
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

    if len(tasks) > _MAX_TASKS:
        print(f"[daemon] boot_tasks 共 {len(tasks)} 条，截断为 {_MAX_TASKS} 条", flush=True)
        tasks = tasks[:_MAX_TASKS]

    total = len(json.dumps(tasks, ensure_ascii=False).encode("utf-8"))
    if total > _MAX_TOTAL_BYTES:
        print(f"[daemon] boot_tasks 总长 {total}B 超过 {_MAX_TOTAL_BYTES}B，截断", flush=True)
        while tasks and len(json.dumps(tasks, ensure_ascii=False).encode("utf-8")) > _MAX_TOTAL_BYTES:
            tasks.pop()

    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(tasks_path.parent), prefix=".boot_tasks_")
        with os.fdopen(fd, "w") as f:
            f.write("[]")
        os.replace(tmp_path, str(tasks_path))
    except Exception as e:
        print(f"[daemon] 清空 boot_tasks.json 失败: {e}", flush=True)

    return tasks



def _get_boot_tasks_session(mgr, config: dict):
    """获取用于执行 boot_tasks 的 session。

    优先使用飞书 owner session（保证 resume 等上下文在飞书渠道可见），
    如果未配置 user_open_id 则 fallback 到 CLI session。
    """
    owner_open_id = config.get("feishu", {}).get("user_open_id", "").strip()
    if owner_open_id:
        session = mgr.get_or_create("feishu", owner_open_id)
        print(f"[daemon] boot_tasks 将在飞书 session 上执行 (owner_open_id={owner_open_id})", flush=True)
        return session

    print("[daemon] 未配置 feishu.user_open_id，boot_tasks 将在 CLI session 上执行", flush=True)
    return mgr.get_or_create("cli", "default")


def _inject_boot_tasks(session, tasks: list[dict], config: dict | None = None) -> None:
    """将 boot_tasks 注入 session 并主动执行一轮 agent。"""
    _feishu_sender = None
    _progress_queue: queue.Queue[dict[str, Any]] | None = None

    # 飞书 session 需要 partial_sender 才能把回复发出去
    if config and session.channel == "feishu":
        owner_chat_id = config.get("feishu", {}).get("owner_chat_id", "").strip()
        app_id = config.get("feishu", {}).get("app_id", "").strip()
        app_secret = config.get("feishu", {}).get("app_secret", "").strip()
        if owner_chat_id and app_id and app_secret:
            from src.feishu.client import FeishuClient
            client = FeishuClient(app_id=app_id, app_secret=app_secret)
            _feishu_sender = lambda t: client.send_message(
                receive_id=owner_chat_id, text=t, receive_id_type="chat_id"
            )
            session.partial_sender = _feishu_sender
            session._reply_callback = _feishu_sender

            # ─── 进度卡片 worker（通过 adapter 抽象层，支持多渠道扩展）───
            from src.platforms.adapters.feishu import FeishuAdapter
            _progress_adapter = FeishuAdapter({
                "app_id": app_id,
                "app_secret": app_secret,
            })
            _progress_queue = queue.Queue()
            _progress_done = threading.Event()

            def _progress_worker() -> None:
                progress_lines: list[str] = []
                last_update_ts = 0.0
                update_interval = 1.5
                progress_msg_id: str | None = None
                _fail_count = 0
                while True:
                    try:
                        event = _progress_queue.get(timeout=0.5)  # type: ignore
                    except queue.Empty:
                        if _progress_done.is_set():
                            if progress_lines and _fail_count < 3:
                                try:
                                    if progress_msg_id is None:
                                        progress_msg_id = _progress_adapter._send_progress_card(owner_chat_id, progress_lines, finished=True)
                                    else:
                                        _progress_adapter._update_progress_card(progress_msg_id, progress_lines, finished=True)
                                except Exception:
                                    pass
                            return
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "model_switch":
                        progress_lines.append(f"**[模型切换]** {event.get('message', '')}")
                    elif event.get("type") == "tool_progress":
                        round_n = event["round"]
                        tool = event["tool"]
                        args_p = event["args_preview"]
                        result_p = event["result_preview"]
                        is_error = result_p.startswith("[错误]") or result_p.startswith("[网络错误]")
                        icon = "x" if is_error else ">"
                        progress_lines.append(f"**{round_n}.** `{tool}`({args_p})\n  {icon} {result_p}")
                    now = time.monotonic()
                    if now - last_update_ts >= update_interval and _fail_count < 3 and progress_lines:
                        try:
                            if progress_msg_id is None:
                                progress_msg_id = _progress_adapter._send_progress_card(owner_chat_id, progress_lines)
                                if progress_msg_id is None:
                                    _fail_count += 1
                            else:
                                _progress_adapter._update_progress_card(progress_msg_id, progress_lines)
                        except Exception:
                            _fail_count += 1
                        last_update_ts = time.monotonic()

            _pw = threading.Thread(target=_progress_worker, daemon=True, name="boot-progress")
            _pw.start()

            def _progress_cb(event: dict) -> None:
                _progress_queue.put(event)  # type: ignore

            session.agent.progress_callback = _progress_cb
            session.agent.interim_sender = _feishu_sender

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
        else:
            # 空结果也通知用户
            msg = "boot_tasks 执行完成但返回为空，可能 context 过长导致 LLM 无法响应。"
            print(f"[daemon] {msg}", flush=True)
            if _feishu_sender:
                try:
                    _feishu_sender(msg)
                except Exception:
                    pass
    except Exception as e:
        err_msg = f"boot_tasks 执行失败: {e}"
        print(f"[daemon] {err_msg}", flush=True)
        # 失败时通知用户
        if _feishu_sender:
            try:
                _feishu_sender(f"⚠️ 启动待办任务执行失败：{e}")
            except Exception:
                pass
    finally:
        # 清理进度回调
        if _progress_queue is not None:
            _progress_done.set()  # type: ignore
            try:
                _pw.join(timeout=3.0)  # type: ignore
            except Exception:
                pass
            session.agent.progress_callback = None
            session.agent.interim_sender = None

def _patch_websockets_ssl() -> None:
    """Monkey-patch websockets.connect 使用 certifi CA 证书。

    macOS launchd / Windows 环境下 Python 默认 SSL context 可能缺少中间 CA
    （尤其有 VPN/代理时），导致飞书 WebSocket 长连接 SSL 握手失败。
    用 certifi 的 CA bundle 更可靠。
    """
    try:
        import ssl
        import certifi
        import websockets

        _original_connect = websockets.connect

        async def _patched_connect(*args, **kwargs):
            if "ssl" not in kwargs:
                ctx = ssl.create_default_context(cafile=certifi.where())
                kwargs["ssl"] = ctx
            return await _original_connect(*args, **kwargs)

        # 保留原始签名属性，避免 websockets 内部检查报错
        _patched_connect.__wrapped__ = _original_connect  # type: ignore[attr-defined]
        websockets.connect = _patched_connect
        print("[daemon] websockets SSL patch 已应用 (certifi CA)", flush=True)
    except ImportError:
        print("[daemon] certifi 未安装，跳过 websockets SSL patch", flush=True)
    except Exception as e:
        print(f"[daemon] websockets SSL patch 失败: {e}", flush=True)


def main() -> None:
    global _heartbeat_mgr, _scheduler
    # 强制 stdout/stderr 行缓冲：文件重定向时默认全缓冲，
    # 会导致日志丢失（进程崩溃时缓冲区内容不刷盘）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)

    # 修复飞书 WebSocket SSL 证书验证失败（macOS launchd / Windows 均可能出现）
    _patch_websockets_ssl()

    parser = argparse.ArgumentParser(
        prog="python -m src.daemon",
        description="Lamix 常驻 daemon：多平台消息网关 + 飞书 WebSocket 长连接监听。",
    )
    parser.parse_args()

    config = load_config()
    if not is_config_complete(config):
        print("[daemon] LLM 未配置，请运行 lamix cli 完成初始配置后重启 daemon。", flush=True)
        _write_daemon_pid()
        _heartbeat_mgr = HeartbeatManager(task_id="daemon")
        _heartbeat_mgr.start()
        _shutdown.wait()
        _heartbeat_mgr.stop(user_initiated=True)
        return

    # ── 初始化 SessionManager ──────────────────────────────────────────────
    mgr = get_session_manager(config)
    session = mgr.get_or_create("cli", "default")

    # ── 初始化并启动 PlatformManager ──────────────────────────────────────
    from src.platforms.manager import PlatformManager
    from src.platforms.adapters.feishu import FeishuAdapter

    pm = PlatformManager(config)
    PlatformManager._instance = pm

    feishu_cfg = config.get("feishu", {})
    if feishu_cfg.get("app_id") and feishu_cfg.get("app_secret"):
        feishu_adapter = FeishuAdapter({
            "app_id": feishu_cfg["app_id"],
            "app_secret": feishu_cfg["app_secret"],
        })
        feishu_adapter.safe_mode_callback = lambda: _trigger_safe_mode(pm, mgr)
        feishu_adapter._shutdown_callback = lambda: _shutdown.set()
        pm.register(feishu_adapter)
        feishu_adapter.start()
        print("[daemon] 飞书 adapter 已启动", flush=True)

    # ── 写 pid ───────────────────────────────────────────────────────────
    pid = os.getpid()
    _write_daemon_pid()
    print(f"[daemon] Lamix daemon 已启动 (PID={pid})", flush=True)

    # ── 启动心跳 ──────────────────────────────────────────────────────────
    _heartbeat_mgr = HeartbeatManager(task_id="daemon")
    _heartbeat_mgr.start()
    print("[daemon] 心跳已启动", flush=True)

    # ── 任务调度器（自我审计）──────────────────────────────────────────
    _register_tasks(session)

    # ── 加载 learned_modules（延迟，避免循环导入）──────────────────────
    load_learned_modules()
    print("[daemon] learned_modules 已加载", flush=True)

    # ── 上线通知 ─────────────────────────────────────────────────────────
    _send_boot_notification(config, pid)

    # ── boot_tasks ──────────────────────────────────────────────────────
    tasks = _load_and_clear_boot_tasks()
    if tasks:
        print(f"[daemon] 发现 {len(tasks)} 条 boot_tasks，开始执行", flush=True)
        # 飞书提示用户有 boot task 正在执行
        _notify_boot_tasks_running(config, tasks)
        boot_session = _get_boot_tasks_session(mgr, config)
        _inject_boot_tasks(boot_session, tasks, config=config)

    # ── Config 热重载检测 ────────────────────────────────────────────────
    _start_config_watcher(pm, config)

    # ── 主事件循环 ────────────────────────────────────────────────────────
    try:
        asyncio.run(pm.run())
    except KeyboardInterrupt:
        print("[daemon] 收到 KeyboardInterrupt", flush=True)

    # ── 优雅退出 ─────────────────────────────────────────────────────────
    if _heartbeat_mgr is not None:
        _heartbeat_mgr.stop(user_initiated=True)
        print("[daemon] 心跳已停止", flush=True)

    if _scheduler is not None:
        scheduler_shutdown()
        _scheduler = None
        print("[daemon] 任务调度器已停止", flush=True)

    try:
        mgr.close_all()
    except Exception as e:
        print(f"[daemon] 保存会话时出错: {e}", flush=True)

    if _DAEMON_PID_PATH.exists():
        try:
            _DAEMON_PID_PATH.unlink()
        except OSError:
            pass

    print("[daemon] 已退出。", flush=True)


# ── Safe Mode 切换 ─────────────────────────────────────────────────────────


def _get_feishu_credentials(config_path) -> tuple[str, str]:
    """从配置文件读取飞书凭证（app_id, app_secret），用于变更比较。"""
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        feishu_cfg = cfg.get("feishu", {}) or {}
        return (
            feishu_cfg.get("app_id", "").strip(),
            feishu_cfg.get("app_secret", "").strip(),
        )
    except Exception:
        return ("", "")


def _start_config_watcher(pm, config: dict) -> None:
    """启动 config.yaml 热重载检测线程。

    每 30 秒检查 config.yaml，只在飞书凭证（app_id/app_secret）变更时触发热重载。
    owner 身份等字段变更不触发（不需要重连 WS）。
    """
    from src.core.config import CONFIG_PATH

    def _watcher():
        last_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        last_creds = _get_feishu_credentials(CONFIG_PATH)
        while not _shutdown.is_set():
            _shutdown.wait(30)
            if _shutdown.is_set():
                break
            try:
                if not CONFIG_PATH.exists():
                    continue
                mtime = CONFIG_PATH.stat().st_mtime
                if mtime == last_mtime:
                    continue
                last_mtime = mtime
                # 只在飞书凭证变更时才触发热重载
                new_creds = _get_feishu_credentials(CONFIG_PATH)
                if new_creds == last_creds:
                    print("[daemon] 配置文件已变更，但飞书凭证未变，跳过热重载", flush=True)
                    continue
                last_creds = new_creds
                print("[daemon] 飞书凭证已变更，热重载飞书 adapter...", flush=True)
                _reload_feishu_adapter(pm)
            except Exception as e:
                print(f"[daemon] config watcher 异常: {e}", flush=True)

    t = threading.Thread(target=_watcher, daemon=True, name="config-watcher")
    t.start()


def _reload_feishu_adapter(pm) -> None:
    """热重载飞书 adapter：标记旧 adapter 为 stopped，创建新的替换。

    不关闭旧 WS client（lark SDK 内部用 asyncio.get_event_loop()，
    在新线程里 run_until_complete 会跟已有 running loop 冲突导致崩溃）。
    旧 adapter 被 _stopped=True 标记后不再处理新消息，自然被 GC 回收。
    """
    from src.core.config import load_config
    from src.platforms.adapters.feishu import FeishuAdapter

    # 1. 标记旧 adapter 为 stopped（不关闭 WS，避免 event loop 冲突）
    old_adapter = pm._adapters.get("feishu")
    if old_adapter is not None:
        try:
            old_adapter._stopped = True
            del pm._adapters["feishu"]
            print("[daemon] 旧飞书 adapter 已标记为 stopped", flush=True)
        except Exception as e:
            print(f"[daemon] 标记旧飞书 adapter 失败: {e}", flush=True)

    # 2. 读新配置
    new_config = load_config()
    feishu_cfg = new_config.get("feishu", {})
    app_id = feishu_cfg.get("app_id", "").strip()
    app_secret = feishu_cfg.get("app_secret", "").strip()

    if not app_id or not app_secret:
        print("[daemon] 新配置中无飞书凭证，不启动 adapter", flush=True)
        return

    # 3. 创建并启动新的
    try:
        new_adapter = FeishuAdapter({
            "app_id": app_id,
            "app_secret": app_secret,
        })
        mgr = get_session_manager(new_config)
        new_adapter.session_manager = mgr
        pm.register(new_adapter)
        new_adapter.start()
        print("[daemon] 新飞书 adapter 已启动，热重载完成", flush=True)
    except Exception as e:
        print(f"[daemon] 热重载飞书 adapter 失败: {e}", flush=True)


def _trigger_safe_mode(pm, mgr) -> None:
    """由 FeishuAdapter 触发：切换到 safe_mode 后再恢复 daemon。"""
    print("[daemon] 切换到 Safe Mode...", flush=True)

    if _heartbeat_mgr is not None:
        try:
            _heartbeat_mgr.stop(user_initiated=True)
            print("[daemon] 心跳已停止", flush=True)
        except Exception as e:
            print(f"[daemon] 停止心跳出错: {e}", flush=True)

    global _scheduler
    if _scheduler is not None:
        scheduler_shutdown()
        _scheduler = None
        print("[daemon] 任务调度器已停止", flush=True)

    # 停止所有 adapter
    import asyncio
    for adapter in list(pm._adapters.values()):
        try:
            asyncio.run(adapter.shutdown())
            print(f"[daemon] {adapter.platform} adapter 已关闭", flush=True)
        except Exception as e:
            print(f"[daemon] 关闭 {adapter.platform} 失败: {e}", flush=True)

    # 保存 session
    try:
        mgr.close_all()
        print("[daemon] Session 已保存", flush=True)
    except Exception as e:
        print(f"[daemon] 保存会话出错: {e}", flush=True)

    # 启动 safe_mode.py（独立进程，阻塞等待它结束）
    if SAFE_MODE_SCRIPT.exists():
        print(f"[daemon] 启动 safe_mode: {SAFE_MODE_SCRIPT}", flush=True)
        try:
            proc = subprocess.Popen(
                [sys.executable, str(SAFE_MODE_SCRIPT)],
                cwd=str(LAMIX_DIR.parent / "lamix"),
                stdout=open(LOG_DIR / "safe_mode.log", "a", encoding="utf-8"),
                stderr=open(LOG_DIR / "safe_mode.err.log", "a", encoding="utf-8"),
            )
            print(f"[daemon] safe_mode 进程已启动 (PID={proc.pid})", flush=True)
            proc.wait()
            print(f"[daemon] safe_mode 已退出 (code={proc.returncode})", flush=True)
        except Exception as e:
            print(f"[daemon] safe_mode 启动失败: {e}", flush=True)
    else:
        print(f"[daemon] safe_mode 脚本不存在: {SAFE_MODE_SCRIPT}", flush=True)

    # safe_mode 结束，恢复 daemon
    print("[daemon] Safe Mode 退出，重启 daemon...", flush=True)
    _restore_daemon(pm, mgr)


def _restore_daemon(pm, mgr) -> None:
    """safe_mode 结束后，重新初始化 adapter、调度器和心跳。"""
    from src.core.config import load_config

    config = load_config()
    if not is_config_complete(config):
        print("[daemon] 恢复失败：配置不完整", flush=True)
        _shutdown.set()
        return

    # 重建 session
    session = mgr.get_or_create("cli", "default")

    # 重新启动所有 adapter
    import asyncio
    feishu_cfg = config.get("feishu", {})
    if feishu_cfg.get("app_id") and feishu_cfg.get("app_secret"):
        from src.platforms.adapters.feishu import FeishuAdapter
        feishu_adapter = FeishuAdapter({
            "app_id": feishu_cfg["app_id"],
            "app_secret": feishu_cfg["app_secret"],
        })
        feishu_adapter.safe_mode_callback = lambda: _trigger_safe_mode(pm, mgr)
        feishu_adapter._shutdown_callback = lambda: _shutdown.set()
        feishu_adapter.session_manager = mgr
        pm.register(feishu_adapter)
        try:
            feishu_adapter.start()
            print("[daemon] 飞书 adapter 已恢复", flush=True)
        except Exception as e:
            print(f"[daemon] 恢复飞书 adapter 失败: {e}", flush=True)

    # 恢复心跳
    global _heartbeat_mgr, _scheduler
    _heartbeat_mgr = HeartbeatManager(task_id="daemon")
    _heartbeat_mgr.start()
    print("[daemon] 心跳已恢复", flush=True)

    # 恢复任务调度器
    _register_tasks(session)
    load_learned_modules()
    print("[daemon] learned_modules 已加载", flush=True)

    # 上线通知
    from src.feishu.client import FeishuClient
    owner_chat_id = config.get("feishu", {}).get("owner_chat_id", "").strip()
    app_id = config.get("feishu", {}).get("app_id", "").strip()
    app_secret = config.get("feishu", {}).get("app_secret", "").strip()
    if owner_chat_id and app_id and app_secret:
        try:
            client = FeishuClient(app_id=app_id, app_secret=app_secret)
            client.send_message(
                receive_id=owner_chat_id,
                text="✅ Safe Mode 已退出，主程序已恢复。",
                receive_id_type="chat_id",
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
