"""Body-size cap, per-client rate limit, max_tokens cap."""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.policy import limits
from gateway.server import app

client = TestClient(app)  # "testclient" host => non-loopback
OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"
BODY = {"model": "local-fast", "messages": [{"role": "user", "content": "Hi"}]}


def _ok():
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                                     "choices": [{"message": {"role": "assistant", "content": "ok"}}]})


def _sec(monkeypatch, **lim):
    monkeypatch.setattr(server, "load_security_config", lambda: {"security": {"default_privacy": "personal", "limits": lim}})
    limits._hits.clear()


def test_clamp_max_tokens():
    assert limits.clamp_max_tokens(None, 0) is None and limits.clamp_max_tokens(500, 0) == 500
    assert limits.clamp_max_tokens(None, 100) == 100 and limits.clamp_max_tokens(500, 100) == 100
    assert limits.clamp_max_tokens(50, 100) == 50


def test_limits_config_defaults_and_bad_values():
    assert limits.limits_config({})["max_body_bytes"] == 20 * 1024 * 1024
    assert limits.limits_config({"limits": {"rate_limit_per_minute": "x", "max_tokens_cap": "7"}}) == {
        "max_body_bytes": 20 * 1024 * 1024, "rate_limit_per_minute": 0, "max_tokens_cap": 7}


def test_check_rate_sliding_window():
    limits._hits.clear()
    assert limits.check_rate("c", 2, now=0) == 0 and limits.check_rate("c", 2, now=1) == 0
    assert limits.check_rate("c", 2, now=2) > 0                  # third within 60s: blocked
    assert limits.check_rate("other", 2, now=2) == 0             # separate client unaffected
    assert limits.check_rate("c", 2, now=61) == 0                # window slid


def test_oversized_body_is_413(monkeypatch):
    _sec(monkeypatch, max_body_bytes=100)
    r = client.post("/v1/chat/completions", content=b"x" * 500, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "body_too_large"


@respx.mock
def test_body_under_limit_passes(monkeypatch):
    _sec(monkeypatch, max_body_bytes=10_000)
    respx.post(OLLAMA_URL).mock(return_value=_ok())
    assert client.post("/v1/chat/completions", json=BODY).status_code == 200


@respx.mock
def test_rate_limit_429_with_retry_after_then_per_key(monkeypatch):
    _sec(monkeypatch, rate_limit_per_minute=2)
    respx.post(OLLAMA_URL).mock(return_value=_ok())
    assert [client.post("/v1/chat/completions", json=BODY).status_code for _ in range(2)] == [200, 200]
    r = client.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert r.json()["error"]["code"] == "rate_limit_exceeded"
    assert client.get("/api/health").status_code == 200          # GETs / non-LLM paths unaffected


@respx.mock
def test_rate_limit_is_per_api_key_and_loopback_exempt(monkeypatch):
    sec = {"security": {"default_privacy": "personal", "require_api_key_for_lan": True,
                        "limits": {"rate_limit_per_minute": 1}}}
    monkeypatch.setattr(server, "load_security_config", lambda: sec)
    monkeypatch.setenv("GATEWAY_API_KEYS", "k1,k2")
    limits._hits.clear()
    respx.post(OLLAMA_URL).mock(return_value=_ok())
    h1, h2 = {"Authorization": "Bearer k1"}, {"Authorization": "Bearer k2"}
    assert client.post("/v1/chat/completions", json=BODY, headers=h1).status_code == 200
    assert client.post("/v1/chat/completions", json=BODY, headers=h1).status_code == 429
    assert client.post("/v1/chat/completions", json=BODY, headers=h2).status_code == 200   # other key has its own budget
    local = TestClient(app, client=("127.0.0.1", 5000))
    assert all(local.post("/v1/chat/completions", json=BODY).status_code == 200 for _ in range(3))


def test_unauthenticated_requests_dont_consume_rate_budget(monkeypatch):
    sec = {"security": {"require_api_key_for_lan": True, "limits": {"rate_limit_per_minute": 1}}}
    monkeypatch.setattr(server, "load_security_config", lambda: sec)
    monkeypatch.setenv("GATEWAY_API_KEYS", "k1")
    limits._hits.clear()
    assert [client.post("/v1/chat/completions", json=BODY).status_code for _ in range(3)] == [401, 401, 401]
    assert not limits._hits


@respx.mock
def test_max_tokens_cap_applied_to_backend_request(monkeypatch):
    _sec(monkeypatch, max_tokens_cap=64)
    route = respx.post(OLLAMA_URL).mock(return_value=_ok())
    client.post("/v1/chat/completions", json=BODY)                              # none given -> cap
    assert json.loads(route.calls.last.request.content)["max_tokens"] == 64
    client.post("/v1/chat/completions", json={**BODY, "max_tokens": 9999})      # too big -> cap
    assert json.loads(route.calls.last.request.content)["max_tokens"] == 64
    client.post("/v1/chat/completions", json={**BODY, "max_tokens": 10})        # under cap -> kept
    assert json.loads(route.calls.last.request.content)["max_tokens"] == 10
