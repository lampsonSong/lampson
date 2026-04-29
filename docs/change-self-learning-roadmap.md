# Lampson 自主学习模块规划

> 基于项目审查（2026-04-29），按投入产出比排序

## 优先级 1：用户反馈学习（Feedback Learning）

**现状**：`reflection.py` 只在任务完成后自动触发，用户说"不对，应该是xxx"时没有结构化处理。

**目标**：识别用户纠正 → 自动修正已记录的 skill/project/memory，而不只是追加。

**计划**：
- [ ] 在 `agent.py` 的 `_run_tool_loop` 末尾增加"用户反馈检测"：检查最近一轮 user→assistant 是否包含纠正信号（关键词匹配 + LLM 判断）
- [ ] 新增 `src/core/feedback.py`：
  - `detect_correction(user_msg, assistant_msg) → Correction | None`
  - `apply_correction(correction) → list[str]`：自动修正对应的 skill/project/memory 文件
- [ ] 反馈类型分类：事实纠正（改 memory）、流程纠正（改 skill）、项目信息纠正（改 project）
- [ ] 修正前备份原文，修正后通知用户确认

**预估工作量**：2-3 天

---

## 优先级 2：自我评估与指标追踪（Self-Evaluation）

**现状**：没有任何任务成功/失败率的统计。

**目标**：为所有学习模块提供数据基础。

**计划**：
- [ ] 新增 `src/core/metrics.py`：
  - 每轮任务记录：耗时、工具调用数、模型名、是否成功、是否被用户纠正、compaction 次数
  - JSONL 格式写入 `~/.lampson/metrics.jsonl`
- [ ] 在 `agent.py` 的 `run()` 和 `_run_tool_loop()` 末尾埋点
- [ ] 新增 `/metrics` 命令：展示最近 N 轮的统计摘要
- [ ] 后续扩展：定期分析指标，识别薄弱环节，自动调整策略

**预估工作量**：1 天

---

## 优先级 3：中断抢占修复（Interrupt Preemption Fix）

**现状**：中断抢占机制已实现但无法生效——`_handle_message` 同步阻塞在 WebSocket 事件循环线程中，新消息根本无法进入回调。

**目标**：新消息到来时能打断正在执行的任务，立即切换到新消息处理。

**计划**：
- [ ] 将 `_handle_message` 中的耗时处理（`session.handle_input`）提交到线程池，立即释放 WebSocket 事件循环线程
- [ ] 确保第二条消息进入 `_handle_message` 时能走到 `request_interrupt()` 分支
- [ ] 验证中断→恢复→继续原任务的完整流程

**预估工作量**：0.5 天（核心改动在 `listener.py` 的线程池化）

---

## 优先级 4：知识关联（Knowledge Cross-Reference）

**现状**：skill 和 project 是独立文件，彼此无关联。

**目标**：skill 引用 project、project 依赖 project、skill 覆盖 skill 的关系图。

**计划**：
- [ ] 在 skill 和 project 的 frontmatter 中增加 `related_skills`、`related_projects` 字段
- [ ] `reflection.py` 生成 knowledge 时自动提取关联
- [ ] `skills/manager.py` 查询时支持沿关联链展开
- [ ] compaction 归档时维护关联而非各存各的

**预估工作量**：3-5 天

---

## 优先级 5：长期记忆蒸馏（Memory Distillation）

**现状**：`core.md` 更新逻辑粗糙，LLM 一次性摘要。

**目标**：按重要性评分、衰减机制、定期强化。

**计划**：
- [ ] 给 memory 条目增加 `importance` 评分（0-10）
- [ ] 每次 compaction 或 session 结束时重新评估重要性
- [ ] `core.md` 只保留 importance > 阈值的条目
- [ ] 定期"强化"：被多次引用的条目 importance 自动提升

**预估工作量**：3-5 天

---

## 优先级 6：元学习（Meta-Learning）

**现状**：没有对自身行为模式的分析。

**目标**：记录"我在哪类任务上容易失败"、"哪个模型擅长什么"，动态调整策略。

**计划**：
- [ ] 基于优先级 2 的 metrics 数据，定期分析模式
- [ ] 新增 `src/core/meta_learner.py`：从 metrics 中提取规律
- [ ] 输出策略调整建议：自动选模型、调整规划粒度、优化工具调用顺序
- [ ] 策略写入 config 或 memory，下一轮自动生效

**预估工作量**：5-7 天

---

## 优先级 7：主动探索（Proactive Exploration）

**现状**：所有行动都是被动的，用户问才做。

**目标**：空闲时主动探索环境，积累背景知识。

**计划**：
- [ ] 新增 `src/core/explorer.py`：空闲时扫描新文件、检查服务状态、更新 project 索引
- [ ] 探索结果写入 memory/project，不主动打扰用户
- [ ] 探索触发条件：连续 N 分钟无消息 + 有已注册的 project
- [ ] 可通过配置开关

**预估工作量**：3-5 天

---

## 执行顺序

1. **中断抢占修复**（0.5天）→ 立即可用，解决已知的 bug
2. **用户反馈学习**（2-3天）→ 日常交互质量提升最大
3. **自我评估指标**（1天）→ 为后续模块铺路
4. 知识关联 → 长期记忆蒸馏 → 元学习 → 主动探索（按需推进）
