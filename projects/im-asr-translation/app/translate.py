"""翻译模块：通过 VLLM (OpenAI 兼容接口) 进行文本翻译。

从 translationservice 的 model_server.py 适配而来，核心逻辑保持一致。
"""

import json
import os
import re
import time
from typing import Optional

from openai import OpenAI

# ========================
# 配置
# ========================
_CHUNK_SIZE = int(os.environ.get("TRANSLATE_CHUNK_SIZE", "300"))
_BATCH_SIZE = int(os.environ.get("TRANSLATE_BATCH_SIZE", "5"))
_VLLM_HOST = os.environ.get("TRANSLATE_VLLM_HOST", "10.224.231.142")
_VLLM_PORT = os.environ.get("TRANSLATE_VLLM_PORT", "9898")
_LLM_TIMEOUT = int(os.environ.get("TRANSLATE_LLM_TIMEOUT", "30"))


# ========================
# 模型注册
# ========================
def translate_model_registry() -> dict[str, tuple[str, str]]:
    """served_model_name -> (OpenAI base_url, vLLM model id)"""
    h = _VLLM_HOST
    p = _VLLM_PORT
    return {
        "GPTOssModel": (f"http://{h}:{p}/v1", "GPTOssModel"),
    }


def translate_model_labels() -> dict[str, str]:
    """展示名映射"""
    return {
        "GPTOssModel": "GPT-OSS 120B",
    }


def default_translate_model() -> str:
    return os.environ.get("TRANSLATE_LLM_MODEL", "GPTOssModel")


def resolve_translate_model(model_id: str) -> Optional[tuple[str, str]]:
    """返回 (api_base, vllm_model_id)"""
    return translate_model_registry().get(model_id)


# ========================
# 文本分块
# ========================
def split_text_into_chunks(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """
    先按标点拆句，再贪心合并到 chunk_size。
    优先级：句末标点 > 段落空行 > 次级标点 > 按字数硬截。
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    def _merge(sentences: list[str]) -> list[str]:
        chunks, current = [], ""
        for s in sentences:
            if not current:
                current = s
            elif len(current) + len(s) <= chunk_size:
                current += s
            else:
                chunks.append(current)
                current = s
        if current:
            chunks.append(current)
        return chunks

    # 1. 句末标点 + 段落空行拆句
    raw = re.split(r'(?<=[。！？!?])\s*|\n{2,}', text)
    sentences = [s for s in raw if s.strip()]

    # 2. 没有句末标点 → 尝试次级标点
    if len(sentences) <= 1:
        raw = re.split(r'(?<=[，,；;、])\s*', text)
        sentences = [s for s in raw if s.strip()]

    # 3. 完全无标点 → 按 chunk_size 硬截
    if len(sentences) <= 1:
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    return _merge(sentences)


# ========================
# JSON 解析
# ========================
def _parse_llm_json(content: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON"""
    if not content or not content.strip():
        return None

    s = content.strip()

    # 去掉 ```json ``` 包裹
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if m:
        s = m.group(1).strip()

    # 截断 thinking
    if "{" in s:
        s = s[s.find("{"):]

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 从后往前找最后一个 JSON
    for start in range(len(s) - 1, -1, -1):
        if s[start] != "{":
            continue
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
        break
    return None


# ========================
# LLM 调用
# ========================
def _build_translate_prompt(text: str, target_language: str) -> list[dict]:
    """构造翻译 prompt"""
    prompt = f"""You are a strict JSON translation API.

TASK:
1. Detect input language (ISO code like en, zh, ja)
2. Translate to target language: {target_language}

STRICT RULES:
- Output MUST be valid JSON
- Output MUST start with '{{' and end with '}}'
- DO NOT output any explanation
- DO NOT output "Thinking Process"
- DO NOT output reasoning
- DO NOT use markdown
- ONLY output JSON

FORMAT:
{{"input_language": "en", "translated_text": "翻译后的文本"}}"""
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]


def call_llm(
    msg_list: list[dict],
    api_base: str,
    model: str,
    max_retries: int = 3,
    retry_delay: int = 2,
):
    """调用 VLLM OpenAI 兼容接口"""
    client = OpenAI(api_key="EMPTY", base_url=api_base)

    for attempt in range(1, max_retries + 1):
        try:
            return client.chat.completions.create(
                model=model,
                messages=msg_list,
                timeout=_LLM_TIMEOUT,
                response_format={"type": "json_object"},
                temperature=0,
                extra_body={"reasoning_effort": "low"},
            )
        except Exception as e:
            if attempt < max_retries:
                print(f"[call_llm] Error: {e}, retry {attempt}/{max_retries}")
                time.sleep(retry_delay)
            else:
                raise


def call_llm_stream(msg_list: list[dict], api_base: str, model: str):
    """流式调用 VLLM"""
    client = OpenAI(api_key="EMPTY", base_url=api_base)

    for attempt in range(1, 4):
        try:
            return client.chat.completions.create(
                model=model,
                messages=msg_list,
                timeout=_LLM_TIMEOUT,
                response_format={"type": "json_object"},
                temperature=0,
                stream=True,
                extra_body={"reasoning_effort": "low"},
            )
        except Exception as e:
            if attempt < 3:
                print(f"[call_llm_stream] Error: {e}, retry {attempt}/3")
                time.sleep(2)
            else:
                raise


# ========================
# 翻译服务函数
# ========================
def translate_text(text: str, target_language: str, model: Optional[str] = None) -> dict:
    """单段文本翻译，返回 {input_language, translated_text, error, model}"""
    mid = (model or default_translate_model()).strip()

    if not text or not target_language:
        return {"input_language": "", "translated_text": "", "error": "text or target_language is empty", "model": mid}

    resolved = resolve_translate_model(mid)
    if not resolved:
        available = ", ".join(translate_model_registry().keys())
        return {
            "input_language": "", "translated_text": "",
            "error": f"未知模型: {mid!r}，可选: {available}", "model": mid,
        }

    api_base, vllm_model_id = resolved
    msg_list = _build_translate_prompt(text, target_language)

    try:
        response = call_llm(msg_list, api_base=api_base, model=vllm_model_id)
        content = response.choices[0].message.content or ""
        parsed = _parse_llm_json(content)
        if parsed:
            return {
                "input_language": parsed.get("input_language", "unknown"),
                "translated_text": parsed.get("translated_text", ""),
                "error": "",
                "model": mid,
            }
        return {
            "input_language": "", "translated_text": "",
            "error": f"LLM 返回非 JSON: {content[:200]!r}", "model": mid,
        }
    except Exception as e:
        return {
            "input_language": "", "translated_text": "",
            "error": f"LLM 调用失败: {e}", "model": mid,
        }


async def translate_text_chunked(
    text: str,
    target_language: str,
    model: Optional[str] = None,
    loop=None,
) -> dict:
    """长文本自动分块、分批并行翻译"""
    import asyncio

    if loop is None:
        loop = asyncio.get_event_loop()

    chunks = split_text_into_chunks(text)

    if len(chunks) <= 1:
        return translate_text(text, target_language, model)

    results = []
    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start: batch_start + _BATCH_SIZE]
        tasks = [
            loop.run_in_executor(
                None,
                lambda c=chunk: translate_text(c, target_language, model),
            )
            for chunk in batch
        ]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)

    input_language = next(
        (r.get("input_language") for r in results
         if r.get("input_language") not in ("", "unknown", None)),
        "unknown",
    )
    errors = [r["error"] for r in results if r.get("error")]
    translated_parts = [r.get("translated_text", "") for r in results]

    return {
        "input_language": input_language,
        "translated_text": "\n".join(p for p in translated_parts if p),
        "error": "; ".join(errors) if errors else "",
        "model": (model or default_translate_model()).strip(),
    }


def translate_stream(text: str, target_language: str, model: Optional[str] = None):
    """流式翻译生成器，yield SSE 格式字符串"""
    mid = (model or default_translate_model()).strip()

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    if not text or not target_language:
        yield _sse({"type": "error", "error": "text or target_language is empty", "model": mid})
        return

    resolved = resolve_translate_model(mid)
    if not resolved:
        yield _sse({
            "type": "error",
            "error": f"未知模型: {mid!r}，可选: " + ", ".join(translate_model_registry().keys()),
            "model": mid,
        })
        return

    api_base, vllm_model_id = resolved
    msg_list = _build_translate_prompt(text, target_language)

    full_content = ""
    try:
        stream = call_llm_stream(msg_list, api_base=api_base, model=vllm_model_id)
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_content += delta
                yield _sse({"type": "chunk", "content": delta})

        parsed = _parse_llm_json(full_content)
        if parsed:
            yield _sse({
                "type": "done",
                "input_language": parsed.get("input_language", "unknown"),
                "translated_text": parsed.get("translated_text", ""),
                "model": mid,
            })
        else:
            yield _sse({"type": "error", "error": f"LLM 返回非 JSON: {full_content[:200]!r}", "model": mid})
    except Exception as e:
        yield _sse({"type": "error", "error": f"LLM 调用失败: {e}", "model": mid})
