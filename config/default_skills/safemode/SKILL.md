---
created_at: '2026-05-10'
description: 安全模式修复自学习模块导致的故障，包含备份、恢复、安全命令。使用场景：自学习改坏了、卡住了、需要恢复备份。
invocation_count: 0
name: safemode
---
# safemode

**触发词**：safemode, safe mode, 安全模式, 切换到安全模式, 卡住了

**功能**：切换到安全模式，修复自学习模块导致的故障

## 使用场景
当自学习把 agent 改坏了（无法对话、执行命令报错等）时，切换到安全模式进行修复。

## 命令

### /safemode
切换到安全模式，停止主程序，启动 safe_mode.py

### /backup
在安全模式中创建当前状态备份（skills/ 和 memory/）

### /recovery
查看恢复选项

### /recovery list
查看所有可用备份（按时间倒序）

### /recovery restore <name>
恢复到指定备份（会先自动备份当前状态再恢复）

### /recovery restore latest
恢复到最新的备份

### /sh <command>
在安全模式中执行 shell 命令

### /exit
退出安全模式，重启主程序

## 核心路径
`src/safe_mode.py`（safe_mode.py 自身不会被自学习模块修改）
