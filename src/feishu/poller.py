"""飞书消息轮询器：定期拉取指定会话的新消息并自动回复。

使用方式：配置 feishu.chat_ids 后运行 lampson --serve
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from src.feishu.client import FeishuClient


class MessageDeduplicator:
    """消息去重：基于 message_id 防止重复处理。"""

    def __init__(self, ttl_seconds: int = 120) -> None:
        self._seen: dict[str, float] = {}
        self._ttl = ttl_seconds

    def is_duplicate(self, message_id: str) -> bool:
        now = time.monotonic()
        if message_id in self._seen:
            return True
        self._seen[message_id] = now
        return False

    def cleanup(self) -> None:
        now = time.monotonic()
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._ttl}


class FeishuPoller:
    """轮询飞书会话消息并自动回复。"""

    def __init__(
        self,
        feishu_client: FeishuClient,
        agent: Any,
        chat_ids: list[str],
        poll_interval: int = 3,
    ) -> None:
        self.feishu_client = feishu_client
        self.agent = agent
        self.chat_ids = chat_ids
        self.poll_interval = poll_interval
        self._dedup = MessageDeduplicator()
        self._bot_open_id: str = ""
        # 每个 chat_id 最后处理的消息时间戳（毫秒字符串）
        self._last_ts: dict[str, str] = {}

    def _get_bot_open_id(self) -> str:
        """获取机器人自身 open_id。"""
        info = self.feishu_client.get_bot_info()
        bot = info.get("bot", {})
        open_id = bot.get("open_id", "")
        if not open_id:
            print(f"[poller] 警告：无法获取 bot open_id，原始响应：{info}")
        return open_id

    def _extract_text(self, content: str) -> str:
        """从消息 content 字段提取纯文本。"""
        try:
            parsed = json.loads(content)
            return parsed.get("text", content)
        except Exception:
            return content

    def _process_message(self, msg: dict[str, Any], chat_id: str) -> None:
        """处理单条消息：调用 agent 获取回复并发送。"""
        msg_id = msg.get("message_id", "")
        body = msg.get("body", {})
        content = body.get("content", "")
        text = self._extract_text(content)

        if not text.strip():
            return

        print(f"[poller] 收到消息（{chat_id}）：{text[:80]}")

        try:
            reply = self.agent.run(text)
        except Exception as e:
            print(f"[poller] agent.run 出错（msg_id={msg_id}）：{e}")
            reply = f"[处理出错] {e}"

        try:
            self.feishu_client.send_message(
                receive_id=chat_id,
                text=reply,
                receive_id_type="chat_id",
            )
            print(f"[poller] 已回复（{chat_id}）：{reply[:80]}")
        except Exception as e:
            print(f"[poller] 发送回复失败（chat_id={chat_id}）：{e}")

    def _poll_chat(self, chat_id: str, base_ts_ms: str) -> None:
        """拉取单个 chat 的新消息并处理。"""
        try:
            items = self.feishu_client.get_messages(
                container_id=chat_id,
                page_size=5,
            )
        except Exception as e:
            print(f"[poller] 拉取消息失败（{chat_id}）：{e}")
            return

        last_ts = self._last_ts.get(chat_id, base_ts_ms)

        # items 按创建时间倒序，先反转成正序处理
        new_messages = []
        for msg in reversed(items):
            message_id = msg.get("message_id", "")
            if self._dedup.is_duplicate(message_id):
                continue

            create_time = msg.get("create_time", "0")
            if create_time <= last_ts:
                continue

            sender_type = msg.get("sender", {}).get("sender_type", "")
            sender_id = msg.get("sender", {}).get("sender_id", {}).get("open_id", "")

            # 跳过机器人自己发的消息
            if sender_type == "app" or (self._bot_open_id and sender_id == self._bot_open_id):
                last_ts = max(last_ts, create_time)
                continue

            new_messages.append((create_time, msg))

        for create_time, msg in new_messages:
            try:
                self._process_message(msg, chat_id)
            except Exception as e:
                print(f"[poller] 处理消息异常：{e}")
            last_ts = max(last_ts, create_time)

        # 定期清理过期去重记录
        self._dedup.cleanup()

        self._last_ts[chat_id] = last_ts

    def start(self) -> None:
        """启动轮询循环（阻塞）。"""
        if not self.chat_ids:
            print("[poller] 没有配置 feishu.chat_ids，请在 ~/.lampson/config.yaml 中添加要监听的会话 ID。")
            return

        print("[poller] 正在获取 bot 信息...")
        try:
            self._bot_open_id = _get_bot_open_id_safe(self)
        except Exception as e:
            print(f"[poller] 获取 bot 信息失败：{e}")

        # 基准时间戳（毫秒字符串），只处理启动后的新消息
        base_ts_ms = str(int(time.time() * 1000))
        for chat_id in self.chat_ids:
            self._last_ts[chat_id] = base_ts_ms

        print(f"[poller] 开始监听 {len(self.chat_ids)} 个会话，轮询间隔 {self.poll_interval}s。Ctrl+C 停止。")

        try:
            while True:
                for chat_id in self.chat_ids:
                    self._poll_chat(chat_id, base_ts_ms)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n[poller] 已停止监听。")


def _get_bot_open_id_safe(poller: FeishuPoller) -> str:
    """安全获取 bot open_id，失败返回空字符串。"""
    try:
        return poller._get_bot_open_id()
    except Exception as e:
        print(f"[poller] 获取 bot open_id 失败：{e}")
        return ""
