"""飞书长连接监听器：通过 WebSocket 接收消息事件，替代轮询方案。"""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
)

if TYPE_CHECKING:
    from src.core.agent import Agent
    from src.core.compaction import CompactionConfig


class MessageDeduplicator:
    """消息去重器：基于 message_id 防止重复处理。

    维护一个滑动窗口内的已处理消息ID集合，
    同一消息在 TTL 窗口内只处理一次。
    """

    def __init__(self, ttl_seconds: int = 60, max_size: int = 10000) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size

    def is_duplicate(self, message_id: str) -> bool:
        """返回 True 表示该消息已被处理过（去重）。"""
        now = time.monotonic()
        with self._lock:
            # 清理过期条目
            expired = [mid for mid, ts in self._seen.items() if now - ts > self._ttl]
            for mid in expired:
                del self._seen[mid]

            if message_id in self._seen:
                return True

            # 防止无限膨胀
            if len(self._seen) >= self._max_size:
                oldest = min(self._seen, key=self._seen.get)
                del self._seen[oldest]

            self._seen[message_id] = now
            return False

    def mark_processed(self, message_id: str) -> None:
        """手动标记一条消息为已处理（用于确认处理成功）。"""
        with self._lock:
            self._seen[message_id] = time.monotonic()


class FeishuListener:
    """基于 lark_oapi WebSocket 长连接的飞书消息监听器。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        agent: "Agent",
        compaction_config: "CompactionConfig | None" = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.agent = agent
        self.compaction_config = compaction_config
        self._dedup = MessageDeduplicator()
        self._lark_client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

    def _send_reply(self, chat_id: str, text: str) -> None:
        """向指定 chat_id 发送文本消息。"""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = self._lark_client.im.v1.message.create(request)
        if not resp.success():
            print(
                f"[listener] 发送消息失败：code={resp.code} msg={resp.msg}",
                flush=True,
            )
        else:
            print("[listener] 消息发送成功", flush=True)

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        """处理收到的消息事件。"""
        try:
            sender = data.event.sender
            message = data.event.message

            sender_type = getattr(sender, "sender_type", None)
            if sender_type == "app":
                print("[listener] 收到机器人自己的消息，跳过", flush=True)
                return

            open_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            raw_content = message.content

            print(
                f"[listener] 收到消息 from={open_id} chat_id={chat_id} content={raw_content}",
                flush=True,
            )

            message_id = getattr(message, "message_id", None) or str(id(data))
            if self._dedup.is_duplicate(message_id):
                print(f"[listener] 消息 {message_id} 已处理过，跳过", flush=True)
                return

            try:
                content_obj = json.loads(raw_content)
                text = content_obj.get("text", "").strip()
            except (json.JSONDecodeError, AttributeError):
                text = raw_content or ""

            if not text:
                print("[listener] 消息内容为空，跳过", flush=True)
                return

            print(f"[listener] 调用 agent.run，输入: {text}", flush=True)
            reply = self.agent.run(text)
            print(f"[listener] agent 回复: {reply}", flush=True)

            self._dedup.mark_processed(message_id)
            self._send_reply(chat_id, reply)

            # 上下文压缩检查
            if self.compaction_config:
                from src.core.compaction import apply_compaction
                try:
                    cr = apply_compaction(
                        self.agent.llm,
                        self.compaction_config,
                        self.agent.last_total_tokens,
                        self.agent.last_stop_reason,
                    )
                    if cr is not None and cr.success:
                        print(f"[listener] 上下文压缩已完成，归档 {cr.archived_count} 条内容。", flush=True)
                    elif cr is not None and not cr.success:
                        print(f"[listener] 上下文压缩失败: {cr.error}", flush=True)
                except Exception as e:
                    print(f"[listener] 上下文压缩异常: {e}", flush=True)


        except Exception as e:
            print(f"[listener] 处理消息时发生错误：{e}", flush=True)

    def start(self) -> None:
        """启动长连接，阻塞运行直到进程退出。"""
        print(
            f"[listener] 启动飞书长连接监听，app_id={self.app_id}",
            flush=True,
        )

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message)
            .build()
        )

        ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.DEBUG,
        )

        print("[listener] WebSocket 客户端已创建，开始连接...", flush=True)
        ws_client.start()
