"""Per-key privacy floor: GATEWAY_API_KEYS=key:tier."""
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.policy import auth
from gateway.policy.privacy import apply_privacy_floor
from gateway.server import app

client = TestClient(app)
OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"
BODY = {"model": "local-fast", "messages": [{"role": "user", "content": "Hi"}]}
SEC = {"security": {"default_privacy": "personal", "require_api_key_for_lan": True}}


def _ok():
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                                     "choices": [{"message": {"role": "assistant", "content": "ok"}}]})


def test_apply_privacy_floor():
    assert apply_privacy_floor("public", "personal") == "personal"        # raised
    assert apply_privacy_floor("restricted", "personal") == "restricted"  # never lowered
    assert apply_privacy_floor("personal", None) == "personal"
    assert apply_privacy_floor("weird", "personal") == "weird"            # unknown left to route policy


def test_parse_keys_with_and_without_tiers(monkeypatch, caplog):
    monkeypatch.setenv("GATEWAY_API_KEYS", " a , b:Personal, c:nonsense ,, d:restricted")
    with caplog.at_level("WARNING", logger="gateway.auth"):
        assert auth.parse_keys() == [("a", None), ("b", "personal"), ("c", None), ("d", "restricted")]
    assert "unknown privacy tier" in caplog.text
    assert auth.configured_keys() == ["a", "b", "c", "d"]
    assert auth.authenticate("b") == (True, "personal") and auth.authenticate("zzz") == (False, None)


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(server, "load_security_config", lambda: SEC)
    monkeypatch.setenv("GATEWAY_API_KEYS", "plain,floored:confidential")


@respx.mock
def test_floored_key_cannot_declare_a_lower_tier(keyed):
    respx.post(OLLAMA_URL).mock(return_value=_ok())
    r = client.post("/v1/chat/completions", json=BODY,
                    headers={"Authorization": "Bearer floored", "x-ai-privacy": "public"})
    assert r.status_code == 200 and r.headers["x-ai-policy"] == "confidential"
    r = client.post("/v1/chat/completions", json={**BODY, "metadata": {"privacy": "public"}},
                    headers={"Authorization": "Bearer floored"})
    assert r.headers["x-ai-policy"] == "confidential"


@respx.mock
def test_unfloored_key_and_loopback_keep_declared_tier(keyed):
    respx.post(OLLAMA_URL).mock(return_value=_ok())
    r = client.post("/v1/chat/completions", json=BODY,
                    headers={"Authorization": "Bearer plain", "x-ai-privacy": "public"})
    assert r.headers["x-ai-policy"] == "public"
    local = TestClient(app, client=("127.0.0.1", 5000))
    assert local.post("/v1/chat/completions", json=BODY, headers={"x-ai-privacy": "public"}).headers["x-ai-policy"] == "public"


@respx.mock
def test_floor_blocks_routes_that_only_allow_lower_tiers(keyed, monkeypatch):
    """cloud-best-style routes allow only 'public'; a floored key can't claim it."""
    routes = {"routes": {"public-only": {"primary": "pc", "privacy_allowed": ["public"]}}, "source": "config"}
    backends = {"backends": {"pc": {"runtime": "ollama", "endpoint": "http://desktop-homepc.local:11434", "enabled": True}}}
    monkeypatch.setattr(server, "load_routes_config", lambda: routes)
    monkeypatch.setattr(server, "load_backends_config", lambda: backends)
    monkeypatch.setattr(server, "load_models_config", lambda: {"models": {}})
    respx.post(OLLAMA_URL).mock(return_value=_ok())
    body = {**BODY, "model": "public-only"}
    ok = client.post("/v1/chat/completions", json=body,
                     headers={"Authorization": "Bearer plain", "x-ai-privacy": "public"})
    blocked = client.post("/v1/chat/completions", json=body,
                          headers={"Authorization": "Bearer floored", "x-ai-privacy": "public"})
    assert ok.status_code == 200 and blocked.status_code == 403
