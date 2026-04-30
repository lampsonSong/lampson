"""多平台架构核心模块。

导出：
  - PlatformMessage / BasePlatformAdapter（base.py）
  - PlatformManager（manager.py）
  - ContextSnapshot / BackgroundTaskManager / BackgroundTask（background.py）
"""

from src.platforms.base import PlatformMessage, BasePlatformAdapter
from src.platforms.manager import PlatformManager
from src.platforms.background import (
    ContextSnapshot,
    BackgroundTaskManager,
    BackgroundTask,
)

__all__ = [
    "PlatformMessage",
    "BasePlatformAdapter",
    "PlatformManager",
    "ContextSnapshot",
    "BackgroundTaskManager",
    "BackgroundTask",
]
