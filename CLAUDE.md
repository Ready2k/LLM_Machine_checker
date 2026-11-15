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
  server.py           # FastAPI app — /v1 + dashboard endpoints, config loaders (mtime-cached)
  chat_helpers.py     # Router: /chat/extract, /chat/memory/extract, /chat/title
  api_observability.py  # Router: traces/logs/audit export (+ AGENT_SECRET resolution)
  providers/
    base.py           # Abstract Provider + shared Pydantic models (ChatCompletionRequest, etc.)
    ollama.py         # Ollama provider (health, chat, models)
    lmstudio.py       # LM Studio provider
    openai_compatible.py  # Generic OpenAI-compatible provider; build_payload() forwards passthrough params
  policy/
    privacy.py        # Extracts privacy level from request; blocks disallowed routes
    routing.py        # resolve_model_to_route() + select_backend(s)() — pure config logic
    auth.py           # API-key middleware (GATEWAY_API_KEYS); loopback trusted
    autoroute.py      # Rule-based prompt classifier for model: "auto"
  tools/
    websearch.py      # SearXNG search + SSRF-guarded fetch + untrusted-data grounding
  telemetry/
    events.py         # log_routing_decision() → SQLite (metadata + token counts, no prompt content)
  static/             # Dashboard HTML/CSS/JS (served from /)
config/
  backends.yaml       # Backend nodes (endpoints, roles, enable flags)
  routes.yaml         # Routes (local-fast, local-quality, private-docs, cloud-best)
  models.yaml         # Model aliases → route mappings
  security.yaml       # Privacy tier rules
inventory/
  machines.json       # Machine fleet inventory (populated by profile_machine.py)
```

**Request flow**: auth middleware → `POST /v1/chat/completions` → privacy check → (optional web grounding) → `resolve_model_to_route()` → `select_backends()` (primary, then fallback) → provider forwards to the first node that answers → telemetry logged (incl. token usage) → response returned.

**Config loading**: YAML/JSON configs are read through `_read_config_file()` in `server.py`, cached by (mtime, size) and deep-copied, so edits apply without a restart. `*.local.yaml` (gitignored) **replaces** the committed file entirely when present (not merged) — use `backends.local.yaml` for local node addresses. Model names may use `~`/`$VARS` (`resolve_target_model()`). Tests patch loaders/paths on `gateway.server` (`load_*_config`, `CONFIG_DIR`, `INVENTORY_PATH`), so keep those names there.

## Key Design Decisions

- Gateway binds to `0.0.0.0:8080` so it's reachable across the LAN (e.g. the `/chat/` UI from phones). Override with `GATEWAY_HOST` / `GATEWAY_PORT`. *(Historically localhost-only; opened up intentionally for the shared LAN chat terminal / Pi deployment.)*
- **HTTPS:** set `GATEWAY_SSL_CERT` + `GATEWAY_SSL_KEY` to serve TLS (required for browser voice/mic — a secure-context feature). `scripts/make_cert.sh` generates a self-signed cert; `manage.sh` auto-enables HTTPS when `certs/server.{crt,key}` exist. Certs are gitignored.
- **Auth:** `gateway/policy/auth.py` enforces `GATEWAY_API_KEYS` (comma-separated; `Authorization: Bearer` or `x-api-key`) on `/v1/*`, `/api/*` and the `/chat/*` helper POSTs for non-loopback clients. HTML shells, static files and `/api/health` stay open; `static/auth.js` adds the key to browser fetches and prompts on 401. **Fails open** (with a startup warning) if `require_api_key_for_lan` is true but no keys are set. Secrets live in `.env` (gitignored; template `.env.example`), loaded by `manage.sh` and the systemd unit. `AGENT_SECRET` is shared with node agents. The privacy tier is client-declared (`x-ai-privacy`) — routing policy, not authentication.
- **Failover:** `chat_completions` tries `select_backends()` in order and falls through on connect errors, timeouts and 5xx (never 4xx); streams fail over before the first chunk. Providers use a 5s connect timeout so a dead node fails over quickly. `x-ai-fallback-used` reports it.
- **Param passthrough:** `ChatCompletionRequest` keeps extra fields (`extra="allow"`); `PASSTHROUGH_PARAMS` in `providers/base.py` (response_format, tools, top_p, stop, seed, …) are forwarded by `build_payload()`.
- **Chat UI** at `/chat/` is a self-contained ES-module app (`gateway/static/chat/`) with conversation history + long-term memory stored **client-side in IndexedDB** — the gateway stays stateless about users (no per-user login; at most the shared API key above). `esc()` escapes quotes because its output is used in attributes; links go through `safeHref()` (http/https only). Spec: `docs/CHAT_HISTORY_MEMORY_SPEC.md`.
- **Mobile-first chat UI:** it's used mostly from phones over the LAN. Load-bearing patterns (documented in the spec's *Mobile & responsive UI* section): shell height is `var(--app-h, 100dvh)` where `--app-h` is set from `window.visualViewport` in `app.js` (keeps the composer above the soft keyboard); 16px form fields on mobile to stop iOS focus-zoom; touch row actions shown via `@media (hover: none)`; Enter sends only on non-`coarse` pointers; and the sidebar dismiss `#sb-backdrop` **must stay nested inside `#shell`** or the `#shell.sb-open #sb-backdrop` rule won't match and the overlay can't be tapped away.
- `cloud-best` route is disabled by default (`status: not_configured`). Enabling it requires explicit config.
- **Web grounding** (`gateway/tools/websearch.py`): the `web` / `auto-web` model alias, the `x-ai-web-search: true` header, or `metadata.web_search` trigger a retrieve-then-answer step — search a self-hosted SearXNG (`config/tools.yaml` / `SEARXNG_URL`), fetch the top results, and pass the extracted text to the model so it cites real sources instead of fabricating URLs. Fetched pages are **untrusted**: the rules go in the system message, the sources go in the last user turn inside `<web_sources>` tags (forged tags stripped), and fetches are SSRF-guarded (non-public IPs blocked, redirects re-checked per hop, size/content-type capped). Forced RAG, not native tool-calling — small local models reliably summarise provided text but unreliably *decide* to call tools. The `web` alias resolves to `auto` for route selection; sources return on the `x-ai-sources` response header (base64 JSON) and grounding state on `x-ai-grounding`. It degrades gracefully (never hard-fails the completion). **Privacy:** SearXNG keeps the query on the LAN but page fetches don't, so it's gated by `security.yaml → web_search.allowed_privacy` (public/personal by default). Chat UI: the 🌐 toggle. Deploy SearXNG via `deployment/searxng/` (JSON output must be enabled).
- Telemetry logs routing metadata (and token usage for non-streaming requests) to SQLite but never logs prompt content.
- `/v1/embeddings` goes to the backend flagged `embeddings: true` (default `macbook-m4pro`); errors map to 502.
- `inference_enabled: false` on a backend prevents it from being selected even if `enabled: true` (used for the utility/admin node).
- **Pi deployment:** `deployment/gateway-node/gateway.service` (systemd) runs it headless on boot; see `deployment/gateway-node/README.md`.

## Diagnostic Scripts

```bash
python scripts/check_backends.py   # health-check all configured backends
python scripts/smoke_gateway.py    # end-to-end smoke test against running gateway
python profile_machine.py          # profile the current machine and print recommended role
```
