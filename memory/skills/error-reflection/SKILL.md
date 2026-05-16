---
name: error-reflection
description: 用户指出错误后的反思流程。使用场景：被纠正、做错了、需要反思。
created_at: "2025-06-17"
invocation_count: 3
---

# 错误反思流程

## 触发条件
用户指出错误时（"你错了"、"为什么会犯这种错"等）

## 反思步骤

### 1. 承认错误
直接承认，不辩解。

### 2. 定位根因
**不是**简单复述原因，而是：
- 找到第一个导致错误的决策点
- 明确我用了什么假设/信息来做的这个决策
- 指出哪个假设是错的，或者哪个信息我没验证

### 3. **采取行动**（关键！）
必须执行以下至少一项，防止再犯：

| 行动类型 | 适用场景 |
|---------|---------|
| 更新 skill | 流程/规范类错误（如写文件前要验证路径） |
| 更新 info | 信息类错误（如记错了配置值） |
| 更新 USER.md | 用户偏好/行为模式类错误 |
| 添加检查点 | 代码层面加校验逻辑 |

### 4. 记录教训
格式：`## YYYY-MM-DD 教训`
```markdown
## 2025-06-17 教训

**错误**：写文件时用了错误路径

**根因**：没验证 PROJECTS_DIR 的实际值，凭直觉用了代码仓库目录

**根因的根因**：不知道 ~/lamix/ 和 ~/.lamix/ 是两个不同目录

**行动**：
- [x] 在本文添加"写文件前验证路径"的检查点
- [ ] 后续写文件用 `python3 -c "from src.core.config import X_DIR; print(X_DIR)"` 验证
```

---

## 本次教训

### 错误
写文件到 `~/lamix/memory/projects/claude-code-analysis.md`，应该是 `~/.lamix/memory/projects/`

### 根因
- 直接用 `file_write(path="~/lamix/...")`
- 没验证 `PROJECTS_DIR` 的实际值
- 不知道 `~/lamix/`（代码仓库）和 `~/.lamix/`（用户数据）是两个不同目录

### 行动
- [x] 在 skill 中添加检查点
- [ ] **以后写/改任何文件时，先用 grep/cat 确认目标路径的实际值**

---

## 防御性检查清单

写文件前检查：
- [ ] 确认路径存在：`ls -la <目标目录>`
- [ ] 确认路径正确：`grep "XXX_DIR" src/core/config.py` 验证
- [ ] 或直接验证：`python3 -c "from src.core.config import XXX_DIR; print(XXX_DIR)"`
