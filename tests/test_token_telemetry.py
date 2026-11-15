"""Token usage from non-streaming responses is recorded in telemetry."""
import httpx
import respx
from fastapi.testclient import TestClient

from gateway.server import app
from gateway.telemetry import events

client = TestClient(app)
OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"


def _capture(monkeypatch):
    rows = []
    monkeypatch.setattr(events, "_load_telemetry_config", lambda: {"enabled": True})
    monkeypatch.setattr(events, "queue_insert", lambda table, row: rows.append((table, row)))
    return rows


@respx.mock
def test_usage_tokens_are_logged(monkeypatch):
    rows = _capture(monkeypatch)
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}))
    assert client.post("/v1/chat/completions", json={
        "model": "local-fast", "messages": [{"role": "user", "content": "Hi"}]}).status_code == 200
    req = [r for t, r in rows if t == "llm_requests"][-1]
    assert (req["tokens_in"], req["tokens_out"]) == (11, 7)


@respx.mock
def test_missing_usage_logs_null_tokens(monkeypatch):
    rows = _capture(monkeypatch)
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}]}))
    assert client.post("/v1/chat/completions", json={
        "model": "local-fast", "messages": [{"role": "user", "content": "Hi"}]}).status_code == 200
    req = [r for t, r in rows if t == "llm_requests"][-1]
    assert req["tokens_in"] is None and req["tokens_out"] is None
