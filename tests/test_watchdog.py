"""测试 watchdog.py - 进程守护"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWatchdogFunctions:
    """Watchdog 函数测试"""

    def test_get_lamix_bin_finds_lamix(self):
        """测试找到 lamix 命令"""
        from src.watchdog import _get_lamix_bin
        
        with patch('src.watchdog.shutil') as mock_shutil:
            mock_shutil.which.return_value = "/usr/local/bin/lamix"
            
            result = _get_lamix_bin()
            
            assert result == "/usr/local/bin/lamix"

    def test_get_lamix_bin_not_found(self):
        """测试找不到 lamix 命令"""
        from src.watchdog import _get_lamix_bin
        
        with patch('src.watchdog.shutil') as mock_shutil:
            with patch('src.watchdog.sysconfig') as mock_sysconfig:
                mock_shutil.which.return_value = None
                mock_sysconfig.get_path.return_value = "/usr/bin"
                
                with patch('os.path.exists', return_value=False):
                    result = _get_lamix_bin()
                    
                    # 找不到时返回 None
                    assert result is None

    def test_log_creates_log_file(self):
        """测试日志写入"""
        from src.watchdog import _log
        
        with patch('src.watchdog.LOG_DIR', Path(tempfile.mkdtemp())):
            with patch('src.watchdog.logger') as mock_logger:
                _log("test message")
                
                mock_logger.info.assert_called_once()


class TestWatchdogConfig:
    """Watchdog 配置测试"""

    def test_load_config_no_file(self):
        """测试加载不存在的配置文件"""
        from src.watchdog import _load_config
        
        with patch('src.watchdog.CONFIG_PATH', Path(tempfile.mkdtemp()) / "nonexistent.yaml"):
            result = _load_config()
            
            assert result == {}

    def test_load_config_with_yaml(self):
        """测试加载 YAML 配置"""
        from src.watchdog import _load_config
        import yaml
        
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.yaml"
        
        config_data = {
            "llm": {"api_key": "test_key"},
            "feishu": {"app_id": "test_app"},
        }
        
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        with patch('src.watchdog.CONFIG_PATH', config_path):
            result = _load_config()
            
            assert result["llm"]["api_key"] == "test_key"
            assert result["feishu"]["app_id"] == "test_app"


class TestWatchdogProcess:
    """Watchdog 进程测试"""

    def test_get_daemon_pid_no_file(self):
        """测试获取不存在的 daemon PID"""
        from src.watchdog import _get_daemon_pid
        
        with patch('src.watchdog.PID_FILE', Path(tempfile.mkdtemp()) / "nonexistent.pid"):
            result = _get_daemon_pid()
            
            assert result is None

    def test_get_daemon_pid_with_file(self):
        """测试获取 daemon PID"""
        from src.watchdog import _get_daemon_pid
        
        tmp_dir = Path(tempfile.mkdtemp())
        pid_file = tmp_dir / "daemon.pid"
        
        with open(pid_file, 'w') as f:
            f.write("12345")
        
        with patch('src.watchdog.PID_FILE', pid_file):
            result = _get_daemon_pid()
            
            assert result == 12345

    def test_is_daemon_running_true(self):
        """测试 daemon 正在运行"""
        from src.watchdog import _is_daemon_running
        
        with patch('src.watchdog._get_daemon_pid', return_value=12345):
            with patch('src.watchdog._check_pid_exists', return_value=True):
                result = _is_daemon_running()
                
                assert result is True

    def test_is_daemon_running_false(self):
        """测试 daemon 未运行"""
        from src.watchdog import _is_daemon_running
        
        with patch('src.watchdog._get_daemon_pid', return_value=None):
            result = _is_daemon_running()
            
            assert result is False
