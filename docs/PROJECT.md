# Lamix 项目文档

> 自主运行的 AI Agent daemon，帮用户把事情做完、做好。

---

## 一、项目概述

**技术栈**：Python 3.11+，智谱 GLM（OpenAI 兼容接口），飞书开放平台 REST API

**核心能力**：
- 多工具调用循环（30 轮自动继续）
- 任务规划与执行（Plan-and-Execute）
- 上下文压缩（归档 + 摘要）
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
| Compaction | `src/core/compaction.py` | 三阶段压缩：Classify → Archive → Summarize |
| Reflection | `src/core/reflection.py` | 反思沉淀（skill/project create/update） |
| Indexer | `src/core/indexer.py` | SkillIndex / ProjectIndex，增量构建 |
| Interrupt | `src/core/interrupt.py` | AgentInterrupted 异常 + 中断标志位 |
| Adapters | `src/core/adapters/` | 多模型适配（OpenAI 兼容 / MiniMax） |
| Heartbeat | `src/core/heartbeat.py` | 心跳管理器 |
| Watchdog | `src/watchdog.py` | 独立看门狗进程，监控 daemon |
| Self-update | `src/selfupdate/updater.py` | git 分支自更新 |

---

## 三、工具列表

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

---

## 四、PromptBuilder 分层

| 层 | 内容 |
|----|------|
| L1 | Identity（MEMORY.md）+ User（USER.md） |
| L2 | Tool Guidance + Skills 索引（名称+描述，无触发词） |
| L3 | Project Index（动态扫描 projects/*.md） |
| L4 | Model Guidance（模型适配提示） |
| L5 | Channel Context（非 CLI 时注入消息来源） |

Skills 索引只展示名称和描述，触发由 LLM 自主判断。详情通过 `skill(action='view')` 按需加载。

---

## 五、数据目录

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
└── logs/                    # daemon 日志
```

---

## 六、配置

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

## 七、命令

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

## 八、特色功能

### 8.1 Boot Tasks（重启验证）

改了 daemon 代码后需要重启才能生效。Boot Task 机制让你在重启前写好验证任务，daemon 重启后自动执行并汇报结果。

**使用场景**：改了 compaction 逻辑，重启后自动验证压缩是否正常。

```bash
# 写入 boot task（重启前执行）
file_write("~/.lamix/boot_tasks.json", '[{"task": "验证改动：发几条消息测试交互，检查日志无异常"}]')

# 重启 daemon
/restart
```

daemon 启动后读取 boot_tasks.json，把任务注入 session 执行，完成后清空文件。

### 8.2 定时任务

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

### 8.3 每日自我审计

daemon 每天凌晨 4 点自动运行自我审计，扫描 skills/projects/learned_modules 的健康状态：

- **自动修复**：空目录删除、散落 .md 合并到 SKILL.md、缺失 frontmatter 自动生成
- **重叠检测**：检查 skill 之间的职责重叠，建议合并
- **问题报告**：通过飞书发送审计结果

可在 `config.yaml` 中关闭：

```yaml
self_audit:
  enabled: false
```

### 8.4 桌面控制（键鼠 + 截图）

Lamix 可以操作鼠标键盘和截屏，实现 GUI 自动化。

**前提条件**：
- macOS：系统设置 → 隐私与安全 → 辅助功能 → 授予终端/Python 权限
- Windows：以管理员身份运行，或授予对应权限
- 依赖 `pyautogui` 和 `Pillow`（默认安装）

**能力**：截图、点击、输入文字、按键、组合键、滚动、拖拽、UI 元素查询

### 8.5 视觉分析

通过截图 + 视觉模型分析屏幕内容，配合桌面控制实现"看到就能操作"。

**前提条件**：在 `config.yaml` 中配置视觉模型：

```yaml
vision:
  model: "glm-4.6v"
```

未配置时，视觉分析工具调用会提示用户配置。

### 8.6 Config 热重载

daemon 运行中修改 `~/.lamix/config.yaml`（比如改了飞书配置），无需重启。daemon 每 30 秒检测配置文件变化，自动热重载飞书 adapter。

## 九、部署

```bash
# 安装
cd ~/lamix && pip install -e .

# CLI 交互
lamix

# Daemon 由 launchd 管理
launchctl kickstart -k gui/$(id -u)/com.lamix.gateway

# 重启（不要用 unload && load）
launchctl kickstart -k gui/$(id -u)/com.lamix.gateway && sleep 1 && launchctl load ~/Library/LaunchAgents/com.lamix.gateway.plist
```

---

## 十、已知问题

- 飞书 WebSocket 断线后不会自动重连
- `_cosine_sim` 使用 `zip(strict=True)` 需要 Python 3.10+
- SkillIndex 增量构建时 config.yaml 路径可能过时（已有自动修正逻辑）
- Compaction 压缩质量依赖 LLM 判断，可能误归档
- Planning replan 场景 prompt 待优化
