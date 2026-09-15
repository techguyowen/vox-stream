#!/usr/bin/env bash
# ==============================================================================
# VoxStream - Linux Automated Setup Script
# ==============================================================================

set -e

# ANSI Color Codes
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   🎙️ VoxStream: OBS Live Captioner Suite - Linux Setup${NC}"
echo -e "${CYAN}======================================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------------------
# STEP 1: Detect Distro & Audio / Codec Dependencies
# ------------------------------------------------------------------------------
echo -e "${CYAN}[1/7] Checking Linux audio & system libraries...${NC}"

MISSING_DEPS=()

# Check for PortAudio library (needed by sounddevice)
if ! ldconfig -p 2>/dev/null | grep -q libportaudio.so; then
    MISSING_DEPS+=("libportaudio")
fi

# Check for ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    MISSING_DEPS+=("ffmpeg")
fi

# Check for git
if ! command -v git &>/dev/null; then
    MISSING_DEPS+=("git")
fi

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠️ Notice: Missing system packages: ${MISSING_DEPS[*]}${NC}"
    
    if command -v apt-get &>/dev/null; then
        echo -e "   Run the following to install required system packages on Debian/Ubuntu:"
        echo -e "   ${GREEN}sudo apt-get update && sudo apt-get install -y python3-venv python3-pip libportaudio2 ffmpeg git${NC}"
    elif command -v dnf &>/dev/null; then
        echo -e "   Run the following to install on Fedora/RHEL:"
        echo -e "   ${GREEN}sudo dnf install -y python3-pip portaudio ffmpeg git${NC}"
    elif command -v pacman &>/dev/null; then
        echo -e "   Run the following to install on Arch Linux/Manjaro:"
        echo -e "   ${GREEN}sudo pacman -S --needed python python-pip portaudio ffmpeg git${NC}"
    fi
    echo ""
else
    echo -e "${GREEN}✅ System audio (PortAudio), ffmpeg, and git detected.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 2: Python 3 Check
# ------------------------------------------------------------------------------
echo -e "${CYAN}[2/7] Checking Python 3 environment...${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ Error: python3 is not installed or not in your PATH.${NC}"
    echo "   Please install Python 3.9 through 3.12 using your system package manager."
    exit 1
fi

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✅ Detected Python: $(python3 --version) (${PYTHON_VER})${NC}"

# ------------------------------------------------------------------------------
# STEP 3: Git Repository Link (for 1-click in-app updates)
# ------------------------------------------------------------------------------
echo -e "${CYAN}[3/7] Checking Git configuration for in-app updates...${NC}"
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
# STEP 4: Virtual Environment Setup
# ------------------------------------------------------------------------------
echo -e "${CYAN}[4/7] Setting up Python virtual environment (.venv)...${NC}"
if [ ! -d ".venv" ] || [ ! -f ".venv/bin/activate" ]; then
    rm -rf .venv 2>/dev/null || true
    python3 -m venv .venv || {
        echo -e "${RED}❌ Error: Failed to create virtual environment.${NC}"
        echo "   On Debian/Ubuntu, run: sudo apt install -y python3-venv"
        exit 1
    }
    echo -e "${GREEN}✅ Virtual environment created.${NC}"
else
    echo -e "${GREEN}✅ Virtual environment already exists.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 5: Install Python Dependencies & Hardware Acceleration
# ------------------------------------------------------------------------------
echo -e "${CYAN}[5/7] Installing Python dependencies & speech engines...${NC}"
if [ -f ".venv/bin/uv" ]; then
    echo -e "${GREEN}⚡ Using fast package manager [uv]...${NC}"
    .venv/bin/uv pip install -r requirements.txt
else
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/python -m pip install -r requirements.txt
fi

# Hardware acceleration check (NVIDIA CUDA)
if command -v nvidia-smi &>/dev/null; then
    echo -e "${GREEN}🚀 NVIDIA GPU detected via nvidia-smi!${NC}"
    echo "   Installing CUDA runtime wheels for Faster-Whisper..."
    .venv/bin/python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# STEP 6: Configure & Pre-Cache AI Models
# ------------------------------------------------------------------------------
echo -e "${CYAN}[6/7] Initializing settings & pre-caching speech models...${NC}"
if [ ! -f "config.json" ]; then
    cp config.json.example config.json
    echo -e "${GREEN}✅ Created config.json from template.${NC}"
fi

# Pre-cache models
.venv/bin/python -m obs_captioner.model_downloader --preload-defaults || echo -e "${YELLOW}⚠️ Model download deferred to first launch.${NC}"

# ------------------------------------------------------------------------------
# STEP 7: Desktop Launcher & Permissions
# ------------------------------------------------------------------------------
echo -e "${CYAN}[7/7] Setting permissions & creating application launcher...${NC}"
chmod +x run_captioner.sh setup_linux.sh setup_mac.sh uninstall_linux.sh uninstall_mac.sh 2>/dev/null || true

# Optional: Create .desktop file if desktop environment exists
DESKTOP_DIR="$HOME/.local/share/applications"
if [ -d "$DESKTOP_DIR" ]; then
    cat << EOF > "$DESKTOP_DIR/voxstream.desktop"
[Desktop Entry]
Name=VoxStream Live Captioner
Comment=Real-Time Live Captioning Suite for OBS Studio
Exec=$SCRIPT_DIR/run_captioner.sh
Icon=audio-input-microphone
Terminal=true
Type=Application
Categories=AudioVideo;Audio;Recorder;
Path=$SCRIPT_DIR
EOF
    chmod +x "$DESKTOP_DIR/voxstream.desktop" 2>/dev/null || true
    echo -e "${GREEN}✅ Added desktop launcher: ~/.local/share/applications/voxstream.desktop${NC}"
fi

echo ""
echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   Available Audio Input Devices:${NC}"
echo -e "${CYAN}======================================================${NC}"
.venv/bin/python -m obs_captioner.main --list-devices 2>/dev/null || true

# ------------------------------------------------------------------------------
# STEP 8: Optional Caddy Reverse Proxy & Local SSL (Port 80 & 443)
# ------------------------------------------------------------------------------
echo ""
echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   Caddy Reverse Proxy & Local SSL Configuration${NC}"
echo -e "${CYAN}======================================================${NC}"
echo "VoxStream can configure Caddy to provide clean URLs (no :8765)"
echo "and local SSL/HTTPS for mobile PWA install and Screen Wake Lock."
echo ""
if [ -z "${INSTALL_CADDY:-}" ]; then
    read -p "Would you like to install & enable Caddy with local SSL? [Y/n]: " INSTALL_CADDY || true
fi
INSTALL_CADDY=${INSTALL_CADDY:-Y}
if [[ "$INSTALL_CADDY" =~ ^[Yy]$ ]]; then
    echo -e "${CYAN}ℹ️  Configuring Caddy Reverse Proxy...${NC}"
    .venv/bin/python -c "from obs_captioner.caddy_manager import download_caddy, trust_caddy_ca, start_caddy; download_caddy(); trust_caddy_ca(); start_caddy()"
    .venv/bin/python -c "from obs_captioner.config import load_config, save_config; c = load_config(); c.caddy.enabled = True; c.caddy.ssl = True; save_config(c)"
    echo -e "${GREEN}✅ Caddy Reverse Proxy and local SSL configured & running!${NC}"
else
    echo -e "${YELLOW}ℹ️  Caddy Reverse Proxy skipped. You can enable it anytime in the Dashboard.${NC}"
fi

LAN_IP=$(.venv/bin/python -c "from obs_captioner.hardware import get_local_ip; print(get_local_ip())" 2>/dev/null || echo "127.0.0.1")
CADDY_ON=$(.venv/bin/python -c "from obs_captioner.config import load_config; print(load_config().caddy.enabled)" 2>/dev/null || echo "False")
echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}🎉 Setup complete! You can now start VoxStream:${NC}"
echo -e "   ${CYAN}./run_captioner.sh${NC}"
if [ "$CADDY_ON" = "True" ]; then
    echo -e "   Local Dashboard:   ${CYAN}https://127.0.0.1/dashboard${NC} (or http://127.0.0.1:8765/dashboard)"
    if [ "$LAN_IP" != "127.0.0.1" ]; then
        echo -e "   Clean Stage SSL:   ${CYAN}https://${LAN_IP}/display${NC}"
        echo -e "   Clean Stage HTTP:  ${CYAN}http://${LAN_IP}/display${NC}"
    fi
else
    echo -e "   Local Dashboard:   ${CYAN}http://127.0.0.1:8765/dashboard${NC}"
    if [ "$LAN_IP" != "127.0.0.1" ]; then
        echo -e "   Network Dashboard: ${CYAN}http://${LAN_IP}:8765/dashboard${NC}"
        echo -e "   Stage / Mobile:    ${CYAN}http://${LAN_IP}:8765/display${NC}"
    fi
fi
echo -e "${GREEN}======================================================${NC}"

