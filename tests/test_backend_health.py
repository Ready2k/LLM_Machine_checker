import pytest
from fastapi.testclient import TestClient
from gateway.server import app
import httpx
import respx

client = TestClient(app)

FIXTURE_BACKENDS = {"backends": {
    "pc-rtx2070": {"role": "local_fast", "runtime": "ollama", "endpoint": "http://desktop-homepc.local:11434",
                   "enabled": True, "inference_enabled": True},
    "macbook-m4pro": {"role": "local_quality", "runtime": "lmstudio", "endpoint": "http://Jamess-MacBook-Pro-2.local:1234",
                      "enabled": True, "inference_enabled": True},
}}


@respx.mock
def test_backend_health_returns_structured_status(monkeypatch):
    # Independent of the (locally modified) config/backends.yaml.
    import gateway.server as server
    monkeypatch.setattr(server, "load_backends_config", lambda: FIXTURE_BACKENDS)
    # Mock PC is up
    respx.get("http://desktop-homepc.local:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b"}]})
    )
    # Mock Mac is down
    respx.get("http://Jamess-MacBook-Pro-2.local:1234/v1/models").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    response = client.get("/api/backends/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    
    backends = {b["id"]: b for b in data["backends"]}
    
    assert backends["pc-rtx2070"]["status"] == "up"
    assert backends["pc-rtx2070"]["reachable"] is True
    assert backends["pc-rtx2070"]["configured"] is True
    assert backends["pc-rtx2070"]["eligible"] is True
    assert backends["pc-rtx2070"]["runtime"] == "ollama"
    assert "qwen2.5-coder:7b" in backends["pc-rtx2070"]["models"]
    
    assert backends["macbook-m4pro"]["status"] == "down"
    assert backends["macbook-m4pro"]["reachable"] is False
    assert backends["macbook-m4pro"]["eligible"] is False
    assert backends["macbook-m4pro"]["runtime"] == "lmstudio"
    assert "Connection refused" in backends["macbook-m4pro"]["error"]

@respx.mock
def test_old_macbook_health_is_not_eligible_for_inference():
    response = client.get("/api/backends/health")
    assert response.status_code == 200
    data = response.json()
    backends = {b["id"]: b for b in data["backends"]}
    assert backends["old-macbook"]["inference_enabled"] is False
    assert backends["old-macbook"]["eligible"] is False
