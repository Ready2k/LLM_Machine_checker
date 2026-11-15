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
  server.py           # FastAPI app — /v1 endpoints, backend health, config loaders (mtime-cached)
  chat_helpers.py     # Router: /chat/extract, /chat/memory/extract, /chat/title
  api_dashboard.py    # Router: /api/health|machines|config/routes|profile/*|events|stats + inventory loader
  api_observability.py  # Router: traces/logs/audit export (+ AGENT_SECRET resolution)
  api_router.py       # Router: /api/router/feedback|stats|decisions — auto-router misroute reports + routing observability
  providers/
    base.py           # Abstract Provider + shared Pydantic models (ChatCompletionRequest, etc.)
    ollama.py         # Ollama provider (health, chat, models)
    lmstudio.py       # LM Studio provider
    openai_compatible.py  # Generic OpenAI-compatible provider; pooled client; build_payload(); shared health probe
    sse.py            # UsageTap: reads token usage out of SSE streams (and hides an injected usage chunk)
  policy/
    privacy.py        # Extracts privacy level from request; blocks disallowed routes
    routing.py        # resolve_model_to_route() + select_backend(s)() — pure config logic
    auth.py           # API-key middleware (GATEWAY_API_KEYS, optional key:tier privacy floor); loopback trusted
    limits.py         # Body-size cap, per-client rate limit, max_tokens cap (security.limits)
    autoroute.py      # Multi-signal classifier for model: "auto" (privacy tiers + weighted complexity score; design note in the file)
  tools/
    websearch.py      # SearXNG search + IP-pinned SSRF-guarded fetch + untrusted-data grounding
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

**Config loading**: YAML/JSON configs are read through `_read_config_file()` in `server.py`, cached by (mtime, size) and deep-copied, so edits apply without a restart. `*.local.yaml` (gitignored) **replaces** the committed file entirely when present (not merged) — use `backends.local.yaml` for local node addresses. Model names may use `~`/`$VARS` (`resolve_target_model()`). Tests patch loaders/paths on `gateway.server` (`load_*_config`, `CONFIG_DIR`, `INVENTORY_PATH`, `PROFILER_PATH`, `_cached_profile`, `get_db`, `subprocess`), so keep those names there; routers split out of `server.py` (`api_dashboard.py`, `chat_helpers.py`) reach them through the server module at call time.

## Key Design Decisions

- Gateway binds to `0.0.0.0:8080` so it's reachable across the LAN (e.g. the `/chat/` UI from phones). Override with `GATEWAY_HOST` / `GATEWAY_PORT`. *(Historically localhost-only; opened up intentionally for the shared LAN chat terminal / Pi deployment.)*
- **HTTPS:** set `GATEWAY_SSL_CERT` + `GATEWAY_SSL_KEY` to serve TLS (required for browser voice/mic — a secure-context feature). `scripts/make_cert.sh` generates a self-signed cert; `manage.sh` auto-enables HTTPS when `certs/server.{crt,key}` exist. Certs are gitignored.
- **Auth:** `gateway/policy/auth.py` enforces `GATEWAY_API_KEYS` (comma-separated; `Authorization: Bearer` or `x-api-key`) on `/v1/*`, `/api/*` and the `/chat/*` helper POSTs for non-loopback clients. HTML shells, static files and `/api/health` stay open; `static/auth.js` adds the key to browser fetches and prompts on 401. **Fails open** (with a startup warning) if `require_api_key_for_lan` is true but no keys are set. Secrets live in `.env` (gitignored; template `.env.example`), loaded by `manage.sh` and the systemd unit. `AGENT_SECRET` is shared with node agents. The privacy tier is client-declared (`x-ai-privacy`) — routing policy, not authentication — but a key written `key:tier` sets a floor that raises the declared tier (`apply_privacy_floor`; effective tier shown in `x-ai-policy`). Middleware order matters: auth is outermost (added last) so unauthenticated requests never reach `limits_middleware`.
- **Limits:** `security.limits` — `max_body_bytes` (default 20 MiB, Content-Length based), `rate_limit_per_minute` and `max_tokens_cap` (both default off; loopback exempt from rate limiting).
- **Failover:** `chat_completions` tries `select_backends()` in order and falls through on connect errors, timeouts and 5xx (never 4xx); streams fail over before the first chunk. Providers share one pooled httpx client per event loop (`get_client()`) with a 5s connect timeout, so a dead node fails over quickly. `x-ai-fallback-used` reports it.
- **Param passthrough:** `ChatCompletionRequest` keeps extra fields (`extra="allow"`); `PASSTHROUGH_PARAMS` in `providers/base.py` (response_format, tools, top_p, stop, seed, …) are forwarded by `build_payload()`.
- **Chat UI** at `/chat/` is a self-contained ES-module app (`gateway/static/chat/`) with conversation history + long-term memory stored **client-side in IndexedDB** — the gateway stays stateless about users (no per-user login; at most the shared API key above). `esc()` escapes quotes because its output is used in attributes; links go through `safeHref()` (http/https only). Spec: `docs/CHAT_HISTORY_MEMORY_SPEC.md`.
- **Mobile-first chat UI:** it's used mostly from phones over the LAN. Load-bearing patterns (documented in the spec's *Mobile & responsive UI* section): shell height is `var(--app-h, 100dvh)` where `--app-h` is set from `window.visualViewport` in `app.js` (keeps the composer above the soft keyboard); 16px form fields on mobile to stop iOS focus-zoom; touch row actions shown via `@media (hover: none)`; Enter sends only on non-`coarse` pointers; and the sidebar dismiss `#sb-backdrop` **must stay nested inside `#shell`** or the `#shell.sb-open #sb-backdrop` rule won't match and the overlay can't be tapped away.
- **Auto-routing** (`gateway/policy/autoroute.py`, tuned via optional `config/autoroute.yaml`): pure-Python, deterministic, ~0.05 ms. Privacy header → content privacy scan (shape-based secret/PII detectors everywhere; ambiguous words like `private`/`password` count in prose but not as code tokens or benign phrases; `privacy_mode: strict` restores any-keyword matching) → weighted complexity score on the last user message vs `quality_threshold`. The reason (`x-ai-auto-reason`, `router.auto` event) lists signal names/weights only — never matched text. An LLM tiebreak for close calls exists but is off by default. Misroutes get fixed by adding the prompt to the corpus in `tests/test_autoroute.py` and adjusting weights/terms.
- **Auto-router feedback loop** (`gateway/api_router.py`, `scripts/autoroute_feedback.py`): the chat UI's *wrong route?* and the dashboard's Auto-Router tab `POST /api/router/feedback` (keyed by `x-correlation-id`) into the `router_feedback` SQLite table; `./manage.sh feedback list|promote|dismiss` appends cases between the `feedback-corpus-begin/end` markers in `tests/test_autoroute.py` (keep those markers). The request path is not involved — the endpoints only read the existing `router.auto` events joined to `llm_requests`, so routing stays deterministic. Prompt text is stored only when explicitly sent, `telemetry.feedback_store_prompts` isn't false and the private route isn't involved; it's nulled on promote/dismiss and never returned by the API. `/api/router/stats` (1h/24h/7d split, p50/p95, tokens) and `/api/router/decisions` (`candidates=true` = low-confidence) back the dashboard tab.
- `cloud-best` route is disabled by default (`status: not_configured`). Enabling it requires explicit config.
- **Web grounding** (`gateway/tools/websearch.py`): the `web` / `auto-web` model alias, the `x-ai-web-search: true` header, or `metadata.web_search` trigger a retrieve-then-answer step — search a self-hosted SearXNG (`config/tools.yaml` / `SEARXNG_URL`), fetch the top results, and pass the extracted text to the model so it cites real sources instead of fabricating URLs. Fetched pages are **untrusted**: the rules go in the system message, the sources go in the last user turn inside `<web_sources>` tags (forged tags stripped), and fetches are SSRF-guarded: `PinnedTransport` resolves once per request/redirect hop, rejects non-public IPs and connects to that IP (Host/SNI keep the real name), with `trust_env=False` so env proxies can't bypass it; size/content-type capped. Forced RAG, not native tool-calling — small local models reliably summarise provided text but unreliably *decide* to call tools. The `web` alias resolves to `auto` for route selection; sources return on the `x-ai-sources` response header (base64 JSON) and grounding state on `x-ai-grounding`. It degrades gracefully (never hard-fails the completion). **Privacy:** SearXNG keeps the query on the LAN but page fetches don't, so it's gated by `security.yaml → web_search.allowed_privacy` (public/personal by default). Chat UI: the 🌐 toggle. Deploy SearXNG via `deployment/searxng/` (JSON output must be enabled).
- Telemetry logs routing metadata and token usage to SQLite but never logs prompt content. Streams get usage via `stream_options.include_usage` (per-backend `stream_usage`, default on for ollama/lmstudio); `UsageTap` removes the injected usage-only chunk unless the client asked for it.
- `/v1/embeddings` goes to the backend flagged `embeddings: true` (default `macbook-m4pro`); errors map to 502.
- `inference_enabled: false` on a backend prevents it from being selected even if `enabled: true` (used for the utility/admin node).
- **Pi deployment:** `deployment/gateway-node/gateway.service` (systemd) runs it headless on boot; see `deployment/gateway-node/README.md`.

## Diagnostic Scripts

```bash
python scripts/check_backends.py   # health-check all configured backends
python scripts/smoke_gateway.py    # end-to-end smoke test against running gateway
python profile_machine.py          # profile the current machine and print recommended role
```
