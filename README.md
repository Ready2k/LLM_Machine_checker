# Local AI Fabric

A lightweight Python gateway and machine profiling system for hybrid local LLM routing. The Local AI Fabric allows you to run multiple inference nodes (e.g., MacBooks with LM Studio, Windows PCs with Ollama) and automatically route API requests to the best available local model, enforcing privacy policies to keep sensitive data off the cloud.

## Features

- **Hardware Auto-Detection**: Profile hostnames, OS, CPU, RAM, and GPU to recommend machine roles.
- **Smart Routing**: Route requests to `local-fast`, `local-quality`, or `private-docs` depending on need.
- **Privacy Enforcement**: Block sensitive code or private documents from ever reaching the cloud via configurable privacy tiers (`public`, `personal`, `confidential`, `restricted`). The tier is declared by the client (`x-ai-privacy` header or `metadata.privacy`) — it's a routing policy, not authentication.
- **Automatic Failover**: Each route has a primary and a fallback backend. If the primary is unreachable, times out (5s connect) or returns a 5xx, the request is retried on the fallback — including streams, before the first token. `x-ai-fallback-used` reports it. 4xx errors are not retried.
- **API-Key Auth**: Set `GATEWAY_API_KEYS` to require a key from non-loopback clients (see [Security](#security)).
- **Web Grounding**: Use the `web` model alias, the `x-ai-web-search: true` header, or the chat UI's 🌐 toggle to search a self-hosted SearXNG, fetch the top pages, and answer from cited sources (see [Web grounding](#web-grounding)).
- **Telemetry & Monitoring**: Log metadata of all routing decisions (route, backend, latency, token usage — including streamed requests) to SQLite (`data/telemetry/observability.db`) without logging sensitive prompts.
- **Unified API**: Exposes an OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoint for drop-in integration with Open WebUI, Cursor, and VS Code.
- **Web Chat UI**: A self-contained, mobile-friendly chat terminal at `/chat/` with conversation history and long-term memory stored client-side (IndexedDB) — no login, nothing user-specific stored on the gateway.

## Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Backends**:
   ```bash
   cp config/backends.local.yaml.example config/backends.local.yaml
   ```
   Edit `config/backends.local.yaml` to point to your live nodes. It is gitignored and **replaces** `backends.yaml` entirely when present. Supported runtimes: `ollama`, `lmstudio`, `openai_compatible` (e.g. mlx-lm / mlx.studio).

3. **Set secrets**:
   ```bash
   cp .env.example .env     # then fill in GATEWAY_API_KEYS and AGENT_SECRET
   ```

4. **Run the Gateway**:
   ```bash
   ./manage.sh start        # loads .env; or: python -m gateway.server
   ```
   Binds to `0.0.0.0:8080` (override with `GATEWAY_HOST` / `GATEWAY_PORT`). Serves HTTPS automatically if `certs/server.{crt,key}` exist (`scripts/make_cert.sh`).

5. **View Dashboard**:
   Open `http://127.0.0.1:8080` in your browser to view the live health and architecture dashboard.

## Security

- **API keys:** with `GATEWAY_API_KEYS` set (comma-separated), requests to `/v1/*`, `/api/*` and the `/chat/*` helper endpoints from non-loopback clients need `Authorization: Bearer <key>` or `x-api-key`. Loopback is always trusted; the HTML pages, static files and `/api/health` stay open. Browsers are prompted for the key once and keep it in localStorage. If `require_api_key_for_lan` is true but no key is set, the gateway runs **open** and logs a warning.
- **Per-key privacy floor:** a key can carry a minimum tier — `GATEWAY_API_KEYS=phonekey:personal,laptopkey`. A request using that key is raised to at least that sensitivity (never lowered), so a shared device key can't declare `public` to reach routes meant for less sensitive data. The effective tier is returned in `x-ai-policy`.
- **Limits** (`security.yaml → limits`): `max_body_bytes` (default 20 MiB, 413 above it, based on Content-Length), `rate_limit_per_minute` (default off; per API key or client IP, 429 + `Retry-After`, loopback exempt) and `max_tokens_cap` (default off; clamps or applies `max_tokens`).
- **Agent secret:** set `AGENT_SECRET` (same value on the gateway and each node agent). Without it, the insecure built-in default is used and a warning is logged; set `AGENT_STRICT=1` on a node agent to make it refuse to start on the default.
- **Cloud:** the `cloud-best` route is disabled unless explicitly configured.
- **Prompts** are never logged; telemetry holds routing metadata only.

## Web grounding

Configure `config/tools.yaml` (or `SEARXNG_URL`) to point at a SearXNG node with JSON output enabled (`deployment/searxng/`). Search queries stay on your LAN; **page fetches go to the open web**, so grounding is only allowed for tiers listed in `security.yaml → web_search.allowed_privacy` (default `public`, `personal`). Fetches are SSRF-guarded: each request and redirect hop resolves the host once, rejects private/loopback/link-local addresses and connects to that exact IP (so DNS rebinding can't swap it), while TLS still verifies the real hostname. Responses are size-capped and fetches ignore `HTTP(S)_PROXY` (a proxy would bypass the check), so an egress proxy isn't supported for page fetches. Fetched text is passed to the model as untrusted data in the user turn. Response headers: `x-ai-grounding` (status) and `x-ai-sources` (base64 JSON).

## Configuration

`config/*.yaml` is cached and reloaded automatically when a file changes. `config/*.local.yaml` (gitignored) takes precedence. Model names in `models.yaml` may use `~` / `$VARS` (e.g. `~/.cache/huggingface/...`). Per-backend options: `stream_usage` (ask streaming backends for a usage chunk so token counts are recorded; default on for `ollama`/`lmstudio`, set `true` for an `openai_compatible` server that supports `stream_options.include_usage`) and `embeddings: true` (serve `/v1/embeddings`). Backend HTTP connections are pooled and reused.

## Key Endpoints

- `GET /` - Admin dashboard.
- `GET /chat/` - Mobile-friendly web chat UI (history + memory stored client-side).
- `GET /api/backends/health` - Live health check of all configured inference nodes.
- `GET /v1/models` - Lists available model aliases (`auto`, `web`, `local-fast`, `local-quality`, `private-docs`, ...).
- `POST /v1/chat/completions` - OpenAI-compatible inference endpoint. Besides `messages`, `temperature` and `max_tokens`, these are forwarded to the backend when sent: `response_format`, `tools`, `tool_choice`, `top_p`, `top_k`, `min_p`, `stop`, `seed`, `n`, penalties, `logit_bias`, `logprobs`, `stream_options`.
- `POST /v1/embeddings` - Pass-through to the backend flagged `embeddings: true` in `backends.yaml` (defaults to `macbook-m4pro`).

## Documentation

- [Open WebUI Setup Guide](docs/OPEN_WEBUI_SETUP.md)
- [Gateway Specification](docs/LOCAL_AI_FABRIC_SPEC.md)
- [Web Dashboard Implementation Plan](docs/WEB_DASHBOARD_IMPLEMENTATION_PLAN.md)
- [Chat History & Memory Spec](docs/CHAT_HISTORY_MEMORY_SPEC.md)

## Tests

```bash
pytest tests/        # offline; test_live_backends.py skips when LAN nodes are unreachable
```

## Diagnostics

Use the built-in scripts to verify your cluster health:

```bash
python scripts/check_backends.py
python scripts/smoke_gateway.py
```
