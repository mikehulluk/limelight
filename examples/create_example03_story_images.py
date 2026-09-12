"""Build the story-images example.

Unlike the other examples, this one does not generate its story in Python. The
story lives at `example03_story_images/story/index.md` with its images beside
it, exactly as an author would keep them, and the build reads that file. That
is the point of the example: the source tree is editable in any Markdown
editor, and everything Limelight does to the images happens on the way into the
package.
"""

from __future__ import annotations

from pathlib import Path

from limelight.writer import ImageWidth, LimelightProject, PageGeometry, SourceProvenance

ROOT = Path(__file__).resolve().parents[1]
STORY = Path(__file__).resolve().parent / "example03_story_images" / "story" / "index.md"


def build_project() -> LimelightProject:
    project = LimelightProject(
        title="Story Images",
        subtitle="Photographs and diagrams inside a story document",
        authors=["Limelight Examples"],
        page=PageGeometry.paged(margin_lr_mm=10.0),
        description="How images are authored, converted and numbered in a Limelight story.",
    )

    # Most images need no declaration at all - the story is the dependency
    # list, and discovery walks it. This one is declared to pin its id, so the
    # anchor a cross-reference points at cannot change if the file is renamed,
    # and to record where the picture came from.
    project.add_image(
        id="bench-rig",
        source_path=STORY.parent / "images" / "bench-rig.jpg",
        # Drawn at 90mm wherever it appears, without every reference repeating
        # the number. Unrelated to max_width, which is about stored quality.
        display_width=ImageWidth.millimetres(90),
        provenance=SourceProvenance(
            origin="Synthetic image generated for this example.",
            release_date="2026-09-09",
        ),
    )

    project.set_story_markdown_file(STORY)
    return project


def main() -> None:
    build_dir = ROOT / "_build" / "examples"
    build_dir.mkdir(parents=True, exist_ok=True)

    output = build_dir / "example03-story-images.limelight"
    build_project().write_folder(output, overwrite=True)
    print(output)


if __name__ == "__main__":
    main()
