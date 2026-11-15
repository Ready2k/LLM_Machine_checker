"""API-key authentication for the gateway.

Keys come from the ``GATEWAY_API_KEYS`` env var (comma-separated) — never from
committed config. Behaviour:

* Loopback clients (127.0.0.1 / ::1) are always trusted.
* Static assets, the HTML shells, ``/api/health`` and the OpenAPI docs stay open,
  so a browser can load the UI and then supply a key (see /static/auth.js).
* Everything under ``/v1/``, ``/api/`` and the ``/chat/*`` POST helpers requires
  ``Authorization: Bearer <key>`` or ``x-api-key: <key>`` from non-loopback clients.
* If ``security.require_api_key_for_lan`` is true but no keys are configured the
  gateway fails *open* (so an upgrade can't lock you out) and logs a loud warning.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("gateway.auth")

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_OPEN_EXACT = {"/api/health", "/api/docs", "/openapi.json"}
_PROTECTED_PREFIXES = ("/v1/", "/api/", "/chat/extract", "/chat/memory/", "/chat/title")


def configured_keys() -> list[str]:
    return [k.strip() for k in os.environ.get("GATEWAY_API_KEYS", "").split(",") if k.strip()]


def auth_status(security_config: dict, log: bool = False) -> str:
    """'enforced' | 'open-unconfigured' | 'disabled'. Optionally log it."""
    required = bool((security_config or {}).get("require_api_key_for_lan", False))
    if not required:
        status = "disabled"
    elif configured_keys():
        status = "enforced"
    else:
        status = "open-unconfigured"
    if log and status == "open-unconfigured":
        logger.warning(
            "require_api_key_for_lan is true but GATEWAY_API_KEYS is not set — "
            "the gateway is OPEN to the LAN. Set GATEWAY_API_KEYS to enforce auth."
        )
    return status


def is_protected(path: str) -> bool:
    if path in _OPEN_EXACT:
        return False
    return path.startswith(_PROTECTED_PREFIXES)


def _extract_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def key_is_valid(presented: str) -> bool:
    ok = False
    for k in configured_keys():  # compare against all keys; no early exit
        ok |= hmac.compare_digest(presented.encode(), k.encode())
    return ok


async def api_key_middleware(request: Request, call_next):
    # Imported lazily to avoid a circular import with gateway.server.
    from gateway.server import load_security_config

    if request.method == "OPTIONS" or not is_protected(request.url.path):
        return await call_next(request)
    if auth_status(load_security_config().get("security", {})) != "enforced":
        return await call_next(request)
    client_host = request.client.host if request.client else ""
    if client_host in _LOOPBACK:
        return await call_next(request)
    presented = _extract_key(request)
    if key_is_valid(presented):
        request.state.client_id = "key:" + hashlib.sha256(presented.encode()).hexdigest()[:12]
        return await call_next(request)
    return JSONResponse(
        status_code=401,
        content={"error": {"message": "Missing or invalid API key", "type": "authentication_error", "code": "invalid_api_key"}},
        headers={"WWW-Authenticate": "Bearer"},
    )
