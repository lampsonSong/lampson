---
created_at: '2026-05-10'
description: 通过截图+视觉分析+键鼠操作控制桌面。用于需要 GUI 操作但无法用 API/CLI 完成的场景。
invocation_count: 0
name: desktop-control
triggers:
- 操作浏览器
- 控制桌面
- 截图操作
- 键盘鼠标
- 打开网页
- 点击按钮
---

# 桌面控制

通过 pyautogui（键鼠）+ 视觉模型（截图分析）操作桌面。

## 工作流

1. **先确认目标窗口**：截图 → 视觉分析确认当前状态
2. **切换到目标应用**：用 `open -a "App Name"` 或 Spotlight（Cmd+Space）
3. **执行操作**：键鼠 API（hotkey/type_text/click）
4. **验证结果**：再次截图确认

## 关键函数

```python
from src.tools.desktop import (
    take_screenshot, take_screenshot_region,
    click, move_to, double_click, right_click, scroll, drag,
    type_text, press_key, key_down, key_up, hotkey,
    get_screen_info,
)
from src.tools.vision import analyze_image
```

## 踩坑记录

### 浏览器全屏问题
- 浏览器全屏时地址栏自动隐藏，`Cmd+L` 无法可靠聚焦地址栏
- **解决方案**：不要操作浏览器 GUI，改用 CLI 命令或 API

### 视觉分析的局限
- 视觉模型只能分析截图内容，无法识别 UI 元素的交互属性
- 全屏应用中看不到菜单栏和地址栏，截图分析会误判

### 输入可靠性
- `type_text` 输入中文可能有问题，优先用英文路径和 URL
- `open` 命令比 GUI 操作可靠得多，优先使用

## 优先级策略

能用 CLI/API 完成的事，不要用桌面控制：
1. **CLI 命令** > 桌面控制
2. **HTTP API** > 操作浏览器 UI
3. **osascript** > 截图+视觉分析+键鼠模拟
