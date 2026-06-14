# Local AI Fabric

A lightweight, zero-dependency Python gateway and machine profiling system for hybrid local LLM routing. The Local AI Fabric allows you to run multiple inference nodes (e.g., MacBooks with LM Studio, Windows PCs with Ollama) and automatically route API requests to the best available local model, enforcing privacy policies to keep sensitive data off the cloud.

## Features

- **Hardware Auto-Detection**: Profile hostnames, OS, CPU, RAM, and GPU to recommend machine roles.
- **Smart Routing**: Route requests to `local-fast`, `local-quality`, or `private-docs` depending on need.
- **Privacy Enforcement**: Block sensitive code or private documents from ever reaching the cloud via configurable privacy tiers (`personal`, `work`, `confidential`, `public`).
- **Telemetry & Monitoring**: Log metadata of all routing decisions to JSONL without logging sensitive prompts.
- **Unified API**: Exposes an OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoint for drop-in integration with Open WebUI, Cursor, and VS Code.

## Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Backends**:
   Copy the example backend configuration:
   ```bash
   cp config/backends.example.yaml config/backends.local.yaml
   ```
   Edit `config/backends.local.yaml` to point to your live local nodes.

3. **Run the Gateway**:
   ```bash
   python -m gateway.server
   ```
   The gateway will start on `http://127.0.0.1:8080`.

4. **View Dashboard**:
   Open `http://127.0.0.1:8080` in your browser to view the live health and architecture dashboard.

## Key Endpoints

- `GET /` - Admin dashboard.
- `GET /api/backends/health` - Live health check of all configured inference nodes.
- `GET /v1/models` - Lists available abstract models (`local-fast`, `local-quality`, `cloud-best`).
- `POST /v1/chat/completions` - OpenAI-compatible inference endpoint.

## Documentation

- [Open WebUI Setup Guide](docs/OPEN_WEBUI_SETUP.md)
- [Gateway Specification](docs/LOCAL_AI_FABRIC_SPEC.md)
- [Web Dashboard Implementation Plan](docs/WEB_DASHBOARD_IMPLEMENTATION_PLAN.md)

## Diagnostics

Use the built-in scripts to verify your cluster health:

```bash
python scripts/check_backends.py
python scripts/smoke_gateway.py
```
