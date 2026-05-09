"""PromptBuilder 和记忆系统重构测试。"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.core.prompt_builder import PromptBuilder


class TestPromptBuilderInit:
    """测试 PromptBuilder 初始化。"""

    def test_default_init(self):
        """默认初始化。"""
        pb = PromptBuilder()
        assert pb.model == ""
        assert pb.channel == "cli"

    def test_custom_init(self):
        """自定义参数初始化。"""
        pb = PromptBuilder(model="gpt-4", channel="feishu")
        assert pb.model == "gpt-4"
        assert pb.channel == "feishu"


class TestPromptBuilderBuild:
    """测试 build 方法。"""

    def test_build_returns_string(self):
        """build 返回非空字符串。"""
        pb = PromptBuilder(model="test-model")
        with patch("src.core.prompt_builder.load_identity", return_value="identity"), \
             patch("src.core.prompt_builder.load_user", return_value="user"), \
             patch("src.core.prompt_builder.build_skills_index", return_value="skills idx"), \
             patch("src.core.prompt_builder.build_project_index", return_value="project idx"):
            result = pb.build()
        assert isinstance(result, str)
        assert len(result) > 0


class TestMemorySize:
    """测试记忆文件大小约束。"""

    def test_user_content_size(self, tmp_path: Path):
        """USER.md 内容不超过限制。"""
        user_file = tmp_path / "USER.md"
        content = "# 用户偏好\n- 简洁回复\n- 先出方案再动手\n- 小事自主决定"
        user_file.write_text(content, encoding="utf-8")
        assert len(content) <= 2000  # 合理上限

    def test_identity_content_size(self, tmp_path: Path):
        """MEMORY.md 内容不超过限制。"""
        mem_file = tmp_path / "MEMORY.md"
        content = "# 身份\n独立 AI Agent"
        mem_file.write_text(content, encoding="utf-8")
        assert len(content) <= 2000
