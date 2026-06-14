#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${ENDPOINT:-http://127.0.0.1:8080/v1}"

echo "Testing OpenAI compatible models endpoint at $ENDPOINT/models..."
curl -s "$ENDPOINT/models" | jq .
