# Lamix

自更新的 AI Agent daemon。帮你把事情做完、做好。

## 为什么做 Lamix

LLM 的核心能力是理解语言和推理，但实际把一件事做成，还需要大量 LLM 自身不具备的能力：

- **知识储备**：项目背景、技术栈、历史决策、环境细节
- **工具选择**：同一个问题有多种解法，知道什么时候用哪个工具
- **信息组织**：把散乱的信息结构化，下次遇到类似问题能快速调用

我们认为 AI 就像一个会思考但没记忆、也不会用工具的人——**它天生聪明，但对这个世界一无所知**。Lamix 做的事，就是和你一起帮它积累经验：把做过的事沉淀成技巧（Skills），把学到的知识归档成记忆（Info），把项目的上下文整理成档案（Projects），把合适的工具交到它手里。不是你在用一个工具，是你在和一个伙伴一起成长——它学会了做事，你学会了和 AI 协作。

Lamix 把这些能力拆成三层：

| 层 | 说明 | 例子 |
|------|------|------|
| **Skills** | 把反复出现的任务标准化成可复用的工作流，包含决策点的选择依据 | debug 一个复杂 bug 的分步排查流程、用 Claude Code 派发编码任务的规范 |
| **Info** | 通用、零散但长久的知识信息 | 机器 IP 映射、TTS 服务调用方式、环境配置 |
| **Projects** | 专注于某个项目的所有上下文 | 项目路径、技术栈、部署方式、约定规范 |

收到用户请求时，Lamix 的执行循环是：**理解意图 → 组合历史信息（skills + info + projects）→ 选最优解执行 → 反思结果 → 沉淀或更新知识**。

其中反思不只是做对了才沉淀。被用户纠正的错误、过时的方案、不再适用的技能，同样是学习——更新比新增更重要。

它不是一个聊天机器人，是一个会学习的执行者。每次完成任务后自主判断有没有值得记住的东西——新发现的工作流沉淀为 skill，项目相关的信息更新到 project，通用知识归档为 info。过时的知识主动淘汰，不让记忆变成负担。用得越久越懂你。

## 功能

- 多平台消息网关（飞书 WebSocket、CLI）
- 持久记忆与自学习（skills、projects、info）
- 定时任务调度
- Watchdog 进程守护 + 自动重启
- 桌面控制（截图、鼠标键盘、UI 元素查询）
- 飞书消息交互（文本、卡片、音频）

## 环境要求

- Python >= 3.11
- Git
- [ripgrep](https://github.com/BurntSushi/ripgrep)（搜索功能依赖）

## 安装

### macOS / Linux

```bash
# 1. 安装 ripgrep
brew install ripgrep        # macOS
# sudo apt install ripgrep  # Ubuntu/Debian

# 2. 克隆并安装
git clone https://github.com/lampsonSong/lamix.git
cd lamix
pip install -e .
# 如需桌面控制功能（截图、鼠标键盘），改为 pip install -e ".[all]"
```

### Windows

**第一步：安装 Python**

1. 访问 https://www.python.org/downloads/ 下载 3.11+
2. 安装时**必须勾选**底部的 `Add python.exe to PATH`

**第二步：安装 Git**

1. 访问 https://git-scm.com/download/win 下载安装，默认选项即可
2. 安装时确保勾选 "Add to PATH"（默认已勾选）。装完后打开 CMD 输入 `git --version` 验证

**第三步：安装 ripgrep**

1. 从 https://github.com/BurntSushi/ripgrep/releases 下载 `ripgrep-x.x.x-x86_64-pc-windows-msvc.zip`
2. 解压，将 `rg.exe` 放到固定目录（如 `C:\Tools\`）
3. 将该目录加入系统 PATH：此电脑 → 属性 → 高级系统设置 → 环境变量 → 用户变量 `Path` → 新建

**第四步：安装 Lamix**

打开 CMD 或 PowerShell：

```cmd
git clone https://github.com/lampsonSong/lamix.git
cd lamix
pip install -e .
rem 如需桌面控制功能（截图、鼠标键盘），改为 pip install -e ".[all]"
```

安装完成后，如果提示 ，说明 Python 的 Scripts 目录不在 PATH 中：



将输出的路径加入系统 PATH（此电脑 → 属性 → 高级系统设置 → 环境变量 → 用户变量  → 新建），然后重新打开 CMD 窗口即可。

**（可选）注册开机自启**

以管理员身份运行 PowerShell，执行安装脚本：

```powershell
python scripts/install_windows.py
```

## 使用

### CLI 模式

```bash
# 交互式聊天
lamix-cli

# 单条查询
lamix-cli "帮我查一下今天天气"

# 查看配置
lamix-cli --config
```

首次运行会进入配置向导，引导填写 LLM API Key 和飞书凭证。

### Daemon 模式（后台常驻）

> 首次使用请先运行 `lamix-cli` 完成初始配置（LLM 供应商、API Key 等），再启动 daemon。

```bash
python -m src.daemon
```

Daemon 模式启动后通过飞书 WebSocket 接收消息，配合 watchdog 实现进程守护。

### macOS 开机自启

```bash
# 注册 launchd 服务（watchdog + daemon）
./scripts/install_macos.sh
```

### Windows 开机自启

```powershell
# 需要管理员权限
python scripts/install_windows.py

# 卸载
python scripts/install_windows.py --uninstall
```

## 项目结构

```
lamix/
├── src/
│   ├── cli.py                 # CLI 入口
│   ├── daemon.py              # Daemon 主进程
│   ├── watchdog.py            # 进程守护
│   ├── core/
│   │   ├── agent.py           # LLM Agent 核心
│   │   ├── session.py         # 会话管理
│   │   ├── config.py          # 配置管理
│   │   ├── heartbeat.py       # 心跳机制
│   │   ├── task_scheduler.py  # 定时任务
│   │   └── tools.py           # 工具注册
│   ├── platforms/
│   │   ├── manager.py         # 多平台消息网关
│   │   ├── adapters/
│   │   │   ├── feishu.py      # 飞书 adapter
│   │   │   └── cli.py         # CLI adapter
│   │   ├── process_manager.py          # 进程管理抽象基类
│   │   ├── posix_process_manager.py    # macOS/Linux 实现
│   │   └── windows/
│   │       └── process_manager.py      # Windows 实现
│   ├── tools/
│   │   ├── desktop.py         # 桌面控制
│   │   ├── shell.py           # Shell 命令执行
│   │   └── search.py          # 文件/内容搜索
│   └── feishu/                # 飞书 API 封装
├── scripts/
│   ├── install_windows.py     # Windows 安装脚本
│   └── safe_mode.py           # 安全模式修复
├── docs/
│   └── windows-port-design.md # Windows 移植设计文档
└── pyproject.toml
```

## 配置

配置文件位于 `~/.lamix/config.yaml`，首次运行自动生成。主要配置项：

| 配置项 | 说明 |
|--------|------|
| `llm.api_key` | LLM API Key |
| `llm.model` | 模型名称 |
| `feishu.app_id` | 飞书应用 App ID |
| `feishu.app_secret` | 飞书应用 App Secret |
| `feishu.owner_chat_id` | 飞书 owner 群 ID |

## 许可证

Private
