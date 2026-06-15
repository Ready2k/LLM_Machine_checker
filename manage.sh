#!/bin/bash

# manage.sh
# Script to start, stop, and test the Local AI Fabric gateway

PID_FILE=".gateway.pid"

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

    echo "Starting Gateway..."
    # Ensure virtualenv is active or used
    if [ -d ".venv" ]; then
        .venv/bin/python -m gateway.server &
    else
        python3 -m gateway.server &
    fi
    
    PID=$!
    echo $PID > "$PID_FILE"
    echo "Gateway started in background (PID: $PID)."
    echo "Dashboard : http://0.0.0.0:8080"
    echo "Chat UI   : http://0.0.0.0:8080/chat/"
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
    *)
        echo "Usage: $0 {start|stop|restart|test}"
        exit 1
        ;;
esac
