#!/usr/bin/env python3
"""Copy the parts of MathJax the story view uses into src/limelight/assets/mathjax.

Produces:
  src/limelight/assets/mathjax/mathjax/          MathJax: TeX in, SVG out, and what it loads on demand
  src/limelight/assets/mathjax/fonts/<font>/     each maths font's SVG glyphs

The story view sets maths with MathJax from these files rather than from a
CDN, so a document's maths is set without a network. The packages come from
the npm registry at MATHJAX_VERSION; only the files the SVG output can load
are kept (about 20 MB of the packages' 85). Run it after changing the version,
or after adding a maths font to typography.MATHS_FONTS.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

MATHJAX_VERSION = "4.1.3"

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from limelight.typography import MATHS_FONTS  # noqa: E402

DESTINATION = REPOSITORY_ROOT / "src" / "limelight" / "assets" / "mathjax"

# tex-svg.js carries the TeX input and SVG output; the rest it loads as a
# story needs them: TeX extensions for the macros that are not built in, and
# the menu and accessibility (speech) code when a reader asks for them.
MATHJAX_PARTS = ["tex-svg.js", "input/tex/extensions", "ui", "a11y", "sre", "LICENSE", "package.json"]
# A font's SVG glyphs, the common ones in svg.js and the rest loaded as a story uses them.
FONT_PARTS = ["svg.js", "svg/dynamic", "package.json"]


def _fetch_package(name: str, staging: Path) -> Path:
    """The npm package ``name`` at MATHJAX_VERSION, unpacked under ``staging``."""

    tarball = f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{MATHJAX_VERSION}.tgz"
    print(f"Fetching {tarball}")
    with urllib.request.urlopen(tarball) as response:
        data = response.read()
    target = staging / name.split("/")[-1]
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        archive.extractall(target, filter="data")
    unpacked = target / "package"
    version = json.loads((unpacked / "package.json").read_text())["version"]
    if version != MATHJAX_VERSION:
        raise SystemExit(f"{name}: expected version {MATHJAX_VERSION}, got {version}")
    return unpacked


def _copy_parts(source: Path, destination: Path, parts: list[str]) -> None:
    for part in parts:
        origin = source / part
        if not origin.exists():
            raise SystemExit(f"{source.name}: has no {part}")
        target = destination / part
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)


def main() -> None:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="limelight-mathjax-") as staging_directory:
        staging = Path(staging_directory)
        output = staging / "out"
        _copy_parts(_fetch_package("mathjax", staging), output / "mathjax", MATHJAX_PARTS)
        for font in MATHS_FONTS.values():
            package = f"@mathjax/{font.mathjax}-font"
            _copy_parts(_fetch_package(package, staging), output / "fonts" / f"{font.mathjax}-font", FONT_PARTS)
        shutil.copy2(DESTINATION / "README.md", output / "README.md")
        shutil.rmtree(DESTINATION)
        shutil.copytree(output, DESTINATION)
    size = sum(path.stat().st_size for path in DESTINATION.rglob("*") if path.is_file())
    print(f"Wrote MathJax {MATHJAX_VERSION} to {DESTINATION} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
