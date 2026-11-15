# Local AI Fabric — Web Dashboard Implementation Plan

Status: Draft v0.1  
Owner: James Cregeen  
Repository: `Ready2k/LLM_Machine_checker`  
Related spec: `docs/LOCAL_AI_FABRIC_SPEC.md`  
Created: 2026-06-14

> **Status:** historical plan. The gateway now binds `0.0.0.0` by default (override with `GATEWAY_HOST`) so the `/chat/` UI works from phones, and non-loopback access is protected by API keys (`GATEWAY_API_KEYS`) — the "localhost-only by default" guidance below no longer applies. See `README.md` → Security.

## 1. Scope decision

This phase builds the first user-facing web dashboard for the Local AI Fabric system.

It is **not** the chat interface. The main chat UI remains Open WebUI unless later evidence proves a custom chat surface is needed. This dashboard is the admin and status command centre described by the specification:

- Admin status page for node health and model availability.
- Benchmark dashboard.
- Routing visibility.
- Fleet inventory viewer.

The dashboard becomes the first interactive layer of the project and establishes the FastAPI structure that the gateway will later extend.

## 2. Product goal

Create a browser-based command centre where James can see:

1. Machine fleet.
2. Benchmark results.
3. Routing configuration.
4. Runtime readiness.
5. Node health.
6. Risk indicators.
7. Architecture view.
8. Current host profile.

This gives the project something useful before the full OpenAI-compatible gateway exists. Tiny miracle, really: useful before over-engineered.

## 3. Architecture position

```text
Browser dashboard
       |
       v
FastAPI admin/status service
       |
       +--> inventory/machines.json
       +--> config/*.yaml or config/*.json
       +--> profile_machine.py for local profiling only
       +--> future gateway health and route APIs
```

This dashboard should eventually sit beside or inside the gateway service, but it must not block Milestone 3 gateway work.

## 4. Tech stack

Backend:

- Python 3.12.
- FastAPI.
- Uvicorn.
- Pydantic, recommended for response models and config validation.
- PyYAML, optional if route/config files use YAML.

Frontend:

- Vanilla HTML.
- Vanilla CSS.
- Vanilla JavaScript.
- No frontend framework in this phase.

Reasoning:

- Lightweight.
- Easy to run on any node.
- Useful on the old MacBook if it can host basic services.
- Avoids creating a frontend build chain before the backend exists, because apparently suffering can be deferred.

## 5. Proposed project structure

```text
LLM_Machine_checker/
  docs/
    LOCAL_AI_FABRIC_SPEC.md
    WEB_DASHBOARD_IMPLEMENTATION_PLAN.md
  inventory/
    machines.json
  profile_machine.py
  gateway/
    __init__.py
    server.py
    static/
      index.html
      css/
        style.css
      js/
        app.js
  tests/
    test_dashboard_api.py
    test_inventory_loading.py
    test_profile_runner.py
  requirements.txt
  .gitignore
  README.md
```

## 6. Backend implementation

### 6.1 New file: `gateway/server.py`

FastAPI application responsible for:

1. Serving the dashboard static frontend.
2. Reading `inventory/machines.json`.
3. Returning machine inventory and individual machine detail.
4. Returning dashboard health.
5. Returning route configuration if present.
6. Running the profiler on the current host when explicitly requested.

### 6.2 Required endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves dashboard HTML. |
| `GET` | `/api/health` | Returns dashboard service health. |
| `GET` | `/api/machines` | Returns full machine inventory from `inventory/machines.json`. |
| `GET` | `/api/machines/{machine_id}` | Returns single machine details. |
| `GET` | `/api/profile/current` | Returns current host profile if cached or available. |
| `POST` | `/api/profile/run` | Runs `profile_machine.py` on the current host and returns results. |
| `GET` | `/api/config/routes` | Returns routing configuration or derived routing from inventory. |

### 6.3 Important backend changes to the original plan

The original plan listed only `POST /api/profile/run`. Add `GET /api/profile/current` as a safe read endpoint.

`POST /api/profile/run` must be treated as an admin action because it executes a local script. We are not building a browser button that runs arbitrary local commands because that is how home labs become cautionary tales.

### 6.4 Static serving

The service should mount static assets from:

```text
gateway/static/
```

`GET /` should return `gateway/static/index.html`.

### 6.5 Running locally

Support both:

```bash
python -m uvicorn gateway.server:app --reload --port 8080
```

and:

```bash
python gateway/server.py
```

Use:

```python
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("gateway.server:app", host="127.0.0.1", port=8080, reload=True)
```

Default bind address must be `127.0.0.1`. LAN binding should be explicit.

## 7. Profiler execution rules

`POST /api/profile/run` must follow these rules:

1. Only run the known local `profile_machine.py` file.
2. Use `subprocess.run()` with an explicit argument list.
3. Never use `shell=True`.
4. Use a timeout.
5. Capture stdout and stderr.
6. Parse JSON output where possible.
7. Return structured errors.
8. Do not overwrite inventory automatically unless an explicit future admin endpoint is added.
9. Add API key or admin token before exposing beyond localhost.

Example safe execution shape:

```python
result = subprocess.run(
    [sys.executable, str(PROJECT_ROOT / "profile_machine.py")],
    cwd=PROJECT_ROOT,
    capture_output=True,
    text=True,
    timeout=120,
    check=False,
)
```

## 8. Frontend implementation

### 8.1 New file: `gateway/static/index.html`

Single-page dashboard with:

1. Header.
2. Fleet overview.
3. This Machine panel.
4. Routing map.
5. Architecture diagram.
6. Footer.

### 8.2 Header

Display:

- Product name: `Local AI Fabric`.
- System status indicator.
- Last refresh timestamp.
- Auto-refresh toggle.

Status indicator rules:

| State | Meaning |
|---|---|
| Green | Inventory loaded and at least one inference node enabled. |
| Amber | Inventory loaded but one or more warnings exist. |
| Red | Inventory/API failed. |

### 8.3 Fleet overview

Card grid showing each machine from `inventory/machines.json`:

- Machine name.
- Hostname.
- Role badge.
- Compatibility status.
- RAM/VRAM gauges when data is available.
- Recommended model.
- Tokens/sec.
- Risk indicator.
- Routing status.
- Preferred routes.
- Notes.

### 8.4 This Machine panel

Show:

- Current host profile, when available.
- Re-profile button.
- Benchmark summary.
- Runtime readiness indicators.
- Profiler errors, if any.

Runtime indicators:

- Ollama.
- LM Studio.
- Docker.
- CUDA.
- Metal.
- Python version.

### 8.5 Routing map

Render route to machine mapping from either `config/routes.*` or derived inventory data.

Initial route names:

- `local-fast` -> RTX 2070 PC.
- `local-quality` -> MacBook M4 Pro.
- `private-docs` -> MacBook M4 Pro, fallback PC.
- `cloud-best` -> disabled unless configured.

The old MacBook must render as `utility_node`, not as an inference route target, based on the latest benchmark.

### 8.6 Architecture diagram

Render the spec architecture as an SVG or HTML diagram:

```text
Users / Tools / IDEs / Apps
          |
          v
   AI Gateway / Router
          |
 +--------+---------+---------+
 |        |         |         |
Mac M4   RTX PC    Old Mac   Cloud
Quality  Fast      Utility   Explicit fallback
```

## 9. Frontend styling

### 9.1 New file: `gateway/static/css/style.css`

Use a dark, premium technical dashboard style.

Design tokens:

```css
--bg-0: #0a0e1a;
--bg-1: #141b2d;
--primary: #00d4ff;
--secondary: #7c3aed;
--success: #22c55e;
--warning: #f59e0b;
--danger: #ef4444;
```

Style requirements:

1. Dark theme.
2. Glassmorphism cards.
3. Subtle borders.
4. Responsive CSS grid.
5. Animated conic-gradient gauges.
6. Pulsing status indicators.
7. Hover lift effects.
8. Fade-in on load.
9. Monospace for technical values.

### 9.2 Font dependency note

The original plan referenced Inter from Google Fonts. For local/private tooling, prefer a system font stack initially to avoid external calls:

```css
font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
```

A self-hosted font can be added later. Do not depend on Google Fonts for the first local dashboard.

## 10. Frontend logic

### 10.1 New file: `gateway/static/js/app.js`

Responsibilities:

1. Fetch `/api/machines` on load.
2. Render machine cards dynamically.
3. Render routing map.
4. Render architecture diagram.
5. Animate gauge fills with `requestAnimationFrame`.
6. Handle re-profile button.
7. Support auto-refresh toggle with 30 second interval.
8. Handle API error states.
9. Show last refresh timestamp.

### 10.2 Re-profile button behaviour

1. Disable button while running.
2. Show progress state.
3. Call `POST /api/profile/run`.
4. Render result in This Machine panel.
5. Do not automatically overwrite `inventory/machines.json` in this phase.
6. Show copy/download result option if useful.

## 11. Configuration handling

### 11.1 Current source of truth

Initial source:

```text
inventory/machines.json
```

This already contains:

- MacBook M4 Pro quality node.
- RTX 2070 PC fast node.
- Old MacBook utility node.

### 11.2 Config routes endpoint

`GET /api/config/routes` should:

1. Read `config/routes.yaml` if present.
2. Else read `config/routes.json` if present.
3. Else derive simple routes from `inventory/machines.json`.

This prevents the dashboard from failing just because the future config system has not arrived yet, which is rude but common.

## 12. Requirements

### 12.1 `requirements.txt`

Minimum:

```text
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic>=2.8.0
```

Optional if YAML configs are implemented immediately:

```text
PyYAML>=6.0.0
```

Test dependencies can either live here initially or in a later `requirements-dev.txt`:

```text
pytest>=8.0.0
httpx>=0.27.0
```

## 13. Git ignore updates

Add entries aligned to the spec security model:

```gitignore
.env
.env.local
secrets/
data/private/
node_modules/
__pycache__/
.pytest_cache/
.venv/
venv/
llm-bench-env/
```

## 14. Security requirements for this phase

1. Default host must be `127.0.0.1`.
2. LAN binding must be explicit.
3. `POST /api/profile/run` is local/admin only.
4. No arbitrary command execution.
5. No prompt or private document handling in this phase.
6. No cloud provider keys in the dashboard.
7. No secrets committed.
8. CORS disabled by default.
9. If API auth is not implemented in this phase, document clearly that the dashboard is localhost-only.

## 15. Tests

### 15.1 Automated tests

Required tests:

```text
test_health_returns_ok
test_machines_returns_inventory
test_machine_by_id_returns_expected_machine
test_machine_by_id_404_for_unknown_machine
test_routes_are_derived_when_config_missing
test_profile_run_uses_safe_subprocess_arguments
test_profile_run_timeout_returns_structured_error
test_static_index_served
test_inventory_missing_returns_structured_error
```

### 15.2 Test command

```bash
pip install -r requirements.txt
pytest
```

### 15.3 API smoke tests

```bash
python -m uvicorn gateway.server:app --reload --port 8080
curl http://localhost:8080/api/health
curl http://localhost:8080/api/machines
curl http://localhost:8080/api/machines/macbook-m4pro
curl http://localhost:8080/api/config/routes
```

## 16. Manual verification

1. Open `http://localhost:8080` in a browser.
2. Verify all three machines render as cards.
3. Verify MacBook M4 Pro displays as `local_quality`.
4. Verify RTX 2070 PC displays as `local_fast`.
5. Verify old MacBook displays as `utility_node` and not an inference route.
6. Verify gauges animate on load.
7. Verify re-profile button runs locally and renders results.
8. Verify routing visualisation shows correct mappings.
9. Verify responsive layout works when resizing.
10. Verify API error state renders cleanly if inventory is missing or malformed.

## 17. Acceptance criteria

This phase is complete when:

1. `python -m uvicorn gateway.server:app --reload --port 8080` starts the dashboard.
2. The dashboard reads `inventory/machines.json`.
3. The three current systems render correctly.
4. Re-profile runs only the local profiler safely.
5. The routing map reflects current benchmark decisions.
6. The old MacBook is shown as utility-only.
7. Basic tests pass.
8. No secrets or private data are logged or committed.
9. The implementation does not create a custom chat interface.

## 18. Deferred work

Do not build these in this phase:

1. Full OpenAI-compatible gateway.
2. Chat interface.
3. RAG upload and document processing.
4. Cloud provider integration.
5. Authentication beyond localhost/admin token basics.
6. Model lifecycle automation.
7. Prometheus/Grafana integration.

These belong to later milestones.

## 19. Implementation order

1. Add `.gitignore` and `requirements.txt`.
2. Add `gateway/__init__.py`.
3. Add `gateway/server.py` with `/api/health` and static serving.
4. Add inventory loader and `/api/machines`.
5. Add `/api/machines/{machine_id}`.
6. Add derived `/api/config/routes`.
7. Add safe profiler runner.
8. Add static HTML/CSS/JS.
9. Add tests.
10. Run local smoke tests.

## 20. Notes for coding agent

When implementing:

1. Keep functions small and testable.
2. Avoid clever state management in frontend JavaScript.
3. Do not mutate inventory during re-profile in this phase.
4. Keep the dashboard read-heavy and safe.
5. Make all file paths relative to repository root.
6. Fail gracefully when optional files do not exist.
7. Preserve the future gateway path: this dashboard should not box the project into a toy architecture.
