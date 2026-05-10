"""task_scheduler_tool 测试：动态注册/取消/列表的 dispatch 逻辑。"""

import time
from unittest.mock import patch, MagicMock

from src.core.task_scheduler.triggers import TaskConfig, TaskType
from src.core.task_scheduler.scheduler import TaskScheduler
from src.tools.task_scheduler_tool import run_dispatch, run_schedule, run_cancel, run_list


def _make_scheduler() -> TaskScheduler:
    """创建测试用调度器。"""
    s = TaskScheduler.__new__(TaskScheduler)
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.executors.pool import ThreadPoolExecutor
    s._scheduler = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=2)},
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    return s


class TestScheduleValidation:
    """参数校验测试。"""

    def test_missing_task_id(self):
        result = run_schedule({"task_type": "interval", "module": "test_mod", "interval_seconds": 60})
        assert "task_id" in result and "错误" in result

    def test_missing_task_type(self):
        result = run_schedule({"task_id": "t1", "module": "test_mod"})
        assert "task_type" in result and "错误" in result

    def test_missing_module(self):
        result = run_schedule({"task_id": "t1", "task_type": "interval", "interval_seconds": 60})
        assert "module" in result and "错误" in result

    def test_invalid_task_type(self):
        result = run_schedule({"task_id": "t1", "task_type": "invalid", "module": "test_mod"})
        assert "不支持" in result

    def test_module_not_found(self):
        """模块不存在时先报错（优先于参数校验）。"""
        result = run_schedule({
            "task_id": "t1",
            "task_type": "delayed",
            "module": "nonexistent_module_xyz",
            "func_name": "run",
            "delay_seconds": 60,
        })
        assert "未加载" in result or "错误" in result

    def test_interval_missing_seconds(self):
        """模块解析成功后，interval 缺少秒数报错。"""
        mock_func = MagicMock()
        with patch("src.tools.task_scheduler_tool._resolve_runner", return_value=(mock_func, "")):
            result = run_schedule({"task_id": "t1", "task_type": "interval", "module": "test_mod"})
            assert "interval_seconds" in result and "错误" in result

    def test_cron_missing_both(self):
        """模块解析成功后，cron 缺少 hour/minute 报错。"""
        mock_func = MagicMock()
        with patch("src.tools.task_scheduler_tool._resolve_runner", return_value=(mock_func, "")):
            result = run_schedule({"task_id": "t1", "task_type": "cron", "module": "test_mod"})
            assert "cron" in result and "错误" in result

    def test_delayed_missing_seconds(self):
        """模块解析成功后，delayed 缺少秒数报错。"""
        mock_func = MagicMock()
        with patch("src.tools.task_scheduler_tool._resolve_runner", return_value=(mock_func, "")):
            result = run_schedule({"task_id": "t1", "task_type": "delayed", "module": "test_mod"})
            assert "delay_seconds" in result and "错误" in result


class TestDispatchRouting:
    """dispatch 路由测试。"""

    def test_unknown_action(self):
        result = run_dispatch({"action": "unknown"})
        assert "未知 action" in result

    def test_schedule_route(self):
        result = run_dispatch({"action": "schedule", "task_id": "", "task_type": "", "module": ""})
        assert "错误" in result  # 参数不全，走校验失败

    def test_cancel_route(self):
        result = run_dispatch({"action": "cancel", "task_id": ""})
        assert "错误" in result

    def test_list_route(self):
        """list 在无调度器时也能调用。"""
        with patch("src.tools.task_scheduler_tool.run_list", return_value="mocked"):
            result = run_dispatch({"action": "list"})
            assert result == "mocked"


class TestCancelNonexistent:
    """取消不存在的任务。"""

    def test_cancel_not_found(self):
        result = run_cancel({"task_id": "nonexistent_xyz"})
        assert "不存在" in result or "失败" in result


class TestListEmpty:
    """空任务列表。"""

    def test_list_empty(self):
        with patch("src.core.task_scheduler.list_tasks", return_value=[]):
            result = run_list({})
            assert "没有" in result


class TestScheduleIntegration:
    """集成测试：通过 mock learned_module 验证完整注册流程。"""

    def test_interval_schedule_success(self):
        """注册一个 interval 任务，验证返回成功。"""
        mock_func = MagicMock(return_value="ok")

        with patch("src.tools.task_scheduler_tool._resolve_runner", return_value=(mock_func, "")):
            with patch("src.core.task_scheduler.schedule") as mock_schedule:
                result = run_schedule({
                    "task_id": "test_interval_task",
                    "task_type": "interval",
                    "module": "fake_module",
                    "func_name": "run",
                    "interval_seconds": 60,
                    "description": "test interval",
                })
                assert "已注册" in result
                mock_schedule.assert_called_once()
                config = mock_schedule.call_args[0][0]
                assert config.task_id == "test_interval_task"
                assert config.task_type == TaskType.INTERVAL
                assert config.interval_seconds == 60

    def test_cron_schedule_with_day_of_week(self):
        """注册 cron 任务带 day_of_week。"""
        mock_func = MagicMock(return_value="ok")

        with patch("src.tools.task_scheduler_tool._resolve_runner", return_value=(mock_func, "")):
            with patch("src.core.task_scheduler.schedule") as mock_schedule:
                result = run_schedule({
                    "task_id": "test_cron_task",
                    "task_type": "cron",
                    "module": "fake_module",
                    "func_name": "run",
                    "cron_hour": 4,
                    "cron_minute": 0,
                    "cron_day_of_week": "mon-fri",
                    "description": "test cron",
                })
                assert "已注册" in result
                config = mock_schedule.call_args[0][0]
                assert config.cron_day_of_week == "mon-fri"

    def test_delayed_schedule_success(self):
        """注册一个 delayed 任务。"""
        mock_func = MagicMock(return_value="ok")

        with patch("src.tools.task_scheduler_tool._resolve_runner", return_value=(mock_func, "")):
            with patch("src.core.task_scheduler.schedule") as mock_schedule:
                result = run_schedule({
                    "task_id": "test_delayed_task",
                    "task_type": "delayed",
                    "module": "fake_module",
                    "func_name": "run",
                    "delay_seconds": 30,
                })
                assert "已注册" in result
                config = mock_schedule.call_args[0][0]
                assert config.trigger_seconds == 30
