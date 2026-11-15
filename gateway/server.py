"""
Local AI Fabric — Gateway Server

FastAPI application serving the admin dashboard and /chat/ UI.
Routes LLM requests across local backends (Ollama, LM Studio).

Bind address: GATEWAY_HOST env var (default 0.0.0.0 — all interfaces).
Port:         GATEWAY_PORT env var (default 8080).
"""

import base64
import copy
import json
import os
from contextlib import asynccontextmanager
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uuid
import time
import httpx

from gateway.providers.base import BackendHealth, ModelInfo, ChatCompletionRequest, ChatCompletionResponse, Provider
from gateway.providers.ollama import OllamaProvider
from gateway.providers.lmstudio import LMStudioProvider
from gateway.providers.openai_compatible import OpenAICompatibleProvider
from gateway.policy.privacy import extract_privacy_level, is_privacy_allowed, can_fallback_to_cloud, is_web_search_allowed
from gateway.policy.routing import resolve_model_to_route, select_backend, select_backends
from gateway.policy.autoroute import select_auto_route
from gateway.policy.auth import api_key_middleware, auth_status
from gateway.tools.websearch import WebSearchClient, inject_grounding
from gateway.telemetry.events import log_routing_decision, log_llm_event, _load_telemetry_config
from gateway.telemetry.database import init_db, close_db
from gateway.api_observability import router as observability_router
from gateway.chat_helpers import (
    router as chat_helpers_router,
    ChatMessagesRequest, _format_transcript, _parse_facts, _run_utility_completion,
)

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


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "local-ai-fabric-dashboard"
    version: str = "0.1.0"


class MachineRouting(BaseModel):
    enabled: bool = False
    preferred_routes: list[str] = Field(default_factory=list)
    fallback_routes: list[str] = Field(default_factory=list)


class RuntimeValidation(BaseModel):
    torch_version: Optional[str] = None
    cuda_available: Optional[bool] = None
    cuda_device: Optional[str] = None


class Machine(BaseModel):
    id: str
    hostname: str
    os: str
    arch: str = "unknown"
    cpu: str = "Unknown"
    ram_gb: Optional[float] = None
    gpu_name: Optional[str] = None
    vram_gb: Optional[float] = None
    compatible: bool = False
    recommended_role: str = "utility_node"
    recommended_model: str = ""
    measured_tokens_per_second: Optional[float] = None
    risk: str = "none"
    routing: MachineRouting = Field(default_factory=MachineRouting)
    runtime_validation: Optional[RuntimeValidation] = None
    notes: list[str] = Field(default_factory=list)


class InventorySummary(BaseModel):
    recommended_architecture: str = ""
    primary_quality_node: str = ""
    primary_fast_node: str = ""
    utility_node: str = ""
    cloud_policy: str = ""


class InventoryResponse(BaseModel):
    updated_at: str = ""
    summary: InventorySummary = Field(default_factory=InventorySummary)
    machines: list[Machine] = Field(default_factory=list)


class RouteConfig(BaseModel):
    primary: Optional[str] = None
    fallback: Optional[str] = None
    cloud_fallback: bool = False


class RoutesResponse(BaseModel):
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    source: str = "derived"


class ProfileRunResponse(BaseModel):
    status: str
    profile: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    stderr: Optional[str] = None


class ProfileCurrentResponse(BaseModel):
    status: str
    profile: Optional[dict[str, Any]] = None


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Inventory loader
# ---------------------------------------------------------------------------


def load_inventory() -> dict:
    """Load machine inventory from JSON file.

    Returns the parsed inventory dict or raises HTTPException if unavailable.
    """
    if not INVENTORY_PATH.exists():
        try:
            display_path = str(INVENTORY_PATH.relative_to(PROJECT_ROOT))
        except ValueError:
            display_path = str(INVENTORY_PATH)
        raise HTTPException(
            status_code=503,
            detail=f"Inventory file not found: {display_path}",
        )

    try:
        data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to read inventory: {exc}",
        )

    return data


def derive_routes_from_inventory(machines: list[dict]) -> dict:
    """Build route configuration from machine inventory data.

    When no config/routes.yaml or config/routes.json exists, derive
    sensible routes from the machine roles and routing preferences.
    """
    routes: dict[str, dict] = {
        "local-fast": {"primary": None, "fallback": None, "cloud_fallback": False},
        "local-quality": {"primary": None, "fallback": None, "cloud_fallback": False},
        "private-docs": {"primary": None, "fallback": None, "cloud_fallback": False},
        "cloud-best": {"primary": None, "fallback": None, "cloud_fallback": True, "enabled": False, "status": "not_configured"},
    }

    for machine in machines:
        mid = machine.get("id", "")
        role = machine.get("recommended_role", "")
        routing = machine.get("routing", {})
        enabled = routing.get("enabled", False)

        if not enabled:
            continue

        preferred = routing.get("preferred_routes", [])
        fallback = routing.get("fallback_routes", [])

        for route_name in preferred:
            if route_name in routes and routes[route_name]["primary"] is None:
                routes[route_name]["primary"] = mid

        for route_name in fallback:
            if route_name in routes and routes[route_name]["fallback"] is None:
                routes[route_name]["fallback"] = mid

    return routes


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
    if runtime == "ollama":
        return OllamaProvider(backend_id, endpoint, role=b_config.get("role", "unknown"))
    elif runtime in ("lmstudio", "lm-studio"):
        return LMStudioProvider(backend_id, endpoint, role=b_config.get("role", "unknown"))
    elif runtime == "openai-compatible":
        return OpenAICompatibleProvider(backend_id, endpoint, role=b_config.get("role", "unknown"))
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
app.middleware("http")(api_key_middleware)

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


@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse()


@app.get("/api/machines", response_model=InventoryResponse)
async def get_machines():
    """Return the full machine inventory."""
    data = load_inventory()
    return InventoryResponse(**data)


@app.get("/api/machines/{machine_id}", response_model=Machine)
async def get_machine(machine_id: str):
    """Return a single machine by ID."""
    data = load_inventory()
    machines = data.get("machines", [])

    for machine in machines:
        if machine.get("id") == machine_id:
            return Machine(**machine)

    raise HTTPException(status_code=404, detail=f"Machine not found: {machine_id}")


@app.get("/api/config/routes", response_model=RoutesResponse)
async def get_routes():
    """Return routing configuration.

    Reads from config/routes.yaml or config/routes.json if available.
    Otherwise derives routes from the machine inventory.
    """
    data = load_routes_config()
    return RoutesResponse(**data)


@app.get("/api/profile/current", response_model=ProfileCurrentResponse)
async def get_current_profile():
    """Return the cached profile for the current host, if available."""
    global _cached_profile

    if _cached_profile is not None:
        return ProfileCurrentResponse(status="available", profile=_cached_profile)

    return ProfileCurrentResponse(status="unavailable", profile=None)


@app.post("/api/profile/run", response_model=ProfileRunResponse)
async def run_profile():
    """Run the profiler on the current host.

    Executes profile_machine.py with --json flag using safe subprocess
    invocation. Does NOT overwrite inventory/machines.json.

    This is an admin action — in production, protect with auth.
    """
    if not PROFILER_PATH.exists():
        try:
            display_path = str(PROFILER_PATH.relative_to(PROJECT_ROOT))
        except ValueError:
            display_path = str(PROFILER_PATH)
        raise HTTPException(
            status_code=503,
            detail=f"Profiler script not found: {display_path}",
        )

    try:
        result = subprocess.run(
            [sys.executable, str(PROFILER_PATH), "--json"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProfileRunResponse(
            status="error",
            error="Profiler timed out after 120 seconds",
        )
    except OSError as exc:
        return ProfileRunResponse(
            status="error",
            error=f"Failed to execute profiler: {exc}",
        )

    if result.returncode != 0:
        return ProfileRunResponse(
            status="error",
            error=f"Profiler exited with code {result.returncode}",
            stderr=result.stderr[:2000] if result.stderr else None,
        )

    # Parse JSON output
    try:
        profile_data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return ProfileRunResponse(
            status="error",
            error=f"Failed to parse profiler JSON output: {exc}",
            stderr=result.stdout[:2000],
        )

    # Cache the result for GET /api/profile/current
    global _cached_profile
    _cached_profile = profile_data

    return ProfileRunResponse(status="ok", profile=profile_data)


@app.get("/api/events")
async def get_events(limit: int = 100, status: str = "all"):
    """Return the latest telemetry events from the JSONL log."""
    limit = min(max(1, limit), 500)
    config = _load_telemetry_config()
    log_path_str = config.get("path", "data/telemetry/gateway-events.jsonl")
    log_path = PROJECT_ROOT / log_path_str

    if not log_path.exists():
        return {"events": [], "total_on_disk": 0, "returned": 0}

    events = []
    total = 0
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    total += 1
                    if status != "all" and event.get("status") != status:
                        continue
                    events.append(event)
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass

    events.reverse()
    returned_events = events[:limit]
    
    return {
        "events": returned_events,
        "total_on_disk": total,
        "returned": len(returned_events)
    }

@app.get("/api/stats")
async def get_stats():
    """Return aggregate statistics from the telemetry log."""
    config = _load_telemetry_config()
    log_path_str = config.get("path", "data/telemetry/gateway-events.jsonl")
    log_path = PROJECT_ROOT / log_path_str

    stats = {
        "total_requests": 0,
        "success_rate_pct": 0.0,
        "error_count": 0,
        "fallback_count": 0,
        "fallback_rate_pct": 0.0,
        "requests_by_route": {},
        "requests_by_backend": {},
        "avg_latency_ms_by_backend": {},
        "p95_latency_ms_by_backend": {},
        "privacy_breakdown": {},
        "stream_count": 0,
        "period_start": None,
        "period_end": None
    }

    if not log_path.exists():
        return stats

    success_count = 0
    latencies_by_backend = {}
    
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    stats["total_requests"] += 1
                    
                    if not stats["period_start"]:
                        stats["period_start"] = event.get("timestamp")
                    stats["period_end"] = event.get("timestamp")
                    
                    if event.get("status") == "success":
                        success_count += 1
                    else:
                        stats["error_count"] += 1
                        
                    if event.get("fallback_used"):
                        stats["fallback_count"] += 1
                        
                    route = event.get("route", "unknown")
                    stats["requests_by_route"][route] = stats["requests_by_route"].get(route, 0) + 1
                    
                    backend = event.get("backend", "unknown")
                    stats["requests_by_backend"][backend] = stats["requests_by_backend"].get(backend, 0) + 1
                    
                    privacy = event.get("privacy", "unknown")
                    stats["privacy_breakdown"][privacy] = stats["privacy_breakdown"].get(privacy, 0) + 1
                    
                    if event.get("stream"):
                        stats["stream_count"] += 1
                        
                    lat = event.get("latency_ms")
                    if lat is not None:
                        if backend not in latencies_by_backend:
                            latencies_by_backend[backend] = []
                        latencies_by_backend[backend].append(lat)
                        
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass

    if stats["total_requests"] > 0:
        stats["success_rate_pct"] = round((success_count / stats["total_requests"]) * 100, 1)
        stats["fallback_rate_pct"] = round((stats["fallback_count"] / stats["total_requests"]) * 100, 1)

    for backend, lats in latencies_by_backend.items():
        if lats:
            stats["avg_latency_ms_by_backend"][backend] = int(sum(lats) / len(lats))
            lats.sort()
            idx = int(len(lats) * 0.95)
            if idx >= len(lats):
                idx = len(lats) - 1
            stats["p95_latency_ms_by_backend"][backend] = lats[idx]

    return stats


@app.get("/api/backends/health")
async def get_backends_health():
    backends_config = load_backends_config()
    backends_list = backends_config.get("backends", {})
    healths = []
    
    for bid, bconf in backends_list.items():
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
            
        healths.append({
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
            "status": status_str
        })
        
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

@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """Pass-through embeddings endpoint — forwards to LM Studio on the quality node."""
    backends_config = load_backends_config()
    backend = backends_config.get("backends", {}).get("macbook-m4pro", {})
    openai_endpoint = backend.get("openai_endpoint", "").rstrip("/")
    if not openai_endpoint:
        raise HTTPException(status_code=503, detail="No embeddings backend configured")

    import httpx
    body = await request.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{openai_endpoint}/embeddings", json=body)
        resp.raise_for_status()
        return JSONResponse(content=resp.json())


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, x_ai_privacy: Optional[str] = Header(None),
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

    # 1. Privacy
    privacy_level = extract_privacy_level(request.metadata, x_ai_privacy, security_config.get("default_privacy", "personal"))

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
        auto_route, auto_reason = select_auto_route(request.messages, privacy_level)
        request = request.model_copy(update={"model": auto_route})

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
            gen = provider.chat_completions_stream(request, target_model_name)
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
                                     fallback_used=fallback_used, start_time=start_time):
                ttft = int((time.time() - start_time) * 1000)
                log_llm_event(correlation_id, "provider.stream.first_token", f"First token received in {ttft}ms")
                try:
                    if first_chunk is not None:
                        yield first_chunk
                    async for chunk in gen:
                        yield chunk
                    latency_ms = int((time.time() - start_time) * 1000)
                    log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "success", error=auto_reason, latency_ms=latency_ms, ttft_ms=ttft, stream=True, correlation_id=correlation_id)
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
        log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "success", error=auto_reason, latency_ms=latency_ms, correlation_id=correlation_id)
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
