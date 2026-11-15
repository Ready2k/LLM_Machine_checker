"""
Telemetry events for the gateway.
"""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from pathlib import Path

from gateway.telemetry.database import queue_insert

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

logger = logging.getLogger(__name__)

_TELEMETRY_CACHE: dict = {}  # path -> ((mtime_ns, size), telemetry-config)


def _load_telemetry_config() -> dict:
    """The ``telemetry`` block of security.{local.,}{yaml,json}. Called several times
    per request, so it's cached until the file's mtime/size changes."""
    for ext in [".local.yaml", ".yaml", ".local.json", ".json"]:
        path = CONFIG_DIR / f"security{ext}"
        if not path.exists():
            continue
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            hit = _TELEMETRY_CACHE.get(str(path))
            if hit and hit[0] == sig:
                return dict(hit[1])
            text = path.read_text(encoding="utf-8")
            if ext.endswith(".yaml"):
                import yaml
                data = yaml.safe_load(text) or {}
            else:
                data = json.loads(text) or {}
            cfg = data.get("telemetry", {}) or {}
            _TELEMETRY_CACHE[str(path)] = (sig, cfg)
            return dict(cfg)
        except Exception:
            pass
    return {}

def log_llm_request(
    correlation_id: str,
    route: str,
    backend: str,
    model: str,
    privacy: str,
    status: str,
    timestamp_start: str,
    timestamp_end: str,
    duration_ms: Optional[int],
    ttft_ms: Optional[int],
    fallback_used: bool,
    is_stream: bool,
    error_code: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    prompt_hash: Optional[str] = None,
    response_hash: Optional[str] = None,
):
    """Logs the completion of an LLM request to SQLite."""
    config = _load_telemetry_config()
    if not config.get("enabled", False):
        return

    queue_insert("llm_requests", {
        "correlation_id": correlation_id,
        "route": route,
        "backend": backend,
        "model": model,
        "privacy": privacy,
        "status": status,
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        "duration_ms": duration_ms,
        "time_to_first_token_ms": ttft_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "fallback_used": fallback_used,
        "error_code": error_code,
        "prompt_hash": prompt_hash,
        "response_hash": response_hash,
        "is_stream": is_stream
    })

def log_llm_event(
    correlation_id: str,
    event_type: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None
):
    """Logs a point-in-time event for a specific request correlation ID."""
    config = _load_telemetry_config()
    if not config.get("enabled", False):
        return

    queue_insert("llm_events", {
        "correlation_id": correlation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "message": message,
        "metadata": json.dumps(metadata) if metadata else None
    })

# Maintain backward compatibility if any old code uses log_routing_decision
def log_routing_decision(
    route: str, 
    model: str, 
    privacy: str, 
    backend: str, 
    fallback_used: bool, 
    status: str = "success", 
    error: str = None,
    ttft_ms: Optional[int] = None,
    latency_ms: Optional[int] = None,
    stream: bool = False,
    correlation_id: str = "unknown",
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
):
    now = datetime.now(timezone.utc).isoformat()
    log_llm_request(
        correlation_id=correlation_id,
        route=route,
        backend=backend,
        model=model,
        privacy=privacy,
        status=status,
        timestamp_start=now,
        timestamp_end=now,
        duration_ms=latency_ms,
        ttft_ms=ttft_ms,
        fallback_used=fallback_used,
        is_stream=stream,
        error_code=error,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
