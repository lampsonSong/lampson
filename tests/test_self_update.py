"""测试 selfupdate/updater.py - 自更新"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSelfUpdateConstants:
    def test_protected_files_defined(self):
        from src.selfupdate.updater import PROTECTED_FILES
        assert isinstance(PROTECTED_FILES, set)
        assert "src/cli.py" in PROTECTED_FILES

    def test_update_system_prompt_defined(self):
        from src.selfupdate.updater import UPDATE_SYSTEM_PROMPT
        assert isinstance(UPDATE_SYSTEM_PROMPT, str)


class TestSelfUpdateFunctions:
    def test_find_project_root(self):
        from src.selfupdate.updater import _find_project_root
        result = _find_project_root()
        assert isinstance(result, Path)
