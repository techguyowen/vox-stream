#!/usr/bin/env bash
# ==============================================================================
# VoxStream - macOS Automated Setup Script
# ==============================================================================

set -e

# ANSI Color Codes
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   🎙️  VoxStream: OBS Live Captioner Suite - macOS Setup${NC}"
echo -e "${CYAN}======================================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------------------
# STEP 1: Python 3 & Homebrew Environment Check
# ------------------------------------------------------------------------------
echo -e "${CYAN}[1/7] Checking macOS environment & Python...${NC}"

if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ Error: python3 is not installed or not in your PATH.${NC}"
    echo "   Please install Python 3.10+ via Homebrew ('brew install python') or from python.org."
    exit 1
fi

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✅ Detected Python: $(python3 --version) (${PYTHON_VER})${NC}"

# Check for Homebrew and system media tools
if command -v brew &>/dev/null; then
    if ! command -v ffmpeg &>/dev/null; then
        echo -e "${YELLOW}ℹ️  Tip: ffmpeg is recommended for audio recording conversion.${NC}"
        echo -e "   Install anytime via: ${GREEN}brew install ffmpeg${NC}"
    fi
else
    echo -e "${YELLOW}ℹ️  Homebrew not found. If audio capture needs extra libraries, install from https://brew.sh${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 2: Git Repository Link (for 1-click in-app updates)
# ------------------------------------------------------------------------------
echo -e "${CYAN}[2/7] Checking Git configuration for in-app updates...${NC}"
if command -v git &>/dev/null; then
    if [ ! -d ".git" ]; then
        echo -e "${YELLOW}ℹ️ Linking directory to GitHub repository for 1-click in-app updates...${NC}"
        git init -q
        git remote add origin https://github.com/techguyowen/vox-stream.git 2>/dev/null || true
        git fetch origin main --depth=1 -q 2>/dev/null || true
        git reset --soft origin/main 2>/dev/null || true
        echo -e "${GREEN}✅ Repository linked! In-app updates will download automatically.${NC}"
    else
        echo -e "${GREEN}✅ Git repository structure active.${NC}"
    fi
fi

# ------------------------------------------------------------------------------
# STEP 3: Virtual Environment Setup
# ------------------------------------------------------------------------------
echo -e "${CYAN}[3/7] Setting up Python virtual environment (.venv)...${NC}"
if [ ! -d ".venv" ] || [ ! -f ".venv/bin/activate" ]; then
    rm -rf .venv 2>/dev/null || true
    python3 -m venv .venv || {
        echo -e "${RED}❌ Error: Failed to create virtual environment.${NC}"
        exit 1
    }
    echo -e "${GREEN}✅ Virtual environment created.${NC}"
else
    echo -e "${GREEN}✅ Virtual environment already exists.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 4: Install Python Dependencies
# ------------------------------------------------------------------------------
echo -e "${CYAN}[4/7] Installing Python dependencies & speech engines...${NC}"
if [ -f ".venv/bin/uv" ]; then
    echo -e "${GREEN}⚡ Using fast package manager [uv]...${NC}"
    .venv/bin/uv pip install -r requirements.txt
else
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/python -m pip install -r requirements.txt
fi

# ------------------------------------------------------------------------------
# STEP 5: Hardware Acceleration (Apple Silicon MPS / Metal)
# ------------------------------------------------------------------------------
echo -e "${CYAN}[5/7] Checking Mac hardware acceleration...${NC}"
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    echo -e "${GREEN}🚀 Apple Silicon detected (${ARCH})!${NC}"
    MPS_AVAIL=$(.venv/bin/python -c 'import torch; print(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())' 2>/dev/null || echo "False")
    if [ "$MPS_AVAIL" = "True" ]; then
        echo -e "${GREEN}✅ Apple Metal Performance Shaders (MPS) hardware acceleration active.${NC}"
    else
        echo -e "${YELLOW}ℹ️  Standard CPU high-performance int8 inference ready.${NC}"
    fi
else
    echo -e "${GREEN}✅ Intel Mac detected (${ARCH}). CPU multi-threaded int8 inference configured.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 6: Configure & Pre-Cache AI Models
# ------------------------------------------------------------------------------
echo -e "${CYAN}[6/7] Initializing settings & pre-caching speech models...${NC}"
if [ ! -f "config.json" ]; then
    cp config.json.example config.json
    echo -e "${GREEN}✅ Created config.json from template.${NC}"
else
    echo -e "${GREEN}✅ Existing config.json preserved.${NC}"
fi

# Pre-cache models
.venv/bin/python -m obs_captioner.model_downloader --preload-defaults || echo -e "${YELLOW}⚠️ Model download deferred to first launch.${NC}"

# ------------------------------------------------------------------------------
# STEP 7: Permissions & Audio Check
# ------------------------------------------------------------------------------
echo -e "${CYAN}[7/7] Setting permissions & verifying audio devices...${NC}"
chmod +x run_captioner.sh setup_mac.sh setup_linux.sh uninstall_mac.sh uninstall_linux.sh 2>/dev/null || true

echo ""
echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   Available Audio Input Devices:${NC}"
echo -e "${CYAN}======================================================${NC}"
.venv/bin/python -m obs_captioner.main --list-devices 2>/dev/null || true

echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}🎉 Setup complete! You can now start VoxStream:${NC}"
echo -e "   ${CYAN}./run_captioner.sh${NC}"
echo -e "   Dashboard available at: ${CYAN}http://127.0.0.1:8765${NC}"
echo -e "${GREEN}======================================================${NC}"

