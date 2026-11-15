"""Request limits: body size, per-client rate limiting, and a max_tokens cap.

Configured under ``security.limits`` in security.yaml:

    limits:
      max_body_bytes: 20971520      # reject larger requests with 413 (0 = off)
      rate_limit_per_minute: 0      # per API key (or client IP); 0 = off
      max_tokens_cap: 0             # clamp/apply max_tokens on completions; 0 = off

Loopback clients are exempt from rate limiting (same trust as auth). The body-size
check uses the Content-Length header; chunked uploads without one aren't measured.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse

DEFAULTS = {"max_body_bytes": 20 * 1024 * 1024, "rate_limit_per_minute": 0, "max_tokens_cap": 0}
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_RATE_LIMITED = ("/v1/chat/completions", "/v1/embeddings", "/chat/extract", "/chat/memory/", "/chat/title")
_WINDOW_S = 60.0

_hits: dict = defaultdict(deque)  # client id -> request timestamps within the window


def limits_config(security_config: dict) -> dict:
    cfg = dict(DEFAULTS)
    for k, v in ((security_config or {}).get("limits") or {}).items():
        if k in cfg:
            try:
                cfg[k] = int(v)
            except (TypeError, ValueError):
                pass
    return cfg


def clamp_max_tokens(max_tokens, cap: int):
    """The max_tokens to use under ``cap`` (0 = no cap)."""
    if not cap:
        return max_tokens
    return cap if max_tokens is None or max_tokens > cap else max_tokens


def check_rate(client_id: str, per_minute: int, now: float | None = None) -> float:
    """Record a hit. Returns 0 if allowed, else seconds until a slot frees up."""
    now = time.monotonic() if now is None else now
    q = _hits[client_id]
    while q and now - q[0] >= _WINDOW_S:
        q.popleft()
    if len(q) >= per_minute:
        return max(0.1, _WINDOW_S - (now - q[0]))
    q.append(now)
    if len(_hits) > 1000:  # don't let one-off clients accumulate forever
        for cid in [c for c, d in _hits.items() if not d or now - d[-1] >= _WINDOW_S]:
            _hits.pop(cid, None)
    return 0.0


def _error(status: int, message: str, code: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, headers=headers,
                        content={"error": {"message": message, "type": "limit_exceeded", "code": code}})


async def limits_middleware(request: Request, call_next):
    from gateway.server import load_security_config  # lazy: avoids circular import

    cfg = limits_config(load_security_config().get("security", {}))

    max_body = cfg["max_body_bytes"]
    if max_body and request.method in ("POST", "PUT", "PATCH"):
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length > max_body:
            return _error(413, f"Request body too large (max {max_body} bytes)", "body_too_large")

    per_minute = cfg["rate_limit_per_minute"]
    path = request.url.path
    host = request.client.host if request.client else ""
    if per_minute and host not in _LOOPBACK and request.method == "POST" and path.startswith(_RATE_LIMITED):
        client_id = getattr(request.state, "client_id", None) or f"ip:{host}"
        wait = check_rate(client_id, per_minute)
        if wait:
            return _error(429, "Rate limit exceeded", "rate_limit_exceeded",
                          {"Retry-After": str(int(wait) + 1)})

    return await call_next(request)
