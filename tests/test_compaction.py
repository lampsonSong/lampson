"""Context Compaction 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.compaction import (
    CompactionConfig,
    CompactionResult,
    Compactor,
    _sanitize,
    apply_compaction,
)


# ── CompactionConfig 测试 ─────────────────────────────────────────────────────

class TestCompactionConfig:
    def test_default_values(self):
        cfg = CompactionConfig()
        assert cfg.trigger_threshold == 0.8
        assert cfg.end_threshold == 0.3
        assert cfg.context_window == 131072
        assert cfg.max_iterations == 3
        assert cfg.enable_archive is True

    def test_should_trigger(self):
        cfg = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        assert cfg.should_trigger(900) is True
        assert cfg.should_trigger(700) is False
        assert cfg.should_trigger(800) is False  # 不超过

    def test_is_below_end_threshold(self):
        cfg = CompactionConfig(context_window=1000, end_threshold=0.3)
        assert cfg.is_below_end_threshold(200) is True
        assert cfg.is_below_end_threshold(350) is False

    def test_custom_values(self):
        cfg = CompactionConfig(
            trigger_threshold=0.5,
            end_threshold=0.2,
            context_window=4096,
            max_iterations=5,
            enable_archive=False,
        )
        assert cfg.trigger_threshold == 0.5
        assert cfg.context_window == 4096
        assert cfg.enable_archive is False


# ── _sanitize 测试 ────────────────────────────────────────────────────────────

class TestSanitize:
    def test_basic(self):
        assert _sanitize("hello world") == "hello-world"

    def test_special_chars(self):
        assert _sanitize("a/b:c*d") == "a-b-c-d"

    def test_long_name(self):
        assert len(_sanitize("a" * 100)) == 64

    def test_empty(self):
        assert _sanitize("") == ""


# ── Compactor 测试 ─────────────────────────────────────────────────────────────

def _make_mock_llm(responses: list[str]) -> MagicMock:
    """创建 mock LLM，按顺序返回指定响应。"""
    llm = MagicMock()
    llm.client.api_key = "test-key"
    llm.client.base_url = "https://api.test.com/v1"
    llm.model = "test-model"

    iter_responses = iter(responses)

    def mock_chat(**kwargs):
        raw = next(iter_responses)
        choice = MagicMock()
        choice.message.content = raw
        choice.message.tool_calls = None
        usage = MagicMock()
        usage.total_tokens = 100
        response = MagicMock()
        response.choices = [choice]
        response.usage = usage
        return response

    # LLMClient 的 chat 是实例方法
    mock_client_instance = MagicMock()
    mock_client_instance.chat = mock_chat
    mock_client_instance.messages = []

    # apply_compaction 需要的属性
    llm.messages = []

    return llm


class TestCompactor:
    def test_empty_messages(self):
        llm = MagicMock()
        compactor = Compactor(llm=llm)
        result = compactor.compact([])
        assert result.summary == ""
        assert result.success

    def test_classify_and_archive(self):
        """测试完整的分类→归档→摘要流程。"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
            {"role": "user", "content": "帮我写一个 Python 脚本，实现快速排序"},
            {"role": "assistant", "content": "好的，我来写一个快排脚本。\n\n```python\ndef quicksort(arr): ...\n```"},
            {"role": "user", "content": "加上单元测试"},
            {"role": "assistant", "content": "已添加测试用例。"},
        ]

        # Mock LLM 的三次调用：分类、归档、摘要
        classify_resp = json.dumps({
            "topic": "实现快速排序算法并添加测试",
            "project_name": "",
            "skill_name": "code-writing",
        })

        archive_resp = json.dumps({
            "classifications": [
                {"index": 0, "action": "discard", "reason": "寒暄"},
                {"index": 1, "action": "discard", "reason": "寒暄回复"},
                {"index": 2, "action": "keep", "reason": "当前问题的核心需求"},
                {"index": 3, "action": "archive", "reason": "已完成的代码实现"},
                {"index": 4, "action": "keep", "reason": "当前问题的后续需求"},
                {"index": 5, "action": "archive", "reason": "已完成的测试代码"},
            ],
            "integrated_content": "## 快速排序实现\n- 实现了 quicksort 函数\n- 添加了单元测试",
            "archive_operations": [
                {"type": "append", "target": "code-writing", "description": "新增快排实现"}
            ],
        })

        summarize_resp = json.dumps({
            "problem": "实现快速排序并添加测试",
            "constraints": ["Python 3.11+"],
            "completed": [],
            "in_progress": ["快排脚本编写"],
            "blocked": [],
            "decisions": [],
            "pending": [],
            "key_files": [],
        })

        # 创建 mock LLM，临时客户端按顺序返回三个响应
        llm = MagicMock()
        llm.client.api_key = "test-key"
        llm.client.base_url = "https://api.test.com/v1"
        llm.model = "test-model"
        llm.messages = []

        responses = [classify_resp, archive_resp, summarize_resp]
        resp_iter = iter(responses)

        with patch("src.core.compaction.LLMClient") as MockLLM:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=next(resp_iter)))]
            )
            mock_instance.messages = []
            MockLLM.return_value = mock_instance

            compactor = Compactor(llm=llm, config=CompactionConfig(enable_archive=False))
            result = compactor.compact(messages)

        assert result.success
        assert "快排" in result.summary or "排序" in result.summary or "问题" in result.summary

    def test_emergency_summary_on_failure(self):
        """LLM 调用失败时返回紧急摘要。"""
        llm = MagicMock()
        llm.client.api_key = "test-key"
        llm.client.base_url = "https://api.test.com/v1"
        llm.model = "test-model"
        llm.messages = []

        with patch("src.core.compaction.LLMClient") as MockLLM:
            mock_instance = MagicMock()
            mock_instance.chat.side_effect = RuntimeError("API 故障")
            mock_instance.messages = []
            MockLLM.return_value = mock_instance

            messages = [{"role": "user", "content": "测试消息"}]
            compactor = Compactor(llm=llm)
            result = compactor.compact(messages)

        assert result.error is not None
        assert "测试消息" in result.summary


# ── apply_compaction 集成测试 ─────────────────────────────────────────────────

class TestApplyCompaction:
    def test_no_trigger_below_threshold(self):
        """token 未达阈值，不触发压缩。"""
        llm = MagicMock()
        llm.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "你好"},
        ]
        cfg = CompactionConfig(context_window=100000, trigger_threshold=0.8)
        result = apply_compaction(llm, cfg, last_total_tokens=1000)
        assert result is None

    def test_trigger_and_compact(self):
        """超过阈值触发压缩。"""
        llm = MagicMock()
        llm.client.api_key = "test-key"
        llm.client.base_url = "https://api.test.com/v1"
        llm.model = "test-model"
        llm.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]

        cfg = CompactionConfig(
            context_window=100,  # 很小的窗口，容易触发
            trigger_threshold=0.5,
            enable_archive=False,
        )

        classify_resp = json.dumps({"topic": "打招呼", "project_name": "", "skill_name": ""})
        archive_resp = json.dumps({
            "classifications": [
                {"index": 0, "action": "discard", "reason": "寒暄"},
                {"index": 1, "action": "discard", "reason": "寒暄"},
            ],
            "integrated_content": "",
            "archive_operations": [],
        })
        summarize_resp = json.dumps({
            "problem": "打招呼",
            "constraints": [],
            "completed": [],
            "in_progress": [],
            "blocked": [],
            "decisions": [],
            "pending": [],
            "key_files": [],
        })

        responses = [classify_resp, archive_resp, summarize_resp]
        resp_iter = iter(responses)

        with patch("src.core.compaction.LLMClient") as MockLLM:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=next(resp_iter)))]
            )
            mock_instance.messages = []
            MockLLM.return_value = mock_instance

            result = apply_compaction(llm, cfg, last_total_tokens=200, stop_reason="stop")

        assert result is not None
        # 验证 messages 被重置（只剩 system + compaction）
        assert len(llm.messages) == 2
        assert llm.messages[0]["role"] == "system"
        assert "Context Compaction" in llm.messages[1]["content"]

    def test_no_messages_to_compact(self):
        """没有非 system 消息时不压缩。"""
        llm = MagicMock()
        llm.messages = [{"role": "system", "content": "system"}]
        cfg = CompactionConfig(context_window=100, trigger_threshold=0.5)
        result = apply_compaction(llm, cfg, last_total_tokens=200)
        assert result is None
