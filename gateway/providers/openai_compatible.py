import asyncio
import time
import httpx
from typing import List, Optional
from .base import Provider, BackendHealth, ModelInfo, ChatCompletionRequest, ChatCompletionResponse, PASSTHROUGH_PARAMS

HEALTH_TIMEOUT = httpx.Timeout(2.0)
CHAT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
STREAM_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

# One pooled client per event loop, shared by every provider instance so that
# keep-alive connections to the LAN nodes are reused across requests. (httpx
# clients are bound to the loop they were first used on; production has a single
# loop, tests spin up several, hence the per-loop check.) Keep-alive expiry stays
# below typical server idle timeouts (uvicorn: 5s) to avoid reusing dead sockets.
_shared: Optional[tuple] = None  # (loop, client)


def get_client() -> httpx.AsyncClient:
    global _shared
    loop = asyncio.get_running_loop()
    if _shared is None or _shared[0] is not loop or _shared[1].is_closed:
        client = httpx.AsyncClient(limits=httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=4.0))
        _shared = (loop, client)
    return _shared[1]


async def close_client() -> None:
    """Close the pooled client (called from the app's shutdown)."""
    global _shared
    if _shared is not None:
        client, _shared = _shared[1], None
        await client.aclose()


def build_payload(request: ChatCompletionRequest, mapped_model: str, stream: bool,
                  include_usage: bool = False) -> dict:
    """Backend request body: the core fields plus any allow-listed passthrough params.
    ``include_usage`` asks a streaming backend for a final usage chunk (unless the
    client already set stream_options)."""
    payload = {"model": mapped_model, "messages": request.messages, "stream": stream}
    if request.metadata and request.metadata.get("correlation_id"):
        payload["user"] = request.metadata["correlation_id"]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    extra = request.model_extra or {}
    for key in PASSTHROUGH_PARAMS:
        if extra.get(key) is not None:
            payload[key] = extra[key]
    if stream and include_usage and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    return payload


class OpenAICompatibleProvider(Provider):
    """Generic OpenAI-compatible provider. Ollama and LM Studio subclass this and
    only override how health/model discovery is probed."""

    runtime_label = "openai-compatible"

    def __init__(self, backend_id: str, endpoint: str, role: str, stream_usage: bool = False):
        super().__init__(backend_id, endpoint)
        self.role = role
        self.stream_usage = stream_usage  # request a usage chunk on streams (backend must support it)
        # Ensure endpoint doesn't have trailing slash
        self.endpoint = self.endpoint.rstrip("/")

    # -- URLs ---------------------------------------------------------------

    def _v1(self, path: str) -> str:
        """URL under the OpenAI-style /v1 root, whether or not the endpoint includes /v1."""
        base = self.endpoint if self.endpoint.endswith("/v1") else f"{self.endpoint}/v1"
        return f"{base}/{path}"

    def _health_url(self) -> str:
        return self._v1("models")

    def _parse_health_models(self, data: dict) -> List[str]:
        return [m.get("id") for m in data.get("data", []) if m.get("id")]

    # -- health / models ----------------------------------------------------

    async def health(self) -> BackendHealth:
        start_time = time.time()
        try:
            resp = await get_client().get(self._health_url(), timeout=HEALTH_TIMEOUT)
            resp.raise_for_status()
            latency_ms = int((time.time() - start_time) * 1000)
            return BackendHealth(
                id=self.backend_id, role=self.role, runtime=self.runtime_label,
                endpoint=self.endpoint, status="up", latency_ms=latency_ms, error=None,
                models=self._parse_health_models(resp.json()),
            )
        except Exception as e:
            return BackendHealth(
                id=self.backend_id, role=self.role, runtime=self.runtime_label,
                endpoint=self.endpoint, status="down", latency_ms=None, error=str(e),
            )

    async def list_models(self) -> List[ModelInfo]:
        resp = await get_client().get(self._v1("models"), timeout=httpx.Timeout(10.0, connect=5.0))
        resp.raise_for_status()
        return [ModelInfo(id=m.get("id")) for m in resp.json().get("data", [])]

    # -- chat ---------------------------------------------------------------

    def _chat_url(self, request: ChatCompletionRequest) -> str:
        url = self._v1("chat/completions")
        if request.metadata and request.metadata.get("correlation_id"):
            url += f"?trace_id={request.metadata['correlation_id']}"
        return url

    def injects_stream_usage(self, request: ChatCompletionRequest) -> bool:
        """True if we add stream_options.include_usage ourselves (so the resulting
        usage-only chunk should be hidden from a client that didn't ask for it)."""
        return self.stream_usage and "stream_options" not in (request.model_extra or {})

    async def chat_completions_stream(self, request: ChatCompletionRequest, mapped_model: str):
        payload = build_payload(request, mapped_model, True, include_usage=self.stream_usage)
        async with get_client().stream("POST", self._chat_url(request), json=payload,
                                       timeout=STREAM_TIMEOUT) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk

    async def chat_completions(self, request: ChatCompletionRequest, mapped_model: str) -> ChatCompletionResponse:
        payload = build_payload(request, mapped_model, request.stream)
        resp = await get_client().post(self._chat_url(request), json=payload, timeout=CHAT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return ChatCompletionResponse(
            id=data.get("id", "chatcmpl-unknown"),
            created=data.get("created", int(time.time())),
            model=mapped_model,
            choices=data.get("choices", []),
            usage=data.get("usage"),
        )
