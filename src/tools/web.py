"""网络搜索工具：通过 DuckDuckGo HTML 接口搜索，无需 API Key。"""

from __future__ import annotations

from typing import Any

import httpx
from bs4 import BeautifulSoup


DDG_URL = "https://html.duckduckgo.com/html/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
MAX_RESULTS = 5
TIMEOUT = 15


def web_search(query: str, max_results: int = MAX_RESULTS) -> str:
    """搜索网页，返回格式化的结果摘要。"""
    if not query.strip():
        return "[错误] 搜索词不能为空"

    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.post(
                DDG_URL,
                data={"q": query, "kl": "cn-zh"},
                headers=HEADERS,
            )
            response.raise_for_status()
    except httpx.TimeoutException:
        return "[超时] 搜索请求超时，请稍后重试。"
    except httpx.HTTPError as e:
        return f"[错误] 搜索请求失败：{e}"

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for result in soup.select(".result")[:max_results]:
        title_tag = result.select_one(".result__title a")
        snippet_tag = result.select_one(".result__snippet")

        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        url = title_tag.get("href", "")
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

        results.append(f"**{title}**\n{url}\n{snippet}")

    if not results:
        return f"[无结果] 未找到关于「{query}」的搜索结果。"

    header = f"搜索「{query}」的结果（共 {len(results)} 条）：\n"
    return header + "\n\n".join(f"{i+1}. {r}" for i, r in enumerate(results))


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网，返回相关网页标题、链接和摘要。适用于查找最新信息、技术文档等。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回几条结果，默认 5",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


def run(params: dict[str, Any]) -> str:
    query = params.get("query", "")
    max_results = int(params.get("max_results", MAX_RESULTS))
    if not query:
        return "[错误] query 参数不能为空"
    return web_search(query, max_results=max_results)
