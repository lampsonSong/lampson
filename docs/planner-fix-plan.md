# Planner 修复方案

> 日期: 2026-04-26
> 状态: 待实施

## 诊断摘要

当前 planner 存在 5 个核心问题，导致用户体验差（计划不透明、响应慢、search 滥用）。

---

## Fix 1: 加入计划确认环节

**问题**: `Plan.confirm()` 和 `PlanStatus.confirmed` 已定义但从未调用，用户完全不知道将要执行什么。

**方案**: 在 `agent.py` 的 `run()` 中，plan 生成后暂停，等用户确认再执行。

```python
# agent.py run() 中，生成 plan 后:

plan = self._planner.plan_v2(...)
self.current_plan = plan

# 显示计划，等用户确认
plan_display = plan.format_for_display()
return f"{plan_display}\n\n确认执行吗？(y/n)"
```

新增一个 `confirm_and_execute()` 方法，由 CLI/飞书在用户回复 y 时调用：

```python
def confirm_and_execute(self) -> str:
    """用户确认后执行当前计划。"""
    if self.current_plan is None:
        return "[错误] 没有待执行的计划"
    plan = self.current_plan
    if plan.status == PlanStatus.created:
        plan.confirm()
    return self._executor.execute(plan)
```

CLI 层判断: 如果 `agent.run()` 返回的内容包含"确认执行吗"，就进入等待确认状态，下一轮输入 y/n 时调用 `confirm_and_execute()` 或取消。

**改动文件**: `agent.py`, `cli.py`

---

## Fix 2: 单步任务快速通道 (Fast Path)

**问题**: 简单的"搜个文件"、"读个文件"也要走 classify → plan_v2 → execute → synthesize 共 4 次 LLM 调用，延迟高、token 浪费。

**方案**: classify 返回后，如果 needs_tools=true 但 intent 很明确（confidence >= 0.8 且 missing_info 为空），跳过 plan_v2，直接走原生 tool calling 循环。

```python
# agent.py run() 中，classify 之后:

intent = self._planner.classify(goal=user_input, context=context)

if not intent.needs_tools:
    return self._reply_without_tools(user_input, intent)

# Fast path: 意图明确、不缺信息，直接让 LLM 通过 tool calling 完成
if intent.confidence >= 0.8 and not intent.missing_info:
    logger.debug(f"Fast path: intent={intent.intent}, confidence={intent.confidence}")
    self.current_plan = None
    if self.llm.supports_native_tool_calling:
        return self._run_native()
    else:
        return self._run_prompt_based()

# Full path: 意图模糊或需要信息收集，走完整规划
```

这样简单任务只有 classify (1次) + tool calling (1-2次) + 可能的 synthesize (1次)，省掉了 plan_v2。

**改动文件**: `agent.py`

---

## Fix 3: 降低置信度保底的侵略性

**问题**: confidence < 0.4 时强制 needs_tools=true，导致很多闲聊也被推入工具链。

**方案**: 调整保底逻辑，闲聊类的 intent (chat) 即使 confidence 低也不强制走工具。

```python
# planner.py classify() 中:

if not result.needs_tools and result.confidence < _INTENT_CONFIDENCE_TOOL_FLOOR:
    # 只有意图看起来像工具任务但 confidence 不够时，才保守走工具
    if result.intent in ("tool_task", "unknown"):
        result.needs_tools = True
    # intent="chat" 或 "info_query" 且 confidence 低 → 可能是闲聊，不强制
```

同时把阈值从 0.4 提高到 0.3（更宽松，只有 confidence 极低时才触发）：

```python
_INTENT_CONFIDENCE_TOOL_FLOOR = 0.3
```

**改动文件**: `planner.py`

---

## Fix 4: 改进 $prev.result 引用只取第一行的问题

**问题**: `_safe_replace_value()` 只取结果第一行，大量上下文丢失。比如 search_content 返回 50 行结果，下一步只能看到第 1 行。

**方案**: 改为取前 N 个字符（而不是只取第一行），并保留更多有用信息。

```python
# executor.py

_MAX_REF_LENGTH = 2000  # 引用替换时的最大字符数

@staticmethod
def _safe_replace_value(result: str) -> str:
    if not result:
        return ""
    if len(result) <= _MAX_REF_LENGTH:
        return result
    return result[:_MAX_REF_LENGTH] + "\n...（结果过长已截断）"
```

**改动文件**: `executor.py`

---

## Fix 5: 优化 classify prompt 减少 search 滥用

**问题**: classify 阶段的 prompt 没有告诉 LLM "什么时候不需要搜索"，导致 LLM 遇到任何不确定的情况都倾向于生成 search 步骤。

**方案**: 在 `build_classify_prompt()` 中增加决策指引：

```
## 决策指引
- 用户的问题如果是关于已有项目/代码的，但路径已知（如 lampson 项目在 /Users/songyuhao/lampson），
  不需要 search 来"确认路径"，直接用已知路径。
- 如果用户给的信息足够明确（如"看一下 xxx 文件"），missing_info 应为空。
- 只有路径/名称真正不确定时才需要 search 探测。
- 闲聊、知识问答、代码解释类请求 → needs_tools=false
- "帮我找一下 XXX 在哪" → needs_tools=true，但应该直接一步 search，不需要复杂计划
```

**改动文件**: `prompts.py` 的 `build_classify_prompt()`

---

## 实施优先级

| 优先级 | Fix | 影响 | 改动量 |
|---|---|---|---|
| P0 | Fix 2: Fast Path | 大幅降低简单任务延迟 | 小（agent.py 约 15 行） |
| P0 | Fix 1: 计划确认 | 用户能看到将执行什么 | 中（agent.py + cli.py 约 40 行） |
| P1 | Fix 4: $prev.result 截断 | 多步计划质量提升 | 小（executor.py 约 5 行） |
| P1 | Fix 3: 置信度保底 | 减少误判 | 小（planner.py 约 5 行） |
| P2 | Fix 5: classify prompt | 减少 search 滥用 | 小（prompts.py 约 10 行） |

建议先做 Fix 2 + Fix 4（纯内部逻辑，不影响外部接口），再做 Fix 1（需要 CLI 配合），最后做 Fix 3 + Fix 5。
