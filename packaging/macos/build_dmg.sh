#!/usr/bin/env bash
# Wrap the PyInstaller .app bundle in a drag-to-Applications disk image.
#
# Expects dist/Limelight.app to exist (see packaging/limelight.spec) and writes the
# image to dist/installer/. Must run on macOS: hdiutil is required.
#
#   python packaging/make_icons.py          # needs iconutil, hence macOS
#   pyinstaller packaging/limelight.spec --noconfirm
#   packaging/macos/build_dmg.sh [version]
#
# The bundle is unsigned. macOS Gatekeeper will refuse to open it on another
# machine until it is signed and notarised with an Apple Developer ID:
#   codesign --deep --force --options runtime --sign "Developer ID Application: ..." dist/Limelight.app
#   xcrun notarytool submit ... && xcrun stapler staple dist/installer/Limelight-<version>.dmg
# Without that, users must right-click the app and choose Open the first time.

set -euo pipefail

VERSION="${1:-0.0.1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP="$REPOSITORY_ROOT/dist/Limelight.app"
OUTPUT="$REPOSITORY_ROOT/dist/installer"
IMAGE="$OUTPUT/Limelight-${VERSION}.dmg"

if [ ! -d "$APP" ]; then
  echo "error: $APP not found. Build the PyInstaller bundle first." >&2
  exit 1
fi

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "error: hdiutil not found; the disk image can only be built on macOS." >&2
  exit 1
fi

mkdir -p "$OUTPUT"
rm -f "$IMAGE"

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

cp -R "$APP" "$STAGING/Limelight.app"
ln -s /Applications "$STAGING/Applications"

# PyInstaller leaves the bundle unsigned; an ad-hoc signature at least keeps
# macOS from killing it outright on Apple silicon.
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$STAGING/Limelight.app" 2>/dev/null || \
    echo "warning: ad-hoc signing failed; the app may not launch on Apple silicon" >&2
fi

hdiutil create \
  -volname "Limelight $VERSION" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$IMAGE" >/dev/null

echo "wrote $IMAGE"
