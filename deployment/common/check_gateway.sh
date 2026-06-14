#!/usr/bin/env bash
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8080}"

echo "Checking gateway health at $GATEWAY_URL/api/health..."
curl -s "$GATEWAY_URL/api/health" | jq .

echo ""
echo "Checking backend fleet health at $GATEWAY_URL/api/backends/health..."
curl -s "$GATEWAY_URL/api/backends/health" | jq .
