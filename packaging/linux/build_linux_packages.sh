#!/usr/bin/env bash
# Wrap the PyInstaller bundle as a .deb and an AppImage.
#
# Expects dist/limelight/ to exist (see packaging/limelight.spec). Writes both artifacts
# to dist/installer/.
#
#   pyinstaller packaging/limelight.spec --noconfirm
#   packaging/linux/build_linux_packages.sh [version]
#
# The .deb needs dpkg-deb. The AppImage needs appimagetool; if it is not on PATH
# the script downloads it, and is skipped entirely when that is not possible.

set -euo pipefail

VERSION="${1:-0.0.1}"
ARCH="amd64"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUNDLE="$REPOSITORY_ROOT/dist/limelight"
OUTPUT="$REPOSITORY_ROOT/dist/installer"

if [ ! -x "$BUNDLE/limelight" ]; then
  echo "error: $BUNDLE/limelight not found. Build the PyInstaller bundle first." >&2
  exit 1
fi

mkdir -p "$OUTPUT"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

# ---------------------------------------------------------------- .deb --------
# The bundle goes to /opt so its private copies of Qt and friends never shadow
# the distribution's, with a launcher symlinked onto PATH.
build_deb() {
  if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "skipping .deb: dpkg-deb not available" >&2
    return
  fi

  local root="$STAGING/deb"
  mkdir -p "$root/DEBIAN" \
           "$root/opt/limelight" \
           "$root/usr/bin" \
           "$root/usr/share/applications" \
           "$root/usr/share/mime/packages" \
           "$root/usr/share/icons/hicolor/256x256/apps"

  cp -a "$BUNDLE/." "$root/opt/limelight/"
  cp "$SCRIPT_DIR/limelight.desktop" "$root/usr/share/applications/limelight.desktop"
  cp "$SCRIPT_DIR/limelight-mime.xml" "$root/usr/share/mime/packages/limelight.xml"
  cp "$SCRIPT_DIR/limelight.png" "$root/usr/share/icons/hicolor/256x256/apps/limelight.png"
  ln -s /opt/limelight/limelight "$root/usr/bin/limelight"

  local installed_size
  installed_size="$(du -sk "$root/opt" | cut -f1)"

  cat > "$root/DEBIAN/control" <<CONTROL
Package: limelight
Version: $VERSION
Section: science
Priority: optional
Architecture: $ARCH
Maintainer: Mike Hull <mikehulluk@gmail.com>
Installed-Size: $installed_size
Depends: libc6, libglib2.0-0, libnss3, libxkbcommon0, libxcomposite1, libxdamage1, libxrandr2, libasound2 | libasound2t64
Description: Limelight package viewer
 Limelight opens .limelight data packages and presents their story, figures,
 datasets and control parameters, and can export the story to PDF.
 .
 Everything the application needs, including Qt, is bundled under
 /opt/limelight.
CONTROL

  # The MIME type and desktop entry only take effect once their caches are
  # rebuilt, so refresh them on install and again after removal.
  cat > "$root/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  update-mime-database /usr/share/mime >/dev/null 2>&1 || true
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  gtk-update-icon-cache --quiet /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
POSTINST

  cat > "$root/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  update-mime-database /usr/share/mime >/dev/null 2>&1 || true
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  gtk-update-icon-cache --quiet /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
POSTRM

  chmod 0755 "$root/DEBIAN/postinst" "$root/DEBIAN/postrm"

  # Qt's own libraries live in /opt; do not let dpkg-shlibdeps guess otherwise.
  dpkg-deb --build --root-owner-group "$root" \
    "$OUTPUT/limelight_${VERSION}_${ARCH}.deb" >/dev/null
  echo "wrote $OUTPUT/limelight_${VERSION}_${ARCH}.deb"
}

# ------------------------------------------------------------ AppImage --------
build_appimage() {
  local tool
  tool="$(command -v appimagetool || true)"

  if [ -z "$tool" ]; then
    tool="$STAGING/appimagetool"
    local url="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    if ! curl -fsSL -o "$tool" "$url"; then
      echo "skipping AppImage: could not download appimagetool" >&2
      return
    fi
    chmod +x "$tool"
  fi

  local root="$STAGING/AppDir"
  mkdir -p "$root/usr/bin" "$root/usr/share/applications" \
           "$root/usr/share/mime/packages" \
           "$root/usr/share/icons/hicolor/256x256/apps"

  cp -a "$BUNDLE/." "$root/usr/bin/"
  cp "$SCRIPT_DIR/limelight.desktop" "$root/usr/share/applications/limelight.desktop"
  cp "$SCRIPT_DIR/limelight.desktop" "$root/limelight.desktop"
  # Desktop integration tools read the MIME definition out of the AppDir.
  cp "$SCRIPT_DIR/limelight-mime.xml" "$root/usr/share/mime/packages/limelight.xml"
  cp "$SCRIPT_DIR/limelight.png" "$root/usr/share/icons/hicolor/256x256/apps/limelight.png"
  cp "$SCRIPT_DIR/limelight.png" "$root/limelight.png"

  cat > "$root/AppRun" <<'APPRUN'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/usr/bin/limelight" "$@"
APPRUN
  chmod +x "$root/AppRun"

  # appimagetool needs a writable, FUSE-free path when running in CI.
  if ! ARCH=x86_64 "$tool" --appimage-extract-and-run "$root" \
       "$OUTPUT/Limelight-${VERSION}-x86_64.AppImage" >/dev/null 2>&1; then
    echo "skipping AppImage: appimagetool failed" >&2
    return
  fi
  echo "wrote $OUTPUT/Limelight-${VERSION}-x86_64.AppImage"
}

build_deb
build_appimage
