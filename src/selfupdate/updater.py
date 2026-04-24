"""自更新逻辑：LLM 分析需求 → 生成修改方案 → 用户确认 → git 分支执行。

流程：
  1. 用户输入需求描述
  2. LLM 生成修改方案（文件列表 + 各文件完整内容）
  3. 展示方案给用户确认
  4. 确认后：git checkout -b self-update/<timestamp>，写入文件，git commit
  5. 用户可随时 /update rollback 回滚到 main
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.core.llm import LLMClient


# 受保护文件（相对于项目根目录），自更新时如果涉及这些文件需要额外确认
PROTECTED_FILES = {
    "src/cli.py",
    "src/core/agent.py",
    "src/core/llm.py",
    "src/feishu/client.py",
    "src/tools/shell.py",
}

UPDATE_SYSTEM_PROMPT = """你是 Lampson 的自更新助手。用户会给你一个需求描述，你需要分析并生成代码修改方案。

响应格式必须是合法的 JSON，结构如下：
{
  "summary": "修改方案的简要描述（1-2句话）",
  "files": [
    {
      "path": "相对于项目根目录的文件路径，如 src/core/tools.py",
      "action": "create 或 modify",
      "content": "文件的完整新内容（不能省略，不能写 TODO）",
      "reason": "为什么要修改这个文件"
    }
  ]
}

注意：
- 只修改必要的文件
- content 字段必须是完整可运行的代码，不能有 TODO 或 placeholder
- 路径相对于项目根目录（lampson/）
- 如果不需要修改任何文件（纯对话），files 返回空数组
"""


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """执行 git 命令，返回 (returncode, stdout, stderr)。"""
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _find_project_root() -> Path:
    """向上查找 pyproject.toml 确定项目根目录。"""
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _check_git_clean(project_root: Path) -> tuple[bool, str]:
    """检查 git 工作区是否干净。"""
    code, out, _ = _run_git(["status", "--porcelain"], project_root)
    if code != 0:
        return False, "无法检查 git 状态，请确认当前目录是 git 仓库。"
    if out:
        return False, f"工作区有未提交的修改：\n{out}\n请先 git stash 或 commit 后再执行自更新。"
    return True, ""


def _get_current_branch(project_root: Path) -> str:
    _, out, _ = _run_git(["branch", "--show-current"], project_root)
    return out or "main"


def _generate_update_plan(description: str, llm: LLMClient) -> dict[str, Any]:
    """调用 LLM 生成修改方案，返回解析后的 dict。"""
        # 注意：此处 future 可扩展异常处理，但目前依赖 LLMClient 内部抛出的 RuntimeError

    temp_client = LLMClient(
        api_key=llm.client.api_key,
        base_url=str(llm.client.base_url),
        model=llm.model,
    )
    temp_client.messages = [
        {"role": "system", "content": UPDATE_SYSTEM_PROMPT},
        {"role": "user", "content": f"需求：{description}"},
    ]

    try:
        response = temp_client.chat()
        raw = response.choices[0].message.content or "{}"
        # 提取 JSON（有时 LLM 会在代码块中包裹）
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM 返回的方案无法解析为 JSON：{e}\n原始内容：{raw[:500]}")
    except RuntimeError:
        raise


def _display_plan(plan: dict[str, Any]) -> None:
    """将修改方案格式化展示给用户。"""
    print(f"\n{'='*60}")
    print(f"修改方案：{plan.get('summary', '(无摘要)')}")
    print(f"{'='*60}")

    files = plan.get("files", [])
    if not files:
        print("此方案不涉及文件修改。")
        return

    for i, f in enumerate(files, 1):
        path = f.get("path", "?")
        action = f.get("action", "modify")
        reason = f.get("reason", "")
        content_lines = len(f.get("content", "").splitlines())
        protected = "⚠️ [受保护文件]" if path in PROTECTED_FILES else ""
        print(f"\n{i}. [{action.upper()}] {path} {protected}")
        print(f"   原因：{reason}")
        print(f"   代码行数：{content_lines} 行")

    print(f"\n{'='*60}")


def run_update(description: str, llm: LLMClient) -> str:
    """执行自更新流程，交互式，返回最终状态描述。"""
    if not sys.stdin.isatty():
        return "[自更新不可用] 当前运行在非交互模式（如飞书服务模式），自更新需要在 CLI REPL 中执行。"

    project_root = _find_project_root()

    # 检查 git 状态
    is_clean, msg = _check_git_clean(project_root)
    if not is_clean:
        return f"[自更新中止] {msg}"

    print("\n正在分析需求，生成修改方案...")
    try:
        plan = _generate_update_plan(description, llm)
    except RuntimeError as e:
        return f"[自更新失败] {e}"

    files = plan.get("files", [])
    if not files:
        return f"分析结果：{plan.get('summary', '无需修改代码。')}"

    _display_plan(plan)

    # 检查是否涉及受保护文件
    protected_involved = [f["path"] for f in files if f.get("path", "") in PROTECTED_FILES]
    if protected_involved:
        print(f"\n⚠️  此方案涉及受保护文件：{', '.join(protected_involved)}")
        print("受保护文件修改后可能影响系统核心功能，请谨慎确认。")

    # 用户确认
    try:
        confirm = input("\n确认执行此修改方案？(y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return "自更新已取消。"

    if confirm != "y":
        return "自更新已取消。"

    # 创建 git 分支
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch_name = f"self-update/{timestamp}"
    code, _, err = _run_git(["checkout", "-b", branch_name], project_root)
    if code != 0:
        return f"[自更新失败] 无法创建分支：{err}"

    print(f"\n已切换到分支：{branch_name}")

    # 执行文件修改
    modified: list[str] = []
    errors: list[str] = []

    for file_info in files:
        path = file_info.get("path", "")
        content = file_info.get("content", "")
        if not path:
            continue

        target = project_root / path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            modified.append(path)
            print(f"  ✓ 已写入：{path}")
        except OSError as e:
            errors.append(f"{path}: {e}")
            print(f"  ✗ 写入失败：{path} ({e})")

    if not modified:
        _run_git(["checkout", "-"], project_root)
        _run_git(["branch", "-D", branch_name], project_root)
        return f"[自更新失败] 所有文件写入失败：{errors}"

    # git add + commit
    _run_git(["add"] + modified, project_root)
    commit_msg = f"self-update: {description[:72]}"
    code, _, err = _run_git(["commit", "-m", commit_msg], project_root)
    if code != 0:
        return f"[自更新警告] 文件已修改但 commit 失败：{err}"

    result_lines = [
        f"\n自更新完成！",
        f"分支：{branch_name}",
        f"修改文件：{', '.join(modified)}",
    ]
    if errors:
        result_lines.append(f"失败文件：{', '.join(errors)}")
    result_lines.append('\n使用 "/update rollback" 可回滚到 main 分支。')

    return "\n".join(result_lines)


def run_rollback() -> str:
    """回滚自更新：切换回 main 分支并删除当前 self-update 分支。"""
    if not sys.stdin.isatty():
        return "[回滚不可用] 当前运行在非交互模式，回滚需要在 CLI REPL 中执行。"

    project_root = _find_project_root()
    current_branch = _get_current_branch(project_root)

    if not current_branch.startswith("self-update/"):
        return f"当前分支是 '{current_branch}'，不是 self-update 分支，无需回滚。"

    try:
        confirm = input(f"确认回滚？将切换回 main 并删除分支 '{current_branch}'(y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return "回滚已取消。"

    if confirm != "y":
        return "回滚已取消。"

    # 切换回 main
    code, _, err = _run_git(["checkout", "main"], project_root)
    if code != 0:
        return f"[回滚失败] 无法切换到 main：{err}"

    # 删除 self-update 分支
    code, _, err = _run_git(["branch", "-D", current_branch], project_root)
    if code != 0:
        return f"已切换到 main，但删除分支失败：{err}"

    return f"已回滚到 main，分支 '{current_branch}' 已删除。"


def list_update_branches() -> str:
    """列出所有 self-update 分支。"""
    project_root = _find_project_root()
    _, out, _ = _run_git(["branch", "--list", "self-update/*"], project_root)
    if not out:
        return "没有 self-update 分支。"
    return "self-update 分支列表：\n" + out
