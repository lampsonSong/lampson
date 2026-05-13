"""测试 session_search.py - 会话搜索"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSearchResult:
    """搜索结果测试"""

    def test_search_result_dataclass(self):
        """测试 SearchResult 数据类"""
        from src.memory.session_search import SearchResult
        
        result = SearchResult(
            session_id="test123",
            ts=1234567890,
            role="user",
            snippet="test snippet",
            bm25_score=1.5,
            cosine_score=0.8,
            final_score=1.2,
        )
        
        assert result.session_id == "test123"
        assert result.ts == 1234567890
        assert result.role == "user"
        assert result.snippet == "test snippet"
        assert result.bm25_score == 1.5
        assert result.cosine_score == 0.8
        assert result.final_score == 1.2


class TestSearchSessions:
    """会话搜索测试"""

    def test_search_empty_query(self):
        """测试空查询返回空"""
        from src.memory.session_search import search_sessions
        
        with patch('src.memory.session_search._search_bm25', return_value=[]):
            results = search_sessions("")
            
            assert results == []

    def test_search_whitespace_query(self):
        """测试空白查询返回空"""
        from src.memory.session_search import search_sessions
        
        results = search_sessions("   ")
        
        assert results == []

    def test_search_returns_search_result_list(self):
        """测试搜索返回 SearchResult 列表"""
        from src.memory.session_search import search_sessions, SearchResult
        
        mock_results = [
            SearchResult(
                session_id="s1",
                ts=123,
                role="user",
                snippet="test",
                bm25_score=1.0,
                cosine_score=None,
                final_score=1.0,
            )
        ]
        
        with patch('src.memory.session_search._search_bm25', return_value=mock_results):
            results = search_sessions("test")
            
            assert len(results) == 1
            assert isinstance(results[0], SearchResult)

    def test_search_with_date_filter(self):
        """测试带日期过滤的搜索"""
        from src.memory.session_search import search_sessions
        
        with patch('src.memory.session_search._search_bm25') as mock_bm25:
            mock_bm25.return_value = []
            
            search_sessions("test", date_from="2024-01-01", date_to="2024-12-31")
            
            mock_bm25.assert_called_once()
            call_kwargs = mock_bm25.call_args[1]
            assert call_kwargs["date_from"] == "2024-01-01"
            assert call_kwargs["date_to"] == "2024-12-31"

    def test_search_with_role_filter(self):
        """测试带角色过滤的搜索"""
        from src.memory.session_search import search_sessions
        
        with patch('src.memory.session_search._search_bm25') as mock_bm25:
            mock_bm25.return_value = []
            
            search_sessions("test", role="user")
            
            mock_bm25.assert_called_once()
            call_kwargs = mock_bm25.call_args[1]
            assert call_kwargs["role"] == "user"

    def test_search_with_session_id_filter(self):
        """测试带 session_id 过滤的搜索"""
        from src.memory.session_search import search_sessions
        
        with patch('src.memory.session_search._search_bm25') as mock_bm25:
            mock_bm25.return_value = []
            
            search_sessions("test", session_id="specific_session")
            
            mock_bm25.assert_called_once()
            call_kwargs = mock_bm25.call_args[1]
            assert call_kwargs["session_id"] == "specific_session"


class TestSearchBM25:
    """BM25 搜索测试"""

    def test_search_bm25_basic(self):
        """测试基本 BM25 搜索"""
        from src.memory.session_search import _search_bm25
        
        results = _search_bm25(
            query="test query",
            top_n=5,
        )
        
        # 临时数据库应该返回空
        assert isinstance(results, list)

    def test_search_bm25_with_params(self):
        """测试带参数的 BM25 搜索"""
        from src.memory.session_search import _search_bm25
        
        results = _search_bm25(
            query="python code",
            top_n=10,
            date_from="2024-01-01",
            date_to="2024-06-01",
            role="assistant",
            session_id=None,
        )
        
        assert isinstance(results, list)


class TestEmbeddingConfig:
    """Embedding 配置测试"""

    def test_get_embedding_config_returns_dict(self):
        """测试获取 embedding 配置"""
        from src.memory.session_search import _get_embedding_config
        
        with patch('src.memory.session_search._config', None):
            config = _get_embedding_config()
            
            assert isinstance(config, dict) or config is None
