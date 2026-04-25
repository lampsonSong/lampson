# 搜索工具设计文档：search_files + search_content

> 版本：v1.0
> 日期：2026-05-10
> 状态：待实现

---

## 一、背景与动机

### 问题

Lampson 当前只有一个 `shell` 工具执行命令，LLM 需要自己写 `find` / `grep` 命令来搜索文件。这导致：

1. **超时风险高**：LLM 生成的 `find` 命令经常没有 `-maxdepth` 限制，搜索 `.venv` / `node_modules` 等巨大目录时跑满 60s 超时
2. **结果不可控**：`find` / `grep` 可能返回数万条结果，淹没有效信息
3. **易写错命令**：LLM 对 `find` 的复杂语法（`-name`、`-path`、`-prune` 组合）经常写错
4. **与 Hermes/OpenClaw 对齐**：成熟 Agent 都把 find/grep 封装为独立工具，底层用 ripgrep（rg），不让 LLM 直接写 shell 命令

### 方案

新增两个独立工具，底层全用 **ripgrep (rg)**：

| 工具 | 替代 | 底层命令 |
|------|------|----------|
| `search_files` | `find` | `rg --files --glob <pattern>` |
| `search_content` | `grep` | `rg <pattern>` |

**不动**：`file_read`（已有）、`shell`（保留但 prompt 中禁止用 find/grep）。

---

## 二、前置条件

- **ripgrep 已安装**：`/opt/homebrew/bin/rg`，版本 15.1.0
- 如果 rg 不存在，工具返回明确错误提示，不 crash

---

## 三、工具定义

### 3.1 search_files — 按文件名/类型搜索

**替代**：`find <path> -name "<pattern>"`

**Schema**：

```json
{
  "type": "function",
  "function": {
    "name": "search_files",
    "description": "按文件名或 glob 模式搜索文件。底层用 ripgrep，自动尊重 .gitignore，自动排除 .git 目录。用于替代 find 命令。",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {
          "type": "string",
          "description": "文件名 glob 模式，如 '*.py'、'test_*'、'README*'。支持 rg glob 语法。"
        },
        "path": {
          "type": "string",
          "description": "搜索根目录，默认当前目录 '.'",
          "default": "."
        },
        "max_depth": {
          "type": "integer",
          "description": "最大搜索深度，默认 5，最大 10",
          "default": 5
        },
        "max_results": {
          "type": "integer",
          "description": "最多返回多少条结果，默认 50，最大 200",
          "default": 50
        }
      },
      "required": ["pattern"]
    }
  }
}
```

**底层命令**：

```bash
rg --files --glob '<pattern>' --max-depth <max_depth> --max-count <max_results> <path>
```

**关键行为**：
- `rg --files`：只列出文件路径，不搜内容
- `--glob`：按文件名匹配（rg 原生支持 glob）
- `--max-depth`：限制搜索深度（默认 5，上限 10）
- `--max-count`：限制结果数量（rg 15.x 的 `--max-count` 对 `--files` 模式也生效；如果不生效则在 Python 层截断）
- 自动尊重 `.gitignore` 和 `.ignore`
- 自动排除 `.git` 目录（rg 默认行为）

**返回格式**：

```
找到 23 个文件（搜索路径：/Users/songyuhao/lampson/src，深度 ≤ 5）：

/Users/songyuhao/lampson/src/core/agent.py
/Users/songyuhao/lampson/src/core/config.py
...（共 23 个）
```

### 3.2 search_content — 按文件内容搜索

**替代**：`grep -rn "<pattern>" <path>`

**Schema**：

```json
{
  "type": "function",
  "function": {
    "name": "search_content",
    "description": "在文件内容中搜索匹配的文本或正则表达式。底层用 ripgrep，自动尊重 .gitignore。用于替代 grep 命令。",
    "parameters": {
      "type": "object",
      "properties": {
        "pattern": {
          "type": "string",
          "description": "搜索模式，支持正则表达式（rg 语法）"
        },
        "path": {
          "type": "string",
          "description": "搜索根目录，默认当前目录 '.'",
          "default": "."
        },
        "file_glob": {
          "type": "string",
          "description": "只在匹配此 glob 的文件中搜索，如 '*.py'、'*.{js,ts}'",
          "default": null
        },
        "max_results": {
          "type": "integer",
          "description": "最多返回多少条匹配，默认 50，最大 200",
          "default": 50
        },
        "context_lines": {
          "type": "integer",
          "description": "每个匹配前后显示多少行上下文，默认 2，最大 5",
          "default": 2
        }
      },
      "required": ["pattern"]
    }
  }
}
```

**底层命令**：

```bash
rg --line-number --max-count <max_results> --context <context_lines> [--glob '<file_glob>'] '<pattern>' <path>
```

**关键行为**：
- `--line-number`：显示行号
- `--max-count`：限制匹配数量
- `--context`：上下文行数
- `--glob`：文件类型过滤（可选）
- 自动尊重 `.gitignore`
- 如果 pattern 是固定字符串（不含正则元字符），加 `--fixed-strings` 加速
- 搜索结果超过 max_results 时截断并提示"结果过多，请缩小范围"

**返回格式**：

```
在 /Users/songyuhao/lampson/src 中搜索 "def run"（共 15 处匹配）：

src/core/agent.py:
  42 |     def run(self, user_input: str) -> str:
  43 |         self.llm.add_user_message(user_input)

src/tools/shell.py:
  78 | def run(params: dict[str, Any]) -> str:
  79 |     command = params.get("command", "")
...
```

---

## 四、实现规格

### 4.1 新文件

创建 `src/tools/search.py`，包含：
- `run_search_files(params) -> str`
- `run_search_content(params) -> str`
- `SEARCH_FILES_SCHEMA`
- `SEARCH_CONTENT_SCHEMA`
- 内部辅助函数

### 4.2 rg 可用性检查

```python
import shutil

_RG_PATH = shutil.which("rg")

def _check_rg() -> str | None:
    """返回 rg 路径，不可用时返回 None。"""
    return _RG_PATH
```

每个工具入口先检查，不可用时返回：
```
[错误] ripgrep (rg) 未安装，无法使用搜索功能。请运行 brew install ripgrep 安装。
```

### 4.3 参数校验与限制

| 参数 | 默认值 | 最小值 | 最大值 | 说明 |
|------|--------|--------|--------|------|
| `max_depth` | 5 | 1 | 10 | search_files 专用 |
| `max_results` | 50 | 1 | 200 | 两个工具共用 |
| `context_lines` | 2 | 0 | 5 | search_content 专用 |

超出范围时 clamp 到边界，不报错。

### 4.4 执行流程（两个工具相同）

```python
def _run_rg(args: list[str], max_results: int, path: str) -> str:
    """执行 rg 命令并格式化输出。"""
    # 1. 参数校验
    if not _RG_PATH:
        return "[错误] ripgrep (rg) 未安装..."

    # 2. 路径展开（~ → /Users/xxx）
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return f"[错误] 路径不存在或不是目录：{path}"

    # 3. 组装完整命令
    cmd = [_RG_PATH] + args + [path]

    # 4. 执行，超时 30s
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    # 5. 处理结果
    if result.returncode == 1:
        return "未找到匹配结果。"
    if result.returncode != 0:
        return f"[错误] rg 执行失败：{result.stderr.strip()}"

    # 6. 截断到 max_results（Python 层保底）
    lines = result.stdout.splitlines()
    truncated = len(lines) > max_results
    lines = lines[:max_results]

    # 7. 格式化输出
    output = "\n".join(lines)
    if truncated:
        output += f"\n\n...（结果过多，已截断到 {max_results} 条，请缩小搜索范围）"

    return output
```

### 4.5 危险 pattern 防护

对 `search_content` 的 pattern 做基本检查：
- 长度不超过 500 字符
- 不允许 ReDoS 高风险模式（如嵌套量词 `(a+)+`），用简单正则检测

```python
_REDOS_RE = re.compile(r"(\([^)]*[+*][^)]*\))[+*]")

def _validate_pattern(pattern: str) -> str | None:
    """返回错误信息，None 表示合法。"""
    if len(pattern) > 500:
        return "搜索模式过长（上限 500 字符），请简化。"
    if _REDOS_RE.search(pattern):
        return "搜索模式可能触发 ReDoS，请简化正则表达式。"
    return None
```

### 4.6 注册到工具表

在 `src/core/tools.py` 中添加：

```python
from src.tools import search as search_tool

_register(search_tool.SEARCH_FILES_SCHEMA, search_tool.run_search_files)
_register(search_tool.SEARCH_CONTENT_SCHEMA, search_tool.run_search_content)
```

---

## 五、配套改动

### 5.1 shell 工具 prompt 禁用 find/grep

在 `src/planning/prompts.py` 的 `PERSISTENT_ENV_BLOCK` 中更新 Shell 命令规范：

```
## 文件搜索规范
- **禁止使用 find 命令**，改用 `search_files` 工具（按文件名搜索）
- **禁止使用 grep/rg 命令**，改用 `search_content` 工具（按内容搜索）
- 需要查看目录内容用 `ls`，这是允许的（执行快，不会超时）
```

### 5.2 shell.py 清理

删除 `_harden_find_command` 函数及其相关常量（`_FIND_EXCLUDE_DIRS`、`_FIND_DEFAULT_MAXDEPTH`），因为 find 不再允许使用，无需加固。

---

## 六、测试要求

在 `tests/test_search.py` 中编写测试，覆盖：

### 6.1 search_files 测试

| 用例 | 说明 |
|------|------|
| 基本搜索 | `pattern="*.py"` 在项目 src 下搜索，返回 .py 文件 |
| max_depth 限制 | `max_depth=1` 只搜一层，不搜子目录 |
| max_results 截断 | 结果超过 max_results 时截断并提示 |
| 路径不存在 | 返回错误信息 |
| 无匹配 | `pattern="*.xyz123"` 返回"未找到" |
| 默认参数 | 只传 `pattern`，其他用默认值 |

### 6.2 search_content 测试

| 用例 | 说明 |
|------|------|
| 基本搜索 | `pattern="def run"` 搜索，返回带行号的结果 |
| file_glob 过滤 | `pattern="import"` + `file_glob="*.py"`，只搜 .py 文件 |
| context_lines | 验证上下文行数正确 |
| 正则搜索 | `pattern="class \\w+"` 支持正则 |
| 固定字符串优化 | 不含正则元字符时加 `--fixed-strings` |
| ReDoS 防护 | `(a+)+` 模式被拒绝 |
| 路径不存在 | 返回错误信息 |
| 无匹配 | 返回"未找到" |

### 6.3 工具注册测试

| 用例 | 说明 |
|------|------|
| 注册成功 | `dispatch("search_files", ...)` 和 `dispatch("search_content", ...)` 能正确路由 |
| schema 正确 | `get_all_schemas()` 包含两个新工具 |

---

## 七、文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `src/tools/search.py` | 搜索工具实现 |
| 修改 | `src/core/tools.py` | 注册两个新工具 |
| 修改 | `src/planning/prompts.py` | Shell 规范禁用 find/grep |
| 修改 | `src/tools/shell.py` | 删除 `_harden_find_command` 及相关常量 |
| 新建 | `tests/test_search.py` | 搜索工具测试 |

---

## 八、验收标准

1. `python -m pytest tests/ -q` 全部通过（原有 44 + 新增测试）
2. CLI 中 "找一下 lampson 项目里的 Python 文件" → 调用 `search_files` 而非 `find`
3. CLI 中 "搜一下哪里用了 def run" → 调用 `search_content` 而非 `grep`
4. shell 工具中不再需要 `_harden_find_command`
5. rg 不可用时返回友好错误，不 crash
