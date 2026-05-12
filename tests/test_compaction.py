"""Context Compaction V2 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.compaction import (
    CompactionConfig,
    CompactionResult,
    Compactor,
    Turn,
    apply_compaction,
    split_into_turns,
)


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _mock_llm(summary: str = "mock summary"):
    """创建返回固定摘要的 mock LLM。"""
    mock = MagicMock()
    resp = MagicMock()
    msg = MagicMock()
    msg.content = summary
    resp.choices = [MagicMock(message=msg)]
    mock.chat.return_value = resp
    return mock


def _mock_llm_fail():
    """创建总是抛出异常的 mock LLM。"""
    mock = MagicMock()
    mock.chat.side_effect = Exception("LLM 调用失败")
    return mock


def _msgs(*roles_and_contents):
    """构造消息列表。roles_and_contents = ("user", "hello"), ("assistant", "hi")"""
    msgs = []
    for i, (role, content) in enumerate(roles_and_contents):
        msg = {"role": role, "content": content}
        if role == "tool":
            msg["tool_call_id"] = f"call_{i:03d}"
        msgs.append(msg)
    return msgs


# ── split_into_turns 测试 ────────────────────────────────────────────────────

class TestSplitIntoTurns:
    def test_single_turn(self):
        """一轮：user → assistant"""
        msgs = _msgs(("user", "hello"), ("assistant", "hi"))
        turns = split_into_turns(msgs)
        assert len(turns) == 1
        t = turns[0]
        assert t.user_query == "hello"
        assert t.user_query_len == 5
        assert t.assistant_texts == ["hi"]
        assert t.assistant_texts_len == 2
        assert len(t.messages) == 2

    def test_multiple_turns(self):
        """多轮：每条 user 开始新的一轮"""
        msgs = _msgs(
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
            ("user", "q3"),
        )
        turns = split_into_turns(msgs)
        assert len(turns) == 3
        assert turns[0].user_query == "q1"
        assert turns[1].user_query == "q2"
        assert turns[2].user_query == "q3"

    def test_turn_with_tool_calls(self):
        """一轮中含 tool_calls + tool_result"""
        # 直接构造含 tool_calls 的 assistant 消息
        msgs = [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_001", "function": {"name": "shell", "arguments": '{"command": "ls"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_001", "content": "a.py\nb.py"},
        ]
        turns = split_into_turns(msgs)
        assert len(turns) == 1
        t = turns[0]
        # assistant 文字为空（content=""）
        assert t.assistant_texts == []
        # tool 层在 messages 里，共 3 条
        assert len(t.messages) == 3

    def test_assistant_only_text_no_tool(self):
        """assistant 只有文字回复"""
        msgs = _msgs(("user", "hi"), ("assistant", "hello world"))
        turns = split_into_turns(msgs)
        t = turns[0]
        assert t.assistant_texts == ["hello world"]
        assert t.assistant_texts_len == 11

    def test_byte_length_includes_tool_layer(self):
        """byte_length 包含 tool_calls/tool_results"""
        msgs = _msgs(
            ("user", "x"),
            ("assistant", "y"),
            ("tool", "z"),
        )
        turns = split_into_turns(msgs)
        # 3 条消息，byte_length > user_query_len + assistant_texts_len
        assert turns[0].byte_length > turns[0].user_query_len + turns[0].assistant_texts_len

    def test_empty_messages(self):
        """空列表"""
        assert split_into_turns([]) == []

    def test_assistant_no_text_only_tool_calls(self):
        """assistant 没有文字内容（只有 tool_calls）"""
        msgs = [
            {"role": "user", "content": "run it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "shell", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
        ]
        turns = split_into_turns(msgs)
        t = turns[0]
        # assistant_texts 为空，但 tool 层保留
        assert t.assistant_texts == []
        assert len(t.messages) == 3


# ── CompactionConfig 测试 ─────────────────────────────────────────────────────

class TestCompactionConfig:
    def test_default_values(self):
        cfg = CompactionConfig()
        assert cfg.trigger_threshold == 0.90
        assert cfg.context_window == 131072
        assert cfg.tail_ratio == 0.2
        assert cfg.tail_threshold == 0.5

    def test_should_trigger(self):
        cfg = CompactionConfig(context_window=1000, trigger_threshold=0.8)
        assert cfg.should_trigger(900, "end_turn") is True
        assert cfg.should_trigger(700, "end_turn") is False
        assert cfg.should_trigger(800, "end_turn") is True
        assert cfg.should_trigger(900, "tool_call") is False
        assert cfg.should_trigger(900, None) is False

    def test_custom_values(self):
        cfg = CompactionConfig(
            trigger_threshold=0.5,
            context_window=4096,
            tail_ratio=0.3,
            tail_threshold=0.6,
        )
        assert cfg.trigger_threshold == 0.5
        assert cfg.context_window == 4096
        assert cfg.tail_ratio == 0.3
        assert cfg.tail_threshold == 0.6


# ── Compactor 测试 ─────────────────────────────────────────────────────────────

class TestCompactor:
    def test_empty_messages(self):
        compactor = Compactor(llm=_mock_llm())
        result = compactor.compact([])
        assert result.success is False

    def test_too_few_turns_skips_compaction(self):
        """轮数 <= 3 不压缩"""
        msgs = _msgs(("user", "q1"), ("assistant", "a1"))
        compactor = Compactor(llm=_mock_llm())
        result = compactor.compact(msgs)
        assert result.success is True
        assert "未压缩" in result.archive_details
        assert len(result.messages_kept) == 2

    def test_strategy_a_ratio_over_50(self):
        """策略 A：tail 占比 > 50%，前段不动，后段摘要"""
        # 10 轮，最后 2 轮占 60%
        msgs = []
        for i in range(10):
            msgs.extend(_msgs((f"user", f"q{i}"), (f"assistant", f"a{i}")))
        # 使 tail 占大头：最后两轮内容更长
        msgs.extend(_msgs(("user", "x" * 500), ("assistant", "y" * 500)))
        msgs.extend(_msgs(("user", "x" * 500), ("assistant", "y" * 500)))

        mock_llm = _mock_llm("summarized")
        compactor = Compactor(llm=mock_llm)
        result = compactor.compact(msgs)
        assert result.success is True
        assert "策略A" in result.archive_details

    def test_strategy_b_ratio_under_50(self):
        """策略 B：tail 占比 <= 50%，前段合并成 summary，后段不动"""
        # 10 轮，每轮长度相近，tail 约 20%
        msgs = []
        for i in range(10):
            msgs.extend(_msgs((f"user", f"q{i}"), (f"assistant", f"a{i}" * 10)))

        mock_llm = _mock_llm("overall summary")
        compactor = Compactor(llm=mock_llm)
        result = compactor.compact(msgs)
        assert result.success is True
        assert "策略B" in result.archive_details
        # 后段原封不动
        assert any("q8" in str(m) for m in result.messages_kept)

    def test_llm_failure_preserves_original(self):
        """LLM 调用失败时保留原始消息"""
        msgs = []
        for i in range(5):
            msgs.extend(_msgs((f"user", f"q{i}"), (f"assistant", f"a{i}")))
        msgs.extend(_msgs(("user", "tail"), ("assistant", "long " * 100)))

        compactor = Compactor(llm=_mock_llm_fail(), config=CompactionConfig(tail_ratio=0.2))
        result = compactor.compact(msgs)
        assert result.success is True
        # LLM 失败，策略 B fallback 保留原始前段
        assert "q0" in str(result.messages_kept)

    def test_strategy_a_preserves_user_query(self):
        """策略 A 保留 user query，只摘要 assistant"""
        msgs = []
        for i in range(8):
            msgs.extend(_msgs((f"user", f"q{i}"), (f"assistant", f"a{i}")))
        # tail: user query 短，assistant 长
        msgs.extend(_msgs(("user", "short"), ("assistant", "long assistant reply " * 50)))

        mock_llm = _mock_llm("summarized assistant")
        compactor = Compactor(llm=mock_llm)
        result = compactor.compact(msgs)
        assert result.success is True
        # user query "short" 应该保留
        assert any(m.get("content") == "short" for m in result.messages_kept)

    def test_task_context_injected(self):
        """压缩后注入任务上下文摘要"""
        msgs = []
        for i in range(5):
            msgs.extend(_msgs((f"user", f"q{i}"), (f"assistant", f"a{i}")))
        msgs.extend(_msgs(("user", "task"), ("assistant", "task result")))

        compactor = Compactor(llm=_mock_llm("task summary"))
        result = compactor.compact(msgs)
        assert result.success is True
        # 注入了一条 task context
        has_context = any(m.get("is_task_context") for m in result.messages_kept)
        assert has_context


# ── apply_compaction 测试 ────────────────────────────────────────────────────

class TestApplyCompaction:
    def test_no_trigger_below_threshold(self):
        cfg = CompactionConfig(context_window=100000, trigger_threshold=0.9)
        mock_llm = MagicMock()
        mock_llm.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        result = apply_compaction(
            agent_llm=mock_llm,
            config=cfg,
            estimated_tokens=1000,
            stop_reason="end_turn",
        )
        # 没超过阈值，不触发
        assert result is None

    def test_force_compact_triggers(self):
        """force=True 无视阈值"""
        cfg = CompactionConfig(context_window=100000, trigger_threshold=0.9)
        mock_llm = MagicMock()
        mock_llm.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        result = apply_compaction(
            agent_llm=mock_llm,
            config=cfg,
            estimated_tokens=100,
            force=True,
        )
        assert result is not None
        assert result.success is True

    def test_system_message_preserved(self):
        """压缩后 system prompt 保留"""
        cfg = CompactionConfig()
        mock_llm = MagicMock()
        mock_llm.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "q0"},
            {"role": "assistant", "content": "a0"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "q4"},
            {"role": "assistant", "content": "a4"},
            {"role": "user", "content": "q5"},
            {"role": "assistant", "content": "a5"},
        ]
        result = apply_compaction(
            agent_llm=mock_llm,
            config=cfg,
            estimated_tokens=200000,
            force=True,
        )
        assert result is not None
        assert mock_llm.messages[0]["role"] == "system"
        assert mock_llm.messages[0]["content"] == "system prompt"
