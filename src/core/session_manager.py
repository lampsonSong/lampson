"""SessionManager：管理多个 Session 实例，按 channel+sender_id 路由。

路由规则：
  "cli"         → 全局唯一一个 Session（开发者专用）
  "feishu:{id}" → 每个 sender_id 一个独立 Session
  "telegram:{id}" → 每个 sender_id 一个独立 Session
  "discord:{id}"  → 每个 sender_id 一个独立 Session

核心接口：
  session_manager.get_or_create(channel, sender_id) → Session
  session_manager.reset_session(channel, sender_id) → Session

Idle 超时重置：
  Session 3 小时无活动自动结束 → 创建新空白 session。
"""

from __future__ import annotations

import time
import threading
from typing import TYPE_CHECKING
import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.core.session import Session


# ── 常量 ────────────────────────────────────────────────────────────────

from src.core.constants import IDLE_TIMEOUT_MINUTES, IDLE_TIMEOUT_SECONDS as _IDLE_TIMEOUT_SECONDS


class SessionManager:
    """管理多个 Session 实例的生命周期，支持 idle 超时重置。"""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._cli_session: Session | None = None  # cli 全局单例

        # 进程启动时清理上次遗留的孤儿/空 session（只执行一次）
        from src.memory import session_store as ss
        ss.close_orphan_sessions()
        ss.purge_empty_sessions()

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
        """创建新 Session（调用时须持有 self._lock）。"""
        from src.core.session import Session

        from src.memory import session_store as ss

        # 重试 create_session，扛住数据库锁等瞬时错误
        session_id = None
        for attempt in range(3):
            try:
                si = ss.create_session(source=channel)
                session_id = si.session_id
                break
            except Exception as e:
                logger.error(f"[session_manager] create_session 失败 (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))

        session = Session.from_config(self._config, channel=channel)
        if session_id:
            session.session_id = session_id
            session.agent.session_id = session_id
        else:
            logger.warning("[session_manager] 警告: session_id 为空，SQLite 索引将不可用")
        session._session_manager = self
        session.last_activity_at = time.time()

        return session

    # ── Session 重置 ──────────────────────────────────────────────────

    def _reset_session(self, channel: str, sender_id: str, is_cli: bool) -> None:
        """结束旧 session，创建新空白 session。

        调用时须持有 self._lock。
        """
        from src.memory import session_store as ss

        # 获取旧 session（在 lock 内操作，缓存引用不变）
        old_session = self._cli_session if is_cli else self._sessions.get(sender_id)
        if old_session is None:
            return

        old_id = old_session.session_id

        logger.info(f"[session_manager] Session {old_id} 重置")

        # 结束旧 session（不生成 summary）
        if old_id:
            try:
                ss.end_session(old_id)
            except Exception as e:
                logger.error(f"[session_manager] end_session 失败: {e}")

        # 创建新 session（空白，不注入任何旧信息）
        new_session = self._create_session(channel=channel, sender_id=sender_id)

        # 更新缓存
        if is_cli:
            self._cli_session = new_session
        else:
            self._sessions[sender_id] = new_session

        logger.info(
            f"[session_manager] 新 session {new_session.session_id} 已创建"
        )

    def reset_session(self, channel: str, sender_id: str) -> "Session":
        """公开接口：结束当前 session，创建并返回新 session。

        供 /new 命令等场景使用，线程安全。
        """
        from src.memory import session_store as ss

        is_cli = channel == "cli"
        key = sender_id if is_cli else f"{channel}:{sender_id}"
        with self._lock:
            old_session = self._cli_session if is_cli else self._sessions.get(key)
            old_id = old_session.session_id if old_session else None
            if old_id:
                try:
                    ss.end_session(old_id)
                except Exception as e:
                    logger.error(f"[session_manager] end_session 失败: {e}")
            logger.info(f"[session_manager] Session {old_id} 重置")
            new_session = self._create_session(channel=channel, sender_id=sender_id)
            if is_cli:
                self._cli_session = new_session
            else:
                self._sessions[key] = new_session
            logger.info(f"[session_manager] 新 session {new_session.session_id} 已创建")
            return new_session

    # ── 生命周期 ───────────────────────────────────────────────────────

    def remove_session(self, channel: str, sender_id: str) -> None:
        """从内存缓存移除 session 并清理存储（适用于空 session 清理）。

        线程安全。如果 session 不存在或不在缓存中，静默跳过。
        """
        from src.memory import session_store as ss

        key = f"{channel}:{sender_id}"
        with self._lock:
            session = self._sessions.pop(key, None)
            if session is None:
                return
            sid = session.session_id

        if sid:
            try:
                ss.purge_session(sid)
                logger.info(f"[session_manager] 已清理空 session {sid}")
            except Exception as e:
                logger.error(f"[session_manager] 清理空 session {sid} 失败: {e}")

    def refresh_all_indices(self) -> None:
        """通知所有活跃 Session 重建索引（memory 目录文件变更时调用）。"""
        with self._lock:
            sessions = list(self._sessions.values())
            if self._cli_session is not None:
                sessions.append(self._cli_session)
        for session in sessions:
            try:
                session.refresh_indices()
            except Exception as e:
                logger.error(f"[session_manager] refresh_indices 失败: {e}")

    def close_all(self) -> None:
        """关闭所有 Session（进程退出时调用）。"""
        from src.memory import session_store as ss

        with self._lock:
            for session in self._sessions.values():
                try:
                    ss.end_session(session.session_id)
                except Exception:
                    pass
            self._sessions.clear()
            if self._cli_session is not None:
                try:
                    ss.end_session(self._cli_session.session_id)
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
