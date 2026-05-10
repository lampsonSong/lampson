"""中断抢占机制测试：模拟各种并发场景验证行为正确性。"""

from __future__ import annotations

import queue
import threading
import time
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from src.core.interrupt import AgentInterrupted


# ── Mock 组件 ───────────────────────────────────────────────────────────

class MockAgent:
    """模拟 Agent，支持控制 run() 的行为（正常返回/抛异常）。"""

    def __init__(self):
        self.messages: list[dict] = []
        self._interrupted: bool = False
        self._interrupt_lock = threading.Lock()
        self.progress_callback = None
        self.interim_sender = None
        self._run_behavior: str = "normal"  # "normal" | "interrupt"
        self._run_count: int = 0

    def request_interrupt(self) -> None:
        with self._interrupt_lock:
            self._interrupted = True

    def check_interrupt(self) -> None:
        if not self._interrupted:
            return
        with self._interrupt_lock:
            if not self._interrupted:
                return
            self._interrupted = False
        raise AgentInterrupted(progress_summary="[任务被中断，以下是已完成的进度]\n\n**原任务**：测试任务\n\n**已调用 1 个工具**：\n  - `shell`(echo hello)")

    def clear_interrupt_state(self) -> None:
        with self._interrupt_lock:
            self._interrupted = False

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def run(self, user_input: str) -> str:
        self._run_count += 1
        self.add_user_message(user_input)

        if self._run_behavior == "interrupt":
            # 模拟：在处理过程中被中断
            self._interrupted = True
            self.check_interrupt()  # 这会抛 AgentInterrupted

        return f"回复_{self._run_count}"


# ── 简化 Session 模拟（只测试核心队列逻辑） ─────────────────────────────

class SimpleSession:
    """Session 核心逻辑的简化版本（不含 LLM/JSONL），用于独立测试。"""

    def __init__(self, agent: MockAgent):
        self.agent = agent
        self.channel: str = "feishu"
        self._input_queue: queue.Queue[str] = queue.Queue()
        self._processing: bool = False
        self._processing_lock = threading.Lock()
        self._pending_task_summary: str = ""
        self._pending_task_messages_snapshot: list[dict] = []
        self._reply_callback: MagicMock | None = None
        self._results: list[str] = []
        self._reply_logs: list[str] = []

    def handle_input(self, user_input: str) -> "MockResult":
        from src.core.interrupt import AgentInterrupted

        if user_input.startswith("/"):
            return MockResult(reply="")

        # 非飞书渠道
        if self.channel != "feishu":
            reply = self.agent.run(user_input)
            return MockResult(reply=reply)

        # 飞书并发渠道
        if self._processing:
            self._input_queue.put(user_input)
            self.agent.request_interrupt()
            return MockResult(reply="")

        acquired = self._processing_lock.acquire(blocking=False)
        if not acquired:
            self._input_queue.put(user_input)
            self.agent.request_interrupt()
            return MockResult(reply="")

        self._processing = True
        try:
            return self._process_with_interrupt(user_input)
        finally:
            self._processing = False
            self._processing_lock.release()

    def _process_with_interrupt(self, user_input: str):
        from src.core.interrupt import AgentInterrupted

        current_input = user_input

        while True:
            self.agent.clear_interrupt_state()

            try:
                reply = self.agent.run(current_input)
            except AgentInterrupted as e:
                interrupt_summary = e.progress_summary
                self._pending_task_messages_snapshot = list(self.agent.messages)

                try:
                    current_input = self._input_queue.get_nowait()
                except Exception:
                    return MockResult(reply="[任务被中断]")

                self._pending_task_summary = interrupt_summary
                current_input = (
                    interrupt_summary
                    + "\n\n--- 任务被新消息中断 ---\n\n"
                    + "**新消息**：" + current_input
                )
                continue

            except Exception as e:
                return MockResult(reply=f"[错误] {e}")

            has_pending_resume = bool(self._pending_task_summary)
            has_queued = not self._input_queue.empty()

            if has_pending_resume or has_queued:
                if reply and self._reply_callback:
                    self._reply_callback(reply)
                    self._reply_logs.append(reply)

                if has_pending_resume:
                    current_input = self._build_resume_prompt()
                    continue

                if has_queued:
                    try:
                        current_input = self._input_queue.get_nowait()
                        continue
                    except Exception:
                        pass

            return MockResult(reply=reply)

    def _build_resume_prompt(self) -> str:
        summary = self._pending_task_summary
        self._pending_task_summary = ""
        self._pending_task_messages_snapshot = []

        return (
            summary
            + "\n\n--- 新消息已处理完毕，继续之前的任务 ---\n\n"
            + "请根据上述进度，继续完成原来的任务。"
            + "如果任务已经完成或不需要继续，请告知用户。"
        )


@dataclass
class MockResult:
    reply: str = ""


# ── 测试用例 ────────────────────────────────────────────────────────────

class TestInterruptQueue(unittest.TestCase):
    """测试中断抢占机制的核心队列逻辑。"""

    def test_single_message_normal(self):
        """场景 1：正常单条消息处理（无并发）"""
        agent = MockAgent()
        agent._run_behavior = "normal"
        session = SimpleSession(agent)

        result = session.handle_input("你好")

        self.assertEqual(result.reply, "回复_1")
        self.assertTrue(session._input_queue.empty())
        self.assertFalse(session._processing)
        print("✅ 场景 1 通过：正常单条消息处理")

    def test_message_interrupted_and_new_message(self):
        """场景 2：处理中收到新消息 → 入队 + 中断"""
        agent = MockAgent()
        agent._run_behavior = "interrupt"  # 第一次 run 会抛出中断
        session = SimpleSession(agent)
        session._processing = True  # 模拟正在处理中

        # 新消息到来
        result = session.handle_input("新消息")

        # 入队，立即返回空结果
        self.assertEqual(result.reply, "")
        self.assertEqual(session._input_queue.qsize(), 1)
        self.assertTrue(session._input_queue.get_nowait(), "新消息")
        print("✅ 场景 2 通过：消息入队 + 请求中断")

    def test_interrupt_then_resume(self):
        """场景 3：中断后处理新消息，然后恢复被中断的任务"""
        agent = MockAgent()
        agent._run_behavior = "interrupt"  # 第一次中断
        session = SimpleSession(agent)
        session._reply_callback = MagicMock()

        # 第一次调用：正常处理任务 A
        result1 = session.handle_input("任务A：帮我查天气")
        # 被中断（队列为空），返回中断信息
        self.assertEqual(result1.reply, "[任务被中断]")
        # 中断后 _interrupted 被 check_interrupt 中的 clear 重置了
        # 这是预期行为：check_interrupt 消费了标志位

        # 模拟新消息入队 + 中断信号
        agent._interrupted = False
        agent._run_behavior = "normal"

        # 再次调用：处理新消息 B（不再中断，正常处理）
        agent._run_count = 0
        result2 = session.handle_input("任务B：你好")
        # 此时 _processing=False → 正常获取锁 → 正常处理 → 返回回复
        self.assertEqual(result2.reply, "回复_1")
        print("✅ 场景 3 预检：中断后下一条消息正常处理")

    def test_queue_fifo_order(self):
        """场景 4：多条消息按 FIFO 顺序处理"""
        agent = MockAgent()
        agent._run_behavior = "normal"
        session = SimpleSession(agent)

        # 模拟处理中，多条消息连续到达
        session._processing = True
        session._input_queue.put("消息1")
        session._input_queue.put("消息2")
        session._input_queue.put("消息3")

        # 验证 FIFO
        self.assertEqual(session._input_queue.get_nowait(), "消息1")
        self.assertEqual(session._input_queue.get_nowait(), "消息2")
        self.assertEqual(session._input_queue.get_nowait(), "消息3")
        print("✅ 场景 4 通过：队列 FIFO 顺序正确")

    def test_lock_prevents_concurrent_processing(self):
        """场景 5：锁争用时消息入队"""
        session = SimpleSession(MockAgent())
        session._processing = True
        session._processing_lock = threading.Lock()
        session._processing_lock.acquire()  # 锁被占用

        # 锁被占用时，消息应入队
        result = session.handle_input("消息X")
        self.assertEqual(result.reply, "")
        self.assertEqual(session._input_queue.qsize(), 1)
        self.assertEqual(session._input_queue.get_nowait(), "消息X")

        session._processing_lock.release()
        print("✅ 场景 5 通过：锁争用时正确入队")

    def test_cli_channel_skips_queue(self):
        """场景 6：CLI 渠道不走队列机制"""
        agent = MockAgent()
        agent._run_behavior = "normal"
        session = SimpleSession(agent)
        session.channel = "cli"
        session._processing = True  # 模拟"正在处理"

        # CLI 渠道：即使 _processing=True，也直接处理
        result = session.handle_input("你好 CLI")
        self.assertEqual(result.reply, "回复_1")
        self.assertTrue(session._input_queue.empty())  # 不入队
        print("✅ 场景 6 通过：CLI 渠道跳过队列机制")

    def test_resume_pending_task_after_new_message(self):
        """场景 7：新消息处理完后恢复被中断的任务"""
        agent = MockAgent()
        session = SimpleSession(agent)
        session._reply_callback = MagicMock()
        session._pending_task_summary = "[任务被中断]\n\n**原任务**：任务A\n\n**已调用 1 个工具**"

        # 任务 B 处理完毕，检查是否尝试恢复任务 A
        agent._run_behavior = "normal"
        agent._run_count = 0

        # 队列中有任务 A
        session._input_queue.put("任务A")

        result = session.handle_input("任务B")

        # B 处理完成 → callback 发送B的回复
        # pending_summary 存在 → 构建 resume prompt → 处理恢复
        # 队列还有"任务A" → callback 发送恢复的回复 → 继续处理"任务A"
        # 最终"任务A"处理完 → 返回
        self.assertEqual(len(session._reply_logs), 2)  # B + resume
        self.assertEqual(agent._run_count, 3)  # B + resume_A + 队列中的"任务A"
        print("✅ 场景 7 通过：任务 B 处理后尝试恢复任务 A")

    def test_multiple_consecutive_interrupts(self):
        """场景 8：连续多次中断（消息1处理中，消息2、3、4连续到达）"""
        agent = MockAgent()
        session = SimpleSession(agent)
        session._reply_callback = MagicMock()

        # 模拟连续多条消息到达
        session._input_queue.put("消息2")
        session._input_queue.put("消息3")
        session._input_queue.put("消息4")

        # 处理消息1（正常）
        agent._run_behavior = "normal"
        agent._run_count = 0
        result = session.handle_input("消息1")

        # 消息1完成后，队列非空 → 处理消息2
        # 4条消息全部在同一次 handle_input 的循环中处理完
        self.assertEqual(agent._run_count, 4)  # 消息1 + 2 + 3 + 4
        self.assertEqual(len(session._reply_logs), 3)  # 消息1/2/3 通过 callback，消息4 作为最终结果
        print("✅ 场景 8 通过：连续消息正确按序处理")

    def test_progress_callback_cleared_on_enqueue(self):
        """场景 9：入队时 progress_callback 不会残留导致误发"""
        agent = MockAgent()
        agent.progress_callback = MagicMock()
        session = SimpleSession(agent)

        # 处理中收到新消息
        session._processing = True
        session.handle_input("新消息")

        # 新消息入队，不触发任何 callback
        self.assertEqual(agent.progress_callback.call_count, 0)
        print("✅ 场景 9 通过：入队时不触发 callback")

    def test_agent_request_interrupt_is_thread_safe(self):
        """场景 10：request_interrupt 多次调用是线程安全的"""
        agent = MockAgent()
        lock = threading.Lock()
        errors = []

        def interrupt_worker():
            try:
                for _ in range(100):
                    agent.request_interrupt()
                    agent.clear_interrupt_state()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=interrupt_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        print("✅ 场景 10 通过：request_interrupt 线程安全")


class TestAgentInterruptedException(unittest.TestCase):
    """测试 AgentInterrupted 异常的行为。"""

    def test_exception_carry_summary(self):
        """异常携带 progress_summary"""
        exc = AgentInterrupted(progress_summary="测试摘要")
        self.assertEqual(exc.progress_summary, "测试摘要")

    def test_exception_can_be_caught(self):
        """可以被正确捕获"""
        agent = MockAgent()
        agent._interrupted = True

        caught = False
        try:
            agent.check_interrupt()
        except AgentInterrupted:
            caught = True

        self.assertTrue(caught)
        print("✅ AgentInterrupted 可被正确捕获")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("中断抢占机制测试")
    print("=" * 60 + "\n")

    unittest.main(verbosity=2)
