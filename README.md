# Lamix

自更新的 AI Agent daemon。帮你把事情做完、做好。

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
```

### Windows

**第一步：安装 Python**

1. 访问 https://www.python.org/downloads/ 下载 3.11+
2. 安装时**必须勾选**底部的 `Add python.exe to PATH`

**第二步：安装 Git**

1. 访问 https://git-scm.com/download/win 下载安装，默认选项即可

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
```

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
