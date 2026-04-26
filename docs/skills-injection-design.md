# Skills 目录注入 + 计数清理 设计文档

## 目标

1. Skills 全量注入 system prompt（学 Hermes 模式），LLM 看到目录后按名加载全文
2. 每个 skill 记录创建日期和调用次数
3. Skill 总数达阈值时自动归档冷 skill

## 核心决策

- **Skills = 全量目录注入**：不需要语义检索，LLM 直接看"菜单"点菜
- **Projects = 保留语义检索**：项目文件长，不适合全量注入，search_projects 保留 embedding 检索
- **search_skills 工具替换为 skill_view(name)**：按名加载，不再做语义搜索

## 改动清单

### 1. `src/core/prompt_builder.py` — 新增 `build_skills_index()`

新增函数，遍历 `~/.lampson/skills/*/SKILL.md`，解析 frontmatter，生成目录块：

```
## Skills（按需加载）
以下是你已掌握的技能目录。当任务与某个 skill 相关时，用 skill_view(name="技能名") 加载全文。
如果没有 skill 与当前任务相关，直接回答即可。

- **code-writing**: 写代码、创建或编辑代码文件（触发: 写代码, 写一个, 创建文件, 编写, implement, 实现）
- **reverse-tracking**: 定位代码/项目的反向追踪方法（触发: 找代码, 找项目, 代码在哪, 项目在哪）
- **debug**: 调试代码、排查错误、分析报错信息（触发: debug, 调试, 报错, 错误, error, exception）
```

在 `PromptBuilder.build()` 的 L2 层插入 skills 索引。

用 mtime 检测做缓存：记一个 `_skills_index_cache` 变量，存 (mtime_tuple, result)，
每次调用时比较所有 SKILL.md 的 mtime，有变化才重建。

### 2. Skill SKILL.md frontmatter 新增字段

```yaml
---
name: code-writing
description: 写代码、创建或编辑代码文件
triggers:
  - 写代码
  - 写一个
created_at: "2026-04-20"    # 新增：创建日期（首次索引时自动填入，已有 skill 补填当天日期）
invocation_count: 0          # 新增：调用次数
---
```

### 3. `src/core/skills_tools.py` — 工具调整

#### 删除 `SEARCH_SKILLS_SCHEMA`
#### 新增 `SKILL_VIEW_SCHEMA`
```python
SKILL_VIEW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "skill_view",
        "description": "加载指定技能的完整内容。当你从 Skills 目录中看到与任务相关的技能时，用此工具加载全文。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "技能名称，例如 'code-writing', 'reverse-tracking'",
                },
            },
            "required": ["name"],
        },
    },
}
```

#### 新增 `skill_view(params)` 函数
- 按名称在 `_active_skill_index` 中查找
- 找到后读取 SKILL.md 全文返回
- 同时递增该 skill 的 `invocation_count`，写回 frontmatter

#### `search_skills(params)` — 保留但降级为简单关键词匹配
- 不再用 embedding，只做 name/description 关键词匹配
- 主要供 skill 数量很多时辅助查找

### 4. `src/core/indexer.py` — `SkillIndex` 简化

#### 删除 embedding 相关逻辑
- 删除 `_embed()` 方法
- 删除 `_use_embedding` 属性
- 删除 `_LoadedModel` 依赖（skills 不需要了）
- `load_or_build()` 不再计算 embedding，只存 frontmatter 元数据

#### 新增 `_maybe_cleanup()` 方法
```python
def _maybe_cleanup(self) -> None:
    """当 skill 总数 >= 阈值时，归档冷 skill。"""
    config = _load_cleanup_config()
    if len(self._entries) < config["max_skills"]:
        return
    cutoff = datetime.now() - timedelta(days=config["age_days"])
    to_archive = [
        e for e in self._entries
        if e.get("created_at") and e["created_at"] <= cutoff.isoformat()[:10]
        and e.get("invocation_count", 0) <= config["min_invocations"]
    ]
    if not to_archive:
        return
    archive_dir = self.skills_dir / ".archived"
    archive_dir.mkdir(exist_ok=True)
    for e in to_archive:
        skill_dir = Path(e["path"]).parent
        if skill_dir.exists():
            shutil.move(str(skill_dir), str(archive_dir / skill_dir.name))
```

#### `list_summaries()` 保持不变

### 5. `src/core/tools.py` — 注册新工具

- 注册 `skill_view` 工具
- `search_skills` 保留（降级为关键词匹配）

### 6. `config/default.yaml` — 新增配置段

```yaml
# Skills 管理
skills_management:
  cleanup_max_skills: 300      # 触发清理的 skill 总数阈值
  cleanup_age_days: 10         # 创建超过此天数的 skill 参与清理
  cleanup_min_invocations: 0   # 调用次数不超过此值的参与清理
```

### 7. `src/planning/prompts.py` — MEMORY_STRUCTURE_BLOCK 更新

把工具列表中的 `search_skills` 改为 `skill_view`：
```
- **skill_view(name)**: 按名称加载指定技能的完整内容（名称已知时使用）
```

## 改动文件列表

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/core/prompt_builder.py` | 修改 | 新增 `build_skills_index()`，在 `build()` 中注入 |
| `src/core/skills_tools.py` | 修改 | 新增 `skill_view`，`search_skills` 降级为关键词匹配 |
| `src/core/indexer.py` | 修改 | `SkillIndex` 删 embedding，新增 `_maybe_cleanup()` |
| `src/core/tools.py` | 修改 | 注册 `skill_view` |
| `src/planning/prompts.py` | 修改 | MEMORY_STRUCTURE_BLOCK 更新工具名 |
| `config/default.yaml` | 修改 | 新增 `skills_management` 配置段 |
| `tests/test_prompt_builder.py` | 新增/修改 | 测试 skills 索引注入 |
| `tests/test_indexer.py` | 修改 | 测试清理逻辑 |

## 不改动的文件

| 文件 | 原因 |
|---|---|
| `src/core/indexer.py` 的 `ProjectIndex` | Projects 保留语义检索不变 |
| `src/core/skills_tools.py` 的 `search_projects` | 保留语义检索 |
| `src/core/agent.py` | 工具循环逻辑不变 |

## 验收标准

1. System prompt 中包含所有 skill 的 name + description + triggers 目录
2. `skill_view(name)` 能按名加载 skill 全文
3. 新 skill 首次被索引时自动写入 `created_at` 和 `invocation_count: 0`
4. `skill_view` 被调用后，对应 skill 的 `invocation_count` 递增
5. Skill 总数 >= 300 时，自动归档满足清理条件的老 skill 到 `.archived/`
6. 配置参数可通过 `config.yaml` 自定义
7. `search_projects` 语义检索不受影响
8. 全部现有测试通过
