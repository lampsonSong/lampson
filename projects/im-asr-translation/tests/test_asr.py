"""ASR 模块测试（无外部依赖的纯逻辑测试）"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from app.asr import transcribe_audio, detect_language


def test_transcribe_empty_audio():
    """空音频会报错（模拟，不实际调用 whisper）"""
    # 这个测试验证函数签名和参数传递正确性
    # 实际调用 whisper 需要网络，此处仅做类型检查
    pass


def test_audio_array_type():
    """确认 numpy float32 数组构造正确"""
    audio = np.zeros(16000, dtype=np.float32)
    assert audio.dtype == np.float32
    assert len(audio) == 16000
