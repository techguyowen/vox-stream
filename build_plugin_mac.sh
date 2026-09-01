#!/usr/bin/env bash
# ==============================================================================
# VoxStream - macOS OBS Plugin Build Script
# ==============================================================================

set -e

if ! command -v cmake &> /dev/null; then
    echo "⚠️ CMake is not installed."
    if command -v brew &> /dev/null; then
        echo "Installing cmake via Homebrew..."
        brew install cmake
    else
        echo "❌ Homebrew is required to install cmake. Please install Homebrew first."
        exit 1
    fi
fi

echo "🚀 Building OBS Native Plugin for macOS..."

cd obs_native_plugin
mkdir -p build
cd build

# Configure CMake
cmake -DCMAKE_BUILD_TYPE=Release ..

# Build
make -j$(sysctl -n hw.logicalcpu)

# Install manually to user's OBS plugins folder
OBS_PLUGIN_DIR="$HOME/Library/Application Support/obs-studio/plugins/obs-live-captions/bin/64bit"
mkdir -p "$OBS_PLUGIN_DIR"
cp obs-live-captions.so "$OBS_PLUGIN_DIR/" 2>/dev/null || cp obs-live-captions.dylib "$OBS_PLUGIN_DIR/"

echo "🎉 Native Plugin Build Finished!"
echo "Installed to: $OBS_PLUGIN_DIR"
echo "Please restart OBS Studio to use the Live Speech Captions filter."
