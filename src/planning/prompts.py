"""Task Planning 的 Prompt 模板。"""

# ── 共用常量块 ──

PERSISTENT_ENV_BLOCK = """## 环境信息
- 运行机器：macOS (Darwin, Apple Silicon)
- lampson 项目路径：/Users/songyuhao/lampson
- 工作目录：/Users/songyuhao
- 已知远程机器：train40 (IP: 10.136.61.40, 跳板: jump2@10.92.160.31)
- 本机只能执行本地命令，操作远程需 SSH
- 文件读取 100KB 限制，shell 默认超时 30 秒

## 行为准则
- 危险操作（rm -rf、chmod 777 等）执行前必须让用户确认
- 远程操作（train40 等）必须通过 SSH 命令
- 文件读取有 100KB 大小限制，超出请用 offset/limit 分批
- shell 命令默认超时 30 秒，复杂命令可设置 timeout 参数（最长 120 秒）

## 文件搜索规范
- **禁止使用 find 命令**，改用 `search_files` 工具（按文件名搜索）
- **禁止使用 grep/rg 命令**，改用 `search_content` 工具（按内容搜索）
- 需要查看目录内容用 `ls`，这是允许的（执行快，不会超时）"""

MEMORY_STRUCTURE_BLOCK = """## lampson 记忆结构

lampson 的所有持久化数据存储在 ~/.lampson/ 目录下：

```
~/.lampson/
├── memory/
│   ├── core.md              # 核心记忆（关于用户的基本偏好和重要事实）
│   └── sessions/            # 历史会话摘要（每个文件是一次对话的总结）
├── projects/                # 项目记录（用户主动记录的项目信息和文档）
│   └── <项目名>.md          # 每个项目一个文件，包含项目相关的笔记和信息
├── skills/                  # 技能文件（lampson 掌握的操作技能）
│   └── <技能名>/SKILL.md    # 每个技能一个目录
└── config.yaml              # 配置文件
```

相关工具：
- **memory_show**: 一次性展示所有记忆内容（core.md + projects + skills + 最近会话摘要）
- **project_context(name)**: 加载指定项目的完整记录（从 projects/ 目录读取）
- **skills_list**: 列出所有技能摘要
- **skill_view(name)**: 加载指定技能的全文内容
- **file_read**: 读取任意文件
- **file_write**: 写入文件（用于记录到 projects 等）
"""

EXPLORATION_RULES_BLOCK = """## 信息探测规则
如果用户请求涉及：
- "分析XX项目/代码" → 先确认项目路径（查 projects_index.md 或 `search_files` 搜索）
- "在XX机器上操作" → 先确认机器可达性（SSH测试）
- "查看XX文件/目录" → 先确认路径存在（ls 或 file_read）
- 任何不确定路径/地址/名称的地方 → 先用 shell/file_read 探测
探测步骤也是计划的一部分，用 $prev.result 引用探测结果。"""

PLAN_OUTPUT_FORMAT = """请只输出一个 JSON 对象，字段：
- "steps": 步骤列表，每步含 id, thought, action, args, reasoning
- "plan_summary": 一句话描述
- "expected_result": 执行完成后应得到什么（一句）

{{
  "steps": [{{"id": 1, "thought": "...", "action": "工具名", "args": {{}}, "reasoning": "..."}}],
  "plan_summary": "...",
  "expected_result": "..."
}}

参数传递规则：
- 上一步结果用 $prev.result；第 N 步用 $step[N].result；用户原文用 $goal
- 确定性参数直接写字面值
- $prev.result 不要用引号包裹
- action 必须是上面列出的工具之一"""


# ── 阶段一：意图分类 ──

def build_classify_prompt(goal: str, context: str, tools_desc: str) -> str:
    """阶段一：判断意图、是否需要工具、缺省信息与可能的直接回复。"""
    return f"""你是一个任务理解助手。根据用户目标与对话上下文，判断意图并决定是否需要工具。

{PERSISTENT_ENV_BLOCK}

{MEMORY_STRUCTURE_BLOCK}

## 工具能力
{tools_desc}

## 最近对话与上下文
{context}

## 用户目标
{goal}

请只输出一个 JSON 对象，不要其他文字。字段说明：
- "intent": 字符串，取值为 "chat" | "info_query" | "tool_task" | "unknown"
- "needs_tools": 布尔。闲聊/简单寒暄且无需查文件或执行命令则为 false
- "intent_detail": 一句话描述用户意图
- "confidence": 0.0-1.0
- "missing_info": 字符串数组。若需要工具但缺少关键信息（如路径未知），列出缺什么；否则 []
- "direct_reply": 若 needs_tools 为 false，可在此给出自然语言直接回复；若留空或 null 则由主对话模型生成
- "initial_plan": 可选。仅当 needs_tools 为 true 且 missing_info 非空时，提供 {{ "steps": [ ... ] }} 用于先收集信息；steps 中每项含 id, thought, action, args, reasoning

通用原则：**意图含糊时，confidence 降低并列出 missing_info，让阶段二去探测，不要硬选工具**

示例结构：
{{
  "intent": "tool_task",
  "needs_tools": true,
  "intent_detail": "...",
  "confidence": 0.85,
  "missing_info": ["项目根路径未确认"],
  "direct_reply": null,
  "initial_plan": {{
    "steps": [
      {{"id": 1, "thought": "...", "action": "shell", "args": {{}}, "reasoning": "..."}}
    ]
  }}
}}"""


# ── 阶段二：生成计划 ──

def build_plan_prompt_v2(
    goal: str,
    context: str,
    tools_desc: str,
    phase1_result: str,
    exploration_results: str,
) -> str:
    """阶段二：在已有分类结论与信息探测结果基础上生成可执行计划。"""
    return f"""你是一个任务规划助手。阶段一已判断用户意图，可能已执行信息探测。请基于**完整**信息把目标拆成可执行步骤。

{PERSISTENT_ENV_BLOCK}

{MEMORY_STRUCTURE_BLOCK}

{EXPLORATION_RULES_BLOCK}

## 工具能力
{tools_desc}

## 阶段一结果（JSON 或摘要）
{phase1_result}

## 信息探测/收集步骤的执行结果
{exploration_results}

## 最近对话
{context}

## 用户目标
{goal}

{PLAN_OUTPUT_FORMAT}"""


# ── 重新规划 ──

def build_replan_prompt(
    goal: str,
    context: str,
    tool_schemas: list[dict],
    failed_step: str,
    error_message: str,
    completed_steps: str,
    failure_context: str = "",
) -> str:
    """构建重新规划 prompt，带着失败信息重新规划。"""
    tools_desc = _format_tool_schemas(tool_schemas)
    fail_extra = f"\n{failure_context}\n" if failure_context.strip() else ""

    return f"""你是一个任务规划助手。之前的执行计划遇到了问题，需要重新规划。

{MEMORY_STRUCTURE_BLOCK}

可用工具：
{tools_desc}

用户目标：{goal}

当前上下文：
{context}

已完成步骤：
{completed_steps}
{fail_extra}
失败的步骤：
{failed_step}
错误信息：{error_message}

请根据已完成步骤的结果和失败信息，重新输出 JSON 格式的计划：

{{
  "steps": [
    {{
      "id": 1,
      "thought": "为什么这一步要做",
      "action": "工具名",
      "args": {{"参数名": "参数值"}},
      "reasoning": "参数是怎么确定的"
    }}
  ],
  "plan_summary": "一句话描述这个计划"
}}

参数传递规则：
- 如果参数值依赖上一步的结果，用 $prev.result 引用上一步的完整输出
- 如果需要引用第 N 步的结果，用 $step[N].result
- 如果需要引用用户原始目标，用 $goal
- 确定性参数直接写字面值"""


# ── 兼容保留（v1 fallback 用） ──

def build_plan_prompt(
    goal: str,
    context: str,
    tool_schemas: list[dict],
) -> str:
    """v1 规划 prompt（仅作 fallback，主流程已用 build_plan_prompt_v2）。"""
    tools_desc = _format_tool_schemas(tool_schemas)
    return f"""你是一个任务规划助手。给定用户目标和当前上下文，把目标分解成可执行步骤。

{MEMORY_STRUCTURE_BLOCK}

可用工具：
{tools_desc}

当前上下文：
{context}

用户目标：{goal}

{PLAN_OUTPUT_FORMAT}"""


# ── 结果汇总 ──

def build_synthesize_prompt(
    goal: str,
    step_results: str,
) -> str:
    """构建最终汇总 prompt，把所有步骤结果整理成用户可读的回答。"""
    return f"""你已经执行完以下步骤：
{step_results}

用户原始目标：{goal}

请根据用户的实际意图，整理以上执行结果，给出一个有条理、用户能直接理解的自然语言回答。
要求：
- 分析用户到底想了解什么，有针对性地回答
- 不要原文倾倒工具输出，要提炼要点
- 如果信息量大，分条列出关键内容
- 语气自然，像在对用户说话而不是在读报告"""


# ── 对话上下文 ──

def build_context_from_history(messages: list[dict], max_chars: int = 2000) -> str:
    """从对话历史构建上下文摘要，供规划 prompt 使用。"""
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

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...（上下文已截断）"
    return text


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
