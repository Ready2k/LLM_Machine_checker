"""Runtime backend fallback (primary -> fallback on connect error / timeout / 5xx)."""
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.server import app

client = TestClient(app)  # client host is "testclient" => non-loopback

BODY = {"model": "local-fast", "messages": [{"role": "user", "content": "Hi"}], "stream": False}
PRIMARY = "http://desktop-homepc.local:11434/v1/chat/completions"
FALLBACK = "http://fallback.local:1234/v1/chat/completions"

def _ok():
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                                     "choices": [{"message": {"role": "assistant", "content": "ok"}}]})


# ------------------------------ fallback ---------------------------------- #

@pytest.fixture
def two_backends(monkeypatch):
    routes = {"routes": {"local-fast": {"primary": "pc", "fallback": "mac", "privacy_allowed": ["personal"]}},
              "source": "config"}
    backends = {"backends": {
        "pc": {"runtime": "ollama", "endpoint": "http://desktop-homepc.local:11434", "enabled": True},
        "mac": {"runtime": "openai_compatible", "endpoint": "http://fallback.local:1234", "enabled": True},
    }}
    monkeypatch.setattr(server, "load_routes_config", lambda: routes)
    monkeypatch.setattr(server, "load_backends_config", lambda: backends)
    monkeypatch.setattr(server, "load_models_config", lambda: {"models": {}})


@respx.mock
def test_falls_back_when_primary_unreachable(two_backends):
    respx.post(PRIMARY).mock(side_effect=httpx.ConnectError("down"))
    respx.post(FALLBACK).mock(return_value=_ok())
    r = client.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 200
    assert r.headers["x-ai-backend"] == "mac"
    assert r.headers["x-ai-fallback-used"] == "true"


@respx.mock
def test_falls_back_on_primary_5xx(two_backends):
    respx.post(PRIMARY).mock(return_value=httpx.Response(503))
    respx.post(FALLBACK).mock(return_value=_ok())
    assert client.post("/v1/chat/completions", json=BODY).headers["x-ai-backend"] == "mac"


@respx.mock
def test_no_fallback_on_4xx(two_backends):
    respx.post(PRIMARY).mock(return_value=httpx.Response(400))
    fb = respx.post(FALLBACK).mock(return_value=_ok())
    assert client.post("/v1/chat/completions", json=BODY).status_code == 502
    assert not fb.called


@respx.mock
def test_502_when_all_backends_fail(two_backends):
    respx.post(PRIMARY).mock(side_effect=httpx.ConnectError("down"))
    respx.post(FALLBACK).mock(side_effect=httpx.ConnectError("down"))
    assert client.post("/v1/chat/completions", json=BODY).status_code == 502


@respx.mock
def test_stream_falls_back_before_first_chunk(two_backends):
    respx.post(PRIMARY).mock(side_effect=httpx.ConnectError("down"))
    respx.post(FALLBACK).mock(return_value=httpx.Response(200, content=b"data: hi\n\n"))
    r = client.post("/v1/chat/completions", json={**BODY, "stream": True})
    assert r.status_code == 200
    assert r.headers["x-ai-backend"] == "mac"
    assert b"data: hi" in r.content
