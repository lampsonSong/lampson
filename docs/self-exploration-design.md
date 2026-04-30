# 主动探索能力设计方案

## 目标

当 LLM 在 tool loop 中反复尝试同一任务失败时，介入探索：
收集上下文、复现错误、尝试多种方案、记录全过程。

## 触发条件

**同一工具 + 相似错误信息连续失败 2+ 次**才触发。

不能只看工具名，要加上错误信息相似度判断（避免一次 ls 失败、一次 rm 失败也被当成"同一问题"）。

## 任务栈设计

```
TaskStack
├── 主任务帧（挂起）
│   ├── goal: str
│   ├── plan_snapshot: Plan | None
│   ├── messages_index: int  # 探索开始时的 messages 长度
│   └── tool_fail_count: dict  # {tool_name: [(error, count), ...]}
└── 探索完成后 → 截断 messages 到 index → 注入探索总结 → 重新规划
```

**messages 处理**：不深拷贝 messages，而是记录探索开始时的长度。探索结束后截断到该长度，注入探索总结。这样避免内存和序列化成本。

**PlanStatus**：需要新增 `suspended` 状态，挂起时 plan 状态机需要兼容。

## 探索流程

```
触发探索
    ↓
[挂起] 主任务压栈，记录 messages_index，plan 标记 suspended
    ↓
[探索] 用独立 LLM 实例（clone_for_inference），不走主 messages
    ↓
LLM 分析根因，生成最多 5 个候选方案
    ↓
[尝试] 依次执行每个方案（探索内禁止再触发探索，栈深度 = 1）
    ↓
    成功 → 记录到 skills（走现有 reflection，不另起炉灶）→ 弹栈 → 截断 messages → 注入总结 → 重新规划
    失败 → 记录到 failure_pattern → 弹栈 → 截断 messages → 注入总结 → 告知用户
```

## 探索进度显示

探索期间同步发送进度通知，用户能感知探索正在进行。

### 飞书卡片格式

每个方案尝试前后，发送一张飞书卡片更新进度：

```
[🔧 探索中] 正在尝试第 2/5 个方案
方案: chmod 600 ~/.ssh/id_rsa
状态: ✓ 成功 / ✗ 失败
```

探索完成时，发送总结卡片：

```
[✅ 探索完成] 找到可行方案
方案: chmod 600 ~/.ssh/id_rsa
总结: SSH key 权限问题，通过 chmod 解决

[❌ 探索完成] 所有方案均失败
已尝试: 5 个方案
结论: 需要用户手动处理 SSH key 权限问题
```

### 实现方式

- 复用现有 `feishu_send()` 工具，通过独立 LLM 实例内部调用
- 不依赖 `progress_callback`（那是主 tool loop 的）
- 探索器初始化时注入 feishu_client reference

## 探索执行规则

- 用独立 LLM 实例，不走主 messages（参考 skills/manager.py consolidation 的做法）
- 探索内禁止再触发探索（栈深度硬限制 = 1）
- 总超时：5 分钟，防止卡住
- 探索过程中可被用户新消息中断（复用现有 request_interrupt 机制）
- 探索方案应尽量只读；如有写操作，记录变更日志供恢复参考

## 中断处理

探索中被新消息中断 →
1. 保存当前探索进度（已尝试 N 个方案）
2. 弹出探索帧，保留主任务帧
3. 处理用户新消息
4. 用户消息处理完后，自动继续原探索（不询问用户）

## 记录机制

### 成功 → skills

探索成功后不走独立写入，而是触发现有 `_maybe_reflect()` 反思机制，让反思系统判断是否沉淀为 skill。

### 失败 → failure_pattern

存储位置：`~/.lampson/failure_patterns/`

```json
{
  "id": "hash(error_pattern + tool)",
  "tool": "shell",
  "error_pattern": "Permission denied.*\\.ssh",
  "context_keywords": ["ssh", "key", "permission"],
  "tried_solutions": [
    {"solution": "chmod 600", "result": "failed"}
  ],
  "conclusion": "需要用户手动修复权限",
  "created_at": "2026-04-29T21:00:00",
  "last_matched_at": "2026-04-29T21:00:00",
  "invocation_count": 1
}
```

**匹配时机**：
1. 探索触发前，先查 failure_pattern。有匹配时跳过探索，直接告知用户已知死路。
2. 探索生成候选方案时，把 failure_pattern 注入 context，让 LLM 排除已知死路。

**清理策略**：
- invocation_count：被匹配次数
- last_matched_at：最后匹配时间
- 超过 30 天未匹配的 pattern 定期清理

## 约束

- 最多 5 个候选方案
- 总超时 5 分钟
- 探索失败不超过 3 轮（防止死循环）
- 栈深度 = 1（探索内禁止再探索）
- 不增强 dispatch() 错误信息
