"""ASR 模块：通过 faster-whisper 服务进行语音识别。

faster-whisper 部署在开发机 46（10.138.0.46），提供 96 个实例（端口 8301-8396）。
API 接受原始音频数据 (float32 array)，返回转录文本。
"""

import io
import logging
import os
from typing import Optional

import httpx
import numpy as np
import soundfile as sf

logger = logging.getLogger("im_asr.asr")

# ========================
# 配置
# ========================
_WHISPER_HOST = os.environ.get("WHISPER_HOST", "10.138.0.46")
_WHISPER_PORT = int(os.environ.get("WHISPER_PORT", "8301"))
_WHISPER_TIMEOUT = int(os.environ.get("WHISPER_TIMEOUT", "120"))

# 音频下载超时
_AUDIO_DOWNLOAD_TIMEOUT = int(os.environ.get("AUDIO_DOWNLOAD_TIMEOUT", "300"))
_MAX_AUDIO_SIZE = int(os.environ.get("MAX_AUDIO_SIZE_MB", "50")) * 1024 * 1024


def _whisper_url() -> str:
    return f"http://{_WHISPER_HOST}:{_WHISPER_PORT}"


async def download_audio(url: str) -> tuple[np.ndarray, int]:
    """从 URL 下载音频文件，返回 (audio_array, sample_rate)

    支持的格式：wav, mp3, m4a, ogg 等（通过 soundfile 自动识别）。
    """
    logger.info("Downloading audio from: %s", url)

    async with httpx.AsyncClient(timeout=_AUDIO_DOWNLOAD_TIMEOUT) as client:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()

    raw_bytes = response.content
    if len(raw_bytes) > _MAX_AUDIO_SIZE:
        raise ValueError(
            f"音频文件过大: {len(raw_bytes) / 1024 / 1024:.1f}MB > {_MAX_AUDIO_SIZE / 1024 / 1024:.0f}MB"
        )

    logger.info("Downloaded %d bytes", len(raw_bytes))

    # 用 soundfile 读取音频数据
    try:
        audio_data, sample_rate = sf.read(io.BytesIO(raw_bytes))
    except Exception as e:
        raise ValueError(f"无法解析音频文件: {e}")

    # 确保是 mono（单声道）
    if len(audio_data.shape) > 1:
        audio_data = audio_data.mean(axis=1)

    # 确保是 float32
    if audio_data.dtype != np.float32:
        audio_data = audio_data.astype(np.float32)

    logger.info("Audio: %d samples, %d Hz, %.1f seconds",
                len(audio_data), sample_rate, len(audio_data) / sample_rate)

    return audio_data, sample_rate


async def transcribe_audio(
    audio_data: np.ndarray,
    language: Optional[str] = None,
) -> dict:
    """将音频数据发送到 faster-whisper 服务进行转录

    Args:
        audio_data: float32 音频数组
        language: 可选的语言代码，不传则自动检测

    Returns:
        {"text": "...", "language": "...", "error": ""}
    """
    url = f"{_whisper_url()}/transcribe/"
    payload = {
        "audio": audio_data.tolist(),
    }
    if language:
        payload["language"] = language

    logger.info("Transcribing audio (language=%s, samples=%d)", language or "auto", len(audio_data))

    async with httpx.AsyncClient(timeout=_WHISPER_TIMEOUT) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            logger.error("Whisper transcription failed: %s", e)
            return {"text": "", "language": "", "error": f"转录失败: {e}"}

    text = result.get("text", result.get("transcription", ""))
    lang = result.get("language", result.get("detected_language", ""))

    logger.info("Transcription result: language=%s, text_length=%d", lang, len(text))

    return {
        "text": text,
        "language": lang,
        "error": "",
    }


async def detect_language(audio_data: np.ndarray) -> str:
    """检测音频的语种"""
    url = f"{_whisper_url()}/detect_language/"
    payload = {"audio": audio_data.tolist()}

    async with httpx.AsyncClient(timeout=_WHISPER_TIMEOUT) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            return result.get("language", result.get("detected_language", ""))
        except Exception as e:
            logger.warning("Language detection failed: %s", e)
            return ""
