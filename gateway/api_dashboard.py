"""Dashboard/inventory/stats endpoints, split out of server.py.

Names that tests (and operators) patch on ``gateway.server`` — the path constants,
``_cached_profile``, ``get_db``, ``load_routes_config`` — are resolved through the
server module at call time via ``_s()``, which also avoids a circular import.
"""
import json
import subprocess
import sys
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


def _s():
    from gateway import server
    return server


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



def load_inventory() -> dict:
    """Load machine inventory from JSON file.

    Returns the parsed inventory dict or raises HTTPException if unavailable.
    """
    if not _s().INVENTORY_PATH.exists():
        try:
            display_path = str(_s().INVENTORY_PATH.relative_to(_s().PROJECT_ROOT))
        except ValueError:
            display_path = str(_s().INVENTORY_PATH)
        raise HTTPException(
            status_code=503,
            detail=f"Inventory file not found: {display_path}",
        )

    try:
        data = json.loads(_s().INVENTORY_PATH.read_text(encoding="utf-8"))
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



@router.get("/api/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse()


@router.get("/api/machines", response_model=InventoryResponse)
async def get_machines():
    """Return the full machine inventory."""
    data = load_inventory()
    return InventoryResponse(**data)


@router.get("/api/machines/{machine_id}", response_model=Machine)
async def get_machine(machine_id: str):
    """Return a single machine by ID."""
    data = load_inventory()
    machines = data.get("machines", [])

    for machine in machines:
        if machine.get("id") == machine_id:
            return Machine(**machine)

    raise HTTPException(status_code=404, detail=f"Machine not found: {machine_id}")


@router.get("/api/config/routes", response_model=RoutesResponse)
async def get_routes():
    """Return routing configuration.

    Reads from config/routes.yaml or config/routes.json if available.
    Otherwise derives routes from the machine inventory.
    """
    data = _s().load_routes_config()
    return RoutesResponse(**data)


@router.get("/api/profile/current", response_model=ProfileCurrentResponse)
async def get_current_profile():
    """Return the cached profile for the current host, if available."""
    cached = _s()._cached_profile
    if cached is not None:
        return ProfileCurrentResponse(status="available", profile=cached)

    return ProfileCurrentResponse(status="unavailable", profile=None)


@router.post("/api/profile/run", response_model=ProfileRunResponse)
async def run_profile():
    """Run the profiler on the current host.

    Executes profile_machine.py with --json flag using safe subprocess
    invocation. Does NOT overwrite inventory/machines.json.

    This is an admin action — in production, protect with auth.
    """
    if not _s().PROFILER_PATH.exists():
        try:
            display_path = str(_s().PROFILER_PATH.relative_to(_s().PROJECT_ROOT))
        except ValueError:
            display_path = str(_s().PROFILER_PATH)
        raise HTTPException(
            status_code=503,
            detail=f"Profiler script not found: {display_path}",
        )

    try:
        result = subprocess.run(
            [sys.executable, str(_s().PROFILER_PATH), "--json"],
            cwd=str(_s().PROJECT_ROOT),
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
    _s()._cached_profile = profile_data

    return ProfileRunResponse(status="ok", profile=profile_data)


def _request_row_to_event(row) -> dict:
    """llm_requests row -> the event shape /api/events has always returned."""
    (ts, route, backend, model, privacy, status, ttft, dur, fb, err, stream, tin, tout) = row
    return {
        "timestamp": ts, "event_type": "chat_completion", "route": route, "backend": backend,
        "model": model, "privacy": privacy, "ttft_ms": ttft, "latency_ms": dur,
        "fallback_used": bool(fb), "status": status, "error_code": err,
        "stream": bool(stream), "tokens_in": tin, "tokens_out": tout,
    }


@router.get("/api/events")
async def get_events(limit: int = 100, status: str = "all"):
    """Latest routing events (newest first) from the SQLite telemetry store."""
    limit = min(max(1, limit), 500)
    db = await _s().get_db()
    if not db:
        return {"events": [], "total_on_disk": 0, "returned": 0}

    async with db.execute("SELECT COUNT(*) FROM llm_requests") as cur:
        total = (await cur.fetchone())[0]
    where, params = ("", ())
    if status != "all":
        where, params = ("WHERE status = ?", (status,))
    async with db.execute(
        "SELECT timestamp_start, route, backend, model, privacy, status, time_to_first_token_ms, "
        "duration_ms, fallback_used, error_code, is_stream, tokens_in, tokens_out "
        f"FROM llm_requests {where} ORDER BY timestamp_start DESC LIMIT ?",
        (*params, limit),
    ) as cur:
        events = [_request_row_to_event(r) for r in await cur.fetchall()]

    return {"events": events, "total_on_disk": total, "returned": len(events)}


@router.get("/api/stats")
async def get_stats():
    """Aggregate routing statistics from the SQLite telemetry store."""
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
        "period_end": None,
    }
    db = await _s().get_db()
    if not db:
        return stats

    async with db.execute(
        "SELECT COUNT(*), SUM(status = 'success'), SUM(COALESCE(fallback_used, 0)), "
        "SUM(COALESCE(is_stream, 0)), MIN(timestamp_start), MAX(timestamp_start) FROM llm_requests"
    ) as cur:
        total, ok, fb, streams, first, last = await cur.fetchone()
    if not total:
        return stats

    stats.update(
        total_requests=total, error_count=total - (ok or 0), fallback_count=fb or 0,
        stream_count=streams or 0, period_start=first, period_end=last,
        success_rate_pct=round((ok or 0) / total * 100, 1),
        fallback_rate_pct=round((fb or 0) / total * 100, 1),
    )

    for column, key in (("route", "requests_by_route"), ("backend", "requests_by_backend"),
                        ("privacy", "privacy_breakdown")):
        async with db.execute(
            f"SELECT COALESCE({column}, 'unknown'), COUNT(*) FROM llm_requests GROUP BY 1"
        ) as cur:
            stats[key] = {name: n for name, n in await cur.fetchall()}

    async with db.execute(
        "SELECT COALESCE(backend, 'unknown'), COUNT(duration_ms), AVG(duration_ms) "
        "FROM llm_requests WHERE duration_ms IS NOT NULL GROUP BY 1"
    ) as cur:
        per_backend = await cur.fetchall()
    for backend, n, avg in per_backend:
        stats["avg_latency_ms_by_backend"][backend] = int(avg)
        idx = min(int(n * 0.95), n - 1)  # same nearest-rank rule as the old JSONL code
        async with db.execute(
            "SELECT duration_ms FROM llm_requests WHERE COALESCE(backend, 'unknown') = ? "
            "AND duration_ms IS NOT NULL ORDER BY duration_ms LIMIT 1 OFFSET ?",
            (backend, idx),
        ) as cur:
            stats["p95_latency_ms_by_backend"][backend] = (await cur.fetchone())[0]

    return stats
