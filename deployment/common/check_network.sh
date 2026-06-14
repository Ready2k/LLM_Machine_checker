#!/usr/bin/env bash
set -euo pipefail

TARGET=${1:-127.0.0.1}

echo "Pinging $TARGET..."
ping -c 4 "$TARGET"
