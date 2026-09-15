#!/usr/bin/env bash
# ==============================================================================
# VoxStream - macOS & Linux Launch Script
# ==============================================================================

# Find the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export VOXSTREAM_RUNNER=sh

# Check if .venv exists
if [ ! -d ".venv" ]; then
    if [ "$(uname -s)" = "Linux" ]; then
        echo "⚠️ Virtual environment not found. Running setup_linux.sh first..."
        bash "$SCRIPT_DIR/setup_linux.sh"
    else
        echo "⚠️ Virtual environment not found. Running setup_mac.sh first..."
        bash "$SCRIPT_DIR/setup_mac.sh"
    fi
fi

# Cleanup trap to ensure Caddy stops when shell exits
cleanup() {
    "$SCRIPT_DIR/.venv/bin/python" -c "from obs_captioner.caddy_manager import stop_caddy; stop_caddy()" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while true; do
    LAN_IP=$("$SCRIPT_DIR/.venv/bin/python" -c "from obs_captioner.hardware import get_local_ip; print(get_local_ip())" 2>/dev/null || echo "127.0.0.1")
    CADDY_ON=$("$SCRIPT_DIR/.venv/bin/python" -c "from obs_captioner.config import load_config; print(load_config().caddy.enabled)" 2>/dev/null || echo "False")
    echo "🚀 Starting VoxStream Live Captioner..."
    if [ "$CADDY_ON" = "True" ]; then
        if [ "$LAN_IP" != "127.0.0.1" ]; then
            echo "🔒 Stage SSL:  https://$LAN_IP/display"
            echo "🌐 Stage HTTP: http://$LAN_IP/display"
        fi
        echo "🎛️  Dashboard:  https://127.0.0.1/dashboard (or http://127.0.0.1:8765/dashboard)"
    else
        if [ "$LAN_IP" != "127.0.0.1" ]; then
            echo "🌐 Network IP: http://$LAN_IP:8765 (Local: http://127.0.0.1:8765)"
        fi
        echo "🎛️  Dashboard:  http://127.0.0.1:8765/dashboard"
    fi
    "$SCRIPT_DIR/.venv/bin/python" -m obs_captioner.main "$@"
    EXIT_CODE=$?

    # Exit code 42 indicates an intentional application restart
    if [ $EXIT_CODE -eq 42 ]; then
        echo ""
        echo "🔄 [VoxStream] Application restart requested. Updating dependencies and reloading..."
        if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
            "$SCRIPT_DIR/.venv/bin/python" -m pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || true
        fi
        sleep 1
        continue
    else
        # Normal shutdown or termination
        exit $EXIT_CODE
    fi
done
