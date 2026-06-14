import pytest
from fastapi.testclient import TestClient
from gateway.server import app
import httpx
import respx

client = TestClient(app)

@respx.mock
def test_chat_completions_basic_routing():
    # Mock Ollama backend for local-fast
    route = respx.post("http://pc-ai.local:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 123456789,
            "model": "qwen2.5-coder:7b",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}]
        })
    )

    response = client.post("/v1/chat/completions", json={
        "model": "local-fast",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["model"] == "qwen2.5-coder:7b"
    assert response.headers["x-ai-route"] == "local-fast"
    assert response.headers["x-ai-backend"] == "pc-rtx2070"
    assert route.called

def test_chat_completion_unknown_model_rejected():
    response = client.post("/v1/chat/completions", json={
        "model": "unknown-model",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False
    })
    
    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "unknown_model"

def test_streaming_rejected():
    response = client.post("/v1/chat/completions", json={
        "model": "local-fast",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": True
    })
    
    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "stream_disabled"

def test_cloud_best_request_returns_route_disabled():
    response = client.post("/v1/chat/completions", json={
        "model": "cloud-best",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False
    })
    
    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "route_not_configured"
    assert response.json()["detail"]["code"] == "route_disabled"
