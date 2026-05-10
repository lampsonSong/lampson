"""Agent 单元测试（Mock LLM，不发真实请求）。"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.agent import Agent


def _make_agent(**kwargs):
    """创建一个 mock 依赖的 Agent 实例。"""
    mock_llm = MagicMock()
    mock_llm.messages = []
    mock_adapter = MagicMock()
    return Agent(llm=mock_llm, adapter=mock_adapter, **kwargs), mock_llm, mock_adapter


class TestAgentInit:
    """测试 Agent 初始化。"""

    def test_agent_init(self):
        agent, _, _ = _make_agent()
        assert agent.skills == {}
        assert agent.skill_index is None
        assert agent.max_tool_rounds == 30

    def test_agent_init_custom_max_rounds(self):
        agent, _, _ = _make_agent(max_tool_rounds=10)
        assert agent.max_tool_rounds == 10

    def test_agent_init_fallback_models(self):
        fb_llm = MagicMock()
        fb_adapter = MagicMock()
        agent, _, _ = _make_agent(fallback_models=[(fb_llm, fb_adapter)])
        assert len(agent.fallback_models) == 1


class TestAgentSetContext:
    """测试 Agent.set_context 方法。"""

    def test_set_context_calls_llm(self):
        agent, mock_llm, _ = _make_agent()
        agent.set_context()
        mock_llm.set_system_context.assert_called_once_with()


class TestAgentSwitchLLM:
    """测试 Agent.switch_llm 方法。"""

    def test_switch_llm(self):
        agent, mock_llm1, _ = _make_agent()
        mock_llm2 = MagicMock()
        mock_adapter2 = MagicMock()

        agent.switch_llm(new_llm=mock_llm2, new_adapter=mock_adapter2)

        assert agent.llm is mock_llm2
        assert agent.adapter is mock_adapter2
        mock_llm2.set_system_context.assert_called_once_with()
        mock_llm2.migrate_from.assert_called_once_with(mock_llm1)

    def test_switch_llm_with_compaction_config(self):
        from src.core.compaction import CompactionConfig

        agent, _, _ = _make_agent()
        mock_llm2 = MagicMock()
        mock_adapter2 = MagicMock()
        new_config = CompactionConfig(trigger_threshold=0.5, context_window=8000)

        agent.switch_llm(new_llm=mock_llm2, new_adapter=mock_adapter2, compaction_config=new_config)
        assert agent._compaction_config is new_config


class TestAgentRefreshTools:
    """测试 Agent.refresh_tools 方法。"""

    def test_refresh_tools(self):
        agent, _, _ = _make_agent()
        agent.refresh_tools()
        assert agent._tools is not None


class TestAgentEstimateContextTokens:
    """测试 _estimate_context_tokens 方法。"""

    def test_estimate_tokens(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.messages = [
            {"role": "system", "content": "A" * 400},
            {"role": "user", "content": "B" * 400},
        ]
        tokens = agent._estimate_context_tokens()
        assert tokens > 0

    def test_estimate_tokens_empty(self):
        agent, mock_llm, _ = _make_agent()
        mock_llm.messages = []
        tokens = agent._estimate_context_tokens()
        assert tokens == 0


class TestAgentInterrupt:
    """测试中断机制。"""

    def test_request_and_check_interrupt(self):
        from src.core.interrupt import AgentInterrupted

        agent, mock_llm, _ = _make_agent()
        mock_llm.messages = [{"role": "user", "content": "test"}]

        agent.request_interrupt()
        assert agent._interrupted is True

        with pytest.raises(AgentInterrupted):
            agent.check_interrupt()

    def test_clear_interrupt_state(self):
        agent, _, _ = _make_agent()
        agent._interrupted = True
        agent.clear_interrupt_state()
        assert agent._interrupted is False


class TestAgentMaybeCompact:
    """测试 maybe_compact 方法。"""

    def test_no_compaction_without_config(self):
        agent, _, _ = _make_agent()
        result = agent.maybe_compact()
        assert result is None


class TestAgentOnToolProgress:
    """测试 _on_tool_progress 回调。"""

    def test_on_tool_progress_with_callback(self):
        agent, _, _ = _make_agent()
        events = []
        agent.progress_callback = lambda e: events.append(e)
        agent._on_tool_progress(round_num=1, tool_name="shell", args='{"command": "ls"}', result="file1")
        assert len(events) == 1

    def test_on_tool_progress_without_callback(self):
        agent, _, _ = _make_agent()
        agent.progress_callback = None
        agent._on_tool_progress(round_num=1, tool_name="shell", args='{}', result="ok")


class TestAgentRun:
    """测试 Agent.run 方法。

    直接 mock _run_tool_loop 避免陷入复杂的内部 tool loop，
    tool loop 内部逻辑在集成测试中覆盖。
    """

    @patch("src.core.agent._session_store")
    def test_run_simple_response(self, _mock_ss):
        """测试直接返回回复。"""
        agent, mock_llm, _ = _make_agent()

        with patch.object(Agent, "_run_tool_loop", return_value="Hello!"):
            result = agent.run("Hi!")

        assert result == "Hello!"
        mock_llm.add_user_message.assert_called_once_with("Hi!")

    @patch("src.core.agent._session_store")
    def test_run_with_interrupt(self, _mock_ss):
        """测试中断时抛出 AgentInterrupted。"""
        from src.core.interrupt import AgentInterrupted

        agent, mock_llm, _ = _make_agent()

        with patch.object(Agent, "_run_tool_loop", side_effect=AgentInterrupted(progress_summary="interrupted")):
            with pytest.raises(AgentInterrupted):
                agent.run("Do something")

    @patch("src.core.agent._session_store")
    def test_run_returns_string(self, _mock_ss):
        """测试 run 返回字符串。"""
        agent, _, _ = _make_agent()

        with patch.object(Agent, "_run_tool_loop", return_value="response text"):
            result = agent.run("test")

        assert isinstance(result, str)
        assert result == "response text"

    @patch("src.core.agent._session_store")
    def test_run_adds_user_message(self, _mock_ss):
        """测试 run 把用户输入加入消息历史。"""
        agent, mock_llm, _ = _make_agent()

        with patch.object(Agent, "_run_tool_loop", return_value="ok"):
            agent.run("my input")

        mock_llm.add_user_message.assert_called_once_with("my input")


class TestAgentRunToolLoop:
    """测试 _run_tool_loop 的核心循环逻辑。

    通过 mock _chat_with_fallback（内部第一步）来控制循环，
    避免触发真实的 LLM 调用和 trace 写入。
    """

    def test_tool_loop_returns_on_no_tool_calls(self):
        """LLM 不返回工具调用时直接结束。"""
        agent, mock_llm, mock_adapter = _make_agent()
        mock_llm.messages = [{"role": "system", "content": "S"}]

        # mock parse_response 返回无工具调用
        mock_parsed = MagicMock()
        mock_parsed.content = "final answer"
        mock_parsed.tool_calls = []
        mock_parsed.finish_reason = "stop"
        mock_adapter.parse_response.return_value = mock_parsed

        # mock _chat_with_fallback 返回一个 response
        mock_resp = MagicMock()
        mock_resp.usage.total_tokens = 50
        mock_resp.usage.prompt_tokens = 50
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.model_dump.return_value = {"role": "assistant", "content": "final answer"}

        with patch.object(Agent, "_chat_with_fallback", return_value=mock_resp), \
             patch("src.core.agent.tool_registry.validate_tool_schema", return_value=[]):
            result = agent._run_tool_loop()

        assert result == "final answer"
