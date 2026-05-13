"""测试 safe_mode.py - 安全模式"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSafeModePaths:
    def test_lamix_dir_defined(self):
        from src.safe_mode import LAMIX_DIR
        assert isinstance(LAMIX_DIR, Path)

    def test_config_path_defined(self):
        from src.safe_mode import CONFIG_PATH
        assert isinstance(CONFIG_PATH, Path)

    def test_critical_dirs_defined(self):
        from src.safe_mode import CRITICAL_DIRS
        assert isinstance(CRITICAL_DIRS, list)


class TestSafeModeFunctions:
    def test_resolve_daemon_cmd(self):
        from src.safe_mode import _resolve_daemon_cmd
        cmd = _resolve_daemon_cmd()
        assert isinstance(cmd, list)

    def test_load_config(self):
        from src.safe_mode import load_config
        result = load_config()
        assert isinstance(result, dict)
