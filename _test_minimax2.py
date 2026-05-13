# -*- coding: utf-8 -*-
"""测试 MiniMax API - 正确端点"""
import httpx

KEY = "sk-cp-bbPpAuJNZm7XmMdzV6zM-NvzGRPUbHEMUMAg3jJjzzn_saOovLQqTa3sCYQxJdVOK3m8w"
BASE_URL = "https://api.minimaxi.com/v1/"

url = BASE_URL + "chat/completions"
print("测试 URL:", url)

try:
    resp = httpx.post(
        url,
        headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
        },
        json={
            "model": "MiniMax-M2.7-highspeed",
            "messages": [{"role": "user", "content": "回复OK即可"}],
            "max_tokens": 20,
        },
        timeout=15,
    )
    print("状态码:", resp.status_code)
    data = resp.json()
    print("响应:", resp.text[:500])
    if resp.status_code == 200:
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        print("模型回复:", content)
        print("OK - 连接成功")
    else:
        print("FAIL - 连接失败")
except Exception as e:
    print("异常:", type(e).__name__, str(e))
