# Task Planning Design

## 1. 现状

Lampson 目前是**单轮问答模式**：用户说一句话，Lampson 调工具回答一句，结束。没有多步骤规划和执行能力。

**用户期望 vs 实际行为：**

| 用户说 | 期望行为 | 实际行为 |
|--------|----------|----------|
| "在训练机器40上找模型平台工程" | 1. 查机器记录 → 2. SSH 登录 → 3. 搜索文件 | 回复一句话 |
| "帮我启动服务并检查日志" | 1. 启动服务 → 2. 等启动 → 3. 查日志 | 回复一句话 |
| "修复这个bug，先看代码再改" | 1. file_read → 2. 分析 → 3. file_write | 回复一句话 |

工具都在，但不会**按顺序、依条件**地调用它们。

---

## 2. 目标

给 Lampson 加**任务规划**（Task Planning）能力：

- 用户给一个高层目标，Lampson **先规划步骤，再执行**，最后汇报结果
- 能处理需要**多步骤、条件分支、循环**的任务
- 能处理需要**外部信息**才知道下一步的场景（如"先查一下机器IP再登录"）

---

## 3. 设计选择

### 方案 A：ReAct（Think-Act-Observe）

每个步骤：**思考（Thought）→ 行动（Action）→ 观察（Observation）→ ... → 最终答案**

```
LLM: Thought: 我需要先查机器40的IP地址
LLM: Action: file_read("/记录/机器列表.md")
LLM: Observe: 机器40的IP是192.168.1.40
LLM: Thought: 现在登录机器40
LLM: Action: shell("ssh user@192.168.1.40")
...
```

**优点**：实现简单，LLM 原生支持
**缺点**：步骤和回答混在一起，长任务时 context 膨胀

### 方案 B：Plan-and-Execute

先**一次性规划所有步骤**（Plan），再**逐个执行**（Execute），最后**汇总**（Synthesize）

```
LLM (Plan阶段):
  步骤1: 读机器记录 → 找到机器40的IP
  步骤2: SSH登录机器40
  步骤3: 在/home/xxx目录下搜索模型平台工程文件

LLM (Execute阶段):
  执行步骤1 → 结果 → 执行步骤2 → 结果 → 执行步骤3 → 最终答案
```

**优点**：计划可见，可让用户确认后再执行；步骤可并行
**缺点**：需要两次 LLM 调用（plan + execute）

### 方案 C：增量规划（Replan）

一次规划 + 执行每步后**检查结果，决定下一步**（遇到意外就 replan）

**优点**：灵活，适合不确定性高的任务
**缺点**：实现复杂，需要保存 plan state

---

## 4. 推荐方案：Plan-and-Execute

Lampson 采用 **Plan-and-Execute**，理由：
1. 计划对用户可见，可以中途打断、修改、追加步骤
2. 实现相对简单，两轮 LLM 调用
3. 适合 Lampson 的工具集（工具比较重，需要明确顺序）
4. 容易加**执行模式开关**（自动执行 vs 每步确认）

### 4.1 "需要规划吗"的判断策略

**不使用硬编码关键词判断**。中文表达千变万化，关键词列表永远覆盖不完。

采用**统一走规划器**策略：
- 所有用户输入都经过 Planner
- 单步任务自然退化为 1-step plan（plan 成本极低，一次 LLM 调用）
- 不需要维护"规划 vs 不规划"的前端判断逻辑

好处：
- 逻辑简单，一条路径，不用维护两套执行代码
- 即使是"你好"这种闲聊，1-step plan 的额外开销也很小
- 避免"本应规划但没规划"的漏判问题

---

## 5. 架构

### 5.1 新增文件

```
src/
  planning/
    __init__.py
    planner.py      # Planner 类，核心规划逻辑
    steps.py        # Step / StepResult 数据类
    prompts.py      # 规划用的 prompt 模板
    executor.py     # 执行器，执行每一步
```

### 5.2 核心数据类

```python
class PlanStatus(str, Enum):
    created = "created"        # 已规划，未确认
    confirmed = "confirmed"    # 用户已确认，准备执行
    executing = "executing"    # 正在执行中
    completed = "completed"    # 全部步骤完成
    failed = "failed"          # 某步骤失败且未恢复
    cancelled = "cancelled"    # 用户取消

@dataclass
class Step:
    id: int
    thought: str           # 为什么这一步要做
    action: str            # 工具名
    args: dict             # 工具参数（支持 $prev 引用，见 5.5 节）
    status: str            # pending | running | done | failed | skipped
    result: str | None     # 执行结果（完成后填充）

@dataclass
class Plan:
    id: str                    # 唯一标识
    goal: str                  # 用户原始目标
    steps: list[Step]          # 步骤列表
    status: PlanStatus         # 当前状态
    plan_summary: str          # 一句话描述计划
    created_at: float          # 创建时间
    current_step_index: int    # 执行到第几步

@dataclass
class StepResult:
    step_id: int
    observation: str       # 执行结果
    status: str            # success | error
    is_final: bool          # 这是不是最后一步
```

### 5.3 Plan 状态机

```
created ──→ confirmed ──→ executing ──→ completed
   │                          │    \→ failed
   │                          \→ cancelled
   \→ cancelled
```

状态转换规则：

| 当前状态 | 触发条件 | 目标状态 |
|----------|----------|----------|
| created | 用户确认 / auto_confirm=True | confirmed |
| created | 用户取消 | cancelled |
| confirmed | 开始执行第一步 | executing |
| executing | 所有步骤 done | completed |
| executing | 某步骤 failed + 重试耗尽 | failed |
| executing | 用户说"停" | cancelled |
| failed | 用户说"重新规划" | created（新 plan） |

### 5.4 Planner 类

```python
class Planner:
    def __init__(self, llm: LLMClient, tools: list[dict]):
        self.llm = llm
        self.tools = tools

    def plan(self, goal: str, context: str) -> list[Step]:
        """给定目标 + 上下文，返回步骤列表。"""

    def execute(
        self,
        steps: list[Step],
        on_step_end: Callable[[Step, StepResult], None],
        auto_confirm: bool = False,
    ) -> str:
        """执行步骤列表，返回最终答案。"""
```

### 5.5 参数传递机制

Plan 阶段，某些步骤的参数依赖上一步的执行结果。例如"先查IP再SSH"，第2步的 IP 地址在第1步执行前不知道。

**语法：`$prev.result` 引用上一步结果**

Plan prompt 生成的 args 中允许使用以下引用：

| 引用 | 含义 | 示例 |
|------|------|------|
| `$prev.result` | 上一步的完整结果文本 | `"command": "ssh root@$prev.result"` |
| `$step[N].result` | 第 N 步的结果 | `"path": "$step[1].result"` |
| `$goal` | 用户原始目标 | `"query": "搜索 $goal 相关信息"` |

**解析时机**：executor 在执行每一步前，用字符串替换把引用替换成实际值。

**解析失败**：如果引用的步骤还没执行或结果为空，该步骤 status 设为 failed，error 记录引用解析失败。

### 5.6 Executor 与 tools.py 的边界

**Executor 只做编排，不做执行。**

```
Executor                          tools.py
┌──────────────┐                 ┌──────────────┐
│ 遍历 steps   │                 │              │
│ 解析 $prev   │  step.action    │  dispatch()  │
│ 维护上下文   │ ──────────────→ │  执行工具    │
│ 处理失败     │  result         │  返回结果    │
│ 汇总结果     │ ←────────────── │              │
└──────────────┘                 └──────────────┘
```

- executor 不知道工具怎么执行，只知道"该调哪个工具、传什么参数"
- tools.py 不知道自己在被编排，和单轮调用时行为一致
- 好处：两套逻辑解耦，工具可以在规划和非规划模式下复用

### 5.7 Agent 集成方式

在 `Agent.run()` 里改造为统一走规划器：

```
用户输入
    │
    ▼
┌─────────────────┐
│ Planner.plan()  │ ← 所有输入都走规划器
└────────┬────────┘
         │
    ┌────┴─────┐
    │ 1-step   │ N-step
    │ 直接执行 │ 展示计划 → 执行
    ▼         ▼
 Planner.execute()
    │
    ▼
 返回结果
```

**单步退化**：plan 返回只有 1 个 step 时，跳过确认直接执行，行为和当前单轮调用一致。

**判断规则**：不需要判断。统一走规划器，1-step plan 自然退化。

---

## 6. 规划 Prompt 设计

### Plan Prompt

```
你是一个任务规划助手。给定用户目标和当前上下文，你需要：
1. 把目标分解成最小可执行步骤
2. 每步只做一个工具调用
3. 考虑可能出错的地方，加错误处理
4. 如果需要外部信息（文件内容、命令输出），在相关步骤前加"查询"步骤

可用工具：{tool_schemas}

当前上下文：
{context}

用户目标：{goal}

请输出JSON格式的计划：
{{
  "steps": [
    {{
      "id": 1,
      "thought": "为什么这一步要做",
      "action": "工具名",
      "args": {{"参数名": "参数值"}},
      "reasoning": "参数是怎么确定的"
    }},
    ...
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
```

### Execute Prompt（可选，用于最终汇总）

```
你已经执行完以下步骤：
{step_results}

用户原始目标：{goal}

请给用户一个完整的回答。
```

**中间步骤的上下文注入**：

执行到第 N 步时，LLM 怎么知道前 N-1 步的结果？每次执行步骤前，executor 把已完成步骤的 observation 拼接到执行上下文中：

```python
execution_context = f"已完成步骤：\n"
for i in range(current_step_index):
    step = plan.steps[i]
    execution_context += f"步骤{step.id}: {step.action}({step.args})\n"
    execution_context += f"结果: {step.result}\n\n"

# 同时用于参数引用解析
# $prev.result → plan.steps[current_step_index - 1].result
# $step[N].result → plan.steps[N-1].result
```

这样每一步执行时，`$prev.result` 等引用都能被正确解析。

---

## 7. 执行模式

### 7.1 自动执行（默认）

Plan → 自动逐个执行 → 汇总回答

### 7.2 每步确认

Plan → 展示计划给用户 → 用户确认/修改 → 逐个执行 → 汇总

用户输入 "先让我看看计划" 或 "确认一下"，进入确认模式。

### 7.3 单步执行

用户可以中途说 "停，先执行到第3步" 或 "跳过第2步"。

### 7.4 步骤失败策略

某步骤执行失败时，按以下策略处理：

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| **重试**（默认） | 重新执行当前步骤，最多 3 次 | 网络抖动、临时错误 |
| **跳过** | 标记为 skipped，继续下一步 | 非关键步骤 |
| **中止** | 停止执行，plan status → failed | 关键步骤失败 |
| **重新规划** | 带着失败信息重新调 Planner | 步骤根本不可行 |

默认行为：**重试 3 次 → 仍失败则中止**。

用户可通过配置覆盖：
```yaml
planning:
  on_step_failure: "retry"    # retry / skip / abort / replan
  max_retries: 3
```

### 7.5 用户中断机制

用户在执行过程中发消息可以打断：

| 用户说 | 行为 |
|--------|------|
| "停" / "暂停" | 完成当前步骤后暂停，等待用户指令 |
| "取消" / "不要了" | 立即取消，plan status → cancelled |
| "跳过这一步" | 当前步骤标为 skipped，继续下一步 |
| "重新规划" | 用已有结果重新调 Planner |

**正在执行的步骤怎么处理**：
- shell 命令正在跑：subprocess 有 timeout，等它超时或完成
- 文件读写：原子操作，不存在"写到一半被打断"
- 网络请求：httpx 有 timeout，等它超时

不主动 kill 正在执行的命令（安全考虑：kill 可能导致状态不一致）。

---

## 8. 工具接口适配

当前 Lampson 工具已经有统一 schema（在 `src/tools/*.py` 中定义的 `SCHEMA` 常量）。

规划器直接复用 `tools.py` 的 `TOOL_REGISTRY`，不需要单独维护一套工具描述：

```python
# planner.py 里直接引用
from src.core.tools import TOOL_REGISTRY

tool_schemas = [info["schema"] for info in TOOL_REGISTRY.values()]
```

当前工具 schema：

| 工具 | 文件 | 说明 |
|------|------|------|
| shell | tools/shell.py | Shell 命令执行，已有 timeout |
| file_read | tools/fileops.py | 文件读取，100KB 限制 |
| file_write | tools/fileops.py | 文件写入 |
| web_search | tools/web.py | DuckDuckGo 搜索 |
| feishu_send | feishu/client.py | 发飞书消息 |
| feishu_read | feishu/client.py | 读飞书消息 |

**注意**：文档早期版本的 shell 工具有 `machine` 参数，这是设计阶段的设想，实际代码中没有。远程机器操作通过 `shell("ssh user@host 'command'")` 实现，不需要单独的 machine 参数。

---

## 9. 多轮对话中的规划状态

规划状态保存在 `Agent` 里：

```python
class Agent:
    # ...现有字段...

    # 规划状态
    current_plan: Plan | None = None   # 当前计划（含状态机）
```

规划模式下，用户每次输入：
- 如果 `current_plan.status == executing`：继续执行下一步或重新规划
- 如果 `current_plan.status == created`：等待用户确认
- 如果 `current_plan` 为 None：创建新规划

### 9.1 与 Context Compaction 的协调

规划执行期间，对话历史仍在增长，可能触发 compaction。需要协调：

**规则：plan.status == executing 时不触发 compaction**

理由：
- 规划执行期间每步的 observation 是下一步的输入（`$prev.result`）
- 如果中途压缩，会丢失关键中间结果
- 规划执行通常不超过 10 步，token 增长可控

实现方式：在 `apply_compaction` 的触发检查中加一个条件：

```python
# cli.py / listener.py 中
if agent.current_plan and agent.current_plan.status == PlanStatus.executing:
    return  # 跳过压缩
```

**规划完成后**：plan status 变为 completed/failed/cancelled，此时正常触发 compaction。压缩时把 plan 的最终结果纳入 summary。

**plan 状态本身也需要写入 compaction summary**：

```
## 压缩摘要
...
## 最近完成的任务
目标：在机器40上找模型平台工程
结果：找到 /home/user/model-platform/，已完成
```

这样压缩后 LLM 还知道"刚才做了什么"。

---

## 10. 实施步骤

**Phase 1（MVP）**：最小可用的单次规划
1. 新建 `src/planning/` 模块（planner.py + steps.py + prompts.py + executor.py）
2. 实现 `Step` / `Plan` / `PlanStatus` 数据类
3. 实现 `Planner.plan()` — 调一次 LLM 生成步骤列表（含参数引用语法）
4. 实现 `Executor._resolve_args()` — 解析 `$prev.result` / `$step[N].result`
5. 实现 `Executor.execute()` — 遍历 steps → 调 `tools.dispatch()` → 拿结果 → 解析下一步参数
6. 实现步骤失败处理 — 默认重试 3 次 → 中止
7. 改造 `Agent.run()` — 统一走 Planner，1-step 退化直接执行
8. `apply_compaction` 加 plan 状态检查 — executing 时不压缩

**Phase 2**：增强
1. 加每步确认模式
2. 加 replan 能力（步骤失败时带着错误信息重新规划）
3. 加步骤状态展示（进度条 / 当前步骤高亮）
4. 用户中断机制（暂停/取消/跳过）

**Phase 3**：高级
1. 步骤并行执行（不依赖结果的步骤可以同时跑）
2. 步骤缓存（同样的步骤不用重新执行）
3. Plan 用小模型（如 glm-4-flash），Execute 用主模型（需多模型配置支持）

---

## 11. 与现有压缩设计的关系

压缩解决的是 **context 太长怎么办**。
规划解决的是 **任务太复杂怎么办**。

两个是正交的设计：
- 复杂任务 + context 没超 → 正常规划执行
- 复杂任务 + context 超了 → 规划 + 压缩同时工作（压缩后上下文变少，规划更准确）

---

## 12. 风险

1. **规划质量依赖 LLM**：如果 LLM 规划能力弱，步骤会不完整或顺序错误。Solution：加验证，检查步骤合理性（如 action 是否在工具列表中、args 是否符合 schema）。
2. **两次 LLM 调用贵**：Plan + Execute 两次 token 消耗。Solution：Phase 1 统一用主模型，Phase 3 引入小模型做规划。
3. **规划结果不稳定**：同样目标两次规划结果不同。Solution：加 step stable hash，结果不一致时提示用户。
4. **参数引用解析失败**：`$prev.result` 引用的步骤结果为空或格式不符预期。Solution：解析失败时中止当前步骤，error 记录具体原因，用户可选择 replan。
5. **统一走规划器的开销**：即使闲聊也会走一次 plan LLM 调用。Solution：1-step plan 的 LLM 调用成本很低（约 100 token），相比维护两套执行逻辑的复杂度，值得。
6. **长计划的中途上下文膨胀**：10 步计划每步 observation 可能让 context 超限。Solution：Phase 1 不处理（10 步以内可控），Phase 2 加 observation 截断（每步结果只保留前 500 字）。
