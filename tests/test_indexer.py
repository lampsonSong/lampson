"""索引器测试：SkillIndex / ProjectIndex 核心功能。"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.indexer import SkillIndex, ProjectIndex, _cosine_sim


@pytest.mark.skipif(sys.version_info < (3, 10), reason="_cosine_sim uses zip(strict=True) requiring 3.10+")
class TestCosineSim:
    """测试 _cosine_sim 函数。"""

    def test_orthogonal(self):
        assert _cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_parallel(self):
        assert _cosine_sim([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)

    def test_opposite(self):
        assert _cosine_sim([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def _write_skill(skills_dir: Path, name: str, description: str = "", body: str = "") -> Path:
    """在 skills_dir 下创建一个平铺 skill 文件 skills/<name>.md。"""
    content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    skill_file = skills_dir / f"{name}.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


class TestSkillIndex:
    """测试 SkillIndex。"""

    def test_build_and_list(self, tmp_path: Path):
        """构建索引后能列出 skills。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "test-skill", "A test skill", "Do something")

        idx = SkillIndex(skills_dir, index_dir)
        idx.load_or_build()

        summaries = idx.list_summaries()
        assert len(summaries) >= 1
        names = [s.get("name", "") for s in summaries]
        assert "test-skill" in names

    def test_keyword_search(self, tmp_path: Path):
        """关键词搜索能匹配。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "code-review", "Review code quality", "Steps for review")
        _write_skill(skills_dir, "debug", "Debug code errors", "Debug workflow")

        idx = SkillIndex(skills_dir, index_dir)
        idx.load_or_build()

        results = idx.search("review")
        assert len(results) >= 1

    def test_empty_skills_dir(self, tmp_path: Path):
        """空目录不报错。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        idx = SkillIndex(skills_dir, index_dir)
        idx.load_or_build()
        assert idx.list_summaries() == []


def _write_project(projects_dir: Path, name: str, content: str = "") -> Path:
    projects_dir.mkdir(parents=True, exist_ok=True)
    p = projects_dir / f"{name}.md"
    p.write_text(content or f"# {name}\n\nSome project info.", encoding="utf-8")
    return p


class TestProjectIndex:
    """测试 ProjectIndex。"""

    def test_build_and_list(self, tmp_path: Path):
        """构建索引后能列出 projects。"""
        projects_dir = tmp_path / "projects"
        index_dir = tmp_path / "index"
        projects_dir.mkdir()
        index_dir.mkdir()

        _write_project(projects_dir, "test-project", "# test-project\n\nInfo")

        idx = ProjectIndex(projects_dir, index_dir)
        idx.load_or_build()

        summaries = idx.list_summaries()
        assert len(summaries) >= 1
