"""视觉分析工具：通过 GLM-4V-Flash 分析截图。"""

from __future__ import annotations

from typing import Any

import httpx


# 智谱 GLM-4V-Flash（免费）
API_KEY = "REDACTED"
API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4v-flash"
TIMEOUT = 60


def analyze_image(image_base64: str, prompt: str = "描述这张图片的内容") -> str:
    """用 GLM-4V-Flash 分析图片。

    Args:
        image_base64: 图片的 base64 编码（不含 data:image/... 前缀）
        prompt: 对图片的提问

    Returns:
        模型的文字回复
    """
    try:
        resp = httpx.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_base64}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 1024,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"].get("content", "")
        return f"[错误] API 返回异常：{resp.text[:200]}"
    except httpx.TimeoutException:
        return "[超时] 视觉分析请求超时"
    except Exception as e:
        return f"[错误] 视觉分析失败：{e}"


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "vision_analyze",
        "description": "用视觉模型分析一张图片。传入图片 base64 编码和问题，返回模型对图片的理解。",
        "parameters": {
            "type": "object",
            "properties": {
                "image_base64": {
                    "type": "string",
                    "description": "图片的 base64 编码字符串（不含 data:image/... 前缀）",
                },
                "prompt": {
                    "type": "string",
                    "description": "对图片的提问，例如 '屏幕上有哪些按钮？' 或 '描述当前桌面'",
                    "default": "描述这张图片的内容",
                },
            },
            "required": ["image_base64"],
        },
    },
}


def run(params: dict[str, Any]) -> str:
    image_base64 = params.get("image_base64", "")
    prompt = params.get("prompt", "描述这张图片的内容")
    if not image_base64:
        return "[错误] image_base64 不能为空"
    return analyze_image(image_base64, prompt)
