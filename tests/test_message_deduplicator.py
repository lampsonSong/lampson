"""MessageDeduplicator 单元测试。"""

import threading
import time

import pytest

from src.feishu.listener import MessageDeduplicator


class TestMessageDeduplicator:
    """测试消息去重器。"""

    def test_new_message_not_duplicate(self):
        """新消息不应被标记为重复。"""
        dedup = MessageDeduplicator()
        assert dedup.is_duplicate("msg_1") is False

    def test_mark_processed_then_duplicate(self):
        """标记后再次检查应返回 True。"""
        dedup = MessageDeduplicator()
        dedup.mark_processed("msg_1")
        assert dedup.is_duplicate("msg_1") is True

    def test_different_messages_not_duplicate(self):
        """不同消息不应互相干扰。"""
        dedup = MessageDeduplicator()
        dedup.mark_processed("msg_1")
        assert dedup.is_duplicate("msg_2") is False

    def test_is_duplicate_does_not_mark(self):
        """is_duplicate 只检查不标记。"""
        dedup = MessageDeduplicator()
        dedup.is_duplicate("msg_1")
        # 未调用 mark_processed，所以不应该是 duplicate
        assert dedup.is_duplicate("msg_1") is False

    def test_mark_processed_is_idempotent(self):
        """重复标记同一消息不应出错。"""
        dedup = MessageDeduplicator()
        dedup.mark_processed("msg_1")
        dedup.mark_processed("msg_1")  # 不应抛出异常
        assert dedup.is_duplicate("msg_1") is True

    def test_ttl_expiry(self):
        """超过 TTL 的消息应不再被认为是重复。"""
        dedup = MessageDeduplicator(ttl_seconds=1)

        dedup.mark_processed("msg_1")
        assert dedup.is_duplicate("msg_1") is True

        # 等待 TTL 过期
        time.sleep(1.1)

        assert dedup.is_duplicate("msg_1") is False

    def test_max_size_eviction(self):
        """超过 max_size 时应淘汰最旧的消息。"""
        dedup = MessageDeduplicator(max_size=3)

        # 填充到上限
        for i in range(3):
            dedup.mark_processed(f"msg_{i}")

        # 所有消息都应该是已知的
        for i in range(3):
            assert dedup.is_duplicate(f"msg_{i}") is True

        # 添加第4条消息
        dedup.mark_processed("msg_3")

        # msg_0 应该被淘汰（最旧的）
        assert dedup.is_duplicate("msg_0") is False
        # msg_1, msg_2, msg_3 应该还在
        assert dedup.is_duplicate("msg_1") is True
        assert dedup.is_duplicate("msg_2") is True
        assert dedup.is_duplicate("msg_3") is True

    def test_concurrent_access(self):
        """测试并发访问安全性。"""
        dedup = MessageDeduplicator(max_size=100)

        def worker(start: int, count: int):
            for i in range(start, start + count):
                dedup.mark_processed(f"msg_{i}")
                dedup.is_duplicate(f"msg_{i}")

        threads = []
        for t_id in range(5):
            thread = threading.Thread(target=worker, args=(t_id * 20, 20))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # 所有消息都应该被正确处理
        for i in range(100):
            dedup.is_duplicate(f"msg_{i}")  # 不应抛出异常

    def test_empty_message_id(self):
        """空字符串作为 message_id 应该正常处理。"""
        dedup = MessageDeduplicator()
        dedup.mark_processed("")
        assert dedup.is_duplicate("") is True

    def test_special_characters_in_message_id(self):
        """特殊字符的 message_id 应该正常处理。"""
        dedup = MessageDeduplicator()
        special_id = "msg:with:colons/and/slashes{brackets}[brackets]"
        dedup.mark_processed(special_id)
        assert dedup.is_duplicate(special_id) is True
        assert dedup.is_duplicate("other_id") is False


class TestFeishuListenerShouldUseCard:
    """测试 _should_use_card 静态方法。"""

    def test_markdown_table(self):
        """包含 markdown 表格应使用卡片。"""
        from src.feishu.listener import FeishuListener

        text = "| Column1 | Column2 |\n| --- | --- |"
        assert FeishuListener._should_use_card(text) is True

    def test_markdown_table_with_spaces(self):
        """带空格的 markdown 表格分隔符。"""
        from src.feishu.listener import FeishuListener

        text = "| Column1 | Column2 |\n| --- | --- |"
        assert FeishuListener._should_use_card(text) is True

    def test_regular_text_no_card(self):
        """普通文本不应使用卡片。"""
        from src.feishu.listener import FeishuListener

        text = "Hello, this is a regular text message without any table."
        assert FeishuListener._should_use_card(text) is False

    def test_empty_text_no_card(self):
        """空文本不应使用卡片。"""
        from src.feishu.listener import FeishuListener

        assert FeishuListener._should_use_card("") is False
        assert FeishuListener._should_use_card("   ") is False

    def test_only_pipe_characters_no_card(self):
        """只有管道符但不是表格不应使用卡片。"""
        from src.feishu.listener import FeishuListener

        text = "a | b | c"
        assert FeishuListener._should_use_card(text) is False
