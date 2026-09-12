"""Images from an author's source tree through to a packaged story."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from limelight.app import LimelightRuntime
from limelight.images import ImageConversionError
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import optional_field, validate_manifest_semantics
from limelight.signing import generate_ed25519_keypair, verify_manifest_signatures
from limelight.story_markdown import CrossReferenceError
from limelight.writer import LimelightProject


def _write_image(path: Path, size: tuple[int, int] = (2400, 1600)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (90, 120, 150)).save(path)
    return path


def _story_source(tmp_path: Path, markdown: str) -> Path:
    """An author's source tree: a Markdown file with images beside it."""

    source = tmp_path / "story"
    source.mkdir(parents=True, exist_ok=True)
    _write_image(source / "images" / "rig.png")
    (source / "index.md").write_text(markdown, encoding="utf-8")
    return source / "index.md"


def _build(tmp_path: Path, project: LimelightProject) -> Path:
    package = tmp_path / "out.limelight"
    project.write_folder(package, overwrite=True)
    return package


def test_an_image_is_converted_copied_and_the_story_rewritten(tmp_path: Path) -> None:
    story = _story_source(tmp_path, "# Test\n\n![Bench setup](images/rig.png)\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)

    package = _build(tmp_path, project)

    packaged_story = (package / "story" / "index.md").read_text(encoding="utf-8")
    assert "images/rig.png" not in packaged_story
    assert "assets/images/rig-" in packaged_story

    with open_limelight(package) as opened:
        manifest = opened.manifest_json()
        validate_manifest_semantics(manifest)
        asset = manifest["assets"][0]

    assert asset["id"] == "rig"
    assert asset["format"] == "png"
    assert (package / asset["path"]).is_file()
    # Downscaled from 2400 on the way in.
    with Image.open(package / asset["path"]) as packaged:
        assert packaged.width == 1600


def test_one_image_referenced_twice_is_packaged_once(tmp_path: Path) -> None:
    story = _story_source(
        tmp_path,
        "# Test\n\n![First](images/rig.png)\n\nProse.\n\n![Again][rig]\n\n[rig]: images/rig.png\n",
    )
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)

    package = _build(tmp_path, project)

    with open_limelight(package) as opened:
        assets = opened.manifest_json()["assets"]

    assert len(assets) == 1
    assert len(list((package / "assets" / "images").iterdir())) == 1


def test_a_figure_image_is_numbered_and_an_inline_one_is_not(tmp_path: Path) -> None:
    source = tmp_path / "story"
    _write_image(source / "images" / "rig.png")
    _write_image(source / "images" / "icon.png", size=(64, 64))
    (source / "index.md").write_text(
        "# Test\n\n![Bench](images/rig.png)\n\nProse with ![icon](images/icon.png) inline.\n",
        encoding="utf-8",
    )
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(source / "index.md")

    package = _build(tmp_path, project)

    with open_limelight(package) as opened:
        assets = {asset["id"]: asset for asset in opened.manifest_json()["assets"]}

    assert optional_field(assets["rig"], "storyNumber") == 1
    assert optional_field(assets["icon"], "storyNumber") is None


def test_add_image_pins_the_id_and_the_conversion(tmp_path: Path) -> None:
    story = _story_source(tmp_path, "# Test\n\n![Bench](images/rig.png)\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.add_image(
        id="bench-rig",
        source_path=tmp_path / "story" / "images" / "rig.png",
        max_width=400,
    )
    project.set_story_markdown_file(story)

    package = _build(tmp_path, project)

    with open_limelight(package) as opened:
        asset = opened.manifest_json()["assets"][0]

    assert asset["id"] == "bench-rig"
    with Image.open(package / asset["path"]) as packaged:
        assert packaged.width == 400


def test_a_generated_story_goes_through_the_same_pipeline(tmp_path: Path) -> None:
    _write_image(tmp_path / "photos" / "rig.png")
    project = LimelightProject(title="Test", authors=["Test"])
    reference = project.add_image(id="rig", source_path="photos/rig.png")
    project.set_story_markdown(
        f"# Test\n\n![Bench setup]({reference})\n",
        base_dir=tmp_path,
    )

    package = _build(tmp_path, project)

    with open_limelight(package) as opened:
        assets = opened.manifest_json()["assets"]

    assert len(assets) == 1
    assert (package / assets[0]["path"]).is_file()


def test_a_remote_image_is_refused(tmp_path: Path) -> None:
    story = _story_source(tmp_path, "# Test\n\n![Remote](https://example.com/rig.png)\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)

    with pytest.raises(ImageConversionError, match="self-contained"):
        project.write_folder(tmp_path / "out.limelight", overwrite=True)


def test_a_missing_image_fails_the_build_naming_the_file(tmp_path: Path) -> None:
    story = _story_source(tmp_path, "# Test\n\n![Gone](images/absent.png)\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)

    with pytest.raises(ImageConversionError, match="absent.png"):
        project.write_folder(tmp_path / "out.limelight", overwrite=True)


def test_an_undeclared_image_is_caught_when_the_package_is_opened(tmp_path: Path) -> None:
    story = _story_source(tmp_path, "# Test\n\n![Bench](images/rig.png)\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)
    package = _build(tmp_path, project)

    # Stand in for a package edited after it was built.
    story_path = package / "story" / "index.md"
    story_path.write_text("# Test\n\n![Bench](assets/images/nope.png)\n", encoding="utf-8")

    with open_limelight(package) as opened:
        with pytest.raises(LimelightError, match="not a declared asset"):
            LimelightRuntime(opened, opened.manifest_json())


def test_the_story_signature_covers_the_rewritten_document(tmp_path: Path) -> None:
    # Signing has to happen after the image references are rewritten, or the
    # signature would cover a document no reader ever loads.
    private_key, _ = generate_ed25519_keypair()
    story = _story_source(tmp_path, "# Test\n\n![Bench](images/rig.png)\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story, signers=[("tester", private_key)])

    package = _build(tmp_path, project)

    with open_limelight(package) as opened:
        checks = verify_manifest_signatures(opened, opened.manifest_json())

    assert [check.ok for check in checks] == [True]
    assert checks[0].signer == "tester"


def test_a_cross_reference_is_resolved_into_the_packaged_prose(tmp_path: Path) -> None:
    story = _story_source(
        tmp_path,
        "# Test\n\nThe rig is in [Figure](#rig).\n\n![Bench](images/rig.png)\n",
    )
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)

    package = _build(tmp_path, project)

    packaged = (package / "story" / "index.md").read_text(encoding="utf-8")
    assert "[Figure 1](#rig)" in packaged


def test_figures_and_images_are_numbered_as_a_reader_meets_them(tmp_path: Path) -> None:
    story = _story_source(
        tmp_path,
        "# Test\n\n@figure(fig-a)\n\n![Bench](images/rig.png)\n\n"
        "Both in [Figure](#fig-a) and [Figure](#rig).\n",
    )
    project = LimelightProject(title="Test", authors=["Test"])
    project.add_figure_view(id="fig-a", ref="unused")
    project.set_story_markdown_file(story)

    resolved = project.resolve_story()

    assert "[Figure 1](#fig-a)" in resolved.markdown
    assert "[Figure 2](#rig)" in resolved.markdown


def test_a_cross_reference_to_nothing_fails_the_build(tmp_path: Path) -> None:
    story = _story_source(tmp_path, "# Test\n\nSee [Figure](#missing).\n")
    project = LimelightProject(title="Test", authors=["Test"])
    project.set_story_markdown_file(story)

    with pytest.raises(CrossReferenceError, match="#missing"):
        project.write_folder(tmp_path / "out.limelight", overwrite=True)
