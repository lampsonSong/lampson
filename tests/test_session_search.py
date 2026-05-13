"""测试 session_search.py - 会话搜索"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSearchResult:
    def test_search_result_dataclass(self):
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


class TestSearchSessions:
    def test_search_empty_query(self):
        from src.memory.session_search import search_sessions
        results = search_sessions("")
        assert results == []

    def test_search_whitespace_query(self):
        from src.memory.session_search import search_sessions
        results = search_sessions("   ")
        assert results == []
