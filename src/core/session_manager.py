"""SessionManager：管理多个 Session 实例，按 channel+sender_id 路由。

设计文档：docs/PROJECT.md §2.1

路由规则：
  "cli"         → 全局唯一一个 Session（开发者专用）
  "feishu:{id}" → 每个 sender_id 一个独立 Session
  "telegram:{id}" → 每个 sender_id 一个独立 Session
  "discord:{id}"  → 每个 sender_id 一个独立 Session

核心接口：
  session_manager.get_or_create(channel, sender_id) → Session
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.session import Session


class SessionManager:
    """管理多个 Session 实例的生命周期。"""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._cli_session: Session | None = None  # cli 全局单例

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
        """获取 CLI 全局 Session（单例）。"""
        if self._cli_session is not None:
            return self._cli_session

        with self._lock:
            if self._cli_session is not None:
                return self._cli_session
            self._cli_session = self._create_session(channel="cli", sender_id="default")
            return self._cli_session

    def _get_or_create(self, channel: str, key: str) -> "Session":
        """获取或创建普通 Session。"""
        with self._lock:
            if key in self._sessions:
                return self._sessions[key]
            sender_id = key.split(":", 1)[1] if ":" in key else key
            session = self._create_session(channel=channel, sender_id=sender_id)
            self._sessions[key] = session
            return session

    def _create_session(self, channel: str, sender_id: str) -> "Session":
        """创建新 Session。"""
        from src.core.session import Session

        # 在 session_store 里记录 source
        from src.memory import session_store

        try:
            si = session_store.create_session(source=channel)
            session_id = si.session_id
        except Exception:
            session_id = None

        session = Session.from_config(self._config)
        if session_id:
            session.session_id = session_id
        session._session_manager = self  # 让 Session 能访问 SessionManager

        return session

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
