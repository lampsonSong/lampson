"""Task Planning 的 Prompt 模板。"""

# ── 共用常量块 ──

PERSISTENT_ENV_BLOCK = """## 环境信息
- 运行机器：由运行环境决定
- lamix 项目路径：由运行环境决定
- 本机只能执行本地命令，操作远程需 SSH
- 文件读取 100KB 限制，shell 默认超时 30 秒

## 行为准则
- 危险操作（rm -rf、chmod 777 等）执行前必须让用户确认
- 远程操作（train40 等）必须通过 SSH 命令
- 文件读取有 100KB 大小限制，超出请用 offset/limit 分批
- shell 命令默认超时 30 秒，复杂命令可设置 timeout 参数（最长 120 秒）

## 文件搜索规范
- **禁止使用 find 命令**，改用 `search(mode="files")` 工具（按文件名搜索）
- **禁止使用 grep/rg 命令**，改用 `search(mode="content")` 工具（按内容搜索）
- 查看目录内容用 `shell` 工具执行 `ls` 命令，例如 `shell(command="ls /path/to/dir")`
- 读取文件内容用 `file_read` 工具，不要用 cat 命令
- **注意**：工具名必须是 `shell`、`file_read`、`file_write`、`search`、`skill`、`search_projects`、`project_context`、`session` 之一
"""

MEMORY_STRUCTURE_BLOCK = """## lamix 记忆结构

lamix 的所有持久化数据存储在 ~/.lamix/ 目录下：

```
~/.lamix/
├── memory/
│   ├── core.md              # 核心记忆（关于用户的基本偏好和重要事实）
│   ├── sessions/            # 历史会话摘要（每个文件是一次对话的总结）
│   ├── projects/            # 项目记录（用户主动记录的项目信息和文档）
│   │   └── <项目名>.md      # 每个项目一个文件
│   ├── skills/              # 技能文件（lamix 掌握的操作技能）
│   │   └── <技能名>/SKILL.md # 每个技能一个目录
│   └── info/                # 知识性信息
│       └── <名>.md          # 每条一个文件
└── config.yaml              # 配置文件
```

相关工具：
- **skill(action="view", name)**: 按名称加载指定技能的完整内容（名称已知时使用）
- **skill(action="search", query)**: 在技能名称与描述中做关键词子串匹配（辅助查找）
- **search_projects(query)**: 根据自然语言描述搜索匹配的项目上下文
- **project_context(name)**: 按名称加载指定项目的完整记录（名称已知时使用）
- **file_read**: 读取任意文件
- **file_write**: 写入文件（用于记录到 projects 等）
"""


# ── 对话上下文 ──

def build_context_from_history(messages: list[dict]) -> str:
    """从对话历史构建上下文摘要，供规划 prompt 使用。

    不做截断 -- 上下文过长由 compaction 机制统一处理。
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        if not content:
            continue
        prefix = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(role, role)
        lines.append(f"{prefix}: {content}")

    return "\n".join(lines)


# ── 内部工具 ──

def _format_tool_schemas(schemas: list[dict]) -> str:
    """把工具 schema 列表格式化为可读文本。"""
    parts = []
    for schema in schemas:
        func = schema.get("function", schema)
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        params = func.get("parameters", {}).get("properties", {})
        required = func.get("parameters", {}).get("required", [])

        param_strs = []
        for pname, pinfo in params.items():
            ptype = pinfo.get("type", "any")
            pdesc = pinfo.get("description", "")
            req = "必填" if pname in required else "可选"
            param_strs.append(f"    - {pname} ({ptype}, {req}): {pdesc}")

        param_text = "\n".join(param_strs) if param_strs else "    （无参数）"
        parts.append(f"- {name}: {desc}\n{param_text}")

    return "\n\n".join(parts)
