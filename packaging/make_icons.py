#!/usr/bin/env python3
"""Render the application icon into the per-platform formats the installers need.

Produces:
  packaging/windows/limelight.ico   multi-resolution Windows icon
  packaging/macos/limelight.icns    macOS icon (built with iconutil when on macOS)
  packaging/linux/limelight.png     256px PNG for the .desktop entry

Rasterises with Qt and assembles the .ico with Pillow, so it needs nothing
beyond the project's own dependencies; the .icns still needs macOS, since only
iconutil writes one that macOS accepts. Run it after changing
src/limelight/assets/limelight-icon.svg.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# The build runners have no display, and rendering to a QImage needs none.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ICON = REPOSITORY_ROOT / "src" / "limelight" / "assets" / "limelight-icon.svg"
PACKAGING_ROOT = REPOSITORY_ROOT / "packaging"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)
DESKTOP_ICON_SIZE = 256


def rasterise(renderer: QSvgRenderer, png: Path, size: int) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(png), "PNG"):
        raise SystemExit(f"could not write {png}")


def build_ico(renderer: QSvgRenderer, staging: Path, destination: Path) -> None:
    # Each size is rendered from the vector rather than downsampled from the
    # largest, so the 16px entry gets the crisp edges the SVG describes.
    images = []
    for size in ICO_SIZES:
        png = staging / f"icon-{size}.png"
        rasterise(renderer, png, size)
        images.append(Image.open(png))

    destination.parent.mkdir(parents=True, exist_ok=True)
    images[-1].save(
        destination,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=images[:-1],
    )
    print(f"wrote {destination}")


def build_icns(renderer: QSvgRenderer, staging: Path, destination: Path) -> None:
    iconset = staging / "limelight.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in ICNS_SIZES:
        rasterise(renderer, iconset / f"icon_{size}x{size}.png", size)
        if size <= 512:
            rasterise(renderer, iconset / f"icon_{size}x{size}@2x.png", size * 2)

    destination.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("iconutil"):
        subprocess.run(
            ["iconutil", "--convert", "icns", "--output", str(destination), str(iconset)],
            check=True,
            capture_output=True,
        )
        print(f"wrote {destination}")
        return

    # Nothing but iconutil writes an .icns that macOS accepts, so skip rather
    # than produce a broken icon. The macOS build job runs this script before
    # PyInstaller and does have iconutil.
    print(
        f"skipping {destination.name}: iconutil is only available on macOS, "
        "so the icon is generated during the macOS build",
        file=sys.stderr,
    )


def main() -> int:
    if not SOURCE_ICON.is_file():
        raise SystemExit(f"{SOURCE_ICON} is missing")

    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SOURCE_ICON))
    if not renderer.isValid():
        raise SystemExit(f"{SOURCE_ICON} did not load as SVG")

    with TemporaryDirectory(prefix="limelight-icons-") as staging_name:
        staging = Path(staging_name)
        build_ico(renderer, staging, PACKAGING_ROOT / "windows" / "limelight.ico")
        build_icns(renderer, staging, PACKAGING_ROOT / "macos" / "limelight.icns")

        desktop_icon = PACKAGING_ROOT / "linux" / "limelight.png"
        desktop_icon.parent.mkdir(parents=True, exist_ok=True)
        rasterise(renderer, desktop_icon, DESKTOP_ICON_SIZE)
        print(f"wrote {desktop_icon}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
