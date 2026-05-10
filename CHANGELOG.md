# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-05-10

### Changed

- 命令体系重构：顶层命令从 `lamix-cli` 改为 `lamix`，通过子命令分发
  - `lamix cli` — 交互式 CLI（原 `lamix-cli`）
  - `lamix gateway` — 启动 daemon（原 `python -m src.daemon`）
  - `lamix config` — 显示当前配置（原 `lamix-cli --config`）
  - `lamix model` — 模型管理（占位）
  - `lamix update` — 自更新（占位）
  - `lamix -V` — 显示版本号
- Windows exe 产物从 `lamix-cli.exe` 改为 `lamix.exe`

## [0.1.0] - 2025-05-10

### Added

- **核心架构**：LLM Agent daemon + CLI + 飞书 WebSocket adapter
- **记忆系统**：Skills（可复用工作流）、Info（通用知识）、Projects（项目上下文）
- **自学习机制**：任务完成后反思→沉淀→淘汰的闭环
- **Setup Wizard**：首次运行引导配置（LLM 供应商选择、API Key、飞书、用户画像）
  - prompt_toolkit 上下键选择交互
  - 自动检测 Python 环境
- **跨平台支持**：
  - ProcessManager 抽象层（macOS/Linux/Windows）
  - Watchdog 进程守护（stop.flag 优雅终止）
  - Desktop 控制工具（截图+视觉分析+键鼠操作）
  - Windows 安装脚本（schtasks 开机自启、UAC 检测）
- **13 个默认 Skills**：
  - code-review、code-writing、debug、desktop-control、error-reflection
  - exploration、grill、macos-periodic-task、project-operation
  - restart-daemon、safemode、skill-creation-criteria、tdd
- **飞书专属 Skills**：用户配置飞书后自动安装（format、reactions、send-audio）
- **自我审计**：
  - 自动修复安全问题（空目录、散落文件、缺失 frontmatter）
  - Skill 职责重叠检测（jieba 分词 + 关键词重叠度）
  - 定时每日报告
- **Daemon 模式**：
  - LLM 未配置时不退出，提示用户配置
  - Heartbeat + Watchdog 进程守护
  - Boot tasks（重启后自动验证改动）

### Changed

- 命令名统一为 `lamix`，通过子命令分发（cli / gateway / model / update / config）
- 默认分支 `main` → `master`
- `is_config_complete` 检查 api_key 而非 base_url（修复 wizard 不触发）
- 编码：全项目 `open()` 显式 `encoding="utf-8"`（Windows GBK 兼容）
- manager.py 捕获 `NotImplementedError`（Windows add_signal_handler）

### Removed

- 个人信息和硬编码路径（planning/prompts.py、boot_tasks.json）
- 历史迁移脚本（rename_to_lamix.sh）
- 不必要的内部工具引用（Cursor Agent、Hermes）
