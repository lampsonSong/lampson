---
name: code-writing
description: 写代码、创建或编辑代码文件
triggers:
  - 写代码
  - 写一个
  - 创建文件
  - 编写
  - implement
  - 实现
  - 新建
  - 生成代码
---

## code-writing 技能

### 描述
帮助用户编写、创建和编辑代码文件。

### 步骤
1. 理解用户需求，确认编程语言和目标文件路径
2. 如果文件已存在，先用 file_read 读取现有内容
3. 生成完整、可运行的代码（不写 TODO 或 placeholder）
4. 用 file_write 工具将代码写入目标文件
5. 用 shell 工具验证语法（如 python -m py_compile 或 node --check）
6. 向用户汇报完成情况，说明文件位置

### 代码规范
- 添加模块级 docstring
- 使用 type hints（Python）
- 函数长度尽量不超过 50 行
- 错误处理要完善

### 注意事项
- 写入前确认路径正确，避免覆盖重要文件
- 危险操作（覆盖已有文件）需先提示用户
