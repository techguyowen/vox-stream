#!/usr/bin/env bash
# ==============================================================================
# VoxStream - macOS & Linux Setup Script
# ==============================================================================

set -e

echo "=================================================="
echo "   🎙️ VoxStream - Environment Setup (macOS/Linux) "
echo "=================================================="

# 1. Check Python 3 installation
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed or not in PATH."
    echo "   Please install Python 3.9+ via Homebrew ('brew install python') or from python.org."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Detected Python: $(python3 --version) ($PYTHON_VERSION)"

# 2. Create virtual environment if not present
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment in .venv..."
    python3 -m venv .venv
else
    echo "ℹ️  Virtual environment (.venv) already exists."
fi

# 3. Upgrade pip and install dependencies
echo "📥 Installing required dependencies from requirements.txt..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# 4. Linux-specific audio check
if [ "$(uname -s)" = "Linux" ]; then
    if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
        echo "⚠️ Note: libportaudio was not found on your system."
        echo "   On Debian/Ubuntu, run: sudo apt-get install -y libportaudio2 ffmpeg"
    fi
fi

# 5. Initialize config.json if not present
if [ ! -f "config.json" ]; then
    echo "⚙️ Initializing config.json from template..."
    cp config.json.example config.json
fi

# 6. Preload default offline AI models
echo "🧠 Pre-caching default speech models (Vosk & Faster-Whisper & Silero VAD)..."
.venv/bin/python -m obs_captioner.model_downloader --preload-defaults || echo "⚠️ Model pre-cache skipped (will download on first launch)."

# 7. Make scripts executable
chmod +x run_captioner.sh setup_mac.sh setup_linux.sh 2>/dev/null || true

echo ""
echo "=================================================="
echo "   Available Audio Input Devices:"
echo "=================================================="
.venv/bin/python -m obs_captioner.main --list-devices 2>/dev/null || true

echo ""
echo "=================================================="
echo "🎉 Setup complete! You can now start VoxStream:"
echo "   ./run_captioner.sh"
echo "   (or: ./run_captioner.sh --engine local_whisper)"
echo "=================================================="

