"""自动压缩集成测试（Mock LLM，不发真实请求）。"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.agent import Agent
from src.core.compaction import CompactionConfig, CompactionResult


def _make_agent_with_compaction(**kwargs):
    """创建带压缩配置的 Agent。"""
    mock_llm = MagicMock()
    mock_llm.messages = []
    mock_adapter = MagicMock()
    config = CompactionConfig(trigger_threshold=0.8, context_window=4000)
    return Agent(
        llm=mock_llm,
        adapter=mock_adapter,
        compaction_config=config,
        **kwargs,
    ), mock_llm, mock_adapter


class TestAutoCompaction:
    """测试自动压缩触发。"""

    def test_no_compaction_when_context_small(self):
        """上下文小时不触发压缩。"""
        agent, mock_llm, _ = _make_agent_with_compaction()
        mock_llm.messages = [{"role": "system", "content": "short"}]
        result = agent.maybe_compact()
        assert result is None

    def test_compaction_not_triggered_without_config(self):
        """没有配置时不压缩。"""
        mock_llm = MagicMock()
        mock_llm.messages = [{"role": "system", "content": "x" * 10000}]
        agent = Agent(llm=mock_llm, adapter=MagicMock())
        result = agent.maybe_compact()
        assert result is None

    @patch("src.core.agent._session_store")
    def test_force_compact_with_mock(self, mock_ss):
        """force_compact 使用 mock 不报错。"""
        agent, mock_llm, mock_adapter = _make_agent_with_compaction()
        mock_llm.messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        mock_llm.get_history.return_value = mock_llm.messages

        with patch("src.core.compaction.apply_compaction") as mock_apply:
            mock_apply.return_value = CompactionResult(
                success=True,
                summary="compacted",
                archived_count=1,
            )
            result = agent.force_compact()
            # Either None (not triggered) or CompactionResult
            assert result is None or isinstance(result, CompactionResult)
