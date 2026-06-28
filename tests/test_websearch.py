"""Tests for web-search grounding (gateway/tools/websearch.py + /v1 integration).

All hermetic: httpx is mocked with respx and trafilatura's extractor is stubbed,
so these run offline and don't depend on a real SearXNG node.
"""
import asyncio
import base64
import json
import types

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import gateway.tools.websearch as ws
from gateway.tools.websearch import Source, WebSearchClient, build_grounding_block
from gateway.policy.privacy import is_web_search_allowed
from gateway.server import app

client = TestClient(app)

OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"
LMSTUDIO_URL = "http://jamess-macbook-pro-2.local:1234/v1/chat/completions"
SEARX = r"http://searxng\.lan:8080/search.*"

PAGE_ONE = "http://example.com/trams"
PAGE_TWO = "http://example.com/empty"


def _stub_trafilatura(monkeypatch):
    """Deterministic extractor: echoes page body, returns '' for pages we want
    treated as un-extractable (so we can test that empties are dropped)."""
    def extract(html, **kw):
        return "" if "DROP_ME" in html else html
    monkeypatch.setattr(ws, "trafilatura", types.SimpleNamespace(extract=extract))


def _searx_response(*urls):
    return httpx.Response(200, json={"results": [{"url": u, "title": f"Title {u}"} for u in urls]})


def _completion(content="ok"):
    return httpx.Response(200, json={
        "id": "chatcmpl-x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    })


# --------------------------------------------------------------------------- #
# Pure-unit pieces
# --------------------------------------------------------------------------- #

def test_is_web_search_allowed_gate():
    cfg = {"web_search": {"allowed_privacy": ["public", "personal"]}}
    assert is_web_search_allowed("personal", cfg)
    assert is_web_search_allowed("public", cfg)
    assert not is_web_search_allowed("confidential", cfg)
    assert not is_web_search_allowed("restricted", cfg)


def test_is_web_search_allowed_default_when_unconfigured():
    assert is_web_search_allowed("personal", {})
    assert not is_web_search_allowed("confidential", {})


def test_build_grounding_block_has_rules_numbers_and_urls():
    block = build_grounding_block([
        Source(n=1, title="First", url="http://a.test", text="alpha body"),
        Source(n=2, title="Second", url="http://b.test", text="beta body"),
    ])
    assert "ONLY the numbered web sources" in block
    assert "Never invent" in block
    assert "[1] First" in block and "[2] Second" in block
    assert "http://a.test" in block and "beta body" in block


def test_client_available_requires_enabled_url_and_dep(monkeypatch):
    _stub_trafilatura(monkeypatch)
    assert WebSearchClient({"enabled": True, "searxng_url": "http://x:8080"}).available
    assert not WebSearchClient({"enabled": False, "searxng_url": "http://x:8080"}).available
    assert not WebSearchClient({"enabled": True, "searxng_url": ""}).available
    monkeypatch.setattr(ws, "trafilatura", None)
    assert not WebSearchClient({"enabled": True, "searxng_url": "http://x:8080"}).available


def test_searxng_url_env_override(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://env-host:9999/")
    c = WebSearchClient({"enabled": True, "searxng_url": "http://config-host:8080"})
    assert c.searxng_url == "http://env-host:9999"  # env wins, trailing slash stripped


@respx.mock
def test_gather_fetches_extracts_and_drops_empty(monkeypatch):
    _stub_trafilatura(monkeypatch)
    respx.get(url__regex=SEARX).mock(return_value=_searx_response(PAGE_ONE, PAGE_TWO))
    respx.get(PAGE_ONE).mock(return_value=httpx.Response(200, text="<html>NO_TRAMS_HERE</html>"))
    respx.get(PAGE_TWO).mock(return_value=httpx.Response(200, text="<html>DROP_ME</html>"))

    c = WebSearchClient({"enabled": True, "searxng_url": "http://searxng.lan:8080", "n_results": 5})
    sources = asyncio.run(c.gather("can i get a tram"))

    assert len(sources) == 1                     # the DROP_ME page is discarded
    assert sources[0].n == 1                      # survivors are renumbered
    assert sources[0].url == PAGE_ONE
    assert "NO_TRAMS_HERE" in sources[0].text


@respx.mock
def test_gather_truncates_per_source(monkeypatch):
    _stub_trafilatura(monkeypatch)
    respx.get(url__regex=SEARX).mock(return_value=_searx_response(PAGE_ONE))
    respx.get(PAGE_ONE).mock(return_value=httpx.Response(200, text="X" * 10000))

    c = WebSearchClient({"enabled": True, "searxng_url": "http://searxng.lan:8080",
                         "max_chars_per_source": 100})
    sources = asyncio.run(c.gather("q"))
    assert len(sources[0].text) == 100


# --------------------------------------------------------------------------- #
# /v1/chat/completions integration
# --------------------------------------------------------------------------- #

@respx.mock
def test_web_grounding_injects_sources_via_header(monkeypatch):
    _stub_trafilatura(monkeypatch)
    respx.get(url__regex=SEARX).mock(return_value=_searx_response(PAGE_ONE, PAGE_TWO))
    respx.get(PAGE_ONE).mock(return_value=httpx.Response(200, text="<html>FACT_NO_TRAMS</html>"))
    respx.get(PAGE_TWO).mock(return_value=httpx.Response(200, text="<html>SECOND_PAGE</html>"))
    backend = respx.post(OLLAMA_URL).mock(return_value=_completion())

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local-fast", "messages": [{"role": "user", "content": "tram to Yarnfield?"}],
              "stream": False},
        headers={"x-ai-web-search": "true"},
    )

    assert resp.status_code == 200
    assert resp.headers["x-ai-grounding"] == "grounded"
    assert resp.headers["x-ai-grounded"] == "true"
    sources = json.loads(base64.b64decode(resp.headers["x-ai-sources"]))
    assert [s["url"] for s in sources] == [PAGE_ONE, PAGE_TWO]

    # The grounding system message actually reached the backend.
    forwarded = json.loads(backend.calls.last.request.content)
    assert forwarded["messages"][0]["role"] == "system"
    assert "Web sources" in forwarded["messages"][0]["content"]
    assert "FACT_NO_TRAMS" in forwarded["messages"][0]["content"]


@respx.mock
def test_web_alias_triggers_grounding(monkeypatch):
    _stub_trafilatura(monkeypatch)
    respx.get(url__regex=SEARX).mock(return_value=_searx_response(PAGE_ONE))
    respx.get(PAGE_ONE).mock(return_value=httpx.Response(200, text="<html>BODY</html>"))
    # auto-routing may pick either local backend — mock both.
    respx.post(OLLAMA_URL).mock(return_value=_completion())
    respx.post(LMSTUDIO_URL).mock(return_value=_completion())

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "web", "messages": [{"role": "user", "content": "research trams"}],
              "stream": False},
    )
    assert resp.status_code == 200
    assert resp.headers["x-ai-grounding"] == "grounded"
    assert "x-ai-sources" in resp.headers


@respx.mock
def test_web_grounding_blocked_by_privacy_still_completes(monkeypatch):
    _stub_trafilatura(monkeypatch)
    searx = respx.get(url__regex=SEARX).mock(return_value=_searx_response(PAGE_ONE))
    backend = respx.post(OLLAMA_URL).mock(return_value=_completion())

    # 'confidential' is allowed by the local-fast route but NOT by web_search.
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local-fast", "messages": [{"role": "user", "content": "hi"}],
              "stream": False},
        headers={"x-ai-web-search": "true", "x-ai-privacy": "confidential"},
    )

    assert resp.status_code == 200
    assert resp.headers["x-ai-grounding"] == "blocked_by_privacy"
    assert "x-ai-sources" not in resp.headers
    assert not searx.called                       # never even searched
    forwarded = json.loads(backend.calls.last.request.content)
    assert forwarded["messages"][0]["role"] == "user"   # no grounding injected


@respx.mock
def test_web_grounding_no_results_degrades(monkeypatch):
    _stub_trafilatura(monkeypatch)
    respx.get(url__regex=SEARX).mock(return_value=_searx_response())   # empty results
    backend = respx.post(OLLAMA_URL).mock(return_value=_completion())

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local-fast", "messages": [{"role": "user", "content": "hi"}],
              "stream": False},
        headers={"x-ai-web-search": "true"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-ai-grounding"] == "no_results"
    assert "x-ai-sources" not in resp.headers
    assert backend.called


@respx.mock
def test_web_grounding_unavailable_when_disabled(monkeypatch):
    import gateway.server as server
    monkeypatch.setattr(server, "load_tools_config",
                        lambda: {"tools": {"web_search": {"enabled": False}}})
    backend = respx.post(OLLAMA_URL).mock(return_value=_completion())

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local-fast", "messages": [{"role": "user", "content": "hi"}],
              "stream": False},
        headers={"x-ai-web-search": "true"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-ai-grounding"] == "unavailable"
    assert backend.called
