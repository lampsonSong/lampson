# Lamix 内部实现

## 目录结构（重要！）

| 变量 | 值 | 用途 |
|------|-----|------|
| `LAMIX_DIR` | `~/.lamix/` | 用户数据目录 |
| `MEMORY_DIR` | `~/.lamix/memory/` | 记忆存储 |
| `PROJECTS_DIR` | `~/.lamix/memory/projects/` | 项目上下文 |
| `SKILLS_DIR` | `~/.lamix/memory/skills/` | 技能目录 |
| `INFO_DIR` | `~/.lamix/memory/info/` | 通用信息 |

⚠️ **注意**：`~/lamix/` 是代码仓库，`~/.lamix/` 是用户数据目录，完全不同！

## Claude CLI

- **路径**: `~/.nvm/versions/node/v22.22.2/bin/claude`
- **用途**: 写代码任务交给 claude code 执行

## System Prompt 注入点

System prompt 是分层构建的，定义在 `src/core/prompt_builder.py`：

| 层级 | 内容 | 来源 |
|------|------|------|
| L1 | Identity | MEMORY.md |
| L1.5 | User | USER.md |
| L2 | Tool Guidance | 硬编码常量 + Skills Index + Info Index |
| L3 | Project Index | projects/*.md |
| L4 | Model Guidance | 硬编码常量 |
| L5 | Channel Context | 代码注入 |

### 硬编码常量位置

- **MEMORY_GUIDANCE**（第38行）："你拥有跨会话的持久记忆…"这段是硬编码在 `prompt_builder.py` 里，不是从文件读取
- **SKILLS_GUIDANCE**（第59行）：技能维护指引
- **TOOL_USE_ENFORCEMENT**（第68行）："执行具体任务时必须立即使用工具行动"

### Skills Index

扫描 `~/.lamix/memory/skills/*/SKILL.md`，提取 frontmatter 的 name 和 description，动态生成索引。

### Projects Index

扫描 `~/.lamix/memory/projects/*.md`，提取第一行标题作为项目名。

### Info Index

扫描 `~/.lamix/memory/info/*.md`，提取 frontmatter 的 description。

## Session 日志位置

- 路径：`~/.lamix/memory/sessions/YYYY-MM-DD/feishu/`
- 文件格式：JSONL
- System prompt 记录：`{"type": "system_prompt", "content": "..."}`

## 教训记录

### 2025-06-17: 路径混淆
- **错误**：写文件到 `~/lamix/memory/projects/`
- **正确**：`~/.lamix/memory/projects/`
- **原因**：不知道这是两个不同目录
- **行动**：写文件前先用 `grep "XXX_DIR" src/core/config.py` 验证路径
