---
created_at: '2026-05-01'
description: macOS 上设置周期性任务是可复用的多步骤工作流，crontab 在 macOS 上有权限问题
invocation_count: 0
name: macos-periodic-task
triggers:
- periodic
- 周期性
- 每半小时
- schedule
- 每小时
- 每隔
- 定期执行
- crontab
- cron
- 半小时检查
- 定时任务
- launchd
- 每分钟
---

## macOS 周期性定时任务（launchd）

当用户要求在 macOS 上定期执行任务时，使用 launchd 而非 crontab。

### 步骤
1. 编写独立可执行脚本（Python/Shell），确保脚本可独立运行、不依赖交互环境
2. 创建 plist 配置文件，放到 `~/Library/LaunchAgents/`，设置 `StartInterval`（秒，固定间隔）或 `StartCalendarInterval`（cron 风格）
3. `launchctl load <plist路径>` 加载任务
4. 手动触发一次测试：`launchctl start <label>`
5. 查看日志验证：检查 plist 中 stdout/stderr 重定向的日志文件

### 注意
- plist 中程序路径必须写绝对路径（如 `/usr/bin/python3`）
- 环境变量需在 plist 的 `EnvironmentVariables` 中显式设置
- 修改 plist 后需先 `unload` 再 `load`
- 用户说「每 X 分钟/小时执行」时触发此 skill，不要用 sleep 循环
