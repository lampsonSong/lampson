# Lampson Skills 系统设计文档

## 1. 概述

Skills 是 lampson 的**可复用操作技能**——把"怎么做某类事情"的方法论从 prompt 硬编码中提取出来，变成独立的、可按需加载的知识单元。

### 设计原则

| 原则 | 说明 |
|------|------|
| **按需加载** | system prompt 只注入 skill 名称和一句话描述，全文通过 `skill_view(name)` 加载 |
| **教方法论不塞答案** | skill 教的是通用操作方法（如 which→head→ls 反向追踪），不是具体路径 |
| **可迭代** | skill 可以由 lampson 自己在使用中发现问题后更新 |
| **与 memory 分离** | memory 存事实（"项目路径是 X"），skill 存方法（"如何找到项目路径"） |

### 什么该放 skill、什么不该放

| 放 Skill | 不放 Skill |
|----------|-----------|
| 可复用的操作方法（反向追踪、调试策略） | 一次性的事实（某项目的路径） |
| 跨项目通用的步骤流程（部署、测试） | 环境配置（IP、机器名） |
| 从实践经验中总结的教训和踩坑经验 | 用户偏好和沟通习惯 |

### 与其他系统的边界

| 系统 | 存什么 | 谁写 | 例子 |
|------|--------|------|------|
| **Skill** | 操作方法论、步骤流程、踩坑经验 | lampson 积累或用户教导 | "如何定位项目代码"、"如何调试" |
| **Memory (core.md)** | 关于用户的事实和偏好 | lampson 观察 | "用户喜欢简洁回复" |
| **Projects** | 具体项目的详细信息 | 用户主动记录或 lampson 发现后记录 | "lampson 项目使用 pytest" |
| **Prompt (硬编码)** | 系统级行为约束 | 开发者 | "禁止 find 命令"、"危险操作须确认" |

---

## 2. Skill 文件格式

### 目录结构

```
~/.lampson/skills/
├── reverse-tracking/          # skill 名 = 目录名
│   └── SKILL.md               # skill 内容（必须有）
├── debug/
│   └── SKILL.md
├── code-writing/
│   └── SKILL.md
└── deployment/                 # 支持子目录分类
    └── docker/SKILL.md         # name = "docker", category = "deployment"
```

### SKILL.md 格式

```yaml
---
name: reverse-tracking          # 技能名称（默认=目录名）
description: 定位代码/项目的反向追踪方法
triggers:                       # 触发关键词（用于 skills_list 搜索匹配）
  - 找代码
  - 找项目
  - 代码在哪
  - 项目在哪
  - where is
  - locate
---

# 反向追踪：如何定位未知代码/项目

## 适用场景
- 用户问"XX 代码在哪里"、"XX 项目的代码在什么位置"
- 需要找到某个命令或工具的源码目录

## 步骤

### 情况一：XX 是一个可执行命令

1. **`which XX`** → 找到可执行文件的路径
2. **`head -5 $(which XX)`** → 看 shebang 行和 import 语句
3. **从 import 路径定位源码目录** → 通常在可执行文件的上级或同级目录
4. **`ls 源码目录`** → 确认项目结构

### 情况二：XX 是项目名（不是命令）

1. **先查已知路径** → 用 project_context 加载 projects_index 或记忆
2. **快速扫描** → `ls ~` 和 `ls ~/projects` 看目录名
3. **记录发现** → 找到后用 file_write 记到 projects 目录，下次不重复搜索

## 禁忌
- **绝对不要**对整个主目录做 search_files 或 search_content
- **绝对不要**用 `find /` 搜索整个文件系统
- 这些操作会超时且极度低效

## 示例
用户："找一下 hermes 的代码在哪里"
1. `which hermes` → /Users/songyuhao/.hermes/hermes-agent/venv/bin/hermes
2. `head -5 /Users/songyuhao/.hermes/hermes-agent/venv/bin/hermes` → 看 shebang 和 import
3. 从 import 路径推断源码在 ~/.hermes/hermes-agent/
4. `ls ~/.hermes/hermes-agent/` → 确认项目结构
```

### Frontmatter 字段说明

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `name` | 否 | string | 技能名称，默认取目录名 |
| `description` | 是 | string | 一句话描述，会出现在 system prompt 的索引中 |
| `triggers` | 否 | string[] | 触发关键词，用于 skills_list(query=) 搜索匹配 |

### Body 格式建议

skill body 应包含以下结构（不是强制，但建议遵循）：

1. **适用场景** — 什么情况下应该加载这个 skill
2. **步骤** — 具体的操作流程，分步骤描述
3. **禁忌** — 绝对不能做的事
4. **示例** — 一个具体的例子（可选但推荐）
5. **踩坑经验** — 实际使用中遇到的问题和解决方法（可选）

---

## 3. 触发机制

Skill 触发通过 **system prompt 索引块 + 强指引** 在 LLM 工具调用循环中实现，无需独立的 classify 阶段：

```
用户消息
  → LLM 读取 system prompt 中的 Skills 索引（含触发词）
    → 如果匹配触发词 → LLM 自行调用 skill_view(name="xxx")
    → 加载 skill 全文到上下文 → 按指导执行任务
```

### 3.1 System prompt 中的 Skills 索引块

`build_skills_index()` 在 system prompt 中注入完整的技能目录，每项包含触发词：

```
## Skills（按需加载）
以下是你已掌握的技能目录，每项包含触发词。
**规则**：当用户输入匹配某个 skill 的触发词时，你必须在回复之前先调用 skill_view(name="技能名") 加载全文，然后按 skill 指导执行任务。
如果没有 skill 的触发词与当前任务相关，直接回答即可。

- **reverse-tracking**: 代码反向追踪方法（触发: 找代码, 找项目, 代码在哪, where is, locate）
- **debug**: 调试方法论（触发: debug, 调试, 报错, error, traceback, fix）
- **code-writing**: 代码编写规范（触发: 写代码, 写一个, 创建文件, implement, 实现）
```

LLM 在工具调用循环中根据触发词自主决定是否调用 skill_view。

### 3.2 缓存与增量更新

索引构建后会被缓存（基于文件 mtime），只有当 SKILL.md 文件被修改时才重新生成，保证性能。

---

## 4. 系统架构

### 数据流

```
~/.lampson/skills/
  ├── reverse-tracking/SKILL.md     ← 知识源头
  ├── debug/SKILL.md
  └── code-writing/SKILL.md
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  prompt_builder.py                                   │
│  build_skills_index() → 生成索引注入 system prompt    │
│  （注入 name + description + triggers）              │
│  索引带缓存，文件 mtime 变化时重新生成               │
└─────────────────────┬───────────────────────────────┘
                      │
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌──────────────┐
  │ LLM 工具 │  │ 触发词   │  │ skill_view   │
  │ 调用循环 │  │ 匹配     │  │ 加载全文     │
  │          │  │ (system  │  │ → 指导执行   │
  │          │  │  prompt) │  │              │
  └──────────┘  └──────────┘  └──────────────┘
        │
        ▼
  ┌──────────────┐
  │ skills_tools │
  │              │
  │ skill_view() │ → 返回 SKILL.md 全文
  │ skills_list()│ → 返回所有 skill 的摘要列表
  └──────────────┘
```

### 涉及文件

| 文件 | 职责 |
|------|------|
| `~/.lampson/skills/<name>/SKILL.md` | Skill 知识文件 |
| `src/core/skills_tools.py` | skill_view / skills_list 工具函数 |
| `src/core/prompt_builder.py` | build_skills_index() 生成索引（含触发词）注入 system prompt |
| `src/core/indexer.py` | SkillIndex 索引管理（关键词检索、增量构建） |
| `src/core/tools.py` | 注册 skill_view / skills_list 到工具注册表 |
| `src/core/agent.py` | 工具调用循环（LLM 自行决定何时调用 skill_view） |

---

## 5. 管理方式

### 5.1 创建 Skill

采用**文件系统即接口**的方式：

- 创建 skill = 在 `~/.lampson/skills/<name>/` 下创建 `SKILL.md`
- 更新 skill = 直接编辑 `SKILL.md`
- 删除 skill = 删除对应目录

lampson 可以通过 `file_write` 工具创建/更新 skill 文件。

### 5.2 Skill 更新时机

| 时机 | 操作 |
|------|------|
| lampson 用 skill 完成任务后发现步骤不够 | 补充步骤到 body |
| lampson 遇到 skill 没覆盖的坑 | 追加"踩坑经验"到 body |
| 用户指出 skill 方法不对 | 修正错误步骤 |

---

## 6. 从 Prompt 迁移到 Skill 的计划

### 6.1 迁移清单

| 当前位置 | 内容 | 迁移到 |
|----------|------|--------|
| `prompts.py` L24-37 PERSISTENT_ENV_BLOCK | "定位代码/项目的方法论" | `~/.lampson/skills/reverse-tracking/SKILL.md` |
| `prompt_builder.py` L288-296 PLATFORM_HINTS | "定位代码/项目的方法论" | 同上（删除重复） |

### 6.2 迁移后效果

- system prompt 更短（少了一段方法论硬编码）
- 方法论可以独立迭代（改 SKILL.md 不用改代码）
- lampson 学会了"遇到找代码 → 先加载方法论"的通用模式
- 未来新增方法论（如"如何调试远程机器"）只需创建新 skill 文件

---

## 7. 自提升能力（Skill/Project 自动沉淀）

### 7.1 核心思路

lampson 每完成一个任务后，自动做一次**反思**：这次任务中积累的信息，是应该记录到 project 还是沉淀为一个 skill？

```
任务完成 → 反思判断 → 记录到 project / 创建或更新 skill / 什么都不做
```

这是 lampson 的核心成长机制——不靠人工维护知识库，而是在使用中自动积累能力。

### 7.2 判断逻辑：Project vs Skill vs 无需记录

| 分类 | 特征 | 存储位置 | 例子 |
|------|------|---------|------|
| **Project 信息** | 关于某个具体项目的事实（路径、配置、约定） | `~/.lampson/projects/<项目名>.md` | "hermes 项目在 ~/.hermes/hermes-agent/" |
| **Skill** | 可复用的操作方法、步骤、踩坑经验 | `~/.lampson/skills/<技能名>/SKILL.md` | "如何用 which→head→ls 定位代码" |
| **无需记录** | 一次性信息、闲聊、已存在的重复记录 | — | "今天天气不错" |

**判断规则**：

1. 如果是**某个项目的事实**（路径、技术栈、部署方式） → Project
2. 如果是**可复用的操作方法**（做某类事情的通用步骤） → Skill
3. 如果发现了**已有 skill 的不足**（步骤缺失、方法错误） → 更新 Skill
4. 如果只是临时信息或已记录过 → 跳过

### 7.3 触发时机

在任务**完成后**触发，有两个入口：

#### 入口 1：plan 执行完成后（confirm_and_execute）

```
confirm_and_execute()
  → executor.execute(plan)  // 执行计划
  → _reflect_and_learn(plan)  // ← 新增：反思沉淀
  → return result
```

#### 入口 2：Fast Path 完成后（_run_native / _run_prompt_based）

```
_run_native()
  → tool calling 循环
  → return 之前
  → _reflect_and_learn_fast_path(user_input, tool_calls_history)  // ← 新增
```

### 7.4 反思流程设计

#### 7.4.1 反思 Prompt

调用一次额外的 LLM，输入是任务目标和执行过程，输出是结构化的沉淀建议：

```python
REFLECT_PROMPT = """你是一个知识管理助手。请分析这次任务执行过程，判断是否有值得持久化的知识。

## 用户目标
{goal}

## 执行过程
{execution_summary}

## 已有 Skills
{existing_skills_list}

## 已有 Projects
{existing_projects_list}

请输出一个 JSON 对象：
{{
  "should_learn": true/false,
  "learnings": [
    {{
      "type": "project" | "skill_update" | "skill_create",
      "target": "项目名或技能名",
      "reason": "为什么值得记录",
      "content": "要写入的内容",
      "triggers": ["触发词1", "触发词2"]  // 仅 skill_create 时需要
    }}
  ]
}}

判断标准：
- project: 记录具体项目的事实信息（路径、技术栈、配置），如 "hermes 项目源码在 ~/.hermes/hermes-agent/"
- skill_create: 发现了一种可复用的操作方法，当前 skills 里没有覆盖的
- skill_update: 执行过程中发现某个已有 skill 的步骤不够或有错误
- should_learn=false: 简单查询、闲聊、或信息已经记录过

注意：
- 不要重复记录已有信息
- skill 的 content 应该是方法论（通用步骤），不是具体答案（具体路径）
- triggers 应该覆盖用户未来可能的表达方式（中英文都要考虑）
"""
```

#### 7.4.2 反思执行器

```python
def _reflect_and_learn(self, plan: Plan) -> None:
    """任务完成后反思，自动沉淀 skill 或 project 信息。"""
    
    # 1. 构建执行摘要
    execution_summary = self._format_execution_summary(plan)
    
    # 2. 获取已有 skills/projects 列表（避免重复）
    existing_skills = skills_tools.skills_list({})
    existing_projects = self._list_projects()
    
    # 3. 调用 LLM 做反思判断
    reflection = self._call_reflection(
        goal=plan.goal,
        execution_summary=execution_summary,
        existing_skills=existing_skills,
        existing_projects=existing_projects,
    )
    
    if not reflection.should_learn:
        return
    
    # 4. 执行沉淀
    for learning in reflection.learnings:
        if learning.type == "project":
            self._save_to_project(learning)
        elif learning.type == "skill_create":
            self._create_skill(learning)
        elif learning.type == "skill_update":
            self._update_skill(learning)
```

### 7.5 Trigger 词自动更新

当 skill 被更新或创建时，trigger 词也需要同步：

| 场景 | trigger 更新策略 |
|------|-----------------|
| **创建新 skill** | 由 LLM 在反思时生成初始 triggers（中英文各覆盖） |
| **更新 skill** | 如果 LLM 认为需要新增 trigger，合并到已有 triggers 中，不删除已有 trigger |

trigger 更新时机：反思 LLM 输出 `skill_create` / `skill_update` 时，可以附带新 triggers。

### 7.6 反思频率控制

不是每个任务都需要反思，否则会：
- 浪费 LLM 调用（额外 token 开销）
- 拖慢响应速度
- 产生大量低质量记录

**跳过反思的条件**：

| 条件 | 跳过 |
|------|------|
| Fast Path 且只用了 0-1 个工具 | 是 |
| 任务是闲聊/简单查询（intent=chat/info_query） | 是 |
| 执行失败（计划未完成） | 是 |
| 距上次反思不到 5 分钟 | 是（防止短时间内重复反思） |
| 用户明确说"不用记" | 是 |

**必须反思的条件**：

| 条件 | 必须 |
|------|------|
| 计划执行 3 步以上 | 是（复杂任务值得总结） |
| 执行过程中用了 skill | 是（检验 skill 是否有效） |
| 遇到错误并成功恢复 | 是（踩坑经验最有价值） |

### 7.7 反思结果的透明性

反思是**后台行为**，不打扰用户。但需要让用户知道发生了什么：

- 当有新 skill/project 被创建时，在回复末尾追加一行提示：
  > "已记录为技能：reverse-tracking（以后遇到类似问题会自动使用）"
- 当 skill 被更新时：
  > "已更新技能：debug（新增了'远程调试'场景的处理步骤）"
- 当没有新的沉淀时：静默，不提示

### 7.8 质量保障

防止反思产生低质量 skill：

1. **去重检查**：写入前检查已有 skills/projects 是否已有相同内容
2. **内容长度下限**：skill body 至少 200 字（太短说明方法论不成熟）
3. **trigger 数量下限**：新建 skill 至少 3 个 trigger

---

## 8. 测试计划

### 8.1 单元测试

**文件**: `tests/test_skills.py` + `tests/test_skills_on_demand.py`

```python
# 1. SKILL.md 解析
test_parse_skill_with_frontmatter()      # 正常解析 name/desc/triggers
test_parse_skill_without_frontmatter()   # 无 frontmatter 时 name=目录名
test_parse_skill_invalid_yaml()          # YAML 格式错误时 fallback

# 2. skill_view / search_skills 工具
test_skill_view_and_search_skills_keyword()  # 精确匹配返回全文，关键词搜索返回匹配

# 3. build_skills_index()
test_empty_skills_dir()                  # 空 skills 目录返回空字符串
test_skills_index_contains_trigger_hint() # 索引包含触发词和 skill_view 强指引
test_skills_index_cache_invalidation()    # 修改文件后缓存失效
test_skills_index_format()               # 格式正确（## Skills、**bold**、触发:）

# 4. SkillIndex 关键词检索
test_search_finds_by_description()       # 按 description 搜索
test_search_finds_by_trigger()           # 按 trigger 搜索
test_search_top_k()                     # top_k 参数限制返回数量
test_search_empty_query()               # 空查询返回空列表

# 5. 增量构建
test_unmodified_file_skipped()           # 未修改文件复用旧索引
test_modified_file_rebuilt()             # 修改文件重新解析
```

### 8.2 反思机制测试

**文件**: `tests/test_reflection.py`

```python
# 1. 反思判断逻辑
test_should_reflect_cooldown()          # 5分钟内不重复反思
test_should_reflect_fast_path_simple()  # 0-1 步 Fast Path 跳过
test_should_reflect_fast_path_complex() # 3+ 步必须反思
test_should_reflect_chat_intent()       # 闲聊跳过
test_should_reflect_plan_3steps()       # 3步计划必须反思

# 2. JSON 解析
test_extract_json_plain()                # 纯 JSON
test_extract_json_in_code_block()       # markdown 代码块包裹
test_extract_json_with_think()          # 包含 <think> 标签
test_extract_json_invalid()             # 无效 JSON 返回 None

# 3. 沉淀执行
test_save_to_project_new()              # 新项目文件创建
test_save_to_project_duplicate()        # 重复内容不写入
test_create_skill()                    # 创建 skill
test_create_skill_too_short()          # 内容太短不创建
test_create_skill_few_triggers()       # trigger 太少不创建
test_update_skill_append()             # 追加内容并合并 triggers
test_content_already_exists()          # 前100字符去重

# 4. Trigger 合并
test_merge_triggers_no_duplicate()      # 不重复
test_merge_triggers_invalid_yaml()      # 无 frontmatter 时不修改

# 5. execute_learnings
test_execute_learnings_project()        # project 类型沉淀
test_execute_learnings_empty()          # 空列表直接返回

# 6. reflect_and_learn
test_reflect_and_learn_no_learning()   # should_learn=false
test_reflect_and_learn_with_project()  # 沉淀 project

# 7. 格式化
test_format_execution_summary()         # 生成执行摘要
```

### 8.3 手动验证场景

| 场景 | 输入 | 期望行为 |
|------|------|---------|
| 找代码 | "找一下 hermes 的代码" | 自动 skill_view("reverse-tracking") → which → head → ls |
| 普通问题 | "python 怎么读文件" | 不加载任何 skill，直接回答 |
| 调试 | "这段代码报错了" | 自动 skill_view("debug") → 按步骤排查 |
| 反思-新建skill | 复杂调试后成功修复 | 任务完成后自动沉淀 skill |
| 反思-记录project | 首次探索某项目结构 | 自动在 projects/ 下创建项目信息文件 |
| 反思-跳过 | "今天星期几" | 不触发反思，静默 |

---

## 9. 实现优先级

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | Skill 文件格式定义 | ✅ 已完成 |
| P0 | skill_view / skills_list 工具 | ✅ 已完成 |
| P0 | build_skills_index() + 缓存 | ✅ 已完成 |
| P0 | 已有 skill 文件 | ✅ 已完成 |
| P0 | 单元测试 | ✅ 已完成 |
| P1 | 反思机制 | ✅ 已完成 |
| P1 | 沉淀执行器 | ✅ 已完成 |
| P1 | trigger 自动更新 | ✅ 已完成 |
| P2 | 反思频率控制优化 | 后续迭代 |
| P2 | 质量保障（长度/trigger 下限） | 后续迭代 |

---

## 10. 当前状态

- [x] Skill 文件格式定义（YAML frontmatter + markdown body）
- [x] skills_tools.py（skill_view / skills_list 工具函数）
- [x] prompt_builder.py build_skills_index()（索引含触发词注入 system prompt）
- [x] indexer.py SkillIndex（关键词检索、增量构建）
- [x] tools.py 注册到工具注册表
- [x] 已有 skill 文件（reverse-tracking, debug, code-writing）
- [x] system prompt skills 索引含触发词 + 强指引
- [x] 反思机制（_reflect_and_learn + reflect prompt）
- [x] 沉淀执行器（project 保存 / skill 创建 / skill 更新）
- [x] trigger 自动更新（反思阶段合并新 triggers）
- [x] test_skills.py 单元测试
- [x] test_skills_on_demand.py 按需加载专项测试
- [x] test_reflection.py 反思机制测试
