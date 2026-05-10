---
created_at: '2026-04-29'
description: 重启 daemon 进程，支持重启前写 boot task 自动验证。
invocation_count: 0
name: restart-daemon
triggers:
- 重启一下
- 重启后自动验证
- restart myself
- boot task
- 改完代码重启一下
- restart daemon
- 重启你自己
- 启动自检
- 重启自己
- restart after code change
- 把自己重启
- 重启
- 启动后自检
- startup self-check
- 改动代码后重启
- 代码改了要重启
- restart yourself
---
# 重启 Daemon

## 重启步骤

### 0. 如果重启后需要验证，先写 boot task

在重启**之前**，用 `file_write` 写 `~/.lamix/boot_tasks.json`。
daemon 启动时会读这个文件，把任务注入 session 让 LLM 执行，然后清空文件。

**boot task 必须是实际验证改动，不是发通知。** 根据改动类型设计具体验证：

| 改动类型 | 验证方式 |
|----------|----------|
| 新命令/功能 | 发送该命令，确认响应符合预期 |
| 修复 bug | 复现原 bug 的步骤，确认不再触发 |
| 配置变更 | 读日志或实际调用，确认新配置生效 |
| 通用 | 发几条消息测试基本交互，确认功能正常 |

示例（通用改动）：
```json
[
  {"task": "验证改动：1) 发几条消息测试基本交互 2) 检查日志确认无异常 3) 汇报验证结果"}
]
```

### 1. macOS（launchd）
```bash
launchctl kickstart -k gui/$(id -u)/com.lamix.gateway && sleep 1 && launchctl load ~/Library/LaunchAgents/com.lamix.gateway.plist
```

### 2. Linux（systemd）
```bash
systemctl --user restart lamix
```

### 3. Windows / 通用 fallback
```bash
# 先停 watchdog 和 daemon，再重新启动
pkill -f "python.*src.daemon" 2>/dev/null
sleep 1
python -m src.daemon &
```

### 4. 验证
```bash
sleep 2 && ps aux | grep 'src.daemon' | grep -v grep
```
确认有新的 PID。

## 注意事项
- 重启后当前会话会断开
- 改代码前先确认无语法错误，避免起不来
- 启动失败时检查 `~/.lamix/logs/` 下的日志
- **危险操作**：重启前确认用户意图，不要擅自重启
- **boot task 核心原则**：验证 > 通知
