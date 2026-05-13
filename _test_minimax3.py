# -*- coding: utf-8 -*-
"""尝试多个可能的 API 端点"""
import httpx

KEY = "sk-cp-bbPpAuJNZm7XmMdzV6zM-NvzGRPUbHEMUMAg3jJjzzn_saOovLQqTa3sCYQxJdVOK3m8w"

# 可能的端点和模型组合
attempts = [
    ("https://api.openclaw.ac.cn/v1/chat/completions", "MiniMax-M2.7-highspeed"),
    ("https://api.minimaxi.com/v1/chat/completions", "MiniMax-M2.7-highspeed"),
    ("https://api.minimax.chat/v1/chat/completions", "MiniMax-M2.7-highspeed"),
]

for url, model in attempts:
    try:
        resp = httpx.post(
            url,
            headers={
                "Authorization": "Bearer " + KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 10,
            },
            timeout=10,
        )
        print(f"[{url}] model={model}")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"  Reply: {content}")
        else:
            print(f"  Body: {resp.text[:200]}")
    except Exception as e:
        print(f"[{url}] Error: {type(e).__name__}: {str(e)[:100]}")
    print()
