#!/usr/bin/env bash
set -euo pipefail

echo "=== Stopping Gateway Node ==="

cd ../../
./manage.sh stop
