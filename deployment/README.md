# Local AI Fabric Deployment Guide

This directory contains the deployment scripts and configurations for the entire Local AI Fabric architecture. 

The architecture strictly follows this path to ensure privacy, telemetry, and fallback controls are respected:
**Open WebUI -> Gateway -> PC Ollama / M4 LM Studio**

Do **not** connect Open WebUI directly to Ollama or LM Studio.

## Recommended Startup Order

Model backends must exist before the gateway health checks them, and the UI must exist after the gateway exposes models.

1. **Start `fast-coder-node` on Windows PC**
   - Start Ollama
   - Pull models (`pull_models.ps1`)
   - Confirm `/api/tags` from another machine

2. **Start `quality-node` on M4 MacBook Pro**
   - Start LM Studio Local Server
   - Confirm `/v1/models` locally
   - Confirm `/v1/models` from the gateway host if remote

3. **Start `gateway-node` (Old MacBook or M4)**
   - Confirm `/api/health`
   - Confirm `/api/backends/health`
   - Confirm `/v1/models`

4. **Start `hub-node` (Old MacBook)**
   - Start Open WebUI
   - Point it at the gateway's `/v1` endpoint

## Network Terminology Rule

When configuring the gateway or backend nodes:
- `127.0.0.1` means **"same machine as the gateway"**
- `.local` hostname (e.g., `Jamess-MacBook-Pro-2.local`) or explicit IP means **"different machine on LAN"**

Following this rule will prevent routing failures during deployment.
