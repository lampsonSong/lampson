"""共享业务常量。

本文件存放跨模块共享的魔数/常量。
模块私有的常量（如 planning 的重试次数、boot_tasks 上限）保留在原文件。
路径类常量定义在 src/core/config.py 中，不在此重复。
"""

# ── Session ──
IDLE_TIMEOUT_MINUTES: int = 180  # session 空闲超时自动重置（分钟）
IDLE_TIMEOUT_SECONDS: int = IDLE_TIMEOUT_MINUTES * 60

# ── 心跳 / Watchdog ──
HEARTBEAT_INTERVAL: int = 10    # 心跳文件写入间隔（秒）
HEARTBEAT_TIMEOUT: int = 30     # 无心跳则认为死亡（秒）
WATCHDOG_INTERVAL: int = 10     # watchdog 检查频率（秒）

# ── 审计 ──
DEFAULT_AUDIT_HOUR: int = 4        # 默认审计触发时间 - 小时
DEFAULT_AUDIT_MINUTE: int = 0      # 默认审计触发时间 - 分钟

# ── 飞书 ──
FEISHU_TOKEN_TTL: int = 7000  # token 有效期余量（秒），官方7200留200余量

# ── 记忆 ──
MEMORY_SIZE_LIMIT: int = 500  # MEMORY.md 最小留存字符数

# ── 报告 ──
REPORT_MAX_LENGTH: int = 4000  # 审计/报告截断长度（字符）
