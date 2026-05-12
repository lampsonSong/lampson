# Lamix

自更新的 AI Agent daemon，和你一起探索这个世界的AI伙计。

## 为什么做 Lamix

LLM 的核心能力是理解语言，但实际把一件事做成，还需要大量 LLM 自身不具备的能力：

- **知识储备**：项目背景、技术栈、历史决策、环境细节
- **工具选择**：同一个问题有多种解法，知道什么时候用哪个工具
- **信息组织**：把散乱的信息结构化，下次遇到类似问题能快速调用

我们认为 AI 就像一个会思考但没记忆、也不会用工具的人——**它天生聪明，知识渊博，但对你的世界一无所知**。决定一个人是否是你的朋友的因素，是你们共同的记忆、经历、行为准则。Lamix 要做的事，就是和你一起帮它积累这些：把做过的事沉淀成技巧（Skills），把学到的知识归档成记忆（Info），把项目的上下文整理成档案（Projects），把合适的工具交到它手里。不是你在用一个工具，是你在和一个伙伴一起成长——它学会了做事，你学会了和 AI 协作。

Lamix 把这些能力拆成三层：

| 层 | 说明 | 例子 |
|------|------|------|
| **Skills** | 把反复出现的任务标准化成可复用工作流，包含工作步骤、决策依据，以及配套的脚本/工具代码 | debug 流程、Claude Code 派发规范；配套脚本放在 `scripts/` 下，下次启动自动注册为工具 |
| **Info** | 通用、零散但长久的知识信息 | 机器 IP 映射、TTS 服务调用方式、环境配置 |
| **Projects** | 专注于某个项目的所有上下文 | 项目路径、技术栈、部署方式、约定规范 |

收到用户请求时，Lamix 的执行循环是：**理解意图 → 组合历史信息（skills + info + projects）→ 选最优解执行 → 反思结果 → 沉淀或更新知识**。

其中反思不只是做对了才沉淀。被用户纠正的错误、过时的方案、不再适用的技能，同样是学习——更新比新增更重要。

它不是一个聊天机器人，是一个会学习的执行者。每次完成任务后自主判断有没有值得记住的东西——新发现的工作流沉淀为 skill，项目相关的信息更新到 project，通用知识归档为 info。过时的知识主动淘汰，不让记忆变成负担。用得越久越懂你。

<details>
<summary><strong>架构</strong></summary>

### 核心流程

```mermaid
graph LR
    U[飞书 / CLI] -->|消息| GW[消息网关]
    GW --> PB[Prompt Builder]
    PB -->|L1 Identity → L2 Tools → L3 Projects → L4 Model → L5 Channel| LLM[LLM]
    LLM -->|tool_calls| T[工具层]
    T --> LLM
    LLM -->|回复| GW
    GW --> U
```

### 自学习闭环

```mermaid
graph TB
    EXEC[执行任务] --> JUDGE{值得沉淀？}
    JUDGE -->|可复用工作流| SKILL[Skill]
    JUDGE -->|通用知识| INFO[Info]
    JUDGE -->|项目信息| PROJ[Project]
    JUDGE -->|固定模式| CODE[Learned Module]
    JUDGE -->|不需要| END[结束]
    SKILL & INFO & PROJ --> RELOAD[下次 Prompt 自动加载]
    CODE --> REG[下次启动自动注册为工具]
```

Lamix 不是一个用完即走的聊天机器人——它有完整的**自学习闭环**：

- **Skills**：把反复出现的任务标准化成可复用工作流。每个 skill 目录下可以有 `SKILL.md`（工作步骤、决策依据）和 `scripts/`（配套工具脚本）。脚本在下一次 daemon 启动时自动注册为原生工具，下次遇到类似问题自动加载——不用从头教
- **Info / Projects**：通用知识和项目上下文分别归档，按需注入 prompt
- **Skill Scripts**：检测到固定调用模式后，Lamix 会自动生成 Python 工具脚本（存放在 skills/*/scripts/ 下），下次启动直接注册为原生工具——从"用工具"进化到"造工具"
- **Reflection**：每次任务完成后自主反思，判断有没有值得记住的东西。被用户纠正的错误、过时的方案同样会触发更新
- **Self Audit**：每天凌晨自动扫描知识库健康状态，清理过时内容，防止记忆变成负担

### 安全机制

| 层 | 机制 | 说明 |
|------|------|------|
| **Shell 拦截** | 危险命令黑名单 | `rm -rf /`、`mkfs`、`dd of=/dev` 等命令执行前拦截确认 |
| **自更新保护** | 保护文件清单 | `agent.py`、`llm.py`、`cli.py` 等核心文件在自更新时不可覆盖 |
| **自更新回滚** | git 分支管理 | 每次自更新创建独立分支，`/update rollback` 一键回退 |
| **上下文保护** | 紧急截断 | Compaction 后仍超阈值时只保留最近 2 轮，防止 API 400 导致会话崩溃 |
| **配置安全** | 热重载不丢密码 | 改配置用 patch 只改目标行，避免 yaml.dump 把 `***` 写入磁盘覆盖真实密钥 |
| **进程守护** | Watchdog + 心跳 | 独立看门狗进程监控 daemon，崩溃自动重启，通过飞书通知用户 |
| **知识归档** | 软删除 | 过期知识移入 `archived/` 而非直接删除，可恢复 |

| 组件 | 说明 |
|------|------|
| 消息网关 | 飞书 WebSocket + CLI，接收消息、分发回复 |
| Prompt Builder | 5 层分层构建 system prompt |
| Agent 核心 | Tool loop、轮次分段压缩（Compaction V2）、反思沉淀 |
| 知识系统 | Skills + Info + Projects + Skill Scripts |
| 进程守护 | Watchdog + Daemon，自动重启 |

</details>

<details>
<summary><strong>功能</strong></summary>

- 多平台消息网关（飞书 WebSocket、CLI）
- 持久记忆与自学习（skills、projects、info）
- 定时任务调度（间隔/cron/一次性延迟）
- Watchdog 进程守护 + 自动重启
- 桌面控制（截图、鼠标键盘、UI 元素查询，需授权）
- 视觉分析（截图 + 视觉模型，需配置模型）
- 自我审计与知识生命周期（自动扫描修复 + 归档未使用知识，可关闭）
- Boot Tasks（重启后自动验证改动）
- Config 热重载（改配置无需重启 daemon）

</details>

<details>
<summary><strong>环境要求</strong></summary>

- Python >= 3.11
- Git
- [ripgrep](https://github.com/BurntSushi/ripgrep)（可选，搜索性能提升；未安装时自动 fallback 到 Python 实现）

</details>

## 安装

<details>
<summary><strong>macOS / Linux</strong></summary>

**1. （可选）安装 ripgrep**

```bash
brew install ripgrep        # macOS
# sudo apt install ripgrep  # Ubuntu/Debian
```

**2. 克隆并安装**

```bash
git clone https://github.com/lampsonSong/lamix.git
cd lamix
```

安装方式二选一：

- **正式模式（推荐）**：安装后代码锁定，适合日常使用
  ```bash
  pip install .
  ```

- **开发模式（editable）**：代码修改即时生效，适合二次开发
  ```bash
  pip install -e .
  ```

**3. 启动**

```bash
# 方式一：交互式 CLI
lamix cli

# 方式二：后台 daemon（飞书消息接收）
lamix gateway
```

</details>

### Windows

<details>
<summary><strong>一键安装（推荐）</strong></summary>

下载源码后，双击 `scripts/install.bat`，自动安装依赖、构建 exe。

产物在 `dist/` 目录：
- **lamix.exe** — 双击启动
- **lamix-uninstall.exe** — 双击卸载

</details>

<details>
<summary>手动安装</summary>

**1. 安装 Python**

1. 访问 https://www.python.org/downloads/ 下载 3.11+
2. 安装时**必须勾选**底部的 `Add python.exe to PATH`

**2. 安装 Git**

1. 访问 https://git-scm.com/download/win 下载安装，默认选项即可
2. 安装时确保勾选 "Add to PATH"（默认已勾选）。装完后打开 CMD 输入 `git --version` 验证

**3. （可选）安装 ripgrep**

1. 从 https://github.com/BurntSushi/ripgrep/releases 下载 `ripgrep-x.x.x-x86_64-pc-windows-msvc.zip`
2. 解压，将 `rg.exe` 放到固定目录（如 `C:\Tools\`）
3. 将该目录加入系统 PATH：此电脑 → 属性 → 高级系统设置 → 环境变量 → 用户变量 `Path` → 新建

**4. 安装 Lamix**

打开 CMD 或 PowerShell：

```cmd
git clone https://github.com/lampsonSong/lamix.git
cd lamix
pip install -e .
```

安装完成后，如果提示 `lamix` 命令未找到，说明 Python 的 Scripts 目录不在 PATH 中。运行以下命令查看路径：

```cmd
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

将输出的路径加入系统 PATH（此电脑 → 属性 → 高级系统设置 → 环境变量 → 用户变量 `Path` → 新建），然后重新打开 CMD 窗口即可。

**5.（可选）注册开机自启**

以管理员身份运行 PowerShell，执行安装脚本：

```powershell
python scripts/install_windows.py
```

</details>

## 使用

### CLI 模式

```bash
# 交互式聊天
lamix cli

# 单条查询
lamix cli "帮我查一下今天天气"
```

首次运行会进入配置向导，引导填写 LLM API Key 和飞书凭证。

### 常用命令

```bash
lamix model           # 重新配置 LLM 模型（供应商/模型/API Key）
lamix update          # 从 GitHub 拉取最新代码并重启 daemon
lamix config          # 查看当前配置
lamix -V              # 查看版本号
```

### Daemon 模式（后台常驻）

> 首次使用请先运行 `lamix cli` 完成初始配置（LLM 供应商、API Key 等），再启动 daemon。

```bash
lamix gateway
```

Daemon 模式启动后通过飞书 WebSocket 接收消息，配合 watchdog 实现进程守护。修改配置后无需重启，daemon 每 30 秒自动热重载。

<details>
<summary><strong>特色功能</strong></summary>

### 定时任务

支持三种触发方式：

| 类型 | 说明 | 示例 |
|------|------|------|
| interval | 固定间隔 | 每 30 分钟检查服务状态 |
| cron | 定时执行 | 每天早 9 点发工作汇总 |
| delayed | 一次性延迟 | 5 分钟后提醒 |

通过飞书或 CLI 对话中直接说"每天早上 9 点提醒我开会"，Lamix 会自动注册任务。

### 自我审计与知识生命周期

Daemon 每 4 小时检查一次是否需要审计，**每天至少执行一次**。扫描 skills、projects、info 的健康状态：

- **自动修复**：空目录清理、散落文件合并、缺失信息补全
- **重叠检测**：发现职责重叠的 skills 并建议合并
- **知识归档**：7 天未使用且 0 次调用→归档，30 天未使用→归档。归档不删除，移入 `archived/` 子目录
- 报告通过飞书发送，同时持久化到 `~/.lamix/audit_reports/`

可在 `~/.lamix/config.yaml` 中设置 `self_audit.enabled: false` 关闭。

### Boot Tasks

改了 daemon 代码需要重启时，先写 boot task 指定重启后的验证步骤。daemon 重启后自动执行验证并汇报结果，不用手动检查。

### 后台任务

Lamix 支持将耗时任务放到后台执行，不阻塞当前对话：

- **独立 Agent**：后台任务创建独立的 Agent 实例，继承发起时的上下文快照（对话历史、项目信息、Skills）
- **结果推送**：任务完成后自动通过原渠道（飞书/CLI）推送结果
- **任务管理**：支持查看运行中的任务、取消任务

典型场景：长时间运行的代码分析、批量文件处理、需要等待的外部命令。对话中直接说把这个任务放到后台即可。

### 桌面控制

Lamix 可以操作鼠标键盘和截屏。需要授权：

- **macOS**：系统设置 → 隐私与安全 → 辅助功能 → 授予终端权限
- **Windows**：以管理员身份运行

### 视觉分析

通过截图 + 视觉模型分析屏幕内容。需要在配置中启用：

```yaml
vision:
  model: "glm-4.6v"
```

未配置时，视觉分析工具会提示用户配置对应模型。

### 开机自启

```bash
# macOS：注册 launchd 服务（watchdog + daemon）
./scripts/install_macos.sh

# Windows：需要管理员权限
python scripts/install_windows.py

# Windows 卸载
python scripts/install_windows.py --uninstall
```

### 对话命令

在飞书或 CLI 对话中可使用以下命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/config` | 查看当前配置（敏感信息脱敏） |
| `/model` | 显示当前模型和可用模型列表 |
| `/model <name>` | 切换到指定模型 |
| `/model all <question>` | 同时向所有可用模型提问，对比回答 |
| `/memory show` | 查看长期记忆 |
| `/memory add <text>` | 添加记忆条目 |
| `/memory search <keyword>` | 搜索记忆 |
| `/memory forget <keyword>` | 删除含关键词的记忆条目 |
| `/search <keyword>` | 搜索历史对话记录 |
| `/resume` | 列出最近 5 个 session |
| `/resume <id>` | 加载指定 session 到当前对话 |
| `/skills list` | 列出所有技能 |
| `/skills show <name>` | 查看技能详情 |
| `/skills create <name>` | 创建新技能 |
| `/skills consolidate` | 分析并合并重复/耦合的技能 |
| `/background <prompt>` | 后台运行任务，完成后推送结果 |
| `/tasks` | 查看运行中的后台任务 |
| `/cancel <task_id>` | 取消后台任务 |
| `/compaction` | 手动触发上下文压缩 |
| `/contextsize` | 查看当前上下文长度和占比 |
| `/self-audit` | 立即触发自我审计 |
| `/audit-report` | 列出历史审计报告 |
| `/audit-report <path>` | 查看指定审计报告详情 |
| `/update <需求描述>` | 触发自更新 |
| `/update rollback` | 回滚自更新 |
| `/update list` | 列出自更新分支 |
| `/feishu send <id> <msg>` | 发送飞书消息 |
| `/feishu read <chat_id>` | 读取飞书消息 |
| `/metrics` | 查看最近任务指标统计 |
| `/new` | 开始新 session（清空当前对话上下文） |
| `/exit` | 退出 |

</details>

<details>
<summary><strong>项目结构</strong></summary>

```
lamix/
├── src/
│   ├── cli.py                 # 命令分发器（cli/gateway/model/update/config）
│   ├── daemon.py              # Daemon 主进程
│   ├── watchdog.py            # 进程守护
│   ├── core/
│   │   ├── agent.py           # LLM Agent 核心
│   │   ├── config.py          # 配置加载/热重载
│   │   ├── session.py         # 会话管理
│   │   ├── tools/             # 内置工具集
│   │   ├── heartbeat.py       # 心跳机制（watchdog 依赖）
│   │   ├── self_audit.py      # 自我审计
│   │   └── task_scheduler.py  # 定时任务调度器
│   ├── platforms/             # 多平台 adapter
│   ├── feishu/                # 飞书 SDK 封装
│   ├── memory/                # 长期记忆存储
│   ├── skills/                # 技能知识库
│   ├── selfupdate/            # 自更新模块
│   └── cli.py                 # 命令分发器（cli/gateway/model/update/config）
├── scripts/                   # 安装/部署脚本
├── tests/                     # 测试
├── config.yaml                # 用户配置（自动生成）
└── pyproject.toml             # 项目配置
```

</details>

---

<sub>Lamix 使用 MIT 协议，详情见 [LICENSE](LICENSE)。</sub>