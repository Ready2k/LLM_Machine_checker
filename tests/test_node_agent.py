"""Node agent auth: constant-time token check and default-secret guard."""
import pytest
from fastapi.testclient import TestClient

from node_agent import agent

client = TestClient(agent.app)


def test_health_requires_valid_bearer_token(monkeypatch):
    monkeypatch.setattr(agent, "SHARED_SECRET", "s3cret")
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Basic x"}).status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_logs_source_pattern_rejects_unknown(monkeypatch):
    monkeypatch.setattr(agent, "SHARED_SECRET", "s3cret")
    r = client.get("/logs?source=evil", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 422


def test_default_secret_warns_or_refuses(monkeypatch, caplog):
    monkeypatch.setattr(agent, "SHARED_SECRET", agent.DEFAULT_SECRET)
    monkeypatch.delenv("AGENT_STRICT", raising=False)
    with caplog.at_level("WARNING", logger="node_agent"):
        agent.check_secret_configured()
    assert "insecure" in caplog.text
    monkeypatch.setenv("AGENT_STRICT", "1")
    with pytest.raises(SystemExit):
        agent.check_secret_configured()
    monkeypatch.setattr(agent, "SHARED_SECRET", "real")
    agent.check_secret_configured()  # configured secret: no error
