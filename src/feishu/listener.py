"""飞书长连接监听器：通过 WebSocket 接收消息事件，替代轮询方案。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
)

if TYPE_CHECKING:
    from src.core.agent import Agent


class FeishuListener:
    """基于 lark_oapi WebSocket 长连接的飞书消息监听器。"""

    def __init__(self, app_id: str, app_secret: str, agent: "Agent") -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.agent = agent
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
