"""Tests for the stateless chat utility endpoints: memory extraction + title."""

import httpx
import respx
from fastapi.testclient import TestClient

from gateway.server import app

client = TestClient(app)

# local-fast routes to the Ollama backend in the test config (see
# test_gateway_chat_completions.py).
OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "qwen2.5-coder:7b",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    })


@respx.mock
def test_memory_extract_returns_facts():
    respx.post(OLLAMA_URL).mock(
        return_value=_completion('Sure: ["Prefers Python", "Building a Raspberry Pi gateway"]')
    )
    r = client.post("/chat/memory/extract", json={"messages": [
        {"role": "user", "content": "I love Python and I'm building a Pi gateway"},
        {"role": "assistant", "content": "Great!"},
    ]})
    assert r.status_code == 200
    assert r.json()["facts"] == ["Prefers Python", "Building a Raspberry Pi gateway"]


@respx.mock
def test_memory_extract_caps_at_five():
    respx.post(OLLAMA_URL).mock(
        return_value=_completion('["a","b","c","d","e","f","g"]')
    )
    r = client.post("/chat/memory/extract", json={"messages": [
        {"role": "user", "content": "lots"}, {"role": "assistant", "content": "ok"},
    ]})
    assert r.json()["facts"] == ["a", "b", "c", "d", "e"]


@respx.mock
def test_memory_extract_malformed_output_returns_empty():
    respx.post(OLLAMA_URL).mock(
        return_value=_completion("I couldn't find anything worth remembering.")
    )
    r = client.post("/chat/memory/extract", json={"messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
    ]})
    assert r.status_code == 200
    assert r.json()["facts"] == []


def test_memory_extract_empty_messages_returns_empty():
    # No backend call should be needed when there's nothing to extract.
    r = client.post("/chat/memory/extract", json={"messages": []})
    assert r.status_code == 200
    assert r.json()["facts"] == []


@respx.mock
def test_title_returns_title():
    respx.post(OLLAMA_URL).mock(return_value=_completion("Raspberry Pi LLM Gateway"))
    r = client.post("/chat/title", json={"messages": [
        {"role": "user", "content": "Help me build a Pi gateway"},
        {"role": "assistant", "content": "Sure"},
    ]})
    assert r.status_code == 200
    assert r.json()["title"] == "Raspberry Pi LLM Gateway"


@respx.mock
def test_title_strips_quotes_and_extra_lines():
    respx.post(OLLAMA_URL).mock(return_value=_completion('"My Great Title"\nignored second line'))
    r = client.post("/chat/title", json={"messages": [
        {"role": "user", "content": "x"}, {"role": "assistant", "content": "y"},
    ]})
    assert r.json()["title"] == "My Great Title"


def test_title_empty_messages_returns_empty():
    r = client.post("/chat/title", json={"messages": []})
    assert r.status_code == 200
    assert r.json()["title"] == ""
