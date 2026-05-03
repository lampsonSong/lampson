"""网络搜索工具：通过 Bing 国内版搜索，稳定可达。"""

from __future__ import annotations

from typing import Any
import re

import httpx
from bs4 import BeautifulSoup


BING_URL = "https://cn.bing.com/search"
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
            response = client.get(
                BING_URL,
                params={"q": query, "count": max_results * 2},
                headers=HEADERS,
            )
            response.raise_for_status()
    except httpx.TimeoutException:
        return "[超时] 搜索请求超时，请稍后重试。"
    except httpx.HTTPError as e:
        return f"[错误] 搜索请求失败：{e}"

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for result in soup.select("li.b_algo")[:max_results]:
        h2 = result.find("h2")
        if not h2:
            continue

        title = h2.get_text(strip=True)
        link_tag = h2.find("a")
        url = link_tag.get("href", "") if link_tag else ""

        # 摘要：优先 p 标签，其次 b_caption div
        snippet = ""
        snippet_p = result.find("p")
        if snippet_p:
            snippet = snippet_p.get_text(strip=True)
        else:
            cap_div = result.find("div", class_=re.compile(r"b_caption"))
            if cap_div:
                snippet = cap_div.get_text(strip=True)

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
