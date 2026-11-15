"""/v1/embeddings: configurable backend + sane error mapping."""
import httpx
import respx
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.server import app

client = TestClient(app)
BODY = {"model": "e", "input": "hi"}


def _cfg(monkeypatch, backends):
    monkeypatch.setattr(server, "load_backends_config", lambda: {"backends": backends})


@respx.mock
def test_uses_backend_flagged_embeddings(monkeypatch):
    _cfg(monkeypatch, {
        "macbook-m4pro": {"openai_endpoint": "http://legacy.local:1/v1", "enabled": True},
        "emb": {"embeddings": True, "enabled": True, "openai_endpoint": "http://emb.local:2/v1"},
    })
    route = respx.post("http://emb.local:2/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1]}]}))
    r = client.post("/v1/embeddings", json=BODY)
    assert r.status_code == 200 and route.called
    assert r.json()["data"][0]["embedding"] == [0.1]


@respx.mock
def test_legacy_backend_still_works(monkeypatch):
    _cfg(monkeypatch, {"macbook-m4pro": {"openai_endpoint": "http://legacy.local:1/v1", "enabled": True}})
    respx.post("http://legacy.local:1/v1/embeddings").mock(return_value=httpx.Response(200, json={"data": []}))
    assert client.post("/v1/embeddings", json=BODY).status_code == 200


@respx.mock
def test_unreachable_backend_is_502_not_500(monkeypatch):
    _cfg(monkeypatch, {"macbook-m4pro": {"openai_endpoint": "http://legacy.local:1/v1", "enabled": True}})
    respx.post("http://legacy.local:1/v1/embeddings").mock(side_effect=httpx.ConnectError("down"))
    assert client.post("/v1/embeddings", json=BODY).status_code == 502


@respx.mock
def test_backend_4xx_passes_through(monkeypatch):
    _cfg(monkeypatch, {"macbook-m4pro": {"openai_endpoint": "http://legacy.local:1/v1", "enabled": True}})
    respx.post("http://legacy.local:1/v1/embeddings").mock(
        return_value=httpx.Response(400, json={"error": "bad input"}))
    r = client.post("/v1/embeddings", json=BODY)
    assert r.status_code == 400 and r.json() == {"error": "bad input"}


def test_no_backend_is_503_and_bad_json_is_400(monkeypatch):
    _cfg(monkeypatch, {})
    assert client.post("/v1/embeddings", json=BODY).status_code == 503
    _cfg(monkeypatch, {"macbook-m4pro": {"openai_endpoint": "http://legacy.local:1/v1", "enabled": True}})
    assert client.post("/v1/embeddings", content=b"not json",
                       headers={"content-type": "application/json"}).status_code == 400
