# 文件搜索经验

## 教训

在 home 目录(`~`)下搜索时，**不要用**：
- `search_files` 工具的宽泛 glob 模式（如 `**/*hermes*`）
- `find ~ -maxdepth N -name "*xxx*"` — ~ 展开后路径过长

**应该用**：
- `ls ~/ | grep xxx` — 快速预览
- `ls -la ~/.xxx/` — 直接列出目标目录

## 原因

- home 目录文件多、层级深
- search_files 和 find 遍历慢，容易超时
- 直接 ls 更高效
