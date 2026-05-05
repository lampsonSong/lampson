"""Context Compaction 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.compaction import (
    CompactionConfig,
    CompactionResult,
    Compactor,
    _build_remaining_messages,
    _estimate_messages_tokens,
    _split_recent_turns,
    apply_compaction,
)


# ── CompactionConfig 测试 ─────────────────────────────────────────────────────


class TestCompactionConfig:
    def test_default_values(self):
        cfg = CompactionConfig()
        assert cfg.trigger_threshold == 0.8
        assert cfg.context_window == 131072
        assert cfg.max_archive_per_compaction == 20
        assert cfg.keep_recent_n == 8
        assert cfg.summary_trigger_ratio == 0.5

    def test_should_trigger(self):
        cfg = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        assert cfg.should_trigger(900, "end_turn") is True
        assert cfg.should_trigger(700, "end_turn") is False
        assert cfg.should_trigger(800, "end_turn") is True  # 刚好等于阈值
        assert cfg.should_trigger(900, "tool_call") is False  # 不在白名单
        assert cfg.should_trigger(900, None) is False

    def test_custom_values(self):
        cfg = CompactionConfig(
            trigger_threshold=0.5,
            context_window=4096,
            max_archive_per_compaction=5,
            keep_recent_n=5,
            summary_trigger_ratio=0.3,
        )
        assert cfg.trigger_threshold == 0.5
        assert cfg.context_window == 4096
        assert cfg.max_archive_per_compaction == 5
        assert cfg.keep_recent_n == 5
        assert cfg.summary_trigger_ratio == 0.3


# ── _split_recent_turns 测试 ─────────────────────────────────────────────────


class TestSplitRecentTurns:
    def test_all_recent(self):
        """消息少于 keep_recent_n 时全部返回为 recent。"""
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        recent, older = _split_recent_turns(messages, 3)
        assert len(recent) == 2
        assert older == []

    def test_split(self):
        """正确分割最近 N 轮和其余。"""
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
        ]
        recent, older = _split_recent_turns(messages, 2)
        # 最近 2 轮 = 最后 2 个 user 起的消息
        assert len(recent) == 4  # q2+a2, q3+a3
        assert len(older) == 2  # q1+a1
        assert older[0]["content"] == "q1"
        assert recent[0]["content"] == "q2"

    def test_exactly_n_turns(self):
        """消息刚好 N 轮时全部为 recent。"""
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        recent, older = _split_recent_turns(messages, 2)
        assert len(recent) == 4
        assert older == []


# ── _build_remaining_messages 测试 ───────────────────────────────────────────


class TestBuildRemainingMessages:
    def test_keep_all(self):
        """全部 keep 时保留所有。"""
        messages = [
            {"id": "m1", "role": "user", "content": "q1"},
            {"id": "m2", "role": "assistant", "content": "a1"},
        ]
        decisions = [
            {"msg_id": "m1", "action": "keep", "reason": "重要"},
            {"msg_id": "m2", "action": "keep", "reason": "重要"},
        ]
        result = _build_remaining_messages(messages, decisions, {})
        assert len(result) == 2

    def test_keep_plus_recent(self):
        """keep + 最近 N 条保障连贯。"""
        messages = [
            {"id": "m1", "role": "user", "content": "q1"},
            {"id": "m2", "role": "assistant", "content": "a1"},
            {"id": "m3", "role": "user", "content": "q2"},
            {"id": "m4", "role": "assistant", "content": "a2"},
            {"id": "m5", "role": "user", "content": "q3"},
            {"id": "m6", "role": "assistant", "content": "a3"},
        ]
        decisions = [
            {"msg_id": "m1", "action": "archive", "target": "skill:test", "reason": "归档"},
            {"msg_id": "m2", "action": "discard", "reason": "无用"},
            {"msg_id": "m3", "action": "keep", "reason": "重要"},
            {"msg_id": "m4", "action": "discard", "reason": "无用"},
            {"msg_id": "m5", "action": "keep", "reason": "重要"},
            {"msg_id": "m6", "action": "discard", "reason": "无用"},
        ]
        result = _build_remaining_messages(messages, decisions, {}, keep_recent_n=2)
        # m3, m5 是 keep; m5, m6 是最近 2 条
        kept_ids = {m.get("id") for m in result}
        assert "m3" in kept_ids
        assert "m5" in kept_ids
        assert "m6" in kept_ids  # 最近 2 条之一

    def test_tool_refs_kept(self):
        """tool_refs 中 action=keep 的也被保留。"""
        messages = [
            {"id": "m1", "role": "user", "content": "q1"},
            {"tool_call_id": "t1", "role": "tool", "content": "result"},
            {"id": "m2", "role": "assistant", "content": "a1"},
        ]
        decisions = [{"msg_id": "m1", "action": "discard", "reason": "-"}]
        tool_refs = {"t1": {"action": "keep", "reason": "引用过"}}
        result = _build_remaining_messages(messages, decisions, tool_refs)
        assert any(m.get("tool_call_id") == "t1" for m in result)


# ── Compactor 测试 ────────────────────────────────────────────────────────────


class TestCompactor:
    def test_empty_messages(self):
        llm = MagicMock()
        compactor = Compactor(llm=llm)
        result = compactor.compact([])
        assert result.success is False
        assert result.error == "空消息列表"

    def test_classify_failure(self):
        """LLM 分类失败时 fallback 到保留最近 N 轮。"""
        llm = MagicMock()
        llm.client.api_key = "key"
        llm.client.base_url = "https://api.test.com/v1"
        llm.model = "test-model"
        messages = [{"id": "m1", "role": "user", "content": "hello"}]

        with patch("src.core.compaction._make_temp_client") as mock_mc:
            mock_client = MagicMock()
            mock_client.chat.side_effect = RuntimeError("API 故障")
            mock_mc.return_value = mock_client

            compactor = Compactor(llm=llm)
            result = compactor.compact(messages)

        assert result.success is True  # fallback 策略成功保留最近 N 轮
        assert result.archive_details == "fallback: classify failed, kept recent turns"


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
        result = apply_compaction(llm, cfg, estimated_tokens=1000)
        assert result is None

    def test_no_messages_to_compact(self):
        """没有非 system 消息时不压缩。"""
        llm = MagicMock()
        llm.messages = [{"role": "system", "content": "system"}]
        cfg = CompactionConfig(context_window=100, trigger_threshold=0.5)
        result = apply_compaction(llm, cfg, estimated_tokens=200)
        assert result is None

    def test_force_compact(self):
        """force=True 忽略阈值直接压缩。"""
        llm = MagicMock()
        llm.client.api_key = "key"
        llm.client.base_url = "https://api.test.com/v1"
        llm.model = "test-model"
        llm.messages = [
            {"role": "system", "content": "sys"},
            {"id": "m1", "role": "user", "content": "hi"},
            {"id": "m2", "role": "assistant", "content": "hello"},
        ]

        classify_result = {
            "decisions": [
                {"msg_id": "m1", "action": "keep", "reason": "重要"},
                {"msg_id": "m2", "action": "keep", "reason": "重要"},
            ],
            "tool_refs": {},
        }

        with patch("src.core.compaction._make_temp_client") as mock_mc:
            mock_client = MagicMock()
            resp_choice = MagicMock()
            resp_choice.message.content = json.dumps(classify_result)
            mock_client.chat.return_value = MagicMock(choices=[resp_choice])
            mock_client.messages = []
            mock_mc.return_value = mock_client

            cfg = CompactionConfig(
                context_window=1_000_000,  # 大窗口，正常不会触发
                trigger_threshold=0.8,
            )
            result = apply_compaction(
                llm, cfg, estimated_tokens=100, force=True
            )

        assert result is not None
        assert result.success is True
