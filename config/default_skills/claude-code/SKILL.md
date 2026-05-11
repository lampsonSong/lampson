---
name: claude-code
description: 使用 Claude Code CLI 编写代码。用于实现功能、创建文件、修改代码、修复 bug。
---

# Claude Code

由 code-writing skill 调用，实际执行编码任务。

## 1. 准备

- 确认 `claude` 命令可用（`which claude`）
- Claude Code 安装在 `~/.nvm/versions/node/*/bin/claude`
- 如有需要，先将 PATH 补充：`export PATH="$HOME/.nvm/versions/node/$(ls ~/.nvm/versions/node/ | head -1)/bin:$PATH"`

## 2. 执行编码

将需要编写的代码需求和文件路径作为 prompt 传给 Claude Code：

```bash
claude -p "具体需求描述" --allowedTools "Read,Write,Edit,Bash,Glob"
```

或通过标准输入传递较长描述：

```bash
echo "详细需求" | claude -p "$(cat)" --allowedTools "Read,Write,Edit,Bash,Glob"
```

## 3. 验证

Claude Code 执行完成后：
- 检查生成的文件是否符合预期
- 运行语法检查 `python -m py_compile`
- 运行测试 `python -m pytest`
