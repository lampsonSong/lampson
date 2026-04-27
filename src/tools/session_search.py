"""session_search 工具：让 LLM 主动搜索历史对话。"""

from __future__ import annotations

from src.memory.session_search import search_sessions, SearchResult

# ── Schema ───────────────────────────────────────────────────────────────────

SESSION_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session_search",
        "description": "搜索 Lampson 的历史对话记录（跨 session 全文搜索）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，可以是中文或英文。支持模糊匹配。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回多少条结果，默认 5。",
                    "default": 5,
                },
                "date_from": {
                    "type": "string",
                    "description": "搜索起始日期，格式 YYYY-MM-DD，如 '2026-04-01'。",
                },
                "date_to": {
                    "type": "string",
                    "description": "搜索结束日期，格式 YYYY-MM-DD，如 '2026-04-30'。",
                },
                "role": {
                    "type": "string",
                    "description": "限定消息角色：user 或 assistant。",
                },
            },
            "required": ["query"],
        },
    },
}


def run(params: dict) -> str:
    """执行 session_search。"""
    query = params.get("query", "")
    if not query:
        return "[错误] query 参数不能为空"

    limit = params.get("limit", 5)
    date_from = params.get("date_from")
    date_to = params.get("date_to")
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
        role_label = "用户" if r.role == "user" else "Lampson"
        from_ts = _format_ts(r.ts)
        lines.append(f"--- 结果 {i} ---\n[{from_ts}] {role_label}（session: {r.session_id}）\n{r.snippet}\n")

    return "\n".join(lines)


def _format_ts(ts: int) -> str:
    """将毫秒时间戳格式化为可读字符串。"""
    from datetime import datetime

    try:
        dt = datetime.fromtimestamp(ts / 1000)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)
