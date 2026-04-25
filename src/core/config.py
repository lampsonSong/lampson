"""配置管理模块：加载、保存、引导用户填写 ~/.lampson/config.yaml"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


LAMPSON_DIR = Path.home() / ".lampson"
CONFIG_PATH = LAMPSON_DIR / "config.yaml"
MEMORY_DIR = LAMPSON_DIR / "memory"
SKILLS_DIR = LAMPSON_DIR / "skills"

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
}

# Pattern to match ${ENV_VAR} placeholders
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def ensure_dirs() -> None:
    """确保 ~/.lampson 及子目录存在。"""
    LAMPSON_DIR.mkdir(exist_ok=True)
    MEMORY_DIR.mkdir(exist_ok=True)
    (MEMORY_DIR / "sessions").mkdir(exist_ok=True)
    SKILLS_DIR.mkdir(exist_ok=True)


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
