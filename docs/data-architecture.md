# Lampson 数据架构设计

## 核心原则

**两层分离：通用 vs 具体**
- `MEMORY.md` 和 `USER.md` 只放通用的、长期有效的规则和信息
- 具体的、领域相关的信息全部下沉到 `skills/` 和 `projects/`

**用户隔离**
- `USER.md` 是多用户的基础，按用户切换文件即可完成身份转换
- `MEMORY.md` 是 agent 自身的人格，与用户无关，所有用户共享

---

## 文件定义

### MEMORY.md — Agent 人格与行为准则

**位置**：`~/.lampson/MEMORY.md`  
**加载**：每次 session 都注入（L3）  
**限制**：500 字符以内  
**谁写的**：人工维护，不自动生成

**存什么**：
- Agent 名字、性格描述
- 通用行为红线（危险操作需确认、不骂人、不绕圈子、诚实回答）
- 跨项目的通用工作规范

**不存什么**：
- ❌ 用户偏好（放 USER.md）
- ❌ 具体工具用法（放 skills）
- ❌ 机器信息、IP、端口（放 projects）
- ❌ 任务进度、临时状态（不记）

**示例**：
```
名字：Lampson
性格：稳定、简洁、不折腾。先想后做，结果说话。

行为准则：
- 危险操作（rm -rf、删库、强制推送、部署生产）必须先确认
- 不对外发布内容除非用户明确授权
- 不猜测不确定的事，宁可说不知道
- 保护用户隐私，不在群聊中透露用户信息
```

---

### USER.md — 用户画像与偏好

**位置**：`~/.lampson/USER.md`  
**加载**：每次 session 都注入（L1.5）  
**限制**：500 字符以内  
**谁写的**：人工维护为主，agent 可按用户指令追加

**存什么**：
- 用户基本信息（称呼、联系方式）
- 沟通偏好（简洁、不废话、不用表情）
- 自主权范围（哪些小事可以自主决定）
- 渠道信息（飞书 chat_id 等）

**不存什么**：
- ❌ agent 行为准则（放 MEMORY.md）
- ❌ 具体项目信息（放 projects）
- ❌ 工具密码、账号（放 skills）

**示例**：
```
称呼：哥哥
飞书 chat_id: oc_xxx

偏好：
- 回复简洁，不废话，不发表情
- 小事自主决定（git commit message 自己生成、常规操作做完汇报）
- 任务风格：先出方案再动手
- 飞书消息用表格格式
```

---

### skills/ — 可复用工作流与工具信息

**位置**：`~/.lampson/skills/<name>/SKILL.md`  
**加载**：按需加载（触发词匹配时）  
**谁写的**：agent 从复杂任务中提炼

**存什么**：
- 有步骤的工作流（debug 流程、代码审查流程）
- 工具性质的信息（VPN 脚本、SSH 别名、常用账号密码）
- 特定场景的操作指南

**不存什么**：
- ❌ 纯行为偏好（放 USER.md）
- ❌ 项目维度的信息（放 projects）
- ❌ 单次任务记录（放 session 日志）

**现有 skills**：
| 技能 | 类型 |
|---|---|
| debug | 工作流 |
| code-writing | 工作流 |
| reverse-tracking | 工作流 |
| cursor-agent | 工作流 |
| hermes-delegate | 工作流 |
| feishu-format | 工具信息 |

**待迁移**：
- 机器名-IP 映射、SSH 配置 → ✅ 已迁入 skills/machines/SKILL.md
- VPN 脚本、常用密码/用户名 → 新建 skill

---

### projects/ — 项目信息

**位置**：`~/.lampson/projects/<name>.md`  
**加载**：按需加载（project_context 工具）  
**谁写的**：agent 维护 + 人工修正

**存什么**：
- 项目路径、技术栈、端口
- 项目架构描述
- 已知 bug 与解决方案
- 项目特定的约束和约定

**不存什么**：
- ❌ 跨项目通用的东西（放 MEMORY/USER/skills）
- ❌ agent 行为准则（放 MEMORY.md）
- ❌ 用户偏好（放 USER.md）

**现有 projects**：
| 项目 | 内容 |
|---|---|
| lampson | 自身项目信息 |
| hermes | Hermes Agent 信息 |
| machines | 机器 SSH 别名与 IP |
| model-platform | 模型平台信息 |

---

## 加载层级

| 层 | 文件 | 加载时机 | 说明 |
|---|---|---|---|
| L1 | MEMORY.md | 每次 | Agent 人格与行为准则 |
| L1.5 | USER.md | 每次 | 用户画像与偏好 |
| L2 | Skills 索引 | 每次 | 扫描 skills/*.md，触发词索引 |
| L3 | Projects 索引 | 每次 | 动态扫描 projects/*.md 生成项目列表 |
| L4 | Model Guidance | 每次 | GLM tool_calls 等模型适配 |
| L5 | Channel Context | 非 CLI 时 | 消息来源标识 |

按需加载（工具调用）：
- `skill(action='view', name='xxx')` → 加载完整 SKILL.md
- `project_context(name='xxx')` → 加载完整 project md

---

## 需要清理的文件

| 文件 | 处理 |
|---|---|
| `~/.lampson/SOUL.md` | 内容合并到 MEMORY.md，删除 |
| `~/.lampson/AGENTS.md` | 删除（无代码加载） |
| `~/.lampson/memory/core.md` | 已删除 |
| `~/lampson/core.md` | 已删除 |
| `~/.lampson/MEMORY.md` | 重写为 agent 人格 |
| `~/.lampson/USER.md` | 重写为纯用户偏好 |

---

## 判断流程：新信息该放哪

```
新信息
├── 跟 agent 怎么做事有关？ → MEMORY.md
├── 跟用户是谁、喜欢什么有关？ → USER.md
├── 是有步骤的工作流？ → skills/
├── 是工具/账号/配置信息？ → skills/
├── 跟某个项目有关？ → projects/<name>.md
└── 是临时状态/任务进度？ → 不记，用 session 搜索
```
