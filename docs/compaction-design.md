# Context Compaction Design

## Overview

传统的上下文压缩方案（如 Hermes 的两阶段压缩、pi-coding-agent 的单阶段摘要）都是从**消息列表**角度出发，本质上是"切掉老的，保留新的"。这种方案的问题在于：

- 最早的消息可能包含重要上下文（决策、约束、项目背景），但被优先切掉
- 最新消息不一定是最重要的，可能只是中间过程
- 没有"把内容写进记忆文件"的概念，压缩完了上下文还是那些消息的变体
- 压缩是"丢弃"而不是"归档"

本文提出一种新的压缩思路：**记忆归档压缩**（Memory Archival Compaction）。核心思想是把"对话"和"记忆"彻底分开——对话是工作区，skill/project 是持久存储。压缩不是切掉，是**归档 + 摘要**。

---

## Design Principles

1. **对话即工作区，记忆即持久存储**。对话窗口只装当前问题，长期上下文都进 skill/project 文件。
2. **归档优于丢弃**。相关内容应写入对应 skill/project，而不是直接删除。
3. **压缩可迭代**。一轮压缩后如果还未达标，允许继续压缩直到达标。
4. **可配置性**。触发阈值、结束阈值、每轮压缩比例都由调用方配置。

---

## Terminology

- **Compaction**：压缩。当对话 token 超过阈值时，触发压缩流程。
- **Archive**：归档。把对话中的有价值内容写入 skill/project 文件。
- **Summarize**：摘要。把剩余内容（当前问题的核心）生成结构化摘要。
- **Checkpoint**：检查点。压缩完成后，对话窗口被重置为只含摘要和关键上下文的状态。

---

## Configuration

```typescript
interface CompactionConfig {
  // 触发阈值（百分比），超过此比例时触发压缩
  // 默认 80%
  triggerThresholdPercent: number;

  // 压缩结束阈值（百分比），压缩后 token 降到此比例以下则结束
  // 默认 30%
  endThresholdPercent: number;

  // 每轮压缩的目标比例（相对于压缩前）
  // 默认 0.5（即每轮压缩到 50%）
  // 最终结束阈值由 endThresholdPercent 保证，此参数控制每轮步进
  roundTargetRatio: number;

  // 最大迭代次数，防止无限循环
  maxIterations: number;

  // 是否启用归档步骤（写 skill/project）
  enableArchive: boolean;
}
```

**stopReason 触发规则**：只有 `end_turn`（正常回复完毕）和 `aborted`（用户中断）才触发压缩。工具调用执行中、stop_sequence 等其他情况不压缩，必须等本轮回答真正结束。

---

## Compression Lifecycle

### 1. 触发条件检查

每次 LLM 回复结束后（`stopReason` 已知）检查：

```
contextTokens > contextWindow × triggerThresholdPercent
&& stopReason in ('end_turn', 'aborted')
```

- `end_turn`：正常回复完毕 → 触发压缩
- `aborted`：用户中断执行 → 触发压缩
- 其他 stopReason 不触发压缩，必须等本轮真正结束

---

### 2. 归档阶段（Archive Phase）

**目的**：把对话中有长期价值的内容写进 skill/project 文件，从当前对话窗口中删除。

**步骤**：

#### 2.1 确定当前问题

从对话中提取当前在解决的问题：

```
当前问题是什么？（用一句话描述）
相关项目：<project_name>
相关技能：<skill_name>
```

这个分类由 LLM 完成。

#### 2.2 读取已有 skill/project 内容

根据 2.1 识别出的相关 project 和 skill，读取对应文件的已有内容。

#### 2.3 遍历所有消息，重新整理

对每条消息判断：

| 类型 | 处理方式 |
|------|---------|
| 打招呼/寒暄（如"你好"、"Hi"） | **丢弃**，不归档 |
| 和当前问题无关但有长期价值 | 写进对应 skill 或 project 文件 |
| 已在 skill/project 中记录过的内容 | **跳过**，不重复写 |
| 工具调用结果 | 归档到对应 project 或 skill |
| 当前问题的核心上下文 | **保留**，进入摘要阶段 |

#### 2.4 重新写入 skill/project

**不是简单的 append，而是重新整理**。流程：

```
已有 skill/project 内容 + 当前对话新增内容
  → LLM 重新整合
  → 写回 skill/project 文件
```

整合策略：
- **合并**：新内容和已有内容属于同一主题 → 合并成更完整的条目
- **更新**：已有条目过时了 → 用新内容覆盖
- **淘汰**：已有内容已失效或被替代 → 删除
- **追加**：纯粹的新知识 → 追加到文件尾部

Skill 文件：`~/.lampson/skills/<skill_name>.md`
Project 文件：`~/.lampson/projects/<project_name>.md`

---

### 3. 摘要阶段（Summarize Phase）

**目的**：把剩余内容（当前问题的核心）生成结构化摘要，重置对话窗口。

**输入**：归档完成后，剩余在对话中的消息。

**输出**：结构化摘要，写入压缩记录。

```markdown
## 问题
<一句话描述>

## 约束
- [约束1]
- [约束2]

## 进度
### 已完成
- [x] ...

### 进行中
- [ ] ...

### 阻塞
- ...

## 关键决策
- **[决策]**: <理由>

## 待处理
- ...

## 关键文件/路径
- ...
```

---

### 4. 迭代检查

压缩完成后检查：

```
compressedTokens < originalTokens × endThresholdPercent
```

- **达标**：压缩结束，摘要+归档内容替代原始对话。
- **未达标**：继续一轮（再次归档 + 摘要），直到达标或达到 `maxIterations`。

---

## Skill/Project 文件结构

Skill 和 Project 文件支持追加压缩归档条目：

```markdown
# Skill: <skill_name>

## 记忆归档

### [2026-04-25 14:30] 当前问题：优化认证流程
- 用户要求实现 OAuth 2.0 认证
- 偏好 JWT token，有效期 24h
- 已在 auth/ 模块定义了相关接口

### [2026-04-24 09:15] 当前问题：添加日志系统
- 采用结构化日志，JSON 格式
- 错误级别分四级：DEBUG/INFO/WARN/ERROR
- 日志输出到 /var/log/app/

---
```

Project 文件同理，换成 `## 项目: <project_name>`。

**归档规则**：
- LLM 读取已有内容，结合当前对话新增内容一起重新整合
- 同一主题的条目合并，新内容覆盖旧内容，过时内容淘汰
- 纯粹的新知识才追加，不重复写已记录的内容
- 相关条目通过"当前问题"关键字串联
- **append-only 不成立**——必须支持合并、更新、淘汰操作

---

## 压缩流程图

```
LLM 回复结束
     │
     ▼
contextTokens > window × triggerThreshold?
│  否 → 不压缩，正常继续
│  是 → stopReason in ('end_turn', 'aborted')?
│         否 → 不压缩，继续（等到下一个结束点）
│         是 → 进入压缩流程
     │
     ▼
┌─ Archive Phase ─────────────────────────┐
│  1. 确定当前问题（LLM 分类）            │
│  2. 读取已有 skill/project 内容         │
│  3. 遍历消息：归档/丢弃/保留            │
│  4. 重新整合并写回 skill/project        │
└─────────────────────────────────────────┘
     │
     ▼
┌─ Summarize Phase ───────────────────────┐
│  对话中剩余内容 → 生成结构化摘要        │
└─────────────────────────────────────────┘
     │
     ▼
compressedTokens < originalTokens × endThreshold?
│  是 → 完成，重置对话窗口为摘要
│  否 && iteration < maxIterations
│         → 继续下一轮压缩（回到 Archive）
│  否 && iteration >= maxIterations
│         → 结束，警告日志（未达标）
```

---

## 失败处理

| 场景 | 处理 |
|------|------|
| LLM 分类失败 | 记录错误，跳过 Archive，继续 Summarize |
| Skill/Project 读写失败 | 写日志，保留内容在对话中，下轮再试 |
| Summarize LLM 调用失败 | 抛异常，压缩中止，对话不变 |
| maxIterations 达仍未达标 | 结束压缩，发送警告给用户 |
| 磁盘空间不足 | 抛异常，中止压缩 |

---

## 与现有系统的区别

| | Memory Archival Compaction | Hermes | pi-coding-agent |
|--|--|--|--|
| 思路 | 归档 + 摘要 | 截断 + 两阶段摘要 | 截断 + 单阶段摘要 |
| 长期上下文 | 写进 skill/project | 丢弃 | 丢弃 |
| 迭代 | 可多轮直到达标 | 不可迭代 | 不可迭代 |
| 触发条件 | 阈值 + stopReason 检查 | 阈值（无 stopReason） | 阈值（无 stopReason） |
| 压缩终点 | `endThresholdPercent` 保障 | 固定比例 | 固定比例 |
| 寒暄处理 | 丢弃 | 保留 | 保留 |

---

## 待办

- [ ] 确定 skill/project 的追加写入格式（支持合并/更新/淘汰）
- [ ] 设计 LLM 分类 prompt（提取"当前问题"）
- [ ] 设计归档决策 prompt（判断内容写进哪个 skill/project）
- [ ] 实现 archive/summarize 流程
- [ ] 实现迭代检查循环
- [ ] 单元测试覆盖率