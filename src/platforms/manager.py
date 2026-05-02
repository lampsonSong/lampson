"""PlatformManager：多平台消息网关核心调度器。

管理所有平台 adapter 的生命周期，统一消息路由到 SessionManager，
管理 BackgroundTaskManager，提供 schedule_async() 供后台线程安全调度协程。
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

from src.platforms.base import BasePlatformAdapter, PlatformMessage
from src.platforms.background import BackgroundTaskManager
from src.core.session_manager import get_session_manager

if TYPE_CHECKING:
    pass


class PlatformManager:
    """多平台消息网关核心调度器，单例模式。"""

    _instance: "PlatformManager | None" = None

    def __init__(self, config: dict) -> None:
        self._config = config
        self._adapters: dict[str, BasePlatformAdapter] = {}
        self._session_manager = get_session_manager(config)
        self._background_mgr = BackgroundTaskManager.instance()
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def instance(cls) -> "PlatformManager":
        if cls._instance is None:
            raise RuntimeError("PlatformManager 未初始化，请先创建实例并赋值给 _instance")
        return cls._instance

    def register(self, adapter: BasePlatformAdapter) -> None:
        """注册平台 adapter，并注入 session_manager。"""
        adapter.session_manager = self._session_manager
        self._adapters[adapter.platform] = adapter

    def dispatch(self, msg: PlatformMessage) -> None:
        """消息入口：路由到对应 Session（由 adapter 线程触发）。

        完整消息处理（进度卡片、命令处理、回复发送）由各 adapter 自行完成，
        这里只做 session 路由 + 回调注入。
        """
        adapter = self._adapters.get(msg.platform)
        if adapter is None:
            print(f"[manager] 无 {msg.platform} adapter，跳过消息", flush=True)
            return

        # 调用 adapter 的完整调度入口（含进度卡片、命令处理、回复发送）
        adapter._handle_dispatch(
            open_id=msg.sender_id,
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            text=msg.text,
            reaction_id=msg.reaction_id,
        )

    async def run(self) -> None:
        """主事件循环：启动所有 adapter，处理信号，保持运行。"""
        self._running = True
        self._loop = asyncio.get_running_loop()

        # 启动所有已配置的 adapter（含指数退避重连）
        platforms_cfg = self._config.get("platforms", {})
        for platform, cfg in platforms_cfg.items():
            if not cfg.get("enabled", False):
                continue
            adapter = self._create_adapter(platform, cfg)
            if adapter is None:
                continue
            self.register(adapter)
            await self._start_adapter_with_retry(adapter)

        # 信号处理
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_shutdown, sig)
            except (ValueError, OSError):
                pass  # macOS 部分场景不支持

        print("[manager] 所有平台 adapter 已启动", flush=True)

        # 主循环
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            for adapter in self._adapters.values():
                try:
                    await adapter.shutdown()
                except Exception as e:
                    print(f"[manager] 关闭 {adapter.platform} 失败: {e}", flush=True)

    async def _start_adapter_with_retry(self, adapter: BasePlatformAdapter) -> None:
        """带指数退避重连的 adapter 启动（1s → 2s → 4s → 60s cap）。"""
        retry_interval = 1
        max_interval = 60
        while True:
            try:
                adapter.start()
                print(f"[manager] adapter {adapter.platform} 启动成功", flush=True)
                return
            except Exception as e:
                print(
                    f"[manager] adapter {adapter.platform} 启动失败: {e}，"
                    f"{retry_interval}s 后重试...",
                    flush=True,
                )
                await asyncio.sleep(retry_interval)
                retry_interval = min(retry_interval * 2, max_interval)

    def _on_shutdown(self, sig: signal.Signals) -> None:
        """信号处理：优雅退出。"""
        print(f"[manager] 收到信号 {sig.name}，准备退出...", flush=True)
        self._running = False

    def schedule_async(self, coro) -> None:
        """从后台线程安全地调度协程到主事件循环。

        BackgroundTask 推送结果和 Session._send_reply 调用此方法，
        避免在线程中 asyncio.run() 导致的事件循环嵌套崩溃。
        """
        if self._loop is None:
            print("[manager] 主事件循环未初始化，无法调度协程", flush=True)
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _create_adapter(self, platform: str, cfg: dict) -> BasePlatformAdapter | None:
        """根据平台名称创建 adapter 实例。"""
        if platform == "feishu":
            from src.platforms.adapters.feishu import FeishuAdapter
            return FeishuAdapter(cfg)
        elif platform == "cli":
            from src.platforms.adapters.cli import CliAdapter
            return CliAdapter(cfg)
        else:
            print(f"[manager] 未知平台: {platform}，跳过", flush=True)
            return None
