# Planner 设计文档 v2

> 更新日期：2026-04-25
> 背景：v1 设计已实现（Plan-and-Execute 单轮规划），本次升级为 **两阶段判断 + ReAct 执行循环**

---

## 1. 核心升级目标

v1 的问题：
- Planner 只拿到 query + 历史摘要，对自身运行环境一无所知，是个"盲人规划器"
- 没有"要不要工具"的判断，所有输入强制走规划
- 执行中失败只能重试，没有动态 replan

v2 要解决：
1. Planner 拿到完整的上下文信息（分层注入）
2. Planner 先判断"要不要工具"，再决定下一步
3. 执行中每步评估结果，失败时动态 replan

---

## 2. 信息分层架构

### 2.1 三层定义

| 层级 | 内容 | 何时加载 | 谁提供 |
|------|------|----------|--------|
| **常驻层** | 工具 schema（10个）、soul 核心规则、环境基础信息 | 每次规划前 | Agent 构造 |
| **按需层** | skills 列表、projects 摘要、core memory 摘要 | Planner 判断需要时显式请求 | 通过 skill_context / project_context / memory_search 工具注入 |
| **动态层** | 历史对话（截断至 N 字）、当次 query | 每次传入 | Agent 构造 |

### 2.2 常驻层内容

```
## 环境信息
- 运行机器：MacBook-Pro.local（Darwin，CPU: Apple Silicon）
- 当前用户：songyuhao
- lampson 项目路径：/Users/songyuhao/lampson
- 工作目录：/Users/songyuhao（可通过 shell("pwd") 动态确认）
- 已知远程机器：train40（IP: 10.136.61.40，跳板: jump2@10.92.160.31）
- 本机只能执行本地命令，如需操作远程机器需通过 SSH

## 工具能力（10个）
{tools_schemas}

## 行为准则
- 危险操作（rm -rf、chmod 777 等）执行前必须让用户确认
- 远程操作（train40 等）必须通过 SSH 命令
- 文件读取有 100KB 大小限制，超出请用 offset/limit 分批
- shell 命令默认超时 60 秒，超时需设置 timeout 参数
```

### 2.3 按需层加载策略

**按需层的核心设计：Planner 自己判断需要什么信息，自己决定怎么获取。**

具体做法：在 `build_plan_prompt()` 里增加一段"信息探测指导"，告诉 Planner：

```
## 信息探测规则
如果用户请求涉及以下场景，你需要先探索环境，再出计划：
- "分析 XX 项目/代码" → 先确认项目路径（查 projects_index.md 或 find 搜索）
- "在 XX 机器上操作" → 先确认机器可达性（SSH 测试）
- "查看 XX 文件/目录" → 先确认路径存在（ls 或 file_read）
- 任何你不确定路径、地址、名称的地方 → 先用 shell("find ...") / file_read 探测

探测步骤也是计划的一部分，用 $prev.result 引用探测结果来出后续步骤。
```

也就是说，按需层不是"预加载"，而是 Planner 通过**主动的信息收集步骤**来获取。

### 2.4 动态层构造

```python
def _build_planner_context(agent: Agent, query: str) -> str:
    """构造传给 Planner 的上下文文本。"""
    # 常驻层：工具 + 环境基础信息（硬编码在 prompt 模板中）

    # 动态层：历史对话
    history = build_context_from_history(agent.llm.get_history(), max_chars=2000)
    # 加上当次 query
    return f"## 最近对话\n{history}\n\n## 本次请求\n{query}"
```

---

## 3. Planner 决策流程（两阶段）

### 3.1 阶段一：理解与分类（第一次 LLM 调用）

**目标**：判断用户意图，决定是否需要工具。

**Prompt 输入**：
- 常驻层（环境 + 工具 + 行为准则）
- 动态层（对话历史 + query）

**Prompt 输出**：JSON

```json
{
  "intent": "chat | info_query | tool_task | unknown",
  "needs_tools": true | false,
  "intent_detail": "一句话描述用户意图",
  "confidence": 0.0-1.0,
  "missing_info": ["如果需要工具但缺少关键信息，列出需要什么"],
  "initial_plan": {
    "steps": [...]
  }
}
```

**intent 分类**：
- `chat`：闲聊、寒暄、简单问答 → 不需要工具
- `info_query`：查信息、问状态 → 可能需要工具（file_read/shell）
- `tool_task`：需要执行操作的任务 → 必须工具
- `unknown`：无法判断 → 保守处理，认为需要工具

**missing_info**：
如果 `needs_tools=true` 但缺少关键信息（如不知道项目路径），在这里列出，阶段一会生成"探测步骤"来获取这些信息。

### 3.2 分支处理

```
阶段一判断结果
  │
  ├─ needs_tools = false
  │    → 直接回复：用主 LLM 生成自然语言回答（不走 Executor）
  │
  └─ needs_tools = true
       │
       ├─ missing_info 不为空
       │    → 执行"信息收集步骤"（阶段一生成的 initial_plan）
       │    → 拿到结果后 → 阶段二
       │
       └─ missing_info 为空
            → 直接进入阶段二
```

### 3.3 阶段二：出执行计划（第二次 LLM 调用）

**目标**：基于完整信息，生成可执行步骤列表。

**Prompt 输入**：
- 阶段一的判断结果
- 如果执行了信息收集步骤 → 收集结果也注入
- 动态层（更新后的上下文）

**Prompt 输出**：JSON

```json
{
  "steps": [
    {
      "id": 1,
      "thought": "为什么这一步要做",
      "action": "工具名",
      "args": {"参数名": "参数值"},
      "reasoning": "参数是怎么确定的"
    }
  ],
  "plan_summary": "一句话描述这个计划",
  "expected_result": "执行完这个计划后应该得到什么"
}
```

### 3.4 设计决策

**为什么分两次调用而不是一次？**

哥哥确认了：Planner 以准确为首要目标，准确之后再省 token。

一次调用的问题：Planner 既要判断意图，又要规划步骤，还要处理信息缺失，prompt 会非常长且复杂，两种不同的决策混在一起容易出错。分开后：
- 阶段一专注"理解 + 判断"，轻量快速
- 阶段二专注"规划 + 执行"，拿到完整信息后才出计划

---

## 4. 执行循环（ReAct 风格）

### 4.1 核心循环

```
执行计划中的每个步骤
      │
      ▼
执行 step_i → 拿到 result_i
      │
      ▼
评估：result_i 是否符合预期？
      │
      ├─ 符合 → 继续执行 step_{i+1}
      │
      └─ 不符合 →
          记录失败尝试（step_id, action, args, error, tried_solutions）
              │
              ▼
          判断原因：
          - 参数错误 → 修正参数重试（最多2次）
          - 计划错误 → replan（最多3次）
          - 环境限制 → 中止，告诉用户限制
```

### 4.2 每步评估逻辑

```python
def _evaluate_step_result(step: Step, result: str) -> StepEvaluation:
    """评估步骤执行结果是否正常。"""
    # 检测错误模式
    if "[错误]" in result or "[拒绝]" in result or "Traceback" in result:
        return StepEvaluation(
            ok=False,
            reason="工具执行出错",
            should_retry=True,
            is_plan_flawed=False
        )

    # 检测"找不到"模式
    if "No such file" in result or "not found" in result or "不存在" in result:
        return StepEvaluation(
            ok=False,
            reason="路径/资源不存在",
            should_retry=False,
            is_plan_flawed=True  # 计划本身错了（路径填错了）
        )

    # 检测结果为空（某些步骤不应该为空）
    if not result.strip() and step.action not in ("file_write", "feishu_send"):
        return StepEvaluation(
            ok=False,
            reason="结果为空，可能步骤不可行",
            should_retry=False,
            is_plan_flawed=True
        )

    return StepEvaluation(ok=True)
```

### 4.3 失败历史记录

```python
@dataclass
class FailedAttempt:
    step_id: int
    action: str
    args: dict
    error: str
    tried_solutions: list[str]  # 尝试过的修正方案

# 保存在 Plan 对象里
class Plan:
    # ... 现有字段 ...
    failed_attempts: list[FailedAttempt] = field(default_factory=list)

    def add_failure(self, attempt: FailedAttempt):
        self.failed_attempts.append(attempt)

    def get_failure_context(self) -> str:
        """生成失败历史文本，注入 replan prompt。"""
        if not self.failed_attempts:
            return ""
        lines = ["## 之前的失败尝试"]
        for f in self.failed_attempts:
            lines.append(f"- 步骤{f.step_id}: {f.action}({f.args})")
            lines.append(f"  错误: {f.error}")
            lines.append(f"  已尝试: {', '.join(f.tried_solutions)}")
        return "\n".join(lines)
```

### 4.4 Replan 流程

当 `is_plan_flawed=True` 且重试耗尽时：

```python
# Agent.run() 或 Executor 中
if should_replan:
    failure_context = plan.get_failure_context()
    new_plan = planner.replan(
        goal=goal,
        context=updated_context,
        failed_step=step,
        completed_steps=completed_steps,
        failure_context=failure_context
    )
    if len(plan.failed_attempts) >= MAX_REPLAN_COUNT:
        return f"[中止] 已重试 {MAX_REPLAN_COUNT} 次仍失败。已完成: {completed_summary}。失败原因: {last_error}"
```

### 4.5 直接回复的处理

当阶段一判断 `needs_tools = false` 时，Planner 不生成计划，直接生成回复：

```python
# Planner._call_llm() 的分支
if phase == "classify":
    intent_result = _parse_intent_response(raw)
    if not intent_result.needs_tools:
        # 直接生成回复（用主 LLM，不走 Executor）
        reply = agent_llm.generate_reply(
            query=query,
            context=planner_context,
            intent=intent_result.intent
        )
        return reply  # Planner 直接返回回复，不走 Executor
```

---

## 5. 完整流程图

```
用户输入
      │
      ▼
┌─────────────────────────────────────┐
│  阶段一：理解与分类（第一次 LLM）      │
│  输入: 常驻层 + 动态层                │
│  输出: intent / needs_tools /        │
│        missing_info / initial_plan   │
└──────────────┬──────────────────────┘
               │
       needs_tools = false
               │
               ▼
        直接回复（主 LLM 生成）
               │
               │
needs_tools = true │
               │
       ┌────────┴────────┐
       │ missing_info?   │
       └────────┬────────┘
                │
        有 → 执行信息收集步骤
        │    （initial_plan）
        │    ↓
        │  阶段二（第二次 LLM）
        │  输入: 收集结果 + 完整上下文
        │  输出: 执行计划
        │
        无 → 阶段二（第二次 LLM）
             输入: 完整上下文
             输出: 执行计划
               │
               ▼
┌──────────────────────────────────────┐
│  执行循环（Executor）                  │
│  for step in plan.steps:             │
│    result = execute(step)             │
│    评估 result                        │
│    ├─ OK → 继续                      │
│    └─ 失败 → 记录 → 判断原因          │
│         ├─ 参数错 → 重试（最多2次）    │
│         ├─ 计划错 → replan（最多3次）  │
│         └─ 环境限制 → 中止             │
└──────────────┬───────────────────────┘
               │
               ▼
         汇总结果（主 LLM）
               │
               ▼
         返回给用户
```

---

## 6. 与 v1 的差异

| 方面 | v1 | v2 |
|------|----|----|
| Planner 输入 | query + 历史摘要（盲人） | 常驻层 + 按需层（主动探测）+ 动态层 |
| 判断流程 | 无，所有输入强制规划 | 阶段一先判断是否需要工具 |
| 信息缺失处理 | 不知道缺信息，硬猜 | 阶段一显式列出 missing_info，先收集再规划 |
| 执行中失败 | 只重试，耗尽则中止 | 每步评估，动态 replan |
| 闲聊处理 | 也会生成1步计划 | 阶段一判断为 chat，直接回复 |
| LLM 调用次数 | 1次（直接规划） | 2次（判断 + 规划），闲聊时只有1次 |

---

## 7. 实施计划

### Phase 1：信息分层 + 两阶段判断（核心改造）
1. 新增 `build_classify_prompt()` 和 `build_plan_prompt_v2()`（区分两个阶段）
2. 新增 `IntentResult` 数据类（阶段一输出）
3. 改造 `Planner` 类：新增 `classify()` 方法，区分判断和规划
4. 改造 `Agent.run()`：先 classify，再根据结果分支
5. 常驻层 prompt 模板写入（环境信息硬编码）

### Phase 2：ReAct 执行循环
1. 新增 `StepEvaluation` 数据类
2. 新增 `FailedAttempt` 数据类
3. 改造 `Executor.execute()`：每步评估 + 失败历史记录
4. 实现 `Planner.replan()` 的完整调用逻辑
5. 最大 replan 次数保护（3次）

### Phase 3：工具保护
1. shell 工具加命令长度检测（超过 ARG_MAX 拒绝并提示）
2. shell 工具加复杂度检测（检测 `cat *` 等危险模式）

---

## 8. 风险

1. **两次 LLM 调用 token 翻倍**：闲聊时阶段一只返回 direct_reply，但仍是两次调用（一次 classify 才知道是闲聊）。解决：闲聊可以用更小的模型做 classify（后续优化方向）。
2. **按需层信息探测可能失败**：如果探测步骤本身出错，Planner 可能陷入"探测→失败→再探测"的循环。解决：探测失败也要记录到 failed_attempts，replan 时让 Planner 知道"这条路走不通"。
3. **Planner 的分类判断可能出错**：把本该需要工具的请求判断为闲聊。解决：confidence 低于阈值时保守处理，认为需要工具。
