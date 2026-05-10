"""统一 session 工具：合并 session_search + session_load，通过 action 参数区分。"""

from __future__ import annotations

from src.memory import session_store
from src.memory.session_search import search_sessions, SearchResult

# ── 当前 Session 引用（由 Session.startup 注入） ────────────────────────────

_current_session: object | None = None


def set_current_session(session: object) -> None:
    """设置当前 Session 引用（供 load action 注入消息时使用）。"""
    global _current_session
    _current_session = session


# ── 统一 Schema ──────────────────────────────────────────────────────────────

SESSION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session",
        "description": (
            "操作历史会话。action='search' 搜索历史对话记录，"
            "action='load' 加载指定或最近的 session 对话历史到当前对话。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "load"],
                    "description": "操作类型：search 搜索历史对话，load 加载历史会话到当前对话",
                },
                "query": {
                    "type": "string",
                    "description": "search 模式的搜索关键词，支持模糊匹配",
                },
                "session_id": {
                    "type": "string",
                    "description": "load 模式要加载的 session ID。不填则加载最近一个已结束的 session。",
                },
                "limit": {
                    "type": "integer",
                    "description": "search 模式返回条数（默认 30）；load 模式加载消息条数（默认 50）",
                    "default": 30,
                },
                "date_from": {
                    "type": "string",
                    "description": "search 模式搜索起始日期，格式 YYYY-MM-DD。不指定则默认近30天",
                },
                "date_to": {
                    "type": "string",
                    "description": "search 模式搜索结束日期，格式 YYYY-MM-DD",
                },
                "role": {
                    "type": "string",
                    "description": "search 模式限定消息角色：user 或 assistant",
                },
            },
            "required": ["action"],
        },
    },
}


# ── 内部实现 ─────────────────────────────────────────────────────────────────

_DEFAULT_SEARCH_LIMIT = 30
_DEFAULT_DAYS_BACK = 30


def _format_ts(ts: int) -> str:
    """将毫秒时间戳格式化为可读字符串。"""
    from datetime import datetime
    try:
        dt = datetime.fromtimestamp(ts / 1000)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _run_search(params: dict) -> str:
    """执行搜索。"""
    query = params.get("query", "")
    if not query:
        return "[错误] search 模式需要 query 参数"

    limit = params.get("limit", _DEFAULT_SEARCH_LIMIT)
    date_from = params.get("date_from")
    date_to = params.get("date_to")

    # 如果用户没指定 date_from，默认搜索近30天
    if not date_from:
        from datetime import date, timedelta
        date_from = (date.today() - timedelta(days=_DEFAULT_DAYS_BACK)).strftime("%Y-%m-%d")

    role = params.get("role")

    try:
        results = search_sessions(
            query=query,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            role=role,
        )
    except Exception as e:
        return f"[错误] 搜索失败: {e}"

    if not results:
        return "没有找到相关历史对话。"

    lines = [f"找到 {len(results)} 条相关记录：\n"]
    for i, r in enumerate(results, 1):
        role_label = "用户" if r.role == "user" else "Lamix"
        from_ts = _format_ts(r.ts)
        lines.append(f"--- 结果 {i} ---\n[{from_ts}] {role_label}（session: {r.session_id}）\n{r.snippet}\n")

    return "\n".join(lines)


def _run_load(params: dict) -> str:
    """执行加载。"""
    session_id = params.get("session_id", "")
    limit = int(params.get("limit", 50))

    # 委托给 Session.load_session()
    if _current_session is not None and hasattr(_current_session, "load_session"):
        return _current_session.load_session(session_id=session_id, limit=limit)  # type: ignore[union-attr]

    # Fallback：没有 session 引用时，只查询不注入
    if not session_id:
        sessions = session_store.list_recent_sessions(limit=1, ended_only=True)
        if not sessions:
            return "没有找到历史 session。"
        session_id = sessions[0]["session_id"]

    messages = session_store.get_session_messages(session_id, limit=limit)
    if not messages:
        return f"Session {session_id} 没有消息记录。"

    return f"找到 session {session_id}，共 {len(messages)} 条消息。但无法注入到当前对话（缺少 Session 引用）。"


# ── 统一入口 ─────────────────────────────────────────────────────────────────

def run(params: dict) -> str:
    """统一 session 工具入口。"""
    action = (params.get("action") or "").strip()
    if action == "search":
        return _run_search(params)
    elif action == "load":
        return _run_load(params)
    else:
        return "[错误] action 参数必须为 'search' 或 'load'"
