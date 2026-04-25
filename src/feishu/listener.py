"""飞书长连接监听器：通过 WebSocket 接收消息事件，替代轮询方案。

职责仅限：消息收发 + 去重。
所有业务逻辑通过 Session.handle_input() 处理。
"""

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
    from src.core.session import Session


class MessageDeduplicator:
    """消息去重器：基于 message_id 防止重复处理。

    维护一个滑动窗口内的已处理消息ID集合，
    同一消息在 TTL 窗口内只处理一次。
    """

    def __init__(self, ttl_seconds: int = 600, max_size: int = 10000) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size

    def is_duplicate(self, message_id: str) -> bool:
        """返回 True 表示该消息已被处理过（去重）。只检查，不写入。"""
        now = time.monotonic()
        with self._lock:
            # 清理过期条目
            expired = [mid for mid, ts in self._seen.items() if now - ts > self._ttl]
            for mid in expired:
                del self._seen[mid]

            return message_id in self._seen

    def mark_processed(self, message_id: str) -> None:
        """标记一条消息为已处理（处理成功后调用）。"""
        with self._lock:
            # 防止无限膨胀
            if len(self._seen) >= self._max_size:
                oldest = min(self._seen, key=self._seen.get)
                del self._seen[oldest]
            self._seen[message_id] = time.monotonic()


class FeishuListener:
    """基于 lark_oapi WebSocket 长连接的飞书消息监听器。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        agent=None,
        session: Session | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        # 兼容旧调用方式（直接传 agent）和新方式（传 session）
        if session is not None:
            self._session = session
        elif agent is not None:
            # 向后兼容：从 agent 构造一个最小 session 包装
            self._session = None
            self._agent = agent
        else:
            raise ValueError("必须传入 agent 或 session")
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

            # 过期消息丢弃：超过 60 秒才投递的视为过期
            create_time_str = getattr(message, "create_time", None)
            if create_time_str:
                try:
                    create_ts = int(create_time_str) / 1000  # 飞书毫秒时间戳
                    delay = time.time() - create_ts
                    if delay > 60:
                        print(
                            f"[listener] 消息已过期（投递延迟 {delay:.0f} 秒），丢弃",
                            flush=True,
                        )
                        return
                except (ValueError, TypeError):
                    pass

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

            # 走 Session（推荐）或直接走 agent（向后兼容）
            if self._session is not None:
                # 注入实时消息回调，供 /model all 等流式场景使用
                self._session.partial_sender = lambda text: self._send_reply(chat_id, text)
                try:
                    result = self._session.handle_input(text)
                finally:
                    self._session.partial_sender = None
                reply = result.reply
                if result.compaction_msg:
                    print(f"[listener] {result.compaction_msg}", flush=True)
            else:
                print(f"[listener] 调用 agent.run，输入: {text}", flush=True)
                reply = self._agent.run(text)
                # 上下文压缩
                try:
                    cr = self._agent.maybe_compact()
                    if cr is not None and cr.success:
                        print(f"[listener] 上下文压缩已完成，归档 {cr.archived_count} 条内容。", flush=True)
                    elif cr is not None and not cr.success:
                        print(f"[listener] 上下文压缩失败: {cr.error}", flush=True)
                except Exception as e:
                    print(f"[listener] 上下文压缩异常: {e}", flush=True)

            print(f"[listener] 回复: {reply}", flush=True)

            self._dedup.mark_processed(message_id)
            if reply:
                self._send_reply(chat_id, reply)

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
