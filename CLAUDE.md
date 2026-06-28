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

- Gateway binds to `0.0.0.0:8080` so it's reachable across the LAN (e.g. the `/chat/` UI from phones). Override with `GATEWAY_HOST` / `GATEWAY_PORT`. *(Historically localhost-only; opened up intentionally for the shared LAN chat terminal / Pi deployment.)*
- **HTTPS:** set `GATEWAY_SSL_CERT` + `GATEWAY_SSL_KEY` to serve TLS (required for browser voice/mic — a secure-context feature). `scripts/make_cert.sh` generates a self-signed cert; `manage.sh` auto-enables HTTPS when `certs/server.{crt,key}` exist. Certs are gitignored.
- **Chat UI** at `/chat/` is a self-contained ES-module app (`gateway/static/chat/`) with conversation history + long-term memory stored **client-side in IndexedDB** — the gateway stays stateless about users (no login on the shared LAN endpoint). Spec: `docs/CHAT_HISTORY_MEMORY_SPEC.md`.
- **Mobile-first chat UI:** it's used mostly from phones over the LAN. Load-bearing patterns (documented in the spec's *Mobile & responsive UI* section): shell height is `var(--app-h, 100dvh)` where `--app-h` is set from `window.visualViewport` in `app.js` (keeps the composer above the soft keyboard); 16px form fields on mobile to stop iOS focus-zoom; touch row actions shown via `@media (hover: none)`; Enter sends only on non-`coarse` pointers; and the sidebar dismiss `#sb-backdrop` **must stay nested inside `#shell`** or the `#shell.sb-open #sb-backdrop` rule won't match and the overlay can't be tapped away.
- `cloud-best` route is disabled by default (`status: not_configured`). Enabling it requires explicit config.
- **Web grounding** (`gateway/tools/websearch.py`): the `web` / `auto-web` model alias, the `x-ai-web-search: true` header, or `metadata.web_search` trigger a retrieve-then-answer step — search a self-hosted SearXNG (`config/tools.yaml` / `SEARXNG_URL`), fetch the top results, and inject the extracted text as a strict system message so the model cites real sources instead of fabricating URLs. Forced RAG, not native tool-calling — small local models reliably summarise provided text but unreliably *decide* to call tools. The `web` alias resolves to `auto` for route selection; sources return on the `x-ai-sources` response header (base64 JSON) and grounding state on `x-ai-grounding`. It degrades gracefully (never hard-fails the completion). **Privacy:** SearXNG keeps the query on the LAN but page fetches don't, so it's gated by `security.yaml → web_search.allowed_privacy` (public/personal by default). Chat UI: the 🌐 toggle. Deploy SearXNG via `deployment/searxng/` (JSON output must be enabled).
- Telemetry logs routing metadata to JSONL/SQLite but never logs prompt content.
- `inference_enabled: false` on a backend prevents it from being selected even if `enabled: true` (used for the utility/admin node).
- **Pi deployment:** `deployment/gateway-node/gateway.service` (systemd) runs it headless on boot; see `deployment/gateway-node/README.md`.

## Diagnostic Scripts

```bash
python scripts/check_backends.py   # health-check all configured backends
python scripts/smoke_gateway.py    # end-to-end smoke test against running gateway
python profile_machine.py          # profile the current machine and print recommended role
```
