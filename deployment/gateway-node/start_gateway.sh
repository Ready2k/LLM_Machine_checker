#!/usr/bin/env bash
set -euo pipefail

echo "=== Starting Gateway Node ==="

cd ../../
if [ ! -f ".gateway.pid" ]; then
    ./manage.sh start
else
    echo "Gateway is already running."
fi

echo "Gateway started. Dashboard: http://127.0.0.1:8080"
