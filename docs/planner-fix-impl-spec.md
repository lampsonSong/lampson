# Planner 修复实施 Spec（给 Cursor Agent）

## 背景
当前 lampson 的 planner 有严重的效率和质量问题。用户问"找一下hermes的代码在哪里"这样的简单问题，lampson 会走 classify → plan_v2 → execute → synthesize 共 4 次 LLM 调用，而且经常生成不必要的 search 步骤。需要让 lampson 对简单任务能快速响应，对复杂任务能正确规划。

## 需要修改的文件

1. `src/core/agent.py`
2. `src/planning/planner.py`
3. `src/planning/executor.py`
4. `src/planning/prompts.py`

## 具体改动

### 改动 1: agent.py — Fast Path（最高优先级）

在 `run()` 方法中，classify 之后、plan_v2 之前，加一个判断：

```python
# 在 intent = self._planner.classify(...) 之后，大约第201行

intent = self._planner.classify(goal=user_input, context=context)

if not intent.needs_tools:
    self.current_plan = None
    return self._reply_without_tools(user_input, intent)

# ===== 新增 Fast Path =====
if intent.confidence >= 0.8 and not intent.missing_info:
    logger.debug(f"Fast path: intent={intent.intent}, confidence={intent.confidence}")
    self.current_plan = None
    if self.llm.supports_native_tool_calling:
        return self._run_native()
    else:
        return self._run_prompt_based()
# ===== Fast Path 结束 =====

# 以下是原有的 full path (exploration + plan_v2 + execute)
```

Fast Path 的含义：当 classify 判定意图明确（confidence >= 0.8）且不缺信息（missing_info 为空），直接走原生 tool calling 循环，跳过 plan_v2 规划。LLM 自己决定调什么工具、调几次。

### 改动 2: agent.py — 计划确认环节

在 `run()` 方法中，plan_v2 生成后、execute 之前，暂停执行，返回计划描述让用户确认。

```python
plan = self._planner.plan_v2(...)
self.current_plan = plan

# ===== 新增：返回计划让用户确认 =====
plan_display = plan.format_for_display()
return f"{plan_display}\n\n请确认是否执行此计划？"
```

然后新增一个方法 `confirm_and_execute()`：

```python
def confirm_and_execute(self) -> str:
    """用户确认后执行当前计划。取消则返回空。"""
    if self.current_plan is None:
        return "[错误] 没有待执行的计划"
    plan = self.current_plan
    if plan.status == PlanStatus.created:
        plan.confirm()
    result = self._executor.execute(plan)
    self.current_plan = None
    return result

def cancel_plan(self) -> str:
    """取消当前计划。"""
    if self.current_plan is None:
        return "[提示] 没有待取消的计划"
    self.current_plan.cancel()
    msg = f"已取消计划：{self.current_plan.plan_summary}"
    self.current_plan = None
    return msg
```

### 改动 3: cli.py — 处理确认/取消

在 CLI 主循环中，当 agent.run() 返回的内容包含"请确认是否执行此计划"时，进入等待确认状态。

```python
# cli.py 的主循环中，处理 agent 响应之后：

response = agent.run(user_input)
print(response)

# 检查是否需要用户确认计划
if "请确认是否执行此计划" in response:
    confirm_input = session.prompt("确认执行？(y/n): ").strip().lower()
    if confirm_input in ("y", "yes", "是"):
        result = agent.confirm_and_execute()
        print(result)
    else:
        print(agent.cancel_plan())
```

注意：需要找到 cli.py 中实际的主循环位置，适配进去。飞书 listener 也需要类似处理（如果有的话），但优先级可以低一些。

### 改动 4: executor.py — 修复 $prev.result 截断

把 `_safe_replace_value` 方法从只取第一行改为取前 2000 字符：

```python
_MAX_REF_LENGTH = 2000

@staticmethod
def _safe_replace_value(result: str) -> str:
    if not result:
        return ""
    if len(result) <= _MAX_REF_LENGTH:
        return result
    return result[:_MAX_REF_LENGTH] + "\n...（结果过长已截断）"
```

### 改动 5: planner.py — 调整置信度保底

修改 `classify()` 方法中的保底逻辑：

```python
# 原：
_INTENT_CONFIDENCE_TOOL_FLOOR = 0.4

# 改为：
_INTENT_CONFIDENCE_TOOL_FLOOR = 0.3
```

```python
# 原：
if not result.needs_tools and result.confidence < _INTENT_CONFIDENCE_TOOL_FLOOR:
    result.needs_tools = True

# 改为：
if not result.needs_tools and result.confidence < _INTENT_CONFIDENCE_TOOL_FLOOR:
    if result.intent in ("tool_task", "unknown"):
        result.needs_tools = True
```

### 改动 6: prompts.py — 优化 classify prompt

在 `build_classify_prompt()` 的 return 字符串中，在 "通用原则" 那一段后面追加：

```
## 决策指引
- 如果用户给的路径/名称已经足够明确（如"看一下 /Users/songyuhao/lampson/src/core/agent.py"），missing_info 应为空 []
- 只有路径/名称真正不确定时才需要把 missing_info 设为非空（如"那个训练项目的代码"需要确认是哪个项目）
- "帮我找一下 XXX" 类请求 → needs_tools=true，但通常一步就能完成
- 闲聊、知识问答、代码解释类请求 → needs_tools=false
- 如果判断 needs_tools=true 且 confidence >= 0.8 且 missing_info 为空，说明意图非常清晰，不需要信息收集
```

## 注意事项
- 不要改动 steps.py（数据类定义）
- 不要改动 tools.py（工具注册）
- 不要改动 search.py（搜索工具实现）
- 保留现有的 fallback 逻辑（PlanParseError 时回退到单轮模式）
- 所有改动要保持现有的日志记录风格
