"""飞书客户端：直接调用飞书开放平台 REST API，支持发送和读取消息。

认证方式：app_id + app_secret 获取 tenant_access_token（有效期 2 小时，自动刷新）。
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx


FEISHU_BASE = "https://open.feishu.cn/open-apis"
TOKEN_TTL = 7000  # token 有效期（秒），官方 7200，留 200s 余量


class FeishuClient:
    """封装飞书 API 调用，自动管理 access token。"""

    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._http = httpx.Client(timeout=15.0)

    def _get_token(self) -> str:
        """获取 tenant_access_token，过期自动刷新。"""
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = self._http.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 token 获取失败：{data.get('msg')}")

        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + TOKEN_TTL
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def send_message(
        self,
        receive_id: str,
        text: str,
        receive_id_type: str = "user_id",
    ) -> dict[str, Any]:
        """发送文本消息到指定用户或群。

        Args:
            receive_id: 接收者 ID（user_id / open_id / chat_id 等）
            text: 消息文本内容
            receive_id_type: ID 类型，默认 user_id
        """
        import json as _json
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": _json.dumps({"text": text}, ensure_ascii=False),
        }
        resp = self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers=self._headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书发送消息失败：{data.get('msg')} (code={data.get('code')})")
        return data

    def get_messages(
        self,
        container_id: str,
        container_id_type: str = "chat",
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        """从指定会话拉取最近消息列表（轮询方式）。"""
        resp = self._http.get(
            f"{FEISHU_BASE}/im/v1/messages",
            params={
                "container_id_type": container_id_type,
                "container_id": container_id,
                "page_size": page_size,
                "sort_type": "ByCreateTimeDesc",
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书读取消息失败：{data.get('msg')} (code={data.get('code')})")
        items = data.get("data", {}).get("items", [])
        return items

    def get_bot_info(self) -> dict[str, Any]:
        """获取机器人自身信息，用于测试连接是否正常。"""
        resp = self._http.get(
            f"{FEISHU_BASE}/bot/v3/info",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._http.close()


# ─── 全局单例（由 CLI 初始化后注入） ─────────────────────────────────────────

_client: Optional[FeishuClient] = None


def init_client(app_id: str, app_secret: str) -> None:
    """初始化全局飞书客户端。"""
    global _client
    _client = FeishuClient(app_id=app_id, app_secret=app_secret)


def get_client() -> FeishuClient:
    if _client is None:
        raise RuntimeError("飞书客户端未初始化，请先在配置中填写 feishu.app_id 和 feishu.app_secret。")
    return _client


# ─── 工具函数（供 tools.py 注册） ────────────────────────────────────────────

def tool_feishu_send(params: dict[str, Any]) -> str:
    """工具实现：发送飞书消息。"""
    receive_id = params.get("receive_id", "").strip()
    text = params.get("text", "").strip()
    receive_id_type = params.get("receive_id_type", "user_id")

    if not receive_id:
        return "[错误] 缺少 receive_id 参数。"
    if not text:
        return "[错误] 消息内容不能为空。"

    try:
        client = get_client()
        client.send_message(receive_id=receive_id, text=text, receive_id_type=receive_id_type)
        return f"消息已发送给 {receive_id}：{text}"
    except RuntimeError as e:
        return f"[飞书错误] {e}"
    except httpx.HTTPError as e:
        return f"[网络错误] {e}"


def tool_feishu_read(params: dict[str, Any]) -> str:
    """工具实现：读取飞书消息。"""
    container_id = params.get("container_id", "").strip()
    container_id_type = params.get("container_id_type", "chat_id")
    page_size = int(params.get("page_size", 5))

    if not container_id:
        return "[错误] 缺少 container_id 参数（会话/群 ID）。"

    try:
        client = get_client()
        items = client.get_messages(
            container_id=container_id,
            container_id_type=container_id_type,
            page_size=page_size,
        )
        if not items:
            return "没有找到消息。"

        lines = [f"最近 {len(items)} 条消息："]
        for item in items:
            sender = item.get("sender", {}).get("sender_id", {}).get("user_id", "unknown")
            create_time = item.get("create_time", "")
            body = item.get("body", {})
            content = body.get("content", "")
            try:
                import json
                parsed = json.loads(content)
                text = parsed.get("text", content)
            except Exception:
                text = content
            lines.append(f"  [{create_time}] {sender}: {text}")

        return "\n".join(lines)
    except RuntimeError as e:
        return f"[飞书错误] {e}"
    except httpx.HTTPError as e:
        return f"[网络错误] {e}"


# ─── Tool Schema ──────────────────────────────────────────────────────────────

FEISHU_SEND_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "feishu_send",
        "description": "发送飞书消息给指定用户或群",
        "parameters": {
            "type": "object",
            "properties": {
                "receive_id": {
                    "type": "string",
                    "description": "接收者的 user_id、open_id 或 chat_id",
                },
                "text": {
                    "type": "string",
                    "description": "要发送的消息文本内容",
                },
                "receive_id_type": {
                    "type": "string",
                    "description": "ID 类型：user_id / open_id / chat_id，默认 user_id",
                    "enum": ["user_id", "open_id", "union_id", "email", "chat_id"],
                },
            },
            "required": ["receive_id", "text"],
        },
    },
}

FEISHU_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "feishu_read",
        "description": "读取指定飞书会话的最近消息",
        "parameters": {
            "type": "object",
            "properties": {
                "container_id": {
                    "type": "string",
                    "description": "会话 ID（chat_id）",
                },
                "container_id_type": {
                    "type": "string",
                    "description": "会话 ID 类型，默认 chat_id",
                    "enum": ["chat_id"],
                },
                "page_size": {
                    "type": "integer",
                    "description": "拉取条数，默认 5，最大 50",
                },
            },
            "required": ["container_id"],
        },
    },
}
