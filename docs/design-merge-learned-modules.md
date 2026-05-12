# 设计文档：合并 Learned Modules 到 Skills 体系

## 背景

当前 Lamix 有两个独立的"自学习"存储机制：

| 机制 | 位置 | 内容 | 工具注册 |
|------|------|------|---------|
| Skills | `~/.lamix/skills/<name>/SKILL.md` | markdown 知识/流程 | 无，LLM 读文本理解后执行 |
| Learned Modules | `~/.lamix/learned_modules/<name>.py` | Python 代码 | 有 TOOL_SCHEMA 自动注册为工具 |

问题：
1. **功能重叠**：skills 目录已支持 `scripts/` 子目录放 .py，learned_modules 做的事完全可以统一
2. **维护分散**：两套扫描、审计、创建、更新逻辑各自独立
3. **实际未使用**：`learned_modules/` 目录为空（只有 `__init__.py`），从未真正沉淀过模块

## 方案：Skills 统一承载

合并后，skill 目录结构变为：

```
~/.lamix/skills/<skill-name>/
├── SKILL.md              # 知识/流程描述（必须）
├── scripts/              # 可选：附属 Python 脚本
│   ├── main.py           # 可定义 TOOL_SCHEMA + TOOL_RUNNER，自动注册为工具
│   └── helper.py         # 辅助模块，不注册
├── templates/            # 可选：模板文件
└── references/           # 可选：参考文档
```

**核心变化**：
- `scripts/` 下的 .py 文件如果定义了 `TOOL_SCHEMA` + `TOOL_RUNNER`，自动注册为工具（复用现有 `register_external` 机制）
- 沙箱约束（禁止 import src）复用到 scripts/ 上
- `~/.lamix/learned_modules/` 废弃，不再扫描

## 涉及文件改动清单

### 1. `src/tools/learned_modules.py` — 重写

**改动**：
- 扫描目标从 `~/.lamix/learned_modules/*.py` 改为 `~/.lamix/skills/*/scripts/*.py`
- `scan_and_register()` 改为遍历每个 skill 的 scripts/ 子目录
- `write_module()` 改为在指定 skill 的 scripts/ 下写入 .py
- `get_module()` 改为按 skill_name + script_name 查找
- 保留 `_contains_blocked_import()` 沙箱检查逻辑不变
- 保留 `_load_module()` 动态加载逻辑不变（只改扫描路径）
- `ensure_dir()` 不再创建 `learned_modules/` 目录
- `list_modules()` 改为列出所有 skill 下的 scripts

**函数签名变化**：
```python
# 旧
def scan_and_register() -> list[dict[str, Any]]           # 扫描 learned_modules/
def get_module(name: str) -> Any | None                    # name = module名
def list_modules() -> list[dict[str, str]]                 # 列出 learned_modules/*.py
def write_module(name: str, code: str) -> str              # 写入 learned_modules/<name>.py

# 新
def scan_and_register() -> list[dict[str, Any]]           # 扫描 skills/*/scripts/*.py
def get_module(skill_name: str, script_name: str) -> Any | None  # 按skill+script查找
def list_modules() -> list[dict[str, str]]                # 列出所有skill的scripts
def write_module(skill_name: str, script_name: str, code: str) -> str  # 写入 skills/<skill>/scripts/<name>.py
```

### 2. `src/core/reflection.py` — 修改沉淀逻辑

**改动**：
- 删除 `_create_module()` / `_update_module()` 函数
- 删除 `_get_existing_modules_summary()` 函数
- 删除 `_sanitize_module_name()` 函数
- 删除 `_contains_blocked_import()` 函数（沙箱检查已移到 learned_modules.py）
- 反思沉淀分类从四类（skill/info/project/learned_module）改为三类（skill/info/project）
- 原来"可复用代码 → learned_module"的场景改为：创建一个带 scripts/ 的 skill
- LLM prompt（REFLECT_TOOL_SCHEMA description）中去掉 "learned_module" 字样

**沉淀逻辑变化**：
```
旧分类：项目事实 → projects / 新方法论 → skills / 可复用代码 → learned_modules / 无价值 → 跳过
新分类：项目事实 → projects / 新方法论 → skills（含可选scripts）/ 无价值 → 跳过
```

### 3. `src/core/self_audit.py` — 修改审计扫描

**改动**：
- `scan_learned_modules()` 改为 `scan_skill_scripts()`，扫描 `skills/*/scripts/*.py`
- 保留语法检查和危险 import 检查逻辑
- `run_audit()` 中：
  - `modules_scanned` 改为统计 `skills/*/scripts/*.py` 数量
  - `scan_learned_modules()` 调用改为 `scan_skill_scripts()`
- `AuditReport` 的 `modules_scanned` 字段改为 `scripts_scanned`
- `AuditReport.summary_text()` 输出改为 "X scripts"
- 删除 `LEARNED_MODULES_DIR` 常量

### 4. `src/core/tools.py` — 修改工具描述

**改动**：
- `execute_shell` 工具描述中去掉 "learned_modules" 字样
- `module` 参数说明改为 "skills 下 scripts 目录中的模块名"
- `load_learned_modules()` 函数保持名称不变（只是内部扫描路径变了）

### 5. `src/tools/task_scheduler_tool.py` — 修改参数描述

**改动**：
- `module` 参数描述从 "learned_modules 下的模块名" 改为 "skills 下 scripts 目录中的模块名"
- `_resolve_module()` 函数适配新的 `get_module()` 签名

### 6. `src/daemon.py` — 无实质改动

`load_learned_modules()` 调用不变，函数内部已改扫描路径。

### 7. `src/core/session.py` — 无实质改动

`/skills` 命令已列出 skills 目录，scripts/ 自然包含在内。

## 迁移策略

当前 `learned_modules/` 为空，无需数据迁移。

如果未来有存量数据，迁移方式：
```bash
# 对每个 learned_modules/xxx.py：
mkdir -p ~/.lamix/skills/xxx/scripts/
mv ~/.lamix/learned_modules/xxx.py ~/.lamix/skills/xxx/scripts/main.py
# 补一个最小 SKILL.md
echo "---\nname: xxx\ndescription: (从 docstring 提取)\n---\n\n# xxx\n\nAuto-migrated from learned_modules." > ~/.lamix/skills/xxx/SKILL.md
```

## 验收标准

1. `uv run pytest tests/ -x` 全部通过
2. 手动验证：`/self-audit` 正常执行，扫描范围包含 scripts
3. 手动验证：在 skill 的 scripts/ 下放一个带 TOOL_SCHEMA 的 .py，重启 daemon 后工具自动注册
4. `~/.lamix/learned_modules/` 目录不再被任何代码引用
5. `grep -r "learned_module" src/` 只剩注释和历史说明，无功能引用

## 风险

- **低风险**：learned_modules 实际未使用，合并不影响现有功能
- **需注意**：`get_module()` 签名变化影响 task_scheduler_tool.py 的调用方
