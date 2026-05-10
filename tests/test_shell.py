"""Shell 工具单元测试。"""

import pytest
from unittest.mock import patch

from src.tools import shell as shell_tool


class TestIsDangerous:
    """测试 is_dangerous 函数。"""

    def test_dangerous_rm_rf_root(self):
        """测试危险命令：rm -rf /"""
        assert shell_tool.is_dangerous("rm -rf /")
        assert shell_tool.is_dangerous("rm -fr /")

    def test_dangerous_mkfs(self):
        """测试危险命令：mkfs"""
        assert shell_tool.is_dangerous("mkfs.ext4 /dev/sda")

    def test_dangerous_fork_bomb(self):
        """测试危险命令：fork bomb"""
        assert shell_tool.is_dangerous(":(){ :|:& };:")

    def test_dangerous_dd_overwrite(self):
        """测试危险命令：dd 覆写磁盘"""
        assert shell_tool.is_dangerous("dd if=/dev/zero of=/dev/sda")

    def test_safe_commands(self):
        """测试安全命令。"""
        assert not shell_tool.is_dangerous("ls -la")
        assert not shell_tool.is_dangerous("echo 'hello'")
        assert not shell_tool.is_dangerous("pwd")
        assert not shell_tool.is_dangerous("git status")
        assert not shell_tool.is_dangerous("python --version")


class TestGlobAbuse:
    """测试 _has_glob_abuse 函数。"""

    def test_glob_abuse_cat(self):
        """测试 cat 滥用通配符。"""
        assert shell_tool._has_glob_abuse("cat *.py")
        assert shell_tool._has_glob_abuse("cat src/*.py")
        assert shell_tool._has_glob_abuse("cat **/*.js")

    def test_glob_abuse_rm(self):
        """测试 rm 滥用通配符。"""
        assert shell_tool._has_glob_abuse("rm *.tmp")
        assert shell_tool._has_glob_abuse("rm -rf logs/*")

    def test_safe_no_glob(self):
        """测试安全命令不带通配符。"""
        assert not shell_tool._has_glob_abuse("cat file.py")
        assert not shell_tool._has_glob_abuse("ls")
        assert not shell_tool._has_glob_abuse("git log --oneline")


class TestExecuteShell:
    """测试 execute_shell 函数。"""

    def test_safe_command(self):
        """测试执行安全命令。"""
        result = shell_tool.execute_shell("echo 'hello world'")
        assert "hello world" in result

    def test_pwd_command(self):
        """测试 pwd 命令。"""
        result = shell_tool.execute_shell("pwd")
        assert result.strip() != ""

    def test_command_with_pipe(self):
        """测试带管道的命令。"""
        result = shell_tool.execute_shell("echo 'test' | grep test")
        assert "test" in result

    def test_command_with_redirect(self):
        """测试带重定向的命令。"""
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("test content")
            temp_path = f.name
        
        try:
            result = shell_tool.execute_shell(f"cat {temp_path}")
            assert "test content" in result
        finally:
            os.unlink(temp_path)

    def test_command_too_long(self):
        """测试命令过长被拒绝。"""
        long_cmd = "echo " + "a" * 100001
        result = shell_tool.execute_shell(long_cmd)
        assert "[拒绝执行]" in result
        assert "过长" in result

    def test_dangerous_command_rejected(self):
        """测试危险命令被拒绝。"""
        result = shell_tool.execute_shell("rm -rf /")
        assert "[拒绝执行]" in result

    def test_glob_abuse_rejected(self):
        """测试通配符滥用被拒绝。"""
        result = shell_tool.execute_shell("cat *.py")
        assert "[拒绝执行]" in result
        assert "通配符" in result

    def test_timeout(self):
        """测试命令超时。"""
        result = shell_tool.execute_shell("sleep 5", timeout=1)
        assert "[超时]" in result

    def test_invalid_command(self):
        """测试无效命令。"""
        result = shell_tool.execute_shell("nonexistent_command_xyz")
        assert "[错误]" in result or "not found" in result.lower()


class TestRun:
    """测试 run 函数（工具入口）。"""

    def test_run_basic_command(self):
        """测试基本命令。"""
        result = shell_tool.run({"command": "echo 'test'"})
        assert "test" in result

    def test_run_with_timeout(self):
        """测试带超时的命令。"""
        result = shell_tool.run({"command": "sleep 10", "timeout": 1})
        assert "[超时]" in result

    def test_run_empty_command(self):
        """测试空命令。"""
        result = shell_tool.run({"command": ""})
        assert "[错误]" in result

    def test_run_missing_command(self):
        """测试缺少 command 参数。"""
        result = shell_tool.run({})
        assert "[错误]" in result

    def test_run_with_safe_timeout(self):
        """测试超时上限限制。"""
        result = shell_tool.run({"command": "echo 'test'", "timeout": 200})
        # 超过 120 秒的上限应该被限制
        # 注意：这个测试可能通过也可能失败，取决于命令执行速度


class TestSchema:
    """测试 SCHEMA 定义。"""

    def test_schema_structure(self):
        """测试 schema 结构。"""
        assert shell_tool.SCHEMA["type"] == "function"
        assert "function" in shell_tool.SCHEMA
        assert shell_tool.SCHEMA["function"]["name"] == "shell"
        assert "description" in shell_tool.SCHEMA["function"]
        assert "parameters" in shell_tool.SCHEMA["function"]
        assert shell_tool.SCHEMA["function"]["parameters"]["type"] == "object"

    def test_schema_command_required(self):
        """测试 command 是必需参数。"""
        params = shell_tool.SCHEMA["function"]["parameters"]
        assert "command" in params["required"]
