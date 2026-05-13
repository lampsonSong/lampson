"""测试 selfupdate/updater.py - 自更新"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSelfUpdateConstants:
    """自更新常量测试"""

    def test_protected_files_defined(self):
        """测试受保护文件列表定义"""
        from src.selfupdate.updater import PROTECTED_FILES
        
        assert isinstance(PROTECTED_FILES, set)
        assert "src/cli.py" in PROTECTED_FILES
        assert "src/core/agent.py" in PROTECTED_FILES
        assert "src/core/llm.py" in PROTECTED_FILES

    def test_update_system_prompt_defined(self):
        """测试更新系统提示定义"""
        from src.selfupdate.updater import UPDATE_SYSTEM_PROMPT
        
        assert isinstance(UPDATE_SYSTEM_PROMPT, str)
        assert len(UPDATE_SYSTEM_PROMPT) > 0
        assert "JSON" in UPDATE_SYSTEM_PROMPT


class TestSelfUpdateGit:
    """Git 操作测试"""

    def test_run_git_success(self):
        """测试成功执行 git 命令"""
        from src.selfupdate.updater import _run_git
        
        with patch('subprocess.run') as mock_run:
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout.strip.return_value = "output"
            mock_result.stderr.strip.return_value = ""
            mock_run.return_value = mock_result
            
            code, out, err = _run_git(["status"], Path("/tmp"))
            
            assert code == 0
            assert out == "output"
            assert err == ""

    def test_run_git_failure(self):
        """测试 git 命令失败"""
        from src.selfupdate.updater import _run_git
        
        with patch('subprocess.run') as mock_run:
            mock_result = Mock()
            mock_result.returncode = 1
            mock_result.stdout.strip.return_value = ""
            mock_result.stderr.strip.return_value = "fatal: not a git repository"
            mock_run.return_value = mock_result
            
            code, out, err = _run_git(["status"], Path("/tmp"))
            
            assert code == 1
            assert "not a git repository" in err

    def test_run_git_with_args(self):
        """测试带参数的 git 命令"""
        from src.selfupdate.updater import _run_git
        
        with patch('subprocess.run') as mock_run:
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout.strip.return_value = "modified: src/test.py"
            mock_result.stderr.strip.return_value = ""
            mock_run.return_value = mock_result
            
            _run_git(["diff", "--name-only"], Path("/project"))
            
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args == ["git", "diff", "--name-only"]


class TestSelfUpdateProjectRoot:
    """项目根目录测试"""

    def test_find_project_root(self):
        """测试查找项目根目录"""
        from src.selfupdate.updater import _find_project_root
        
        with patch.object(Path, 'parents', [Path('/'), Path('/home')]):
            with patch('pathlib.Path.__truediv__') as mock_join:
                # 测试向上查找逻辑
                result = _find_project_root()
                
                assert isinstance(result, Path)

    def test_find_project_root_fallback(self):
        """测试查找失败时回退到 cwd"""
        from src.selfupdate.updater import _find_project_root
        
        with patch.object(Path, 'parents', [Path('/')]):
            result = _find_project_root()
            
            assert isinstance(result, Path)


class TestSelfUpdateGitCheck:
    """Git 状态检查测试"""

    def test_check_git_clean_success(self):
        """测试工作区干净"""
        from src.selfupdate.updater import _check_git_clean
        
        with patch('src.selfupdate.updater._run_git') as mock_git:
            mock_git.return_value = (0, "", "")
            
            is_clean, msg = _check_git_clean(Path("/project"))
            
            assert is_clean is True
            assert msg == ""

    def test_check_git_clean_with_changes(self):
        """测试工作区有修改"""
        from src.selfupdate.updater import _check_git_clean
        
        with patch('src.selfupdate.updater._run_git') as mock_git:
            mock_git.return_value = (0, "M  src/test.py", "")
            
            is_clean, msg = _check_git_clean(Path("/project"))
            
            assert is_clean is False
            assert "未提交的修改" in msg

    def test_check_git_clean_error(self):
        """测试 git 命令执行失败"""
        from src.selfupdate.updater import _check_git_clean
        
        with patch('src.selfupdate.updater._run_git') as mock_git:
            mock_git.return_value = (128, "", "fatal: not a git repository")
            
            is_clean, msg = _check_git_clean(Path("/project"))
            
            assert is_clean is False
            assert "无法检查" in msg


class TestSelfUpdateBranch:
    """分支管理测试"""

    def test_get_current_branch(self):
        """测试获取当前分支"""
        from src.selfupdate.updater import _get_current_branch
        
        with patch('src.selfupdate.updater._run_git') as mock_git:
            mock_git.return_value = (0, "feature/new-feature", "")
            
            branch = _get_current_branch(Path("/project"))
            
            assert branch == "feature/new-feature"

    def test_get_current_branch_master(self):
        """测试获取 master 分支"""
        from src.selfupdate.updater import _get_current_branch
        
        with patch('src.selfupdate.updater._run_git') as mock_git:
            mock_git.return_value = (0, "master", "")
            
            branch = _get_current_branch(Path("/project"))
            
            assert branch == "master"

    def test_get_current_branch_empty(self):
        """测试获取空分支名"""
        from src.selfupdate.updater import _get_current_branch
        
        with patch('src.selfupdate.updater._run_git') as mock_git:
            mock_git.return_value = (0, "", "")
            
            branch = _get_current_branch(Path("/project"))
            
            assert branch == "master"  # 应该回退到 master
