"""Context Compaction：记忆归档压缩。

当对话 token 超过阈值时，执行归档+摘要压缩：
1. Archive Phase：LLM分类→读取已有skill/project→遍历消息归档/丢弃/保留→重新整合写回
2. Summarize Phase：对剩余内容生成结构化摘要
3. 迭代检查：未达标则继续压缩

设计文档：docs/compaction-design.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.llm import LLMClient
from src.core.prompt_builder import PromptBuilder


# ── 配置 ──────────────────────────────────────────────────────────────────────

@dataclass
class CompactionConfig:
    """压缩配置。"""

    # 触发阈值（百分比），context tokens 超过 context_window × 此值时触发
    trigger_threshold: float = 0.8

    # 压缩结束阈值，压缩后 token 降到 context_window × 此值以下则结束
    end_threshold: float = 0.3

    # LLM context window 大小（token 数），默认 128K
    context_window: int = 131072

    # 最大迭代次数
    max_iterations: int = 3

    # 是否启用归档（写 skill/project 文件）
    enable_archive: bool = True

    def should_trigger(self, total_tokens: int) -> bool:
        return total_tokens > self.context_window * self.trigger_threshold

    def is_below_end_threshold(self, total_tokens: int) -> bool:
        return total_tokens < self.context_window * self.end_threshold


# ── LLM Prompt 模板 ────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """\
分析以下对话历史，回答两个问题：

1. 当前正在解决的问题是什么？（一句话描述）
2. 对话中涉及哪些相关项目和技能？

输出合法 JSON：
{
  "topic": "当前问题的一句话描述",
  "project_name": "相关项目名（没有则为空字符串）",
  "skill_name": "相关技能名（没有则为空字符串）"
}"""

_ARCHIVE_PROMPT = """\
你正在对一段对话进行归档整理。

## 相关上下文
- 当前问题：{topic}
- 相关项目：{project_name}
- 相关技能：{skill_name}

## 已有归档内容
{existing_content}

## 当前对话中需要归档的消息
{messages_text}

请逐条处理每条消息，判断属于以下哪种：
- **archive**：有长期价值，应归档到 skill/project 文件
- **keep**：属于当前问题的核心上下文，保留在对话窗口
- **discard**：寒暄/无关内容，直接丢弃

对于 archive 的内容，你需要和已有归档内容一起重新整合。整合策略：
- **merge**：新内容和已有内容属于同一主题 → 合并成更完整的条目
- **update**：已有条目过时 → 用新内容覆盖
- **evict**：已有内容失效或被替代 → 删除
- **append**：纯粹的新知识 → 追加

输出合法 JSON：
{{
  "classifications": [
    {{"index": 0, "action": "archive|keep|discard", "reason": "原因"}},
    ...
  ],
  "integrated_content": "重新整合后的完整归档内容（Markdown格式，包含已有内容+新内容的合并结果）",
  "archive_operations": [
    {{"type": "merge|update|evict|append", "target": "目标条目标识", "description": "操作描述"}}
  ]
}}"""

_SUMMARIZE_PROMPT = """\
以下是经过归档后，对话中剩余的核心上下文消息。请生成结构化摘要。

{messages_text}

输出合法 JSON：
{
  "problem": "当前问题的描述",
  "constraints": ["约束1", "约束2"],
  "completed": ["已完成的事项"],
  "in_progress": ["进行中的事项"],
  "blocked": ["阻塞的事项"],
  "decisions": ["关键决策：决策内容 - 理由"],
  "pending": ["待处理事项"],
  "key_files": ["关键文件路径"]
}"""


# ── 文件工具 ───────────────────────────────────────────────────────────

def _get_skill_path(skill_name: str) -> Path:
    """获取 skill 归档文件路径（~/.lampson/skills/<name>.md）。"""
    skills_dir = Path.home() / ".lampson" / "skills"
    safe_name = _sanitize(skill_name)
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir / f"{safe_name}.md"


def _get_project_path(project_name: str) -> Path:
    """获取 project 归档文件路径（~/.lampson/projects/<name>.md）。"""
    projects_dir = Path.home() / ".lampson" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize(project_name)
    return projects_dir / f"{safe_name}.md"


def _sanitize(name: str) -> str:
    """清理名称为安全文件名。"""
    return re.sub(r"[^\w\-.]", "-", name).strip("-")[:64]


def _read_file_safe(path: Path) -> str:
    """安全读取文件内容。"""
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _write_file_safe(path: Path, content: str) -> str:
    """安全写入文件。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"已写入 {path.name}"
    except OSError as e:
        return f"[写入失败] {path.name}: {e}"


# ── Compactor ─────────────────────────────────────────────────────────

class Compactor:
    """上下文压缩器：完整的归档+摘要流程。"""

    def __init__(self, llm: LLMClient, config: CompactionConfig | None = None) -> None:
        self.llm = llm
        self.config = config or CompactionConfig()
        # 复用临时 LLMClient，避免每次 _call_llm 都新建 OpenAI SDK 实例
        self._temp_client = LLMClient(
            api_key=llm.client.api_key,
            base_url=str(llm.client.base_url),
            model=llm.model,
        )

    def compact(
        self,
        messages: list[dict[str, Any]],
        stop_reason: str | None = None,
    ) -> CompactionResult:
        """执行完整压缩流程（可迭代）。

        Args:
            messages: 当前对话消息列表（不含 system prompt）。
            stop_reason: 当前回复的 stop reason，end_turn/aborted 才触发。

        Returns:
            CompactionResult 包含摘要和归档信息。
        """
        if not messages:
            return CompactionResult(summary="", messages_kept=[])

        remaining = list(messages)

        for iteration in range(self.config.max_iterations):
            # Phase 1: Classify（确定当前问题）
            classification = self._classify(remaining)
            if classification is None:
                return CompactionResult(
                    summary=self._emergency_summary(remaining),
                    messages_kept=remaining,
                    error="分类阶段失败",
                )

            topic = classification.get("topic", "未分类")
            project_name = classification.get("project_name", "")
            skill_name = classification.get("skill_name", "")

            # 空对话跳过归档，直接摘要
            if not topic or topic == "空对话":
                summary_text = self._summarize(remaining, topic)
                return CompactionResult(
                    summary=summary_text,
                    messages_kept=[],
                    archived_count=0,
                )

            # Phase 2: Archive（归档+分类消息）
            archive_result = self._archive(
                messages=remaining,
                topic=topic,
                project_name=project_name,
                skill_name=skill_name,
            )

            if archive_result is None:
                return CompactionResult(
                    summary=self._emergency_summary(remaining),
                    messages_kept=remaining,
                    error="归档阶段失败",
                )

            remaining = archive_result.messages_kept
            archive_ops = archive_result.archive_operations

            # Phase 3: Summarize（对剩余内容生成摘要）
            summary_text = self._summarize(remaining, topic)

            # Phase 4: 迭代检查
            estimated_tokens = len(summary_text.encode("utf-8")) // 3 if summary_text else 0
            if self.config.is_below_end_threshold(estimated_tokens):
                return CompactionResult(
                    summary=summary_text,
                    messages_kept=remaining,
                    archived_count=archive_ops.get("archived_count", 0),
                    archive_details=archive_ops.get("write_result", ""),
                )

            # 未达标，把摘要作为新的"剩余消息"继续下一轮
            # 注意：不能用 system role，因为 _format_messages 会跳过 system 消息
            remaining = [{"role": "user", "content": f"[Context Compaction Summary]\n{summary_text}"}]

        # 达到 max_iterations 仍未达标
        return CompactionResult(
            summary=self._emergency_summary(messages),
            messages_kept=messages,
            error="达到最大迭代次数仍未达标",
        )

    # ── Phase 1: Classify ────────────────────────────────────────────

    def _classify(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """LLM 分类：确定当前问题和相关项目/技能。"""
        conversation = self._format_messages(messages)
        if not conversation.strip():
            return {"topic": "空对话", "project_name": "", "skill_name": ""}

        try:
            raw = self._call_llm(
                system=_CLASSIFY_PROMPT,
                user=f"以下是对话历史：\n\n{conversation}",
            )
            return self._parse_json(raw) if raw else None
        except Exception:
            return None

    # ── Phase 2: Archive ─────────────────────────────────────────────

    def _archive(
        self,
        messages: list[dict[str, Any]],
        topic: str,
        project_name: str,
        skill_name: str,
    ) -> _ArchiveResult | None:
        """归档阶段：分类消息 + 写入归档文件。"""
        # 读取已有 skill/project 内容
        existing_content = ""
        archive_path: Path | None = None
        archive_label = ""

        if project_name:
            archive_path = _get_project_path(project_name)
            existing_content = _read_file_safe(archive_path)
            archive_label = f"项目: {project_name}"
        elif skill_name:
            archive_path = _get_skill_path(skill_name)
            existing_content = _read_file_safe(archive_path)
            archive_label = f"技能: {skill_name}"

        # 格式化消息供 LLM 分类
        messages_text = self._format_messages_with_index(messages)

        try:
            raw = self._call_llm(
                system=_ARCHIVE_PROMPT.format(
                    topic=topic,
                    project_name=project_name or "(无)",
                    skill_name=skill_name or "(无)",
                    existing_content=existing_content or "(空)",
                    messages_text=messages_text,
                ),
                user="请对以上消息进行归档分类。",
            )
        except Exception:
            return None

        if raw is None:
            return None

        plan = self._parse_json(raw)
        if plan is None:
            return None

        classifications = plan.get("classifications", [])
        integrated_content = plan.get("integrated_content", "")

        # 按分类结果处理消息
        messages_kept: list[dict[str, Any]] = []
        archived_count = 0
        details_parts: list[str] = []

        for i, msg in enumerate(messages):
            action = self._get_action_for_index(classifications, i)
            if action == "keep":
                messages_kept.append(msg)
            elif action == "archive":
                archived_count += 1

        for cls in classifications:
            action = cls.get("action", "")
            reason = cls.get("reason", "")
            details_parts.append(f"  [{action}] {reason}")

        # 写入归档文件（整合后的内容，不是 append）
        write_detail = ""
        if self.config.enable_archive and integrated_content:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            if archive_path:
                header = f"# {archive_label}\n\n最后更新: {timestamp}\n\n"
                write_detail = _write_file_safe(archive_path, header + integrated_content)
            else:
                # 没有匹配到 project/skill，写入通用归档
                archive_dir = Path.home() / ".lampson" / "archives"
                archive_dir.mkdir(parents=True, exist_ok=True)
                generic_path = archive_dir / f"archive-{_sanitize(topic[:30])}.md"
                header = f"# 归档: {topic}\n\n最后更新: {timestamp}\n\n"
                write_detail = _write_file_safe(generic_path, header + integrated_content)

        return _ArchiveResult(
            messages_kept=messages_kept,
            archive_operations={
                "archived_count": archived_count,
                "details": "\n".join(details_parts),
                "write_result": write_detail,
            },
        )

    # ── Phase 3: Summarize ──────────────────────────────────────────

    def _summarize(self, messages: list[dict[str, Any]], topic: str) -> str:
        """对剩余消息生成结构化摘要。"""
        messages_text = self._format_messages(messages)

        if not messages_text.strip():
            return f"## 问题\n{topic}"

        try:
            raw = self._call_llm(
                system=_SUMMARIZE_PROMPT.format(messages_text=messages_text),
                user="请生成结构化摘要。",
            )
        except Exception:
            return self._emergency_summary(messages)

        if raw is None:
            return self._emergency_summary(messages)

        summary_data = self._parse_json(raw)
        if summary_data is None:
            return self._emergency_summary(messages)

        return self._build_summary_text(summary_data, topic)

    # ── 工具方法 ────────────────────────────────────────────────────

    def _call_llm(self, system: str, user: str) -> str | None:
        """调用 LLM（复用临时客户端）。"""
        self._temp_client.messages = []
        self._temp_client.set_system_context(core_memory=system)
        self._temp_client.add_user_message(user)
        response = self._temp_client.chat()
        return (response.choices[0].message.content or "").strip() or None

    def _parse_json(self, text: str | None) -> dict[str, Any] | None:
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

    def _extract_content(self, content: str | list[dict[str, Any]]) -> str:
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

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """将消息列表格式化为可读文本。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                continue
            content = self._extract_content(msg.get("content", ""))
            if not content:
                continue
            prefix = {"user": "用户", "assistant": "Lampson", "tool": "工具结果"}.get(role, role)
            if len(content) > 2000:
                content = content[:2000] + "\n...[截断]"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    def _format_messages_with_index(self, messages: list[dict[str, Any]]) -> str:
        """带索引号的消息格式化，供归档分类使用。"""
        lines = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = self._extract_content(msg.get("content", ""))
            if not content:
                continue
            prefix = {"user": "用户", "assistant": "Lampson", "tool": "工具结果"}.get(role, role)
            if len(content) > 2000:
                content = content[:2000] + "\n...[截断]"
            lines.append(f"[{i}] {prefix}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _get_action_for_index(classifications: list[dict], index: int) -> str:
        """获取指定索引消息的分类动作。"""
        for cls in classifications:
            if cls.get("index") == index:
                return cls.get("action", "keep")
        return "keep"

    def _build_summary_text(self, data: dict[str, Any], topic: str) -> str:
        """从 JSON 数据构建结构化摘要文本。"""
        parts = []

        if data.get("problem"):
            parts.append(f"## 问题\n{data['problem']}")
        elif topic and topic != "空对话":
            parts.append(f"## 问题\n{topic}")

        if data.get("constraints"):
            items = "\n".join(f"- {c}" for c in data["constraints"])
            parts.append(f"## 约束\n{items}")

        if data.get("completed"):
            items = "\n".join(f"- [x] {c}" for c in data["completed"])
            parts.append(f"## 已完成\n{items}")

        if data.get("in_progress"):
            items = "\n".join(f"- [ ] {c}" for c in data["in_progress"])
            parts.append(f"## 进行中\n{items}")

        if data.get("blocked"):
            items = "\n".join(f"- {c}" for c in data["blocked"])
            parts.append(f"## 阻塞\n{items}")

        if data.get("decisions"):
            items = "\n".join(f"- {c}" for c in data["decisions"])
            parts.append(f"## 关键决策\n{items}")

        if data.get("pending"):
            items = "\n".join(f"- {c}" for c in data["pending"])
            parts.append(f"## 待处理\n{items}")

        if data.get("key_files"):
            items = "\n".join(f"- {c}" for c in data["key_files"])
            parts.append(f"## 关键文件\n{items}")

        return "\n\n".join(parts) if parts else (f"## 问题\n{topic}" if topic else "")

    def _emergency_summary(self, messages: list[dict[str, Any]]) -> str:
        """紧急摘要：LLM 调用失败时的兜底方案。"""
        text = self._format_messages(messages)
        if len(text) > 2000:
            text = text[:2000] + "\n...[截断]"
        return f"## 对话历史（压缩失败，保留前2000字）\n\n{text}"


# ── 内部数据类 ─────────────────────────────────────────────────────────

@dataclass
class _ArchiveResult:
    """归档阶段结果。"""
    messages_kept: list[dict[str, Any]]
    archive_operations: dict[str, Any]


# ── 对外数据类 ────────────────────────────────────────────────────────

@dataclass
class CompactionResult:
    """压缩结果。"""

    # 结构化摘要（用于注入对话窗口）
    summary: str

    # 保留的消息（摘要之外的）
    messages_kept: list[dict[str, Any]] | None = None

    # 归档了多少条内容
    archived_count: int = 0

    # 归档操作详情
    archive_details: str = ""

    # 错误信息（None = 成功）
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


# ── Agent 集成 ────────────────────────────────────────────────────────

def apply_compaction(
    agent_llm: LLMClient,
    config: CompactionConfig,
    last_total_tokens: int,
    stop_reason: str | None = None,
) -> CompactionResult | None:
    """检查并执行压缩。

    在 Agent.run() 返回后调用。
    如果超过阈值且 stopReason 允许，执行压缩并重置对话历史。

    Args:
        agent_llm: Agent 的 LLMClient 实例。
        config: 压缩配置。
        last_total_tokens: 最近一次 LLM 调用的 total_tokens。
        stop_reason: stop reason，end_turn/aborted 才触发压缩。

    Returns:
        CompactionResult（触发了压缩）或 None（不需要压缩）。
    """
    if not config.should_trigger(last_total_tokens):
        return None

    # stopReason 白名单检查
    if stop_reason not in ("end_turn", "aborted", "stop"):
        return None

    # 提取非 system 消息
    messages = [m for m in agent_llm.messages if m.get("role") != "system"]
    if not messages:
        return None

    compactor = Compactor(llm=agent_llm, config=config)
    result = compactor.compact(messages, stop_reason=stop_reason)

    if result.success and result.summary:
        # 保留 system prompt，用摘要替换对话历史
        system_msg = agent_llm.messages[0] if agent_llm.messages else {}
        compaction_msg = {
            "role": "system",
            "content": (
                "# Context Compaction（上下文压缩）\n\n"
                "以下是对之前对话的压缩摘要，请在此基础上继续对话。\n\n"
                f"{result.summary}"
            ),
        }
        agent_llm.messages = [system_msg, compaction_msg]

    return result
