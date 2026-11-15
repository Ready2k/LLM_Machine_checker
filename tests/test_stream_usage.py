"""Token usage from streamed responses is recorded and (when we injected the
request for it) hidden from clients that didn't ask."""
import json

import httpx
import respx
from fastapi.testclient import TestClient

from gateway.providers.base import ChatCompletionRequest
from gateway.providers.openai_compatible import OpenAICompatibleProvider, build_payload
from gateway.providers.sse import UsageTap
from gateway.server import app
from gateway.telemetry import events

client = TestClient(app)
OLLAMA_URL = "http://desktop-homepc.local:11434/v1/chat/completions"

SSE = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
       b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4}}\n\n'
       b'data: [DONE]\n\n')


def test_tap_records_usage_across_chunk_boundaries_and_passes_events_through():
    tap = UsageTap()
    out = []
    for i in range(0, len(SSE), 7):                       # awkward 7-byte chunks
        out += list(tap.feed(SSE[i:i + 7]))
    out += list(tap.flush())
    assert b"".join(out) == SSE                           # unchanged when not dropping
    assert tap.tokens == (9, 4)


def test_tap_drops_only_the_usage_only_event():
    tap = UsageTap(drop_usage_only=True)
    out = b"".join(list(tap.feed(SSE)) + list(tap.flush()))
    assert b'"usage"' not in out and b'"content":"hi"' in out and out.endswith(b"[DONE]\n\n")
    assert tap.tokens == (9, 4)


def test_tap_keeps_usage_that_rides_a_content_chunk():
    ev = b'data: {"choices":[{"delta":{"content":"x"}}],"usage":{"prompt_tokens":1,"completion_tokens":2}}\r\n\r\n'
    tap = UsageTap(drop_usage_only=True)
    assert b"".join(tap.feed(ev)) == ev and tap.tokens == (1, 2)


def test_build_payload_injects_include_usage_only_when_asked_and_unset():
    req = ChatCompletionRequest(model="m", messages=[])
    assert build_payload(req, "m", True, include_usage=True)["stream_options"] == {"include_usage": True}
    assert "stream_options" not in build_payload(req, "m", True)
    assert "stream_options" not in build_payload(req, "m", False, include_usage=True)
    own = ChatCompletionRequest(model="m", messages=[], stream_options={"include_usage": False})
    assert build_payload(own, "m", True, include_usage=True)["stream_options"] == {"include_usage": False}
    assert OpenAICompatibleProvider("b", "http://h", "r", stream_usage=True).injects_stream_usage(req)
    assert not OpenAICompatibleProvider("b", "http://h", "r", stream_usage=True).injects_stream_usage(own)


def _capture(monkeypatch):
    rows = []
    monkeypatch.setattr(events, "_load_telemetry_config", lambda: {"enabled": True})
    monkeypatch.setattr(events, "queue_insert", lambda t, r: rows.append((t, r)))
    return rows


@respx.mock
def test_stream_end_to_end_records_tokens_and_hides_injected_usage(monkeypatch):
    rows = _capture(monkeypatch)
    backend = respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, content=SSE))
    r = client.post("/v1/chat/completions", json={
        "model": "local-fast", "stream": True, "messages": [{"role": "user", "content": "Hi"}]})
    assert r.status_code == 200
    assert b'"usage"' not in r.content and b'"content":"hi"' in r.content and b"[DONE]" in r.content
    assert json.loads(backend.calls.last.request.content)["stream_options"] == {"include_usage": True}
    req = [row for t, row in rows if t == "llm_requests"][-1]
    assert (req["tokens_in"], req["tokens_out"], req["is_stream"]) == (9, 4, True)


@respx.mock
def test_client_that_requests_usage_still_receives_it(monkeypatch):
    rows = _capture(monkeypatch)
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, content=SSE))
    r = client.post("/v1/chat/completions", json={
        "model": "local-fast", "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "Hi"}]})
    assert b'"usage"' in r.content
    assert [row for t, row in rows if t == "llm_requests"][-1]["tokens_out"] == 4
