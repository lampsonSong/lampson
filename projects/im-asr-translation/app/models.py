"""请求/响应模型定义"""

from pydantic import BaseModel, Field


class TranslateRequest(BaseModel):
    """文本翻译请求"""
    text: str = Field(..., description="待翻译文本")
    target_language: str = Field(..., description="目标语种代码，如 en/zh/ja/ar 等")
    model: str = Field(default="GPTOssModel", description="VLLM 模型 ID")
    stream: bool = Field(default=False, description="是否流式返回")


class TranslateResponse(BaseModel):
    """文本翻译响应"""
    input_language: str = ""
    translated_text: str = ""
    error: str = ""
    model: str = ""


class TranscribeRequest(BaseModel):
    """语音转录请求"""
    audio_url: str = Field(..., description="音频文件 URL")
    source_language: str | None = Field(default=None, description="源语言代码，不传则自动检测")
    target_language: str = Field(..., description="目标语种代码")
    model: str = Field(default="GPTOssModel", description="翻译用的 VLLM 模型 ID")


class TranscribeResponse(BaseModel):
    """语音转录+翻译响应"""
    transcribed_text: str = ""
    translated_text: str = ""
    input_language: str = ""
    error: str = ""
    model: str = ""
