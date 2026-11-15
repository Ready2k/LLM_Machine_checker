"""OpenAI params beyond temperature/max_tokens must reach the backend."""
import json

import httpx
import respx
from fastapi.testclient import TestClient

from gateway.providers.base import ChatCompletionRequest
from gateway.providers.openai_compatible import build_payload
from gateway.server import app

client = TestClient(app)
OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"


def test_build_payload_forwards_allowlisted_extras_only():
    req = ChatCompletionRequest(
        model="m", messages=[{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"}, top_p=0.9, stop=["\n"],
        tools=[{"type": "function", "function": {"name": "f"}}], bogus_field="x",
    )
    p = build_payload(req, "mapped", stream=False)
    assert p["response_format"] == {"type": "json_object"}
    assert p["top_p"] == 0.9 and p["stop"] == ["\n"] and p["tools"][0]["function"]["name"] == "f"
    assert "bogus_field" not in p
    assert p["model"] == "mapped" and p["stream"] is False


def test_build_payload_core_fields_unchanged():
    req = ChatCompletionRequest(model="m", messages=[], temperature=0.2, max_tokens=5,
                                metadata={"correlation_id": "abc"})
    p = build_payload(req, "x", stream=True)
    assert p == {"model": "x", "messages": [], "stream": True, "user": "abc",
                 "temperature": 0.2, "max_tokens": 5}


@respx.mock
def test_extras_survive_routing_end_to_end():
    backend = respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"message": {"role": "assistant", "content": "{}"}}]}))
    r = client.post("/v1/chat/completions", json={
        "model": "local-fast", "messages": [{"role": "user", "content": "Hi"}],
        "response_format": {"type": "json_object"}, "seed": 7})
    assert r.status_code == 200
    sent = json.loads(backend.calls.last.request.content)
    assert sent["response_format"] == {"type": "json_object"} and sent["seed"] == 7
