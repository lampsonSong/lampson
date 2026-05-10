"""TaskMetrics 单元测试。"""

import json
import tempfile
from pathlib import Path

from src.core.metrics import TaskMetrics, TaskCollector, _write_metrics, load_metrics, format_summary


def test_task_collector_basic():
    """基本流程：start → record → finish → 写入 JSONL。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    tmp.close()
    tmp_path = Path(tmp.name)

    import src.core.metrics as m
    original = m.METRICS_PATH
    m.METRICS_PATH = tmp_path

    try:
        c = TaskCollector()
        c.start(model="test-model", channel="cli", session_id="s1", input_preview="hello")
        c.record_tool_call()
        c.record_tool_call()
        c.record_tokens(500)
        c.finish(success=True)

        assert tmp_path.exists()
        data = json.loads(tmp_path.read_text().strip())
        assert data["model"] == "test-model"
        assert data["tool_call_count"] == 2
        assert data["total_tokens"] == 500
        assert data["success"] is True
        assert data["duration"] > 0
        print("✓ test_task_collector_basic")
    finally:
        m.METRICS_PATH = original
        tmp_path.unlink(missing_ok=True)


def test_task_collector_fallback():
    """记录 fallback 使用。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    tmp.close()
    tmp_path = Path(tmp.name)

    import src.core.metrics as m
    original = m.METRICS_PATH
    m.METRICS_PATH = tmp_path

    try:
        c = TaskCollector()
        c.start(model="glm-5.1")
        c.record_fallback()
        c.record_llm_error()
        c.finish(success=True)

        data = json.loads(tmp_path.read_text().strip())
        assert data["used_fallback"] is True
        assert data["llm_error"] is True
        print("✓ test_task_collector_fallback")
    finally:
        m.METRICS_PATH = original
        tmp_path.unlink(missing_ok=True)


def test_load_metrics():
    """加载多条记录。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    tmp.close()
    tmp_path = Path(tmp.name)

    import src.core.metrics as m
    original = m.METRICS_PATH
    m.METRICS_PATH = tmp_path

    try:
        for i in range(5):
            c = TaskCollector()
            c.start(model=f"model-{i}")
            c.record_tool_call()
            c.finish(success=i != 3)

        records = load_metrics(limit=3)
        assert len(records) == 3
        # 应该是最后3条
        assert records[0].model == "model-2"
        assert records[2].model == "model-4"
        print("✓ test_load_metrics")
    finally:
        m.METRICS_PATH = original
        tmp_path.unlink(missing_ok=True)


def test_format_summary():
    """格式化摘要。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    tmp.close()
    tmp_path = Path(tmp.name)

    import src.core.metrics as m
    original = m.METRICS_PATH
    m.METRICS_PATH = tmp_path

    try:
        c = TaskCollector()
        c.start(model="glm-5.1", channel="feishu")
        c.record_tool_call()
        c.record_tokens(1000)
        c.finish(success=True)

        c = TaskCollector()
        c.start(model="glm-5.1", channel="feishu")
        c.record_llm_error()
        c.finish(success=False)

        summary = format_summary()
        assert "2 轮" in summary
        assert "50%" in summary  # 1/2 success
        assert "glm-5.1" in summary
        print("✓ test_format_summary")
        print(summary)
    finally:
        m.METRICS_PATH = original
        tmp_path.unlink(missing_ok=True)


def test_empty_metrics():
    """无数据时的摘要。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    tmp.close()
    tmp_path = Path(tmp.name)

    import src.core.metrics as m
    original = m.METRICS_PATH
    m.METRICS_PATH = tmp_path

    try:
        summary = format_summary()
        assert "暂无" in summary
        print("✓ test_empty_metrics")
    finally:
        m.METRICS_PATH = original
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_task_collector_basic()
    test_task_collector_fallback()
    test_load_metrics()
    test_format_summary()
    test_empty_metrics()
    print("\n全部通过 ✓")
