---
name: code-writing
description: 写代码、创建或编辑代码文件。使用场景：实现功能、创建新文件、修改现有代码、修复 bug。
triggers:
  - 写代码
  - 创建文件
  - 编辑代码
  - 实现
  - 编码
---

# Code Writing

写代码前必须先加载本 skill，按流程执行。

## 1. 理清需求

- 梳理需求，确认理解无误
- 确认语言和目标文件路径
- 不确定时先问清楚，不要猜

## 2. 编写代码

按优先级派发，前者不可用时才用后者：

1. **Cursor Agent** → `cursor-agent` skill
2. **Hermes** → `hermes-delegate` skill
3. **自己写** → 直接编写完整、可运行的代码（不写 TODO 或 placeholder）

## 3. 验证（必须执行，不跳过）

1. **语法检查**：`python -m py_compile` / `node --check` 等
2. **运行测试**：编写并运行测试用例，覆盖正常路径和边界情况
3. **端到端验证**：构造真实场景，确认功能可用

## 4. 重启判断

改动文件在 `src/` 下 → 写 boot task → 重启 daemon → 验证。

## 规范

- 模块级 docstring、type hints
- 函数 ≤ 50 行
- 错误处理完善
- 不留 .bak，用 git 管理
