#!/usr/bin/env bash
# ==============================================================================
# VoxStream - macOS Uninstaller Script
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "   🗑️  VoxStream - macOS Uninstaller"
echo "=================================================="
echo ""

AUTO_YES=false
CLEAN_MODELS=false
CLEAN_CONFIG=false

for arg in "$@"; do
    case "$arg" in
        -y|--yes) AUTO_YES=true ;;
        --clean-models) CLEAN_MODELS=true ;;
        --clean-config) CLEAN_CONFIG=true ;;
        --all) AUTO_YES=true; CLEAN_MODELS=true; CLEAN_CONFIG=true ;;
    esac
done

if [ "$AUTO_YES" = false ]; then
    echo "This script will uninstall VoxStream from your Mac:"
    echo "  - Stop running VoxStream processes"
    echo "  - Delete the Python virtual environment (.venv) (~2-4 GB)"
    echo "  - Clean up build & bytecode caches"
    echo ""
    read -p "Are you sure you want to proceed? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "ℹ️  Uninstallation canceled."
        exit 0
    fi
fi

# 1. Stop running processes
echo "🛑 Stopping any running VoxStream processes..."
pkill -f "obs_captioner.main" 2>/dev/null || true
pkill -f "run_captioner" 2>/dev/null || true

# 2. Remove virtual environment
if [ -d ".venv" ]; then
    echo "📦 Removing Python virtual environment (.venv)..."
    rm -rf .venv
    echo "✅ Virtual environment removed."
else
    echo "ℹ️  No .venv found."
fi

# 3. Clean caches
echo "🧹 Cleaning temporary Python bytecode caches..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true

# 4. Optional model & config cleanup
if [ "$AUTO_YES" = false ]; then
    if [ "$CLEAN_MODELS" = false ]; then
        read -p "Delete cached offline AI models in ~/.cache? [y/N]: " del_models
        if [[ "$del_models" =~ ^[Yy]$ ]]; then CLEAN_MODELS=true; fi
    fi
    if [ "$CLEAN_CONFIG" = false ]; then
        read -p "Delete config.json? [y/N]: " del_cfg
        if [[ "$del_cfg" =~ ^[Yy]$ ]]; then CLEAN_CONFIG=true; fi
    fi
fi

if [ "$CLEAN_MODELS" = true ]; then
    echo "🧠 Removing cached speech models (~/.cache/vosk and Hugging Face Whisper)..."
    rm -rf "$HOME/.cache/vosk" 2>/dev/null || true
    rm -rf "$HOME/.cache/huggingface/hub/models--Systran--faster-whisper"* 2>/dev/null || true
    echo "✅ Cached models removed."
fi

if [ "$CLEAN_CONFIG" = true ]; then
    if [ -f "config.json" ]; then
        rm -f config.json
        echo "✅ config.json removed."
    fi
fi

echo ""
echo "=================================================="
echo "🎉 VoxStream has been successfully uninstalled!"
echo "   You can now safely delete this folder:"
echo "   $SCRIPT_DIR"
echo "=================================================="