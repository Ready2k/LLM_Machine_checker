# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A lightweight Python gateway that exposes an OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`) and routes requests across a local LLM cluster (Ollama on a Windows/CUDA PC, LM Studio on a MacBook). Includes a web dashboard at `http://127.0.0.1:8080`, privacy enforcement, and telemetry.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure live backends (the committed backends.yaml targets LAN hostnames)
cp config/backends.local.yaml.example config/backends.local.yaml
# Edit backends.local.yaml if your node addresses differ
```

## Running

```bash
./manage.sh start       # background; dashboard at http://127.0.0.1:8080
./manage.sh stop
./manage.sh restart
./manage.sh test        # run pytest + check_backends.py

# Or directly:
python -m gateway.server
```

## Tests

```bash
pytest tests/                                # all tests
pytest tests/test_routing_policy.py -v      # single file
pytest tests/test_gateway_chat_completions.py -v
```

Most tests are offline/unit tests against the FastAPI app using `httpx`. `test_live_backends.py` hits real LAN nodes and is skipped automatically when they're unreachable.

## Architecture

```
gateway/
  server.py           # FastAPI app — all HTTP endpoints + startup config loading
  providers/
    base.py           # Abstract Provider + shared Pydantic models (ChatCompletionRequest, etc.)
    ollama.py         # Ollama provider (health, chat, models)
    lmstudio.py       # LM Studio provider
    openai_compatible.py  # Generic OpenAI-compatible provider
  policy/
    privacy.py        # Extracts privacy level from request; blocks disallowed routes
    routing.py        # resolve_model_to_route() + select_backend() — pure config logic
  telemetry/
    events.py         # log_routing_decision() → JSONL (metadata only, no prompt content)
  static/             # Dashboard HTML/CSS/JS (served from /)
config/
  backends.yaml       # Backend nodes (endpoints, roles, enable flags)
  routes.yaml         # Routes (local-fast, local-quality, private-docs, cloud-best)
  models.yaml         # Model aliases → route mappings
  security.yaml       # Privacy tier rules
inventory/
  machines.json       # Machine fleet inventory (populated by profile_machine.py)
```

**Request flow**: `POST /v1/chat/completions` → privacy check → `resolve_model_to_route()` → `select_backend()` → provider forwards to LAN node → telemetry logged → response returned.

**Config loading**: `server.py` loads all four YAML files at startup. `backends.local.yaml` (gitignored) overrides `backends.yaml` when present — use it for local node addresses without touching the committed config.

## Key Design Decisions

- Gateway binds to `127.0.0.1:8080` only (never `0.0.0.0`) — intentional per security requirements.
- `cloud-best` route is disabled by default (`status: not_configured`). Enabling it requires explicit config.
- Telemetry logs routing metadata to JSONL but never logs prompt content.
- `inference_enabled: false` on a backend prevents it from being selected even if `enabled: true` (used for the utility/admin node).

## Diagnostic Scripts

```bash
python scripts/check_backends.py   # health-check all configured backends
python scripts/smoke_gateway.py    # end-to-end smoke test against running gateway
python profile_machine.py          # profile the current machine and print recommended role
```
