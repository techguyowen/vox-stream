#!/usr/bin/env bash
# ==============================================================================
# VoxStream - Standalone Launcher for macOS and Linux
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_EXE="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_EXE="python3"
else
    echo "Python 3 is required but not found."
    exit 1
fi

exec "$PYTHON_EXE" -m obs_captioner.launcher "$@"
