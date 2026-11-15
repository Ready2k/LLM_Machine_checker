"""API-key auth middleware."""
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.server import app

client = TestClient(app)  # client host is "testclient" => non-loopback

SEC = {"security": {"default_privacy": "personal", "require_api_key_for_lan": True}}


def _unused():
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m",
                                     "choices": [{"message": {"role": "assistant", "content": "ok"}}]})


# ------------------------------- auth ------------------------------------- #

@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(server, "load_security_config", lambda: SEC)
    monkeypatch.setenv("GATEWAY_API_KEYS", "k1, k2")


def test_protected_routes_require_key(enforced):
    assert client.get("/v1/models").status_code == 401
    assert client.get("/api/stats").status_code == 401
    assert client.post("/chat/title", json={"messages": []}).status_code == 401


def test_wrong_key_rejected_right_key_accepted(enforced):
    assert client.get("/api/machines", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/machines", headers={"Authorization": "Bearer k2"}).status_code != 401
    assert client.get("/api/machines", headers={"x-api-key": "k1"}).status_code != 401


def test_open_paths_stay_open(enforced):
    assert client.get("/api/health").status_code == 200
    assert client.get("/chat/").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/static/auth.js").status_code == 200


def test_loopback_is_trusted(enforced):
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.get("/api/machines").status_code != 401


def test_fail_open_when_no_keys_configured(monkeypatch):
    monkeypatch.setattr(server, "load_security_config", lambda: SEC)
    monkeypatch.delenv("GATEWAY_API_KEYS", raising=False)
    assert client.get("/api/machines").status_code != 401
