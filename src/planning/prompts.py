"""Task Planning 的 Prompt 模板。"""


def build_plan_prompt(
    goal: str,
    context: str,
    tool_schemas: list[dict],
) -> str:
    """构建规划 prompt，让 LLM 生成步骤列表。

    Args:
        goal: 用户原始目标。
        context: 当前对话上下文（前几轮对话摘要）。
        tool_schemas: 可用工具的 schema 列表。

    Returns:
        完整的规划 prompt 文本。
    """
    tools_desc = _format_tool_schemas(tool_schemas)

    return f"""你是一个任务规划助手。给定用户目标和当前上下文，你需要：
1. 把目标分解成最小可执行步骤
2. 每步只做一个工具调用
3. 考虑可能出错的地方，加错误处理
4. 如果需要外部信息（文件内容、命令输出），在相关步骤前加"查询"步骤
5. 如果任务很简单，只需要一步就能完成，就只输出一个步骤

## 语义映射（用户意图 → 工具选择）

**"记忆/记了啥"类问题：**
- "你记了啥"/"你记住了什么"/"看看记忆"/"你都存了什么" → **memory_show**
- "你都写了什么"/"刚才记录了什么"/"刚才写进去的是啥" → 读取最近写入的 project 文件：`file_read`(`path`=`~/.lampson/projects/xxx.md`)，文件名从上下文中的写入操作结果推断（如 `已写入 .../xxx.md`）

**"记录/写入"类问题：**
- "记录在XX项目"/"把...写到XX" → **file_write**，`content` 直接用用户的原文（`$goal` 去掉指令前缀后就是用户要记录的内容），**不要先调用 skills_list 或 project_context**，除非用户明确要求"检查项目是否存在"
- 如果 `$goal` 包含大段正文（如项目概览、文档），`content` 就是那段正文，不需要查询任何其他工具

**"查看项目"类问题：**
- "查看XX项目"/"XX项目是什么" → **project_context**(`name`="XX")

**"技能"类问题：**
- "列出技能" → **skills_list**
- "查看XX技能详情" → **skill_view**(`name`="XX")

**⚠️ 常见错误：**
- **绝不要**把 `skills_list` 的输出当作内容写入文件——skills_list 只返回技能摘要，不是用户要记录的内容
- "你都记了啥"在有上文时（如最近执行过写入操作），优先读那个 project 文件，而不是再调 skills_list
- 写入文件前不需要先"查询项目列表"或"列出技能"，直接写入

可用工具：
{tools_desc}

当前上下文：
{context}

用户目标：{goal}

请输出JSON格式的计划（不要输出其他内容，只输出JSON）：
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
- 确定性参数（如固定路径、已知值）直接写字面值
- 示例：第一步 file_read 读配置文件，第二步 shell 执行配置中的命令，
  第二步的 command 参数写 "bash $prev.result"

⚠️ 重要：$prev.result 是纯文本占位符，**不要用引号包裹它**：
- 正确: "command": "ls $prev.result"
- 错误: "command": "ls '$prev.result'"  ← 这样无法被解析器识别
- 错误: "command": "ls \"$prev.result\""  ← 双引号也不行

注意：
- action 必须是上面列出的可用工具之一
- args 必须符合该工具的参数 schema
- 不要编造不存在的参数
- 如果只需要一步就能完成，就只输出一个步骤"""


def build_replan_prompt(
    goal: str,
    context: str,
    tool_schemas: list[dict],
    failed_step: str,
    error_message: str,
    completed_steps: str,
) -> str:
    """构建重新规划 prompt，带着失败信息重新规划。

    Args:
        goal: 用户原始目标。
        context: 当前上下文。
        tool_schemas: 可用工具 schema。
        failed_step: 失败步骤的描述。
        error_message: 错误信息。
        completed_steps: 已完成步骤的结果。

    Returns:
        重新规划的 prompt。
    """
    tools_desc = _format_tool_schemas(tool_schemas)

    return f"""你是一个任务规划助手。之前的执行计划遇到了问题，需要重新规划。

可用工具：
{tools_desc}

用户目标：{goal}

当前上下文：
{context}

已完成步骤：
{completed_steps}

失败的步骤：
{failed_step}
错误信息：{error_message}

请根据已完成步骤的结果和失败信息，重新输出JSON格式的计划（不要输出其他内容，只输出JSON）：
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


def build_synthesize_prompt(
    goal: str,
    step_results: str,
) -> str:
    """构建最终汇总 prompt，把所有步骤结果整理成用户可读的回答。

    Args:
        goal: 用户原始目标。
        step_results: 所有步骤执行结果的文本。

    Returns:
        汇总 prompt。
    """
    return f"""你已经执行完以下步骤：
{step_results}

用户原始目标：{goal}

请给用户一个完整的回答。总结执行结果，告诉用户目标是否达成。"""


def build_context_from_history(messages: list[dict], max_chars: int = 2000) -> str:
    """从对话历史构建上下文摘要，供规划 prompt 使用。

    Args:
        messages: LLM 对话消息列表。
        max_chars: 最大字符数。

    Returns:
        截断后的上下文文本。
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            # OpenAI 格式的 content（多模态）
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
