"""测试 skills/projects/info 动态索引刷新机制。

覆盖三个层面：
1. _get_index_fingerprint 对 skills（递归）/projects/info 的检测
2. LLMClient.auto_refresh_if_needed 检测变更并刷新 system prompt
3. prompt_builder 缓存在文件变更后正确失效
"""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import prompt_builder as pb
from src.core.llm import LLMClient, _get_identity_mtimes, _get_index_fingerprint


# ── 辅助函数 ──────────────────────────────────────────────────────────────

def _setup_dirs(tmp_path: Path):
    """创建 skills/projects/info 目录结构，返回 (skills, projects, info)。"""
    skills = tmp_path / "skills"
    projects = tmp_path / "projects"
    info = tmp_path / "info"
    skills.mkdir()
    projects.mkdir()
    info.mkdir()
    return skills, projects, info


def _write_skill(skills_dir: Path, name: str, desc: str = "") -> Path:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n",
        encoding="utf-8",
    )
    return p


def _write_project(projects_dir: Path, name: str, content: str = "") -> Path:
    p = projects_dir / f"{name}.md"
    p.write_text(content or f"# {name}\n\nInfo.", encoding="utf-8")
    return p


def _write_info(info_dir: Path, name: str, desc: str = "") -> Path:
    p = info_dir / f"{name}.md"
    p.write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\ncontent\n",
        encoding="utf-8",
    )
    return p


def _make_llm_client(tmp_path: Path) -> LLMClient:
    """创建一个 LLMClient，monkey-patch 路径到 tmp_path。"""
    client = LLMClient(
        api_key="test",
        base_url="https://api.test.com",
        model="test-model",
    )
    return client


def _patch_dirs(tmp_path: Path, skills: Path, projects: Path, info: Path):
    """返回 monkeypatch 路径的 patcher 列表。"""
    return [
        patch("src.core.llm.SKILLS_DIR", skills),
        patch("src.core.llm.PROJECTS_DIR", projects),
        patch("src.core.llm.INFO_DIR", info),
        patch("src.core.llm.MEMORY_PATH", tmp_path / "MEMORY.md"),
        patch("src.core.llm.USER_PATH", tmp_path / "USER.md"),
        patch("src.core.prompt_builder.SKILLS_DIR", skills),
        patch("src.core.prompt_builder.PROJECTS_DIR", projects),
        patch("src.core.prompt_builder.INFO_DIR", info),
        patch("src.core.prompt_builder.MEMORY_PATH", tmp_path / "MEMORY.md"),
        patch("src.core.prompt_builder.USER_PATH", tmp_path / "USER.md"),
        patch("src.core.prompt_builder.LAMIX_DIR", tmp_path),
    ]


# ── _get_index_fingerprint 测试 ─────────────────────────────────────────

class TestGetIndexFingerprint:
    """测试 _get_index_fingerprint 对三种目录的扫描。"""

    def test_skills_recursive(self, tmp_path: Path):
        """skills 目录是嵌套结构，必须递归扫描。"""
        skills, projects, info = _setup_dirs(tmp_path)
        _write_skill(skills, "my-skill", "test skill")

        with patch("src.core.llm.SKILLS_DIR", skills), \
             patch("src.core.llm.PROJECTS_DIR", projects), \
             patch("src.core.llm.INFO_DIR", info):
            fp = _get_index_fingerprint()

        # 应该扫描到 skills/my-skill/SKILL.md
        assert len(fp) >= 1
        paths = [p for p, _ in fp]
        assert any("SKILL.md" in p for p in paths)

    def test_projects_flat(self, tmp_path: Path):
        """projects 目录是平铺结构，只扫一层。"""
        skills, projects, info = _setup_dirs(tmp_path)
        _write_project(projects, "my-project")

        with patch("src.core.llm.SKILLS_DIR", skills), \
             patch("src.core.llm.PROJECTS_DIR", projects), \
             patch("src.core.llm.INFO_DIR", info):
            fp = _get_index_fingerprint()

        paths = [p for p, _ in fp]
        assert any("my-project.md" in p for p in paths)

    def test_info_flat(self, tmp_path: Path):
        """info 目录是平铺结构。"""
        skills, projects, info = _setup_dirs(tmp_path)
        _write_info(info, "my-info", "test info")

        with patch("src.core.llm.SKILLS_DIR", skills), \
             patch("src.core.llm.PROJECTS_DIR", projects), \
             patch("src.core.llm.INFO_DIR", info):
            fp = _get_index_fingerprint()

        paths = [p for p, _ in fp]
        assert any("my-info.md" in p for p in paths)

    def test_empty_dirs(self, tmp_path: Path):
        """空目录返回空 fingerprint。"""
        skills, projects, info = _setup_dirs(tmp_path)

        with patch("src.core.llm.SKILLS_DIR", skills), \
             patch("src.core.llm.PROJECTS_DIR", projects), \
             patch("src.core.llm.INFO_DIR", info):
            fp = _get_index_fingerprint()

        assert fp == frozenset()

    def test_new_file_changes_fingerprint(self, tmp_path: Path):
        """新增文件后 fingerprint 应该变化。"""
        skills, projects, info = _setup_dirs(tmp_path)

        with patch("src.core.llm.SKILLS_DIR", skills), \
             patch("src.core.llm.PROJECTS_DIR", projects), \
             patch("src.core.llm.INFO_DIR", info):
            fp1 = _get_index_fingerprint()

            _write_project(projects, "new-project")
            fp2 = _get_index_fingerprint()

        assert fp1 != fp2

    def test_mtime_change_detected(self, tmp_path: Path):
        """文件内容修改（mtime 变化）后 fingerprint 应该变化。"""
        skills, projects, info = _setup_dirs(tmp_path)
        p = _write_project(projects, "existing")

        with patch("src.core.llm.SKILLS_DIR", skills), \
             patch("src.core.llm.PROJECTS_DIR", projects), \
             patch("src.core.llm.INFO_DIR", info):
            fp1 = _get_index_fingerprint()

            # 确保mtime变化（文件系统精度可能不够）
            time.sleep(0.05)
            p.write_text("# existing\n\nUpdated content.", encoding="utf-8")

            fp2 = _get_index_fingerprint()

        assert fp1 != fp2


# ── auto_refresh_if_needed 测试 ─────────────────────────────────────────

class TestAutoRefreshIfNeeded:
    """测试 LLMClient.auto_refresh_if_needed 的变更检测与 system prompt 刷新。"""

    def _setup_client(self, tmp_path: Path, skills: Path, projects: Path, info: Path):
        """创建 client 并 patch 所有路径，返回 (client, patchers)。"""
        patchers = _patch_dirs(tmp_path, skills, projects, info)
        for p in patchers:
            p.start()

        # 创建 identity 文件
        (tmp_path / "MEMORY.md").write_text("# Identity\nTest identity.", encoding="utf-8")
        (tmp_path / "USER.md").write_text("称呼：伙伴", encoding="utf-8")

        client = _make_llm_client(tmp_path)
        client.set_system_context()
        return client, patchers

    def _teardown(self, patchers):
        for p in patchers:
            p.stop()
        # 清除 prompt_builder 缓存
        pb._skills_index_cache = None
        pb._projects_index_cache = None
        pb._info_index_cache = None

    def test_no_change_no_refresh(self, tmp_path: Path):
        """无变更时 refresh_system_prompt 不应被调用。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            old_content = client.messages[0]["content"]
            client.auto_refresh_if_needed()
            new_content = client.messages[0]["content"]
            assert old_content == new_content
        finally:
            self._teardown(patchers)

    def test_new_project_triggers_refresh(self, tmp_path: Path):
        """新增 project 文件后 system prompt 应刷新。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            old_content = client.messages[0]["content"]

            _write_project(projects, "dynamic-test", "# dynamic-test\n\nDynamic project.")
            client.auto_refresh_if_needed()

            new_content = client.messages[0]["content"]
            assert new_content != old_content
            assert "dynamic-test" in new_content
        finally:
            self._teardown(patchers)

    def test_new_skill_triggers_refresh(self, tmp_path: Path):
        """新增 skill 文件后 system prompt 应刷新。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            old_content = client.messages[0]["content"]

            _write_skill(skills, "dynamic-skill", "A dynamically added skill")
            client.auto_refresh_if_needed()

            new_content = client.messages[0]["content"]
            assert new_content != old_content
            assert "dynamic-skill" in new_content
        finally:
            self._teardown(patchers)

    def test_new_info_triggers_refresh(self, tmp_path: Path):
        """新增 info 文件后 system prompt 应刷新。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            old_content = client.messages[0]["content"]

            _write_info(info, "dynamic-info", "Dynamic info entry")
            client.auto_refresh_if_needed()

            new_content = client.messages[0]["content"]
            assert new_content != old_content
            assert "dynamic-info" in new_content
        finally:
            self._teardown(patchers)

    def test_memory_md_change_triggers_refresh(self, tmp_path: Path):
        """MEMORY.md 内容变化后应刷新。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            old_content = client.messages[0]["content"]

            time.sleep(0.05)
            (tmp_path / "MEMORY.md").write_text("# Updated Identity\nNew identity.", encoding="utf-8")
            client.auto_refresh_if_needed()

            new_content = client.messages[0]["content"]
            assert new_content != old_content
            assert "Updated Identity" in new_content
        finally:
            self._teardown(patchers)

    def test_refresh_preserves_conversation_history(self, tmp_path: Path):
        """刷新 system prompt 不应丢失对话历史。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            client.add_user_message("Hello")
            client.add_tool_result("call_1", "result")

            _write_project(projects, "new-proj", "# new-proj")
            client.auto_refresh_if_needed()

            # system + user + tool 三条消息都在
            assert len(client.messages) == 3
            assert client.messages[0]["role"] == "system"
            assert client.messages[1]["role"] == "user"
            assert client.messages[2]["role"] == "tool"
            assert "new-proj" in client.messages[0]["content"]
        finally:
            self._teardown(patchers)

    def test_consecutive_refreshes_idempotent(self, tmp_path: Path):
        """连续两次 refresh 不应产生重复内容或异常。"""
        skills, projects, info = _setup_dirs(tmp_path)
        client, patchers = self._setup_client(tmp_path, skills, projects, info)
        try:
            _write_project(projects, "stable-proj", "# stable")
            client.auto_refresh_if_needed()
            content_after_first = client.messages[0]["content"]

            # 第二次触发（fingerprint 已更新，不会再变）
            client.auto_refresh_if_needed()
            content_after_second = client.messages[0]["content"]

            assert content_after_first == content_after_second
        finally:
            self._teardown(patchers)


# ── prompt_builder 缓存失效测试 ─────────────────────────────────────────

class TestPromptBuilderCacheInvalidation:
    """测试 prompt_builder 的 mtime fingerprint 缓存失效机制。"""

    def test_skills_cache_invalidated_on_new_skill(self, tmp_path: Path):
        """新增 skill 后缓存失效，重新构建。"""
        skills = tmp_path / "skills"
        projects = tmp_path / "projects"
        info = tmp_path / "info"
        skills.mkdir()
        projects.mkdir()
        info.mkdir()

        with patch("src.core.prompt_builder.SKILLS_DIR", skills), \
             patch("src.core.prompt_builder.PROJECTS_DIR", projects), \
             patch("src.core.prompt_builder.INFO_DIR", info), \
             patch("src.core.prompt_builder.MEMORY_PATH", tmp_path / "MEMORY.md"), \
             patch("src.core.prompt_builder.USER_PATH", tmp_path / "USER.md"), \
             patch("src.core.prompt_builder.LAMIX_DIR", tmp_path):
            pb._skills_index_cache = None
            pb._projects_index_cache = None
            pb._info_index_cache = None

            result1 = pb.build_skills_index()
            assert result1 == ""

            _write_skill(skills, "new-skill", "Fresh skill")
            result2 = pb.build_skills_index()

            assert "new-skill" in result2

    def test_projects_cache_invalidated_on_new_project(self, tmp_path: Path):
        """新增 project 后缓存失效。"""
        skills = tmp_path / "skills"
        projects = tmp_path / "projects"
        info = tmp_path / "info"
        skills.mkdir()
        projects.mkdir()
        info.mkdir()

        with patch("src.core.prompt_builder.SKILLS_DIR", skills), \
             patch("src.core.prompt_builder.PROJECTS_DIR", projects), \
             patch("src.core.prompt_builder.INFO_DIR", info), \
             patch("src.core.prompt_builder.MEMORY_PATH", tmp_path / "MEMORY.md"), \
             patch("src.core.prompt_builder.USER_PATH", tmp_path / "USER.md"), \
             patch("src.core.prompt_builder.LAMIX_DIR", tmp_path):
            pb._skills_index_cache = None
            pb._projects_index_cache = None
            pb._info_index_cache = None

            result1 = pb.build_project_index()
            # 空目录也返回标题模板，断言不含项目条目
            assert "new-project" not in result1

            _write_project(projects, "new-project")
            result2 = pb.build_project_index()

            assert "new-project" in result2

    def test_info_cache_invalidated_on_new_info(self, tmp_path: Path):
        """新增 info 后缓存失效。"""
        skills = tmp_path / "skills"
        projects = tmp_path / "projects"
        info = tmp_path / "info"
        skills.mkdir()
        projects.mkdir()
        info.mkdir()

        with patch("src.core.prompt_builder.SKILLS_DIR", skills), \
             patch("src.core.prompt_builder.PROJECTS_DIR", projects), \
             patch("src.core.prompt_builder.INFO_DIR", info), \
             patch("src.core.prompt_builder.MEMORY_PATH", tmp_path / "MEMORY.md"), \
             patch("src.core.prompt_builder.USER_PATH", tmp_path / "USER.md"), \
             patch("src.core.prompt_builder.LAMIX_DIR", tmp_path):
            pb._skills_index_cache = None
            pb._projects_index_cache = None
            pb._info_index_cache = None

            result1 = pb.build_info_index()
            # 空目录也返回标题模板，断言不含 info 条目
            assert "new-info" not in result1

            _write_info(info, "new-info", "Fresh info")
            result2 = pb.build_info_index()

            assert "new-info" in result2
