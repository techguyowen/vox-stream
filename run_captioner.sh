#!/usr/bin/env bash
# ==============================================================================
# VoxStream - macOS & Linux Launch Script
# ==============================================================================

# Find the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if .venv exists
if [ ! -d ".venv" ]; then
    echo "⚠️ Virtual environment not found. Running setup_mac.sh first..."
    bash "$SCRIPT_DIR/setup_mac.sh"
fi

while true; do
    echo "🚀 Starting VoxStream Live Captioner..."
    "$SCRIPT_DIR/.venv/bin/python" -m obs_captioner.main "$@"
    EXIT_CODE=$?

    # Exit code 42 indicates an intentional application restart
    if [ $EXIT_CODE -eq 42 ]; then
        echo ""
        echo "🔄 [VoxStream] Application restart requested. Reloading in 1s..."
        sleep 1
        continue
    else
        # Normal shutdown or termination
        exit $EXIT_CODE
    fi
done
