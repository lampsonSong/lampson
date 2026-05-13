"""测试 cli.py - CLI 入口"""
import pytest
import sys
import py_compile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCLIModule:
    def test_cli_module_valid_python(self):
        cli_path = Path(__file__).parent.parent / "src" / "cli.py"
        try:
            py_compile.compile(str(cli_path), doraise=True)
            assert True
        except py_compile.PyCompileError:
            assert False

    def test_cli_module_exists(self):
        from src import cli
        assert cli.__name__ == 'src.cli'
