"""Agent 中断异常：当新消息抢占时，从 tool_loop 中跳出。

不继承 Exception，必须显式 catch。
"""

from __future__ import annotations


class AgentInterrupted(Exception):
    """Agent 被新消息抢占，当前任务被中断。

    Attributes:
        progress_summary: 中断时的进度摘要（已完成的工具调用等）。
    """

    def __init__(self, message: str = "任务被新消息中断", progress_summary: str = "") -> None:
        super().__init__(message)
        self.progress_summary = progress_summary
