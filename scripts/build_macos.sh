#!/usr/bin/env bash
# Build TangoSuggest.app and optionally TangoSuggest.dmg
#
# Usage:
#   bash scripts/build_macos.sh          # build .app only
#   bash scripts/build_macos.sh --dmg    # build .app then wrap in .dmg
#
# Prerequisites:
#   uv sync --group dev          (installs pyinstaller + pillow)
#   brew install create-dmg      (only if using --dmg)

set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_DMG=false
if [[ "${1:-}" == "--dmg" ]]; then
    BUILD_DMG=true
fi

echo "==> Generating icon..."
uv run python scripts/make_icon.py

echo "==> Building .app bundle..."
uv run pyinstaller TangoSuggest.spec --noconfirm

APP="dist/TangoSuggest.app"

if [[ ! -d "$APP" ]]; then
    echo "ERROR: $APP not found after build." >&2
    exit 1
fi

echo "==> Build complete: $APP"

if [[ "$BUILD_DMG" == true ]]; then
    if ! command -v create-dmg &>/dev/null; then
        echo "create-dmg not found. Install with: brew install create-dmg" >&2
        exit 1
    fi

    DMG="dist/TangoSuggest.dmg"
    rm -f "$DMG"

    echo "==> Creating $DMG..."
    create-dmg \
        --volname "TangoSuggest" \
        --volicon "src/tanda_suggester/gui/resources/TangoSuggest.icns" \
        --window-size 600 400 \
        --icon-size 128 \
        --icon "TangoSuggest.app" 150 200 \
        --app-drop-link 450 200 \
        --no-internet-enable \
        "$DMG" \
        "$APP"

    echo "==> DMG ready: $DMG"
fi

echo ""
echo "To install: drag dist/TangoSuggest.app to /Applications"
echo "To remove:  drag TangoSuggest from /Applications to Trash"
echo ""
echo "First launch (unsigned app): right-click → Open, then confirm."
