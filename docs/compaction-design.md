# Lampson - Compaction 设计文档

## 目标

对话持续进行，context window 有限。每次对话结束后，对历史消息做**归档**（Archive），而不是丢弃。

核心原则：
- **归档而不是丢弃** — 有价值的内容沉淀到 skill / project 文件
- **保留原始消息** — 只对必须压缩的部分做摘要，不反复摘要造成信息损耗
- **可演进** — 初期只做 append，稳定后再支持 merge/update/evict

---

## 何时触发

### 触发条件

| 条件 | 阈值 | 说明 |
|------|------|------|
| 消息数量 | 累计 150 条消息 | 保护 context 不溢出 |
| Token 估算 | 触发时 ~60k tokens | 预留空间 |
| stopReason 白名单 | `end_turn` 或 `aborted` | 防止工具调用中途被打断时触发 |

```python
STOP_REASONS = {"end_turn", "aborted"}
TRIGGER_MSG_COUNT = 150
TRIGGER_TOKEN_ESTIMATE = 60_000
END_THRESHOLD_PERCENT = 80   # 归档完成后，context 应降到 80% 以下
```

### 触发流程

```
触发条件满足
    │
    ▼
等待 stopReason in STOP_REASONS
    │
    ▼
执行 Archive Phase（见下文）
    │
    ▼
校验: 剩余消息 token < END_THRESHOLD_PERCENT * context_limit
    │
    ├── 通过 → 完成，写入 .compaction_log.jsonl
    └── 未通过 → 报警（LLM 归档不充分，需人工 review）
```

---

## Archive Phase — 三步流水线

> **设计原则**：一个 LLM 调用只做一件事。拆成流水线后，每步认知负载低，错误可定位。

### Step 1：分类（Classify）

LLM 输出 JSON，不做任何写入：

```json
{
  "decisions": [
    {
      "msg_id": "msg_001",
      "action": "keep",
      "reason": "用户明确的需求，后续还要继续"
    },
    {
      "msg_id": "msg_002",
      "action": "archive",
      "target": "skill:python-patterns",
      "reason": "讨论了 Python 上下文管理器的最佳实践"
    },
    {
      "msg_id": "msg_003",
      "action": "discard",
      "reason": "纯粹闲聊，无持久价值"
    }
  ],
  "tool_refs": {
    "msg_004": {          // msg_004 是 tool 返回结果
      "referenced_by": ["msg_005"],  // msg_005 的回复里引用了这个结果
      "action": "keep",   // 因为被引用过，视为有价值
      "reason": "LLM 回复中引用了 browser_tool 返回的页面内容"
    }
  }
}
```

**LLM prompt 模板**：

```
你是归档分类助手。根据以下对话历史，输出 JSON 格式的分类决策。

## 分类标准

- `keep`: 有上下文价值，且无法简单总结（如用户正在讨论的问题）
- `archive`: 可以提炼沉淀到文件的内容（如：技术方案、决策、用户偏好、踩坑记录）
- `discard`: 纯粹闲聊、礼貌性回复、无效内容

## 工具调用结果处理

assistant 消息的 `referenced_tool_results` 字段记录了该回复引用了哪些 tool_call id（如 `["call_001"]`）。该字段由 Agent 在写入 JSONL 时生成，已持久化。

分类时：
1. 如果 tool_call id 在某条 assistant 消息的 `referenced_tool_results` 中出现 → action = "keep"
2. 如果没被任何 assistant 引用 → action = "discard"
3. 如果有价值值得归档 → action = "archive"，target 为相关 skill 或 project

## 当前已有文件

{existing_files_summary}

## 输出格式

只输出 JSON，不要其他内容。
```

### Step 2：读已有内容（Read Existing）

```python
def read_target_file(target: str) -> str:
    """读取 skill 或 project 文件的现有内容。"""
    if target.startswith("skill:"):
        path = SKILLS_DIR / f"{target[6:]}.md"
    elif target.startswith("project:"):
        path = PROJECTS_DIR / f"{target[8:]}.md"
    else:
        return ""
    return path.read_text() if path.exists() else ""
```

### Step 3：整合写入（Integrate & Write）

```python
def integrate(archive_items: list[dict], existing_content: str, target: str, msg_map: dict) -> str:
    """
    整合新旧内容。

    整合策略（见"归档策略"）：
    - 只做 append，不做 merge/update/evict
    - LLM 在归档文本里自己处理去重和结构
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_entries = "\n".join(
        f"- {msg_map[e['msg_id']]['content']} _(归档: {timestamp})_"
        for e in archive_items
        if e["msg_id"] in msg_map
    )
    return f"{existing_content}\n{new_entries}\n"
```

**写前备份（安全机制）**：

```python
def safe_write(path: Path, content: str) -> None:
    # 写前备份
    if path.exists():
        backup_path = path.with_suffix(".md.bak")
        shutil.copy2(path, backup_path)

    # 写入
    path.write_text(content, encoding="utf-8")

    # 校验（可跳过）
    # 简单校验：前 10 行是否包含有效 markdown
```

---

## 归档策略

当前采用 append-only 策略，merge/update/evict 作为未来演进方向。

| 策略 | 条件 | 行为 |
|------|------|------|
| **append** | 默认 | 直接追加，LLM 在归档文本里自己处理去重和结构 |
| **merge** | 同一 target 多次 archive | 合并多条归档为一个连贯段落 |
| **update** | 已有内容与新内容矛盾 | 替换旧内容，保留变更历史 |
| **evict** | 已有内容过期或被取代 | 删除旧条目 |

---

## 压缩后的消息列表

归档完成后，剩余消息 = `keep` 列表 + 系统消息 + 最新 2-3 条消息（保障上下文连贯）：

```python
def build_remaining_messages(messages: list, decisions: dict) -> list:
    keep_ids = {d["msg_id"] for d in decisions["decisions"] if d["action"] == "keep"}
    tool_keep_ids = {k for k, v in decisions["tool_refs"].items() if v["action"] == "keep"}

    keep_ids |= tool_keep_ids

    # 保留原始消息，不做摘要
    remaining = [msg for msg in messages if msg["id"] in keep_ids]

    # 追加最近 2-3 条（保障上下文连贯）
    recent = messages[-3:]
    for msg in recent:
        if msg["id"] not in keep_ids:
            remaining.append(msg)

    # 加入 system 消息
    system_msgs = [msg for msg in messages if msg["role"] == "system"]
    return system_msgs + remaining
```

---

## 召回路径（与 memory-design.md 联动）

归档写进 skill / project 后，需要有召回机制：

1. **skill injection**：每次对话开始时，relevant skills 自动注入 context（由 skills-injection-design.md 定义）
2. **session_search**：用户或 LLM 主动搜索历史 JSONL（FTS5），搜到的 snippet 注入 context
3. **project_context()**：LLM 在规划任务时，调用 project_context() 加载相关 project 内容

### Resume 时的上下文重建

程序崩溃后 resume 时，需要重建 compaction 后的上下文：

```
resume session
    │
    ▼
读取 JSONL，从最后一个 segment_boundary 行获取 archive 字段
    │
    ▼
archive = [{"target": "skill:python-patterns", "entry_count": 3}, ...]
    │
    ▼
按 target 加载对应 skill/project 文件内容
    │
    ▼
注入到 Agent 上下文
```

> **为什么 archive 字段很重要**：segment_boundary 和 skill/project 是分开存储的，resume 时不知道该注入哪些 skill/project。archive 字段让这个过程不需要读 skill/project 文件内容，只需要知道有哪些 target 即可。

---

## 文件结构

```
lampson/
├── skills/              # Skill 文件（归档沉淀地）
│   ├── python-patterns.md
│   └── bash-tips.md
├── projects/            # Project 文件（按项目组织）
│   ├── my-website.md
│   └── cli-tool.md
├── sessions/            # 原始对话 JSONL（按日期/session 组织）
│   ├── 2025-04-27/
│   │   ├── session_abc.jsonl
│   │   └── session_def.jsonl
│   └── 2025-04-28/
│       └── session_ghi.jsonl
├── compaction.py       # 压缩模块
└── .compaction_log.jsonl  # 压缩操作日志（可审计）
```

---

## compaction.py 实现草稿

```python
"""
Compaction 模块 — 对话归档策略

每次对话结束后，对历史消息做归档，而不是丢弃。
核心原则：归档而不是丢弃，保留原始消息，不反复摘要。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from .llm import llm

SKILLS_DIR = Path("~/.lampson/skills").expanduser()
PROJECTS_DIR = Path("~/.lampson/projects").expanduser()
COMPACTION_LOG = Path("~/.lampson/.compaction_log.jsonl")

STOP_REASONS = {"end_turn", "aborted"}
TRIGGER_MSG_COUNT = 150
TRIGGER_TOKEN_ESTIMATE = 60_000  # ~150 条消息 × 400 tokens/条
END_THRESHOLD_PERCENT = 80.0


class Decision(TypedDict):
    msg_id: str
    action: str          # keep | archive | discard
    target: str | None   # e.g. "skill:python-patterns" or None
    reason: str


class ToolRef(TypedDict):
    referenced_by: list[str]
    action: str
    reason: str


class ClassifyResult(TypedDict):
    decisions: list[Decision]
    tool_refs: dict[str, ToolRef]


def should_trigger(messages: list[dict], stop_reason: str) -> bool:
    """判断是否应该触发归档。"""
    if stop_reason not in STOP_REASONS:
        return False
    if len(messages) < TRIGGER_MSG_COUNT:
        return False
    # Token 估算：按平均每条消息 400 tokens 估算（user+assistant 往返），
    # 触发阈值 60k ~150 条消息量级。若实际 token 超限但条数不足，
    # 下次 compaction 会继续触发，不漏检。
    estimated_tokens = len(messages) * 400
    if estimated_tokens < TRIGGER_TOKEN_ESTIMATE:
        return False
    return True


def classify_messages(messages: list[dict], existing_files: dict[str, str]) -> ClassifyResult:
    """Step 1：LLM 分类，不做写入。"""
    prompt = _build_classify_prompt(messages, existing_files)
    response = llm.complete(prompt)
    return json.loads(response)


def _build_classify_prompt(messages: list[dict], existing_files: dict[str, str]) -> str:
    """构建 LLM 分类 prompt，格式见上方 Step 1 prompt template。"""
    header = "你是归档分类助手。根据以下对话历史，输出 JSON 格式的分类决策。"
    existing_summary = "\n".join(
        f"- {k}: {v[:200]}"
        for k, v in existing_files.items()
    ) if existing_files else "(无已有文件)"

    lines = [header, "", "## 当前已有文件摘要\n" + existing_summary, "", "## 对话历史\n"]
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        refs = msg.get("referenced_tool_results", [])
        ref_note = f" (引用了 tool: {', '.join(refs)})" if refs else ""
        lines.append(f"[{msg['id']}] {role}: {content[:300]}{ref_note}")

    return "\n".join(lines)


def read_target_file(target: str) -> str:
    """Step 2：读取已有文件内容。"""
    if target.startswith("skill:"):
        path = SKILLS_DIR / f"{target[6:]}.md"
    elif target.startswith("project:"):
        path = PROJECTS_DIR / f"{target[8:]}.md"
    else:
        return ""
    return path.read_text() if path.exists() else ""


def safe_write(path: Path, content: str) -> None:
    """Step 3：写前备份，安全写入。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".md.bak"))
    path.write_text(content, encoding="utf-8")


def run_compaction(messages: list[dict]) -> list[dict]:
    """
    主入口：执行归档流水线。

    **调用方**：Agent 运行时由 `maybe_compact()` 调用，不在 Session 退出时调用。

    触发条件：消息数 ≥ 150 条 或 Token 估算 ≥ 60k，且 stopReason 为 end_turn/aborted。

    步骤顺序（原子性保障）：
    1. LLM 分类（不涉及写入）
    2. 写 segment_boundary 到 session JSONL（含 archive 字段，供 resume 重建上下文）
    3. 写 skill/project 归档文件
    4. 写 compaction 日志
    5. 构建剩余消息返回

    Returns: 归档后剩余的消息列表（不含 system 消息）
    """
    # Step 1: 分类（不涉及写入）
    existing_files = _list_existing_files()
    result = classify_messages(messages, existing_files)

    # 提取 archive 目标列表（供后续写入 JSONL 和文件用）
    archive_targets = [
        {"target": d["target"], "entry_count": 1}
        for d in result["decisions"]
        if d["action"] == "archive" and d.get("target")
    ]

    # Step 2: 写 segment_boundary 到 session JSONL（原子性保障的核心）
    _write_segment_boundary(messages, archive_targets)

    # Step 3: 读取已有内容 + 整合写入 skill/project
    _write_archive_entries(result["decisions"], messages)

    # Step 4: 写压缩日志
    _log_compaction(messages, result, archive_targets)

    # Step 5: 构建剩余消息列表
    remaining = _build_remaining_messages(messages, result)

    return remaining


def _write_segment_boundary(messages: list[dict], archive_targets: list[dict]) -> None:
    """
    写入 segment_boundary 到 session JSONL 文件。

    这是原子性保障的核心：segment_boundary 和 archive 字段一起写入 JSONL，
    程序崩溃后 resume 时可从 JSONL 重建完整的上下文注入。

    archive_targets 格式：
    [{"target": "skill:python-patterns", "entry_count": 3}, ...]
    """
    from .session_store import session_store

    # 提取当前 session 信息
    session_id = messages[0].get("session_id") if messages else None
    if not session_id:
        return  # 不应该发生

    # 当前 segment 号 = 最后一条消息的 segment
    current_segment = messages[-1].get("segment", 0)

    # 下一 segment 开始时间 = 下一条消息的 ts（如果还没发就是当前时间戳的估计值）
    next_segment_started_at = int(datetime.now().timestamp() * 1000)

    boundary_row = {
        "ts": int(datetime.now().timestamp() * 1000),
        "session_id": session_id,
        "segment": current_segment,
        "type": "segment_boundary",
        "next_segment_started_at": next_segment_started_at,
        "archive": archive_targets,
    }

    session_store.append(boundary_row)


def _list_existing_files() -> dict[str, str]:
    """列出所有现有 skill 和 project 文件的前 200 字摘要，供 LLM 分类时参考。"""
    result: dict[str, str] = {}
    for path in SKILLS_DIR.glob("*.md"):
        content = path.read_text(encoding="utf-8")[:200]
        result[f"skill:{path.stem}"] = content
    for path in PROJECTS_DIR.glob("*.md"):
        content = path.read_text(encoding="utf-8")[:200]
        result[f"project:{path.stem}"] = content
    return result


def _target_to_path(target: str) -> Path:
    """将 target 字符串转为文件 Path。"""
    if target.startswith("skill:"):
        return SKILLS_DIR / f"{target[6:]}.md"
    elif target.startswith("project:"):
        return PROJECTS_DIR / f"{target[8:]}.md"
    return Path()  # 不应该走到这里


def _write_archive_entries(decisions: list[Decision], messages: list[dict]) -> None:
    """将 archive 决策写入对应文件。"""
    msg_map = {m["id"]: m for m in messages}

    # 按 target 分组
    by_target: dict[str, list] = {}
    for d in decisions:
        if d["action"] == "archive" and d.get("target"):
            by_target.setdefault(d["target"], []).append(d)

    for target, entries in by_target.items():
        existing = read_target_file(target)
        new_content = integrate(entries, existing, target, msg_map)
        path = _target_to_path(target)
        safe_write(path, new_content)


def integrate(entries: list[dict], existing: str, target: str, msg_map: dict) -> str:
    """只追加，不做 merge/update/evict。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_entries = "\n".join(
        f"- {msg_map[e['msg_id']]['content']} _(归档: {timestamp})_"
        for e in entries
        if e["msg_id"] in msg_map
    )
    return f"{existing}\n{new_entries}\n"


def _build_remaining_messages(messages: list[dict], result: ClassifyResult) -> list[dict]:
    """保留 keep 列表 + 最近 3 条，原始消息不摘要。"""
    keep_ids = {
        d["msg_id"] for d in result["decisions"]
        if d["action"] == "keep"
    }
    keep_ids |= {k for k, v in result["tool_refs"].items() if v["action"] == "keep"}

    remaining = [msg for msg in messages if msg["id"] in keep_ids]

    # 追加最近 3 条保障连贯
    recent = [msg for msg in messages[-3:] if msg["id"] not in keep_ids]
    remaining.extend(recent)

    return remaining


def _log_compaction(original: list, result: ClassifyResult, archive_targets: list[dict]) -> None:
    """写压缩操作日志。日志文件超过 10MB 时自动轮转（保留最近 5 个文件）。"""
    COMPACTION_LOG.parent.mkdir(parents=True, exist_ok=True)

    # 轮转检查
    if COMPACTION_LOG.exists() and COMPACTION_LOG.stat().st_size > 10 * 1024 * 1024:
        _rotate_compaction_log()

    with open(COMPACTION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now().isoformat(),
            "original_count": len(original),
            "decisions": result,
            "archive_targets": archive_targets,
        }, ensure_ascii=False) + "\n")


def _rotate_compaction_log() -> None:
    """轮转压缩日志：.compaction_log.jsonl → .compaction_log.jsonl.1 → .2 → ... → .4。"""
    import shutil
    for i in range(4, 0, -1):
        src = COMPACTION_LOG.with_suffix(f".jsonl.{i}")
        dst = COMPACTION_LOG.with_suffix(f".jsonl.{i + 1}")
        if src.exists():
            shutil.move(str(src), str(dst))
    shutil.move(str(COMPACTION_LOG), str(COMPACTION_LOG.with_suffix(".jsonl.1")))
```

---

## Session 生命周期时序

compaction、core.md 更新、session_end 写入发生在不同阶段：

| 阶段 | 触发时机 | 操作 |
|------|----------|------|
| **运行时** | 消息数 ≥ 150 或 Token ≥ 60k，stopReason 为 end_turn/aborted | `run_compaction()` → 写 segment_boundary + skill/project |
| **退出时** | 用户结束对话 / 断连 | core.md 更新检查（累计 archive 次数 > 5 或距上次更新 > N 小时） |
| **退出时** | 同上 | 写入 `session_end` 行到 JSONL |

三者互不干扰：compaction 是运行时多次触发，core.md 更新和 session_end 是退出时一次性执行。

---

## core.md 更新路径

`core.md`（核心记忆）在 session 退出时更新，不在 compaction 时更新：

```
Session 退出
    │
    ▼
检查 core.md 当前内容
    │
    ├── 累计 archive 次数 > 阈值（如 5 次）
    │      或
    │      距上次 core.md 更新超过 N 小时
    │         → LLM 抽取 skill/project 归档中的精华更新 core.md
    └── 否则不操作
```

**为什么不每次 compaction 都更新 core.md**：
- compaction 频繁（每 150 条消息），core.md 膨胀会每次拖慢启动
- core.md 内容本身需要人工可读可维护，频繁改写不利

**召回路径优先级**：
1. `core.md` → 启动时全量进 system prompt（用户偏好、长期事实）
2. `skill / project` → 规划时 relevant skills/projects 全文注入（compaction 归档沉淀）
3. `session JSONL` → session_search 按需召回（可被搜索的历史消息）

---

## 验收标准

- [ ] 触发条件正确：150 条消息 + stopReason 白名单 + token 估算
- [ ] Step 1 输出有效 JSON，无写入操作
- [ ] Step 2 读取已有文件（支持 skill / project 两种 target）
- [ ] Step 3 写前备份，写后校验
- [ ] 压缩后 context token < 80% threshold
- [ ] `.compaction_log.jsonl` 记录每次操作，超过 10MB 自动轮转
- [ ] 只做 append，不做 merge/update/evict
- [ ] tool_calls 结果依据 `referenced_tool_results` 字段判断是否 keep
- [ ] 召回路径由 memory-design.md 覆盖
- [ ] session 退出时触发 core.md 更新检查

---

## 与 Hermes 的对比

| 维度 | Hermes | Lampson |
|------|--------|---------|
| 触发时机 | 会话开始时扫描 | 会话结束时触发 |
| 存储 | SQLite FTS5 | JSONL + 文件系统 |
| 召回 | session_search 主动召回 | skill injection + session_search |
| 归档 | 无归档层 | skill / project 双层归档 |
| 安全 | Frozen snapshot | 写前备份 |
