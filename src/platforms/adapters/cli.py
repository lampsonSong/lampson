"""CLI 平台适配器：将终端作为消息渠道，支持后台任务推送结果到终端。

与 FeishuAdapter 对称：输入来自 stdin，输出回到 stdout。
后台任务完成后通过此 adapter 打印结果到终端。
"""

from __future__ import annotations

import asyncio
import sys

from src.platforms.base import BasePlatformAdapter, PlatformMessage


class CliAdapter(BasePlatformAdapter):
    """CLI 平台适配器：输出到终端。CLI 模式下无消息入口。"""

    platform = "cli"

    def __init__(self, config: dict | None = None) -> None:
        pass

    def start(self) -> None:
        """CLI 不需要启动连接。"""

    async def shutdown(self, timeout: float = 30.0) -> None:
        """CLI 不需要关闭。"""

    async def send(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        """输出到 stdout。"""
        await asyncio.to_thread(self._print, text)

    async def send_card(self, chat_id: str, card: dict, thread_id: str | None = None) -> None:
        """卡片降级为文本输出。"""
        elements = card.get("body", {}).get("elements", [])
        parts = []
        for elem in elements:
            content = elem.get("content", "")
            if content:
                parts.append(content)
        text = "\n".join(parts) if parts else str(card)
        await asyncio.to_thread(self._print, text)

    @staticmethod
    def _print(text: str) -> None:
        """线程安全地打印到终端。"""
        print(f"\n{'='*60}\n{text}\n{'='*60}", flush=True)

    def _handle_dispatch(
        self,
        open_id: str,
        chat_id: str,
        message_id: str,
        text: str,
        reaction_id: str | None = None,
    ) -> None:
        """CLI 无消息入口，忽略。"""
        pass
