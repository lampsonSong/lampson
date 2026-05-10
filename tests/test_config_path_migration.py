"""测试 config.yaml 旧路径自动修正逻辑。"""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


class TestFixConfigPaths:
    """测试 _fix_config_paths 自动修正 config.yaml 中的旧路径。"""

    def test_fixes_old_skills_path(self, tmp_path: Path) -> None:
        """config.yaml 中 skills_path 指向旧路径（不含 memory/）时应自动修正。"""
        from src.core import config as cfg_mod

        new_skills = tmp_path / "memory" / "skills"
        old_skills_path = str(tmp_path / "skills")
        new_skills_path = str(new_skills)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "skills_path": old_skills_path,
            "llm": {"api_key": "test"},
        }
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        with (
            patch.object(cfg_mod, "LAMIX_DIR", tmp_path),
            patch.object(cfg_mod, "CONFIG_PATH", config_file),
            patch.object(cfg_mod, "SKILLS_DIR", new_skills),
            patch.object(cfg_mod, "PROJECTS_DIR", tmp_path / "memory" / "projects"),
            patch.object(cfg_mod, "INFO_DIR", tmp_path / "memory" / "info"),
            patch.object(cfg_mod, "MEMORY_DIR", tmp_path / "memory"),
        ):
            cfg_mod._fix_config_paths()

        # 重新读取 config.yaml
        with config_file.open("r", encoding="utf-8") as f:
            fixed = yaml.safe_load(f) or {}
        assert fixed["skills_path"] == new_skills_path

    def test_does_not_overwrite_correct_path(self, tmp_path: Path) -> None:
        """config.yaml 中已经是新路径时不应修改。"""
        from src.core import config as cfg_mod

        new_skills = tmp_path / "memory" / "skills"
        new_skills_path = str(new_skills)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "skills_path": new_skills_path,
            "llm": {"api_key": "test"},
        }
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        with (
            patch.object(cfg_mod, "LAMIX_DIR", tmp_path),
            patch.object(cfg_mod, "CONFIG_PATH", config_file),
            patch.object(cfg_mod, "SKILLS_DIR", new_skills),
            patch.object(cfg_mod, "PROJECTS_DIR", tmp_path / "memory" / "projects"),
            patch.object(cfg_mod, "INFO_DIR", tmp_path / "memory" / "info"),
            patch.object(cfg_mod, "MEMORY_DIR", tmp_path / "memory"),
        ):
            cfg_mod._fix_config_paths()

        with config_file.open("r", encoding="utf-8") as f:
            fixed = yaml.safe_load(f) or {}
        assert fixed["skills_path"] == new_skills_path

    def test_fixes_multiple_old_paths(self, tmp_path: Path) -> None:
        """同时修正 skills_path 和 projects_path。"""
        from src.core import config as cfg_mod

        config_file = tmp_path / "config.yaml"
        config_data = {
            "skills_path": str(tmp_path / "skills"),
            "projects_path": str(tmp_path / "projects"),
            "llm": {"api_key": "test"},
        }
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        new_skills = tmp_path / "memory" / "skills"
        new_projects = tmp_path / "memory" / "projects"

        with (
            patch.object(cfg_mod, "LAMIX_DIR", tmp_path),
            patch.object(cfg_mod, "CONFIG_PATH", config_file),
            patch.object(cfg_mod, "SKILLS_DIR", new_skills),
            patch.object(cfg_mod, "PROJECTS_DIR", new_projects),
            patch.object(cfg_mod, "INFO_DIR", tmp_path / "memory" / "info"),
            patch.object(cfg_mod, "MEMORY_DIR", tmp_path / "memory"),
        ):
            cfg_mod._fix_config_paths()

        with config_file.open("r", encoding="utf-8") as f:
            fixed = yaml.safe_load(f) or {}
        assert fixed["skills_path"] == str(new_skills)
        assert fixed["projects_path"] == str(new_projects)

    def test_no_config_file_is_ok(self, tmp_path: Path) -> None:
        """config.yaml 不存在时不应报错。"""
        from src.core import config as cfg_mod

        config_file = tmp_path / "nonexistent.yaml"
        with (
            patch.object(cfg_mod, "CONFIG_PATH", config_file),
        ):
            cfg_mod._fix_config_paths()  # 应该静默通过

    def test_fixes_tilde_expanded_path(self, tmp_path: Path) -> None:
        """旧路径使用 ~ 格式时也能正确修正。"""
        from src.core import config as cfg_mod

        config_file = tmp_path / "config.yaml"
        config_data = {
            "skills_path": "~/.lamix/skills",
            "llm": {"api_key": "test"},
        }
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        new_skills = tmp_path / "memory" / "skills"

        with (
            patch.object(cfg_mod, "LAMIX_DIR", tmp_path),
            patch.object(cfg_mod, "CONFIG_PATH", config_file),
            patch.object(cfg_mod, "SKILLS_DIR", new_skills),
            patch.object(cfg_mod, "PROJECTS_DIR", tmp_path / "memory" / "projects"),
            patch.object(cfg_mod, "INFO_DIR", tmp_path / "memory" / "info"),
            patch.object(cfg_mod, "MEMORY_DIR", tmp_path / "memory"),
        ):
            cfg_mod._fix_config_paths()

        with config_file.open("r", encoding="utf-8") as f:
            fixed = yaml.safe_load(f) or {}
        assert fixed["skills_path"] == str(new_skills)


class TestMigrateOldDirs:
    """测试 _migrate_old_dirs 在已迁移情况下仍修正 config 路径。"""

    def test_fixes_config_even_after_migration(self, tmp_path: Path) -> None:
        """即使 .memory_migrated 标记存在，仍应修正 config.yaml 中的旧路径。"""
        from src.core import config as cfg_mod

        # 创建迁移标记
        (tmp_path / ".memory_migrated").write_text("v1", encoding="utf-8")

        # config.yaml 中有旧路径
        config_file = tmp_path / "config.yaml"
        config_data = {
            "skills_path": str(tmp_path / "skills"),
            "llm": {"api_key": "test"},
        }
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        new_skills = tmp_path / "memory" / "skills"

        with (
            patch.object(cfg_mod, "LAMIX_DIR", tmp_path),
            patch.object(cfg_mod, "CONFIG_PATH", config_file),
            patch.object(cfg_mod, "SKILLS_DIR", new_skills),
            patch.object(cfg_mod, "PROJECTS_DIR", tmp_path / "memory" / "projects"),
            patch.object(cfg_mod, "INFO_DIR", tmp_path / "memory" / "info"),
            patch.object(cfg_mod, "MEMORY_DIR", tmp_path / "memory"),
        ):
            cfg_mod._migrate_old_dirs()

        with config_file.open("r", encoding="utf-8") as f:
            fixed = yaml.safe_load(f) or {}
        assert fixed["skills_path"] == str(new_skills)


class TestSkillIndexWithConfigOverride:
    """测试 SkillIndex 在 config.yaml 覆盖路径时仍能正确索引。"""

    def test_index_finds_skills_after_path_fix(self, tmp_path: Path) -> None:
        """config.yaml 中的旧路径被修正后，索引应能找到 skill 文件。"""
        from src.core.indexer import SkillIndex

        # 模拟新的 skills 目录结构
        skills_dir = tmp_path / "memory" / "skills"
        skills_dir.mkdir(parents=True)
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: a test skill\n---\n\nbody",
            encoding="utf-8",
        )

        index_dir = tmp_path / "index"
        index_dir.mkdir()

        si = SkillIndex(skills_dir, index_dir)
        si.load_or_build()

        assert len(si._entries) == 1
        assert si._entries[0]["name"] == "test-skill"

        # 搜索应正常工作
        results = si.search("test", top_k=3, similarity_threshold=0.1)
        assert len(results) == 1
