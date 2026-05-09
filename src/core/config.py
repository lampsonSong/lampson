"""配置管理模块：加载、保存、引导用户填写 ~/.lampson/config.yaml"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

LAMPSON_DIR = Path.home() / ".lampson"
CONFIG_PATH = LAMPSON_DIR / "config.yaml"
MEMORY_DIR = LAMPSON_DIR / "memory"
SKILLS_DIR = LAMPSON_DIR / "memory" / "skills"
INDEX_DIR = LAMPSON_DIR / "index"
PROJECTS_DIR = LAMPSON_DIR / "memory" / "projects"
INFO_DIR = LAMPSON_DIR / "memory" / "info"

# 旧路径（迁移前）
_OLD_SKILLS_DIR = LAMPSON_DIR / "skills"
_OLD_PROJECTS_DIR = LAMPSON_DIR / "projects"

_DEFAULT_RETRIEVAL: dict[str, Any] = {
    "skill_top_k": 3,
    "project_top_k": 2,
    "similarity_threshold": 0.3,
}

_DEFAULT_EMBEDDING: dict[str, Any] = {
    "provider": "zhipu",
    "model": "embedding-3",
}

_DEFAULT_SKILLS_MANAGEMENT: dict[str, Any] = {
    "cleanup_max_skills": 300,
    "cleanup_age_days": 10,
    "cleanup_min_invocations": 0,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "api_key": "",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-5.1",
    },
    "models": [],
    "feishu": {
        "app_id": "",
        "app_secret": "",
        "chat_ids": [],
    },
    "memory_path": str(MEMORY_DIR),
    "skills_path": str(SKILLS_DIR),
    "projects_path": str(PROJECTS_DIR),
    "info_path": str(INFO_DIR),
    "retrieval": dict(_DEFAULT_RETRIEVAL),
    "skills_management": dict(_DEFAULT_SKILLS_MANAGEMENT),
}

# Pattern to match ${ENV_VAR} placeholders
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def ensure_dirs() -> None:
    """确保 ~/.lampson 及子目录存在。"""
    LAMPSON_DIR.mkdir(exist_ok=True)
    MEMORY_DIR.mkdir(exist_ok=True)
    (MEMORY_DIR / "sessions").mkdir(exist_ok=True)
    (MEMORY_DIR / "sessions" / "tool_bodies").mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    INFO_DIR.mkdir(exist_ok=True)
    INDEX_DIR.mkdir(exist_ok=True)

    _migrate_old_dirs()


def _fix_config_paths() -> None:
    """修正 config.yaml 中指向旧路径的配置项。

    迁移到 memory/ 子目录后，config.yaml 中用户显式配置的 skills_path / projects_path
    可能仍指向旧路径，导致索引扫描到空目录。此处自动更新为新路径。
    """
    if not CONFIG_PATH.exists():
        return
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return
    if not isinstance(data, dict):
        return

    changed = False
    path_fixes = {
        "skills_path": str(SKILLS_DIR),
        "projects_path": str(PROJECTS_DIR),
        "info_path": str(INFO_DIR),
        "memory_path": str(MEMORY_DIR),
    }
    for key, new_value in path_fixes.items():
        old_value = data.get(key)
        if isinstance(old_value, str) and old_value.strip():
            expanded = Path(old_value.strip()).expanduser()
            # 如果配置的路径既不是新路径，也不是旧路径的实际位置，跳过
            # 只修正指向旧路径（~/.lampson/skills 等不含 memory/）的情况
            new_path = Path(new_value).expanduser()
            if expanded.resolve() != new_path.resolve():
                # 检查是否是旧路径（不含 memory/ 子目录）
                if "memory" not in expanded.parts:
                    data[key] = new_value
                    changed = True
                    logger.info("Fixed config %s: %s -> %s", key, old_value, new_value)

    if changed:
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            logger.info("Updated config.yaml with corrected paths")
        except Exception as ex:
            logger.warning("Failed to update config.yaml paths: %s", ex)


def _migrate_old_dirs() -> None:
    import shutil
    migrated = LAMPSON_DIR / ".memory_migrated"
    if migrated.exists():
        # 即使已迁移，仍需检查 config.yaml 路径是否过时
        _fix_config_paths()
        return
    old_skills = LAMPSON_DIR / "skills"
    old_projects = LAMPSON_DIR / "projects"
    moved = False
    if old_skills.is_dir() and any(old_skills.iterdir()):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        for item in old_skills.iterdir():
            dest = SKILLS_DIR / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
                moved = True
        if not any(old_skills.iterdir()):
            old_skills.rmdir()
    if old_projects.is_dir() and any(old_projects.iterdir()):
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        for item in old_projects.iterdir():
            dest = PROJECTS_DIR / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
                moved = True
        if not any(old_projects.iterdir()):
            old_projects.rmdir()
    if moved:
        migrated.write_text("v1", encoding="utf-8")
    # 迁移完成后修正 config.yaml 中的旧路径
    _fix_config_paths()

def get_skills_management_config(config: dict[str, Any]) -> dict[str, int]:
    """合并 skills_management 段，供 SkillIndex 清理逻辑使用。"""
    sm = config.get("skills_management")
    if not isinstance(sm, dict):
        sm = {}
    base = _deep_merge(dict(_DEFAULT_SKILLS_MANAGEMENT), sm)
    return {
        "cleanup_max_skills": int(
            base.get("cleanup_max_skills", _DEFAULT_SKILLS_MANAGEMENT["cleanup_max_skills"])
        ),
        "cleanup_age_days": int(
            base.get("cleanup_age_days", _DEFAULT_SKILLS_MANAGEMENT["cleanup_age_days"])
        ),
        "cleanup_min_invocations": int(
            base.get(
                "cleanup_min_invocations",
                _DEFAULT_SKILLS_MANAGEMENT["cleanup_min_invocations"],
            )
        ),
    }


def get_retrieval_config(config: dict[str, Any]) -> dict[str, Any]:
    """合并 retrieval 段，带默认值。字段均可被 user config 覆盖。"""
    r = config.get("retrieval")
    if not isinstance(r, dict):
        r = {}
    base = _deep_merge(dict(_DEFAULT_RETRIEVAL), r)
    return {
        "skill_top_k": int(base.get("skill_top_k", _DEFAULT_RETRIEVAL["skill_top_k"])),
        "project_top_k": int(
            base.get("project_top_k", _DEFAULT_RETRIEVAL["project_top_k"])
        ),
        "similarity_threshold": float(
            base.get("similarity_threshold", _DEFAULT_RETRIEVAL["similarity_threshold"])
        ),
    }


def get_embedding_config(config: dict[str, Any]) -> dict[str, str]:
    """
    合并 embedding 段。base_url 必须显式配置（不继承 llm 段），不配则 embedding 不可用。
    返回的 api_key 也必须显式在 embedding 段指定，否则为空（降级为纯关键词搜索）。
    """
    e = config.get("embedding")
    if not isinstance(e, dict):
        e = {}
    base = _deep_merge(dict(_DEFAULT_EMBEDDING), e)
    provider = str(base.get("provider", _DEFAULT_EMBEDDING["provider"]))
    model = str(base.get("model", _DEFAULT_EMBEDDING["model"]))
    base_url = str(base.get("base_url", "") or "").strip()
    # api_key: 只取 embedding 段显式配置的值，不继承 llm 段
    api_key = str(base.get("api_key", "") or "").strip()
    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
    }


def _expand_env_vars(value: str) -> str:
    """Expand ${ENV_VAR} patterns with environment variable values."""
    if not isinstance(value, str):
        return value
    
    def replacer(m: re.Match) -> str:
        var_name = m.group(1)
        return os.environ.get(var_name, "")
    
    return _ENV_VAR_PATTERN.sub(replacer, value)


def _expand_config(obj: Any) -> Any:
    """Recursively expand env vars in config values."""
    if isinstance(obj, str):
        return _expand_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _expand_config(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_config(item) for item in obj]
    return obj


def load_config() -> dict[str, Any]:
    """加载配置文件，不存在则返回默认配置。"""
    ensure_dirs()
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    merged = _deep_merge(dict(DEFAULT_CONFIG), data)
    expanded = _expand_config(merged)
    return expanded


def save_config(config: dict[str, Any]) -> None:
    """将配置写入磁盘。"""
    ensure_dirs()
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def is_config_complete(config: dict[str, Any]) -> bool:
    """检查必填项是否已填写。base_url 必须有值，api_key 可为空。"""
    try:
        return bool(config["llm"]["base_url"])
    except (KeyError, TypeError):
        return False


def run_setup_wizard() -> dict[str, Any]:
    """首次运行引导用户填写配置，返回配置字典。"""
    print("\n欢迎使用 Lampson！首次运行需要配置一些信息。\n")

    config = load_config()

    api_key = input("请输入 API Key（内网模型可直接回车跳过）: ").strip()
    config["llm"]["api_key"] = api_key

    base_url = input(
        f"LLM Base URL（回车使用默认 {config['llm']['base_url']}）: "
    ).strip()
    if base_url:
        config["llm"]["base_url"] = base_url

    model = input(
        f"模型名（回车使用默认 {config['llm']['model']}）: "
    ).strip()
    if model:
        config["llm"]["model"] = model

    print("\n飞书配置（可选，直接回车跳过）：")
    app_id = input("飞书 App ID: ").strip()
    if app_id:
        config["feishu"]["app_id"] = app_id

    app_secret = input("飞书 App Secret: ").strip()
    if app_secret:
        config["feishu"]["app_secret"] = app_secret

    chat_ids_raw = input("要监听的飞书会话 ID（chat_id，多个用逗号分隔，回车跳过）: ").strip()
    if chat_ids_raw:
        config["feishu"]["chat_ids"] = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]

    save_config(config)
    print(f"\n配置已保存到 {CONFIG_PATH}\n")
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个字典，override 优先。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
