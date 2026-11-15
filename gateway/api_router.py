"""Auto-router feedback + observability endpoints (/api/router/*).

Everything here reads what the request path already logs (``router.auto`` events
joined to ``llm_requests``), so ``model: "auto"`` routing itself is untouched.

Privacy: a feedback entry keeps the decision trace (signal names/weights) and a
truncated hash of the prompt. The prompt *text* is stored only when the reporter
sends it explicitly, ``telemetry.feedback_store_prompts`` is not false, and
neither the route taken nor the wanted route is the private one. It is cleared
when the entry is promoted to a test or dismissed (scripts/autoroute_feedback.py).

``get_db`` / ``load_autoroute_config`` are resolved through ``gateway.server`` at
call time (tests patch them there), like api_dashboard.py.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gateway.policy import autoroute
from gateway.telemetry.events import _load_telemetry_config

router = APIRouter(prefix="/api/router", tags=["auto-router"])

WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
MAX_PROMPT_CHARS = 8000        # stored prompt text is capped; the hash covers the full text
LOW_CONFIDENCE = 0.65          # decisions at or below this are "misroute candidates"
_SCAN_LIMIT = 50000            # rows read per stats call (a week of home-LAN traffic)
_AUTO_SQL = (
    "SELECT e.timestamp, e.correlation_id, e.metadata, r.backend, r.status, r.duration_ms, "
    "r.time_to_first_token_ms, r.tokens_in, r.tokens_out, r.fallback_used "
    "FROM llm_events e LEFT JOIN llm_requests r ON r.correlation_id = e.correlation_id "
    "WHERE e.event_type = 'router.auto' "
)


def _s():
    from gateway import server
    return server


def _route_names() -> dict:
    """{'fast': ..., 'quality': ..., 'private': ...} from config/autoroute.yaml."""
    return dict(autoroute._settings_for(_s().load_autoroute_config()).routes)


def prompt_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _meta(raw) -> dict:
    try:
        d = json.loads(raw) if raw else {}
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _pct(sorted_vals: list, p: float):
    if not sorted_vals:
        return None
    return sorted_vals[min(int(len(sorted_vals) * p), len(sorted_vals) - 1)]  # nearest rank, as /api/stats


async def _require_db():
    db = await _s().get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Telemetry database not ready")
    return db


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

class FeedbackIn(BaseModel):
    correlation_id: str = Field(min_length=1, max_length=64)
    desired: str                                  # fast | quality | private, or a route name
    note: Optional[str] = Field(default=None, max_length=500)
    prompt: Optional[str] = None                  # opt-in: the user message that was misrouted
    # Used only when the decision event isn't in the DB (telemetry off / pruned):
    actual_route: Optional[str] = Field(default=None, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=300)


@router.post("/feedback")
async def submit_feedback(body: FeedbackIn):
    """Mark an auto-routing decision as wrong: "this should have been {desired}"."""
    db = await _require_db()
    routes = _route_names()
    desired = routes.get(body.desired.strip().lower(), body.desired.strip())
    if desired not in routes.values():
        raise HTTPException(status_code=422, detail=f"desired must be one of: {', '.join(routes)}")

    async with db.execute(
        "SELECT metadata FROM llm_events WHERE correlation_id = ? AND event_type = 'router.auto' "
        "ORDER BY id DESC LIMIT 1", (body.correlation_id,),
    ) as cur:
        row = await cur.fetchone()
    trace = _meta(row[0]) if row else {}
    actual = trace.get("route") or body.actual_route
    if not actual:
        raise HTTPException(status_code=404, detail="No auto-routing decision recorded for that correlation id")
    if actual == desired:
        raise HTTPException(status_code=422, detail=f"The request was already routed to {desired}")

    text = body.prompt if isinstance(body.prompt, str) and body.prompt.strip() else None
    private = routes["private"]
    may_store = (
        text is not None
        and _load_telemetry_config().get("feedback_store_prompts", True) is not False
        and private not in (actual, desired)
    )
    await db.execute(
        "INSERT INTO router_feedback (correlation_id, created_at, actual_route, desired_route, stage, score, "
        "threshold, confidence, reason, signals, prompt_hash, prompt_text, note, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'new') "
        "ON CONFLICT(correlation_id) DO UPDATE SET created_at=excluded.created_at, "
        "desired_route=excluded.desired_route, prompt_hash=excluded.prompt_hash, "
        "prompt_text=excluded.prompt_text, note=excluded.note, status='new'",
        (
            body.correlation_id, datetime.now(timezone.utc).isoformat(), actual, desired,
            trace.get("stage"), trace.get("score"), trace.get("threshold"), trace.get("confidence"),
            trace.get("reason") or body.reason,
            json.dumps(trace["signals"]) if trace.get("signals") else None,
            prompt_fingerprint(text) if text else None,
            text[:MAX_PROMPT_CHARS] if may_store else None,
            (body.note or "").strip() or None,
        ),
    )
    await db.commit()
    async with db.execute("SELECT id FROM router_feedback WHERE correlation_id = ?", (body.correlation_id,)) as cur:
        fid = (await cur.fetchone())[0]
    return {"id": fid, "actual_route": actual, "desired_route": desired, "prompt_stored": may_store}


@router.get("/feedback")
async def list_feedback(status: str = "new", limit: int = 100):
    """Reported misroutes (never returns prompt text — use the CLI on the gateway host)."""
    db = await _require_db()
    limit = min(max(1, limit), 500)
    where, params = ("", ()) if status == "all" else ("WHERE status = ?", (status,))
    async with db.execute(
        "SELECT id, correlation_id, created_at, actual_route, desired_route, stage, score, threshold, "
        "confidence, reason, prompt_hash, prompt_text IS NOT NULL, note, status "
        f"FROM router_feedback {where} ORDER BY id DESC LIMIT ?", (*params, limit),
    ) as cur:
        rows = await cur.fetchall()
    keys = ("id", "correlation_id", "created_at", "actual_route", "desired_route", "stage", "score",
            "threshold", "confidence", "reason", "prompt_hash", "has_prompt", "note", "status")
    items = [dict(zip(keys, r)) for r in rows]
    for it in items:
        it["has_prompt"] = bool(it["has_prompt"])
    return {"feedback": items, "returned": len(items)}


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

def _summarise(rows: list) -> dict:
    """rows: (route, stage, confidence, status, duration_ms, ttft_ms, tokens_in, tokens_out, fallback)."""
    total = len(rows)
    per: dict = {}
    stages: dict = {}
    low = 0
    for route, stage, conf, status, dur, ttft, tin, tout, fb in rows:
        r = per.setdefault(route, {"requests": 0, "errors": 0, "fallbacks": 0, "tokens_in": 0,
                                   "tokens_out": 0, "_dur": [], "_ttft": [], "_conf": []})
        r["requests"] += 1
        r["errors"] += status not in (None, "success")
        r["fallbacks"] += bool(fb)
        r["tokens_in"] += tin or 0
        r["tokens_out"] += tout or 0
        if status == "success" and dur is not None:
            r["_dur"].append(dur)
        if status == "success" and ttft is not None:
            r["_ttft"].append(ttft)
        stages[stage] = stages.get(stage, 0) + 1
        if stage in ("complexity", "model") and conf is not None:
            r["_conf"].append(conf)
            low += conf <= LOW_CONFIDENCE
    for r in per.values():
        dur, ttft, conf = sorted(r.pop("_dur")), sorted(r.pop("_ttft")), r.pop("_conf")
        r["share_pct"] = round(r["requests"] / total * 100, 1)
        r["p50_ms"], r["p95_ms"] = _pct(dur, 0.5), _pct(dur, 0.95)
        r["ttft_p50_ms"], r["ttft_p95_ms"] = _pct(ttft, 0.5), _pct(ttft, 0.95)
        r["avg_confidence"] = round(sum(conf) / len(conf), 2) if conf else None
    return {"total": total, "routes": per, "stages": stages, "low_confidence": low}


@router.get("/stats")
async def router_stats():
    """How model:"auto" traffic split, per route, over the last 1h / 24h / 7d."""
    empty = {"total": 0, "routes": {}, "stages": {}, "low_confidence": 0}
    out = {"windows": {w: dict(empty) for w in WINDOWS}, "low_confidence_threshold": LOW_CONFIDENCE,
           "feedback": {"new": 0, "promoted": 0, "dismissed": 0}, "truncated": False}
    db = await _s().get_db()
    if not db:
        return out

    now = datetime.now(timezone.utc)
    async with db.execute(
        _AUTO_SQL + "AND e.timestamp >= ? ORDER BY e.id DESC LIMIT ?",
        ((now - WINDOWS["7d"]).isoformat(), _SCAN_LIMIT),
    ) as cur:
        raw = await cur.fetchall()
    out["truncated"] = len(raw) >= _SCAN_LIMIT
    parsed = []
    for ts, _cid, metadata, _backend, status, dur, ttft, tin, tout, fb in raw:
        m = _meta(metadata)
        parsed.append((ts, (m.get("route") or "unknown", m.get("stage") or "unknown", m.get("confidence"),
                            status, dur, ttft, tin, tout, fb)))
    for name, delta in WINDOWS.items():
        cutoff = (now - delta).isoformat()
        out["windows"][name] = _summarise([r for ts, r in parsed if ts >= cutoff])

    try:
        async with db.execute("SELECT status, COUNT(*) FROM router_feedback GROUP BY 1") as cur:
            out["feedback"].update({k or "new": n for k, n in await cur.fetchall()})
    except Exception:
        pass  # table missing on a DB created before this feature
    return out


@router.get("/decisions")
async def router_decisions(limit: int = 50, candidates: bool = False, max_confidence: float = LOW_CONFIDENCE):
    """Recent auto decisions, newest first, with reason / score / confidence.

    ``candidates=true`` keeps only likely misroutes: complexity decisions whose
    confidence is at or below ``max_confidence`` (close to the threshold).
    """
    limit = min(max(1, limit), 500)
    db = await _s().get_db()
    if not db:
        return {"decisions": [], "returned": 0}

    async with db.execute(_AUTO_SQL + "ORDER BY e.id DESC LIMIT ?", (2000 if candidates else limit,)) as cur:
        raw = await cur.fetchall()
    feedback = {}
    try:
        async with db.execute("SELECT correlation_id, desired_route, status FROM router_feedback") as cur:
            feedback = {cid: {"desired_route": d, "status": st} for cid, d, st in await cur.fetchall()}
    except Exception:
        pass

    decisions = []
    for ts, cid, metadata, backend, status, dur, ttft, tin, tout, fb in raw:
        m = _meta(metadata)
        conf, stage = m.get("confidence"), m.get("stage")
        low = stage in ("complexity", "model") and conf is not None and conf <= max_confidence
        if candidates and not low:
            continue
        decisions.append({
            "timestamp": ts, "correlation_id": cid, "route": m.get("route"), "stage": stage,
            "score": m.get("score"), "threshold": m.get("threshold"), "confidence": conf,
            "reason": m.get("reason"), "low_confidence": low,
            "backend": backend, "status": status, "latency_ms": dur, "ttft_ms": ttft,
            "tokens_in": tin, "tokens_out": tout, "fallback_used": bool(fb),
            "feedback": feedback.get(cid),
        })
        if len(decisions) >= limit:
            break
    return {"decisions": decisions, "returned": len(decisions)}
