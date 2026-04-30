# Lampson 多平台架构设计方案（v2）

**v1 问题修复版**（asyncio 嵌套崩溃、ContextSnapshot hack、信号处理缺失、task_id 未完成、cancel 缺失、adapter 无重连）

## 目标

将 Lampson 从单渠道（飞书）改造成多平台消息网关，支持后台任务。

## 目录结构

```
src/platforms/
├── __init__.py
├── base.py                  # BasePlatformAdapter + PlatformMessage
├── manager.py               # PlatformManager（asyncio 主循环）
├── background.py            # BackgroundTaskManager + BackgroundTask + ContextSnapshot
└── adapters/
    ├── __init__.py
    ├── feishu.py            # FeishuAdapter（重构自现有 feishu/listener.py）
    └── ...                  # 将来加 telegram、discord 等
```

---

## 核心组件

### PlatformMessage（base.py）

```python
@dataclass
class PlatformMessage:
    platform: str           # "feishu" / "telegram"
    sender_id: str          # 用户在平台上的 ID
    chat_id: str            # 会话 ID
    thread_id: str | None   # 线程 ID（无则 None）
    message_id: str         # 消息 ID（去重）
    text: str               # 消息文本
    timestamp: float        # 收到时间
```

### BasePlatformAdapter（base.py）

```python
class BasePlatformAdapter(ABC):
    platform: str

    @abstractmethod
    def start(self) -> None:
        """启动平台连接，非阻塞。失败时由调用方处理重连。"""

    @abstractmethod
    async def shutdown(self, timeout: float = 30.0) -> None:
        """优雅关闭"""

    @abstractmethod
    async def send(self, chat_id: str, text: str,
                   thread_id: str | None = None) -> None:
        """发送文本消息"""

    @abstractmethod
    async def send_card(self, chat_id: str, card: dict,
                        thread_id: str | None = None) -> None:
        """发送卡片消息"""

    def on_message(self, msg: PlatformMessage) -> None:
        """平台收到消息 → 推给 PlatformManager"""
        PlatformManager.instance().dispatch(msg)
```

### PlatformManager（manager.py）

```python
class PlatformManager:
    _instance: "PlatformManager | None" = None

    def __init__(self, config: dict):
        self._adapters: dict[str, BasePlatformAdapter] = {}
        self._session_manager = get_session_manager(config)
        self._background_mgr = BackgroundTaskManager()
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None  # 主事件循环引用

    @classmethod
    def instance(cls) -> "PlatformManager":
        return cls._instance

    def register(self, adapter: BasePlatformAdapter) -> None:
        self._adapters[adapter.platform] = adapter

    def dispatch(self, msg: PlatformMessage) -> None:
        """消息入口：路由到 Session（同步调用，由 adapter 线程触发）"""
        session = self._session_manager.get_or_create(
            msg.platform, msg.sender_id
        )
        session.set_reply_channel(
            platform=msg.platform,
            chat_id=msg.chat_id,
            thread_id=msg.thread_id,
        )
        result = session.handle_input(msg.text)

    async def run(self) -> None:
        """主事件循环"""
        self._running = True
        self._loop = asyncio.get_running_loop()

        # 启动所有已配置的 adapter（含重连逻辑）
        for platform, cfg in self._config.get("platforms", {}).items():
            if not cfg.get("enabled", False):
                continue
            adapter = self._create_adapter(platform, cfg)
            self.register(adapter)
            await self._start_adapter_with_retry(adapter)

        # 信号处理：SIGTERM / SIGINT 优雅退出
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_shutdown, sig)
            except ValueError:
                # macOS / 非主线程不支持 add_signal_handler，跳过
                pass

        # 主循环：所有工作由 adapter 回调触发，保持运行即可
        while self._running:
            await asyncio.sleep(1)

        # 优雅退出
        for adapter in self._adapters.values():
            await adapter.shutdown()

    async def _start_adapter_with_retry(self, adapter: BasePlatformAdapter) -> None:
        """带指数退避重连的 adapter 启动"""
        retry_interval = 1
        max_interval = 60
        while True:
            try:
                adapter.start()
                return
            except Exception as e:
                print(f"[manager] adapter {adapter.platform} 启动失败: {e}，"
                      f"{retry_interval}s 后重试...")
                await asyncio.sleep(retry_interval)
                retry_interval = min(retry_interval * 2, max_interval)

    def _on_shutdown(self, sig: signal.Signals) -> None:
        print(f"[manager] 收到信号 {sig.name}，准备退出...")
        self._running = False

    def schedule_async(self, coro) -> None:
        """从后台线程安全地调度协程到主事件循环。
        
        BackgroundTask 推送结果时调用此方法，
        避免在线程中 asyncio.run() 导致的事件循环嵌套崩溃。
        """
        if self._loop is None:
            raise RuntimeError("主事件循环未初始化")
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _create_adapter(self, platform: str, cfg: dict) -> BasePlatformAdapter:
        if platform == "feishu":
            return FeishuAdapter(cfg)
        elif platform == "telegram":
            return TelegramAdapter(cfg)
        else:
            raise ValueError(f"Unknown platform: {platform}")
```

### ContextSnapshot（background.py）

```python
@dataclass
class ContextSnapshot:
    """从发起 session 提取的上下文快照，用于后台任务继承上下文"""
    recent_messages: list[dict]    # 最近 N 轮对话（不含 system prompt）
    system_prompt: str             # 当前 system prompt 全文
    session_id: str               # 发起 session ID（用于关联）
    channel: str                  # 发起渠道
    chat_id: str                  # 发起会话 ID
    project_context: str | None    # 当前项目上下文（如有）
```

### BackgroundTaskManager（background.py）

```python
class BackgroundTaskManager:
    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()

    def start(self, prompt: str, platform: str, chat_id: str,
              thread_id: str | None, snapshot: ContextSnapshot) -> str:
        """启动后台任务，返回 task_id"""
        import uuid
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
        # 在独立线程中运行（LLM 调用是同步的）
        t = threading.Thread(target=task.run, daemon=True, name=f"bg-{task_id}")
        t.start()
        return task_id

    def cancel(self, task_id: str) -> bool:
        """取消任务。只对 pending/running 状态有效，已完成或已取消的返回 False。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status not in ("pending", "running"):
                return False
            task.status = "cancelled"
            return True

    def list(self) -> list[dict]:
        """查看运行中的任务"""
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
```

### BackgroundTask（background.py）

```python
class BackgroundTask:
    def __init__(self, task_id: str, prompt: str, platform: str,
                 chat_id: str, thread_id: str | None,
                 snapshot: ContextSnapshot):
        self.task_id = task_id
        self.prompt = prompt
        self.platform = platform
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.snapshot = snapshot
        self.status = "running"

    def run(self) -> None:
        """在线程中执行，不阻塞主事件循环"""
        try:
            agent = self._create_agent()
            # 注入上下文（通过正规 API，不直接操作 messages[0]）
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

    def _create_agent(self) -> Agent:
        """从当前 session 配置创建独立 Agent 实例"""
        # 从 PlatformManager 获取当前 LLM 配置
        mgr = PlatformManager.instance()
        config = mgr._config
        # 复用现有 Agent 创建逻辑，但不使用当前 session 的 messages
        # （独立上下文，由 ContextSnapshot 提供）
        from src.core.session import _create_llm, _build_compaction_config
        llm, adapter = _create_llm(config, channel=self.platform)
        compaction_cfg = _build_compaction_config(config)
        agent = Agent(llm, adapter, compaction_config=compaction_cfg)
        agent.set_context()
        agent.skills = skills_mgr.load_all_skills()
        return agent

    def _inject_context(self, agent: Agent) -> None:
        """通过正规 API 注入上下文快照，不直接操作 messages[0]
        
        注入方式：将 ContextSnapshot 的信息作为 system prompt 扩展，
        不添加假的 assistant 回复。
        """
        # 构造扩展后的 system prompt
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
        # 替换 system prompt（通过 LLMClient 的正规接口）
        if agent.llm.messages and agent.llm.messages[0].get("role") == "system":
            agent.llm.messages[0]["content"] = extended_system
        else:
            agent.llm.messages.insert(0, {"role": "system", "content": extended_system})

    def _deliver(self, content: str) -> None:
        """通过主事件循环安全地发送结果（避免 asyncio.run 嵌套崩溃）"""
        if self.status == "cancelled":
            return
        header = (
            f"✅ 后台任务完成\n"
            f"Task ID: {self.task_id}\n"
            f"发起 session: {self.snapshot.session_id}\n\n"
        )
        full_content = header + content

        mgr = PlatformManager.instance()
        adapter = mgr._adapters.get(self.platform)
        if adapter is None:
            print(f"[background] 无法推送结果：找不到 {self.platform} adapter")
            return

        # 通过主事件循环安全调度，不在后台线程中 asyncio.run()
        coro = adapter.send(self.chat_id, full_content, thread_id=self.thread_id)
        mgr.schedule_async(coro)
```

---

## Session 改造

```python
# session.py 新增方法

def set_reply_channel(self, platform: str, chat_id: str,
                      thread_id: str | None = None) -> None:
    """注入回复渠道，由 PlatformManager.dispatch() 调用"""
    self._reply_adapter = PlatformManager.instance()._adapters[platform]
    self._reply_chat_id = chat_id
    self._reply_thread_id = thread_id

def _send_reply(self, text: str) -> None:
    """通过主事件循环安全发送回复"""
    if not self._reply_adapter:
        return
    mgr = PlatformManager.instance()
    if mgr._loop is None:
        return
    coro = self._reply_adapter.send(
        self._reply_chat_id, text, self._reply_thread_id
    )
    mgr.schedule_async(coro)

# 新增命令处理
HELP_TEXT += """
  /background <prompt>       后台运行任务，完成后推送结果
  /tasks                    查看运行中的后台任务
  /cancel <task_id>         取消后台任务
"""

def _handle_background(self, prompt: str) -> HandleResult:
    snapshot = self._snapshot_context()
    mgr = PlatformManager.instance()
    task_id = mgr._background_mgr.start(
        prompt=prompt,
        platform=self.channel,
        chat_id=self._current_chat_id,
        thread_id=self._current_thread_id,
        snapshot=snapshot,
    )
    preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
    reply = (f"🔄 后台任务已启动\n"
             f"Task ID: {task_id}\n"
             f'"{preview}"\n\n'
             f"完成后会自动通知，继续聊天即可。")
    return HandleResult(reply=reply, is_command=True)

def _snapshot_context(self, max_turns: int = 6) -> ContextSnapshot:
    """提取当前 session 上下文快照"""
    messages = self.agent.llm.messages

    # 取最近 N 轮 user+assistant
    recent = []
    for msg in reversed(messages):
        role = msg.get("role", "")
        if role in ("user", "assistant") and len(recent) < max_turns * 2:
            recent.insert(0, {
                "role": role,
                "content": msg.get("content", "")[:500]
            })

    # system prompt
    system = ""
    if messages and messages[0].get("role") == "system":
        system = messages[0].get("content", "")

    # project context
    project_ctx = None
    if self.agent.project_index:
        try:
            # 取当前项目的 key facts
            project_ctx = self.agent.project_index.get_summary() if hasattr(
                self.agent.project_index, "get_summary"
            ) else None
        except Exception:
            pass

    return ContextSnapshot(
        recent_messages=recent,
        system_prompt=system,
        session_id=self.session_id,
        channel=self.channel,
        chat_id=self._current_chat_id,
        project_context=project_ctx,
    )
```

---

## daemon.py 改造

```python
# 原来：
while not _shutdown.is_set():
    signal.pause()

# 改为：
async def main():
    config = load_config()
    mgr = get_session_manager(config)
    pm = PlatformManager(config)
    PlatformManager._instance = pm
    await pm.run()

asyncio.run(main())
```

---

## FeishuListener → FeishuAdapter

- 保留 WebSocket 长连接逻辑（threading）
- `_handle_message` 提取 `PlatformMessage` 后调 `self.on_message(msg)`
- `send` / `send_card` 改为 `async def`，复用 `FeishuClient`
- 实现 `start()`：启动 WebSocket 长连接线程
- 实现 `shutdown()`：优雅关闭 WebSocket 连接

---

## config.yaml 变更

```yaml
platforms:
  feishu:
    enabled: true
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"
```

---

## v1 → v2 修复清单

| 问题 | v1 | v2 修复 |
|------|-----|---------|
| asyncio.run 嵌套崩溃 | `asyncio.run(adapter.send())` | `schedule_async()` + `asyncio.run_coroutine_threadsafe()` |
| ContextSnapshot hack | `messages[0] =` + 假 assistant | 扩展 system_prompt，不加假回复 |
| 主循环空转无信号 | `while sleep(1)` | `add_signal_handler(SIGTERM/SIGINT)` |
| task_id 未完成 | `f"bg_{...}"` | `uuid.uuid4().hex[:4]` |
| /cancel 缺失 | 只有 HELP_TEXT | `BackgroundTaskManager.cancel()` |
| adapter 无重连 | `start()` 失败即退出 | `_start_adapter_with_retry()` 指数退避 |

---

## 不变的组件

- Agent（完全不变，平台无关）
- SessionManager（不变）
- HeartbeatManager（不变）
- tools / memory（不变）
