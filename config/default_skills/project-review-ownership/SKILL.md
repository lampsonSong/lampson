---
name: project-review-ownership
description: 项目审查结果应记录在对应项目本身，不是 hermes 或其他位置。
---

# Project Review Ownership

审查某个项目时，审查结果属于被审查的项目，不属于审查工具。

## 规则

- 项目审查结论 → 写入 `~/.lamix/projects/{project_name}.md`
- 不要把项目信息混进 hermes 或其他项目的记录里
- 审查时先加载项目上下文（`project_context`），审查完更新回项目
