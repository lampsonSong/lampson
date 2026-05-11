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

按优先级派发，**严格按顺序执行**，LLM 不可跳过第 1 步直接自己写：

1. **Claude Code** → 加载 `claude-code` skill，交由 Claude Code 编写（**强制优先**）
2. **自己写** → 仅在 Claude Code 不可用时才回退到此方案，直接编写完整可运行的代码（不写 TODO 或 placeholder）

## 3. 验证（必须执行，不跳过）

1. **语法检查**：`python -m py_compile` / `node --check` 等
2. **运行测试**：
   - 优先用 **openclaw** 根据改动内容自动生成测试用例，验证改动不影响主体功能
   - openclaw 不可用时，手动构建测试用例，覆盖正常路径和边界情况
3. **端到端验证**：构造真实场景，确认功能可用

## 4. 重启判断

改动文件在 `src/` 下 → 写 boot task → 重启 daemon → 验证。

## 规范

- 模块级 docstring、type hints
- 函数 ≤ 50 行
- 错误处理完善
- 不留 .bak，用 git 管理
