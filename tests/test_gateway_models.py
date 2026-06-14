import pytest
from fastapi.testclient import TestClient
from gateway.server import app

client = TestClient(app)

def test_v1_models_returns_aliases():
    response = client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    models = [m["id"] for m in data["data"]]
    assert "local-fast" in models
    assert "local-quality" in models
    assert "private-docs" in models
    assert "cloud-best" not in models
