from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from limelight.images import DEFAULT_MAX_WIDTH, ImageConversionError, convert_image


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """A wide JPEG carrying the sort of metadata a camera adds."""

    path = tmp_path / "photo.jpg"
    image = Image.new("RGB", (4032, 3024), (120, 90, 60))
    exif = Image.Exif()
    exif[271] = "ACME Phone"
    exif[272] = "Model X"
    exif[305] = "Camera Software 1.0"
    image.save(path, exif=exif, quality=90)
    return path


@pytest.fixture
def diagram(tmp_path: Path) -> Path:
    path = tmp_path / "diagram.png"
    Image.new("RGBA", (200, 120), (10, 20, 30, 128)).save(path)
    return path


def test_a_wide_photograph_is_downscaled(photo: Path) -> None:
    converted = convert_image(photo)

    assert converted.width == DEFAULT_MAX_WIDTH
    assert converted.height == 1200
    assert converted.format == "jpeg"


def test_a_small_image_is_never_upscaled(diagram: Path) -> None:
    converted = convert_image(diagram)

    assert (converted.width, converted.height) == (200, 120)


def test_metadata_does_not_survive_conversion(photo: Path) -> None:
    converted = convert_image(photo)

    packaged = Image.open(io.BytesIO(converted.data))
    assert dict(packaged.getexif()) == {}


def test_transparency_forces_png(diagram: Path) -> None:
    assert convert_image(diagram).format == "png"


def test_the_packaged_format_can_be_overridden(diagram: Path) -> None:
    converted = convert_image(diagram, format="jpeg", quality=70)

    assert converted.format == "jpeg"
    assert converted.quality == 70


def test_identity_is_the_source_not_the_packaged_bytes(photo: Path) -> None:
    # Two conversions of one file agree about the source, so a build cache and
    # the rolling-build hash both key off something an encoder cannot change.
    first = convert_image(photo)
    second = convert_image(photo, max_width=800)

    assert first.source_sha256 == second.source_sha256
    assert first.max_width != second.max_width


def test_a_rotated_photograph_is_uprighted(tmp_path: Path) -> None:
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (400, 200), (30, 60, 90))
    exif = Image.Exif()
    # Orientation 6 is the "rotate 90 clockwise to view" a phone writes when it
    # is held on its side. The pixels are unrotated until something applies it.
    exif[274] = 6
    image.save(path, exif=exif)

    converted = convert_image(path)

    assert (converted.width, converted.height) == (200, 400)


def test_a_vector_source_is_refused_with_an_explanation(tmp_path: Path) -> None:
    svg = tmp_path / "diagram.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")

    with pytest.raises(ImageConversionError, match="export it to PNG"):
        convert_image(svg)


def test_a_missing_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(ImageConversionError, match="does not exist"):
        convert_image(tmp_path / "nope.png")


def test_an_unknown_packaged_format_is_refused(diagram: Path) -> None:
    with pytest.raises(ImageConversionError, match="Unsupported packaged image format"):
        convert_image(diagram, format="webp")
