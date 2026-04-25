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

### 当前实现（被动式）

```
用户消息 → system prompt 包含 skill 索引 → LLM 自己判断是否需要 skill_view
```

问题：LLM 经常**不主动调用** skill_view，而是自己瞎搞。

### 目标实现（主动式）

在 planner 的 **阶段一（意图分类）** 加入 skill 触发检查：

```
用户消息
  → 阶段一 classify（判断意图 + 检查是否匹配 skill trigger）
    → 如果匹配 → 阶段二 plan 的第一步自动加入 skill_view(name="xxx")
    → 进入计划执行时，第一步先加载 skill 内容到上下文
```

### 具体改动

#### 3.1 classify prompt 增加 skill 触发判断

在 `build_classify_prompt()` 中，除了输出 `intent/confidence/missing_info` 外，增加：

```json
{
  "matched_skill": "reverse-tracking"  // 或 null
}
```

classify prompt 中注入所有 skill 的 triggers 列表（很轻量）：

```
## Skills 触发词
- reverse-tracking: 找代码, 找项目, 代码在哪, 项目在哪, where is, locate
- debug: debug, 调试, 报错, 错误, error, exception, traceback, fix
- code-writing: 写代码, 写一个, 创建文件, 编写, implement, 实现
```

#### 3.2 planner 阶段二自动注入 skill

如果 phase1 结果中 `matched_skill` 非空，plan 的第一步自动设为：

```json
{"id": 0, "thought": "加载相关技能", "action": "skill_view", "args": {"name": "reverse-tracking"}, "reasoning": "用户要找代码，先加载反向追踪技能"}
```

#### 3.3 Fast Path 场景处理

如果 confidence >= 0.8 且 matched_skill 非空（Fast Path 跳过 plan_v2），在 agent.py 的 fast path 逻辑中：

```python
if phase1.get("matched_skill"):
    # 先调用 skill_view 加载 skill 内容
    skill_content = tools.dispatch("skill_view", {"name": phase1["matched_skill"]})
    # 将 skill 内容注入到后续的对话上下文中
```

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
│  （只注入 name + description，不注入全文）            │
└─────────────────────┬───────────────────────────────┘
                      │
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌──────────────┐
  │ classify │  │ plan_v2  │  │ plan execute │
  │ 阶段一   │  │ 阶段二   │  │ 执行阶段     │
  │          │  │          │  │              │
  │ 检查     │  │ 自动注入 │  │ skill_view   │
  │ triggers │  │ skill    │  │ 加载全文     │
  │ 匹配     │  │ 到 step0 │  │ → 指导执行   │
  └──────────┘  └──────────┘  └──────────────┘
        │             │              │
        └─────────────┼──────────────┘
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
| `src/core/prompt_builder.py` | build_skills_index() 生成索引注入 system prompt |
| `src/core/tools.py` | 注册 skill_view / skills_list 到工具注册表 |
| `src/planning/prompts.py` | classify prompt 中增加 trigger 匹配引导 |
| `src/planning/planner.py` | 阶段一结果中提取 matched_skill |
| `src/core/agent.py` | Fast Path 中处理 matched_skill |

---

## 5. 管理方式

### 5.1 创建 Skill

目前没有 create/delete 的管理工具。采用**文件系统即接口**的方式：

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

### 5.3 未来可选：skill_manage 工具

如果文件系统方式不够方便，可以后续添加一个 `skill_manage` 工具：

```python
SKILL_MANAGE_SCHEMA = {
    "name": "skill_manage",
    "description": "管理技能：创建、更新、删除",
    "parameters": {
        "action": "create | update | delete",
        "name": "技能名称",
        "content": "SKILL.md 完整内容（create/update 时必填）"
    }
}
```

---

## 6. 从 Prompt 迁移到 Skill 的计划

当前有方法论硬编码在两个地方，需要迁移到 skill：

### 6.1 迁移清单

| 当前位置 | 内容 | 迁移到 |
|----------|------|--------|
| `prompts.py` L24-37 PERSISTENT_ENV_BLOCK | "定位代码/项目的方法论" | `~/.lampson/skills/reverse-tracking/SKILL.md` |
| `prompt_builder.py` L288-296 PLATFORM_HINTS | "定位代码/项目的方法论" | 同上（删除重复） |

### 6.2 迁移步骤

1. **创建 skill 文件**：`~/.lampson/skills/reverse-tracking/SKILL.md`
2. **删除 prompts.py 中的硬编码**：去掉 PERSISTENT_ENV_BLOCK 里的"定位代码/项目的方法论"段落
3. **删除 prompt_builder.py 中的硬编码**：去掉 PLATFORM_HINTS 里的"定位代码/项目的方法论"段落
4. **在 classify prompt 中加入 trigger 匹配**：当用户说"找代码"、"找项目"时，自动匹配到 reverse-tracking skill
5. **测试**：验证 lampson 遇到"找一下XX代码"时，会先 skill_view 加载方法论

### 6.3 迁移后效果

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


def _reflect_and_learn_fast_path(self, user_input: str, tool_history: list) -> None:
    """Fast Path 完成后的轻量反思。"""
    # 同上，但 execution_summary 从 tool_history 构建
    # 且如果 tool_history 很简单（0-1步），直接跳过
    if len(tool_history) <= 1:
        return
    ...
```

#### 7.4.3 沉淀执行

```python
def _save_to_project(self, learning) -> None:
    """将项目信息追加到 projects/<项目名>.md"""
    project_file = Path.home() / ".lampson" / "projects" / f"{learning.target}.md"
    if project_file.exists():
        # 追加到已有文件
        existing = project_file.read_text()
        if learning.content in existing:
            return  # 已存在，跳过
        updated = existing + f"\n\n## {datetime.now():%Y-%m-%d}\n{learning.content}"
    else:
        updated = f"# {learning.target}\n\n{learning.content}"
    
    # 通过 file_write 工具写入
    tools.dispatch("file_write", {"path": str(project_file), "content": updated})


def _create_skill(self, learning) -> None:
    """创建新的 skill 文件"""
    skill_dir = Path.home() / ".lampson" / "skills" / learning.target
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # 构建 SKILL.md
    frontmatter = yaml.dump({
        "name": learning.target,
        "description": learning.reason,
        "triggers": learning.triggers,
    }, allow_unicode=True)
    
    skill_content = f"---\n{frontmatter}---\n\n{learning.content}"
    
    skill_file = skill_dir / "SKILL.md"
    tools.dispatch("file_write", {"path": str(skill_file), "content": skill_content})


def _update_skill(self, learning) -> None:
    """更新已有 skill"""
    skill_content = tools.dispatch("skill_view", {"name": learning.target})
    if skill_content.startswith("[Skill"):
        # skill 不存在，降级为创建
        self._create_skill(learning)
        return
    
    # 追加新内容（踩坑经验、补充步骤等）
    updated = skill_content + f"\n\n## 更新 ({datetime.now():%Y-%m-%d})\n{learning.content}"
    
    # 同时更新 triggers（合并新旧 triggers）
    # 重新解析 frontmatter，合并 triggers，写回
    ...
```

### 7.5 Trigger 词自动更新

当 skill 被更新或创建时，trigger 词也需要同步：

| 场景 | trigger 更新策略 |
|------|-----------------|
| **创建新 skill** | 由 LLM 在反思时生成初始 triggers（中英文各覆盖） |
| **更新 skill** | 如果 LLM 认为需要新增 trigger，合并到已有 triggers 中，不删除已有 trigger |
| **发现新表达方式** | 用户用了一种新的说法触发了这个 skill（如"代码藏哪了"匹配了 reverse-tracking），把这种新说法追加到 triggers |

trigger 更新时机：
1. 反思 LLM 输出 `skill_create` / `skill_update` 时，可以附带新 triggers
2. 如果某次 classify 阶段通过**模糊匹配**命中了 skill（不是精确 trigger 匹配），事后把用户的原始表达追加到 trigger 列表

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
4. **人工确认（可选）**：可以在 config.yaml 中配置 `reflection.require_confirm: true`，每次沉淀前问用户

---

## 8. 测试计划

### 8.1 单元测试

**文件**: `tests/test_skills.py`

```python
# 测试用例清单

# 1. SKILL.md 解析
test_parse_skill_with_frontmatter()      # 正常解析 name/desc/triggers
test_parse_skill_without_frontmatter()   # 无 frontmatter 时 name=目录名
test_parse_skill_invalid_yaml()          # YAML 格式错误时 fallback

# 2. skills_list 工具
test_skills_list_all()                   # 列出所有 skill
test_skills_list_by_query()              # 按 trigger 关键词搜索
test_skills_list_by_category()           # 按 category 过滤
test_skills_list_empty()                 # skills 目录为空

# 3. skill_view 工具
test_skill_view_found()                  # 精确匹配返回全文
test_skill_view_not_found()              # 不存在时列出可用 skill
test_skill_view_empty_name()             # name 为空时提示

# 4. build_skills_index()
test_build_skills_index()                # 生成格式正确的索引文本
test_build_skills_index_empty()          # 空 skills 目录返回空字符串
test_build_skills_index_with_category()  # 子目录下 skill 的 category 归类

# 5. 触发匹配（新增功能）
test_classify_matches_skill_trigger()    # "找一下代码" 匹配 reverse-tracking
test_classify_no_skill_match()           # "今天天气" 不匹配任何 skill
```

### 8.2 集成测试

```python
# 端到端测试（mock LLM 调用）

# 1. classify 阶段输出 matched_skill
test_classify_returns_matched_skill()

# 2. plan 阶段自动注入 skill_view 作为第一步
test_plan_includes_skill_view_step()

# 3. Fast Path 也能处理 matched_skill
test_fast_path_with_matched_skill()

# 4. 反思机制
test_reflect_creates_skill()            # 复杂任务后自动创建 skill
test_reflect_updates_project()          # 项目事实记录到 projects/
test_reflect_skips_simple_task()        # 简单任务跳过反思
test_reflect_skips_duplicate()          # 已有信息不重复记录
test_reflect_trigger_auto_update()      # 新表达方式自动追加到 triggers
```

### 8.4 反思机制专项测试

**文件**: `tests/test_reflection.py`

```python
# 1. 反思判断逻辑
test_reflect_classify_project_info()    # "项目路径是 X" → project
test_reflect_classify_new_method()      # "发现一种新方法" → skill_create
test_reflect_classify_update_skill()    # "之前的方法有坑" → skill_update
test_reflect_classify_skip()            # 闲聊/简单查询 → should_learn=false

# 2. 沉淀执行
test_save_to_project_new()              # 新项目文件创建
test_save_to_project_append()           # 已有项目追加信息
test_save_to_project_no_duplicate()     # 重复信息不写入
test_create_skill_with_triggers()       # 创建 skill 并写入 triggers
test_update_skill_preserves_existing()  # 更新不丢失已有内容

# 3. 频率控制
test_reflect_skip_simple_fast_path()    # 0-1 步 Fast Path 跳过
test_reflect_skip_chat_intent()         # intent=chat 跳过
test_reflect_skip_failed_plan()         # 执行失败跳过
test_reflect_must_complex_task()        # 3+ 步计划必须反思
test_reflect_must_error_recovery()      # 错误恢复必须反思

# 4. Trigger 自动更新
test_trigger_merge_on_create()          # 新建时写入初始 triggers
test_trigger_merge_on_update()          # 更新时合并新旧 triggers
test_trigger_auto_discover()            # 模糊匹配后追加新 trigger
test_trigger_minimum_count()            # 新 skill 至少 3 个 trigger
```

### 8.3 手动验证场景

| 场景 | 输入 | 期望行为 |
|------|------|---------|
| 找代码 | "找一下 hermes 的代码" | 自动 skill_view("reverse-tracking") → which → head → ls |
| 找项目 | "那个训练项目的代码在哪" | skill_view → 查 projects_index → ls ~ |
| 普通问题 | "python 怎么读文件" | 不加载任何 skill，直接回答 |
| 调试 | "这段代码报错了" | 自动 skill_view("debug") → 按步骤排查 |
| 反思-新建skill | 复杂调试后成功修复 | 任务完成后自动沉淀 "调试远程服务" skill |
| 反思-记录project | 首次探索某项目结构 | 自动在 projects/ 下创建项目信息文件 |
| 反思-更新skill | 用 reverse-tracking 遇到特殊情况 | 自动追加踩坑经验到 reverse-tracking skill |
| 反思-跳过 | "今天星期几" | 不触发反思，无额外提示 |

---

## 9. 实现优先级

| 优先级 | 任务 | 预估工作量 |
|--------|------|-----------|
| P0 | 创建 reverse-tracking SKILL.md | 10 分钟 |
| P0 | 删除 prompts.py / prompt_builder.py 中的硬编码方法论 | 10 分钟 |
| P0 | 写 test_skills.py 单元测试 | 30 分钟 |
| P1 | classify prompt 增加 trigger 匹配 + matched_skill 输出 | 30 分钟 |
| P1 | plan_v2 自动注入 skill_view step0 | 20 分钟 |
| P1 | Fast Path 处理 matched_skill | 20 分钟 |
| P1 | 反思机制（_reflect_and_learn） | 1 小时 |
| P1 | 反思 prompt + 沉淀执行器 | 30 分钟 |
| P2 | skill_manage 管理工具（create/update/delete） | 1 小时 |
| P2 | trigger 自动发现（模糊匹配后追加） | 30 分钟 |
| P2 | SKILL_GUIDANCE 中加入"使用 skill 后发现过时则更新"的自省引导 | 10 分钟 |

---

## 10. 当前状态

- [x] Skill 文件格式定义（YAML frontmatter + markdown body）
- [x] skills_tools.py（skill_view / skills_list 工具函数）
- [x] prompt_builder.py build_skills_index()（索引注入 system prompt）
- [x] tools.py 注册到工具注册表
- [x] 已有 3 个 skill：code-writing、debug、reverse-tracking（待创建）
- [ ] reverse-tracking SKILL.md 创建
- [ ] prompts.py / prompt_builder.py 硬编码方法论删除
- [ ] classify trigger 匹配机制
- [ ] plan 自动注入 skill_view
- [ ] Fast Path matched_skill 处理
- [ ] 反思机制（_reflect_and_learn + reflect prompt）
- [ ] 沉淀执行器（project 保存 / skill 创建 / skill 更新）
- [ ] trigger 自动更新
- [ ] test_skills.py 单元测试
- [ ] test_reflection.py 反思机制测试
- [ ] skill_manage 管理工具
