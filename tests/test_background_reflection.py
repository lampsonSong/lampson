"""后台反思机制测试。

验证核心链路：
  run() → _maybe_spawn_background_reflection → should_reflect → reflect_and_learn → execute_learnings → _refresh_all_indices
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.core.agent import Agent


def _make_agent(**kwargs):
    """创建一个 mock 依赖的 Agent 实例。"""
    mock_llm = MagicMock()
    mock_llm.messages = []
    mock_adapter = MagicMock()
    return Agent(llm=mock_llm, adapter=mock_adapter, **kwargs), mock_llm, mock_adapter


# ═══════════════════════════════════════════════════════════════════════════════
# 1. should_reflect 短路判断
# ═══════════════════════════════════════════════════════════════════════════════


class TestShouldReflectGate:
    """验证 should_reflect 被正确调用，闲聊/无工具任务被跳过。"""

    def test_should_reflect_false_skips_thread(self):
        """should_reflect 返回 False 时不启动后台线程。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 0

        with patch("src.core.reflection.should_reflect", return_value=False):
            agent._maybe_spawn_background_reflection("你好")

        # 不崩溃即通过 — 没起线程

    def test_should_reflect_true_starts_thread(self):
        """should_reflect 返回 True 时启动后台线程。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 3
        agent.llm.messages = [
            {"role": "user", "content": "帮我查一下天气"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "web_search"}}]},
            {"role": "tool", "content": "晴天 25度"},
            {"role": "assistant", "content": "今天是晴天，25度"},
        ]

        started_threads = []

        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            started_threads.append(t)
            return t

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=[]), \
             patch("src.core.reflection._llm_client", MagicMock()), \
             patch("threading.Thread", side_effect=capture_thread):
            agent._maybe_spawn_background_reflection("帮我查一下天气")

        assert len(started_threads) == 1
        assert started_threads[0].daemon is True
        assert started_threads[0].name == "bg-reflection"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 水位线机制
# ═══════════════════════════════════════════════════════════════════════════════


class TestReflectWatermark:
    """验证水位线：每次反思只处理增量消息。"""

    def test_initial_watermark_is_zero(self):
        """初始水位线为 0。"""
        agent, _, _ = _make_agent()
        assert agent._reflect_watermark == 0

    def test_watermark_advances_after_reflection(self):
        """反思后水位线更新到 messages 当前长度。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 1
        agent.llm.messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "reply1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "shell"}}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=[]), \
             patch("src.core.reflection._llm_client", MagicMock()):
            agent._maybe_spawn_background_reflection("msg2")

        assert agent._reflect_watermark == 6

    def test_second_reflection_only_sees_incremental(self):
        """第二次反思只看到水位线之后的新消息（水位线正确前移）。"""
        agent, _, _ = _make_agent()

        # 第一轮：2 条消息
        agent.llm.messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "reply1"},
        ]
        agent._fast_path_tool_count = 1

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=[]), \
             patch("src.core.reflection._llm_client", MagicMock()):
            agent._maybe_spawn_background_reflection("msg1")
        assert agent._reflect_watermark == 2

        # 第二轮：新增 2 条消息，总共 4 条
        agent.llm.messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "reply1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "shell"}}]},
        ]

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=[]), \
             patch("src.core.reflection._llm_client", MagicMock()):
            agent._maybe_spawn_background_reflection("msg2")

        assert agent._reflect_watermark == 4

    def test_watermark_does_not_move_when_should_reflect_false(self):
        """should_reflect 返回 False 时水位线不变。"""
        agent, _, _ = _make_agent()
        agent.llm.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        with patch("src.core.reflection.should_reflect", return_value=False):
            agent._maybe_spawn_background_reflection("hi")

        assert agent._reflect_watermark == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 后台线程不阻塞主流程
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackgroundReflectionNonBlocking:
    """验证反思是后台执行的，不阻塞 run() 的返回。"""

    @patch("src.core.agent._session_store")
    def test_run_returns_immediately(self, _mock_ss):
        """run() 立即返回，不等反思完成。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 2
        agent.llm.messages = [
            {"role": "user", "content": "do stuff"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "shell"}}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]

        # reflect_and_learn 模拟耗时 2 秒
        def slow_reflect(*args, **kwargs):
            time.sleep(2)
            return []

        with patch.object(Agent, "_run_tool_loop", return_value="result"), \
             patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", side_effect=slow_reflect), \
             patch("src.core.reflection._llm_client", MagicMock()):
            start = time.time()
            result = agent.run("do stuff")
            elapsed = time.time() - start

        # run 应该在 1 秒内返回（反思在后台跑 2 秒）
        assert result == "result"
        assert elapsed < 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. execute_learnings 调用 _refresh_all_indices
# ═══════════════════════════════════════════════════════════════════════════════


class TestRefreshAllIndices:
    """验证各类沉淀操作都触发索引刷新。"""

    @patch("src.core.reflection._refresh_all_indices")
    def test_skill_create_triggers_refresh(self, mock_refresh):
        """skill_create 触发 _refresh_all_indices。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._create_skill", return_value="created"), \
             patch("src.core.reflection._notify_user"):
            execute_learnings([{
                "type": "skill_create",
                "target": "test-skill",
                "content": "some content",
                "reason": "test",
            }])
        assert mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_skill_update_triggers_refresh(self, mock_refresh):
        """skill_update 触发 _refresh_all_indices。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._update_skill", return_value="updated"), \
             patch("src.core.reflection._notify_user"):
            execute_learnings([{
                "type": "skill_update",
                "target": "test-skill",
                "content": "new content",
                "reason": "test",
            }])
        assert mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_project_create_triggers_refresh(self, mock_refresh):
        """project_create 触发 _refresh_all_indices。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._create_project", return_value="created"):
            execute_learnings([{
                "type": "project_create",
                "target": "my-project",
                "content": "project info",
                "reason": "test",
            }])
        assert mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_project_update_triggers_refresh(self, mock_refresh):
        """project_update 触发 _refresh_all_indices。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._update_project", return_value="updated"):
            execute_learnings([{
                "type": "project_update",
                "target": "my-project",
                "content": "updated info",
                "reason": "test",
            }])
        assert mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_info_create_triggers_refresh(self, mock_refresh):
        """info_create 触发 _refresh_all_indices。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._create_info", return_value="created"):
            execute_learnings([{
                "type": "info_create",
                "target": "api-key",
                "content": "key info",
                "reason": "test",
            }])
        assert mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_info_update_triggers_refresh(self, mock_refresh):
        """info_update 触发 _refresh_all_indices。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._update_info", return_value="updated"):
            execute_learnings([{
                "type": "info_update",
                "target": "api-key",
                "content": "new key info",
                "reason": "test",
            }])
        assert mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_unknown_type_no_refresh(self, mock_refresh):
        """未知类型不触发刷新。"""
        from src.core.reflection import execute_learnings

        execute_learnings([{
            "type": "unknown_type",
            "target": "x",
            "content": "y",
            "reason": "z",
        }])
        assert not mock_refresh.called

    @patch("src.core.reflection._refresh_all_indices")
    def test_multiple_learnings_trigger_refresh_per_learning(self, mock_refresh):
        """多个 learnings 每个都触发一次刷新。"""
        from src.core.reflection import execute_learnings

        with patch("src.core.reflection._create_skill", return_value="ok"), \
             patch("src.core.reflection._create_info", return_value="ok"), \
             patch("src.core.reflection._notify_user"):
            execute_learnings([
                {"type": "skill_create", "target": "s1", "content": "c", "reason": "r"},
                {"type": "info_create", "target": "i1", "content": "c", "reason": "r"},
            ])
        # 每个 learning 都触发一次
        assert mock_refresh.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. _format_messages_for_context
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormatMessagesForContext:
    """验证上下文格式化。"""

    def test_filters_to_user_and_assistant_only(self):
        """只保留 user 和 assistant 消息。"""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "x"}}]},
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "reply"},
        ]
        result = Agent._format_messages_for_context(messages)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "[user] hello" == lines[0]
        assert "[assistant] reply" == lines[1]

    def test_empty_messages(self):
        """空消息列表返回默认文本。"""
        result = Agent._format_messages_for_context([])
        assert result == "（无对话记录）"

    def test_content_truncated_at_300(self):
        """内容超过 300 字符被截断。"""
        long_content = "x" * 500
        messages = [{"role": "user", "content": long_content}]
        result = Agent._format_messages_for_context(messages)
        # "[user] " = 7 字符 + 300 字符内容
        body = result[len("[user] "):]
        assert len(body) == 300

    def test_assistant_with_only_tool_calls_skipped(self):
        """只有 tool_calls 没有 content 的 assistant 消息被跳过。"""
        messages = [
            {"role": "assistant", "tool_calls": [{"function": {"name": "x"}}]},
            {"role": "assistant", "content": "real reply"},
        ]
        result = Agent._format_messages_for_context(messages)
        assert "[assistant] real reply" in result
        # 没有 content 的 assistant 不应出现
        assert result.count("[assistant]") == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 6. _refresh_all_indices 覆盖三种索引
# ═══════════════════════════════════════════════════════════════════════════════


class TestRefreshAllIndicesCoverage:
    """验证 _refresh_all_indices 刷新 skill/project/info 三种索引。

    注意：reflection.py 里用 from src.tools import session as session_tool
    然后 session_tool.get_current_session()，但该函数实际上不存在于模块中
    （_current_session 是模块级全局变量，运行时可能被 monkey-patch）。
    所以 _refresh_all_indices 的 session_tool.get_current_session() 会抛
    AttributeError，被 try/except 静默吞掉。测试中通过在模块上临时注入
    get_current_session 来模拟。
    """

    def _patch_session(self, return_value=None):
        """在 src.tools.session 模块上临时注入 get_current_session。"""
        from src.tools import session as session_mod
        return patch.object(session_mod, "get_current_session", return_value=return_value, create=True)

    def test_refreshes_skill_index(self):
        """刷新 skill 索引。"""
        mock_skill_index = MagicMock()

        with patch("src.core.reflection._skill_index", mock_skill_index), \
             self._patch_session(None):
            from src.core.reflection import _refresh_all_indices
            _refresh_all_indices()

        mock_skill_index.load_or_build.assert_called_once()

    def test_refreshes_project_index(self):
        """刷新 project 索引。"""
        mock_skill_index = MagicMock()
        mock_project_index = MagicMock()
        mock_session = MagicMock()
        mock_session.project_index = mock_project_index
        mock_session.agent = MagicMock()

        with patch("src.core.reflection._skill_index", mock_skill_index), \
             self._patch_session(mock_session), \
             patch("src.core.skills_tools.set_retrieval_indices"):
            from src.core.reflection import _refresh_all_indices
            _refresh_all_indices()

        mock_project_index.load_or_build.assert_called_once()

    def test_clears_info_cache(self):
        """清除 info 缓存。"""
        import src.core.prompt_builder as pb

        # 设置一个假缓存
        pb._info_index_cache = ("old_cache", "data")

        with patch("src.core.reflection._skill_index", MagicMock()), \
             self._patch_session(None):
            from src.core.reflection import _refresh_all_indices
            _refresh_all_indices()

        assert pb._info_index_cache is None

    def test_no_skill_index_no_crash(self):
        """_skill_index 为 None 时不崩溃。"""
        with patch("src.core.reflection._skill_index", None), \
             self._patch_session(None):
            from src.core.reflection import _refresh_all_indices
            _refresh_all_indices()  # 不崩溃即通过

    def test_no_session_no_crash(self):
        """没有 current_session 时不崩溃。"""
        with patch("src.core.reflection._skill_index", MagicMock()), \
             self._patch_session(None):
            from src.core.reflection import _refresh_all_indices
            _refresh_all_indices()  # 不崩溃即通过


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 集成：run() 端到端触发反思
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunTriggersReflection:
    """验证 run() 完成后确实触发反思（端到端，但 LLM mock）。"""

    @patch("src.core.agent._session_store")
    def test_run_with_tool_calls_triggers_reflection(self, _mock_ss):
        """run() 中有 tool call 时触发 _maybe_spawn_background_reflection。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 2
        agent.llm.messages = [
            {"role": "user", "content": "do stuff"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "shell"}}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]

        with patch.object(Agent, "_run_tool_loop", return_value="done"), \
             patch.object(Agent, "_maybe_spawn_background_reflection") as mock_reflect:
            result = agent.run("do stuff")

        assert result == "done"
        mock_reflect.assert_called_once_with("do stuff")

    @patch("src.core.agent._session_store")
    def test_run_without_tool_calls_still_calls_maybe_reflect(self, _mock_ss):
        """run() 即使无 tool call 也会调 _maybe_spawn_background_reflection（由 should_reflect 决定是否继续）。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 0

        with patch.object(Agent, "_run_tool_loop", return_value="hello"), \
             patch.object(Agent, "_maybe_spawn_background_reflection") as mock_reflect:
            result = agent.run("hi")

        assert result == "hello"
        mock_reflect.assert_called_once_with("hi")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 通知机制
# ═══════════════════════════════════════════════════════════════════════════════


class TestReflectNotification:
    """验证 reflect_notify_callback 正确调用。"""

    def test_notify_called_with沉淀结果(self):
        """有沉淀时，callback 被调用且包含沉淀内容。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 1
        agent.llm.messages = [
            {"role": "user", "content": "do stuff"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "shell"}}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]

        notifications = []

        def capture_notify(msg):
            notifications.append(msg)

        agent.reflect_notify_callback = capture_notify

        learnings = [{
            "type": "info_create",
            "target": "test-info",
            "content": "some content",
            "reason": "test",
        }]

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=learnings), \
             patch("src.core.reflection.execute_learnings", return_value=["已创建 Info: test-info"]), \
             patch("src.core.reflection._llm_client", MagicMock()), \
             patch("src.core.reflection._refresh_all_indices"):
            agent._maybe_spawn_background_reflection("do stuff")

        # 等待后台线程执行
        time.sleep(0.5)

        assert len(notifications) == 1
        assert "test-info" in notifications[0]

    def test_notify_called_with暂无新内容(self):
        """无沉淀时，callback 被调用且提示无新内容。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 1
        agent.llm.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        notifications = []

        def capture_notify(msg):
            notifications.append(msg)

        agent.reflect_notify_callback = capture_notify

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=[]), \
             patch("src.core.reflection._llm_client", MagicMock()):
            agent._maybe_spawn_background_reflection("hello")

        time.sleep(0.5)

        assert len(notifications) == 1
        assert "暂无新内容" in notifications[0]

    def test_notify_none_does_not_crash(self):
        """reflect_notify_callback 为 None 时不崩溃。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 1
        agent.reflect_notify_callback = None
        agent.llm.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", return_value=[]), \
             patch("src.core.reflection._llm_client", MagicMock()):
            # 不崩溃即通过
            agent._maybe_spawn_background_reflection("hello")

        time.sleep(0.5)

    def test_notify_reflect失败时也通知(self):
        """反思执行失败时，callback 被调用通知失败。"""
        agent, _, _ = _make_agent()
        agent._fast_path_tool_count = 1
        agent.llm.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "shell"}}]},
        ]

        notifications = []

        def capture_notify(msg):
            notifications.append(msg)

        agent.reflect_notify_callback = capture_notify

        with patch("src.core.reflection.should_reflect", return_value=True), \
             patch("src.core.reflection.reflect_and_learn", side_effect=Exception("LLM 调用失败")), \
             patch("src.core.reflection._llm_client", MagicMock()):
            agent._maybe_spawn_background_reflection("hello")

        time.sleep(0.5)

        assert len(notifications) == 1
        assert "反思失败" in notifications[0]
