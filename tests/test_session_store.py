"""测试 session_store.py - 会话存储"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import json
import tempfile
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSessionStoreSchema:
    """会话存储 Schema 测试"""

    def test_schema_defines_sessions_table(self):
        """测试 sessions 表定义"""
        from src.memory.session_store import _SCHEMA
        
        assert "CREATE TABLE IF NOT EXISTS sessions" in _SCHEMA
        assert "session_id TEXT PRIMARY KEY" in _SCHEMA

    def test_schema_defines_segments_table(self):
        """测试 segments 表定义"""
        from src.memory.session_store import _SCHEMA
        
        assert "CREATE TABLE IF NOT EXISTS segments" in _SCHEMA
        assert "session_id TEXT NOT NULL" in _SCHEMA

    def test_schema_defines_messages_index_table(self):
        """测试 messages_index 表定义"""
        from src.memory.session_store import _SCHEMA
        
        assert "CREATE TABLE IF NOT EXISTS messages_index" in _SCHEMA
        assert "FTS5" in _SCHEMA


class TestSessionStorePaths:
    """会话存储路径测试"""

    def test_sessions_dir_defined(self):
        """测试 sessions 目录定义"""
        from src.memory.session_store import SESSIONS_DIR
        
        assert isinstance(SESSIONS_DIR, Path)
        assert "sessions" in str(SESSIONS_DIR)

    def test_search_db_defined(self):
        """测试 search.db 路径定义"""
        from src.memory.session_store import SEARCH_DB
        
        assert isinstance(SEARCH_DB, Path)
        assert "search.db" in str(SEARCH_DB)

    def test_tool_bodies_dir_defined(self):
        """测试 tool_bodies 目录定义"""
        from src.memory.session_store import TOOL_BODIES_DIR
        
        assert isinstance(TOOL_BODIES_DIR, Path)
        assert "tool_bodies" in str(TOOL_BODIES_DIR)


class TestSessionStoreCache:
    """会话存储缓存测试"""

    def test_sid_source_cache_exists(self):
        """测试 session_id → source 缓存存在"""
        from src.memory.session_store import _sid_source_cache
        
        assert isinstance(_sid_source_cache, dict)

    def test_sid_path_cache_exists(self):
        """测试 session_id → path 缓存存在"""
        from src.memory.session_store import _sid_path_cache
        
        assert isinstance(_sid_path_cache, dict)


class TestSessionStoreFunctions:
    """会话存储函数测试"""

    def test_get_connection(self):
        """测试获取数据库连接"""
        from src.memory.session_store import _get_connection, SEARCH_DB
        
        # 使用临时数据库
        tmp_db = Path(tempfile.mktemp(suffix='.db'))
        
        with patch.object(Path, '__truediv__', return_value=tmp_db):
            conn = _get_connection()
            
            assert isinstance(conn, sqlite3.Connection)
            conn.close()

    def test_init_schema(self):
        """测试初始化数据库 schema"""
        from src.memory.session_store import _init_schema
        
        tmp_db = Path(tempfile.mktemp(suffix='.db'))
        conn = sqlite3.connect(tmp_db)
        
        try:
            _init_schema(conn)
            
            # 验证表已创建
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = [row[0] for row in cursor.fetchall()]
            
            assert 'sessions' in tables
            assert 'segments' in tables
            assert 'messages_index' in tables
        finally:
            conn.close()
            tmp_db.unlink()

    def test_jieba_cut(self):
        """测试 jieba 分词"""
        from src.memory.session_store import _jieba_cut
        
        result = _jieba_cut("你好世界")
        
        assert isinstance(result, str)
        assert len(result) > 0
