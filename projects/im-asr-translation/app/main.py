"""
IM-ASR-Translation 统一服务

合并两个服务：
1. POST /translate  — 文本翻译（原 translationservice）
2. POST /transcribe — 语音转录+翻译（原 im-asr-translation）

技术栈：FastAPI + faster-whisper (ASR) + VLLM (翻译)
"""

import logging
import os
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.models import (
    TranslateRequest,
    TranslateResponse,
    TranscribeRequest,
    TranscribeResponse,
)
from app.translate import (
    default_translate_model,
    translate_model_labels,
    translate_model_registry,
    translate_stream,
    translate_text_chunked,
)
from app.asr import download_audio, transcribe_audio, detect_language

# ========================
# 应用初始化
# ========================
app = FastAPI(
    title="IM-ASR-Translation API",
    description="统一语音转录 + 文本翻译服务（faster-whisper + VLLM）",
    version="1.0.0",
)

logger = logging.getLogger("im_asr")
if not logger.handlers:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# 工具函数
# ========================
def _safe_text(text: str | None, max_len: int = 2000) -> str:
    if not text:
        return ""
    s = str(text)
    return f"{s[:max_len]}...(truncated)" if len(s) > max_len else s


def _safe_decode(raw: bytes, max_len: int = 4000) -> str:
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if len(text) > max_len:
        return f"{text[:max_len]}...(truncated, total={len(text)})"
    return text


# ========================
# 异常处理
# ========================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    logger.warning("Validation failed method=%s path=%s detail=%s",
                   request.method, request.url.path, exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "error": "请求参数校验失败"},
    )


# ========================
# 请求日志中间件
# ========================
@app.middleware("http")
async def log_request_response(request: Request, call_next):
    started = time.perf_counter()
    raw_body = await request.body()
    client_host = request.client.host if request.client else "unknown"

    logger.info("Request method=%s path=%s client=%s body=%s",
                request.method, request.url.path, client_host,
                _safe_decode(raw_body))

    try:
        response = await call_next(request)
    except Exception:
        cost_ms = (time.perf_counter() - started) * 1000
        logger.exception("Response method=%s path=%s status=500 cost_ms=%.2f",
                         request.method, request.url.path, cost_ms)
        raise

    cost_ms = (time.perf_counter() - started) * 1000
    content_type = response.headers.get("content-type", "")
    if isinstance(response, StreamingResponse) or content_type.startswith("text/event-stream"):
        logger.info("Response method=%s path=%s status=%s cost_ms=%.2f body=<streaming>",
                    request.method, request.url.path, response.status_code, cost_ms)
        return response

    # 读取响应体用于日志
    resp_body_bytes = b""
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is not None:
        chunks = []
        async for chunk in body_iterator:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            chunks.append(chunk)
        resp_body_bytes = b"".join(chunks)
    else:
        body = getattr(response, "body", b"")
        if isinstance(body, str):
            resp_body_bytes = body.encode("utf-8", errors="replace")
        elif body:
            resp_body_bytes = body

    logger.info("Response method=%s path=%s status=%s cost_ms=%.2f body=%s",
                request.method, request.url.path, response.status_code, cost_ms,
                _safe_decode(resp_body_bytes))

    # 重建响应（body_iterator 已被消费）
    return Response(
        content=resp_body_bytes,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
        background=response.background,
    )


# ========================
# 路由: 健康检查
# ========================
@app.get("/health")
async def health():
    return {"status": "ok", "service": "im-asr-translation"}


@app.get("/models")
async def list_models():
    """列出可用的翻译模型"""
    labels = translate_model_labels()
    registry = translate_model_registry()
    models = [
        {"id": mid, "name": labels.get(mid, mid), "api_base": reg[0]}
        for mid, reg in registry.items()
    ]
    return {"models": models, "default": default_translate_model()}


# ========================
# 路由: 文本翻译（原翻译服务）
# ========================
@app.post("/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest):
    """
    文本翻译到指定语种。

    支持长文本自动分块、分批并行翻译。
    流式返回请设置 stream=true（返回 SSE 格式）。
    """
    if req.stream:
        return StreamingResponse(
            translate_stream(req.text, req.target_language, req.model),
            media_type="text/event-stream",
        )

    result = await translate_text_chunked(req.text, req.target_language, req.model)
    logger.info("TranslateResult model=%s input_language=%s error=%s",
                result.get("model"), result.get("input_language"),
                _safe_text(result.get("error", ""), max_len=500))

    return TranslateResponse(**result)


# ========================
# 路由: 语音转录 + 翻译（原 IM-ASR 服务）
# ========================
@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(req: TranscribeRequest):
    """
    语音转录并翻译到指定语种。

    流程：下载音频 → faster-whisper 转录 → VLLM 翻译
    """
    # 1. 下载音频
    try:
        audio_data, sample_rate = await download_audio(req.audio_url)
    except Exception as e:
        logger.error("Audio download failed: %s", e)
        return TranscribeResponse(
            transcribed_text="",
            translated_text="",
            error=f"音频下载失败: {e}",
            model=req.model,
        )

    # 2. ASR 转录
    asr_result = await transcribe_audio(audio_data, language=req.source_language)
    if asr_result["error"]:
        return TranscribeResponse(
            transcribed_text="",
            translated_text="",
            error=asr_result["error"],
            model=req.model,
        )

    transcribed_text = asr_result["text"]
    detected_lang = asr_result["language"]

    if not transcribed_text.strip():
        return TranscribeResponse(
            transcribed_text="",
            translated_text="",
            error="转录结果为空，无法翻译",
            model=req.model,
            input_language=detected_lang,
        )

    # 3. 翻译
    translation = await translate_text_chunked(transcribed_text, req.target_language, req.model)

    logger.info("TranscribeResult audio_len=%.1fs transcribed_len=%d input_lang=%s error=%s",
                len(audio_data) / sample_rate,
                len(transcribed_text),
                detected_lang or translation.get("input_language", "?"),
                _safe_text(translation.get("error", ""), max_len=500))

    return TranscribeResponse(
        transcribed_text=transcribed_text,
        translated_text=translation.get("translated_text", ""),
        input_language=detected_lang or translation.get("input_language", ""),
        error=translation.get("error", ""),
        model=req.model,
    )


# ========================
# 入口
# ========================
if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5031"))
    workers = int(os.environ.get("UVICORN_WORKERS", "1"))
    uvicorn.run("app.main:app", host=host, port=port, workers=workers)
