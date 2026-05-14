# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.x] - 2026-05-14

### Added
- **Skills 双层目录结构**：skill 目录升级为 `skills/<name>/SKILL.md` + `references/` + `templates/` + `scripts/` + `assets/` 子目录，支持渐进加载
- **Skill 子项路径**：`skill(action='view', name='code-writing/references/python-patterns')` 支持按路径加载子文件
- **Skill search 增强**：搜索范围扩展到 `references/` 和 `templates/`，支持 body 内容全文匹配和位置排序
- **Gateway 管理命令**：`lamix gateway start/stop/restart`
- **CLI Ctrl+C 中断**：CLI 模式下支持中断正在执行的命令

### Changed
- **工具描述更新**：`skill` 工具说明扩展，添加 `skill_scripts` 工具条目
- **Skill Scripts 扫描路径**：从全局 `skills/scripts/` 改为 `skills/*/scripts/`，脚本命名从 `skill_script.py` 改为 `script.py`
- **Skill 索引格式**：索引展示子项数量（如 `references/[2]`），向后兼容平铺 `.md` 文件
- **Shell 工具**：通配符滥用检测（`cat *.py` 等拦截），命令长度限制 100KB

## [0.1.x] - 2025-05-10

### Added
- **核心架构**：LLM Agent daemon + CLI + 飞书 WebSocket adapter
- **记忆系统**：Skills（可复用工作流）、Info（通用知识）、Projects（项目上下文）
- **自学习机制**：任务完成后反思→沉淀→淘汰的闭环
- **Setup Wizard**：首次运行引导配置（LLM 供应商选择、API Key、飞书、用户画像）
- **跨平台支持**：
  - ProcessManager 抽象层（macOS/Linux/Windows）
  - Watchdog 进程守护（优雅终止）
  - Desktop 控制工具（截图+视觉分析+键鼠操作）
  - Windows 安装脚本（schtasks 开机自启、UAC 检测）
- **13 个默认 Skills**：code-review、code-writing、debug、desktop-control、error-reflection、exploration、feishu-doc、grill、project-operation、restart-daemon、safemode、skill-creation-criteria、tdd
- **飞书专属 Skills**：用户配置飞书后自动安装（format、reactions、send-audio）
- **自我审计**：
  - 自动修复安全问题（空目录、散落文件、缺失 frontmatter）
  - Skill 职责重叠检测
  - 定时每日报告
- **Daemon 模式**：
  - LLM 未配置时不退出，提示用户配置
  - Heartbeat + Watchdog 进程守护
  - Boot tasks（重启后自动验证改动）

### Changed
- 命令名统一为 `lamix`，通过子命令分发（cli / gateway / model / update / config）
- `is_config_complete` 检查 api_key 而非 base_url
- 编码：全项目 `open()` 显式 `encoding="utf-8"`（Windows GBK 兼容）
- manager.py 捕获 `NotImplementedError`（Windows add_signal_handler）

### Removed
- 个人信息和硬编码路径（planning/prompts.py、boot_tasks.json）
- 历史迁移脚本（rename_to_lamix.sh）
- 不必要的内部工具引用（Cursor Agent、Hermes）
