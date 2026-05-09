"""飞书客户端单元测试（Mock HTTP，不发真实请求）。"""

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.feishu.client import (
    FeishuClient,
    tool_feishu_send,
    tool_feishu_read,
    _detect_id_type,
    init_client,
    get_client,
)


def _mock_client():
    """创建一个 mock HTTP 的 FeishuClient。"""
    client = FeishuClient.__new__(FeishuClient)
    client.app_id = "test_app_id"
    client.app_secret = "test_app_secret"
    client._token = "fake_token"
    client._token_expires_at = 9999999999.0
    client._http = MagicMock()
    return client


class TestFeishuClientSendMessage:
    """测试 send_message 方法。"""

    def test_send_message_success(self):
        """测试发送文本消息成功。"""
        client = _mock_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"message_id": "msg_1"}}
        client._http.post.return_value = mock_resp

        result = client.send_message(receive_id="ou_123", text="hello")
        assert result["code"] == 0
        client._http.post.assert_called_once()

    def test_send_message_failure(self):
        """测试发送失败抛出异常。"""
        client = _mock_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.raise_for_status.side_effect = Exception("HTTP 400")
        client._http.post.return_value = mock_resp

        with pytest.raises(Exception, match="HTTP 400"):
            client.send_message(receive_id="ou_123", text="hello")


class TestFeishuClientSendCard:
    """测试 send_card 方法。"""

    def test_send_card_success(self):
        """测试发送卡片成功。"""
        client = _mock_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"message_id": "msg_2"}}
        client._http.post.return_value = mock_resp

        card = client.build_form_card(title="Test", fields=[{"label": "K", "value": "V"}])
        result = client.send_card(receive_id="ou_123", card=card)
        assert result["code"] == 0

    def test_send_card_failure(self):
        """测试卡片发送失败。"""
        client = _mock_client()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500")
        client._http.post.return_value = mock_resp

        with pytest.raises(Exception):
            client.send_card(receive_id="ou_123", card={})


class TestFeishuClientBuildCards:
    """测试各种卡片构建方法。"""

    def test_build_form_card(self):
        """测试表单卡片构建。"""
        client = _mock_client()
        card = client.build_form_card(
            title="标题",
            fields=[{"label": "Name", "value": "Test"}],
            header_template="green",
        )
        assert card["header"]["template"] == "green"
        assert card["header"]["title"]["content"] == "标题"

    def test_build_md_card(self):
        """测试 Markdown 卡片构建。"""
        client = _mock_client()
        card = client.build_md_card(title="标题", content="# Hello", header_template="red")
        assert card["header"]["template"] == "red"
        # 找到 markdown element
        md_elements = [e for e in card["body"]["elements"] if e.get("tag") == "markdown"]
        assert len(md_elements) == 1

    def test_build_table_card(self):
        """测试表格卡片构建。"""
        client = _mock_client()
        card = client.build_table_card(
            title="表格",
            columns=[{"title": "Col1"}],
            rows=[["val1"]],
        )
        assert card["header"]["title"]["content"] == "表格"


class TestFeishuClientGetMessages:
    """测试 get_messages 方法。"""

    def test_get_messages_success(self):
        """测试拉取消息成功。"""
        client = _mock_client()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"items": [{"body": {"content": json.dumps({"text": "hi"})}, "sender": {"sender_id": {"user_id": "u1"}}, "create_time": "123"}]},
        }
        client._http.get.return_value = mock_resp

        items = client.get_messages(container_id="oc_123")
        assert len(items) == 1


class TestDetectIdType:
    """测试 ID 类型自动检测。"""

    def test_open_id_prefix(self):
        assert _detect_id_type("ou_abc") == "open_id"

    def test_chat_id_prefix(self):
        assert _detect_id_type("oc_abc") == "chat_id"

    def test_email(self):
        assert _detect_id_type("user@example.com") == "email"

    def test_explicit_override(self):
        assert _detect_id_type("ou_abc", "chat_id") == "chat_id"


class TestToolFeishuSend:
    """测试 tool_feishu_send 工具函数。"""

    @patch("src.feishu.client.get_client")
    def test_send_text_success(self, mock_get_client):
        """测试发送文本消息工具调用。"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = tool_feishu_send({
            "receive_id": "ou_123",
            "msg_type": "text",
            "text": "hello",
        })
        assert "已发送" in result
        mock_client.send_message.assert_called_once()

    @patch("src.feishu.client.get_client")
    def test_send_card_success(self, mock_get_client):
        """测试发送卡片消息工具调用。"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = tool_feishu_send({
            "receive_id": "ou_123",
            "msg_type": "card",
            "card_type": "md",
            "title": "Test",
            "content": "# Hello",
        })
        assert "已发送" in result or "卡片" in result

    def test_send_missing_receive_id(self):
        """测试缺少 receive_id。"""
        result = tool_feishu_send({"msg_type": "text", "text": "hello"})
        assert "错误" in result

    def test_send_missing_text(self):
        """测试缺少文本内容。"""
        with patch("src.feishu.client.get_client") as mock_gc:
            mock_gc.return_value = MagicMock()
            result = tool_feishu_send({"receive_id": "ou_123", "msg_type": "text"})
            assert "错误" in result


class TestToolFeishuRead:
    """测试 tool_feishu_read 工具函数。"""

    @patch("src.feishu.client.get_client")
    def test_read_success(self, mock_get_client):
        """测试读取消息成功。"""
        mock_client = MagicMock()
        mock_client.get_messages.return_value = [
            {"body": {"content": json.dumps({"text": "hi"})}, "sender": {"sender_id": {"user_id": "u1"}}, "create_time": "123"},
        ]
        mock_get_client.return_value = mock_client

        result = tool_feishu_read({"container_id": "oc_123"})
        assert "hi" in result

    def test_read_missing_container_id(self):
        """测试缺少 container_id。"""
        result = tool_feishu_read({})
        assert "错误" in result


class TestInitAndGetClient:
    """测试全局客户端初始化。"""

    def test_get_client_without_init(self):
        """未初始化时 get_client 抛异常。"""
        import src.feishu.client as mod
        old = mod._client
        mod._client = None
        try:
            with pytest.raises(RuntimeError):
                get_client()
        finally:
            mod._client = old
