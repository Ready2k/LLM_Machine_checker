#!/usr/bin/env bash
set -euo pipefail

echo "=== Starting Hub Node (Open WebUI) ==="
docker compose up -d

echo ""
echo "Open WebUI is starting up."
echo "It will be available at http://127.0.0.1:3000"
