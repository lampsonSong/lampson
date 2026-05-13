"""测试 session_store.py - 会话存储"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSessionStoreSchema:
    def test_schema_defines_tables(self):
        from src.memory.session_store import _SCHEMA
        assert "CREATE TABLE IF NOT EXISTS sessions" in _SCHEMA

    def test_paths_defined(self):
        from src.memory.session_store import SESSIONS_DIR, SEARCH_DB
        assert isinstance(SESSIONS_DIR, Path)
        assert isinstance(SEARCH_DB, Path)

    def test_cache_exists(self):
        from src.memory.session_store import _sid_source_cache
        assert isinstance(_sid_source_cache, dict)
