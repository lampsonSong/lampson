"""API 路由测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["service"] == "im-asr-translation"


def test_models():
    resp = client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "default" in data
    assert len(data["models"]) > 0


def test_translate_validation():
    """缺少参数返回 422"""
    resp = client.post("/translate", json={})
    assert resp.status_code == 422

    resp = client.post("/translate", json={"text": "hello"})
    assert resp.status_code == 422


def test_translate_empty_text():
    """空文本返回 error"""
    resp = client.post("/translate", json={
        "text": "",
        "target_language": "zh",
    })
    data = resp.json()
    assert data["error"] != ""


def test_transcribe_validation():
    """缺少 audio_url 返回 422"""
    resp = client.post("/transcribe", json={})
    assert resp.status_code == 422
