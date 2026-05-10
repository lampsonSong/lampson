"""网络搜索工具：DuckDuckGo HTML，走代理优先。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup


DDG_URL = "https://html.duckduckgo.com/html/"
PROXY_URL = "http://127.0.0.1:17890"  # 飞毯VPN Clash 代理
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


def _clean_ddg_url(raw_url: str) -> str:
    """从 DuckDuckGo 跳转链接中提取真实 URL。"""
    if not raw_url:
        return ""
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    if "uddg=" in raw_url:
        parsed = parse_qs(urlparse(raw_url).query)
        if "uddg" in parsed:
            return unquote(parsed["uddg"][0])
    return raw_url


def _parse_ddg(html: str, max_results: int) -> list[str]:
    """解析 DuckDuckGo HTML 搜索结果。"""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for result in soup.select(".result")[:max_results]:
        title_el = result.select_one(".result__title a, .result__a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        raw_url = title_el.get("href", "")
        url = _clean_ddg_url(raw_url)
        snippet_el = result.select_one(".result__snippet")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title:
            results.append(f"**{title}**\n{url}\n{snippet}")
    return results


def _ddg_search(query: str, max_results: int, proxy: str | None = None) -> list[str]:
    """执行一次 DDG 搜索。"""
    kwargs = dict(timeout=TIMEOUT, follow_redirects=True)
    if proxy:
        kwargs["proxy"] = proxy
    with httpx.Client(**kwargs) as client:
        response = client.post(
            DDG_URL,
            data={"q": query, "kl": "cn-zh"},
            headers=HEADERS,
        )
        response.raise_for_status()
        return _parse_ddg(response.text, max_results)


def web_search(query: str, max_results: int = MAX_RESULTS) -> str:
    """搜索网页：代理优先 → 直连 fallback。"""
    if not query.strip():
        return "[错误] 搜索词不能为空"

    results = []

    # 1. 走代理（国内直连 DDG 不稳定）
    try:
        results = _ddg_search(query, max_results, proxy=PROXY_URL)
    except (httpx.TimeoutException, httpx.HTTPError, Exception):
        pass

    # 2. 直连 fallback
    if not results:
        try:
            results = _ddg_search(query, max_results)
        except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
            return f"[错误] 搜索失败（代理和直连均不可用）：{e}"

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
