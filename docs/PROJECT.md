# Lampson 项目文档

> 文档版本：2026-04-28（daemon 架构 + USER.md 注入 + boot_tasks 机制）
> 项目版本：v0.3.0-dev

---

## 一、项目概述

**Lampson** 是一个运行在终端（CLI）的自更新智能助手，通过自然语言对话的方式帮助用户完成任务。

**核心理念**：
- **工具优先**：能动手绝不光说——必须使用工具执行操作
- **自进化**：能自己改自己的代码，不断提升能力
- **记忆持久**：跨会话记住用户偏好和重要事实
- **技能复用**：将复杂工作流保存为可复用技能

**技术选型**：
- Python 3.11+，Prompt Toolkit (REPL)
- LLM：智谱 GLM-5.1（OpenAI 兼容接口）
- 飞书：通过飞书开放平台 REST API 直连
- 自更新：基于 Git 分支 + 回滚
- 打包：`pip install -e .`

---

## 二、技术架构

### 2.1 架构演进

#### 旧架构（v0.1）：单 Session 全局共享

```
cli.py ──────────────────→ Session (ONE) ──→ Agent ──→ LLM(messages)
feishu/listener.py (blocking start()) ↗
```

问题：所有渠道共享同一个 LLM context；Feishu start() 阻塞，CLI 无法并发。

#### 新架构（v0.2+）：daemon + CLI 分离

```
launchd ──→ com.lampson.gateway.plist
              └── python -m src.daemon
                  ├── 飞书 WebSocket 监听（后台线程）
                  ├── SessionManager + Agent
                  └── signal.pause() 主循环

lampson 命令（独立进程）
└── python -m src.cli（纯交互入口，不连 daemon，不启动 listener）
```

**核心原则**：daemon 承载常驻能力（飞书监听），CLI 是独立交互入口，两者不共享进程。Session 按 channel + sender_id 隔离。

┌──────────────────────────────────────────────────────────┐
│                      Gateway 层                           │
│                                                           │
│   daemon.py (后台)        cli.py (前台)                   │
│   启动飞书监听            纯 REPL 交互                     │
│   WebSocket 非阻塞         不连 daemon，不启动 listener    │
│   ↓ route_to_session()   结果展示                        │
└───────────────────────────────┬──────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────┐
│              SessionManager (src/core/session_manager.py) │
│                                                           │
│   session_manager.get_or_create(channel, sender_id)       │
│   ├─ 检查 last_activity_at 是否 > 180 分钟                │
│   │   └─ 超时：_reset_session()                           │
│   │       1. 调用 LLM 生成 progress summary               │
│   │       2. session_store.end_session(summary=...)        │
│   │       3. _create_session() 自动注入旧 summary         │
│   └─→ 返回 Session 实例                                   │
│                                                           │
│   channel 路由规则：                                      │
│     "cli"        → 全局唯一一个 Session（开发者专用）      │
│     "feishu:*"   → 每个 sender_id 一个独立 Session         │
│     "telegram:*" → 每个 sender_id 一个独立 Session        │
│     "discord:*"  → 每个 sender_id 一个独立 Session         │
└───────────────────────────────┬──────────────────────────┘
                                │ session.handle_input(text)
                                ▼
┌──────────────────────────────────────────────────────────┐
│              Session 层（每渠道/用户独立）                  │
│                                                           │
│   core/session.py                                          │
│   独立的 Agent 实例 / 独立的 LLM.messages / 独立压缩触发   │
│                                                           │
│   Session.from_config(config)  工厂方法（每个 Session 调用一次）│
│   Session.handle_input(text)   统一入口 → HandleResult     │
│   Session.exit()               退出时保存摘要 + 触发压缩    │
└───────────────────────────────┬──────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────┐
│                      Agent 层                             │
│                                                           │
│   core/agent.py             planning/                     │
│   LLM 主循环 + 工具分发      规划器 + 执行器 + 状态机      │
│   max_tool_rounds 循环模式（30轮自动继续，不交用户选择）    │
└────────┬────────────────────────┬───────────────────────┘
         │                        │
    ┌────┴────┐            ┌───────┴────────┐
    │         │            │                │
    ▼         ▼            ▼                ▼
┌──────┐  ┌────────┐  ┌──────────┐  ┌─────────┐
│ LLM  │  │Tool    │  │ Prompt   │  │Compaction│
│Client│  │Registry│  │ Builder  │  │ 归档+摘要│
└──┬───┘  └────┬───┘  └──────────┘  └─────────┘
   │            │
   │       ┌────┴────┬──────────┬──────────┐
   │       ▼         ▼          ▼          ▼
   │   ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐
   │   │ Shell  │ │FileOps │ │  Web   │ │ Feishu │
   │   └────────┘ └────────┘ └────────┘ └─────────┘
   │
   ▼
 智谱 API

         ┌───┴────┐         ┌──────▼─────┐
         │ Memory  │         │  Skills    │
         │ Manager │         │  Manager   │
         └────┬────┘         └──────┬─────┘
              │                    │
         ┌────┴────┐         ┌────▼─────┐
         │ core.md │         │ ~/.lampson/
         │sessions/│         │ skills/  │
         └─────────┘         └──────────┘
```

**核心原则**：每个渠道（channel + sender_id）拥有独立的 Session，各自的 LLM context 独立累积、互不干扰。

### 2.2 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| Daemon 入口 | `src/daemon.py` | daemon 主进程。加载配置、启动飞书监听、boot_tasks 机制、主循环阻塞 |
| CLI 入口 | `src/cli.py` | 纯交互入口（Gateway 层）。单条查询 / REPL 循环，不启动飞书监听，不连 daemon |
| SessionManager | `src/core/session_manager.py` | 管理多个 Session 实例，按 channel+sender_id 路由；**支持 3 小时 idle 超时自动重置 + 跨 session 进度延续** |
| Session | `src/core/session.py` | Agent 生命周期 + 命令路由 + 压缩触发 + 飞书初始化 |
| Agent | `src/core/agent.py` | LLM 主循环，工具调用分发，规划执行，max_tool_rounds 循环 |
| Planning | `src/planning/` | 任务规划器 + 步骤执行器 + Plan 状态机 |
| LLM | `src/core/llm.py` | 封装 OpenAI SDK，支持原生/prompt-based 两种 tool calling |
| PromptBuilder | `src/core/prompt_builder.py` | 分层构建 system prompt（9层） |
| Tools | `src/core/tools.py` | 工具注册表 + 分发调度 |
| Config | `src/core/config.py` | 加载/保存配置，首次运行引导 |
| Memory | `src/memory/manager.py` | 两层记忆（core.md + sessions/） |
| Skills | `src/skills/manager.py` | 技能发现、匹配、加载 |
| Skills Tools | `src/core/skills_tools.py` | Agent 可调用的 skill_view / skills_list / project_context |
| Feishu Client | `src/feishu/client.py` | 飞书 REST API 封装（发送/读取消息） |
| Feishu Listener | `src/feishu/listener.py` | WebSocket 非阻塞监听（daemon thread），消息路由到 SessionManager |
| Self-update | `src/selfupdate/updater.py` | 自更新流程（LLM生成方案 → 用户确认 → git分支执行） |
| Shell Tool | `src/tools/shell.py` | 执行 shell 命令，带危险命令拦截 |
| FileOps Tool | `src/tools/fileops.py` | 文件读写，带大小限制保护 |
| Web Tool | `src/tools/web.py` | DuckDuckGo 网页搜索 |
| Compaction | `src/core/compaction.py` | 上下文压缩：归档+摘要，可迭代 |
| Session Resume | `src/core/session_resume.py` | **新增**：idle 超时生成 progress summary + 新 session 自动加载旧 summary |


## 三、功能清单

### 3.1 已实现

#### 对话交互
- CLI REPL 交互（`lampson` 命令启动）
- 非交互模式（`lampson "query"` 单次查询）
- 管道输入支持（`echo "..." | lampson`）
- 多轮对话（LLM messages 列表维护）

#### LLM 调用
- 智谱 GLM-5.1（OpenAI 兼容接口）
- 支持原生 `tool_calls` 模式（默认）
- 支持 prompt-based tool calling 模式（fallback）
- System prompt 分层构建（9层）
- 模型引导语（Model Guidance）

#### 工具系统（9个工具）
| 工具 | 功能 | 安全机制 |
|------|------|----------|
| `shell` | 执行 shell 命令 | 危险命令拦截（rm -rf /、mkfs 等） |
| `file_read` | 读文件 | 100KB 大小限制 |
| `file_write` | 写文件 | 自动创建父目录 |
| `feishu_send` | 发送飞书消息 | - |
| `feishu_read` | 读取飞书消息 | - |
| `web_search` | DuckDuckGo 搜索 | - |
| `skill_view` | 按需加载技能全文 | - |
| `skills_list` | 列出/搜索技能 | - |
| `project_context` | 加载项目上下文 | - |

#### 记忆系统
- 核心记忆 `core.md`：启动全量加载，5KB 限制，自动警告
- 会话摘要 `sessions/YYYY-MM-DD.md`：退出时写入
- 记忆操作：`/memory show`、`/memory add`、`/memory search`、`/memory forget`

#### 技能系统
- 技能发现：启动时扫描 `~/.lampson/skills/`
- 技能格式：YAML frontmatter + Markdown 正文
- 技能匹配：关键词 + LLM 语义匹配
- 内置技能：code-writing、debug
- 技能操作：`/skills list`、`/skills show`、`/skills create`

#### 飞书通信
- 发送消息（支持 user_id / open_id / chat_id）
- 读取最近消息（按时间倒序）
- WebSocket 长连接监听（非阻塞 daemon thread，路由到 SessionManager）
- 消息去重器（基于 message_id 的滑动窗口 TTL）
- daemon 模式常驻监听飞书，CLI 不启动 listener
- **Session idle 超时重置**：3 小时无活动自动结束当前 session，生成 progress summary，新 session 创建时自动加载旧 summary 延续上下文

#### 自更新
- `/update <需求描述>`：LLM 分析需求 → 生成代码修改方案 → 用户确认 → git 分支执行
- `/update rollback`：回滚到 main 并删除分支
- `/update list`：列出所有 self-update 分支
- 受保护文件：cli.py、agent.py、llm.py、feishu/client.py、tools/shell.py
- 受保护文件修改需额外确认

#### 命令行接口
- `/help`、`/config`、`/exit`
- `/memory`、`/skills`、`/feishu`、`/update`
- 全套 `--memory`、`--skills`、`--feishu`、`--update` 等命令行参数
- daemon 由 launchd 管理，`lampson` 命令是纯 CLI 入口

#### 上下文压缩（Context Compaction）
- 自动检测：agent.run() 返回后检查 token 用量，超过阈值（默认 80%）触发
- 三阶段流程：Classify（分类当前问题）→ Archive（归档有价值内容）→ Summarize（生成结构化摘要）
- 归档策略：LLM 逐条分类消息为 archive/keep/discard，读取已有 skill/project 内容后重新整合（merge/update/evict/append）
- 写回文件：整合后的内容写回 skill/project 文件，不是简单追加
- 迭代压缩：未达标自动继续下一轮，最多 max_iterations 轮
- 结构化摘要格式：问题、约束、已完成、进行中、阻塞、关键决策、待处理、关键文件
- 可配置：触发阈值、结束阈值、context window 大小、是否启用归档
- 压缩失败不影响正常对话

### 3.2 暂未实现（Roadmap）

- MCP Server 接入（预留接口，Phase 2）
- `file_edit`（patch 模式）
- `code_search`（代码搜索）
- 自更新的 LLM 建议触发
- 自更新的定时检查
- 语义搜索记忆
- TUI 界面
- 多用户支持

---

## 四、模块详解

### 4.1 CLI 入口 (`src/cli.py`)

纯 Gateway 层，不含任何业务逻辑。

**核心流程**：

```
main()
  ├─ _parse_args()              解析命令行参数
  ├─ load_config()              加载配置
  ├─ run_setup_wizard()         首次运行引导
  ├─ Session.from_config(config)  工厂方法创建 Session
  └─ _run_repl(session)         REPL 循环
       ├─ prompt_session.prompt()    读取用户输入
       ├─ session.handle_input()     返回 HandleResult
       ├─ result.reply → print       展示回复
       ├─ result.compaction_msg → print  压缩通知
       └─ result.is_exit → break     退出
```

**HandleResult 结构**：
- `reply`: str — 回复文本
- `is_exit`: bool — 是否退出
- `is_command`: bool — 是否 / 命令
- `compaction_msg`: str — 压缩通知

### 4.2 Session (`src/core/session.py`)

中间层，管理 Agent 生命周期和所有业务逻辑。Gateway 层只需调用 `handle_input()`。

```python
class Session:
    @classmethod
    def from_config(cls, config) -> Session   # 工厂：技能→LLM→Agent→飞书

    def handle_input(self, text) -> HandleResult  # 统一入口，更新 last_activity_at
        ├─ /command → _handle_command()  命令路由
        └─ 自然语言 → agent.run() → maybe_compact()

    def init_feishu(self) -> bool              # 飞书客户端初始化
    def start_feishu_listener(self) -> None    # 启动 WebSocket 监听（daemon thread，非阻塞）
    def save_summary(self) -> None             # 退出时保存会话摘要
    def _inject_resume_summary(self, summary) -> None  # 注入上一条 session 的 progress summary 到 system prompt
```

**新增属性**：
- `last_activity_at: float` — 秒级时间戳，`handle_input()` 被调用时更新
- `_session_manager: SessionManager` — SessionManager 引用（用于 FeishuListener 路由）

### 4.3 SessionManager Idle 重置 (`src/core/session_manager.py`)

**核心机制**：Session 3 小时（180 分钟）无任何对话活动自动结束，生成 progress summary，新 session 创建时自动加载旧 summary。

```python
IDLE_TIMEOUT_MINUTES = 180  # 3 小时

class SessionManager:
    def get_or_create(self, channel, sender_id) -> Session:
        # 进入时检查旧 session 是否 idle 超时
        # 超时：_reset_session() → 生成 summary → 结束旧 session → 创建新 session → 注入旧 summary
        # 未超时：直接返回现有 session

    def _is_idle_expired(self, session) -> bool:
        # 检查 session.last_activity_at > 180 分钟

    def _reset_session(self, channel, sender_id, is_cli) -> None:
        # 1. 读取旧 session 所有消息
        # 2. 调用 LLM 生成 "任务/已完成/下一步" progress summary
        # 3. session_store.end_session(old_id, summary=summary)
        # 4. _create_session() 自动加载上一条已结束 session 的 summary
        # 5. 新 session._inject_resume_summary() 追加到 system prompt

    def _create_session(self, channel, sender_id) -> Session:
        # 1. session_store.create_session() 写入新 session
        # 2. session_store.get_last_session_summary() 加载上一条已结束 session 的 summary
        # 3. Session.from_config() 创建 Session
        # 4. 若有 summary，调用 _inject_resume_summary() 注入到 system prompt
```

**Summary 生成**（`src/core/session_resume.py`）：
- Prompt 要求：简洁、200字以内、三要素（任务/已完成/下一步）
- 生成失败时 summary 为空，不阻塞重置流程
- 只在 idle reset 时生成，手动 `/stop` 不生成

**注入格式**：
```
## 上一轮会话进展

上一轮会话因超过 3 小时无活动而结束，以下是当时的进展：

{summary}

请继续推进上述任务。如果任务已完成或有新需求，请告知用户。
```

**命令路由**：`/help` `/config` `/memory` `/skills` `/feishu` `/update` `/exit` 全部在 Session 内部处理。

### 4.4 Agent 主循环 (`src/core/agent.py`)

```python
class Agent:
    def run(self, user_input: str) -> str:
        self.llm.add_user_message(user_input)
        if native: return self._run_native()   # tool_calls 模式
        else:      return self._run_prompt_based()  # XML tag 模式

    def _run_native(self):
        for _ in range(MAX_TOOL_ROUNDS=10):
            response = self.llm.chat(tools=schemas)
            if stop: return content
            for tool_call in message.tool_calls:
                result = dispatch(tool_name, args)
                self.llm.add_tool_result(id, result)
```

**设计决策**：
- 最多 10 轮工具调用，防止死循环
- Skills 通过 `skill_view(name)` 工具按需加载，不每轮自动注入
- `_inject_tools_prompt()` 只在 prompt-based 模式注入一次

### 4.5 LLM 客户端 (`src/core/llm.py`)

```python
class LLMClient:
    def __init__(
        api_key, base_url, model,
        supports_native_tool_calling=True
    ):
    def chat(tools=None) -> ChatCompletion:
        # 原生模式: 传 tools 给 SDK
        # prompt模式: 不传 tools，暂存到 _pending_tools
```

**异常处理**：超时、连接错误、频率限制

### 4.6 分层 System Prompt (`src/core/prompt_builder.py`)

9层结构：

| 层 | 内容 | 说明 |
|----|------|------|
| L1 | Identity | `~/.lampson/SOUL.md` 全文，Lampson 身份声明 |
| L2 | Tool Guidance | Memory/Skills/Session-Search 使用指引 |
| L3 | Memory Block | `core.md` 全文 |
| L4 | Project Index | 项目索引 + `project_context` 工具 |
| L5 | Context Files | `.lampson.md` / `AGENTS.md` |
| L6 | Model Guidance | 模型适配语（GLM 等） |
| L7 | Platform Hints + **USER.md** + DAEMON_HINTS | CLI 环境提示；**USER.md 全文（用户画像）**；daemon 身份声明 |
| L8 | Timestamp | 会话开始时间 |

**SOUL.md vs USER.md 边界**：SOUL.md 是 Lampson 的自我认知，USER.md 是服务对象的画像。两者分离，不混在一起。
- SOUL.md：Lampson 是什么、怎么运行、会什么工具
- USER.md：用户昵称、chat_id、沟通偏好、渠道偏好

### 4.7 工具注册与分发 (`src/core/tools.py`)

```python
_REGISTRY: dict[str, tuple[schema, runner]]

def dispatch(tool_name, arguments_raw):
    # JSON字符串 → dict → runner 执行
    # 异常捕获，返回错误信息字符串
```

每个工具提供：schema（OpenAI function calling 格式）+ runner（实际执行函数）。

### 4.8 记忆管理 (`src/memory/manager.py`)

两层架构：
- **core.md**：键值对风格，启动全量加载
- **sessions/YYYY-MM-DD.md**：退出时 LLM 生成摘要，按时间段追加

关键函数：
- `add_memory()`：追加时间戳条目
- `search_memory()`：关键词搜索 core + sessions
- `forget_memory()`：删除含关键词的条目

### 4.9 技能管理 (`src/skills/manager.py`)

SKILL.md 格式：
```yaml
---
name: skill-name
description: 简短描述
triggers:
  - 关键词1
  - 关键词2
---
## 正文
```

两种匹配方式：
- **关键词匹配**：`match_skill()` 简单字符串包含
- **LLM 语义匹配**：`match_skill_with_llm()`（需要 LLM 调用）

### 4.10 飞书客户端 (`src/feishu/client.py`)

- `FeishuClient`：封装所有 REST API 调用
- **自动刷新 token**：每 2 小时刷新，留 200s 余量
- `send_message()`：发送文本消息
- `get_messages()`：拉取历史消息（REST API，非 WebSocket 轮询）
- 全局单例模式：`init_client()` → `get_client()`

### 4.11 飞书监听 (`src/feishu/listener.py`)

纯 Gateway 层，基于 `lark_oapi` WebSocket 长连接，非阻塞（daemon thread）：

```
start()
  ├─ threading.Thread(target=ws_client.start, daemon=True)
  └─ t.start()  立即返回，REPL 继续
```

- `MessageDeduplicator`：基于 message_id 的滑动窗口 TTL 去重
- `_handle_message()`：解析消息 → `session_manager.get_or_create(...)` 取得 `Session` → `session.handle_input()` → 回复
- 路由：消息根据 channel + sender_id 分发到对应 Session

### 4.12 自更新 (`src/selfupdate/updater.py`)

```python
run_update(description, llm):
    1. _check_git_clean()     检查工作区干净
    2. _generate_update_plan()  LLM 生成 JSON 方案
    3. _display_plan()         展示给用户
    4. input("确认?")         用户确认
    5. git checkout -b self-update/<timestamp>
    6. 写入文件
    7. git add + commit
```

LLM 返回格式：
```json
{
  "summary": "...",
  "files": [
    {"path": "...", "action": "create/modify", "content": "...", "reason": "..."}
  ]
}
```

### 4.13 上下文压缩 (`src/core/compaction.py`)

设计文档：`docs/compaction-design.md`

压缩触发由 Session 层统一管理（`agent.maybe_compact()`），不在 gateway 层调用。

**三阶段流程**：

```
agent.run() 返回
     │
     ▼
total_tokens > context_window × 80% ?
│  否 → 不压缩
│  是 → 进入压缩流程
     │
     ▼
┌─ Phase 1: Classify ──────────────────────┐
│  LLM 分析对话历史，提取：               │
│  - 当前问题（一句话描述）               │
│  - 相关项目名                           │
│  - 相关技能名                           │
└──────────────────────────────────────────┘
     │
     ▼
┌─ Phase 2: Archive ───────────────────────┐
│  1. 读取已有 skill/project 文件内容      │
│  2. LLM 逐条分类消息：                  │
│     - archive：有长期价值               │
│     - keep：当前问题核心上下文          │
│     - discard：寒暄/无关               │
│  3. 整合已有内容 + 新归档内容：         │
│     merge/update/evict/append           │
│  4. 写回 skill/project 文件             │
└──────────────────────────────────────────┘
     │
     ▼
┌─ Phase 3: Summarize ─────────────────────┐
│  对剩余 keep 消息生成结构化摘要：       │
│  问题/约束/进度/决策/关键文件           │
└──────────────────────────────────────────┘
     │
     ▼
达标？→ 是 → 摘要替换对话历史
     → 否 → 继续下一轮压缩
```

### 4.14 任务规划 (`src/planning/`)

设计文档：`docs/planning-design.md`

**模块组成**：

| 文件 | 内容 |
|------|------|
| `steps.py` | `PlanStatus` / `StepStatus` / `Step` / `Plan` 数据类 + Plan 状态机 |
| `planner.py` | `Planner` 类：调 LLM 生成步骤、JSON 解析、action 校验与模糊修正、replan |
| `executor.py` | `Executor` 类：参数引用解析（`$prev.result` / `$step[N].result` / `$goal`）、重试、失败处理 |
| `prompts.py` | 规划/重新规划/汇总 prompt 模板 + 上下文构建 |

**Agent.run() 集成**：所有输入统一走规划器（1-step 退化 + 规划失败回退到直接对话）。

**Plan 状态机**：`pending → planning → executing → completed/failed/cancelled`

**关键设计决策**：
- 归档而非丢弃：有长期价值的内容写入 skill/project 文件
- 重新整合而非简单追加：LLM 读取已有内容 + 新内容，执行 merge/update/evict/append
- 可迭代：未达标自动继续压缩
- 压缩失败兜底：LLM 调用失败时截取前2000字作为紧急摘要

---

## 五、配置说明

配置文件路径：`~/.lampson/config.yaml`

```yaml
llm:
  api_key: ""                                     # 智谱 API Key（必填）
  base_url: "https://open.bigmodel.cn/api/paas/v4/"  # LLM API 地址
  model: "glm-5.1"                               # 模型名
  native_tool_calling: true                        # 是否使用原生 tool_calls（默认 true）

feishu:
  app_id: ""                                      # 飞书应用 App ID（可选）
  app_secret: ""                                  # 飞书应用 App Secret（可选）
  chat_ids: []                                    # 要监听的 chat_id 列表（可选）

memory_path: "~/.lampson/memory"                  # 记忆文件目录
skills_path: "~/.lampson/skills"                  # 技能文件目录

compaction:
  enabled: true                                   # 是否启用自动压缩
  trigger_threshold: 0.8                          # 触发压缩的 token 占比（0-1）
  end_threshold: 0.3                              # 压缩后的目标 token 占比
  context_window: 131072                          # 模型上下文窗口大小（token 数）
  max_iterations: 3                               # 单次压缩最大迭代轮数
  enable_archive: true                            # 是否启用归档阶段（写入 skill/project）

# MCP 服务器配置（Phase 2 预留）
# mcp:
#   servers:
#     - name: filesystem
#       command: "npx"
#       args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
#       enabled: true
```

**首次运行**：`lampson` 启动 REPL，若未配置则自动进入 `run_setup_wizard()` 引导填写 API Key 等信息。

---

## 六、数据流

### 6.1 CLI 对话流程

```
用户输入自然语言
    │
    ▼
cli.py: session.handle_input(text)
    │
    ▼
Session.handle_input()
    ├─ /command → 命令路由（_handle_command）
    └─ 自然语言 → Agent.run(user_input)
                      │
                      ├─ Planner：生成执行步骤（1-step 退化）
                      ├─ Executor：逐步执行
                      │   └─ LLMClient.add_user_message()
                      │
                      ▼
                  LLM chat(tools=schemas)
                      │ (原生 tool_calls)
                      ▼
                ┌─ 返回 content ───────────────────┐
                │         ↓                       │
                │  有 tool_calls → dispatch()     │
                │         ↓                       │
                │  工具执行结果                   │
                │         ↓                       │
                │  LLMClient.add_tool_result()   │
                │         ↓                       │
                │  再次 chat() ◀─────────────┐   │
                │         ↓                  │   │
                └──── 循环直到 stop ──────────┘   │
                      │
                      ▼
                  返回 HandleResult → cli.py → 打印给用户
```

### 6.2 飞书消息处理流程（WebSocket）

```
飞书服务器
  │ WebSocket 长连接
  ▼
FeishuListener._handle_message(data)
  │
  ├─ 跳过 sender_type=app（机器人自己的消息）
  ├─ MessageDeduplicator 去重
  └─ 提取 text 字段
      │
      ▼
  session.handle_input(text)  →  HandleResult
      │
      ▼
  result.reply
      │
      ▼
  FeishuListener._send_reply(chat_id, text)
      │
      ▼
  飞书 API: POST /im/v1/messages
      │
      ▼
  用户收到回复
```

### 6.3 Daemon 启动流程（boot_tasks）

```
launchd 拉起 daemon
  → load_config()
  → SessionManager 初始化
  → session.start_feishu_listener()  ← 后台线程，WebSocket 连接
  → _write_boot_task({"task": "通知哥哥上线"})
  → time.sleep(2)  ← 等 WebSocket 就绪
  → _load_and_clear_boot_tasks()
  → LLM 执行 boot_task → feishu_send → 发飞书消息
  → daemon 进入 signal.pause() 主循环
```

**重启机制**：禁止执行 `launchctl unload`（KeepAlive 会失效）。LLM 重启自己前必须先写 `boot_tasks.json`，daemon 拉起后读取并执行待办。

---

## 七、部署方式

### 7.1 安装

```bash
cd ~/lampson
pip install -e .
```

### 7.2 运行

```bash
# 交互式 REPL
lampson

# 单次查询（非交互）
lampson "帮我查看当前目录"
echo "query" | lampson

# daemon（由 launchd 管理，不需要手动启动）
# python -m src.daemon  # 调试用，直接运行
```

**daemon 管理：**
```bash
launchctl load ~/Library/LaunchAgents/com.lampson.gateway.plist   # 启动
launchctl unload ~/Library/LaunchAgents/com.lampson.gateway.plist # 停止（不要轻易执行，会破坏 KeepAlive）
```

### 7.3 配置

```bash
# 首次运行会自动引导配置
lampson

# 或手动编辑
vim ~/.lampson/config.yaml
```

### 7.4 目录结构

```
~/.lampson/
├── config.yaml          # 主配置文件
├── SOUL.md              # Lampson 身份声明
├── USER.md              # 用户画像（注入 system prompt）
├── boot_tasks.json      # 重启前待办（daemon 读后清空）
├── memory/
│   ├── core.md          # 核心记忆
│   └── sessions/        # 会话摘要
│       └── 2026-04-24.md
├── skills/              # 用户技能
│   ├── code-writing/
│   │   └── SKILL.md
│   └── debug/
│       └── SKILL.md
├── projects_index.md    # 项目索引
└── logs/
    ├── launchd.log     # daemon stdout
    └── launchd.err.log  # daemon stderr
```

### 7.5 内置技能安装

首次运行时，`Session.from_config()` 自动调用 `_install_default_skills()` 将 `config/default_skills/` 中的技能复制到 `~/.lampson/skills/`（已存在的不覆盖）。

---

## 八、当前状态

### 8.1 已完成

| 功能 | 状态 | 备注 |
|------|------|------|
| CLI REPL | done | 交互式 + 非交互模式，纯 Gateway 层 |
| Session 中间层 | done | 三层架构（Gateway→Session→Agent） |
| LLM 对话 | done | 原生 + prompt-based tool calling |
| Shell 工具 | done | 危险命令拦截 |
| 文件读写 | done | 大小限制保护 |
| 网页搜索 | done | DuckDuckGo HTML |
| 飞书发送/读取 | done | REST API |
| 飞书 WebSocket 监听 | done | 长连接 + 去重，走 Session |
| 飞书轮询监听 | done | 备选方案（已删除，只保留 WebSocket） |
| 核心记忆 | done | core.md 全量加载 |
| 会话摘要 | done | 退出时写入 |
| 技能系统 | done | 发现/匹配/加载 |
| 自更新 | done | git 分支 + 回滚 |
| 首次运行引导 | done | API Key 配置 |
| Prompt 分层 | done | 9层 system prompt |
| Context Compaction | done | 三阶段压缩，14个测试全通过 |
| 任务规划 (Planning) | done | Plan-and-Execute，30个测试全通过 |
| /model 多模型对比 | done | `/model all` 并发实时流式对比，`/model <name>` 切换（方案B） |
| 过期消息丢弃 | done | 飞书投递延迟 >60s 的消息自动丢弃 |
| 项目文档 | done | PROJECT.md 完整梳理 |

### 8.2 2026-04-25 更新：/model 多模型对比 + 飞书稳定性

**改动概要**（commit `7d9a9e3` + `c3eef23`）：

| 改动 | 文件 | 说明 |
|------|------|------|
| `/model all` 多模型实时对比 | `session.py` | 并发查询多个模型，每轮工具调用实时通过飞书 partial_sender 推送 |
| `/model <name>` 模型切换（方案B） | `session.py`, `agent.py` | 切换时迁移对话历史到新 client，system prompt 按模型重新生成 |
| clone_for_inference | `llm.py` | 只带 system prompt 的轻量克隆，用于 /model all 避免深拷贝 |
| 裸 JSON 工具调用解析 | `session.py` | GPTOssModel 有时输出 `{"command":"..."}` 不走 `<tool_call:xxx>` 格式，加 json.loads fallback |
| PLATFORM_HINTS 远程机器提示 | `prompt_builder.py` | 强制要求先 `project_context("machines")` 获取 SSH 别名，find 加 `-maxdepth` |
| max_tool_rounds 循环模式 | `config.yaml`, `agent.py` | 从 config 读取，默认 30。30轮内解决则返回；达到上限则 LLM 总结现状后**自动继续**（不交用户选择），直到 LLM 主动声明完成 |
| MessageDeduplicator TTL | `listener.py` | 60s → 600s，/model all 工具调用耗时超过 60s 导致重复处理 |
| 过期消息丢弃 | `listener.py` | 投递延迟超过 60 秒的消息直接丢弃，防止飞书 WebSocket 积压后补投旧消息 |
| executor `_safe_replace_value` | `executor.py` | 多行 `$step[N].result` 引用截断为第一行，避免破坏 shell 命令语法 |
| 删除 poller.py | `feishu/` | 只保留 WebSocket listener 模式 |

**当前模型配置**：

| 模型 | base_url | tool calling 模式 |
|------|----------|------------------|
| GPTOssModel | `http://openai-gpt.test.beemai.svc/v1` | prompt-based（无原生 tool_calls） |
| MiniMax-M2.7-highspeed | `https://api.minimaxi.com/v1/` | native tool_calls |

**部署**：launchd 守护 (`~/Library/LaunchAgents/com.lampson.gateway.plist`)，日志 `~/.lampson/logs/launchd.log`

### 8.3 当前阶段：v0.2 — 智能化增强

从"能跑的工具箱"进化到"能独立做复杂任务的智能体"。

#### TODO 1：Skill & Project 总结能力

**目标**：Lampson 能主动归纳、总结会话中积累的经验和知识，自动维护 skill 和 project 文件。

现状：
- `src/core/compaction.py` 已实现归档阶段（Archive Phase），能将对话中有价值的内容写入 skill/project 文件
- Skill 文件格式已有（YAML frontmatter + Markdown）
- Project 上下文机制已有（`project_context` 工具）

待实现：
- [ ] **主动总结触发**：不仅仅是 compaction 时被动归档，用户说"总结一下"/"记下来"时也能主动总结
- [ ] **总结质量**：LLM 生成的总结需要结构化（背景、要点、踩坑、结论），不是简单压缩
- [ ] **已有内容去重**：总结前先读已有 skill/project 内容，避免重复写入相同知识点
- [ ] **`/skills edit` 和 `/skills delete`**：PRD 中定义但代码未实现的 skill 管理命令
- [ ] **`memory update` / `memory compact`**：PRD 中定义但代码未实现的记忆管理命令

#### TODO 2：多轮复杂任务执行

**目标**：Lampson 能接一个复杂任务（如"帮我部署 XXX"、"实现 YYY 功能"），自主拆解、多轮执行、跟踪进度、遇到问题自行调整。

现状：
- Agent 单轮对话能力强（LLM + 9个工具）
- Compaction 保证长对话不爆上下文
- **任务规划器已实现**：`src/planning/` 模块（Planner + Executor + Plan 状态机），30 个测试全通过
- Agent.run() 已集成规划器，所有输入统一走 Plan-and-Execute（1-step 退化 + 失败回退）
- 设计文档：`docs/planning-design.md`

已实现：
- [x] **任务规划器（Planner）**：接收复杂任务后生成结构化执行计划（步骤列表）
- [x] **步骤跟踪（Plan 状态机）**：pending → planning → executing → completed/failed/cancelled
- [x] **失败处理**：Executor 支持失败后回退到直接对话
- [x] **参数引用**：`$prev.result` / `$step[N].result` / `$goal` 步骤间传参

待实现：
- [ ] **中途校验（Checkpoint）**：关键步骤后校验结果，不一致则回退或调整计划
- [ ] **Replan**：执行失败时重新规划（接口已有，prompt 待优化）
- [ ] **进度报告**：向用户汇报当前执行到哪一步、预计还需多久
- [ ] **人工确认点**：高风险操作（删除、部署上线等）在执行前暂停等用户确认
- [ ] **并发子任务**：多个独立步骤可以并行执行（如果工具支持）

### 8.3 Roadmap（优先级排序）

| 优先级 | 功能 | 依赖 |
|--------|------|------|
| ~~P0~~ | ~~多轮任务规划器（Planner）~~ | ~~已完成~~ |
| ~~P0~~ | ~~步骤跟踪 + Plan 状态机~~ | ~~已完成~~ |
| ~~P0~~ | ~~失败处理 + 参数引用~~ | ~~已完成~~ |
| P0 | 中途校验（Checkpoint）+ Replan | Planner |
| P0 | 进度报告 + 人工确认点 | Planner |
| P1 | `/skills edit`、`/skills delete` | - |
| P1 | `memory update`、`/memory compact` | - |
| P1 | 主动总结触发（"记下来"等指令） | - |
| P1 | 并发子任务执行 | Planner + 工具并发 |
| P2 | MCP Server 接入 | - |
| P2 | `file_edit`（patch 模式） | - |
| P2 | `code_search`（代码搜索） | - |
| P3 | 语义搜索记忆 | Embedding 模型 |
| P3 | 自更新的自动触发机制 | - |
| P3 | TUI 界面 | - |

---

## 九、已知问题和限制

1. **飞书 WebSocket 重连**：网络波动时断线后不会自动重连，需手动重启
2. **Session summary 生成**：退出时用临时 LLMClient 生成摘要，若 API 异常则回退到截取前500字
3. **危险命令拦截**：正则匹配可能漏掉变形写法
4. **文件大小限制**：读文件 100KB 上限，大文件场景需多次分段读取
5. **Skills 语义匹配**：`match_skill_with_llm()` 需要额外 LLM 调用，有延迟和 token 开销
6. **Compaction 压缩质量**：依赖 LLM 对内容价值的判断，可能误判归档/丢弃
7. **Planning prompt 待优化**：Replan 场景的 prompt 需要更多测试数据打磨
8. **MiniMax 不稳定读 machines**：有时跳过 `project_context("machines")` 直接猜 SSH 别名，需在 system prompt 中强制要求
9. **GPTOssModel 输出不确定**：低 temperature（0.3）下稳定走 `<tool_call:xxx>` 格式，高 temperature 偶尔返回空 content

---

## 十、项目文件索引

| 文件 | 说明 |
|------|------|
| `docs/PRD.md` | 产品需求文档 |
| `docs/PROJECT.md` | 本文档 |
| `docs/compaction-design.md` | Context Compaction 设计文档 |
| `docs/planning-design.md` | 任务规划设计文档 |
| `pyproject.toml` | 包配置 |
| `config/default.yaml` | 默认配置模板（含 compaction 配置） |
| `config/default_skills/` | 内置技能 |
| `.cursorrules` | Cursor 开发规范 |
| `src/daemon.py` | daemon 主进程（启动飞书监听 + boot_tasks） |
| `src/cli.py` | CLI 入口（纯 Gateway：参数解析 + REPL） |
| `src/core/session.py` | Session 中间层（生命周期 + 命令路由 + 压缩） |
| `src/core/agent.py` | Agent 主循环（LLM + 工具 + 规划执行） |
| `src/core/llm.py` | LLM 调用封装 |
| `src/core/prompt_builder.py` | 分层 Prompt 构建器 |
| `src/core/tools.py` | 工具注册与分发 |
| `src/core/config.py` | 配置管理 |
| `src/core/skills_tools.py` | Skills 工具（skill_view 等） |
| `src/core/compaction.py` | 上下文压缩（归档+摘要） |
| `src/planning/__init__.py` | 规划模块入口 |
| `src/planning/steps.py` | Plan/Step 数据类 + 状态机 |
| `src/planning/planner.py` | 规划器（LLM 生成步骤） |
| `src/planning/executor.py` | 执行器（参数引用 + 重试 + 失败处理） |
| `src/planning/prompts.py` | 规划相关 prompt 模板 |
| `src/memory/manager.py` | 记忆管理器 |
| `src/skills/manager.py` | 技能管理器 |
| `src/feishu/client.py` | 飞书 API 客户端 |
| `src/feishu/listener.py` | 飞书 WebSocket 监听 + 消息去重 + 过期丢弃 |
| `src/tools/shell.py` | Shell 执行工具 |
| `src/tools/fileops.py` | 文件读写工具 |
| `src/tools/web.py` | 网页搜索工具 |
| `src/selfupdate/updater.py` | 自更新逻辑 |
| `tests/test_compaction.py` | Compaction 单元测试（14个） |
| `tests/test_planning.py` | Planning 单元测试（30个） |
