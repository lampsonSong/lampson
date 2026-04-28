"""飞书长连接监听器：通过 WebSocket 接收消息事件，替代轮询方案。

职责仅限：消息收发 + 去重。
所有业务逻辑通过 SessionManager → Session.handle_input() 处理。

设计文档：docs/PROJECT.md §4.10
start() 为非阻塞（WebSocket 在独立线程中运行，进程退出前应调用 shutdown）。
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.api.im.v1.model import (
    CreateMessageReactionRequestBuilder,
    CreateMessageReactionRequestBodyBuilder,
    EmojiBuilder,
    DeleteMessageReactionRequestBuilder,
)

if TYPE_CHECKING:
    from src.core.session import Session
    from src.core.session_manager import SessionManager


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
    """基于 lark_oapi WebSocket 长连接的飞书消息监听器（后台线程运行，见 shutdown）。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        session_manager: "SessionManager | None" = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self._mgr = session_manager
        self._dedup = MessageDeduplicator()
        self._ws_client: Any | None = None
        self._ws_thread: threading.Thread | None = None
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

    # ─── Reaction（ack 表情）─────────────────────────────────────────────

    def _add_reaction(self, message_id: str, emoji_type: str = "THINKING") -> str | None:
        """给消息添加表情，返回 reaction_id；失败返回 None。"""
        try:
            req = (
                CreateMessageReactionRequestBuilder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBodyBuilder()
                    .reaction_type(EmojiBuilder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            resp = self._lark_client.im.v1.message_reaction.create(req)
            if resp.success():
                return resp.data.reaction_id
            print(f"[listener] add_reaction 失败: {resp.code} {resp.msg}", flush=True)
            return None
        except Exception as e:
            print(f"[listener] add_reaction 异常: {e}", flush=True)
            return None

    def _remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        """撤销表情，返回是否成功。"""
        if not reaction_id:
            return False
        try:
            req = (
                DeleteMessageReactionRequestBuilder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            resp = self._lark_client.im.v1.message_reaction.delete(req)
            if not resp.success():
                print(f"[listener] remove_reaction 失败: {resp.code} {resp.msg}", flush=True)
            return resp.success()
        except Exception as e:
            print(f"[listener] remove_reaction 异常: {e}", flush=True)
            return False

    # ─── 消息处理 ─────────────────────────────────────────────────────────

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        """处理收到的消息事件，路由到 SessionManager。"""
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
                    create_ts = int(create_time_str) / 1000
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

            if self._mgr is None:
                print("[listener] SessionManager 未初始化，无法处理消息", flush=True)
                return

            # 通过 SessionManager 获取该 sender_id 对应的 Session
            session = self._mgr.get_or_create("feishu", open_id)
            session.partial_sender = lambda t: self._send_reply(chat_id, t)
            session._reply_callback = lambda t: self._send_reply(chat_id, t)

            _progress_queue: queue.Queue[dict[str, Any]] = queue.Queue()
            _progress_done = threading.Event()
            progress_msg_id: str | None = None

            def _progress_worker() -> None:
                """后台线程：收集工具调用进度，用卡片 + update 不刷屏。"""
                nonlocal progress_msg_id
                progress_lines: list[str] = []
                last_update_ts = 0.0
                update_interval = 1.5

                while True:
                    try:
                        event = _progress_queue.get(timeout=0.5)
                    except queue.Empty:
                        if _progress_done.is_set():
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

                    if not isinstance(event, dict):
                        continue

                    if event.get("type") == "progress_reset":
                        # 阶段总结已发出，结束旧进度卡片，清空状态以便新开卡片
                        if progress_lines:
                            if progress_msg_id is None:
                                self._send_progress_card(
                                    chat_id, progress_lines, finished=True
                                )
                            else:
                                self._update_progress_card(
                                    progress_msg_id, progress_lines, finished=True
                                )
                        progress_lines = []
                        progress_msg_id = None
                        last_update_ts = 0.0
                        continue

                    if event.get("type") == "model_switch":
                        progress_lines.append(
                            f"**[模型切换]** {event.get('message', '')}"
                        )
                        now = time.monotonic()
                        if now - last_update_ts >= update_interval:
                            if progress_msg_id is None:
                                progress_msg_id = self._send_progress_card(
                                    chat_id, progress_lines
                                )  # None 表示发送失败，下次重试发送
                            else:
                                self._update_progress_card(progress_msg_id, progress_lines)
                        continue

                    if event.get("type") != "tool_progress":
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

                    now = time.monotonic()
                    if now - last_update_ts < update_interval:
                        continue

                    if progress_msg_id is None:
                        progress_msg_id = self._send_progress_card(
                            chat_id, progress_lines
                        )  # None 表示发送失败，下次重试发送
                    else:
                        self._update_progress_card(progress_msg_id, progress_lines)
                    last_update_ts = time.monotonic()

            _worker = threading.Thread(
                target=_progress_worker,
                daemon=True,
                name="feishu-progress",
            )
            _worker.start()

            def _progress_cb(event: dict) -> None:
                _progress_queue.put(event)

            # Ack reaction: 收到消息立即加表情
            reaction_id = self._add_reaction(message_id)

            session.set_message_context(message_id=message_id, chat_id=chat_id)
            session.agent.progress_callback = _progress_cb
            session.agent.interim_sender = lambda t: self._send_reply(chat_id, t)
            try:
                result = session.handle_input(text)
            finally:
                # 只在非入队场景下清理 progress worker
                # 入队场景：result.reply 为空，消息会在另一个线程的处理循环中被处理
                if result.reply or result.is_new or result.is_exit:
                    _progress_done.set()
                    _worker.join(timeout=3.0)
                else:
                    # 入队场景：快速清理 progress worker
                    _progress_done.set()
                    _worker.join(timeout=1.0)
                session.agent.progress_callback = None
                session.agent.interim_sender = None

            # 入队场景：消息已入队，不回复（回复会在处理线程中发送）
            if not result.reply and not result.is_new and not result.is_exit and not result.is_command:
                self._dedup.mark_processed(message_id)
                print(f"[listener] 消息已入队，等待当前任务中断后处理", flush=True)
                return

            # 处理 /new 命令：重置 session
            if result.is_new and self._mgr is not None:
                session = self._mgr.reset_session("feishu", open_id)
                session.partial_sender = lambda t: self._send_reply(chat_id, t)
                self._dedup.mark_processed(message_id)
                self._send_reply(chat_id, "[新 session 已开始]")
                return

            reply = result.reply
            if result.compaction_msg:
                print(f"[listener] {result.compaction_msg}", flush=True)

            print(f"[listener] 回复: {reply}", flush=True)

            self._remove_reaction(message_id, reaction_id or "")
            self._dedup.mark_processed(message_id)
            if reply:
                self._send_reply(chat_id, reply)

        except Exception as e:
            print(f"[listener] 处理消息时发生错误：{e}", flush=True)

    def start(self) -> None:
        """启动长连接，在独立线程中运行，立即返回（不阻塞调用方）。

        设计文档：docs/PROJECT.md §4.10
        """
        print(
            f"[listener] 启动飞书长连接监听（daemon），app_id={self.app_id}",
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
        self._ws_client = ws_client

        # daemon=True：若 shutdown 无法在超时内停止 SDK，进程仍可退出（避免 interpreters 在退出阶段无限等待）。
        t = threading.Thread(
            target=ws_client.start,
            daemon=True,
            name="feishu-ws",
        )
        self._ws_thread = t
        t.start()
        print("[listener] WebSocket 线程已启动，立即返回", flush=True)

    def shutdown(self, timeout: float = 30.0) -> None:
        """停止 WebSocket 并 join 监听线程（供 daemon 优雅退出）。

        SDK 若有 stop/close 等接口则调用，否则会依赖线程在进程退出时被回收。
        """
        ws = self._ws_client
        th = self._ws_thread
        if ws is not None:
            for name in ("stop", "close", "interrupt"):
                fn = getattr(ws, name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception as e:
                        print(f"[listener] shutdown {name}(): {e}", flush=True)
                    break
        if th is not None and th.is_alive():
            th.join(timeout=timeout)
            if th.is_alive():
                print(
                    f"[listener] WebSocket 线程未在 {timeout}s 内退出（可能仍可被 launchd 强杀）",
                    flush=True,
                )
