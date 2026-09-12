"""Converting author-supplied images into the form a package ships.

A Limelight package bundles a converted copy of every image its story references
rather than the file the author edited. Converting rather than copying buys
three things:

* Size. A photograph off a phone is several thousand pixels wide, and no
  consumer of a story needs more than the print column can show.
* Safety. The packaged encodings are a closed set, so a format that can carry
  script never reaches the viewer.
* Privacy. A `.limelight` is signed and meant to be passed around. A photograph
  carries GPS coordinates and a camera serial number until something strips
  them.

Pillow is already in the dependency tree by way of matplotlib.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# Wide enough for the print column: A4 less its margins is roughly 180mm, which
# at the 220 dpi figures are rasterised for is about 1560 pixels. The story
# panel lays out at 900 and the rich text PDF fallback at 540, so one packaged
# variant covers every consumer.
DEFAULT_MAX_WIDTH = 1600
DEFAULT_JPEG_QUALITY = 85

PNG = "png"
JPEG = "jpeg"
PACKAGED_FORMATS = (PNG, JPEG)

_SUFFIXES = {PNG: ".png", JPEG: ".jpg"}

# Formats Pillow cannot open, listed so the failure names the real problem
# rather than surfacing a decoder error.
_UNSUPPORTED_SUFFIXES = {
    ".svg": "SVG is a vector format Pillow cannot rasterise; export it to PNG first",
    ".pdf": "PDF is not an image format; export the page to PNG first",
}


class ImageConversionError(Exception):
    """Raised when an image cannot be read or converted."""


@dataclass(frozen=True)
class ConvertedImage:
    """The packaged bytes of one image, and how they were produced."""

    data: bytes
    format: str
    width: int
    height: int
    source_sha256: str
    max_width: int
    quality: int | None

    @property
    def suffix(self) -> str:
        return _SUFFIXES[self.format]

    def sha256_prefix8(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:8]


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _packaged_format(image: Image.Image, source_format: str | None) -> str:
    """Choose the packaged encoding for an image.

    The rule is deliberately about what the image needs rather than what it
    depicts: transparency has to survive, a JPEG stays a JPEG rather than being
    re-encoded into a larger PNG, and everything else becomes PNG. A heuristic
    guessing "photograph" from pixel content would be unpredictable, and an
    author who disagrees can say so explicitly.
    """

    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        return PNG
    if source_format == "JPEG":
        return JPEG
    return PNG


def _without_metadata(image: Image.Image) -> Image.Image:
    """Copy the pixels into a fresh image, leaving every metadata chunk behind.

    Pillow carries `info` through `copy()`, and several plugins write parts of
    it back out on save. Rebuilding from raw pixel bytes is the one approach
    that cannot leak a field nobody thought to clear.
    """

    return Image.frombytes(image.mode, image.size, image.tobytes())


def convert_image(
    source_path: str | Path,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    format: str | None = None,
    quality: int | None = None,
) -> ConvertedImage:
    """Read an image and return the bytes a package should ship.

    The image is oriented according to its EXIF rotation, stripped of all
    metadata, and downscaled if it is wider than ``max_width``. It is never
    upscaled: a small diagram stays the size it was drawn.
    """

    path = Path(source_path)
    if not path.is_file():
        raise ImageConversionError(f"Image {str(path)!r} does not exist")

    unsupported = _UNSUPPORTED_SUFFIXES.get(path.suffix.lower())
    if unsupported is not None:
        raise ImageConversionError(f"Cannot package {str(path)!r}: {unsupported}")

    if format is not None and format not in PACKAGED_FORMATS:
        raise ImageConversionError(
            f"Unsupported packaged image format {format!r}; expected one of {', '.join(PACKAGED_FORMATS)}"
        )

    source_sha256 = sha256_of_file(path)

    try:
        with Image.open(path) as opened:
            source_format = opened.format
            oriented = ImageOps.exif_transpose(opened)
            image = _without_metadata(oriented)
    except OSError as error:
        raise ImageConversionError(f"Could not read image {str(path)!r}: {error}") from error

    packaged_format = format if format is not None else _packaged_format(image, source_format)

    if image.width > max_width:
        height = max(1, round(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)

    if packaged_format == JPEG and image.mode not in ("RGB", "L"):
        # JPEG has no alpha channel, and an image that reaches here with one
        # was asked for explicitly, so the transparent areas become white.
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background

    buffer = io.BytesIO()
    if packaged_format == JPEG:
        encoded_quality = DEFAULT_JPEG_QUALITY if quality is None else quality
        image.save(buffer, format="JPEG", quality=encoded_quality, optimize=True)
    else:
        encoded_quality = None
        image.save(buffer, format="PNG", optimize=True)

    return ConvertedImage(
        data=buffer.getvalue(),
        format=packaged_format,
        width=image.width,
        height=image.height,
        source_sha256=source_sha256,
        max_width=max_width,
        quality=encoded_quality,
    )
