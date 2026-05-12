# Lamix 项目文档

> 自主运行的 AI Agent daemon，帮用户把事情做完、做好。

---

## 一、项目概述

**技术栈**：Python 3.11+，智谱 GLM（OpenAI 兼容接口），飞书开放平台 REST API

**核心能力**：
- 多工具调用循环（30 轮自动继续）
- 任务规划与执行（Plan-and-Execute）
- 上下文压缩（轮次分段 + LLM 摘要）
- 反思沉淀（自动创建/更新 skill 和 project）
- 飞书 WebSocket 长连接 + 中断抢占
- 自更新（git 分支 + 回滚）

---

## 二、架构

```
launchd ──→ daemon.py
              ├── 飞书 WebSocket 监听（daemon thread）
              ├── SessionManager（按 channel+sender_id 隔离）
              └── asyncio.run(pm.run())

lamix 命令 ──→ cli.py（独立 REPL，不连 daemon）
```

每个渠道拥有独立 Session（Agent + LLM messages），互不干扰。Session 3 小时 idle 自动重置。

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| Daemon | `src/daemon.py` | 后台主进程，启动飞书监听 + boot_tasks |
| CLI | `src/cli.py` | 纯 REPL 入口，不启动飞书监听 |
| SessionManager | `src/core/session_manager.py` | 按 channel+sender_id 路由 Session，idle 超时重置 |
| Session | `src/core/session.py` | Agent 生命周期 + 命令路由 + 压缩触发 + 飞书初始化 |
| Agent | `src/core/agent.py` | LLM 主循环，工具分发，规划执行，fallback，中断检查 |
| Planning | `src/planning/` | Planner + Executor + Plan 状态机 |
| LLM | `src/core/llm.py` | OpenAI SDK 封装，消息管理 |
| PromptBuilder | `src/core/prompt_builder.py` | 分层 system prompt（L1-L5） |
| Tools | `src/core/tools.py` | 工具注册表 + dispatch |
| Config | `src/core/config.py` | 配置加载/保存，路径迁移 |
| Memory | `src/memory/manager.py` | MEMORY.md 读写（load/add/search/forget） |
| Skills Tools | `src/core/skills_tools.py` | skill / info / project_context / search_projects |
| Feishu Client | `src/feishu/client.py` | 飞书 REST API（发送/读取/卡片） |
| Feishu Listener | `src/feishu/listener.py` | WebSocket 非阻塞监听，消息路由 |
| Compaction | `src/core/compaction.py` | 轮次压缩：split_into_turns → 策略 A/B → LLM 摘要 |
| Reflection | `src/core/reflection.py` | 反思沉淀（skill/project create/update） |
| Indexer | `src/core/indexer.py` | SkillIndex / ProjectIndex，增量构建 |
| Interrupt | `src/core/interrupt.py` | AgentInterrupted 异常 + 中断标志位 |
| Adapters | `src/core/adapters/` | 多模型适配（OpenAI 兼容 / MiniMax） |
| Heartbeat | `src/core/heartbeat.py` | 心跳管理器 |
| Watchdog | `src/watchdog.py` | 独立看门狗进程，监控 daemon |
| Self-update | `src/selfupdate/updater.py` | git 分支自更新 |

---

## 三、Setup Wizard 流程

首次运行 `lamix cli` 时自动进入配置向导：

| 步骤 | 内容 | 可跳过 |
|------|------|--------|
| 1 | 选择 LLM 供应商（GLM/MiniMax/DeepSeek/自定义），上下键选择 | 否 |
| 2 | 输入 API Key | 否 |
| 3 | 选择模型（上下键选择） | 否 |
| 4 | 连通性验证 | 自动 |
| 5 | 飞书配置（App ID、App Secret、Chat IDs） | 是 |
| 6 | Fallback 模型配置（选供应商→选模型→输入 Key，可连续配多个） | 是 |
| 7 | 保存配置 | 自动 |
| 8 | 用户画像（称呼、偏好、主渠道） | 是 |

Fallback 模型配置流程：
1. 选择供应商（GLM/MiniMax/DeepSeek/自定义/跳过）
2. 选择模型（上下键选择）
3. 输入 API Key（同供应商自动继承主 Key，否则单独输入）
4. 询问"继续添加？"→ 是则回到步骤 1，否则结束
5. 最多 5 个 fallback 模型

配完后自动安装飞书专属 skills（如果配了飞书），daemon 运行中则 30 秒内热重载生效。

## 四、工具列表

| 工具名 | 功能 |
|--------|------|
| `shell` | 执行 shell 命令（危险命令拦截） |
| `search` | ripgrep 文件名/内容搜索（mode=files/content） |
| `file_read` | 读文件（100KB 限制） |
| `file_write` | 写文件（自动创建父目录） |
| `feishu_send` | 发送飞书消息（text/card） |
| `feishu_read` | 读取飞书会话消息 |
| `skill` | 加载/搜索技能（action=view/search） |
| `info` | 加载 info 知识文件 |
| `project_context` | 加载项目上下文 |
| `search_projects` | 语义搜索项目 |
| `session` | 搜索/加载历史会话 |
| `web_search` | 网页搜索 |
| `task_schedule` | 注册定时任务（interval/cron/delayed） |
| `task_list` | 查看定时任务 |
| `task_cancel` | 取消定时任务 |
| `desktop_*` | 桌面操作系列（截图/点击/输入/滚动） |
| `vision_analyze` | 视觉分析 |
| `reflect_and_learn` | 反思沉淀（任务完成后自主判断是否持久化知识） |

---

## 五、PromptBuilder 分层

| 层 | 内容 |
|----|------|
| L1 | Identity（MEMORY.md）+ User（USER.md） |
| L2 | Tool Guidance + Skills 索引（名称+描述，无触发词） |
| L3 | Project Index（动态扫描 projects/*.md） |
| L4 | Model Guidance（模型适配提示） |
| L5 | Channel Context（非 CLI 时注入消息来源） |

Skills 索引只展示名称和描述，触发由 LLM 自主判断。详情通过 `skill(action='view')` 按需加载。

---

## 六、数据目录

```
~/.lamix/
├── config.yaml              # 主配置
├── MEMORY.md                # Agent 身份 + 行为准则（500字符限制）
├── USER.md                  # 用户画像 + 偏好
├── boot_tasks.json          # 重启前待办
├── memory/
│   ├── skills/              # 技能（每个子目录含 SKILL.md）
│   ├── projects/            # 项目信息（*.md）
│   ├── info/                # 知识文件（*.md）
│   ├── sessions/            # 会话 JSONL
│   │   └── tool_bodies/     # 大型工具结果
│   └── errors.jsonl         # 错误日志
├── index/                   # 索引文件（skills.jsonl, projects.jsonl）
├── search.db                # SQLite FTS5 搜索
├── task_scheduler.db        # APScheduler 持久化
├── metrics.jsonl            # 任务指标
├── archived/                # 归档的 skills/info/projects
│   ├── skills/
│   ├── projects/
│   └── info/
└── logs/                    # daemon 日志
```

---

## 七、配置

`~/.lamix/config.yaml`：

```yaml
llm:
  api_key: ""
  base_url: "https://open.bigmodel.cn/api/paas/v4/"
  model: "glm-5.1"
models: []                    # fallback 模型列表
feishu:
  app_id: ""
  app_secret: ""
  chat_ids: []
memory_path: "~/.lamix/memory"
skills_path: "~/.lamix/memory/skills"
projects_path: "~/.lamix/memory/projects"
info_path: "~/.lamix/memory/info"
retrieval:
  skill_top_k: 3
  project_top_k: 2
  similarity_threshold: 0.3
skills_management:
  cleanup_max_skills: 300
  cleanup_age_days: 10
  cleanup_min_invocations: 0
```

---

## 八、命令

### CLI 子命令（shell 入口）

| 命令 | 功能 |
|------|------|
| `lamix cli [query]` | 交互式 CLI（含 daemon），可跟查询内容直接对话 |
| `lamix gateway` | 仅启动 daemon（后台常驻，飞书消息接收） |
| `lamix model` | 重新配置 LLM 模型（供应商/模型/API Key） |
| `lamix update` | 从 GitHub 拉取最新代码，自动重启 daemon |
| `lamix config` | 显示当前配置 |
| `lamix -V` | 显示版本号 |

### REPL 内部命令（`lamix cli` 交互模式下）

| 命令 | 功能 |
|------|------|
| `/help` | 帮助信息 |
| `/config` | 查看/编辑配置 |
| `/model` | 切换/对比模型 |
| `/memory` | 记忆管理（show/add/search/forget） |
| `/skills` | 技能管理 |
| `/feishu` | 飞书操作 |
| `/update` | 自更新 |
| `/compaction` | 手动触发压缩 |
| `/contextsize` | 查看当前上下文长度和使用率 |
| `/search` | 跨 session 搜索历史 |
| `/resume` | 加载历史 session |
| `/new` | 结束当前 session，创建空白 |
| `/background` | 后台任务 |
| `/tasks` | 查看后台任务 |
| `/cancel` | 取消任务 |
| `/metrics` | 任务统计 |
| `/safemode` | 安全模式 |
| `/exit` | 退出 |

---

## 九、特色功能


### 9.1 Boot Tasks（重启验证）

改了 daemon 代码后需要重启才能生效。Boot Task 机制让你在重启前写好验证任务，daemon 重启后自动执行并汇报结果。

**使用场景**：改了 compaction 逻辑，重启后自动验证压缩是否正常。

```bash
# 写入 boot task（重启前执行）
file_write("~/.lamix/boot_tasks.json", '[{"task": "验证改动：发几条消息测试交互，检查日志无异常"}]')

# 重启 daemon
/restart
```

daemon 启动后读取 boot_tasks.json，把任务注入 session 执行，完成后清空文件。

### 9.2 定时任务

通过 `task_schedule` 工具注册定时任务，支持三种触发方式：

| 类型 | 说明 | 示例 |
|------|------|------|
| `interval` | 固定间隔 | 每 30 分钟检查一次 |
| `cron` | 定时执行 | 每天凌晨 4 点自我审计 |
| `delayed` | 一次性延迟 | 5 分钟后提醒 |

```bash
# 注册间隔任务
task_schedule(action="schedule", task_id="monitor", task_type="interval", interval_seconds=1800, prompt="检查服务状态")

# 注册 cron 任务
task_schedule(action="schedule", task_id="daily_report", task_type="cron", cron_hour=9, cron_minute=0, prompt="发送昨日工作汇总")

# 查看所有任务
task_schedule(action="list")

# 取消任务
task_schedule(action="cancel", task_id="monitor")
```

### 9.3 自我审计与知识生命周期

daemon 通过定时审计（每天凌晨 4 点）和空闲触发（24h 空闲 / 最后使用后 1h，每 4h 检查）扫描 skills/projects/info 的健康状态：

**自动修复**：
- 空目录删除、散落 .md 合并到 SKILL.md、缺失 frontmatter 自动生成
- 重叠检测：检查 skill 之间的职责重叠，建议合并

**知识归档**：
- 7 天未使用且调用次数为 0 → 自动归档
- 30 天未使用 → 自动归档
- 归档不删除，移入 `~/.lamix/archived/`，可通过 `list_archived` 查看、`restore_archived` 恢复
- `last_used_at` 在每次 skill view、info 加载、project_context 加载时自动更新

**问题报告**：通过飞书发送审计结果

可在 `config.yaml` 中关闭：

```yaml
self_audit:
  enabled: false
```

### 9.4 桌面控制（键鼠 + 截图）

Lamix 可以操作鼠标键盘和截屏，实现 GUI 自动化。

**前提条件**：
- macOS：系统设置 → 隐私与安全 → 辅助功能 → 授予终端/Python 权限
- Windows：以管理员身份运行，或授予对应权限
- 依赖 `pyautogui` 和 `Pillow`（默认安装）

**能力**：截图、点击、输入文字、按键、组合键、滚动、拖拽、UI 元素查询

### 9.5 视觉分析

通过截图 + 视觉模型分析屏幕内容，配合桌面控制实现"看到就能操作"。

**前提条件**：在 `config.yaml` 中配置视觉模型：

```yaml
vision:
  model: "glm-4.6v"
```

未配置时，视觉分析工具调用会提示用户配置。

### 9.6 Config 热重载

daemon 运行中修改 `~/.lamix/config.yaml`（比如改了飞书配置），无需重启。daemon 每 30 秒检测配置文件变化，自动热重载飞书 adapter。

## 十、部署

```bash
# 安装
cd ~/lamix && pip install -e .

# CLI 交互
lamix cli

# 单条查询
lamix cli "你好"

# 后台 daemon
lamix gateway

# macOS：Daemon 由 launchd 管理
launchctl kickstart -k gui/$(id -u)/com.lamix.gateway

# 重启（不要用 unload && load）
launchctl kickstart -k gui/$(id -u)/com.lamix.gateway && sleep 1 && launchctl load ~/Library/LaunchAgents/com.lamix.gateway.plist

# 自更新
lamix update

# 重新配置模型
lamix model
```

### Windows 移植

Phase 1-4 已完成（ProcessManager 抽象、Desktop 工具拆分、Shell 编码修复、安装脚本）。核心设计决策：

- **进程管理**：`ProcessManager` 抽象基类，`PosixProcessManager`（launchctl + SIGTERM）/ `WindowsProcessManager`（stop.flag 优雅终止）
- **UI 查询**：macOS 用 AppleScript，Windows 用 PowerShell UI Automation
- **daemon 化**：Windows 用 `pythonw.exe` + `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`
- **自更新文件锁定**：下载到临时目录 + flag 文件，下次启动替换
- **安装**：`scripts/install_windows.py`（schtasks 注册开机自启）+ `scripts/build_exe.py`（PyInstaller 打包）

Phase 0/5（环境搭建 + 端到端测试）需要实际 Windows 机器验证。

---

## 十一、Compaction V2 机制

### 触发条件

Token 估算 >= context_window * trigger_threshold（默认 90%），且 stop_reason 为 end_turn/aborted/stop/stop_sequence。

### 核心算法

以 user query 为锚点将消息切分为若干轮（turn），计算最后 20% 轮的数据量占比：

- **策略 A（ratio > 50%）**：前 80% 轮原封不动，最后 20% 轮逐轮摘要
  - 每轮判断 user_query 和 assistant 回复谁更长，摘要长的那个
  - tool_calls / tool_results 原封保留
- **策略 B（ratio <= 50%）**：前 80% 轮合并成一条 summary，后 20% 轮原封不动

### 摘要输入

只取 user query + assistant 文字回复，不含 tool_calls / tool_results（避免膨胀源被重复喂入）。

### 紧急截断

压缩后仍超阈值时，强制只保留最近 2 轮，防止 API 400 错误。

### 任务上下文

压缩后自动注入任务进度摘要，防止多次压缩后任务上下文丢失。

### 配置参数

```yaml
compaction:
  context_window: 131072      # 模型 context window
  trigger_threshold: 0.9      # 触发阈值（90%）
  tail_ratio: 0.2            # tail 占比（20%）
  tail_threshold: 0.5        # 策略A/B 分界线（50%）
```

## 十二、已知问题

- 飞书 WebSocket 断线后不会自动重连
- `_cosine_sim` 使用 `zip(strict=True)` 需要 Python 3.10+
- SkillIndex 增量构建时 config.yaml 路径可能过时（已有自动修正逻辑）
- Planning replan 场景 prompt 待优化

