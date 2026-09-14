#!/usr/bin/env bash
# ==============================================================================
# VoxStream - Linux Uninstaller Script
# ==============================================================================

set -e

# ANSI Color Codes
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   🗑️  VoxStream - Linux Uninstaller Suite${NC}"
echo -e "${CYAN}======================================================${NC}"
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
    echo "This script will uninstall VoxStream from your Linux system:"
    echo "  - Terminate running VoxStream processes"
    echo "  - Remove application desktop launcher (if installed)"
    echo "  - Delete the Python virtual environment (.venv) (~2-4 GB)"
    echo "  - Clean up build & bytecode caches"
    echo ""
    read -p "Are you sure you want to proceed? [y/N]: " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}ℹ️  Uninstallation canceled.${NC}"
        exit 0
    fi
fi

# 1. Stop running processes
echo -e "${CYAN}[1/4] Stopping running VoxStream processes...${NC}"
pkill -f "obs_captioner.main" 2>/dev/null || true
pkill -f "run_captioner" 2>/dev/null || true
echo -e "${GREEN}✅ Running processes stopped.${NC}"

# 2. Remove desktop shortcut if present
echo -e "${CYAN}[2/4] Checking desktop entries...${NC}"
DESKTOP_FILE="$HOME/.local/share/applications/voxstream.desktop"
if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    echo -e "${GREEN}✅ Removed desktop entry: $DESKTOP_FILE${NC}"
else
    echo "ℹ️  No desktop entry found."
fi

# 3. Remove virtual environment
echo -e "${CYAN}[3/4] Removing Python virtual environment (.venv)...${NC}"
if [ -d ".venv" ]; then
    rm -rf .venv
    echo -e "${GREEN}✅ Virtual environment removed (disk space reclaimed).${NC}"
else
    echo "ℹ️  No .venv found."
fi

# 4. Clean caches
echo -e "${CYAN}[4/4] Cleaning temporary build & bytecode caches...${NC}"
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true
echo -e "${GREEN}✅ Caches cleaned.${NC}"

# 5. Optional model & config cleanup
if [ "$AUTO_YES" = false ]; then
    if [ "$CLEAN_MODELS" = false ]; then
        read -p "Delete cached offline AI speech models in ~/.cache? [y/N]: " del_models
        if [[ "$del_models" =~ ^[Yy]$ ]]; then CLEAN_MODELS=true; fi
    fi
    if [ "$CLEAN_CONFIG" = false ]; then
        read -p "Delete personal config.json? [y/N]: " del_cfg
        if [[ "$del_cfg" =~ ^[Yy]$ ]]; then CLEAN_CONFIG=true; fi
    fi
fi

if [ "$CLEAN_MODELS" = true ]; then
    echo -e "${CYAN}🧠 Removing cached speech models (~/.cache/vosk & Hugging Face)...${NC}"
    rm -rf "$HOME/.cache/vosk" 2>/dev/null || true
    rm -rf "$HOME/.cache/huggingface/hub/models--Systran--faster-whisper"* 2>/dev/null || true
    echo -e "${GREEN}✅ Cached models removed.${NC}"
fi

if [ "$CLEAN_CONFIG" = true ]; then
    if [ -f "config.json" ]; then
        rm -f config.json
        echo -e "${GREEN}✅ config.json removed.${NC}"
    fi
fi

echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}🎉 VoxStream has been successfully uninstalled!${NC}"
echo -e "   You can now safely delete this folder:"
echo -e "   ${CYAN}$SCRIPT_DIR${NC}"
echo -e "${GREEN}======================================================${NC}"