"""语义检索模块测试：测试 retrieve_for_plan 和 format_retrieved_context 的各种场景。"""

import pytest
from unittest.mock import MagicMock, patch

from src.core.retrieval import format_retrieved_context, retrieve_for_plan


class TestFormatRetrievedContext:
    """测试 format_retrieved_context 函数的各种输入场景。"""

    def test_empty_input(self) -> None:
        """空输入应返回空字符串。"""
        assert format_retrieved_context([], []) == ""

    def test_skills_only(self) -> None:
        """只有技能内容。"""
        result = format_retrieved_context(
            ["# Debug Skill\n\nDebug steps..."],
            []
        )
        assert "匹配的技能" in result
        assert "Debug Skill" in result
        assert "匹配的项目" not in result

    def test_projects_only(self) -> None:
        """只有项目内容。"""
        result = format_retrieved_context(
            [],
            ["# Lamix\n\nLamix project info..."]
        )
        assert "匹配的项目" in result
        assert "Lamix" in result
        assert "匹配的技能" not in result

    def test_both_skills_and_projects(self) -> None:
        """同时有技能和项目。"""
        result = format_retrieved_context(
            ["# Skill1\n\nContent1", "# Skill2\n\nContent2"],
            ["# Project1\n\nInfo1"]
        )
        assert "匹配的技能" in result
        assert "匹配的项目" in result
        assert "Skill1" in result
        assert "Skill2" in result
        assert "Project1" in result

    def test_multiple_skills_with_separator(self) -> None:
        """多个技能之间应该用 --- 分隔。"""
        result = format_retrieved_context(
            ["Skill A", "Skill B", "Skill C"],
            []
        )
        assert "---" in result
        # 应该有 2 个分隔符（3 个技能）
        assert result.count("---") == 2

    def test_multiple_projects_with_separator(self) -> None:
        """多个项目之间应该用 --- 分隔。"""
        result = format_retrieved_context(
            [],
            ["Proj A", "Proj B"]
        )
        assert "---" in result
        assert result.count("---") == 1


class TestRetrieveForPlan:
    """测试 retrieve_for_plan 函数的各种场景。"""

    def test_empty_needs(self) -> None:
        """skill_needs 和 project_needs 都为空时，不应调用索引。"""
        sidx = MagicMock()
        pidx = MagicMock()

        result = retrieve_for_plan("", "", sidx, pidx, {})

        assert result == ""
        sidx.search.assert_not_called()
        pidx.search.assert_not_called()

    def test_skill_needs_only(self) -> None:
        """只有 skill_needs。"""
        sidx = MagicMock()
        sidx.search.return_value = ["# Debug Skill\n\nDebug content"]
        pidx = MagicMock()

        result = retrieve_for_plan(
            "debug python code",
            "",
            sidx,
            pidx,
            {"skill_top_k": 2, "project_top_k": 1, "similarity_threshold": 0.3}
        )

        assert "匹配的技能" in result
        assert "Debug Skill" in result
        sidx.search.assert_called_once_with(
            "debug python code",
            top_k=2,
            similarity_threshold=0.3
        )
        pidx.search.assert_not_called()

    def test_project_needs_only(self) -> None:
        """只有 project_needs。"""
        sidx = MagicMock()
        pidx = MagicMock()
        pidx.search.return_value = ["# Lamix\n\nLamix context"]

        result = retrieve_for_plan(
            "",
            "lamix project structure",
            sidx,
            pidx,
            {"skill_top_k": 3, "project_top_k": 2, "similarity_threshold": 0.3}
        )

        assert "匹配的项目" in result
        assert "Lamix" in result
        pidx.search.assert_called_once_with(
            "lamix project structure",
            top_k=2,
            similarity_threshold=0.3
        )
        sidx.search.assert_not_called()

    def test_both_needs(self) -> None:
        """同时有 skill_needs 和 project_needs。"""
        sidx = MagicMock()
        sidx.search.return_value = ["# Debug\n\nDebug skill"]
        pidx = MagicMock()
        pidx.search.return_value = ["# Lamix\n\nLamix info"]

        result = retrieve_for_plan(
            "debug skill needed",
            "lamix context needed",
            sidx,
            pidx,
            {
                "skill_top_k": 3,
                "project_top_k": 2,
                "similarity_threshold": 0.25
            }
        )

        assert "匹配的技能" in result
        assert "匹配的项目" in result
        assert "Debug" in result
        assert "Lamix" in result
        sidx.search.assert_called_once()
        pidx.search.assert_called_once()

    def test_none_indices(self) -> None:
        """索引为 None 时不应报错。"""
        result = retrieve_for_plan(
            "some skill",
            "some project",
            None,
            None,
            {}
        )

        assert result == ""

    def test_index_search_exception_handling(self) -> None:
        """索引搜索抛出异常时应优雅处理。"""
        sidx = MagicMock()
        sidx.search.side_effect = Exception("Index error")
        pidx = MagicMock()
        pidx.search.side_effect = ValueError("Search failed")

        result = retrieve_for_plan(
            "skill needs",
            "project needs",
            sidx,
            pidx,
            {}
        )

        # 不应抛异常，返回空字符串或部分结果
        assert isinstance(result, str)

    def test_default_config_values(self) -> None:
        """配置为空字典时应使用默认值。"""
        sidx = MagicMock()
        sidx.search.return_value = []
        pidx = MagicMock()
        pidx.search.return_value = []

        retrieve_for_plan(
            "skill",
            "project",
            sidx,
            pidx,
            {}
        )

        # 检查调用时的默认参数
        call_args = sidx.search.call_args
        assert call_args[1]["top_k"] == 3
        assert call_args[1]["similarity_threshold"] == 0.3

        call_args2 = pidx.search.call_args
        assert call_args2[1]["top_k"] == 2

    def test_whitespace_handling(self) -> None:
        """空白字符处理。"""
        sidx = MagicMock()
        sidx.search.return_value = ["# Skill\n\nContent"]

        # 前后空白应被 strip
        result = retrieve_for_plan(
            "  skill with spaces  ",
            "  ",
            sidx,
            MagicMock(),
            {}
        )

        sidx.search.assert_called_once_with(
            "skill with spaces",
            top_k=3,
            similarity_threshold=0.3
        )

    def test_invalid_config_types(self) -> None:
        """配置类型不正确时会报错。"""
        sidx = MagicMock()
        pidx = MagicMock()

        # 传入错误的配置类型，会报 ValueError
        with pytest.raises(ValueError):
            retrieve_for_plan(
                "skill",
                "project",
                sidx,
                pidx,
                {
                    "skill_top_k": "invalid",  # 字符串而非数字
                    "project_top_k": None,
                    "similarity_threshold": "0.5"
                }
            )

    def test_empty_search_results(self) -> None:
        """索引搜索返回空列表时。"""
        sidx = MagicMock()
        sidx.search.return_value = []
        pidx = MagicMock()
        pidx.search.return_value = []

        result = retrieve_for_plan(
            "skill needs",
            "project needs",
            sidx,
            pidx,
            {}
        )

        # 没有匹配结果时返回空字符串
        assert result == ""
