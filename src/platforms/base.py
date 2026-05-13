"""平台适配器抽象基类与消息数据结构。

所有平台 adapter 继承 BasePlatformAdapter，实现统一的 send/start/shutdown 接口。
平台收到消息后通过 on_message() 推给 PlatformManager 调度。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class PlatformMessage:
    """跨平台统一消息结构。"""

    platform: str  # feishu / cli / ...
    sender_id: str  # 用户 open_id / user_id
    chat_id: str  # 会话 ID
    thread_id: str | None  # 线程 ID（飞书支持）
    message_id: str  # 平台消息 ID（用于去重）
    text: str  # 消息正文
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    reaction_id: str | None = None  # 消息所属 reaction_id（飞书专用）
    metadata: dict[str, Any] = field(default_factory=dict)


class BasePlatformAdapter(ABC):
    """所有平台 adapter 的抽象基类。

    子类需要实现 start / shutdown / send / send_card 四个方法。
    收到消息时调用 self.on_message(msg) 推给 PlatformManager。

    Attributes
    ----------
    session_manager : SessionManager | None
        由 PlatformManager 在 dispatch 前设置，提供 session 访问能力。
    safe_mode_callback : callable | None
        触发 /recovery 时的回调，由 PlatformManager 设置。
    """

    platform: str = ""

    session_manager: Any = None
    safe_mode_callback: Any = None

    @abstractmethod
    def start(self) -> None:
        """启动平台连接，非阻塞。失败时抛异常，由 PlatformManager 处理重连。"""

    @abstractmethod
    async def shutdown(self, timeout: float = 30.0) -> None:
        """优雅关闭平台连接。"""

    @abstractmethod
    async def send(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        """发送文本消息到指定会话。"""

    @abstractmethod
    async def send_card(self, chat_id: str, card: dict, thread_id: str | None = None) -> None:
        """发送卡片消息到指定会话。"""

    def on_message(self, msg: PlatformMessage) -> None:
        """平台收到消息时调用，推给 PlatformManager 调度。"""
        from src.platforms.manager import PlatformManager

        try:
            manager = PlatformManager.instance()
        except RuntimeError:
            logger.warning(
                f"[{self.platform}] PlatformManager 未初始化，消息暂被丢弃: {msg.message_id}"
            )
            return

        manager.dispatch(msg)
