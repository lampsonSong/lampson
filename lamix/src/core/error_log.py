"""
Error Log — 结构化错误日志，支持自动 debug 复现。

写入 ~/.lamix/memory/errors.jsonl，每条记录包含：
- 错误基本信息（类型、消息、来源）
- 上下文快照（session_id、最近 N 条 messages 摘要、tool_call/result）
- 用于自动 debug 的复现信息

设计原则：
- 只追加，不修改已有记录
- 单条记录尽量自包含（不依赖其他文件即可复现）
- 轻量：正常路径零开销，只在出错时写入
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LAMIX_DIR = Path.home() / ".lamix"
ERRORS_LOG = LAMIX_DIR / "memory" / "errors.jsonl"

# 错误来源分类
SOURCE_LLM = "llm"
SOURCE_TOOL = "tool"
SOURCE_AGENT = "agent"

# 上下文快照中保留的最大消息条数
_MAX_CONTEXT_MESSAGES = 20

# 日志文件最大 20MB，超过则轮转
_MAX_LOG_BYTES = 20 * 1024 * 1024
_MAX_ROTATIONS = 5


def log_error(
    error_type: str,
    message: str,
    source: str,
    *,
    session_id: str | None = None,
    detail: dict[str, Any] | None = None,
    messages_snapshot: list[dict] | None = None,
    tool_name: str | None = None,
    tool_arguments: dict | None = None,
    tool_result: str | None = None,
    exception: Exception | None = None,
) -> dict[str, Any]:
    """记录一条结构化错误日志。

    Args:
        error_type: 错误类型分类（如 ToolExecutionError, LLMFatalError, LLMRateLimitError 等）
        message: 人类可读的错误消息
        source: 错误来源（SOURCE_LLM / SOURCE_TOOL / SOURCE_AGENT）
        session_id: 关联的 session ID
        detail: 额外结构化信息（如 model、status_code、duration_ms 等）
        messages_snapshot: 当前对话 messages 快照（会截断到最近 N 条）
        tool_name: 如果是工具错误，工具名称
        tool_arguments: 如果是工具错误，工具参数
        tool_result: 如果是工具错误，工具返回结果
        exception: 原始异常对象（会提取 traceback）

    Returns:
        写入的记录 dict
    """
    now = datetime.now(timezone.utc)

    record: dict[str, Any] = {
        "ts": now.isoformat(),
        "ts_ms": int(now.timestamp() * 1000),
        "error_type": error_type,
        "message": message,
        "source": source,
    }

    if session_id:
        record["session_id"] = session_id

    if detail:
        record["detail"] = detail

    # 上下文快照
    if messages_snapshot:
        record["context"] = _snapshot_messages(messages_snapshot)

    # 工具信息
    if tool_name:
        record["tool_name"] = tool_name
    if tool_arguments:
        # 截断参数避免过大
        args_str = json.dumps(tool_arguments, ensure_ascii=False, default=str)
        record["tool_arguments"] = args_str[:2000]
    if tool_result:
        record["tool_result"] = tool_result[:2000]

    # 异常 traceback
    if exception:
        record["traceback"] = traceback.format_exception(
            type(exception), exception, exception.__traceback__
        )

    # 写入文件
    _append(record)
    return record


def _snapshot_messages(messages: list[dict]) -> dict[str, Any]:
    """从 messages 列表中提取复现所需的上下文快照。

    保留最近 _MAX_CONTEXT_MESSAGES 条，每条截断内容。
    """
    snapshot = {
        "total_count": len(messages),
        "tail": [],
    }

    tail_messages = messages[-_MAX_CONTEXT_MESSAGES:]
    for msg in tail_messages:
        entry: dict[str, Any] = {"role": msg.get("role", "unknown")}

        # content
        content = msg.get("content", "")
        if content:
            entry["content"] = str(content)[:500]

        # tool_calls（assistant 消息）
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "arguments": str(tc.get("function", {}).get("arguments", ""))[:500],
                }
                for tc in tool_calls
            ]

        # tool result
        tool_call_id = msg.get("tool_call_id")
        if tool_call_id:
            entry["tool_call_id"] = tool_call_id

        snapshot["tail"].append(entry)

    return snapshot


def _append(record: dict[str, Any]) -> None:
    """追加一条记录到 errors.jsonl，自动轮转。"""
    try:
        ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)

        if ERRORS_LOG.exists() and ERRORS_LOG.stat().st_size > _MAX_LOG_BYTES:
            _rotate()

        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(ERRORS_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        # 错误日志系统自身出错不应影响主流程
        logger.debug(f"写入 error_log 失败: {e}")


def _rotate() -> None:
    """轮转错误日志：errors.jsonl → .1 → .2 → ... → .5"""
    for i in range(_MAX_ROTATIONS - 1, 0, -1):
        src = ERRORS_LOG.with_suffix(f".jsonl.{i}")
        dst = ERRORS_LOG.with_suffix(f".jsonl.{i + 1}")
        if src.exists():
            src.replace(dst)
    ERRORS_LOG.replace(ERRORS_LOG.with_suffix(".jsonl.1"))


def query_recent_errors(
    limit: int = 20,
    source: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """查询最近的错误记录（用于 debug/review）。

    Args:
        limit: 最多返回多少条
        source: 按来源过滤（SOURCE_LLM / SOURCE_TOOL / SOURCE_AGENT）
        session_id: 按 session_id 过滤

    Returns:
        错误记录列表，最新的在前
    """
    if not ERRORS_LOG.exists():
        return []

    results: list[dict[str, Any]] = []
    try:
        with open(ERRORS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if source and record.get("source") != source:
                    continue
                if session_id and record.get("session_id") != session_id:
                    continue

                results.append(record)
    except Exception as e:
        logger.debug(f"读取 error_log 失败: {e}")

    # 返回最新的 limit 条（倒序）
    return results[-limit:][::-1]
