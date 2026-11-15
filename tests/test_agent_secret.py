"""Node-agent shared secret resolution."""
from gateway.api_observability import resolve_agent_secret, DEFAULT_AGENT_SECRET


def test_agent_secret_precedence(monkeypatch):
    b = {"agent_url": "http://n:8081", "agent_secret": "from-config"}
    monkeypatch.delenv("AGENT_SECRET", raising=False)
    assert resolve_agent_secret(b) == "from-config"
    monkeypatch.setenv("AGENT_SECRET", "from-env")
    assert resolve_agent_secret(b) == "from-env"           # env wins
    monkeypatch.delenv("AGENT_SECRET")
    assert resolve_agent_secret({"agent_url": "http://n:8081"}) == DEFAULT_AGENT_SECRET
    assert resolve_agent_secret({}) is None                # no agent, no secret
