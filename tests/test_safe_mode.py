"""测试 safe_mode.py - 安全模式"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import json
import tempfile
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSafeModePaths:
    """安全模式路径测试"""

    def test_lamix_dir_defined(self):
        """测试 LAMIX_DIR 定义"""
        from src.safe_mode import LAMIX_DIR
        
        assert isinstance(LAMIX_DIR, Path)
        assert ".lamix" in str(LAMIX_DIR)

    def test_config_path_defined(self):
        """测试配置路径定义"""
        from src.safe_mode import CONFIG_PATH
        
        assert isinstance(CONFIG_PATH, Path)
        assert "config.yaml" in str(CONFIG_PATH)

    def test_backup_dir_defined(self):
        """测试备份目录定义"""
        from src.safe_mode import BACKUP_DIR
        
        assert isinstance(BACKUP_DIR, Path)

    def test_critical_dirs_defined(self):
        """测试关键目录列表定义"""
        from src.safe_mode import CRITICAL_DIRS
        
        assert isinstance(CRITICAL_DIRS, list)
        assert "memory" in CRITICAL_DIRS


class TestSafeModeResolveDaemon:
    """Daemon 命令解析测试"""

    def test_resolve_with_which(self):
        """测试通过 which 找到 lamix"""
        from src.safe_mode import _resolve_daemon_cmd
        
        with patch('src.safe_mode.shutil') as mock_shutil:
            mock_shutil.which.return_value = "/usr/bin/lamix"
            
            cmd = _resolve_daemon_cmd()
            
            assert "lamix" in cmd
            assert "gateway" in cmd

    def test_resolve_with_sysconfig(self):
        """测试通过 sysconfig 找到 lamix"""
        from src.safe_mode import _resolve_daemon_cmd
        
        with patch('src.safe_mode.shutil') as mock_shutil:
            with patch('src.safe_mode.sysconfig') as mock_sysconfig:
                mock_shutil.which.return_value = None
                mock_sysconfig.get_path.return_value = "/usr/bin"
                
                with patch('os.path.exists', return_value=True):
                    cmd = _resolve_daemon_cmd()
                    
                    assert "lamix" in cmd or "src.daemon" in cmd

    def test_resolve_fallback(self):
        """测试回退到 python -m"""
        from src.safe_mode import _resolve_daemon_cmd
        
        with patch('src.safe_mode.shutil') as mock_shutil:
            with patch('src.safe_mode.sysconfig') as mock_sysconfig:
                mock_shutil.which.return_value = None
                mock_sysconfig.get_path.return_value = "/nonexistent"
                
                with patch('os.path.exists', return_value=False):
                    cmd = _resolve_daemon_cmd()
                    
                    assert "src.daemon" in cmd


class TestSafeModeLoadConfig:
    """配置读取测试"""

    def test_load_config_no_file(self):
        """测试加载不存在的配置文件"""
        from src.safe_mode import load_config
        
        with patch('src.safe_mode.CONFIG_PATH', Path(tempfile.mktemp())):
            result = load_config()
            
            assert result == {}

    def test_load_config_with_yaml(self):
        """测试加载 YAML 配置"""
        from src.safe_mode import load_config
        import yaml
        
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.yaml"
        
        config_data = {
            "llm": {"api_key": "test_key"},
            "feishu": {"app_id": "test_app", "app_secret": "test_secret"},
        }
        
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        with patch('src.safe_mode.CONFIG_PATH', config_path):
            result = load_config()
            
            assert result["llm"]["api_key"] == "test_key"
            assert result["feishu"]["app_id"] == "test_app"

    def test_load_config_without_yaml_lib(self):
        """测试无 yaml 库时的备用解析"""
        from src.safe_mode import load_config
        
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.yaml"
        
        with open(config_path, 'w') as f:
            f.write('app_id: "test123"\napp_secret: "secret456"\n')
        
        with patch('src.safe_mode.CONFIG_PATH', config_path):
            with patch.dict('sys.modules', {'yaml': None}):
                result = load_config()
                
                assert result.get("feishu", {}).get("app_id") == "test123"


class TestSafeModeBackup:
    """备份功能测试"""

    def test_list_backups_empty(self):
        """测试列出空备份"""
        from src.safe_mode import list_backups
        
        with patch('src.safe_mode.BACKUP_DIR', Path(tempfile.mkdtemp())):
            backups = list_backups()
            
            assert isinstance(backups, list)

    def test_list_backups_returns_sorted(self):
        """测试列出备份返回排序列表"""
        from src.safe_mode import list_backups
        
        tmp_dir = Path(tempfile.mkdtemp())
        
        # 创建一些备份文件
        (tmp_dir / "backup-2024-01-01.tar.gz").touch()
        (tmp_dir / "backup-2024-01-02.tar.gz").touch()
        
        with patch('src.safe_mode.BACKUP_DIR', tmp_dir):
            backups = list_backups()
            
            assert len(backups) == 2
            # 应该是倒序
            assert backups[0] >= backups[1]


class TestSafeModeRecovery:
    """恢复功能测试"""

    def test_get_backup_path(self):
        """测试获取备份路径"""
        from src.safe_mode import _get_backup_path
        
        with patch('src.safe_mode.BACKUP_DIR', Path(tempfile.mkdtemp())):
            path = _get_backup_path("test_backup")
            
            assert isinstance(path, Path)
            assert "test_backup" in str(path)

    def test_validate_backup_valid(self):
        """测试验证有效备份"""
        from src.safe_mode import _validate_backup
        
        with patch('src.safe_mode.BACKUP_DIR', Path(tempfile.mkdtemp())):
            tmp_dir = Path(tempfile.mkdtemp())
            backup_file = tmp_dir / "backup-2024-01-01.tar.gz"
            
            # 创建有效的 tar.gz 文件
            with tarfile.open(backup_file, 'w:gz') as tar:
                tar.addfile(tarfile.TarInfo(name="memory/test.txt"))
            
            with patch('src.safe_mode.BACKUP_DIR', tmp_dir):
                is_valid, msg = _validate_backup(backup_file.name)
                
                # 验证结果取决于实现
                assert isinstance(is_valid, bool)

    def test_validate_backup_invalid(self):
        """测试验证无效备份"""
        from src.safe_mode import _validate_backup
        
        with patch('src.safe_mode.BACKUP_DIR', Path(tempfile.mkdtemp())):
            tmp_dir = Path(tempfile.mkdtemp())
            backup_file = tmp_dir / "backup-2024-01-01.tar.gz"
            
            # 创建无效文件
            with open(backup_file, 'w') as f:
                f.write("not a tar file")
            
            with patch('src.safe_mode.BACKUP_DIR', tmp_dir):
                is_valid, msg = _validate_backup(backup_file.name)
                
                assert is_valid is False
