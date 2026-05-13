"""测试 MiniMax API 连通性"""
import httpx
import json
import sys

KEY = "sk-cp-bbPpAuJNZm7XmMdzV6zM-NvzGRPUbHEMUMAg3jJjzzn_saOovLQqTa3sCYQxJdVOK3m8w"

# 尝试不同 endpoint
endpoints = [
    "https://api.minimax.chat/v1/chat/completions",
]

for url in endpoints:
    try:
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "MiniMax-M2.7-highspeed",
                "messages": [{"role": "user", "content": "回复OK即可"}],
                "max_tokens": 20,
            },
            timeout=15,
        )
        print(f"[{url}]")
        print(f"  状态码: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"  返回内容: {content}")
            print("  ✅ 连接成功！")
        else:
            print(f"  响应: {resp.text[:300]}")
            print("  ❌ 连接失败")
    except Exception as e:
        print(f"[{url}]")
        print(f"  异常: {type(e).__name__}: {e}")
        print("  ❌ 连接失败")
