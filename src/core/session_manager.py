"""SessionManager：管理多个 Session 实例，按 channel+sender_id 路由。

设计文档：docs/PROJECT.md §2.1

路由规则：
  "cli"         → 全局唯一一个 Session（开发者专用）
  "feishu:{id}" → 每个 sender_id 一个独立 Session
  "telegram:{id}" → 每个 sender_id 一个独立 Session
  "discord:{id}"  → 每个 sender_id 一个独立 Session

核心接口：
  session_manager.get_or_create(channel, sender_id) → Session

Idle 超时重置：
  Session 3 小时无活动自动结束 → 生成 summary → 新 session 加载旧 summary。
"""

from __future__ import annotations

import time
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.session import Session


# ── 常量 ────────────────────────────────────────────────────────────────

IDLE_TIMEOUT_MINUTES = 180  # 3 小时无活动则重置 session
_IDLE_TIMEOUT_SECONDS = IDLE_TIMEOUT_MINUTES * 60


class SessionManager:
    """管理多个 Session 实例的生命周期，支持 idle 超时重置。"""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._cli_session: Session | None = None  # cli 全局单例

    # ── 核心路由 ────────────────────────────────────────────────────────

    def get_or_create(self, channel: str, sender_id: str) -> "Session":
        """获取或创建一个 Session 实例。

        Args:
            channel: 渠道标识，如 "cli"、"feishu"、"telegram"、"discord"
            sender_id: 发送者 ID，cli 渠道固定为 "default"

        Returns:
            Session 实例
        """
        # cli 渠道：全局单例
        if channel == "cli":
            return self._get_or_create_cli()

        # 其他渠道：每个 sender_id 独立
        key = f"{channel}:{sender_id}"
        return self._get_or_create(channel, key)

    def _get_or_create_cli(self) -> "Session":
        """获取 CLI 全局 Session（单例），idle 超时触发重置。"""
        if self._cli_session is not None:
            if self._is_idle_expired(self._cli_session):
                self._reset_session(channel="cli", sender_id="default", is_cli=True)
            return self._cli_session

        with self._lock:
            if self._cli_session is not None:
                if self._is_idle_expired(self._cli_session):
                    self._reset_session(channel="cli", sender_id="default", is_cli=True)
                return self._cli_session
            self._cli_session = self._create_session(channel="cli", sender_id="default")
            return self._cli_session

    def _get_or_create(self, channel: str, key: str) -> "Session":
        """获取或创建普通 Session，idle 超时触发重置。"""
        with self._lock:
            if key in self._sessions:
                if self._is_idle_expired(self._sessions[key]):
                    self._reset_session(channel=channel, sender_id=key, is_cli=False)
                return self._sessions[key]

            sender_id = key.split(":", 1)[1] if ":" in key else key
            session = self._create_session(channel=channel, sender_id=sender_id)
            self._sessions[key] = session
            return session

    # ── 空闲检测 ───────────────────────────────────────────────────────

    def _is_idle_expired(self, session: "Session") -> bool:
        """检查 session 是否已 idle 超时。"""
        if session.last_activity_at <= 0:
            return False
        return (time.time() - session.last_activity_at) > _IDLE_TIMEOUT_SECONDS

    # ── Session 创建 ────────────────────────────────────────────────────

    def _create_session(self, channel: str, sender_id: str) -> "Session":
        """创建新 Session（调用时须持有 self._lock）。

        如果存在同 channel 的上一条已结束 session，自动加载其 summary 注入到 system prompt。
        """
        from src.core.session import Session

        from src.memory import session_store as ss

        # 尝试加载上一条 session 的 summary
        prev_summary = ""
        try:
            prev_summary = ss.get_last_session_summary(channel, sender_id) or ""
        except Exception:
            pass

        try:
            si = ss.create_session(source=channel)
            session_id = si.session_id
        except Exception:
            session_id = None

        session = Session.from_config(self._config)
        if session_id:
            session.session_id = session_id
        session._session_manager = self
        session.last_activity_at = time.time()

        # 注入上一条 session 的 summary
        if prev_summary:
            session._inject_resume_summary(prev_summary)

        return session

    # ── Idle 重置 ──────────────────────────────────────────────────────

    def _reset_session(self, channel: str, sender_id: str, is_cli: bool) -> None:
        """结束旧 session、生成 summary、注入新 session 并加载旧 summary。

        调用时须持有 self._lock。
        """
        from src.memory import session_store as ss
        from src.core import session_resume

        # 获取旧 session（在 lock 内操作，缓存引用不变）
        old_session = self._cli_session if is_cli else self._sessions.get(sender_id)
        if old_session is None:
            return

        old_id = old_session.session_id
        cache_key = "cli" if is_cli else sender_id

        print(f"[session_manager] Session {old_id} idle 超时，触发重置", flush=True)

        # 生成 summary（使用旧 session 的 LLM client）
        summary = ""
        if old_id:
            messages = ss.get_session_messages(old_id)
            if messages:
                llm_bundle = old_session.llm_clients.get(old_session._current_model_name)
                if llm_bundle:
                    summary = session_resume.generate_session_summary(messages, llm_bundle["llm"])
                    if summary:
                        print(f"[session_manager] summary: {summary[:80]}...", flush=True)

        # 结束旧 session（写入 summary）
        if old_id:
            try:
                ss.end_session(old_id, summary=summary)
            except Exception as e:
                print(f"[session_manager] end_session 失败: {e}", flush=True)

        # 创建新 session（在 lock 内；_create_session 会自动加载旧 summary）
        new_session = self._create_session(channel=channel, sender_id=sender_id)

        # 更新缓存
        if is_cli:
            self._cli_session = new_session
        else:
            self._sessions[sender_id] = new_session

        print(
            f"[session_manager] 新 session {new_session.session_id} 已创建"
            f"{'，已注入旧 summary' if summary else ''}",
            flush=True,
        )

    # ── 生命周期 ───────────────────────────────────────────────────────

    def close_all(self) -> None:
        """关闭所有 Session（进程退出时调用）。"""
        with self._lock:
            for session in self._sessions.values():
                try:
                    session.save_summary()
                except Exception:
                    pass
            self._sessions.clear()
            if self._cli_session is not None:
                try:
                    self._cli_session.save_summary()
                except Exception:
                    pass
                self._cli_session = None


# ── 全局单例 ───────────────────────────────────────────────────────────

_manager: SessionManager | None = None


def get_session_manager(config: dict) -> SessionManager:
    """获取全局 SessionManager（懒创建）。"""
    global _manager
    if _manager is None:
        _manager = SessionManager(config)
    return _manager
