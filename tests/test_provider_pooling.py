"""Shared pooled client + the de-duplicated health probes."""
import asyncio

import httpx
import pytest
import respx

from gateway.providers import openai_compatible as oc
from gateway.providers.lmstudio import LMStudioProvider
from gateway.providers.ollama import OllamaProvider
from gateway.providers.openai_compatible import OpenAICompatibleProvider


def test_client_is_shared_within_a_loop_and_rebuilt_across_loops():
    async def two():
        return oc.get_client(), oc.get_client()
    a, b = asyncio.run(two())
    assert a is b                                       # same loop -> same pooled client

    async def one():
        return oc.get_client()
    c = asyncio.run(one())                              # new loop -> fresh client
    assert c is not a


def test_close_client_resets():
    async def go():
        c = oc.get_client()
        await oc.close_client()
        return c, oc.get_client()
    old, new = asyncio.run(go())
    assert old.is_closed and new is not old


@pytest.mark.parametrize("cls,endpoint,url,body,label,models", [
    (OllamaProvider, "http://o:11434", "http://o:11434/api/tags",
     {"models": [{"name": "llama3"}]}, "ollama", ["llama3"]),
    (LMStudioProvider, "http://l:1234", "http://l:1234/v1/models",
     {"data": [{"id": "qwen"}]}, "lmstudio", ["qwen"]),
    (OpenAICompatibleProvider, "http://m:8001", "http://m:8001/v1/models",
     {"data": [{"id": "x"}]}, "openai-compatible", ["x"]),
    (OpenAICompatibleProvider, "http://m:8001/v1", "http://m:8001/v1/models",
     {"data": [{"id": "x"}]}, "openai-compatible", ["x"]),
])
@respx.mock
def test_health_probe_per_runtime(cls, endpoint, url, body, label, models):
    respx.get(url).mock(return_value=httpx.Response(200, json=body))
    h = asyncio.run(cls("b", endpoint, "r").health())
    assert (h.status, h.runtime, h.models) == ("up", label, models)
    assert h.latency_ms is not None and h.error is None


@respx.mock
def test_health_down_reports_error():
    respx.get("http://l:1234/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    h = asyncio.run(LMStudioProvider("b", "http://l:1234", "r").health())
    assert h.status == "down" and "refused" in h.error and h.runtime == "lmstudio"
