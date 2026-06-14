#!/usr/bin/env bash
set -euo pipefail

LMSTUDIO_URL="${LMSTUDIO_URL:-http://127.0.0.1:1234/v1}"

echo "Testing LM Studio inference at $LMSTUDIO_URL"
curl -X POST "$LMSTUDIO_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Reply with exactly: test ok"}],
    "max_tokens": 10
  }' | jq .
