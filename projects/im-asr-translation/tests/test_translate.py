"""翻译模块测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.translate import split_text_into_chunks, _parse_llm_json, _build_translate_prompt


def test_split_text_short():
    """短文本不分块"""
    chunks = split_text_into_chunks("Hello world", chunk_size=300)
    assert chunks == ["Hello world"]


def test_split_text_empty():
    assert split_text_into_chunks("") == []
    assert split_text_into_chunks(None) == []


def test_split_text_long():
    """长文本按句末标点分块"""
    text = "第一句。第二句！第三句？第四句。" * 20
    chunks = split_text_into_chunks(text, chunk_size=100)
    assert len(chunks) > 1
    # 每块不超过 chunk_size
    for c in chunks:
        assert len(c) <= 100


def test_split_text_no_punctuation():
    """无标点文本按字数硬截"""
    text = "一二三四五六七八九十" * 30
    chunks = split_text_into_chunks(text, chunk_size=50)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 50


def test_parse_llm_json_plain():
    """标准 JSON 解析"""
    result = _parse_llm_json('{"input_language": "en", "translated_text": "hello"}')
    assert result == {"input_language": "en", "translated_text": "hello"}


def test_parse_llm_json_markdown():
    """带 markdown 包裹的 JSON"""
    result = _parse_llm_json('```json\n{"input_language": "zh", "translated_text": "你好"}\n```')
    assert result == {"input_language": "zh", "translated_text": "你好"}


def test_parse_llm_json_with_thinking():
    """带 thinking 前缀的 JSON"""
    content = "Let me think... ok I will translate\n{\"input_language\": \"ja\", \"translated_text\": \"こんにちは\"}"
    result = _parse_llm_json(content)
    assert result == {"input_language": "ja", "translated_text": "こんにちは"}


def test_parse_llm_json_no_json():
    """非 JSON 内容返回 None"""
    assert _parse_llm_json("Hello, this is not JSON") is None
    assert _parse_llm_json("") is None
    assert _parse_llm_json(None) is None


def test_build_translate_prompt():
    """翻译 prompt 结构"""
    msgs = _build_translate_prompt("Hello", "zh")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "zh" in msgs[0]["content"]
    assert msgs[1]["content"] == "Hello"
