# TaskQueue 后台任务架构设计

## 目标

Lampson 从单任务模型扩展为多任务并行模型：支持后台任务、任务可排队、探索作为主任务的一部分不拆分。

## 任务类型

| 类型 | 说明 | 可并行数 |
|------|------|----------|
| foreground | 前台主任务，一次只跑一个 | 1 |
| background_user | 用户主动要求放后台的任务 | ≤2 |
| background_agent | agent 执行时自己拆出来的子任务 | ≤2 |

**探索不是独立任务**：探索是 foreground 内部的一个阶段，用户看到的是"主任务跑一半遇到了问题，正在解决问题"。探索不占后台任务配额，不进 TaskQueue。

## Task 数据结构

```python
class TaskStatus(Enum):
    pending = "pending"
    running = "running"
    waiting_exploration = "waiting_exploration"  # 主任务等探索
    interrupted = "interrupted"  # 被打断（daemon 重启或用户停止）
    failed = "failed"
    done = "done"

class ChannelInfo:
    type: "feishu" | "cli"
    feishu_chat_id: str | None
    cli_session_id: str | None
    is_active: bool  # 当前是否在线

class ExplorationResult:
    diagnosis: str          # 根因诊断
    fix_plan: str            # 修复方案描述
    resume_from_step: int | None  # 从第几步继续（None=重新规划）
    attempts: list[dict]     # 尝试过的方案及结果

class Task:
    task_id: str
    type: TaskType
    goal: str
    status: TaskStatus
    channels: list[ChannelInfo]  # 支持多渠道
    context_snapshot_path: Path | None  # 存独立文件，不塞 JSONL
    exploration_snapshot_path: Path | None  # 探索开始前的快照（记录探索进展）
    exploration_result: ExplorationResult | None
    result_cache_path: Path | None
    pushed_channels: list[str] = []  # 已推送过的渠道（幂等用）
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    interrupted_at: datetime | None
```

## TaskQueue 状态机

```
pending → running → waiting_exploration → running → done
                   └→ failed
                   
任何状态 → interrupted（daemon 重启 或 用户主动停止）
interrupted → pending（用户决定重跑）
```

## 队列规则

1. **最多同时 2 个后台任务**（background_user + background_agent 共享配额）
2. **第 3 个进来排队**，FIFO，不拒绝
3. **无优先级**，先到先排队
4. **exploration 不占配额**，作为 foreground 内部阶段

## 探索任务流程（用户感知是主任务在探索）

```
Task A（前台）running
    ↓ Tool 连续失败 2 次
Task A 状态 → waiting_exploration
保存 context_snapshot 到独立文件
保存 exploration_snapshot（探索前的快照）
开始探索阶段（不进队列，不占后台配额）

用户看到：
  "🔧 正在探索解决方案..."
  "  - 正在分析错误原因..."
  "  - 尝试方案 A（chmod 600）..."
  "  - 方案 A 失败，尝试方案 B..."
    ↓
探索完成（最多 5 分钟）
    ↓
超时 → 主任务标记 interrupted（而非 failed），用户可选择继续探索
    ↓
成功 → Task A 恢复执行，从 context_snapshot_path 恢复上下文
           基于 exploration_result 决定：
           - 原方案参数错 → 从 resume_from_step 继续
           - 原方案不行 → 重新规划
失败 → Task A 标记 failed → 告知用户探索失败原因
```

**LLM 并发限制**：exploration 与后台任务共享 LLM 并发上限（如 3 路），防止打爆 API。

## 心跳机制

所有长时间运行的任务（包括前台任务、后台任务、探索阶段）都需要心跳进度通知，让用户感知任务在正常进行。

### 心跳触发时机

- 前台任务：每完成一个步骤 / 每隔 30 秒
- 探索阶段：每个子步骤（分析、尝试方案、验证结果）
- 后台任务：每完成一个里程碑 / 每隔 60 秒

### 心跳消息格式

```
[⏳ 任务进行中]
步骤 2/5：正在读取项目文件...
预计剩余：约 30 秒
```

```
[🔧 探索中]
- 正在分析错误原因...
- 尝试方案 A（chmod 600）... → 失败
- 尝试方案 B（sudo chown）... → 进行中
```

### 实现方式

- 复用现有飞书卡片更新机制
- 每个任务维护自己的进度卡片 card_id
- 进度变化时 patch 更新，而非重复发送

## 打断处理

- **用户主动停止**（Ctrl+C 或飞书发"停止"）→ 走打断逻辑，同 interrupted
- **daemon 重启** → 所有 running/pending 任务标记 interrupted
- **exploration 超时**（5 分钟）→ 主任务标记 interrupted（不是 failed），用户可选择继续探索

## 结果返回机制

### 实时返回（用户在场）

- 前台风台任务：直接响应
- 后台任务：完成时发结果卡片

### 缓存返回（用户不在场）

- 任务完成后，如果用户不在渠道，结果写入缓存文件
- 缓存文件路径：`~/.lampson/task_cache/<task_id>.json`
- **7 天过期**，清理时删除 7 天前的缓存文件
- 用户下次回来 → 读所有缓存 → 推送到所有活跃渠道（推送前重新检查 is_active）→ 每个渠道推送后标记到 pushed_channels → 全部推送完才清理缓存

**推送失败的兜底**：单渠道推送 3 次仍失败后，标记为"永久推送失败"，不再阻塞缓存清理。

缓存文件结构：
```json
{
  "task_id": "...",
  "goal": "...",
  "result": "...",
  "status": "done",
  "completed_at": "2026-04-29T21:00:00"
}
```

### 多端同时在线（幂等）

- 推送前检查 `is_active`，用最新状态而非写入时的状态
- 推送前检查 pushed_channels，未推送过的才推
- 全部渠道推送完才清理缓存文件

## 任务去重

- 用户提交新任务时，LLM 判断是否与 pending/running/interrupted 任务重复
- 如果判断为重复，跳过创建，**告知用户去重结果**（"检测到重复任务，已跳过"或"未检测到重复"）
- 不做文本完全匹配，用 LLM 语义判断

## 任务取消

用户显式说"取消 XXX 任务"时：

- pending 任务：直接移除
- running 任务：发送停止信号，取消 LLM 调用和工具执行
- waiting_exploration 任务：同时取消探索阶段
- interrupted 任务：移除

**副作用说明**：任务取消时已产生的副作用（写了文件、发了请求）无回滚机制，不在本文讨论范围。

## 清理机制

以下内容 7 天后清理：

- `~/.lampson/task_cache/` 缓存文件
- `~/.lampson/snapshots/` context_snapshot 和 exploration_snapshot 文件

daemon 启动时执行一次清理。

## TaskQueue 持久化

- TaskQueue 持久化到 `~/.lampson/task_queue.jsonl`
- 每条记录一个 Task，append 模式
- JSONL 写入加进程内锁（asyncio.Lock）
- daemon 启动时：
  1. 执行清理（7 天前缓存 + snapshots）
  2. 所有 running/pending 任务标记为 `interrupted`，记录 `interrupted_at`
  3. 向所有渠道推送 interrupted 任务通知（多个任务合并通知，防通知风暴）
  4. 定期 compaction（只保留 pending/interrupted 的任务，done/failed 清理）

## 已知限制

- **interrupted 任务重跑**：适用于幂等任务或纯计算任务。不适用于有副作用的任务（修改线上数据、创建外部资源等），因为外部状态可能已变化。
- **任务取消**：已产生的副作用无回滚机制。
- **exploration 超时**：超时后 exploration 上下文可能丢失，超时过长（>10 分钟）建议用户放弃探索重新开始。

## 不在本文讨论范围

- 具体编码实现细节
- Task 内步骤（Step）的执行逻辑
- 探索任务的具体探索策略
