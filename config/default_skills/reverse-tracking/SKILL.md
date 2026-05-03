---
name: reverse-tracking
description: 反向追踪定位代码/项目。使用场景：找某个命令对应的代码、定位工具实现、从报错信息反向追踪到源码。
---

# Reverse Tracking

从已知线索反向追踪到目标代码，不做全盘搜索。

## 工作流

1. **定位入口** — `which` 找可执行文件路径
2. **看头部** — `head` 看 shebang 或 import
3. **列目录** — `ls` 看项目结构
4. **顺藤摸瓜** — 根据上一步发现继续深入

## 原则

- 从线索开始，逐步缩小范围
- 每步只做最小探测（`head -5`、`ls`、`grep`）
- 不要一开始就 `find /` 或 `rg -r` 全盘搜
- `find` 必须加 `-maxdepth` 限制
