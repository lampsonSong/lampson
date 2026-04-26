"""飞书长连接监听器：通过 WebSocket 接收消息事件，替代轮询方案。

职责仅限：消息收发 + 去重。
所有业务逻辑通过 Session.handle_input() 处理。
"""

from __future__ import annotations

import json
import queue
import re
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

    # ─── 发送方法 ───────────────────────────────────────────────────────────

    def _send_reply(self, chat_id: str, text: str) -> None:
        """发送最终回复，自动判断用卡片还是文本。"""
        if self._should_use_card(text):
            self._send_reply_as_card(chat_id, text)
        else:
            self._send_reply_as_text(chat_id, text)

    @staticmethod
    def _should_use_card(text: str) -> bool:
        """判断回复是否含结构化数据（表格/指标），适合用卡片展示。"""
        # 包含 markdown 表格
        if "|---" in text or "| ---" in text:
            return True
        return False

    def _send_reply_as_card(self, chat_id: str, text: str) -> None:
        """用 Markdown 卡片发送回复（表格/指标等结构化数据）。"""
        from src.feishu.client import get_client
        try:
            client = get_client()
            # 提取标题：取第一个 ## 或 **加粗**
            title = "Lampson 回复"
            m = re.search(r"##\s*(.+)", text) or re.search(r"\*\*(.+?)\*\*", text)
            if m:
                title = m.group(1).strip()
            card = client.build_md_card(title=title, content=text, header_template="green")
            client.send_card(receive_id=chat_id, card=card, receive_id_type="chat_id")
            print("[listener] 卡片消息发送成功", flush=True)
        except Exception as e:
            print(f"[listener] 卡片发送失败({e})，降级为文本", flush=True)
            self._send_reply_as_text(chat_id, text)

    def _send_reply_as_text(self, chat_id: str, text: str) -> None:
        """发送纯文本消息。"""
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

    # ─── 进度卡片（发一条 + update，不刷屏）─────────────────────────────

    def _make_progress_card(self, lines: list[str], finished: bool = False) -> dict:
        """构建工具调用进度卡片。"""
        status = "已完成" if finished else "处理中..."
        elements = [
            {"tag": "markdown", "content": f"**{status}** ({len(lines)} 个工具调用)"},
        ]
        shown = lines[-15:]
        if len(lines) > 15:
            elements.append({"tag": "markdown", "content": f"_...前 {len(lines) - 15} 条省略_"})
        for line in shown:
            elements.append({"tag": "markdown", "content": line})
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "Lampson 工作进度"},
                "template": "green" if finished else "blue",
            },
            "body": {"elements": elements},
        }

    def _send_progress_card(self, chat_id: str, lines: list[str], finished: bool = False) -> str | None:
        """发送进度卡片，返回 message_id。"""
        from src.feishu.client import get_client
        card = self._make_progress_card(lines, finished=finished)
        try:
            client = get_client()
            data = client.send_card(receive_id=chat_id, card=card, receive_id_type="chat_id")
            return data.get("data", {}).get("message_id")
        except Exception as e:
            print(f"[listener] 发送进度卡片失败: {e}", flush=True)
            return None

    def _update_progress_card(self, message_id: str, lines: list[str], finished: bool = False) -> None:
        """更新已有的进度卡片。"""
        from src.feishu.client import get_client
        card = self._make_progress_card(lines, finished=finished)
        try:
            client = get_client()
            client.update_message(message_id=message_id, card=card)
        except Exception as e:
            print(f"[listener] 更新进度卡片失败: {e}", flush=True)

    # ─── 消息处理 ─────────────────────────────────────────────────────────

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
                self._session.partial_sender = lambda text: self._send_reply(chat_id, text)

                _progress_queue: queue.Queue = queue.Queue()
                _progress_done = threading.Event()

                def _progress_worker() -> None:
                    """后台线程：收集工具调用进度，用卡片 + update 不刷屏。"""
                    progress_lines: list[str] = []
                    progress_msg_id: str | None = None
                    last_update_ts = 0.0
                    update_interval = 1.5

                    while True:
                        try:
                            event = _progress_queue.get(timeout=0.5)
                        except queue.Empty:
                            if _progress_done.is_set():
                                # 最终更新：标记为已完成
                                if progress_lines:
                                    if progress_msg_id is None:
                                        self._send_progress_card(
                                            chat_id, progress_lines, finished=True
                                        )
                                    else:
                                        self._update_progress_card(
                                            progress_msg_id, progress_lines, finished=True
                                        )
                                return
                            continue

                        if not isinstance(event, dict) or event.get("type") != "tool_progress":
                            continue

                        round_n = event["round"]
                        tool = event["tool"]
                        args_p = event["args_preview"]
                        result_p = event["result_preview"]
                        is_error = (
                            result_p.startswith("[错误]")
                            or result_p.startswith("[飞书错误]")
                            or result_p.startswith("[网络错误]")
                        )
                        icon = "x" if is_error else ">"
                        progress_lines.append(
                            f"**{round_n}.** `{tool}`({args_p})\n  {icon} {result_p}"
                        )

                        # 节流：每 1.5 秒最多更新一次卡片
                        now = time.monotonic()
                        if now - last_update_ts < update_interval:
                            continue

                        if progress_msg_id is None:
                            progress_msg_id = self._send_progress_card(
                                chat_id, progress_lines
                            )
                        else:
                            self._update_progress_card(progress_msg_id, progress_lines)
                        last_update_ts = time.monotonic()

                _worker = threading.Thread(
                    target=_progress_worker,
                    daemon=True,
                    name="feishu-progress",
                )
                _worker.start()

                def _progress_cb(event):
                    _progress_queue.put(event)

                self._session.agent.progress_callback = _progress_cb
                try:
                    result = self._session.handle_input(text)
                finally:
                    _progress_done.set()
                    _worker.join(timeout=3.0)
                    self._session.partial_sender = None
                    self._session.agent.progress_callback = None
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
