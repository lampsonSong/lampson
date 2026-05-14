"""技能脚本管理器：扫描 skills/*/scripts/，动态注册为工具。

skills/<skill-name>/scripts/ 下的 .py 文件如果定义了 TOOL_SCHEMA + TOOL_RUNNER，会自动注册为工具。
通过在模块中定义 TOOL_SCHEMA + TOOL_RUNNER，可以自动将模块功能暴露为工具供 LLM 调用。

模块规范：
    1. 位于 skills/<skill-name>/scripts/ 目录下
    2. 可选定义 TOOL_SCHEMA: dict — OpenAI function calling schema
    3. 可选定义 TOOL_RUNNER: Callable[[dict], str] — 工具执行函数
    4. 如果没有 TOOL_SCHEMA，模块仍可作为普通库被其他模块调用
    5. 安全约束：禁止 import src 内部模块，只能用标准库 + 已安装的第三方库
"""

from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path
from typing import Any

from src.core.config import SKILLS_DIR

logger = logging.getLogger(__name__)

# 禁止 scripts import 的包（防止逃逸沙箱）
BLOCKED_IMPORTS = frozenset({
    "src",
    "src.core",
    "src.tools",
    "src.feishu",
    "src.skills",
    "src.memory",
    "src.platforms",
    "src.selfupdate",
    "src.planning",
})

# 已加载的模块 {skill_name/script_name: module}
_loaded_modules: dict[str, Any] = {}


def scan_and_register() -> list[dict[str, Any]]:
    """扫描 skills/*/scripts/ 目录，加载所有模块并注册为工具。

    同时扫描所有 skill 目录下的 scripts/ 子目录。
    返回注册成功的工具 schema 列表。
    """
    registered: list[dict[str, Any]] = []

    if not SKILLS_DIR.exists():
        return registered

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.is_dir():
            continue

        for py_file in sorted(scripts_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            script_name = py_file.stem
            module_key = f"{skill_dir.name}/{script_name}"

            try:
                module = _load_module(module_key, py_file)
            except Exception as e:
                logger.warning(f"加载脚本 {module_key} 失败: {e}")
                continue

            _loaded_modules[module_key] = module

            # 检查是否定义了 TOOL_SCHEMA 和 TOOL_RUNNER
            schema = getattr(module, "TOOL_SCHEMA", None)
            runner = getattr(module, "TOOL_RUNNER", None)

            if schema and runner:
                try:
                    from src.core import tools as tool_registry
                    ok = tool_registry.register_external(schema, runner)
                    if ok:
                        registered.append(schema)
                        logger.info(f"已注册技能脚本工具: {module_key}")
                    else:
                        logger.warning(f"脚本 {module_key} schema 校验未通过，已跳过工具注册")
                except Exception as e:
                    logger.warning(f"注册脚本 {module_key} 工具失败: {e}")
            else:
                logger.debug(f"脚本 {module_key} 无 TOOL_SCHEMA，跳过工具注册")

    return registered


def _load_module(module_key: str, py_file: Path) -> Any:
    """动态加载单个 Python 模块。先做静态 import 检查，不通过则拒绝加载。"""
    # 加载前静态检查危险 import
    code = py_file.read_text(encoding="utf-8")
    blocked = _check_blocked_imports(code)
    if blocked:
        raise RuntimeError(
            f"拒绝加载 {module_key}: 包含危险 import: {blocked}"
        )

    # module_key 格式: "skill-name/script-name"
    spec = importlib.util.spec_from_file_location(
        f"skills.scripts.{module_key.replace('/', '.')}",
        str(py_file),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法创建模块 spec: {module_key}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_blocked_imports(code: str) -> list[str]:
    """静态检查代码中的危险 import，返回违规列表。"""
    violations: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        # from src.xxx import ...
        m = re.match(r"^from\s+(\S+)", stripped)
        if m and m.group(1).split(".")[0] in BLOCKED_IMPORTS:
            violations.append(m.group(1))
        # import src.xxx
        m2 = re.match(r"^import\s+(src\.\S+)", stripped)
        if m2 and m2.group(1).split(".")[0] in BLOCKED_IMPORTS:
            violations.append(m2.group(1))
    return violations


def get_module(skill_name: str, script_name: str) -> Any | None:
    """获取已加载的模块对象。"""
    return _loaded_modules.get(f"{skill_name}/{script_name}")


def get_module_by_script_name(script_name: str) -> Any | None:
    """按脚本名搜索已加载的模块（遍历所有 skill）。"""
    for key, mod in _loaded_modules.items():
        if key.split("/", 1)[-1] == script_name:
            return mod
    return None


def list_modules() -> list[dict[str, str]]:
    """列出所有已加载的技能脚本。"""
    result = []
    if not SKILLS_DIR.exists():
        return result

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.exists():
            continue

        for py_file in sorted(scripts_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            script_name = py_file.stem
            module_key = f"{skill_dir.name}/{script_name}"
            module = _loaded_modules.get(module_key)
            has_tool = hasattr(module, "TOOL_SCHEMA") if module else False
            result.append({
                "name": module_key,
                "skill": skill_dir.name,
                "script": script_name,
                "path": str(py_file),
                "registered_as_tool": str(has_tool),
            })
    return result


def write_module(skill_name: str, script_name: str, code: str) -> str:
    """写入一个新的技能脚本文件到 skills/<skill_name>/scripts/，并自动注册为工具。

    Args:
        skill_name: 技能目录名
        script_name: 脚本名（不含 .py）
        code: 完整的 Python 源码

    Returns:
        操作结果描述
    """
    # 安全校验：名称合法性
    if not skill_name.replace("_", "-").replace("-", "").isalnum():
        return f"[错误] 技能名 '{skill_name}' 不合法，只允许字母、数字和连字符"

    if not script_name.replace("_", "").isalnum():
        return f"[错误] 脚本名 '{script_name}' 不合法，只允许字母、数字和下划线"

    # 安全校验：禁止 import src
    if _contains_blocked_import(code):
        return "[错误] 代码包含禁止的 import（不允许 import src 内部模块）"

    # 确保 skill 目录存在
    skill_dir = SKILLS_DIR / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    target = scripts_dir / f"{script_name}.py"

    # 如果已存在，先读出来做备份信息
    action = "更新" if target.exists() else "创建"

    try:
        target.write_text(code, encoding="utf-8")
    except OSError as e:
        return f"[错误] 写入失败: {e}"

    # 语法检查
    import py_compile
    try:
        py_compile.compile(str(target), doraise=True)
    except py_compile.PyCompileError as e:
        # 语法错误，回滚
        target.unlink(missing_ok=True)
        return f"[错误] 代码语法检查失败，已回滚: {e}"

    module_key = f"{skill_name}/{script_name}"
    logger.info(f"已{action}技能脚本: {module_key}")

    # 自动注册新写入的脚本
    try:
        module = _load_module(module_key, target)
        _loaded_modules[module_key] = module
        schema = getattr(module, "TOOL_SCHEMA", None)
        runner = getattr(module, "TOOL_RUNNER", None)
        if schema and runner:
            from src.core import tools as tool_registry
            tool_registry.register_external(schema, runner)
        logger.info(f"已自动注册脚本: {module_key}")
    except Exception as e:
        logger.warning(f"脚本写入成功但自动注册失败（需重启 daemon）: {e}")

    return f"已{action}脚本: {module_key}"


def _contains_blocked_import(code: str) -> bool:
    """检查代码是否包含禁止的 import 语句。"""
    import re
    for line in code.splitlines():
        stripped = line.strip()
        # from src.xxx import ...
        m = re.match(r"^from\s+(\S+)", stripped)
        if m:
            top = m.group(1).split(".")[0]
            if top in BLOCKED_IMPORTS:
                return True
        # import src.xxx
        m = re.match(r"^import\s+(\S+)", stripped)
        if m:
            top = m.group(1).split(".")[0]
            if top in BLOCKED_IMPORTS:
                return True
    return False


def get_module_code(skill_name: str, script_name: str) -> str | None:
    """读取指定脚本的源码。"""
    target = SKILLS_DIR / skill_name / "scripts" / f"{script_name}.py"
    if not target.exists():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return None


def get_modules_summary() -> str:
    """生成技能脚本概要，供 system prompt 注入。"""
    modules = list_modules()
    if not modules:
        return ""
    lines = ["已加载的技能脚本："]
    for m in modules:
        tool_flag = " [工具]" if m["registered_as_tool"] == "True" else ""
        lines.append(f"- {m['name']}{tool_flag}")
    return "\n".join(lines)
