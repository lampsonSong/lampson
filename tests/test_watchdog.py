"""测试 watchdog.py - 进程守护"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWatchdogModule:
    def test_module_imports(self):
        from src import watchdog
        assert hasattr(watchdog, '_get_lamix_bin')

    def test_get_lamix_bin_returns_string_or_none(self):
        from src.watchdog import _get_lamix_bin
        result = _get_lamix_bin()
        assert result is None or isinstance(result, str)

    def test_constants_defined(self):
        from src.watchdog import HEARTBEAT_TIMEOUT, WATCHDOG_INTERVAL
        assert isinstance(HEARTBEAT_TIMEOUT, (int, float))
        assert isinstance(WATCHDOG_INTERVAL, (int, float))
