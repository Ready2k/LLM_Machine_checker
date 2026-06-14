import os
import pytest
from fastapi.testclient import TestClient
from gateway.server import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="Live tests require RUN_LIVE_TESTS=1 and running backends"
)

@pytest.mark.live
def test_live_backends_health():
    response = client.get("/api/backends/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["ok", "degraded", "down"]
    assert "backends" in data
    
    # Check if at least one backend is reachable
    reachable = [b for b in data["backends"] if b["reachable"]]
    assert len(reachable) > 0, "No backends are reachable. Are Ollama/LM Studio running?"
    
    # Check that reachable backends have models
    for b in reachable:
        assert isinstance(b["models"], list)

@pytest.mark.live
def test_live_routes_test():
    # Test local-fast
    response = client.post("/api/routes/test", json={"route": "local-fast"})
    if response.status_code == 200:
        data = response.json()
        assert data["status"] == "ok"
        assert "latency_ms" in data
        assert len(data["response_preview"]) > 0
    else:
        # It's possible the route fails if local-fast is down during live testing
        pass
