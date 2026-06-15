"""
Telemetry events for the gateway.
"""

import json
import os
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

def _load_telemetry_config() -> dict:
    for ext in [".local.yaml", ".yaml", ".local.json", ".json"]:
        path = CONFIG_DIR / f"security{ext}"
        if path.exists():
            try:
                if ext.endswith(".yaml"):
                    import yaml
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    return data.get("telemetry", {})
                else:
                    data = json.loads(path.read_text(encoding="utf-8")) or {}
                    return data.get("telemetry", {})
            except Exception:
                pass
    return {}

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
    stream: bool = False
):
    """Logs a routing decision metadata event."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "chat_completion",
        "route": route,
        "backend": backend,
        "model": model,
        "privacy": privacy,
        "ttft_ms": ttft_ms,
        "latency_ms": latency_ms,
        "fallback_used": fallback_used,
        "status": status,
        "error_code": error,
        "stream": stream
    }
    
    print(f"[TELEMETRY] {event}")

    config = _load_telemetry_config()
    if not config.get("enabled", False):
        return

    log_path_str = config.get("path", "data/telemetry/gateway-events.jsonl")
    log_path = PROJECT_ROOT / log_path_str

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[TELEMETRY-WARN] Could not create telemetry directory: {e}")
        return

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            json.dump(event, f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        print(f"[TELEMETRY-WARN] Could not write to telemetry file: {e}")
