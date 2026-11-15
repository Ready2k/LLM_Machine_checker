#!/bin/bash

# manage.sh
# Script to start, stop, and test the Local AI Fabric gateway

PID_FILE=".gateway.pid"
AGENT_PID_FILE=".agent.pid"

# Load local secrets (GATEWAY_API_KEYS, AGENT_SECRET, SEARXNG_URL, ...) if present.
if [ -f ".env" ]; then set -a; . ./.env; set +a; fi

start() {
    if [ -f "$PID_FILE" ]; then
        if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "Gateway is already running (PID: $(cat $PID_FILE))."
            return
        else
            echo "Removing stale PID file."
            rm "$PID_FILE"
        fi
    fi

    # Enable HTTPS automatically if a cert pair is present (certs/server.{crt,key}).
    SCHEME="http"
    if [ -f "certs/server.crt" ] && [ -f "certs/server.key" ]; then
        export GATEWAY_SSL_CERT="certs/server.crt"
        export GATEWAY_SSL_KEY="certs/server.key"
        SCHEME="https"
    fi
    PORT="${GATEWAY_PORT:-8080}"

    echo "Starting Gateway (${SCHEME})..."
    # Ensure virtualenv is active or used
    if [ -d ".venv" ]; then
        .venv/bin/python -m gateway.server &
    else
        python3 -m gateway.server &
    fi

    PID=$!
    echo $PID > "$PID_FILE"

    echo "Starting Local Node Agent..."
    if [ -d ".venv" ]; then
        .venv/bin/python node_agent/agent.py > /dev/null 2>&1 &
    else
        python3 node_agent/agent.py > /dev/null 2>&1 &
    fi
    AGENT_PID=$!
    echo $AGENT_PID > "$AGENT_PID_FILE"

    # Best-effort LAN IP for convenience (macOS, then Linux/Pi)
    LAN_IP=""
    if command -v ipconfig >/dev/null 2>&1; then
        LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
    fi
    if [ -z "$LAN_IP" ] && command -v hostname >/dev/null 2>&1; then
        LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi

    echo "Gateway started in background (PID: $PID)."
    echo "  Local : ${SCHEME}://127.0.0.1:${PORT}/chat/"
    if [ -n "$LAN_IP" ]; then
        echo "  LAN   : ${SCHEME}://${LAN_IP}:${PORT}/chat/   (use this on your phone)"
    fi
    if [ "$SCHEME" = "http" ]; then
        echo "  (no TLS — run scripts/make_cert.sh then restart to enable HTTPS / phone voice)"
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        echo "Stopping Gateway (PID: $PID)..."
        kill "$PID" 2>/dev/null
        rm "$PID_FILE"
        echo "Gateway stopped."
    else
        echo "Gateway is not running (no PID file found)."
        # Fallback to pkill if necessary
        if pkill -f "gateway\.server"; then
            echo "Killed orphaned processes."
        fi
    fi

    if [ -f "$AGENT_PID_FILE" ]; then
        AGENT_PID=$(cat "$AGENT_PID_FILE")
        echo "Stopping Node Agent (PID: $AGENT_PID)..."
        kill "$AGENT_PID" 2>/dev/null
        rm "$AGENT_PID_FILE"
        echo "Node Agent stopped."
    else
        echo "Node Agent is not running (no PID file found)."
        # Fallback to pkill if necessary
        if pkill -f "node_agent/agent.py"; then
            echo "Killed orphaned agent processes."
        fi
    fi
}

test_services() {
    echo "=== Running Gateway Tests ==="
    if [ -d ".venv" ]; then
        .venv/bin/pytest tests/
    else
        python3 -m pytest tests/
    fi

    echo ""
    echo "=== Checking Live Backend Health ==="
    if [ -d ".venv" ]; then
        .venv/bin/python scripts/check_backends.py
    else
        python3 scripts/check_backends.py
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        sleep 1
        start
        ;;
    test)
        test_services
        ;;
    feedback)
        # Auto-router misroute reports -> regression tests (list | promote | dismiss)
        shift
        if [ -d ".venv" ]; then
            .venv/bin/python scripts/autoroute_feedback.py "$@"
        else
            python3 scripts/autoroute_feedback.py "$@"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|test|feedback <list|promote|dismiss> ...}"
        exit 1
        ;;
esac
