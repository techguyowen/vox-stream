#!/usr/bin/env bash
# ==============================================================================
# VoxStream - Release Packager
# ==============================================================================

VERSION="1.0.0"
RELEASE_NAME="voxstream-v$VERSION"
ZIP_NAME="$RELEASE_NAME.zip"

echo "📦 Building Release Package: $ZIP_NAME..."

# Create a temporary staging directory
mkdir -p "$RELEASE_NAME"

# Copy essential files
cp -R obs_captioner "$RELEASE_NAME/"
cp -R obs_script "$RELEASE_NAME/"
cp -R integrations "$RELEASE_NAME/" 2>/dev/null || true
cp -R obs_native_plugin "$RELEASE_NAME/" 2>/dev/null || true
cp requirements.txt "$RELEASE_NAME/"
cp config.json.example "$RELEASE_NAME/"
cp README.md "$RELEASE_NAME/"
cp INSTALL_GUIDE.md "$RELEASE_NAME/"
cp FEATURES_AND_SYSTEM_SPECIFICATION.md "$RELEASE_NAME/" 2>/dev/null || true
cp FULL_SETUP_GUIDE.md "$RELEASE_NAME/" 2>/dev/null || true
cp API_GUIDE.md "$RELEASE_NAME/" 2>/dev/null || true
cp LICENSE "$RELEASE_NAME/" 2>/dev/null || true
cp run_captioner.sh "$RELEASE_NAME/"
cp run_captioner.bat "$RELEASE_NAME/"
cp setup_mac.sh "$RELEASE_NAME/"
cp setup_windows.bat "$RELEASE_NAME/"
cp launch_obs_clean.bat "$RELEASE_NAME/" 2>/dev/null || true
cp build_plugin_mac.sh "$RELEASE_NAME/" 2>/dev/null || true
cp build_plugin_windows.bat "$RELEASE_NAME/" 2>/dev/null || true

# Clean up any local cache or environment files from the copy
find "$RELEASE_NAME" -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null
find "$RELEASE_NAME" -type d -name ".pytest_cache" -exec rm -r {} + 2>/dev/null
find "$RELEASE_NAME" -type f -name "*.pyc" -delete 2>/dev/null
find "$RELEASE_NAME" -type f -name ".DS_Store" -delete 2>/dev/null

# Zip the staging directory
if command -v zip &> /dev/null; then
    zip -r -q "$ZIP_NAME" "$RELEASE_NAME"
    echo "✅ Successfully created $ZIP_NAME"
else
    echo "❌ Error: 'zip' command not found. Please install zip."
    exit 1
fi

# Cleanup staging directory
rm -rf "$RELEASE_NAME"
echo "🎉 Release ready for upload to GitHub!"
