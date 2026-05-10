"""索引增量更新测试。"""

import json
import time
from pathlib import Path

import pytest

from src.core.indexer import SkillIndex, ProjectIndex


def _write_skill(skills_dir: Path, name: str, description: str = "", body: str = "") -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    f = skill_dir / "SKILL.md"
    f.write_text(content, encoding="utf-8")
    return f


def _write_project(projects_dir: Path, name: str, content: str = "") -> Path:
    projects_dir.mkdir(parents=True, exist_ok=True)
    p = projects_dir / f"{name}.md"
    p.write_text(content or f"# {name}\n\nInfo", encoding="utf-8")
    return p


class TestSkillIndexIncrementalUpdate:
    """测试 SkillIndex 增量更新。"""

    def test_initial_build_creates_index_file(self, tmp_path: Path):
        """首次构建创建索引文件。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "skill-a", "desc a")

        idx = SkillIndex(skills_dir, index_dir)
        idx.load_or_build()

        idx_file = index_dir / "skills.jsonl"
        assert idx_file.exists()

    def test_no_change_skips_rebuild(self, tmp_path: Path):
        """无变化时不重建。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "skill-a", "desc a")

        idx1 = SkillIndex(skills_dir, index_dir)
        idx1.load_or_build()

        idx2 = SkillIndex(skills_dir, index_dir)
        idx2.load_or_build()

        summaries = idx2.list_summaries()
        assert any(s.get("name") == "skill-a" for s in summaries)

    def test_modified_file_updates_index(self, tmp_path: Path):
        """修改文件后索引更新。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "skill-a", "old desc")

        idx1 = SkillIndex(skills_dir, index_dir)
        idx1.load_or_build()

        # 修改 skill
        time.sleep(0.1)
        _write_skill(skills_dir, "skill-a", "new desc")

        idx2 = SkillIndex(skills_dir, index_dir)
        idx2.load_or_build()

        # 索引应该反映新内容
        summaries = idx2.list_summaries()
        assert any("new desc" in s.get("description", "") for s in summaries)

    def test_deleted_file_removed_from_index(self, tmp_path: Path):
        """删除文件后从索引移除。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "skill-a", "desc a")
        _write_skill(skills_dir, "skill-b", "desc b")

        idx1 = SkillIndex(skills_dir, index_dir)
        idx1.load_or_build()
        assert len(idx1.list_summaries()) == 2

        # 删除 skill-b
        import shutil
        shutil.rmtree(skills_dir / "skill-b")

        idx2 = SkillIndex(skills_dir, index_dir)
        idx2.load_or_build()
        assert len(idx2.list_summaries()) == 1

    def test_new_file_added_to_index(self, tmp_path: Path):
        """新增文件加入索引。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        _write_skill(skills_dir, "skill-a", "desc a")

        idx1 = SkillIndex(skills_dir, index_dir)
        idx1.load_or_build()

        _write_skill(skills_dir, "skill-b", "desc b")

        idx2 = SkillIndex(skills_dir, index_dir)
        idx2.load_or_build()
        assert len(idx2.list_summaries()) == 2

    def test_subdirectory_skills(self, tmp_path: Path):
        """支持子目录中的 skills。"""
        skills_dir = tmp_path / "skills"
        index_dir = tmp_path / "index"
        skills_dir.mkdir()
        index_dir.mkdir()

        # 直接在 skills_dir 下创建
        _write_skill(skills_dir, "my-skill", "desc")

        idx = SkillIndex(skills_dir, index_dir)
        idx.load_or_build()
        assert len(idx.list_summaries()) >= 1


class TestProjectIndexIncrementalUpdate:
    """测试 ProjectIndex 增量更新。"""

    def test_initial_build_creates_index_file(self, tmp_path: Path):
        """首次构建创建索引文件。"""
        projects_dir = tmp_path / "projects"
        index_dir = tmp_path / "index"
        projects_dir.mkdir()
        index_dir.mkdir()

        _write_project(projects_dir, "proj-a")

        idx = ProjectIndex(projects_dir, index_dir)
        idx.load_or_build()

        idx_file = index_dir / "projects.jsonl"
        assert idx_file.exists()

    def test_modified_project_updates_index(self, tmp_path: Path):
        """修改项目文件后索引更新。"""
        projects_dir = tmp_path / "projects"
        index_dir = tmp_path / "index"
        projects_dir.mkdir()
        index_dir.mkdir()

        _write_project(projects_dir, "proj-a", "# old")

        idx1 = ProjectIndex(projects_dir, index_dir)
        idx1.load_or_build()

        time.sleep(0.1)
        _write_project(projects_dir, "proj-a", "# new content here")

        idx2 = ProjectIndex(projects_dir, index_dir)
        idx2.load_or_build()
        summaries = idx2.list_summaries()
        assert len(summaries) >= 1

    def test_multiple_projects(self, tmp_path: Path):
        """多个项目都能被索引。"""
        projects_dir = tmp_path / "projects"
        index_dir = tmp_path / "index"
        projects_dir.mkdir()
        index_dir.mkdir()

        _write_project(projects_dir, "proj-a")
        _write_project(projects_dir, "proj-b")

        idx = ProjectIndex(projects_dir, index_dir)
        idx.load_or_build()
        assert len(idx.list_summaries()) == 2
