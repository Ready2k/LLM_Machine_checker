# Observability Dashboard — Update Spec

> **Status:** original spec, partly superseded. Telemetry is now persisted to SQLite (`data/telemetry/observability.db`, see `gateway/telemetry/database.py`) and the Observability tab reads it via `/api/observability/*`. The JSONL file and `telemetry.path` described below are legacy.

## Overview

Extend the existing Local AI Fabric dashboard (`http://127.0.0.1:8080`) with a live observability panel covering request history, latency metrics, route stats, and a warning/error stream — similar in spirit to a lightweight OTEL viewer, but built entirely within the existing FastAPI + vanilla JS stack.

No new dependencies. No external services. Everything reads from the existing JSONL telemetry file.

---

## Current State (do not break)

- Dashboard has four sections: Fleet Overview, This Machine, Routing Map, Architecture
- Static files: `gateway/static/index.html`, `gateway/static/js/app.js`, `gateway/static/css/style.css`
- Existing API endpoints: `/api/health`, `/api/machines`, `/api/config/routes`, `/api/profile/current`, `/api/backends/health`
- Telemetry is written to `data/telemetry/gateway-events.jsonl` (one JSON object per line)
- Gateway runs at `127.0.0.1:8080` via `gateway/server.py` (FastAPI)

---

## Change 1 — Measure and record latency in the gateway

**File:** `gateway/server.py`

In the `POST /v1/chat/completions` handler, record wall-clock latency around the provider call and pass it to `log_routing_decision`.

For non-streaming requests, measure total round-trip time from just before `provider.chat_completions()` to just after.

For streaming requests, latency cannot be measured at log time (the stream is still open). Log `latency_ms: null` for streaming — this is acceptable.

**File:** `gateway/telemetry/events.py`

The `log_routing_decision` function already accepts `ttft_ms` and `latency_ms` parameters but they are never populated. No changes needed to the function signature — just ensure the caller passes real values.

Also fix the `error_code` field: currently streaming events log `error_code: "stream"` which is misleading. Change the caller in `server.py` to log `error_code: null` and add a separate `mode: "stream"` or `stream: true` field to the event. *(This requires adding `stream` as an optional field to the event dict in `log_routing_decision`.)*

---

## Change 2 — New API endpoint: `/api/events`

**File:** `gateway/server.py`

Add a new GET endpoint that reads the JSONL telemetry file and returns the last N events as JSON.

```
GET /api/events?limit=100&status=all
```

**Query params:**
- `limit` — integer, default 100, max 500
- `status` — `all` | `success` | `error` — filter by status field

**Response:**
```json
{
  "events": [ ...array of event objects, newest first... ],
  "total_on_disk": 342,
  "returned": 100
}
```

**Implementation notes:**
- Read the JSONL file, parse each line as JSON, skip malformed lines silently
- Return newest-first (reverse order)
- If the file doesn't exist, return `{"events": [], "total_on_disk": 0, "returned": 0}`
- Do not cache — read fresh on each request

---

## Change 3 — New API endpoint: `/api/stats`

**File:** `gateway/server.py`

Aggregate stats computed from the full JSONL file.

```
GET /api/stats
```

**Response:**
```json
{
  "total_requests": 42,
  "success_rate_pct": 95.2,
  "error_count": 2,
  "fallback_count": 1,
  "fallback_rate_pct": 2.4,
  "requests_by_route": {
    "local-fast": 28,
    "local-quality": 14
  },
  "requests_by_backend": {
    "pc-rtx2070": 28,
    "macbook-m4pro": 14
  },
  "avg_latency_ms_by_backend": {
    "pc-rtx2070": 312,
    "macbook-m4pro": 890
  },
  "p95_latency_ms_by_backend": {
    "pc-rtx2070": 520,
    "macbook-m4pro": 1340
  },
  "privacy_breakdown": {
    "personal": 38,
    "confidential": 4
  },
  "stream_count": 10,
  "period_start": "2026-06-14T17:55:40+00:00",
  "period_end": "2026-06-15T18:10:07+00:00"
}
```

**Implementation notes:**
- Latency averages/p95 should only include events where `latency_ms` is not null
- Compute p95 by sorting the latency list and taking the value at the 95th percentile index
- If no latency data exists yet (all null), omit those fields or return null

---

## Change 4 — Dashboard UI: Observability section

Add a new fifth section to `gateway/static/index.html` between the Routing Map and Architecture sections:

```html
<!-- ============ OBSERVABILITY ============ -->
<section class="fade-in" id="observability-section">
  <h2 class="section-title"><span class="icon">📊</span> Observability</h2>
  <!-- Stats bar, latency chart, event log rendered by JS -->
  <div id="observability-panel"></div>
</section>
```

---

## Change 5 — Dashboard JS: render the observability panel

**File:** `gateway/static/js/app.js`

Add the following to the existing JS (do not rewrite the file — append/extend):

### 5a — Stats bar

Fetch `/api/stats` and render a horizontal strip of metric chips above the event log:

```
Total: 42  |  Success: 95.2%  |  Errors: 2  |  Fallbacks: 1  |  Streams: 10
Routes: local-fast 28  local-quality 14
Avg latency: pc-rtx2070 312ms  macbook-m4pro 890ms
```

Style consistently with existing `.glass-card-static` and badge/chip patterns already in the CSS.

### 5b — Latency sparkline per backend

A simple SVG sparkline (no library) showing the last 50 `latency_ms` values per backend over time. One sparkline row per backend that has latency data. Skip backends with all-null latency.

Draw as an inline `<svg>` with a polyline. Use the existing CSS colour variables for the line colour. Label each row with the backend id and current avg latency.

### 5c — Event log table

Fetch `/api/events?limit=50` and render a scrollable table (max height ~400px, overflow-y scroll):

| Time | Route | Backend | Model | Privacy | Latency | Status |
|---|---|---|---|---|---|---|
| 18:09:36 | local-quality | macbook-m4pro | local-quality | personal | 312ms | ✓ |

**Row colouring:**
- `status: success` — default (no highlight)
- `status: error` — red-tinted row (use existing error colour vars)
- `fallback_used: true` — amber-tinted row
- `stream: true` or `error_code: "stream"` — small `[stream]` badge in Status column

**Timestamp:** display as `HH:MM:SS` local time (convert from UTC ISO string).

**Latency cell:** display as `312ms` if present, `—` if null.

### 5d — Auto-refresh

The observability panel should refresh on the same cycle as the rest of the dashboard (existing auto-refresh toggle). Add `fetchObservability()` to the existing `refreshAll()` function.

---

## Change 6 — CSS additions

**File:** `gateway/static/css/style.css`

Add styles for:
- `.obs-stats-bar` — flex row, wraps on small screens, gap between chips
- `.obs-stat-chip` — consistent with existing `.badge` style, shows label + value
- `.obs-sparkline-row` — flex row, label left, SVG right, subtle separator
- `.obs-event-table` — full-width table, monospace font for timestamps/latency, compact rows
- `.obs-event-table tr.status-error` — red tint background
- `.obs-event-table tr.fallback-used` — amber tint background
- `.badge--stream` — small inline badge, blue-tint, for stream indicator

Match the existing dark theme (background `#0a0e1a`, glass cards, existing colour variables).

---

## File change summary

| File | Change |
|---|---|
| `gateway/server.py` | Measure latency; add `/api/events` and `/api/stats` endpoints; fix stream event logging |
| `gateway/telemetry/events.py` | Accept and write `stream` boolean field to event dict |
| `gateway/static/index.html` | Add observability section HTML |
| `gateway/static/js/app.js` | Add stats bar, sparklines, event log, wire into refresh cycle |
| `gateway/static/css/style.css` | Add observability component styles |

---

## Constraints

- No npm, no build step, no external JS libraries — vanilla JS only
- No new Python packages — use only what's already in `requirements.txt` (fastapi, uvicorn, pydantic, PyYAML, httpx)
- Do not modify existing API endpoints or their response shapes
- Do not log prompt content — the telemetry spec explicitly forbids it (`log_prompts: false`)
- The JSONL file path comes from `security.yaml` → `telemetry.path` — use the existing `_load_telemetry_config()` helper, do not hardcode the path
- Gateway must restart cleanly after changes (`./manage.sh restart`)
- All existing tests in `tests/` must continue to pass
