"""测试 FeishuAdapter - 飞书平台适配器"""
import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestMessageDeduplicator:
    """消息去重器测试"""

    def test_first_message_not_duplicate(self):
        """测试首次消息不是重复"""
        from src.platforms.adapters.feishu import MessageDeduplicator
        
        dedup = MessageDeduplicator()
        assert dedup.is_duplicate("msg1") is False

    def test_duplicate_message_detected(self):
        """测试重复消息被检测"""
        from src.platforms.adapters.feishu import MessageDeduplicator
        
        dedup = MessageDeduplicator()
        dedup.mark_processed("msg1")
        
        assert dedup.is_duplicate("msg1") is True
        assert dedup.is_duplicate("msg2") is False

    def test_ttl_expiration(self):
        """测试 TTL 过期"""
        from src.platforms.adapters.feishu import MessageDeduplicator
        
        # 使用极短的 TTL
        dedup = MessageDeduplicator(ttl_seconds=0)
        dedup.mark_processed("msg1")
        
        # 短暂等待后应该过期
        import time
        time.sleep(0.1)
        
        assert dedup.is_duplicate("msg1") is False

    def test_max_size_eviction(self):
        """测试容量上限驱逐"""
        from src.platforms.adapters.feishu import MessageDeduplicator
        
        dedup = MessageDeduplicator(max_size=3)
        
        # 添加 3 个消息
        for i in range(3):
            dedup.mark_processed(f"msg{i}")
        
        # 前 3 个应该都不重复
        for i in range(3):
            assert dedup.is_duplicate(f"msg{i}") is True
        
        # 添加第 4 个，最老的应该被驱逐
        dedup.mark_processed("msg3")
        assert dedup.is_duplicate("msg0") is False
        assert dedup.is_duplicate("msg3") is True


class TestFeishuAdapter:
    """FeishuAdapter 测试"""

    def test_init_requires_config(self):
        """测试初始化需要配置"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        config = {"app_id": "test_id", "app_secret": "test_secret"}
        adapter = FeishuAdapter(config)
        
        assert adapter.app_id == "test_id"
        assert adapter.app_secret == "test_secret"
        assert adapter._stopped is False

    def test_init_missing_app_id(self):
        """测试缺少 app_id 抛出异常"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        with pytest.raises(KeyError):
            FeishuAdapter({"app_secret": "test"})

    def test_init_missing_app_secret(self):
        """测试缺少 app_secret 抛出异常"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        with pytest.raises(KeyError):
            FeishuAdapter({"app_id": "test_id"})

    def test_platform_name(self):
        """测试平台名称"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        adapter = FeishuAdapter({"app_id": "id", "app_secret": "secret"})
        assert adapter.platform == "feishu"

    def test_stopped_flag(self):
        """测试停止标志"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        adapter = FeishuAdapter({"app_id": "id", "app_secret": "secret"})
        
        assert adapter._stopped is False
        adapter._stopped = True
        assert adapter._stopped is True

    def test_strip_think_tags(self):
        """测试移除 think 标签"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        adapter = FeishuAdapter({"app_id": "id", "app_secret": "secret"})
        
        # 测试标准格式
        text = "<think> some thought</think> result"
        assert adapter._strip_think_tags(text) == "result"
        
        # 测试多行格式
        text2 = "<think>\nthought\n</think> result2"
        assert adapter._strip_think_tags(text2) == "result2"
        
        # 测试无标签
        text3 = "plain text"
        assert adapter._strip_think_tags(text3) == "plain text"

    def test_should_use_card(self):
        """测试判断是否使用卡片"""
        from src.platforms.adapters.feishu import FeishuAdapter
        
        assert FeishuAdapter._should_use_card("| col1 | col2 |") is True
        assert FeishuAdapter._should_use_card("| --- | --- |") is True
        assert FeishuAdapter._should_use_card("plain text") is False
        assert FeishuAdapter._should_use_card("hello world") is False
