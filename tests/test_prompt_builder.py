"""prompt_builder：skills 目录注入与 frontmatter 补填。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import prompt_builder as pb
from src.core.prompt_builder import PromptBuilder, _parse_frontmatter, build_skills_index


def test_build_skills_index_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr(pb, "SKILLS_DIR", skills)
    pb._skills_index_cache = None
    assert build_skills_index() == ""


def test_build_skills_index_format_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "skills"
    (skills / "code-writing").mkdir(parents=True)
    (skills / "code-writing" / "SKILL.md").write_text(
        "---\nname: code-writing\ndescription: 写代码\n---\n\n正文保持\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pb, "SKILLS_DIR", skills)
    pb._skills_index_cache = None
    out = build_skills_index()
    assert "## Skills（按需加载）" in out
    assert "- **code-writing**: 写代码" in out
    assert "正文保持" not in out
    # 缓存生效：第二次调用返回相同结果
    out2 = build_skills_index()
    assert out2 == out


def test_build_skills_index_fills_created_at_and_invocation_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "skills"
    (skills / "a").mkdir(parents=True)
    path = skills / "a" / "SKILL.md"
    path.write_text(
        "---\nname: alpha\ndescription: d\n---\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pb, "SKILLS_DIR", skills)
    pb._skills_index_cache = None
    build_skills_index()
    raw = path.read_text(encoding="utf-8")
    assert "created_at:" in raw
    assert "invocation_count: 0" in raw
    meta, _ = _parse_frontmatter(raw)
    assert meta.get("invocation_count") == 0
    assert meta.get("created_at")


def test_build_skills_index_skips_existing_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skills = tmp_path / "skills"
    (skills / "a").mkdir(parents=True)
    path = skills / "a" / "SKILL.md"
    path.write_text(
        '---\nname: alpha\ndescription: d\ncreated_at: "2020-01-01"\ninvocation_count: 7\n---\n\nbody\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pb, "SKILLS_DIR", skills)
    pb._skills_index_cache = None
    build_skills_index()
    raw = path.read_text(encoding="utf-8")
    assert "2020-01-01" in raw
    assert "invocation_count: 7" in raw


def test_load_identity_falls_back_to_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MEMORY.md 不存在时，应从 config/default_identity.md 读取。"""
    monkeypatch.setattr(pb, "MEMORY_PATH", tmp_path / "nonexistent_MEMORY.md")
    identity = pb.load_identity()
    assert "Lamix" in identity


def test_load_user_creates_from_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """USER.md 不存在时，应从模板复制创建。"""
    user_path = tmp_path / "USER.md"
    monkeypatch.setattr(pb, "USER_PATH", user_path)
    monkeypatch.setattr(pb, "LAMIX_DIR", tmp_path)
    result = pb.load_user()
    assert result != ""
    assert user_path.exists()
    assert "称呼" in user_path.read_text(encoding="utf-8")
