"""平台适配器抽象基类与消息数据结构。

所有平台 adapter 继承 BasePlatformAdapter，实现统一的 send/start/shutdown 接口。
平台收到消息后通过 on_message() 推给 PlatformManager 调度。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.session_manager import SessionManager


@dataclass
class PlatformMessage:
    """平台消息的统一数据结构，所有 adapter 收到消息后转换为此格式。"""

    platform: str  # "feishu" / "telegram" / "discord"
    sender_id: str  # 用户在平台上的 ID
    chat_id: str  # 会话 ID
    thread_id: str | None = None  # 线程 / topic ID（无则 None）
    message_id: str = ""  # 消息 ID（去重用）
    text: str = ""  # 消息文本
    timestamp: float = field(default_factory=time.time)


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
    session_manager: "SessionManager | None" = None
    safe_mode_callback: callable | None = None

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

        PlatformManager.instance().dispatch(msg)
