"""
Local AI Fabric — Gateway Server

FastAPI application serving the admin dashboard and /chat/ UI.
Routes LLM requests across local backends (Ollama, LM Studio).

Bind address: GATEWAY_HOST env var (default 0.0.0.0 — all interfaces).
Port:         GATEWAY_PORT env var (default 8080).
"""

import io
import json
import os
from contextlib import asynccontextmanager
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uuid
import time

from gateway.providers.base import BackendHealth, ModelInfo, ChatCompletionRequest, ChatCompletionResponse, Provider
from gateway.providers.ollama import OllamaProvider
from gateway.providers.lmstudio import LMStudioProvider
from gateway.providers.openai_compatible import OpenAICompatibleProvider
from gateway.policy.privacy import extract_privacy_level, is_privacy_allowed, can_fallback_to_cloud
from gateway.policy.routing import resolve_model_to_route, select_backend
from gateway.policy.autoroute import select_auto_route
from gateway.telemetry.events import log_routing_decision, log_llm_event, _load_telemetry_config
from gateway.telemetry.database import init_db, close_db
from gateway.api_observability import router as observability_router

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


def load_routes_config() -> dict:
    """Load route configuration from config files or derive from inventory.

    Checks config/routes.yaml then config/routes.json.
    Falls back to deriving routes from inventory/machines.json.
    """
    # Try YAML config first
    yaml_path = CONFIG_DIR / "routes.yaml"
    if yaml_path.exists():
        try:
            import yaml
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            return {"routes": data.get("routes", {}), "source": "config"}
        except Exception:
            pass

    # Try JSON config
    json_path = CONFIG_DIR / "routes.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
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
                if ext.endswith(".yaml"):
                    import yaml
                    return yaml.safe_load(path.read_text(encoding="utf-8")) or default_val
                else:
                    return json.loads(path.read_text(encoding="utf-8")) or default_val
            except Exception:
                pass
    return default_val

def load_models_config() -> dict:
    return _load_yaml_or_json("models", {"models": {}})

def load_security_config() -> dict:
    return _load_yaml_or_json("security", {"security": {"default_privacy": "personal"}})

def load_backends_config() -> dict:
    return _load_yaml_or_json("backends", {"backends": {}})

def get_provider(backend_id: str, backends_config: dict) -> Optional[Provider]:
    b_config = backends_config.get("backends", {}).get(backend_id)
    if not b_config:
        return None
    runtime = b_config.get("runtime")
    endpoint = b_config.get("endpoint")
    if not runtime or not endpoint:
        return None
    if runtime == "ollama":
        return OllamaProvider(backend_id, endpoint, role=b_config.get("role", "unknown"))
    elif runtime == "lmstudio":
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


@app.post("/chat/extract")
async def extract_file(file: UploadFile = File(...)):
    """Extract plain text from an uploaded file (PDF, DOCX, or text)."""
    MAX_BYTES = 5 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")

    name = (file.filename or "").lower()

    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise HTTPException(status_code=501, detail="pypdf not installed — run: pip install pypdf")
        reader = PdfReader(io.BytesIO(content))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()

    elif name.endswith(".docx"):
        try:
            from docx import Document
        except ImportError:
            raise HTTPException(status_code=501, detail="python-docx not installed — run: pip install python-docx")
        doc = Document(io.BytesIO(content))
        text = "\n".join(p.text for p in doc.paragraphs if p.text).strip()

    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=415,
                detail="Binary file — only text, PDF, and DOCX are supported",
            )

    return {"text": text, "filename": file.filename, "chars": len(text)}


# ---------------------------------------------------------------------------
# Chat utility endpoints (memory extraction + title).
#
# Stateless helpers for the /chat/ UI's long-term memory feature: they wrap a
# fixed prompt around the normal routing pipeline (forced to a cheap local
# route) and store nothing server-side. Both always return a usable response;
# on any failure they fall back to empty so the client is never blocked.
# ---------------------------------------------------------------------------

_MEMORY_EXTRACTION_PROMPT = (
    "Extract durable facts about the user worth remembering for future "
    "conversations — preferences, recurring context, projects, constraints. "
    "Ignore one-off task details and anything about the assistant. "
    "Return a JSON array of short factual strings (max 5). "
    "If nothing is worth remembering, return []."
)

_TITLE_PROMPT = (
    "Generate a concise 3 to 6 word title summarising the conversation. "
    "Reply with only the title text — no quotes, no punctuation, no preamble."
)


class ChatMessagesRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


def _format_transcript(messages: list[dict], limit: int = 6000) -> str:
    """Render user/assistant messages into a plain transcript, length-capped."""
    lines = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            who = "User" if role == "user" else "Assistant"
            lines.append(f"{who}: {content.strip()}")
    return "\n\n".join(lines)[:limit]


def _parse_facts(text: str) -> list[str]:
    """Defensively pull a JSON string-array out of model output."""
    if not text:
        return []
    import re
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    facts = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            facts.append(item.strip())
    return facts[:5]


async def _run_utility_completion(messages: list[dict], max_tokens: int, route_alias: str = "local-fast") -> str:
    """Run a non-streaming completion through the routing pipeline on a cheap
    local route. Returns the assistant text, or "" if nothing came back."""
    import asyncio

    models_config = load_models_config()
    routes_config = load_routes_config().get("routes", {})
    backends_config = load_backends_config()

    route_name, route_config = resolve_model_to_route(route_alias, models_config, routes_config)
    backend_id = select_backend(route_config, backends_config)
    provider = get_provider(backend_id, backends_config)
    if not provider:
        raise HTTPException(status_code=503, detail="No backend available for utility request")

    target_model_name = route_alias
    if route_alias in models_config.get("models", {}):
        target_model_name = models_config["models"][route_alias].get("model", route_alias)

    chat_req = ChatCompletionRequest(
        model=target_model_name,
        messages=messages,
        stream=False,
        max_tokens=max_tokens,
    )
    response = await asyncio.wait_for(provider.chat_completions(chat_req, target_model_name), timeout=60.0)
    if response.choices:
        return response.choices[0].get("message", {}).get("content", "") or ""
    return ""


@app.post("/chat/memory/extract")
async def memory_extract(req: ChatMessagesRequest):
    """Extract candidate long-term memory facts from a conversation.
    Stateless — stores nothing. Returns {"facts": [...]} (possibly empty)."""
    transcript = _format_transcript(req.messages)
    if not transcript.strip():
        return {"facts": []}
    messages = [
        {"role": "system", "content": _MEMORY_EXTRACTION_PROMPT},
        {"role": "user", "content": f"Conversation:\n\n{transcript}\n\nReturn the JSON array."},
    ]
    try:
        text = await _run_utility_completion(messages, max_tokens=300)
    except Exception:
        return {"facts": []}
    return {"facts": _parse_facts(text)}


@app.post("/chat/title")
async def chat_title(req: ChatMessagesRequest):
    """Generate a short conversation title. Stateless. Returns {"title": "..."}
    (possibly empty — the client falls back to the first message locally)."""
    transcript = _format_transcript(req.messages, limit=2000)
    if not transcript.strip():
        return {"title": ""}
    messages = [
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"Conversation:\n\n{transcript}\n\nTitle:"},
    ]
    try:
        text = await _run_utility_completion(messages, max_tokens=24)
    except Exception:
        return {"title": ""}
    title = (text or "").strip()
    if title:
        title = title.splitlines()[0].strip().strip('"').strip()[:60]
    return {"title": title}


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
async def chat_completions(request: ChatCompletionRequest, x_ai_privacy: Optional[str] = Header(None)):
    correlation_id = str(uuid.uuid4())
    log_llm_event(correlation_id, "request.received", f"Received chat completion request for {request.model}")
    pass  # streaming handled below

    models_config = load_models_config()
    routes_data = load_routes_config()
    routes_config = routes_data.get("routes", {})
    security_config = load_security_config().get("security", {})
    backends_config = load_backends_config()

    # 1. Privacy
    privacy_level = extract_privacy_level(request.metadata, x_ai_privacy, security_config.get("default_privacy", "personal"))

    # 2. Auto-routing: inspect prompt and pick best route automatically
    auto_reason = None
    if request.model == "auto":
        auto_route, auto_reason = select_auto_route(request.messages, privacy_level)
        request = request.model_copy(update={"model": auto_route})

    # 3. Resolve Model -> Route
    route_name, route_config = resolve_model_to_route(request.model, models_config, routes_config)

    # 3. Apply Privacy Policy
    if not is_privacy_allowed(privacy_level, route_config):
        log_routing_decision(route_name, request.model, privacy_level, "none", False, "blocked", "privacy_policy_violation", correlation_id=correlation_id)
        raise HTTPException(status_code=403, detail={"message": f"Privacy level {privacy_level} not allowed for route {route_name}", "type": "privacy_blocked", "code": "policy_violation"})

    # 4. Select Backend
    try:
        backend_id = select_backend(route_config, backends_config)
    except HTTPException as e:
        log_routing_decision(route_name, request.model, privacy_level, "none", False, "error", "no_backends_available", correlation_id=correlation_id)
        raise e

    # Determine if fallback was used
    primary_id = route_config.get("primary")
    fallback_used = backend_id != primary_id

    # 5. Forward Request
    provider = get_provider(backend_id, backends_config)
    if not provider:
        log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", "provider_not_found", correlation_id=correlation_id)
        raise HTTPException(status_code=500, detail={"message": f"Provider adapter not found for backend {backend_id}", "type": "config_error", "code": "internal_error"})

    target_model_name = request.model
    if request.model in models_config.get("models", {}):
        target_model_name = models_config["models"][request.model].get("model", request.model)

    headers = {
        "x-correlation-id": correlation_id,
        "x-ai-route": route_name,
        "x-ai-backend": backend_id,
        "x-ai-model": target_model_name,
        "x-ai-fallback-used": str(fallback_used).lower(),
        "x-ai-policy": privacy_level
    }

    if auto_reason:
        headers["x-ai-auto-reason"] = auto_reason

    if request.stream:
        async def stream_wrapper(gen):
            start_time = time.time()
            ttft = None
            try:
                async for chunk in gen:
                    if ttft is None:
                        ttft = int((time.time() - start_time) * 1000)
                        log_llm_event(correlation_id, "provider.stream.first_token", f"First token received in {ttft}ms")
                    yield chunk
                latency_ms = int((time.time() - start_time) * 1000)
                log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "success", error=auto_reason, latency_ms=latency_ms, ttft_ms=ttft, stream=True, correlation_id=correlation_id)
                log_llm_event(correlation_id, "request.complete", "Stream completed successfully", {"latency_ms": latency_ms})
            except Exception as e:
                latency_ms = int((time.time() - start_time) * 1000)
                log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", str(e), latency_ms=latency_ms, ttft_ms=ttft, stream=True, correlation_id=correlation_id)
                raise e

        stream_gen = provider.chat_completions_stream(request, target_model_name)
        return StreamingResponse(stream_wrapper(stream_gen), media_type="text/event-stream", headers=headers)

    log_llm_event(correlation_id, "router.selected", f"Selected backend {backend_id}", {"backend": backend_id})
    start_time = time.time()
    try:
        response = await provider.chat_completions(request, target_model_name)
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "error", str(e), latency_ms=latency_ms, correlation_id=correlation_id)
        raise HTTPException(status_code=502, detail={"message": f"Backend {backend_id} failed: {str(e)}", "type": "backend_error", "code": "bad_gateway"})

    latency_ms = int((time.time() - start_time) * 1000)
    log_routing_decision(route_name, request.model, privacy_level, backend_id, fallback_used, "success", error=auto_reason, latency_ms=latency_ms, correlation_id=correlation_id)
    log_llm_event(correlation_id, "request.complete", "Request completed successfully", {"latency_ms": latency_ms})
    return JSONResponse(content=response.model_dump(), headers=headers)

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

    target_model_name = request.route
    if request.route in models_config.get("models", {}):
        target_model_name = models_config["models"][request.route].get("model", request.route)
        
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
    uvicorn.run(
        "gateway.server:app",
        host=_host,
        port=_port,
        reload=False,
    )
