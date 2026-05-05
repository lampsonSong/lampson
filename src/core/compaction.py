"""Context Compaction：对话归档系统。

设计原则：
- 归档而不是丢弃：有价值的内容沉淀到 skill / project 文件
- 保留原始消息：只对必须压缩的部分做归档，不反复摘要造成信息损耗
- 崩溃可恢复：segment_boundary 含 archive 字段，resume 时可重建上下文

Archive Phase 三步流水线（原子性保障）：
1. Classify（LLM 分类，不涉及写入）
2. Read（读已有 skill/project 内容）
3. Integrate（写 segment_boundary + skill/project + compaction_log）

触发条件：Token 估算 >= context_window * trigger_threshold，且 stopReason 为 end_turn/aborted。

设计文档：docs/compaction-design.md
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _notify_progress(cb: Callable[[str], None] | None, msg: str) -> None:
    if cb is None:
        return
    try:
        cb(msg)
    except Exception as e:
        logger.debug("Compaction progress_callback 失败: %s", e)

# ── 目录常量 ──────────────────────────────────────────────────────────────────

LAMPSON_DIR = Path.home() / ".lampson"
SKILLS_DIR = LAMPSON_DIR / "skills"
PROJECTS_DIR = LAMPSON_DIR / "projects"
COMPACTION_LOG = LAMPSON_DIR / ".compaction_log.jsonl"

# ── 配置 ──────────────────────────────────────────────────────────────────────

STOP_REASONS = {"end_turn", "aborted"}
END_THRESHOLD_PERCENT = 80.0
DEFAULT_CONTEXT_WINDOW = 131_072
DEFAULT_TRIGGER_THRESHOLD = 0.8


@dataclass
class CompactionConfig:
    """压缩配置。"""

    context_window: int = DEFAULT_CONTEXT_WINDOW
    trigger_threshold: float = DEFAULT_TRIGGER_THRESHOLD
    end_threshold_percent: float = END_THRESHOLD_PERCENT
    max_archive_per_compaction: int = 20  # 防止一次归档太多条目
    compaction_log_max_bytes: int = 10 * 1024 * 1024  # 10MB 轮转
    keep_recent_n: int = 3               # 保留最近 N 轮不做摘要
    summary_trigger_ratio: float = 0.5   # 阶段一后仍超过此比例则触发阶段二

    def should_trigger(self, estimated_tokens: int, stop_reason: str | None) -> bool:
        """判断是否应该触发归档。"""
        if stop_reason not in STOP_REASONS:
            return False
        threshold_tokens = self.context_window * self.trigger_threshold
        return estimated_tokens >= threshold_tokens


# ── 数据类 ─────────────────────────────────────────────────────────────────────

@dataclass
class CompactionResult:
    """压缩结果。"""

    success: bool
    summary: str = ""
    messages_kept: list[dict[str, Any]] = field(default_factory=list)
    archived_count: int = 0
    archive_details: str = ""
    archive_targets: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


# ── Prompt 模板 ───────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """你是归档分类助手。根据以下对话历史，输出 JSON 格式的分类决策。

## 分类标准

- `keep`: 有上下文价值，且无法简单总结（如用户正在讨论的问题、需要继续的任务）
- `archive`: 可以提炼沉淀到文件的内容（如：技术方案、决策、用户偏好、踩坑记录、工具使用心得）
- `discard`: 纯粹闲聊、礼貌性回复、无效内容

## 工具调用结果处理

assistant 消息的 `referenced_tool_results` 字段记录了该回复引用了哪些 tool_call id（如 `["call_001"]`）。该字段由 Agent 在写入 JSONL 时生成，已持久化。

分类时：
1. 如果 tool_call id 在某条 assistant 消息的 `referenced_tool_results` 中出现 → action = "keep"
2. 如果没被任何 assistant 引用 → action = "discard"
3. 如果有价值值得归档 → action = "archive"，target 为相关 skill 或 project

## 输出格式

只输出 JSON，不要其他内容：
{
  "decisions": [
    {"msg_id": "msg_001", "action": "keep|archive|discard", "target": "skill:xxx|project:xxx|null", "reason": "原因"}
  ],
  "tool_refs": {
    "call_001": {
      "referenced_by": ["msg_005"],
      "action": "keep|discard",
      "reason": "原因"
    }
  }
}"""


_SUMMARY_PROMPT = """你是一个对话摘要助手。以下是一段对话历史，请按主题分组总结。

要求：
1. 按讨论的主题分组，每个主题一段
2. 每段包含：讨论了什么、做出了什么决定/结论、是否有待办事项
3. 保留所有关键信息（文件路径、命令、决策、偏好），不要遗漏
4. 总长度不超过原始对话的 30%

## 对话历史

{messages_text}"""


# ── Compactor ─────────────────────────────────────────────────────────────────

class Compactor:
    """归档压缩器：Classify → Read → Integrate 三步流水线。"""

    def __init__(self, llm: Any, config: CompactionConfig | None = None) -> None:
        self.llm = llm
        self.config = config or CompactionConfig()

    def compact(
        self,
        messages: list[dict[str, Any]],
        session_store: Any = None,
        session_id: str = "",
        progress_callback: Callable[[str], None] | None = None,
    ) -> CompactionResult:
        """执行归档流水线。

        **调用方**：Agent 运行时由 `maybe_compact()` 调用，不在 Session 退出时调用。

        触发条件：Token 估算 >= context_window * trigger_threshold，且 stopReason 为 end_turn/aborted。

        步骤顺序（原子性保障）：
        1. LLM 分类（不涉及写入）
        2. 写 segment_boundary 到 session JSONL（含 archive 字段）
        3. 读已有 skill/project 内容
        4. 整合写入 skill/project 归档文件
        5. 写 compaction 日志
        6. 构建剩余消息返回

        Returns:
            CompactionResult 包含归档信息和剩余消息列表。
        """
        if not messages:
            return CompactionResult(success=False, error="空消息列表")

        # Step 1: LLM 分类（不涉及写入）
        # 分两批 classify：先后半（最近的），再前半（更早的），避免单次 prompt 超长
        existing_files = _list_existing_files()
        mid = len(messages) // 2
        # 后半 = 索引 [mid:]（最近的对话）
        later_half = messages[mid:]
        # 前半 = 索引 [:mid]（更早的对话）
        earlier_half = messages[:mid]

        all_decisions: list[dict[str, Any]] = []
        all_tool_refs: dict[str, Any] = {}

        # 批次一：classify 后半（最近）
        _notify_progress(progress_callback, "[1/6] 正在分析最近对话...")
        if later_half:
            try:
                result = _classify_messages(later_half, existing_files, self.llm)
                all_decisions.extend(result.get("decisions", []))
                all_tool_refs.update(result.get("tool_refs", {}))
            except Exception as e:
                logger.warning(f"Compaction classify 后半失败: {e}")

        # 批次二：classify 前半（更早）
        _notify_progress(progress_callback, "[1/6] 正在分析早期对话...")
        if earlier_half:
            try:
                result = _classify_messages(earlier_half, existing_files, self.llm)
                all_decisions.extend(result.get("decisions", []))
                all_tool_refs.update(result.get("tool_refs", {}))
            except Exception as e:
                logger.warning(f"Compaction classify 前半失败: {e}")

        # Fallback: classify 全部失败时，保留最近 N 轮对话
        if not all_decisions and not all_tool_refs:
            logger.warning("Compaction classify 全部失败，回退到保留最近 N 轮策略")
            _notify_progress(progress_callback, "[回退] classify 失败，仅保留最近 N 轮对话")
            recent_msgs, _ = _split_recent_turns(messages, self.config.keep_recent_n)
            _log_compaction(
                original_count=len(messages),
                decisions=[],
                tool_refs={},
                archive_targets=[],
                config=self.config,
            )
            return CompactionResult(
                success=True,
                messages_kept=recent_msgs,
                archived_count=0,
                archive_details="fallback: classify failed, kept recent turns",
            )

        decisions = all_decisions
        tool_refs = all_tool_refs

        # 提取 archive 目标列表
        archive_targets = [
            {"target": d["target"], "entry_count": 1}
            for d in decisions
            if d.get("action") == "archive" and d.get("target")
        ]
        # 去重
        seen: set[str] = set()
        unique_targets: list[dict[str, Any]] = []
        for t in archive_targets:
            if t["target"] not in seen:
                seen.add(t["target"])
                unique_targets.append(t)

        # 限制每次归档数量
        if len(unique_targets) > self.config.max_archive_per_compaction:
            unique_targets = unique_targets[: self.config.max_archive_per_compaction]

        archived_count = len(unique_targets)

        # Step 2: 写 segment_boundary 到 session JSONL（原子性保障的核心）
        _notify_progress(progress_callback, "[2/6] 正在写入会话边界...")
        if session_store is not None and session_id:
            _write_segment_boundary(messages, unique_targets, session_id, session_store)

        # Step 3: 读已有 skill/project 内容
        # Step 4: 整合写入
        _notify_progress(progress_callback, "[3/6] 正在读取已有归档文件...")
        _notify_progress(progress_callback, "[4/6] 正在写入归档文件...")
        details_parts: list[str] = []
        for d in decisions:
            action = d.get("action", "keep")
            reason = d.get("reason", "")
            details_parts.append(f"  [{action}] {reason}")

        try:
            _write_archive_entries(decisions, messages)
        except Exception as e:
            logger.warning(f"Compaction 归档写入失败: {e}")
            # 不算失败，归档文件错误不影响核心功能

        # Step 5: 写 compaction 日志
        _notify_progress(progress_callback, "[5/6] 正在写入压缩日志...")
        try:
            _log_compaction(
                original_count=len(messages),
                decisions=decisions,
                tool_refs=tool_refs,
                archive_targets=unique_targets,
                config=self.config,
            )
        except Exception as e:
            logger.warning(f"Compaction 日志写入失败: {e}")

        # Step 6: 构建剩余消息列表
        _notify_progress(progress_callback, "[6/6] 正在构建剩余上下文...")
        remaining = _build_remaining_messages(
            messages, decisions, tool_refs, self.config.keep_recent_n
        )

        # Step 7: 阶段二 — 摘要压缩（条件触发）
        original_tokens = _estimate_messages_tokens(messages)
        remaining_tokens = _estimate_messages_tokens(remaining)

        if (
            original_tokens > 0
            and remaining_tokens > original_tokens * self.config.summary_trigger_ratio
        ):
            logger.info(
                f"Compaction 阶段一后仍占 {remaining_tokens/original_tokens:.0%}，触发阶段二摘要"
            )
            _notify_progress(progress_callback, "[摘要] 正在生成对话摘要...")
            remaining = self._summarize_keep_messages(remaining, messages)

        _notify_progress(
            progress_callback,
            f"[完成] 归档 {archived_count} 条内容，保留 {len(remaining)} 条消息",
        )

        return CompactionResult(
            success=True,
            summary="",
            messages_kept=remaining,
            archived_count=archived_count,
            archive_details="\n".join(details_parts),
            archive_targets=unique_targets,
        )


    def _summarize_keep_messages(
        self,
        remaining: list[dict[str, Any]],
        original_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """阶段二：对除最近 N 轮外的 keep 消息做 LLM 分组摘要。"""
        keep_recent_n = self.config.keep_recent_n

        recent_messages, older_messages = _split_recent_turns(remaining, keep_recent_n)

        if not older_messages:
            return remaining

        # 跳过已有的 compaction summary（避免"摘要的摘要"）
        older_messages = [m for m in older_messages if not m.get("is_compaction_summary")]

        if not older_messages:
            return remaining

        summary_text = self._generate_summary(older_messages)

        summary_msg = {
            "role": "assistant",
            "content": f"## 对话摘要\n\n{summary_text}",
            "is_compaction_summary": True,
        }

        logger.info(f"Compaction 阶段二：生成摘要 {len(older_messages)} → 1 条 summary message")

        return [summary_msg] + recent_messages

    def _generate_summary(self, messages: list[dict[str, Any]]) -> str:
        """用 LLM 对消息列表生成分组摘要。"""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = _extract_content(msg.get("content", ""))
            if content:
                truncated = content[:500] + ("..." if len(content) > 500 else "")
                lines.append(f"[{role}] {truncated}")

        messages_text = "\n".join(lines)
        prompt = _SUMMARY_PROMPT.format(messages_text=messages_text)

        temp_client = _make_temp_client(self.llm)
        temp_client.messages = [{"role": "system", "content": "你是一个简洁准确的对话摘要助手。"}]
        temp_client.add_user_message(prompt)

        try:
            response = temp_client.chat()
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning(f"摘要生成失败: {e}")
            return "（摘要生成失败，原始对话已被归档）"


# ── Step 1: Classify ─────────────────────────────────────────────────────────

MAX_CLASSIFY_BATCH = 30  # 每批最多分类的消息数

def _classify_messages(
    messages: list[dict[str, Any]],
    existing_files: dict[str, str],
    llm: Any,
) -> dict[str, Any]:
    """Step 1：LLM 分类，不做写入。"""
    # 限制批量大小，避免 prompt 过长导致超时
    if len(messages) > MAX_CLASSIFY_BATCH:
        logger.info(f"classify batch 截断: {len(messages)} -> {MAX_CLASSIFY_BATCH}")
        messages = messages[:MAX_CLASSIFY_BATCH]
    prompt = _build_classify_prompt(messages, existing_files)

    # 调用 LLM（通过 llm.messages 接口）
    temp_client = _make_temp_client(llm)
    temp_client.messages = []
    temp_client.messages = [{"role": "system", "content": _CLASSIFY_SYSTEM}]
    temp_client.add_user_message(prompt)
    try:
        response = temp_client.chat()
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败: {e}") from e

    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        raise RuntimeError("LLM 返回为空")

    parsed = _parse_json(raw)
    if parsed is None:
        raise RuntimeError(f"JSON 解析失败: {raw[:200]}")
    return parsed


def _make_temp_client(llm: Any) -> Any:
    """复用 llm 的连接参数创建临时客户端。"""
    from src.core.llm import LLMClient

    client = llm.client
    return LLMClient(
        api_key=client.api_key if hasattr(client, "api_key") else getattr(llm, "_api_key", ""),
        base_url=str(client.base_url) if hasattr(client, "base_url") else getattr(llm, "_base_url", ""),
        model=llm.model,
        timeout=600.0,
    )


def _build_classify_prompt(messages: list[dict[str, Any]], existing_files: dict[str, str]) -> str:
    """构建 LLM 分类 prompt。"""
    existing_summary = "\n".join(
        f"- {k}: {v[:200]}"
        for k, v in existing_files.items()
    ) if existing_files else "(无已有文件)"

    lines = ["## 当前已有文件摘要\n" + existing_summary, "", "## 对话历史\n"]
    for msg in messages:
        msg_id = msg.get("id", msg.get("msg_id", f"msg_{id(msg)}"))
        role = msg.get("role", "unknown")
        content = _extract_content(msg.get("content", ""))
        refs = msg.get("referenced_tool_results", [])
        ref_note = f" (引用了 tool: {', '.join(refs)})" if refs else ""
        # tool 角色用 tool_call id
        if role == "tool":
            tool_call_id = msg.get("tool_call_id", msg.get("id", "unknown"))
            lines.append(f"[{tool_call_id}] tool_result: {content[:150]}{ref_note}")
        else:
            truncated = content[:150] + ("..." if len(content) > 150 else "")
            lines.append(f"[{msg_id}] {role}: {truncated}{ref_note}")

    return "\n".join(lines)


# ── Step 2: segment_boundary ─────────────────────────────────────────────────

def _write_segment_boundary(
    messages: list[dict[str, Any]],
    archive_targets: list[dict[str, Any]],
    session_id: str,
    session_store: Any,
) -> None:
    """写入 segment_boundary 到 session JSONL，并同步 segments 表。"""
    if not session_id:
        return

    # 当前 segment 号 = 最后一条消息的 segment
    current_segment = messages[-1].get("segment", 0)
    ts = int(datetime.now().timestamp() * 1000)

    try:
        session_store.write_segment_boundary(
            session_id=session_id,
            segment=current_segment,
            next_segment_started_at=ts,
            archive=archive_targets or None,
        )
    except Exception as e:
        logger.warning(f"segment_boundary 写入失败: {e}")


# ── Step 3-4: Read + Integrate ───────────────────────────────────────────────

def _list_existing_files() -> dict[str, str]:
    """列出所有现有 skill 和 project 文件的前 200 字摘要。"""
    result: dict[str, str] = {}
    for path in SKILLS_DIR.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")[:200]
            result[f"skill:{path.stem}"] = content
        except OSError:
            pass
    for path in PROJECTS_DIR.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")[:200]
            result[f"project:{path.stem}"] = content
        except OSError:
            pass
    return result


def _write_archive_entries(decisions: list[dict[str, Any]], messages: list[dict[str, Any]]) -> None:
    """将 archive 决策写入对应文件。"""
    msg_map: dict[str, dict[str, Any]] = {}
    for m in messages:
        # 支持多种 id 字段名
        mid = m.get("id") or m.get("msg_id") or str(id(m))
        msg_map[mid] = m

    # 按 target 分组
    by_target: dict[str, list] = {}
    for d in decisions:
        if d.get("action") == "archive" and d.get("target"):
            by_target.setdefault(d["target"], []).append(d)

    for target, entries in by_target.items():
        existing = _read_target_file(target)
        new_content = _integrate(entries, existing, target, msg_map)
        path = _target_to_path(target)
        _safe_write(path, new_content)


def _read_target_file(target: str) -> str:
    """读取已有文件内容。"""
    path = _target_to_path(target)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _target_to_path(target: str) -> Path | None:
    """将 target 字符串转为文件 Path。"""
    if target.startswith("skill:"):
        return SKILLS_DIR / f"{target[6:]}.md"
    elif target.startswith("project:"):
        return PROJECTS_DIR / f"{target[8:]}.md"
    return None


def _integrate(entries: list[dict[str, Any]], existing: str, target: str, msg_map: dict[str, Any]) -> str:
    """只追加，不做 merge/update/evict。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    for e in entries:
        mid = e.get("msg_id", "")
        if mid in msg_map:
            content = _extract_content(msg_map[mid].get("content", ""))
            lines.append(f"- {content} _(归档: {timestamp})_")
    if not lines:
        return existing
    new_entries = "\n".join(lines)
    return f"{existing}\n{new_entries}\n"


def _safe_write(path: Path, content: str) -> None:
    """写前备份，安全写入。"""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(".md.bak"))
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.warning(f"safe_write 失败 {path}: {e}")


# ── Step 5: compaction_log ───────────────────────────────────────────────────

def _log_compaction(
    original_count: int,
    decisions: list[dict[str, Any]],
    tool_refs: dict[str, Any],
    archive_targets: list[dict[str, Any]],
    config: CompactionConfig,
) -> None:
    """写压缩操作日志，超过 max_bytes 自动轮转。"""
    COMPACTION_LOG.parent.mkdir(parents=True, exist_ok=True)

    if COMPACTION_LOG.exists() and COMPACTION_LOG.stat().st_size > config.compaction_log_max_bytes:
        _rotate_compaction_log()

    with open(COMPACTION_LOG, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now().isoformat(),
                    "original_count": original_count,
                    "decisions": decisions,
                    "tool_refs": tool_refs,
                    "archive_targets": archive_targets,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _rotate_compaction_log() -> None:
    """轮转压缩日志：.compaction_log.jsonl → .1 → .2 → ... → .5。"""
    for i in range(4, 0, -1):
        src = COMPACTION_LOG.with_suffix(f".jsonl.{i}")
        dst = COMPACTION_LOG.with_suffix(f".jsonl.{i + 1}")
        if src.exists():
            shutil.move(str(src), str(dst))
    shutil.move(str(COMPACTION_LOG), str(COMPACTION_LOG.with_suffix(".jsonl.1")))


# ── Step 6: 构建剩余消息 ─────────────────────────────────────────────────────

def _split_recent_turns(
    messages: list[dict[str, Any]], keep_recent_n: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """将消息列表分为最近 N 轮和其余部分。

    一轮 = 从一条 user 消息到下一条 user 消息之前的所有消息。
    """
    # 找到所有 user 消息的索引
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]

    if len(user_indices) <= keep_recent_n:
        return messages, []  # 全部是最近的

    # 分割点：倒数第 N 个 user 消息
    split_idx = user_indices[-keep_recent_n]

    older = messages[:split_idx]
    recent = messages[split_idx:]
    return recent, older


def _build_remaining_messages(
    messages: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    tool_refs: dict[str, Any],
    keep_recent_n: int = 3,
) -> list[dict[str, Any]]:
    """保留 keep 列表 + 最近 N 条，原始消息不摘要。"""
    keep_ids: set[str] = set()
    for d in decisions:
        if d.get("action") == "keep":
            keep_ids.add(d.get("msg_id", ""))

    # tool 结果按 tool_call_id 判断
    for tool_id, ref_info in tool_refs.items():
        if ref_info.get("action") == "keep":
            keep_ids.add(tool_id)

    def _msg_matched_by_keep_id(msg: dict[str, Any]) -> bool:
        for key in ("id", "msg_id", "tool_call_id"):
            v = msg.get(key)
            if v and v in keep_ids:
                return True
        return False

    remaining = [msg for msg in messages if _msg_matched_by_keep_id(msg)]

    # 追加最近 N 条保障连贯
    recent = [msg for msg in messages[-keep_recent_n:] if not _msg_matched_by_keep_id(msg)]
    for msg in recent:
        if msg not in remaining:
            remaining.append(msg)

    return remaining


# ── 工具方法 ─────────────────────────────────────────────────────────────────

def _extract_content(content: str | list[Any] | None) -> str:
    """从消息的 content 字段提取可读文本（支持 list block 格式）。"""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_call":
                name = block.get("name", "unknown")
                args = block.get("arguments", {})
                if isinstance(args, dict):
                    args_str = json.dumps(args, ensure_ascii=False)
                else:
                    args_str = str(args)
                parts.append(f"<tool_call:{name}>\n{args_str}\n</tool_call:{name}>")
            elif btype == "toolResult":
                parts.append(f"[tool result]: {block.get('content', '')}")
            elif btype == "thinking":
                parts.append(f"[thinking]: {block.get('thinking', '')}")
        return "\n".join(parts)
    return str(content)


def _parse_json(text: str | None) -> dict[str, Any] | None:
    """从 LLM 回复中提取并解析 JSON。"""
    if not text:
        return None
    text = text.strip()

    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ── Agent 集成 ───────────────────────────────────────────────────────────────

def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算消息列表的 token 总数（粗略：UTF-8 字节数 / 4）。"""
    try:
        serialized = json.dumps(messages, ensure_ascii=False)
        return len(serialized.encode("utf-8")) // 4
    except Exception:
        return 0


def apply_compaction(
    agent_llm: Any,
    config: CompactionConfig,
    estimated_tokens: int,
    stop_reason: str | None = None,
    session_id: str = "",
    session_store: Any = None,
    *,
    force: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> CompactionResult | None:
    """检查并执行压缩。

    在 Agent.run() 返回后调用。
    如果超过阈值且 stopReason 允许，执行归档并返回结果。

    支持循环压缩：如果一次压缩后 token 仍超限，会逐步增加 keep_recent_n
    再次压缩，最多重试 _MAX_COMPACTION_RETRIES 次。最后 fallback 到只保留
    最近一轮对话。

    Args:
        force: 强制触发压缩，忽略 token 阈值和 stop_reason 检查。
            由 force_compact() 使用，避免用伪造 token 数绕过检查。
        progress_callback: 可选，Compaction 进度文案回调。

    Returns:
        CompactionResult（触发了压缩）或 None（不需要压缩）。
    """
    if not force and not config.should_trigger(estimated_tokens, stop_reason):
        return None

    # 提取非 system 消息
    messages = [m for m in agent_llm.messages if m.get("role") != "system"]
    if not messages:
        return None

    threshold_tokens = int(config.context_window * config.trigger_threshold)
    system_msg = agent_llm.messages[0] if agent_llm.messages else {}

    # 单次压缩，不依赖 bytes/4 估算判断成功与否
    # bytes/4 在 context overflow 后 last_prompt_tokens=0 时严重不准
    # 压缩是否足够，由 _run_tool_loop 重新调用 LLM 来验证（靠实际错误判断）
    compactor = Compactor(llm=agent_llm, config=config)
    result = compactor.compact(
        messages,
        session_store=session_store,
        session_id=session_id,
        progress_callback=progress_callback,
    )

    if not result.success or not result.messages_kept:
        return result

    # 更新 agent messages（压缩结果）
    agent_llm.messages = [system_msg] + result.messages_kept

    # Fallback 安全网：如果压缩后消息已很少但 token 估算仍超阈值，
    # 说明 system prompt 本身可能过长，此时返回 success 让外层继续试 LLM call
    if len(result.messages_kept) <= 3:
        new_estimated = _estimate_messages_tokens(agent_llm.messages)
        if new_estimated >= threshold_tokens:
            logger.warning(
                f"Compaction 后消息已很少（{len(result.messages_kept)} 条）"
                f"但 token 估算仍超（{new_estimated} >= {threshold_tokens}），"
                f"system prompt 可能过长，仍返回成功由外层验证"
            )

    return result
