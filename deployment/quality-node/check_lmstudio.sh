#!/usr/bin/env bash
set -euo pipefail

LMSTUDIO_URL="${LMSTUDIO_URL:-http://127.0.0.1:1234/v1}"

echo "Checking if LM Studio is responding at $LMSTUDIO_URL"
curl -s "$LMSTUDIO_URL/models" | jq .
