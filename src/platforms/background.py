"""后台任务管理器：支持用户将任务放到后台执行，完成后推送结果。

设计原则：
- 不做 TaskQueue、不做持久化、不做状态机
- 后台任务用独立 Agent 实例，从发起 session 提取 ContextSnapshot 继承上下文
- 完成后通过 PlatformManager.schedule_async() 安全推送结果，不在线程中 asyncio.run()
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.core.agent import Agent


@dataclass
class ContextSnapshot:
    """从发起 session 提取的上下文快照，供后台 Agent 继承上下文。"""

    recent_messages: list[dict]  # 最近 N 轮对话（不含 system prompt）
    system_prompt: str  # 当前 system prompt 全文
    session_id: str  # 发起 session ID（用于关联）
    channel: str  # 发起渠道
    chat_id: str  # 发起会话 ID
    project_context: str | None = None  # 当前项目上下文


class BackgroundTaskManager:
    """管理所有运行中的后台任务。"""

    _instance: "BackgroundTaskManager | None" = None

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()

    @classmethod
    def instance(cls) -> "BackgroundTaskManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(
        self,
        prompt: str,
        platform: str,
        chat_id: str,
        thread_id: str | None,
        snapshot: ContextSnapshot,
    ) -> str:
        """启动后台任务，返回 task_id。"""
        task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"
        task = BackgroundTask(
            task_id=task_id,
            prompt=prompt,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            snapshot=snapshot,
        )
        with self._lock:
            self._tasks[task_id] = task
        t = threading.Thread(target=task.run, daemon=True, name=f"bg-{task_id}")
        t.start()
        return task_id

    def cancel(self, task_id: str) -> bool:
        """取消任务。只对 running 状态有效。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status not in ("pending", "running"):
                return False
            task.status = "cancelled"
            return True

    def list(self) -> list[dict]:
        """查看运行中的任务。"""
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "prompt": t.prompt[:60] + ("..." if len(t.prompt) > 60 else ""),
                    "status": t.status,
                    "channel": t.platform,
                }
                for t in self._tasks.values()
            ]

    def _remove(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)


class BackgroundTask:
    """单个后台任务：创建独立 Agent，注入上下文，执行，推送结果。"""

    def __init__(
        self,
        task_id: str,
        prompt: str,
        platform: str,
        chat_id: str,
        thread_id: str | None,
        snapshot: ContextSnapshot,
    ) -> None:
        self.task_id = task_id
        self.prompt = prompt
        self.platform = platform
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.snapshot = snapshot
        self.status = "running"

    def run(self) -> None:
        """在线程中执行，不阻塞主事件循环。"""
        try:
            agent = self._create_agent()
            self._inject_context(agent)
            result = agent.run(self.prompt)
            if self.status == "cancelled":
                return
            self._deliver(result)
        except Exception as e:
            if self.status != "cancelled":
                self._deliver(f"[错误] 后台任务失败: {e}")
        finally:
            BackgroundTaskManager.instance()._remove(self.task_id)

    def _create_agent(self) -> "Agent":
        """从当前配置创建独立 Agent 实例，不复用 session。"""
        from src.core.agent import Agent
        from src.core.session import _create_llm, _build_compaction_config
        from src.skills import manager as skills_mgr
        from src.platforms.manager import PlatformManager

        mgr = PlatformManager.instance()
        config = mgr._config
        llm, adapter = _create_llm(config, channel=self.platform)
        compaction_cfg = _build_compaction_config(config)
        agent = Agent(llm, adapter, compaction_config=compaction_cfg)
        agent.set_context()
        agent.skills = skills_mgr.load_all_skills()
        return agent

    def _inject_context(self, agent: "Agent") -> None:
        """通过扩展 system prompt 注入上下文，不直接操作 messages 加假 assistant。"""
        parts = [self.snapshot.system_prompt]

        if self.snapshot.recent_messages:
            context_lines = ["\n\n[后台任务上下文]\n以下是对话背景："]
            for msg in self.snapshot.recent_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")[:500]
                context_lines.append(f"{role}: {content}")
            parts.append("\n".join(context_lines))

        if self.snapshot.project_context:
            parts.append(f"\n\n[当前项目]\n{self.snapshot.project_context}")

        extended_system = "\n".join(parts)
        if agent.llm.messages and agent.llm.messages[0].get("role") == "system":
            agent.llm.messages[0]["content"] = extended_system
        else:
            agent.llm.messages.insert(0, {"role": "system", "content": extended_system})

    def _deliver(self, content: str) -> None:
        """通过主事件循环安全推送结果，避免 asyncio.run 嵌套崩溃。"""
        if self.status == "cancelled":
            return

        from src.platforms.manager import PlatformManager

        mgr = PlatformManager.instance()
        adapter = mgr._adapters.get(self.platform)
        if adapter is None:
            logger.info(f"[background] 无法推送结果：找不到 {self.platform} adapter")
            return

        header = (
            f"✅ 后台任务完成\n"
            f"Task ID: {self.task_id}\n"
            f"发起 session: {self.snapshot.session_id}\n\n"
        )
        coro = adapter.send(self.chat_id, header + content, thread_id=self.thread_id)
        mgr.schedule_async(coro)
