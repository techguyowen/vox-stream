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

# 4. Make scripts executable
chmod +x run_captioner.sh setup_mac.sh 2>/dev/null || true

echo ""
echo "=================================================="
echo "🎉 Setup complete! You can now start VoxStream:"
echo "   ./run_captioner.sh"
echo "   (or: ./run_captioner.sh --engine google_web)"
echo "=================================================="
