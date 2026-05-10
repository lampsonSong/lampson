"""config retrieval 测试：验证 get_retrieval_config 合并逻辑。"""

import pytest

from src.core.config import get_retrieval_config, _DEFAULT_RETRIEVAL


class TestGetRetrievalConfig:
    """测试 get_retrieval_config 函数。"""

    def test_default_config_when_no_retrieval_key(self):
        """没有 retrieval key 时返回默认值。"""
        result = get_retrieval_config({})
        assert result["skill_top_k"] == _DEFAULT_RETRIEVAL["skill_top_k"]
        assert result["project_top_k"] == _DEFAULT_RETRIEVAL["project_top_k"]
        assert result["similarity_threshold"] == _DEFAULT_RETRIEVAL["similarity_threshold"]

    def test_partial_override(self):
        """只覆盖部分值。"""
        result = get_retrieval_config({"retrieval": {"skill_top_k": 10}})
        assert result["skill_top_k"] == 10
        assert result["project_top_k"] == _DEFAULT_RETRIEVAL["project_top_k"]

    def test_full_override(self):
        """完全覆盖所有值。"""
        result = get_retrieval_config({
            "retrieval": {
                "skill_top_k": 10,
                "project_top_k": 5,
                "similarity_threshold": 0.8,
            }
        })
        assert result["skill_top_k"] == 10
        assert result["project_top_k"] == 5
        assert result["similarity_threshold"] == 0.8

    def test_return_type(self):
        """返回值类型正确。"""
        result = get_retrieval_config({})
        assert isinstance(result, dict)
        assert isinstance(result["skill_top_k"], int)
        assert isinstance(result["project_top_k"], int)
        assert isinstance(result["similarity_threshold"], float)

    def test_retrieval_is_not_dict_ignored(self):
        """retrieval 不是 dict 时使用默认值。"""
        result = get_retrieval_config({"retrieval": "invalid"})
        assert result["skill_top_k"] == _DEFAULT_RETRIEVAL["skill_top_k"]

    def test_type_coercion(self):
        """字符串数值能被转为正确类型。"""
        result = get_retrieval_config({
            "retrieval": {"skill_top_k": "7", "similarity_threshold": "0.5"}
        })
        assert result["skill_top_k"] == 7
        assert isinstance(result["skill_top_k"], int)
        assert result["similarity_threshold"] == 0.5
