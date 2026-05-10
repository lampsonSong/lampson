# Lamix Windows 移植设计文档

> 目标：Lamix 在 Windows 上完整运行，核心功能与 macOS 一致。

## 实施进度

| 阶段 | 状态 | 提交 |
|------|------|------|
| Phase 0: 环境准备 | ⏳ 待做（需要 Windows 机器） | - |
| Phase 1: ProcessManager 抽象 + watchdog 改造 | ✅ 已完成 | 5ae2c78 |
| Phase 2: Desktop 工具 Windows UI 查询 | ✅ 已完成 | dcf9dd2 |
| Phase 3: Shell/Search 工具 + 编码修复 | ✅ 已完成 | dcf9dd2 |
| Phase 4: 安装脚本 | ✅ 已完成 | 待提交 |
| Phase 5: 端到端测试 | ⏳ 待做（需要 Windows 机器） | - |

> Phase 1-4 的代码均在 macOS 上编写并通过语法检查和单元测试（474 passed）。
> Phase 0 和 Phase 5 需要在实际 Windows 环境中验证。


## 1. 平台差异分析

### 1.1 不可直接移植的部分

| 模块 | macOS 实现 | Windows 等价方案 | 工作量 |
|------|------------|-----------------|--------|
| **Watchdog 进程管理** | `launchctl kickstart` | `schtasks` 或 NSSM | 中 |
| **Watchdog 进程发现** | `pgrep -f "python.*src.daemon"` | `wmic process where` 或 `Get-Process` | 中 |
| **Watchdog 进程终止** | `os.kill(pid, SIGTERM)` | `taskkill /PID xxx /T` | 小 |
| **Desktop UI 查询** | `osascript` AppleScript | PowerShell UI Automation 或 pywin32 | 中 |
| **Shell 命令** | bash | cmd / PowerShell（命令语法不同） | 小 |

### 1.2 天然跨平台（无需修改）

- 核心 agent/LLM/adapters/skills/memory：纯 Python ✅
- 飞书 WebSocket：`lark-oapi` 跨平台 ✅
- CLI REPL：`prompt_toolkit` 跨平台 ✅
- 定时任务：`APScheduler` 跨平台 ✅
- HTTP 请求：`httpx` 跨平台 ✅
- 配置文件 `~/.lamix/config.yaml`：`Path.home()` ✅

### 1.3 条件编译

| 模块 | 条件 | 说明 |
|------|------|------|
| `desktop.py` - osascript | `sys.platform == "darwin"` | 其他平台用 Windows API |
| `rg` 路径探测 | Linux/macOS 路径 vs Windows 路径 | 加 winreg / shutil 探测 |
| watchdog 平台抽象 | `sys.platform` 检测 | 抽象 `ProcessManager` 接口 |
| 信号处理 | `signal.SIGTERM` | Windows Python 3.10+ 支持 |
| `add_signal_handler` | `hasattr(loop, "add_signal_handler")` | Windows 不支持，try/except 兜底 |

---

## 2. 架构设计：平台抽象层

### 2.1 新增 `src/platforms/windows/`

```
src/
  platforms/
    windows/              # 新增
      __init__.py
      process_manager.py  # Windows 进程管理
      desktop.py          # Windows 桌面控制
      service.py          # Windows 服务注册
    process_manager.py    # 抽象基类
```

### 2.2 `ProcessManager` 抽象接口

```python
# src/platforms/process_manager.py
from abc import ABC, abstractmethod

class ProcessManager(ABC):
    @abstractmethod
    def find_process(self, name_pattern: str) -> int | None:
        """根据进程名模式找到 pid，不存在返回 None。"""
        ...

    @abstractmethod
    def kill_process(self, pid: int, graceful: bool = True) -> bool:
        """终止进程，graceful=True 先发 SIGTERM 再强杀。"""
        ...

    @abstractmethod
    def restart_service(self, service_name: str) -> bool:
        """重启后台服务。"""
        ...
```

实现：

| 类 | 文件 | 平台 |
|----|------|------|
| `PosixProcessManager` | `_process_manager.py`（改名） | Linux/macOS |
| `WindowsProcessManager` | `windows/process_manager.py` | Windows |

### 2.3 入口选择

```python
# src/watchdog.py 改动
import sys

if sys.platform == "win32":
    from src.platforms.windows.process_manager import WindowsProcessManager as ProcessMgr
else:
    from src.platforms.posix_process_manager import PosixProcessManager as ProcessMgr
```

---

## 3. Watchdog 改造

### 3.1 当前 watchdog 职责

```
Watchdog 进程（独立进程）
  ├── 定期检查 daemon 心跳（文件 + os.kill(pid, 0)）
  ├── 超时 → 重启 daemon（launchctl kickstart）
  └── 清理过时心跳文件
```

### 3.2 改造后

```
Watchdog 进程（独立进程）
  ├── 定期检查 daemon 心跳（文件 + ProcessManager.is_alive(pid)）
  ├── 超时 → 重启 daemon（ProcessManager.restart_service()）
  └── 清理过时心跳文件
```

所有 macOS/Linux 特有调用下沉到 `ProcessManager`，watchdog 本身不感知平台。

### 3.3 Windows 重启策略

Windows 没有 launchd，两种方案：

**方案 A：Task Scheduler（推荐）**
- 创建 Lamix 定时任务（每分钟执行，启动条件：有进程未运行）
- watchdog 超时 → 删除旧任务 → 重建新任务
- 缺点：需要管理员权限

**方案 B：简单进程拉起**
- watchdog 超时 → 直接 `subprocess.Popen([sys.executable, daemon_path])`
- 维护 `daemon.pid` 文件
- 缺点：开机不自启，需额外引导

**选定方案 B**（简单稳定），开机自启动通过 Windows 任务计划程序一次性设置，不在 watchdog 内处理。

---

## 4. Desktop 工具改造

### 4.1 当前模块

| 工具 | macOS | Windows | 状态 |
|------|-------|---------|------|
| 截图 | `pyautogui.screenshot()` | 同样可用 | ✅ 跨平台 |
| 鼠标/键盘 | `pyautogui` | 同样可用 | ✅ 跨平台 |
| UI 元素查询 | `osascript` AppleScript | PowerShell UI Automation | ❌ 需改造 |
| 屏幕信息 | `pyautogui.size()` | 同样可用 | ✅ 跨平台 |

### 4.2 Windows UI 查询方案

用 PowerShell UI Automation（Windows 内置，无需安装）：

```python
# windows 下的 query_ui_element
script = f'''
Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
$app = Get-Process -Name "{app_name}" | Select-Object -First 1
if (-not $app) {{ return "未找到进程: {app_name}" }}
$root = [UIAutomationClient.AutomationElement]::RootElement
$condition = New-Object UIAutomationClient.PropertyCondition(
    [UIAutomationClient.AutomationElement]::ProcessIdProperty, $app.Id)
$elements = $root.FindAll([UIAutomationClient.TreeScope]::Children, $condition)
# ... 遍历元素返回
'''
result = subprocess.run(["powershell", "-Command", script], capture_output=True, text=True)
```

### 4.3 Desktop 模块结构

```python
# src/tools/desktop.py
import sys

if sys.platform == "win32":
    from src.platforms.windows.desktop import run as windows_run
    # 导出 windows_run，替换 SCHEMAS
elif sys.platform == "darwin":
    # 保持现有实现
    ...
else:
    # Linux: 可选支持（pyautogui + xdotool）
    ...
```

> 注：`desktop_query_ui` 在 Windows 下依赖 `powershell`，无 macOS Accessibility 的细粒度，可能降级为只按进程名查找顶层窗口。

---

## 5. Shell 工具

现有 `src/tools/shell.py` 中的 `subprocess.run(command, shell=True)` 大部分在 Windows 下可用，但：

| 命令 | macOS | Windows | 处理 |
|------|-------|---------|------|
| `launchctl` | ✅ | ❌ | 检测到时报错"仅 macOS 可用" |
| 路径 `/tmp/` | Linux/macOS | ❌ | 改为 `tempfile.gettempdir()` |
| 命令行工具路径 | `/opt/homebrew/bin/...` | `C:\Program Files\...` | 动态探测 |

---

## 6. 安装脚本设计

### 6.1 `scripts/install_windows.py`

```python
"""Windows 安装脚本：注册服务、安装依赖、拉起 daemon。"""
import subprocess, sys, os

def install():
    # 1. 检查 Python 版本
    assert sys.version_info >= (3, 11), "需要 Python 3.11+"
    
    # 2. 安装依赖（如果有缺失）
    subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], check=True)
    
    # 3. 注册 Windows 任务计划（开机自启）
    # schtasks /create /tn "Lamix" /tr "python lamix" /sc onlogon /rl limited
    task_xml = build_task_xml()
    with open(os.path.expandvars("%TEMP%\\lamix_task.xml"), "w") as f:
        f.write(task_xml)
    subprocess.run(["schtasks", "/create", "/tn", "Lamix", "/xml", f"..."], check=True)
    
    # 4. 拉起 daemon
    subprocess.Popen([sys.executable, "-m", "src.daemon"], ...)
    
    print("安装完成！")
```

---

## 7. 文件改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/watchdog.py` | 修改 | ProcessManager 抽象，所有平台调用下沉 |
| `src/tools/shell.py` | 修改 | 加 `launchctl` 检测、路径处理 |
| `src/tools/search.py` | 修改 | 加 Windows rg 路径探测 |
| `src/tools/desktop.py` | 修改 | 拆分平台分支 |
| `src/platforms/process_manager.py` | 新增 | 抽象基类 |
| `src/platforms/posix_process_manager.py` | 新增 | 现有 watchdog 的进程逻辑移入 |
| `src/platforms/windows/process_manager.py` | 新增 | Windows 进程管理实现 |
| `src/platforms/windows/desktop.py` | 新增 | Windows UI 查询 |
| `src/platforms/windows/service.py` | 新增 | Windows 服务注册 |
| `scripts/install_windows.py` | 新增 | Windows 安装引导 |

---

## 8. 风险与限制

| 风险 | 影响 | 缓解 |
|------|------|------|
| Windows UI Automation 精度不如 macOS Accessibility | `desktop_query_ui` 功能降级 | 降级为按进程名查窗口，提示用户 |
| watchdog 进程管理需要区分进程名 | Windows 进程名匹配不准 | 用命令行完整路径匹配 |
| Windows 权限问题（UAC） | 无法操作某些系统进程 | 降级为普通用户模式 |
| 开机自启需要管理员权限 | 限制普通用户使用 | 引导用户手动设置开机任务 |

---

## 9. 实施计划

**Phase 1：平台抽象（1天）**
- 新增 `ProcessManager` 抽象接口
- 迁移现有 watchdog 逻辑到 `PosixProcessManager`
- 实现 `WindowsProcessManager`
- 修改 watchdog 使用抽象接口

**Phase 2：Desktop 工具（0.5天）**
- 拆分 `desktop.py` 平台分支
- 实现 Windows UI 查询（PowerShell）

**Phase 3：Shell 工具（0.5天）**
- 路径动态探测
- `launchctl` 检测报错

**Phase 4：安装脚本（1天）**
- `install_windows.py`
- Windows 任务计划注册

**总计：3天**

---

## 10. 测试策略

- Windows 机器上跑完整测试套件
- watchdog 进程管理：在无 daemon、有 daemon、daemon 崩溃三种场景下验证
- Desktop 工具：截图、鼠标、键盘基础功能测试
- `desktop_query_ui`：macOS 降级提示验证
- 安装脚本：全新 Windows 环境从零安装测试

---

## 附：Hermes Review 补充

> 来源：Hermes Agent review (2026-05-10)

### 关键问题（设计缺陷，必须在实施前解决）

**问题 1：Windows 优雅进程终止**
- `os.kill(pid, SIGTERM)` 在 Windows 下实际是 TerminateProcess（硬杀）
- 方案：在 heartbeat 文件中加入 `pid_file` 路径，daemon 定期检查 `stop.flag` 文件是否存在，存在则自行退出。watchdog 通过写文件而不是发信号来优雅终止。

**问题 2：watchdog 自身守护**
- macOS 下 watchdog 由 launchd 管理，Windows 下没有等价物
- 方案：开机自启动用 schtasks（`/sc onlogon`），watchdog 崩溃后由 Windows 任务计划器的"失败后重试"机制兜底

**问题 3：daemon 化的 detach 机制**
- `subprocess.Popen` 拉起的子进程可能随 watchdog 退出而被连带杀掉
- 方案：使用 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` 创建标志，或通过 `schtasks` 管理 daemon，watchdog 不直接拉进程

**问题 4：自更新文件锁定**
- Windows 上正在运行的文件无法被覆盖写入
- 方案：自更新下载到 `~/.lamix/update/` 临时目录，完成后写 `update_ready.flag`，daemon 下次启动时检测到 flag 则替换自身并重启

**问题 5：安装脚本 UAC 提权和多 Python 环境**
- schtasks 创建需要管理员权限
- 方案：安装脚本检测是否管理员权限，非管理员则提示用 `runas` 或 UAC 引导
- Windows Python 环境复杂（Microsoft Store 版、python.org、Anaconda），通过 `where python` 或 `py -0` 探测，给用户明确指引

### 补充风险项

| 遗漏风险 | 缓解方案 |
|---------|---------|
| Windows 路径 `\` vs `/` | 统一用 `pathlib.Path`，禁止手动字符串拼接 |
| 文件锁定（自更新） | 临时目录下载 + flag 文件机制 |
| 编码（GBK 默认） | 显式 `encoding="utf-8"`，环境变量 `PYTHONIOENCODING=utf-8` |
| MAX_PATH 260 字符 | 项目路径避免过深，启用 Windows 长路径（注册表） |
| pyautogui 无法操作 UAC 窗口 | 文档说明限制，需管理员运行 |
| 进程组管理 | `CREATE_NEW_PROCESS_GROUP` flag |
| 日志归属 | daemon stdout/stderr 重定向到 `~/.lamix/logs/daemon.log` |
| APScheduler 时区 | 显式设置 `scheduler.configure(timezone="Asia/Shanghai")` |
| Windows Terminal 编码 | prompt_toolkit 建议在 Windows Terminal 下运行 |

### 补充架构决策

**Named Pipe 替代 Unix Socket（如果用到）：**
- 如果 agent 内部有 Unix domain socket 通信，改为 TCP loopback `127.0.0.1:port`
- Windows 不支持 Unix socket

**UIElement 统一返回格式：**
```python
@dataclass
class UIElement:
    role: str       # button, textfield, ...
    name: str       # 元素名称
    position: tuple[int, int]  # (x, y)
    size: tuple[int, int]     # (w, h)
    value: str      # 当前值（可选）
    precision: str  # "full" | "window_only"，标记查询精度
```

**实施计划修正：**

| 阶段 | 内容 | 修正工时 |
|------|------|---------|
| Phase 0 | Windows 开发/测试环境 + CI 配置 | 0.5 天 |
| Phase 1 | ProcessManager 抽象 + watchdog 改造 + 优雅终止机制 | 2 天 |
| Phase 2 | Desktop 工具拆分 + Windows UI 查询 | 0.5 天 |
| Phase 3 | Shell 工具 + 路径处理 | 0.5 天 |
| Phase 4 | 安装脚本（含 UAC、Python 环境检测、卸载流程） | 1.5 天 |
| Phase 5 | 端到端集成测试 + 自更新测试 | 1 天 |
| **合计** | | **6 天** |
