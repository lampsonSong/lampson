# Lampson - 自更新CLI智能助手 需求文档 v1

## 一、项目概述

**项目名**: Lampson  
**定位**: 一个可以自己更新自己代码的CLI智能助手  
**语言**: Python 3.11+  
**目标**: MVP版本可执行命令、写代码、debug、飞书通信

---

## 二、整体CLI流程

### 2.1 启动流程

用户在终端输入: `lampson`

1. 加载配置文件 `~/.lampson/config.yaml`
2. 初始化 Memory 系统（加载长期记忆 + 会话上下文）
3. 初始化 Skills 注册表
4. 初始化 MCP 客户端连接
5. 进入 REPL 交互循环

### 2.2 REPL 交互流程

```
用户输入
  → 意图识别（LLM判断需要用什么工具）
  → 工具调用 / 直接对话回复
  → 执行结果返回给用户
  → 更新 Memory（会话上下文 + 必要的长期记忆）
  → 等待下一轮输入
```

### 2.3 命令体系

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/memory` | 查看/管理记忆 |
| `/skills` | 查看/管理技能 |
| `/mcp` | 查看/管理 MCP 连接 |
| `/update` | 触发自更新 |
| `/config` | 查看配置 |
| `/feishu` | 飞书相关操作 |
| `/exit` | 退出 |

---

## 三、Memory 管理系统

### 3.1 两层记忆架构

```
~/.lampson/memory/
├── core.md          # 核心记忆：用户偏好、安全规则、重要事实（启动全量加载）
└── sessions/        # 会话记忆：每次对话的摘要（退出时写入）
    └── 2026-04-23.md
```

> 说明：当前会话上下文直接由 LLM 的 messages 列表管理，不单独抽象 working.json。

### 3.2 记忆类型

| 类型 | 存储 | 加载策略 | 大小限制 |
|------|------|----------|----------|
| 核心记忆(core) | core.md | 启动全量加载 | < 5KB |
| 会话记忆(session) | sessions/*.md | 退出时写入，按关键词检索 | 每文件 < 10KB |

### 3.3 记忆操作

- **add**: 添加新记忆条目
- **search**: 搜索历史记忆（关键词匹配）
- **update**: 更新已有记忆
- **forget**: 删除记忆（需确认）
- **compact**: 压缩/整理记忆（文件过大时）

### 3.4 自动记忆策略

- 每轮对话结束，判断是否有值得记住的信息
- 用户说"记住这个" -> 强制写入核心记忆
- 会话结束时，自动生成本次会话摘要写入 sessions/
- 核心记忆超过5KB时提示用户整理

---

## 四、Skills 系统

### 4.1 目录结构

```
~/.lampson/skills/
└── <skill-name>/
    ├── SKILL.md        # 技能描述（触发条件、步骤、注意事项）
    ├── references/     # 参考文档
    ├── templates/      # 模板文件
    └── scripts/        # 脚本文件
```

### 4.2 技能生命周期

1. **发现**: 启动时扫描 skills/ 目录，解析所有 SKILL.md
2. **匹配**: 用户输入到达时，根据描述和触发条件匹配相关技能
3. **加载**: 匹配成功后加载完整技能内容到 prompt
4. **执行**: 按技能步骤执行
5. **更新**: 执行后可根据反馈更新技能（需确认）

### 4.3 技能操作

- `/skills list` - 列出所有技能
- `/skills show <name>` - 查看技能详情
- `/skills create <name>` - 创建新技能
- `/skills edit <name>` - 编辑技能
- `/skills delete <name>` - 删除技能（需确认）

### 4.4 内置技能（MVP）

- `code-writing`: 写代码（文件创建/编辑）
- `code-debug`: 调试代码
- `command-exec`: 执行 shell 命令
- `feishu-msg`: 发送飞书消息
- `self-update`: 自更新流程

---

## 五、MCP（Model Context Protocol）系统

### 5.1 架构

```
Lampson CLI
    ↓ (stdio)
MCP Client Manager
    ↓
┌──────────┐  ┌──────────┐  ┌──────────┐
│MCP Server│  │MCP Server│  │MCP Server│
│(文件系统) │  │(飞书)     │  │(自定义)   │
└──────────┘  └──────────┘  └──────────┘
```

### 5.2 配置

```yaml
# ~/.lampson/config.yaml
mcp:
  servers:
    - name: filesystem
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
      enabled: true
    - name: feishu
      command: "python"
      args: ["-m", "lampson_mcp_feishu"]
      env:
        FEISHU_APP_ID: "xxx"
        FEISHU_APP_SECRET: "xxx"
      enabled: true
```

### 5.3 MCP 客户端管理

- 启动时按配置连接所有 MCP Server
- 发现并注册所有 tools/resources/prompts
- LLM 可调用任意已连接的 MCP 工具
- 支持运行时热加载/卸载 MCP Server
- 连接异常自动重试

### 5.4 MVP 范围

MVP 阶段先用**内置工具**而非完整 MCP。飞书通信做成内置模块。MCP 作为扩展机制预留接口，后续迭代接入。

---

## 六、自更新流程（核心特性）

### 6.1 更新触发方式

1. **用户主动触发**: `/update` 命令
2. **LLM 建议触发**: LLM 发现能力不足时建议更新
3. **定时检查**: 每次启动检查是否有新版本

### 6.2 自更新流程

```
触发更新
  1. 分析需求：用户描述要改什么 / LLM建议
  2. LLM 生成修改方案（代码diff描述）
  3. 用户确认方案（展示将要修改的文件列表）
  4. 创建 git branch: self-update/<desc>
  5. 执行代码修改
  6. 运行测试（如有测试）
  7. 用户确认效果
  8. 合并到 main / 回滚
```

### 6.3 安全机制

- **沙箱执行**: 修改前创建 git 分支
- **确认机制**: 所有代码修改需用户确认后执行
- **回滚能力**: 任何更新都可以 `git revert` 回滚
- **版本锁定**: 核心启动代码(cli.py, agent.py)不可自修改（防砖）
- **备份检查**: 更新前检查 git 状态，确保可回退

### 6.4 MVP 范围

- 用户描述需求 -> LLM 生成代码修改 -> 用户确认 -> 执行
- 基于 git 的分支+回滚机制
- 基本的测试验证

---

## 七、工具系统

### 7.1 内置工具（MVP）

| 工具 | 功能 |
|------|------|
| `shell` | 执行 shell 命令 |
| `file_read` | 读文件 |
| `file_write` | 写文件 |
| `file_edit` | 编辑文件（patch） |
| `feishu_send` | 发送飞书消息 |
| `feishu_read` | 读取飞书消息 |
| `web_search` | 搜索网页 |
| `code_search` | 搜索代码 |

### 7.2 工具调用流程

```
用户输入 → LLM 判断需要调用工具 → 生成工具调用JSON
  → 工具执行器验证参数 → 执行工具 → 结果返回LLM
  → LLM 组织回复 → 输出给用户
```

---

## 八、飞书通信集成

### 8.1 功能

- 发送消息到指定飞书用户/群
- 接收飞书消息（轮询/webhook）
- 在 CLI 中直接和飞书对话

### 8.2 MVP 范围

- 发送文本消息到飞书
- 读取最近的飞书消息
- 使用现有飞书 App

---

## 九、技术架构

### 9.1 技术栈

- **语言**: Python 3.11+
- **CLI框架**: Prompt Toolkit (REPL)
- **LLM调用**: OpenAI SDK (兼容各provider)
- **配置**: YAML (PyYAML)
- **记忆**: Markdown/JSON 文件系统
- **版本控制**: Git (自更新)
- **飞书**: 飞书开放平台 SDK

### 9.2 项目结构

```
lampson/
├── docs/
│   └── PRD.md
├── src/
│   ├── __init__.py
│   ├── cli.py              # CLI 入口 + REPL
│   ├── core/
│   │   ├── __init__.py
│   │   ├── agent.py        # Agent 主循环
│   │   ├── llm.py          # LLM 调用封装
│   │   ├── config.py       # 配置管理
│   │   └── tools.py        # 工具注册与调度
│   ├── memory/
│   │   ├── __init__.py
│   │   └── manager.py      # 记忆管理
│   ├── skills/
│   │   ├── __init__.py
│   │   └── manager.py      # 技能管理
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── client.py       # MCP 客户端
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── shell.py        # Shell 命令执行
│   │   ├── fileops.py      # 文件操作
│   │   └── web.py          # 网络搜索
│   ├── selfupdate/
│   │   ├── __init__.py
│   │   └── updater.py      # 自更新逻辑
│   └── feishu/
│       ├── __init__.py
│       └── client.py       # 飞书客户端
├── tests/
├── config/
│   └── default.yaml
├── pyproject.toml
├── README.md
└── .gitignore
```

---

## 十、MVP 版本范围（v0.1.0）

### 必须完成

1. **CLI 基础**: REPL 交互，支持自然语言输入，首次运行自动引导配置
2. **LLM 对话**: 对接智谱 GLM-5.1（OpenAI 兼容），支持 function calling / tool use，支持多轮对话
3. **工具调用**（4个）: shell 命令执行、读文件、写文件、浏览器搜索
4. **Memory**: 两层记忆 -- core.md（核心记忆，全量加载）+ sessions/（会话摘要，退出时写入）
5. **Skills**: 技能发现、加载、匹配，支持内置技能和用户自定义技能
6. **飞书通信**: 发送消息 + 接收消息（轮询方式）
7. **自更新**: `/update` 手动触发，LLM 生成代码修改，用户确认后执行，git 分支 + 回滚

### 暂不做

- MCP Server 对接（预留接口）
- TUI 界面（用简单 REPL）
- 多用户支持
- 插件市场
- 语义搜索记忆
- 自更新的 LLM 建议触发 / 定时检查
- file_edit（patch 模式）
- code_search（代码搜索）

### 验收标准

- [ ] `lampson` 命令启动 REPL，首次运行引导配置 API Key
- [ ] 可以自然语言对话（多轮）
- [ ] 可以执行 shell 命令：`帮我看看当前目录有什么文件`
- [ ] 可以读写文件：`在 ~/test/ 下创建一个 hello.py`
- [ ] 可以浏览器搜索：`搜一下 Python asyncio 怎么用`
- [ ] 可以发送飞书消息：`给 xxx 发一条消息说 "测试"`
- [ ] 可以接收飞书消息：查看最近收到的消息
- [ ] 可以自更新：`/update 给自己加一个 /time 命令`
- [ ] Skills 可以发现和加载
- [ ] 记忆可以保存和检索
- [ ] git 回滚可以恢复

---

## 十一、开发计划

| 阶段 | 内容 | 负责人 | 预计时间 |
|------|------|--------|----------|
| 1 | 确认需求文档 | 三儿+哥哥 | 1天 |
| 2 | CLI框架 + LLM对话 | Cursor | 2天 |
| 3 | 工具系统(shell/文件) | Cursor | 2天 |
| 4 | Memory 系统 | Cursor | 1天 |
| 5 | Skills 系统 | Cursor | 1天 |
| 6 | 飞书通信 | Cursor | 2天 |
| 7 | 自更新流程 | Cursor | 2天 |
| 8 | 集成测试 + 验收 | Claude Code + 三儿 | 2天 |

每个阶段: Cursor写 -> Claude Code review -> 三儿验收 -> 下一阶段

---

## 十二、已确认的技术决策

1. **LLM Provider**: 智谱 GLM-5.1，走 OpenAI 兼容接口（api.zhipuai.cn）
2. **CLI框架**: Prompt Toolkit（轻量 REPL）
3. **飞书方式**: 直接调飞书 API，不搞 MCP Server
4. **包管理**: `pip install -e .`，开发模式安装
5. **自更新保护**: 以下文件标记为 protected，自更新不可修改：
   - `src/cli.py`（入口）
   - `src/core/agent.py`（主循环）
   - `src/core/llm.py`（LLM调用，保证基本对话能力）
   - `src/feishu/client.py`（飞书通信，保证收发消息）
   - `src/tools/shell.py`（命令执行，保证执行命令能力）
   - 自更新时如果涉及上述文件，必须额外确认
