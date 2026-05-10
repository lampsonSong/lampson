"""TaskScheduler 测试。"""

import time
import threading
from unittest.mock import MagicMock

from src.core.task_scheduler.triggers import TaskConfig, TaskType
from src.core.task_scheduler.scheduler import TaskScheduler


def _make_scheduler() -> TaskScheduler:
    """创建测试用调度器（不持久化，避免 SQLite 文件残留）。"""
    s = TaskScheduler()  # 正常走 __init__，确保 _session 等属性初始化
    return s


class TestDelayedTask:
    def test_delayed_fires_once(self):
        s = _make_scheduler()
        result = []
        config = TaskConfig(
            task_id="test_delayed",
            task_type=TaskType.DELAYED,
            trigger_seconds=1,
            func=lambda: result.append("fired"),
        )
        s.schedule(config)
        s.start()
        time.sleep(2)
        s.shutdown(wait=False)
        assert result == ["fired"]

    def test_delayed_with_args(self):
        s = _make_scheduler()
        result = []
        config = TaskConfig(
            task_id="test_delayed_args",
            task_type=TaskType.DELAYED,
            trigger_seconds=1,
            func=lambda x, y: result.append(x + y),
            func_args={"x": 1, "y": 2},
        )
        s.schedule(config)
        s.start()
        time.sleep(2)
        s.shutdown(wait=False)
        assert result == [3]


class TestIntervalTask:
    def test_interval_fires_multiple(self):
        s = _make_scheduler()
        result = []
        config = TaskConfig(
            task_id="test_interval",
            task_type=TaskType.INTERVAL,
            interval_seconds=1,
            func=lambda: result.append(1),
        )
        s.schedule(config)
        s.start()
        time.sleep(3.5)
        s.shutdown(wait=False)
        assert len(result) >= 3

    def test_interval_cancel(self):
        s = _make_scheduler()
        result = []
        config = TaskConfig(
            task_id="test_cancel",
            task_type=TaskType.INTERVAL,
            interval_seconds=1,
            func=lambda: result.append(1),
        )
        s.schedule(config)
        s.start()
        time.sleep(1.5)
        assert s.cancel("test_cancel") is True
        count_after_cancel = len(result)
        time.sleep(2)
        s.shutdown(wait=False)
        assert len(result) == count_after_cancel


class TestCronTask:
    def test_cron_registers(self):
        s = _make_scheduler()
        config = TaskConfig(
            task_id="test_cron",
            task_type=TaskType.CRON,
            cron_hour=4,
            cron_minute=0,
            func=lambda: None,
        )
        s.schedule(config)
        s.start()
        tasks = s.list_tasks()
        assert any(t["task_id"] == "test_cron" for t in tasks)
        s.shutdown(wait=False)


class TestCallbacks:
    def test_on_done_called(self):
        s = _make_scheduler()
        done = MagicMock()
        config = TaskConfig(
            task_id="test_done",
            task_type=TaskType.DELAYED,
            trigger_seconds=1,
            func=lambda: 42,
            on_done=done,
        )
        s.schedule(config)
        s.start()
        time.sleep(2)
        s.shutdown(wait=False)
        done.assert_called_once_with(42)

    def test_on_error_called(self):
        s = _make_scheduler()
        err = MagicMock()

        def bad_func():
            raise ValueError("boom")

        config = TaskConfig(
            task_id="test_err",
            task_type=TaskType.DELAYED,
            trigger_seconds=1,
            func=bad_func,
            on_error=err,
        )
        s.schedule(config)
        s.start()
        time.sleep(2)
        s.shutdown(wait=False)
        err.assert_called_once()
        assert isinstance(err.call_args[0][0], ValueError)


class TestListTasks:
    def test_list_returns_registered(self):
        s = _make_scheduler()
        s.schedule(TaskConfig(
            task_id="t1",
            task_type=TaskType.INTERVAL,
            interval_seconds=60,
            func=lambda: None,
            description="task one",
        ))
        s.schedule(TaskConfig(
            task_id="t2",
            task_type=TaskType.CRON,
            cron_hour=4,
            func=lambda: None,
            description="task two",
        ))
        s.start()
        tasks = s.list_tasks()
        ids = [t["task_id"] for t in tasks]
        assert "t1" in ids
        assert "t2" in ids
        s.shutdown(wait=False)

    def test_replace_existing(self):
        s = _make_scheduler()
        s.schedule(TaskConfig(
            task_id="dup",
            task_type=TaskType.INTERVAL,
            interval_seconds=60,
            func=lambda: "v1",
        ))
        s.schedule(TaskConfig(
            task_id="dup",
            task_type=TaskType.INTERVAL,
            interval_seconds=120,
            func=lambda: "v2",
        ))
        s.start()
        tasks = s.list_tasks()
        dup_tasks = [t for t in tasks if t["task_id"] == "dup"]
        assert len(dup_tasks) == 1
        s.shutdown(wait=False)


class TestPromptTask:
    """测试 prompt 模式的定时任务。"""

    def test_prompt_task_injected_to_session(self):
        s = _make_scheduler()
        mock_session = MagicMock()
        mock_session.handle_input.return_value = "done"
        s.set_session(mock_session)

        config = TaskConfig(
            task_id="test_prompt",
            task_type=TaskType.DELAYED,
            trigger_seconds=1,
            prompt="检查训练状态并发飞书报告",
        )
        s.schedule(config)
        s.start()
        time.sleep(2)
        s.shutdown(wait=False)
        mock_session.handle_input.assert_called_once_with("检查训练状态并发飞书报告")

    def test_prompt_task_without_session_fails_gracefully(self):
        s = _make_scheduler()
        # 不设置 session
        config = TaskConfig(
            task_id="test_no_session",
            task_type=TaskType.DELAYED,
            trigger_seconds=1,
            prompt="应该失败",
        )
        s.schedule(config)
        s.start()
        time.sleep(2)
        s.shutdown(wait=False)
        # 不崩溃就算通过

    def test_func_and_prompt_exclusive(self):
        """func 和 prompt 不能都不填。"""
        import pytest
        with pytest.raises(ValueError, match="必须指定 func 或 prompt"):
            TaskConfig(
                task_id="bad",
                task_type=TaskType.INTERVAL,
                interval_seconds=60,
            )
