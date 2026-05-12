"""自我评估与指标追踪模块。

每轮任务完成后记录关键指标到 JSONL，为后续自学习提供数据基础。
支持 /metrics 命令查看统计摘要。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.core.config import LAMIX_DIR

logger = logging.getLogger(__name__)

METRICS_PATH = LAMIX_DIR / "metrics.jsonl"


@dataclass
class TaskMetrics:
    """单轮任务的指标记录。"""

    # 时间戳（秒）
    timestamp: float = 0.0
    # 耗时（秒）
    duration: float = 0.0
    # 使用的模型名
    model: str = ""
    # 工具调用次数
    tool_call_count: int = 0
    # 总 token 消耗（prompt + completion）
    total_tokens: int = 0
    # 是否成功完成（无错误、无中断）
    success: bool = True
    # 是否被用户中断（新消息抢占）
    interrupted: bool = False
    # 是否发生 LLM 错误（含熔断）
    llm_error: bool = False
    # 是否使用了 fallback 模型
    used_fallback: bool = False
    # 是否触发了上下文压缩
    compacted: bool = False
    # 压缩次数（单轮内可能多次压缩）
    compaction_count: int = 0
    # 用户输入前 100 字（用于分类，不记全文）
    input_preview: str = ""
    # 渠道（cli / feishu）
    channel: str = ""
    # session_id
    session_id: str = ""


@dataclass
class _TaskTimer:
    """任务计时器，配合 TaskCollector 使用。"""

    start_time: float = 0.0

    def start(self) -> None:
        self.start_time = time.time()

    def elapsed(self) -> float:
        if not self.start_time:
            return 0.0
        return time.time() - self.start_time


class TaskCollector:
    """收集一轮任务的指标，完成后写入 JSONL。

    使用方式：
        collector = TaskCollector()
        collector.start(model="deepseek-v4-flash", channel="feishu", ...)
        # ... 任务执行中 ...
        collector.record_tool_call()
        collector.finish(success=True)
    """

    def __init__(self) -> None:
        self._metrics = TaskMetrics()
        self._timer = _TaskTimer()

    def start(
        self,
        model: str = "",
        channel: str = "",
        session_id: str = "",
        input_preview: str = "",
    ) -> None:
        """开始一轮任务计时。"""
        self._metrics = TaskMetrics(
            model=model,
            channel=channel,
            session_id=session_id,
            input_preview=input_preview[:100],
        )
        self._timer.start()

    def record_tool_call(self) -> None:
        self._metrics.tool_call_count += 1

    def record_tokens(self, total: int) -> None:
        self._metrics.total_tokens += total

    def record_fallback(self) -> None:
        self._metrics.used_fallback = True

    def record_llm_error(self) -> None:
        self._metrics.llm_error = True

    def record_compaction(self) -> None:
        self._metrics.compacted = True
        self._metrics.compaction_count += 1

    def record_interrupt(self) -> None:
        self._metrics.interrupted = True

    def finish(self, success: bool = True) -> TaskMetrics:
        """结束计时并写入 JSONL，返回指标对象。"""
        self._metrics.timestamp = time.time()
        self._metrics.duration = self._timer.elapsed()
        self._metrics.success = success

        if not self._metrics.success:
            self._metrics.llm_error = True

        _write_metrics(self._metrics)
        return self._metrics


def _write_metrics(m: TaskMetrics) -> None:
    """追加一条指标记录到 JSONL。"""
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(m), ensure_ascii=False) + "\n"
        with METRICS_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"写入指标失败: {e}")


def load_metrics(limit: int = 500) -> list[TaskMetrics]:
    """加载最近 N 条指标记录（从文件尾部读）。"""
    if not METRICS_PATH.exists():
        return []
    try:
        lines = METRICS_PATH.read_text(encoding="utf-8").strip().split("\n")
        lines = [l for l in lines if l.strip()]
        # 取最后 limit 条
        lines = lines[-limit:]
        results: list[TaskMetrics] = []
        for line in lines:
            try:
                d = json.loads(line)
                results.append(TaskMetrics(**d))
            except Exception:
                continue
        return results
    except Exception:
        return []


def format_summary(limit: int = 100) -> str:
    """生成最近 N 轮的统计摘要文本（供 /metrics 命令展示）。"""
    records = load_metrics(limit=limit)
    if not records:
        return "暂无指标数据。"

    total = len(records)
    success = sum(1 for r in records if r.success)
    interrupted = sum(1 for r in records if r.interrupted)
    llm_errors = sum(1 for r in records if r.llm_error)
    fallback_used = sum(1 for r in records if r.used_fallback)
    compacted = sum(1 for r in records if r.compacted)

    total_duration = sum(r.duration for r in records)
    avg_duration = total_duration / total if total else 0
    total_tools = sum(r.tool_call_count for r in records)
    avg_tools = total_tools / total if total else 0
    total_tokens = sum(r.total_tokens for r in records)

    # 按模型统计
    model_stats: dict[str, dict[str, int | float]] = {}
    for r in records:
        name = r.model or "unknown"
        if name not in model_stats:
            model_stats[name] = {
                "count": 0, "success": 0, "errors": 0,
                "avg_duration": 0.0, "total_duration": 0.0,
            }
        s = model_stats[name]
        s["count"] = s["count"] + 1
        s["total_duration"] = s["total_duration"] + r.duration
        if r.success:
            s["success"] = s["success"] + 1
        if r.llm_error:
            s["errors"] = s["errors"] + 1

    for s in model_stats.values():
        cnt = s["count"]
        s["avg_duration"] = s["total_duration"] / cnt if cnt else 0

    # 时间范围
    from datetime import datetime
    first_ts = records[0].timestamp
    last_ts = records[-1].timestamp
    first_dt = datetime.fromtimestamp(first_ts).strftime("%m-%d %H:%M")
    last_dt = datetime.fromtimestamp(last_ts).strftime("%m-%d %H:%M")

    lines = [
        f"📊 **最近 {total} 轮任务指标**（{first_dt} ~ {last_dt}）\n",
        f"  成功率：{success}/{total}（{success/total*100:.0f}%）",
        f"  平均耗时：{avg_duration:.1f}s",
        f"  平均工具调用：{avg_tools:.1f} 次/轮",
        f"  总 token：{total_tokens:,}",
        f"  中断：{interrupted} 次",
        f"  LLM 错误：{llm_errors} 次",
        f"  使用 fallback：{fallback_used} 次",
        f"  触发压缩：{compacted} 次",
    ]

    if model_stats:
        lines.append("\n**按模型**：")
        for name, s in sorted(model_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            cnt = s["count"]
            succ = s["success"]
            avg_d = s["avg_duration"]
            err = s["errors"]
            lines.append(
                f"  {name}：{cnt} 轮，成功率 {succ}/{cnt}（{succ/cnt*100:.0f}%），"
                f"平均 {avg_d:.1f}s，错误 {err} 次"
            )

    return "\n".join(lines)
