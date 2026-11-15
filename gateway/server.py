"""
Local AI Fabric — Gateway Server

FastAPI application serving the admin dashboard and /chat/ UI.
Routes LLM requests across local backends (Ollama, LM Studio).

Bind address: GATEWAY_HOST env var (default 0.0.0.0 — all interfaces).
Port:         GATEWAY_PORT env var (default 8080).
"""

import asyncio
import base64
import copy
import json
import os
from contextlib import asynccontextmanager
import subprocess  # noqa: F401  (tests patch gateway.server.subprocess.run)
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import time
import httpx

from gateway.providers.base import ChatCompletionRequest, Provider
from gateway.providers.ollama import OllamaProvider
from gateway.providers.lmstudio import LMStudioProvider
from gateway.providers.openai_compatible import OpenAICompatibleProvider, close_client
from gateway.providers.sse import UsageTap
from gateway.policy.privacy import extract_privacy_level, is_privacy_allowed, can_fallback_to_cloud, is_web_search_allowed, apply_privacy_floor
from gateway.policy.routing import resolve_model_to_route, select_backend, select_backends
from gateway.policy.autoroute import select_auto_route, classify as classify_auto_route, refine_with_model, wants_tiebreak, tiebreak_settings  # noqa: F401  (select_auto_route kept importable from here)
from gateway.policy.auth import api_key_middleware, auth_status
from gateway.policy.limits import limits_middleware, limits_config, clamp_max_tokens
from gateway.tools.websearch import WebSearchClient, inject_grounding
from gateway.telemetry.events import log_routing_decision, log_llm_event
from gateway.telemetry.database import init_db, close_db, get_db  # noqa: F401  (get_db is patched on this module by tests and read via api_dashboard)
from gateway.api_observability import router as observability_router
from gateway.api_dashboard import (  # noqa: F401  (re-exported: tests and other modules import these from gateway.server)
    router as dashboard_router,
    HealthResponse,
    MachineRouting,
    RuntimeValidation,
    Machine,
    InventorySummary,
    InventoryResponse,
    RouteConfig,
    RoutesResponse,
    ProfileRunResponse,
    ProfileCurrentResponse,
    ErrorResponse,
    load_inventory,
    derive_routes_from_inventory,
    _request_row_to_event,
)
from gateway.chat_helpers import router as chat_helpers_router

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = PROJECT_ROOT / "inventory" / "machines.json"
PROFILER_PATH = PROJECT_ROOT / "profile_machine.py"
STATIC_DIR = Path(__file__).resolve().parent / "static"
CHAT_STATIC_DIR = STATIC_DIR / "chat"
CONFIG_DIR = PROJECT_ROOT / "config"

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Inventory loader
# ---------------------------------------------------------------------------


_CONFIG_CACHE: dict = {}  # path -> ((mtime_ns, size), parsed)


def _read_config_file(path: Path):
    """Parse a YAML/JSON config file, cached until its mtime/size changes so the
    request path doesn't re-read and re-parse on every call. Returns a deep copy
    so callers can't corrupt the cache. Raises on read/parse errors (uncached)."""
    st = path.stat()
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CONFIG_CACHE.get(str(path))
    if hit and hit[0] == sig:
        return copy.deepcopy(hit[1])
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml
        parsed = yaml.safe_load(text)
    else:
        parsed = json.loads(text)
    _CONFIG_CACHE[str(path)] = (sig, parsed)
    return copy.deepcopy(parsed)


def load_routes_config() -> dict:
    """Load route configuration from config files or derive from inventory.

    Checks config/routes.yaml then config/routes.json.
    Falls back to deriving routes from inventory/machines.json.
    """
    for name in ("routes.yaml", "routes.json"):
        path = CONFIG_DIR / name
        if path.exists():
            try:
                data = _read_config_file(path) or {}
                return {"routes": data.get("routes", {}), "source": "config"}
            except Exception:
                pass

    # Derive from inventory
    inventory = load_inventory()
    machines = inventory.get("machines", [])
    routes = derive_routes_from_inventory(machines)
    return {"routes": routes, "source": "derived"}


def _load_yaml_or_json(name: str, default_val: dict) -> dict:
    for ext in [".local.yaml", ".yaml", ".example.yaml", ".local.json", ".json", ".example.json"]:
        path = CONFIG_DIR / f"{name}{ext}"
        if path.exists():
            try:
                return _read_config_file(path) or default_val
            except Exception:
                pass
    return default_val

def load_models_config() -> dict:
    return _load_yaml_or_json("models", {"models": {}})

def load_security_config() -> dict:
    return _load_yaml_or_json("security", {"security": {"default_privacy": "personal"}})

def load_backends_config() -> dict:
    return _load_yaml_or_json("backends", {"backends": {}})

def load_tools_config() -> dict:
    return _load_yaml_or_json("tools", {"tools": {}})

def load_autoroute_config() -> dict:
    """Optional tuning for model:"auto" (config/autoroute.yaml); {} = built-in defaults."""
    return _load_yaml_or_json("autoroute", {})

def resolve_target_model(alias: str, models_config: dict) -> str:
    """Map a model alias to the backend's model id. ``~`` and ``$VARS`` are
    expanded so configs can say ``~/.cache/...`` instead of a user-specific path."""
    entry = models_config.get("models", {}).get(alias)
    if not entry:
        return alias
    model = entry.get("model") or alias
    return os.path.expanduser(os.path.expandvars(model)) if isinstance(model, str) else alias


def get_provider(backend_id: str, backends_config: dict) -> Optional[Provider]:
    b_config = backends_config.get("backends", {}).get(backend_id)
    if not b_config:
        return None
    runtime = b_config.get("runtime")
    endpoint = b_config.get("endpoint")
    if not runtime or not endpoint:
        return None
    runtime = str(runtime).replace("_", "-").lower()  # accept openai_compatible / openai-compatible
    role = b_config.get("role", "unknown")
    # Ask for a usage chunk on streams so telemetry can record token counts. On by
    # default for runtimes known to support it; opt in/out per backend with stream_usage.
    stream_usage = bool(b_config.get("stream_usage", runtime in ("ollama", "lmstudio", "lm-studio")))
    if runtime == "ollama":
        return OllamaProvider(backend_id, endpoint, role=role, stream_usage=stream_usage)
    elif runtime in ("lmstudio", "lm-studio"):
        return LMStudioProvider(backend_id, endpoint, role=role, stream_usage=stream_usage)
    elif runtime == "openai-compatible":
        return OpenAICompatibleProvider(backend_id, endpoint, role=role, stream_usage=stream_usage)
    return None


# ---------------------------------------------------------------------------
# Cached profile for current host
# ---------------------------------------------------------------------------

_cached_profile: Optional[dict] = None


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(PROJECT_ROOT)
    auth_status(load_security_config().get("security", {}), log=True)
    yield
    await close_client()
    await close_db()

app = FastAPI(
    title="Local AI Fabric Dashboard",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.include_router(observability_router)
app.include_router(chat_helpers_router)
app.include_router(dashboard_router)
app.middleware("http")(limits_middleware)
app.middleware("http")(api_key_middleware)  # added last = outermost: runs first

# Mount static files (CSS, JS, images)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=FileResponse)
async def serve_dashboard():
    """Serve the dashboard HTML page."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(str(index_path), media_type="text/html")


@app.get("/chat", include_in_schema=False)
async def redirect_chat():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/chat/")


@app.get("/chat/")
async def serve_chat():
    """Serve the chat UI."""
    chat_index = CHAT_STATIC_DIR / "index.html"
    if not chat_index.exists():
        raise HTTPException(status_code=404, detail="Chat UI not found")
    return FileResponse(str(chat_index), media_type="text/html")


WEB_MODEL_ALIASES = {"web", "auto-web", "web-search"}


def _last_user_text(messages: list[dict]) -> str:
    """The latest user turn as plain text — used as the web-search query.
    Handles both string content and OpenAI structured content parts."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return ""


async def _apply_web_grounding(request: ChatCompletionRequest, privacy_level: str,
                               security_config: dict, correlation_id: str):
    """Search the web for the latest user turn and prepend a grounding system
    message instructing the model to answer only from real, fetched sources.

    Never raises — on any problem it returns the request unchanged plus a status
    string explaining why grounding didn't apply, so the request still completes
    (just ungrounded). Returns (request, sources_meta, status)."""
    if not is_web_search_allowed(privacy_level, security_config):
        return request, [], "blocked_by_privacy"

    client = WebSearchClient(load_tools_config().get("tools", {}).get("web_search", {}))
    if not client.available:
        return request, [], "unavailable"

    query = _last_user_text(request.messages).strip()
    if not query:
        return request, [], "no_query"

    try:
        sources = await client.gather(query)
    except Exception as e:
        log_llm_event(correlation_id, "web.search.error", str(e)[:200])
        return request, [], "error"

    if not sources:
        return request, [], "no_results"

    grounded = request.model_copy(update={
        "messages": inject_grounding(request.messages, sources)
    })
    log_llm_event(correlation_id, "web.search", f"grounded with {len(sources)} sources",
                  {"sources": len(sources)})
    return grounded, [s.to_meta() for s in sources], "grounded"


@app.get("/api/backends/health")
async def get_backends_health():
    backends_config = load_backends_config()
    backends_list = backends_config.get("backends", {})

    async def check(bid: str, bconf: dict) -> dict:
        enabled = bconf.get("enabled", False)
        inference_enabled = bconf.get("inference_enabled", False)

        if bid == "old-macbook":
            inference_enabled = False

        provider = get_provider(bid, backends_config)
        reachable = False
        status_str = "down"
        latency_ms = None
        error_str = None
        models = []

        if provider and enabled:
            try:
                h = await provider.health()
                status_str = h.status
                reachable = (h.status == "up")
                latency_ms = h.latency_ms
                error_str = h.error
                models = h.models
            except Exception as e:
                error_str = str(e)

        eligible = enabled and inference_enabled and reachable

        if bid == "old-macbook":
            eligible = False

        return {
            "id": bid,
            "runtime": bconf.get("runtime", "unknown"),
            "role": bconf.get("role", "unknown"),
            "configured": True,
            "enabled": enabled,
            "inference_enabled": inference_enabled,
            "reachable": reachable,
            "eligible": eligible,
            "endpoint": bconf.get("endpoint", ""),
            "openai_endpoint": bconf.get("openai_endpoint", ""),
            "latency_ms": latency_ms,
            "models": models,
            "error": error_str,
            "status": status_str,
        }

    # Probe all backends concurrently (a down node costs one timeout, not N).
    # gather() preserves config order.
    healths = list(await asyncio.gather(*(check(bid, bconf) for bid, bconf in backends_list.items())))

    status = "ok"
    if any(not h["reachable"] for h in healths if h["enabled"]):
        status = "degraded"
    if all(not h["reachable"] for h in healths if h["enabled"]) and healths:
        status = "down"
    if not healths:
        status = "degraded"
    
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "backends": healths
    }

@app.get("/v1/models")
async def get_v1_models():
    models_config = load_models_config()
    routes_config = load_routes_config().get("routes", {})
    
    enabled_models = []
    for alias, mconf in models_config.get("models", {}).items():
        route = mconf.get("route")
        if route and route in routes_config:
            rconf = routes_config[route]
            if rconf.get("enabled") is False or rconf.get("status") == "not_configured":
                continue
            enabled_models.append({
                "id": alias,
                "object": "model",
                "owned_by": "local-ai-fabric"
            })
            
    return {"object": "list", "data": enabled_models}

def _pick_embeddings_backend(backends_config: dict) -> Optional[dict]:
    """Backend to serve embeddings: the first enabled one flagged ``embeddings: true``,
    else the legacy ``macbook-m4pro`` entry (kept for existing configs)."""
    backends = backends_config.get("backends", {})
    for b in backends.values():
        if b.get("embeddings") and b.get("enabled", False):
            return b
    return backends.get("macbook-m4pro")


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """Pass-through embeddings endpoint. Local backends only, so no privacy gate is
    needed. Set ``embeddings: true`` on a backend in backends.yaml to choose it."""
    backend = _pick_embeddings_backend(load_backends_config()) or {}
    base = (backend.get("openai_endpoint") or "").rstrip("/")
    if not base and backend.get("endpoint"):
        base = backend["endpoint"].rstrip("/") + "/v1"
    if not base:
        raise HTTPException(status_code=503, detail="No embeddings backend configured")

    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.post(f"{base}/embeddings", json=body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail={
            "message": f"Embeddings backend unreachable: {type(e).__name__}: {e}",
            "type": "backend_error", "code": "bad_gateway"})
    if resp.status_code >= 500:
        raise HTTPException(status_code=502, detail={
            "message": f"Embeddings backend error ({resp.status_code})",
            "type": "backend_error", "code": "bad_gateway"})
    try:
        content = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Embeddings backend returned non-JSON")
    return JSONResponse(content=content, status_code=resp.status_code)


async def _autoroute_tiebreak(decision, messages, privacy_level, autoroute_config,
                              models_config, routes_config, backends_config):
    """Ask the configured small local model whether a borderline request is
    SIMPLE or COMPLEX. Only runs on routes this privacy tier may use."""
    tb = tiebreak_settings(autoroute_config)
    alias = tb.get("model", "local-fast")

    async def ask(judge_messages: list) -> str:
        _, route_config = resolve_model_to_route(alias, models_config, routes_config)
        if not is_privacy_allowed(privacy_level, route_config):
            raise PermissionError("tiebreak route not allowed for this privacy tier")
        backend_id = select_backends(route_config, backends_config)[0]
        provider = get_provider(backend_id, backends_config)
        if not provider:
            raise RuntimeError("no provider for tiebreak")
        judge_request = ChatCompletionRequest(model=alias, messages=judge_messages,
                                              max_tokens=4, temperature=0, stream=False)
        response = await asyncio.wait_for(
            provider.chat_completions(judge_request, resolve_target_model(alias, models_config)),
            timeout=float(tb.get("timeout_s", 1.5)),
        )
        return (response.choices[0].get("message") or {}).get("content") or ""

    return await refine_with_model(decision, messages, ask, autoroute_config)


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw_request: Request,
                           x_ai_privacy: Optional[str] = Header(None),
                           x_ai_web_search: Optional[str] = Header(None)):
    correlation_id = str(uuid.uuid4())
    if not request.metadata:
        request.metadata = {}
    request.metadata["correlation_id"] = correlation_id
    log_llm_event(correlation_id, "request.received", f"Received chat completion request for {request.model}")
    pass  # streaming handled below

    models_config = load_models_config()
    routes_data = load_routes_config()
    routes_config = routes_data.get("routes", {})
    security_config = load_security_config().get("security", {})
    backends_config = load_backends_config()

    # 0. Optional cap on generated tokens (security.limits.max_tokens_cap)
    capped = clamp_max_tokens(request.max_tokens, limits_config(security_config)["max_tokens_cap"])
    if capped != request.max_tokens:
        request = request.model_copy(update={"max_tokens": capped})

    # 1. Privacy
    privacy_level = extract_privacy_level(request.metadata, x_ai_privacy, security_config.get("default_privacy", "personal"))
    # A key's privacy floor (GATEWAY_API_KEYS=key:tier) can raise the declared tier, never lower it.
    privacy_level = apply_privacy_floor(privacy_level, getattr(raw_request.state, "privacy_floor", None))

    # 1b. Web grounding requested? Via model alias, header, or metadata. The
    #     alias is rewritten to "auto" so route selection runs on the clean
    #     prompt; the actual search/inject happens after auto-routing below.
    web_requested = (
        request.model in WEB_MODEL_ALIASES
        or str(x_ai_web_search or "").lower() in ("1", "true", "yes", "on")
        or bool(request.metadata.get("web_search"))
    )
    if request.model in WEB_MODEL_ALIASES:
        request = request.model_copy(update={"model": "auto"})

    # 2. Auto-routing: inspect prompt and pick best route automatically
    auto_reason = None
    if request.model == "auto":
        extra = request.model_extra or {}
        autoroute_config = load_autoroute_config()
        decision = classify_auto_route(
            request.messages, privacy_level,
            tools=extra.get("tools"), response_format=extra.get("response_format"),
            config=autoroute_config,
        )
        # Optional (off by default): a close call gets a one-word verdict from the
        # fast local model. Best-effort — any failure keeps the rule-based answer.
        if wants_tiebreak(decision, autoroute_config):
            decision = await _autoroute_tiebreak(
                decision, request.messages, privacy_level, autoroute_config,
                models_config, routes_config, backends_config,
            )
        auto_reason = decision.reason
        # The trace holds signal names and counts only — no prompt content.
        log_llm_event(correlation_id, "router.auto", f"auto -> {decision.route}: {decision.reason}", decision.to_dict())
        request = request.model_copy(update={"model": decision.route})

    # 2b. Web grounding: search + fetch, then prepend a grounding system message.
    #     Done after route selection so the injected sources don't skew the
    #     prompt classifier. Degrades gracefully (status header) on any failure.
    grounding_sources: list[dict] = []
    grounding_status: Optional[str] = None
    if web_requested:
        request, grounding_sources, grounding_status = await _apply_web_grounding(
            request, privacy_level, security_config, correlation_id
        )

    # 3. Resolve Model -> Route
    route_name, route_config = resolve_model_to_route(request.model, models_config, routes_config)

    # 3. Apply Privacy Policy
    if not is_privacy_allowed(privacy_level, route_config):
        log_routing_decision(route_name, request.model, privacy_level, "none", False, "blocked", "privacy_policy_violation", correlation_id=correlation_id)
        raise HTTPException(status_code=403, detail={"message": f"Privacy level {privacy_level} not allowed for route {route_name}", "type": "privacy_blocked", "code": "policy_violation"})

    # 4. Select backends (primary first, then fallback) — retried at runtime below
    try:
        candidates = select_backends(route_config, backends_config)
    except HTTPException as e:
        log_routing_decision(route_name, request.model, privacy_level, "none", False, "error", "no_backends_available", correlation_id=correlation_id)
        raise e

    primary_id = route_config.get("primary")

    target_model_name = resolve_target_model(request.model, models_config)

    def build_headers(backend_id: str) -> dict:
        h = {
            "x-correlation-id": correlation_id,
            "x-ai-route": route_name,
            "x-ai-backend": backend_id,
            "x-ai-model": target_model_name,
            "x-ai-fallback-used": str(backend_id != primary_id).lower(),
            "x-ai-policy": privacy_level,
        }
        if auto_reason:
            h["x-ai-auto-reason"] = auto_reason
        # Web grounding result for the client (sources ride a header so streaming is
        # unaffected; the value is base64(JSON) to stay ASCII-safe for any title/URL).
        if web_requested:
            h["x-ai-grounding"] = grounding_status or "unknown"
            if grounding_sources:
                h["x-ai-grounded"] = "true"
                h["x-ai-sources"] = base64.b64encode(
                    json.dumps(grounding_sources).encode("utf-8")
                ).decode("ascii")
        return h

    # 5. Forward — try each candidate; fall through on connection errors,
    #    timeouts and 5xx. 4xx (bad request) is the caller's fault: don't retry.
    last_error: Optional[Exception] = None
    for idx, backend_id in enumerate(candidates):
        fallback_used = backend_id != primary_id
        provider = get_provider(backend_id, backends_config)
        if not provider:
            log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", "provider_not_found", correlation_id=correlation_id)
            last_error = RuntimeError(f"Provider adapter not found for backend {backend_id}")
            continue
        start_time = time.time()

        if request.stream:
            tap = UsageTap(drop_usage_only=bool(getattr(provider, "injects_stream_usage", lambda r: False)(request)))
            raw = provider.chat_completions_stream(request, target_model_name)

            async def tapped(raw=raw, tap=tap):
                async for chunk in raw:
                    for out in tap.feed(chunk):
                        yield out
                for out in tap.flush():
                    yield out

            gen = tapped()
            try:
                # Pull the first chunk *before* committing to the response so a
                # dead backend can still fail over to the next candidate.
                first_chunk = await gen.__anext__()
            except StopAsyncIteration:
                first_chunk = None
            except Exception as e:
                last_error = e
                log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", str(e), latency_ms=int((time.time() - start_time) * 1000), stream=True, correlation_id=correlation_id)
                if not _is_retryable(e):
                    break
                continue

            async def stream_wrapper(gen=gen, first_chunk=first_chunk, backend_id=backend_id,
                                     fallback_used=fallback_used, start_time=start_time, tap=tap):
                ttft = int((time.time() - start_time) * 1000)
                log_llm_event(correlation_id, "provider.stream.first_token", f"First token received in {ttft}ms")
                try:
                    if first_chunk is not None:
                        yield first_chunk
                    async for chunk in gen:
                        yield chunk
                    latency_ms = int((time.time() - start_time) * 1000)
                    tin, tout = tap.tokens
                    log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "success", error=auto_reason, latency_ms=latency_ms, ttft_ms=ttft, stream=True, correlation_id=correlation_id,
                                         tokens_in=tin, tokens_out=tout)
                    log_llm_event(correlation_id, "request.complete", "Stream completed successfully", {"latency_ms": latency_ms})
                except Exception as e:
                    latency_ms = int((time.time() - start_time) * 1000)
                    log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", str(e), latency_ms=latency_ms, ttft_ms=ttft, stream=True, correlation_id=correlation_id)
                    raise

            return StreamingResponse(stream_wrapper(), media_type="text/event-stream", headers=build_headers(backend_id))

        log_llm_event(correlation_id, "router.selected", f"Selected backend {backend_id}", {"backend": backend_id})
        try:
            response = await provider.chat_completions(request, target_model_name)
        except Exception as e:
            last_error = e
            log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", str(e), latency_ms=int((time.time() - start_time) * 1000), correlation_id=correlation_id)
            if not _is_retryable(e):
                break
            continue

        latency_ms = int((time.time() - start_time) * 1000)
        usage = response.usage or {}
        log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "success", error=auto_reason, latency_ms=latency_ms, correlation_id=correlation_id,
                             tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"))
        log_llm_event(correlation_id, "request.complete", "Request completed successfully", {"latency_ms": latency_ms})
        return JSONResponse(content=response.model_dump(), headers=build_headers(backend_id))

    if isinstance(last_error, RuntimeError) and "Provider adapter not found" in str(last_error):
        raise HTTPException(status_code=500, detail={"message": str(last_error), "type": "config_error", "code": "internal_error"})
    raise HTTPException(status_code=502, detail={"message": f"All backends failed (tried: {', '.join(candidates)}). Last error: {type(last_error).__name__}: {last_error}", "type": "backend_error", "code": "bad_gateway"})

def _is_retryable(exc: Exception) -> bool:
    """Connection problems, timeouts and upstream 5xx justify trying the fallback."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, OSError))

class RouteTestRequest(BaseModel):
    route: str
    privacy: str = "personal"
    prompt: str = "Reply with exactly: ok"

@app.post("/api/routes/test")
async def route_test(request: RouteTestRequest):
    models_config = load_models_config()
    routes_data = load_routes_config()
    routes_config = routes_data.get("routes", {})
    backends_config = load_backends_config()

    route_name = request.route
    if route_name not in routes_config:
        raise HTTPException(status_code=404, detail={"message": f"Route {route_name} not found", "type": "route_not_found", "code": "not_found"})
    
    route_config = routes_config[route_name]

    if route_name == "cloud-best" and not route_config.get("enabled", False):
         raise HTTPException(status_code=400, detail={"message": "cloud-best is disabled", "type": "route_not_configured", "code": "route_disabled"})

    if not is_privacy_allowed(request.privacy, route_config):
        raise HTTPException(status_code=403, detail={"message": f"Privacy level {request.privacy} not allowed for route {route_name}", "type": "privacy_blocked", "code": "policy_violation"})

    try:
        backend_id = select_backend(route_config, backends_config)
    except HTTPException as e:
        raise e

    if backend_id == "old-macbook":
        raise HTTPException(status_code=503, detail={"message": "old-macbook is not eligible for inference", "type": "no_backends_available", "code": "service_unavailable"})

    primary_id = route_config.get("primary")
    fallback_used = backend_id != primary_id

    provider = get_provider(backend_id, backends_config)
    if not provider:
        raise HTTPException(status_code=500, detail={"message": f"Provider adapter not found for backend {backend_id}", "type": "config_error", "code": "internal_error"})

    target_model_name = resolve_target_model(request.route, models_config)
        
    chat_req = ChatCompletionRequest(
        model=target_model_name,
        messages=[{"role": "user", "content": request.prompt}],
        stream=False,
        max_tokens=32
    )

    import time
    import asyncio
    start_time = time.time()
    try:
        response = await asyncio.wait_for(provider.chat_completions(chat_req, target_model_name), timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail={"message": f"Backend {backend_id} timed out", "type": "timeout", "code": "gateway_timeout"})
    except Exception as e:
        raise HTTPException(status_code=502, detail={"message": f"Backend {backend_id} failed: {str(e)}", "type": "backend_error", "code": "bad_gateway"})
        
    latency_ms = int((time.time() - start_time) * 1000)
    preview = ""
    if response.choices and len(response.choices) > 0:
        preview = response.choices[0].get("message", {}).get("content", "")

    return {
        "route": route_name,
        "backend": backend_id,
        "model": target_model_name,
        "fallback_used": fallback_used,
        "latency_ms": latency_ms,
        "status": "ok",
        "response_preview": preview[:100]
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    _host = os.environ.get("GATEWAY_HOST", "0.0.0.0")
    _port = int(os.environ.get("GATEWAY_PORT", "8080"))

    # Optional TLS: set both GATEWAY_SSL_CERT and GATEWAY_SSL_KEY to serve HTTPS.
    # HTTPS is required for browser voice/mic (secure context) on the LAN.
    _ssl_cert = os.environ.get("GATEWAY_SSL_CERT") or None
    _ssl_key = os.environ.get("GATEWAY_SSL_KEY") or None

    _kwargs = {"host": _host, "port": _port, "reload": False}
    if _ssl_cert and _ssl_key:
        _kwargs["ssl_certfile"] = _ssl_cert
        _kwargs["ssl_keyfile"] = _ssl_key

    uvicorn.run("gateway.server:app", **_kwargs)
