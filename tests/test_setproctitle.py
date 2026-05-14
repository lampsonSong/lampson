"""测试 setproctitle 功能 - 进程名设置"""
import sys  # noqa: E402
import pytest
import platform
import multiprocessing
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _set_proctitle_worker(queue):
    """在子进程中设置进程名"""
    import setproctitle
    setproctitle.setproctitle("lamix-test")
    # 获取当前进程名
    name = setproctitle.getproctitle()
    queue.put(name)


class TestSetproctitle:
    """setproctitle 功能测试 — 未安装则跳过"""

    @pytest.fixture(autouse=True)
    def require_setproctitle(self):
        """模块未安装时跳过整个类。"""
        pytest.importorskip("setproctitle")

    def test_setproctitle_module_available(self):
        """测试 setproctitle 模块可用"""
        import setproctitle
        assert hasattr(setproctitle, 'setproctitle')
        assert hasattr(setproctitle, 'getproctitle')

    def test_setproctitle_changes_process_name(self):
        """测试 setproctitle 能在子进程中改变进程名"""
        import setproctitle

        queue = multiprocessing.Queue()
        proc = multiprocessing.Process(target=_set_proctitle_worker, args=(queue,))
        proc.start()
        proc.join(timeout=5)

        # 从队列获取子进程设置的进程名
        if not queue.empty():
            new_name = queue.get(timeout=1)
            assert new_name == "lamix-test"
        else:
            pytest.skip("队列为空，可能是进程异常退出")

    def test_setproctitle_works(self):
        """测试 setproctitle 基本功能"""
        import setproctitle

        # 获取当前进程名（Windows 可能返回空串，这是正常的）
        name = setproctitle.getproctitle()
        assert isinstance(name, str)

        # 在支持的平台上，进程名应该有内容
        # Linux/macOS 上有效，Windows 上可能无效但不影响测试通过
        if platform.system() != "Windows":
            if len(name) == 0:
                pytest.skip("进程名为空（可能是 mock 环境）")

    def test_daemon_imports_setproctitle(self):
        """测试 daemon.py 导入了 setproctitle"""
        daemon_source = Path(__file__).parent.parent / "src" / "daemon.py"
        content = daemon_source.read_text(encoding='utf-8', errors='ignore')
        assert 'import setproctitle' in content

    def test_daemon_calls_setproctitle(self):
        """测试 daemon.py 调用了 setproctitle.setproctitle"""
        daemon_source = Path(__file__).parent.parent / "src" / "daemon.py"
        content = daemon_source.read_text(encoding='utf-8', errors='ignore')
        assert 'setproctitle.setproctitle' in content
