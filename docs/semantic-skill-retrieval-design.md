# 语义检索 Skill/Project 设计文档

> 日期：2026-04-26
> 状态：待实现（Cursor 执行）

---

## 1. 背景与动机

当前 skill 系统采用"预加载索引 → LLM 自己挑选"的模式。问题是：

1. **万级 skill 放不下**：即使分类折叠，10000 个 skill 的索引也占大量 token
2. **LLM 不擅长翻目录**：让 LLM 从目录列表中挑选 skill，经常不调 skill_view，直接瞎搞
3. **检索是代码的事，不是 LLM 的事**：LLM 应该描述"我需要什么"，代码负责匹配

**新思路**：LLM 不看 skill 目录，只描述需求；代码通过语义检索匹配 skill/project，再把匹配结果注入后续 LLM 调用。

---

## 2. 整体流程（3步 LLM 调用）

```
用户 query
   │
   ▼
┌──────────────────────────────────────────┐
│ Step 1: 意图分析 (LLM — classify 阶段)    │
│                                           │
│ 输入: 用户 query + 对话历史               │
│ 输出: {                                   │
│   intent: "chat|info_query|tool_task",    │
│   needs_tools: true/false,               │
│   intent_detail: "...",                   │
│   confidence: 0.0-1.0,                    │
│   skill_needs: "需要什么类型的技能",       │
│   project_needs: "需要什么项目的上下文"    │
│ }                                         │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│ Step 2: 语义检索 (纯代码，不调 LLM)        │
│                                           │
│ 2a. 把 skill_needs 文本做 embedding       │
│     → 在 skill 索引中做余弦相似度检索      │
│     → 取 top-K（可配置，默认 3）           │
│     → 加载匹配 skill 的 SKILL.md 全文     │
│                                           │
│ 2b. 把 project_needs 文本做 embedding     │
│     → 在 project 索引中做余弦相似度检索    │
│     → 取 top-K（可配置，默认 2）           │
│     → 加载匹配 project 的 .md 全文        │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│ Step 3: 方案生成 (LLM — plan/execute 阶段) │
│                                           │
│ 输入: 用户 query + 匹配到的 skill 全文 +  │
│       匹配到的 project context + 对话历史  │
│ 输出: 执行计划 或 直接回复                │
└──────────────────────────────────────────┘
```

---

## 3. Embedding 方案

### 3.1 模型选择

使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`：
- **支持中文**（multilingual）
- **体积小**：模型 ~470MB，embedding 维度 384
- **速度快**：单条 embedding ~5ms（CPU）
- **无需 GPU**
- 安装：`pip install sentence-transformers`（会拉 torch CPU 版）

### 3.2 依赖添加

在 `pyproject.toml` 的 `dependencies` 中添加：
```
"sentence-transformers>=3.0.0",
```

### 3.3 Embedding 策略

| 对象 | embedding 文本 | 说明 |
|------|---------------|------|
| Skill | `name + " " + description + " " + " ".join(triggers)` | frontmatter 的可搜索字段 |
| Project | `name + " " + 文件第一段（前200字）` | 概览性内容 |
| LLM 需求描述 | 直接 embedding skill_needs / project_needs 文本 | Step 1 的输出 |

---

## 4. 索引设计

### 4.1 存储格式

```
~/.lampson/
├── index/                    ← 新增目录
│   ├── skills.jsonl          ← 每行一条
│   └── projects.jsonl        ← 每行一条
├── skills/                   ← 不变
└── projects/                 ← 不变
```

每行 JSON 格式：
```json
{
  "name": "reverse-tracking",
  "category": "general",
  "description": "定位代码/项目的反向追踪方法",
  "triggers": ["找代码", "找项目"],
  "path": "~/.lampson/skills/reverse-tracking/SKILL.md",
  "mtime": 1714000000.0,
  "embedding": [0.123, -0.456, ...]
}
```

### 4.2 索引更新策略

**启动时增量更新**（不是每次查询时更新）：

1. 加载 `index/skills.jsonl` 到内存（如果存在）
2. 扫描 `skills/` 目录所有 SKILL.md
3. 对比 mtime：
   - 新文件 → 解析 frontmatter → embedding → 追加
   - mtime 变化 → 重新解析 + 重新 embedding → 更新
   - 文件消失 → 从索引删除
4. 写回 `index/skills.jsonl`

project 索引同理。

### 4.3 索引管理器 API

```python
class SkillIndex:
    """Skill 语义索引管理器。"""

    def __init__(self, skills_dir: Path, index_dir: Path):
        self.skills_dir = skills_dir
        self.index_dir = index_dir
        self._model = None  # 延迟加载 sentence-transformers
        self._entries: list[IndexEntry] = []

    def load_or_build(self) -> None:
        """启动时调用：加载索引，增量更新。"""

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """语义检索，返回匹配 skill 的 SKILL.md 全文列表。"""

    def _embed(self, text: str) -> list[float]:
        """单条文本 embedding。"""

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """余弦相似度。"""
```

ProjectIndex 同理（更简单，不需要 category）。

---

## 5. 配置扩展

在 `config/default.yaml` 中新增 `retrieval` 段：

```yaml
# 语义检索配置
retrieval:
  embedding_model: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
  skill_top_k: 3          # 检索 skill 时返回 top-K 结果
  project_top_k: 2        # 检索 project 时返回 top-K 结果
  similarity_threshold: 0.3  # 最低相似度阈值，低于此值不返回
```

在 `src/core/config.py` 中读取这些配置，提供默认值。

---

## 6. 代码改动清单

### 6.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/core/indexer.py` | SkillIndex + ProjectIndex 类（索引管理 + 语义检索） |

### 6.2 需要修改的文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `pyproject.toml` | 添加 sentence-transformers 依赖 | |
| `config/default.yaml` | 新增 `retrieval` 配置段 | |
| `src/core/config.py` | 读取 retrieval 配置 | |
| `src/planning/steps.py` | IntentResult 增加 `skill_needs`、`project_needs` 字段 | |
| `src/planning/prompts.py` | `build_classify_prompt()` 输出增加 skill_needs / project_needs | |
| `src/planning/planner.py` | `classify()` 解析 skill_needs / project_needs；`plan_v2()` 前插入语义检索步骤 | |
| `src/core/agent.py` | Fast Path 中插入语义检索步骤 | |
| `src/core/session.py` | 启动时初始化 SkillIndex / ProjectIndex | |
| `src/core/prompt_builder.py` | `build_skills_index()` 删除（不再注入 skill 索引到 system prompt） | |

### 6.3 需要删除/简化的代码

| 代码 | 原因 |
|------|------|
| `prompt_builder.py` 中的 `build_skills_index()` | system prompt 不再注入 skill 索引 |
| `prompt_builder.py` 中的 `_iter_skill_files()` | 移到 indexer.py |
| `skills_tools.py` 中的 `skills_list()` | 不再需要 LLM 调工具翻目录 |
| `skills_tools.py` 中的 `skill_view()` | 语义检索直接返回全文，不需要按名查看 |
| `tools.py` 中注册 `skill_view` / `skills_list` | 从工具注册表移除 |
| `memory_show()` 中调用 `_iter_skills()` | 改为从索引读取（概要信息） |

### 6.4 不变的部分

- Skill 和 Project 的**文件结构**不变（SKILL.md、projects/*.md）
- Executor、ReAct 循环不变
- Compaction 不变
- Feishu 不变
- 自更新不变

---

## 7. 数据流详细设计

### 7.1 classify 阶段（Step 1）

**输入**（传给 LLM）：
- 用户 query
- 对话历史（动态层）
- 环境信息（常驻层）
- 工具 schema

**输出**（LLM 返回 JSON）：
```json
{
  "intent": "tool_task",
  "needs_tools": true,
  "intent_detail": "用户要调试一个 Python 脚本的报错",
  "confidence": 0.9,
  "missing_info": [],
  "skill_needs": "需要调试 Python 程序的方法论，包括错误定位、日志分析",
  "project_needs": "可能需要了解 lampson 项目的结构"
}
```

**关键**：`skill_needs` 和 `project_needs` 是自然语言描述，不是 skill 名字。LLM 只描述需求，不需要知道有哪些 skill。

如果 `needs_tools = false`（闲聊），`skill_needs` 和 `project_needs` 可以为空字符串。

### 7.2 语义检索阶段（Step 2）

```python
def _retrieve_context(
    skill_needs: str,
    project_needs: str,
    skill_index: SkillIndex,
    project_index: ProjectIndex,
    config: RetrievalConfig,
) -> tuple[list[str], list[str]]:
    """根据 LLM 的需求描述，检索匹配的 skill 和 project 全文。"""

    matched_skills = []
    if skill_needs:
        matched_skills = skill_index.search(
            query=skill_needs,
            top_k=config.skill_top_k,
        )

    matched_projects = []
    if project_needs:
        matched_projects = project_index.search(
            query=project_needs,
            top_k=config.project_top_k,
        )

    return matched_skills, matched_projects
```

### 7.3 plan 阶段（Step 3）

**额外输入**（注入到 plan prompt）：
```
## 匹配的技能
{matched_skill_1 全文}

## 匹配的项目上下文
{matched_project_1 全文}
```

LLM 拿着这些 context 生成更准确的执行计划。

### 7.4 Fast Path 处理

Fast Path（confidence >= 0.8 且 needs_tools = true）也需要走语义检索：

```python
# agent.py 中
if fast_path and phase1.needs_tools:
    # 语义检索
    skills, projects = _retrieve_context(
        phase1.skill_needs, phase1.project_needs, ...
    )
    # 将检索结果注入 tool calling 循环的上下文
    context_block = _format_retrieved_context(skills, projects)
    # 继续原有的 fast path 逻辑，但 system prompt 带上检索到的 context
```

---

## 8. 边界情况处理

| 场景 | 处理 |
|------|------|
| 索引目录不存在 | 启动时自动创建 `~/.lampson/index/` |
| sentence-transformers 未安装 | 降级为关键词匹配（原有的 triggers 逻辑） |
| 无匹配结果（相似度都低于阈值） | 不注入任何 skill/project，LLM 裸跑 |
| skill_needs / project_needs 为空 | 跳过对应检索步骤 |
| LLM classify 返回的 JSON 缺少 skill_needs | 默认空字符串，不检索 |
| 首次运行（无索引文件） | 全量构建索引 |

---

## 9. 实现顺序

按以下顺序实现，每步可独立测试：

### Phase 1：索引基础设施
1. 安装 sentence-transformers，添加依赖
2. 实现 `src/core/indexer.py`：SkillIndex、ProjectIndex
3. 在 `config/default.yaml` 和 `config.py` 中添加 retrieval 配置
4. 在 `session.py` 启动时初始化索引

### Phase 2：classify 输出扩展
5. `steps.py`：IntentResult 增加 skill_needs / project_needs
6. `prompts.py`：classify prompt 增加这两个输出字段
7. `planner.py`：解析这两个字段

### Phase 3：检索注入
8. 在 planner.py 的 plan_v2() 前插入语义检索
9. 在 agent.py 的 Fast Path 中插入语义检索
10. 将检索结果注入 plan prompt

### Phase 4：清理
11. 删除 `build_skills_index()` 及相关代码
12. 从工具注册表移除 `skill_view` / `skills_list`
13. 清理 `memory_show()` 中的 skill 列表逻辑
14. 更新测试

---

## 10. 验收标准

1. `pytest tests/` 全部通过（当前 119 个）
2. 新增 indexer 相关测试用例（mock embedding）
3. 首次运行自动构建索引，无需手动操作
4. sentence-transformers 未安装时降级为关键词匹配，不报错
5. 语义检索耗时 < 100ms（CPU 模式）
6. system prompt 不再包含任何 skill 目录/索引信息
7. classify 输出包含 skill_needs / project_needs 字段
8. plan prompt 中能看到检索到的 skill/project 全文

---

## 11. 关键设计决策记录

| 决策 | 理由 |
|------|------|
| 用本地 embedding 模型而非 API | 零延迟、无网络依赖、无额外成本 |
| paraphrase-multilingual-MiniLM-L12-v2 | 中文支持好、体积小（470MB）、速度快 |
| 索引存 JSONL 而非 SQLite | 简单、无额外依赖、万级规模足够 |
| 启动时增量更新而非实时更新 | skill/project 不频繁变更，无需实时监听 |
| 保留降级机制 | sentence-transformers 是重依赖，需能降级运行 |
| top-K 可配置 | 不同场景对检索数量需求不同 |
| LLM 只描述需求不看目录 | 万级 skill 时 LLM 翻目录不可行 |
