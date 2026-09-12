"""The page a story is laid out on, from the writer through to both renderers."""

from __future__ import annotations

from pathlib import Path

from limelight.app import (
    DEFAULT_BLOCK_GAP_MM,
    DEFAULT_PAGE_HEIGHT_MM,
    DEFAULT_PAGE_MARGIN_MM,
    DEFAULT_PAGE_WIDTH_MM,
    PageGeometry,
    StorySpacing,
    _page_geometry_from_story,
    _story_spacing_from_story,
    story_rhythm_css,
    story_run_edge_css,
)
from limelight.pdf_export import (
    CONTINUOUS_PAGE_HEIGHT_MM,
    FALLBACK_PAGE_WIDTH_MM,
    printed_page,
)
from limelight.reader import open_limelight
from limelight.writer import LimelightProject
from limelight.writer import PageGeometry as WrittenPageGeometry
from limelight.writer import StorySpacing as WrittenStorySpacing


def _story(
    width: object,
    height: object,
    margin_lr: float = 15.0,
    margin_tb: float = 15.0,
) -> dict[str, object]:
    """A story as dhall-to-json renders one: a union is its payload, or its name."""

    return {"page": {"width": width, "height": height, "marginLR": margin_lr, "marginTB": margin_tb}}


def test_a_measured_page_keeps_its_millimetres() -> None:
    geometry = _page_geometry_from_story(_story(210.0, 297.0, margin_lr=20.0, margin_tb=10.0))

    assert (geometry.width_mm, geometry.height_mm) == (210.0, 297.0)
    assert (geometry.margin_lr_mm, geometry.margin_tb_mm) == (20.0, 10.0)
    # The column is the page less the side margins; the top and bottom ones
    # do not narrow it.
    assert geometry.content_width_mm == 170.0


def test_a_page_with_one_margin_puts_it_on_every_side() -> None:
    # Written before the margin was split into sides.
    geometry = _page_geometry_from_story(
        {"page": {"width": 210.0, "height": 297.0, "margin": 12.0}}
    )

    assert (geometry.margin_lr_mm, geometry.margin_tb_mm) == (12.0, 12.0)


def test_a_viewport_width_and_continuous_height_have_no_measurement() -> None:
    geometry = _page_geometry_from_story(_story("viewport", "continuous"))

    assert geometry.width_mm is None
    assert geometry.height_mm is None
    assert geometry.content_width_mm is None
    assert geometry.content_width_px(96) is None


def test_a_package_without_a_page_falls_back_to_what_it_was_built_as() -> None:
    geometry = _page_geometry_from_story({})

    assert geometry.width_mm == DEFAULT_PAGE_WIDTH_MM
    assert geometry.height_mm == DEFAULT_PAGE_HEIGHT_MM
    assert (geometry.margin_lr_mm, geometry.margin_tb_mm) == (
        DEFAULT_PAGE_MARGIN_MM,
        DEFAULT_PAGE_MARGIN_MM,
    )


def test_millimetres_become_pixels_at_the_screen_resolution() -> None:
    geometry = _page_geometry_from_story(_story(210.0, 297.0))

    # 180mm of column at 96 dots per inch.
    assert geometry.content_width_px(96) == 680
    assert geometry.content_width_px(192) == 1361
    assert geometry.margin_lr_px(96) == 57
    assert geometry.margin_tb_px(96) == 57


class _PageRuntime:
    def __init__(self, geometry: PageGeometry) -> None:
        self.page_geometry = geometry


def test_a_continuous_page_prints_as_one_long_sheet() -> None:
    page = printed_page(_PageRuntime(PageGeometry(170.0, None, 12.0, 8.0)))

    assert page.width_mm == 170.0
    assert page.height_mm == CONTINUOUS_PAGE_HEIGHT_MM
    assert page.content_width_mm == 146.0
    assert (page.margin_lr_mm, page.margin_tb_mm) == (12.0, 8.0)


def test_a_viewport_width_prints_on_a_real_page() -> None:
    # Paper has a size even when the screen does not.
    page = printed_page(_PageRuntime(PageGeometry(None, None, 15.0, 15.0)))

    assert page.width_mm == FALLBACK_PAGE_WIDTH_MM


def _built_page(tmp_path: Path, geometry: WrittenPageGeometry) -> dict[str, object]:
    project = LimelightProject(title="Page", authors=["Test"], page=geometry)
    project.set_story_markdown("# Page\n\nProse.\n")
    package = tmp_path / "page.limelight"
    project.write_folder(package, overwrite=True)
    with open_limelight(package) as opened:
        return opened.manifest_json()["story"]["page"]


def test_the_writer_records_a_paged_document(tmp_path: Path) -> None:
    page = _built_page(
        tmp_path,
        WrittenPageGeometry.paged(width_mm=148.0, height_mm=210.0, margin_lr_mm=12.0, margin_tb_mm=18.0),
    )

    assert page["width"] == 148.0
    assert page["height"] == 210.0
    assert (page["marginLR"], page["marginTB"]) == (12.0, 18.0)


def test_the_writer_records_a_continuous_document(tmp_path: Path) -> None:
    page = _built_page(tmp_path, WrittenPageGeometry.continuous(width_mm=170.0))

    assert page["width"] == 170.0
    assert page["height"] == "continuous"


def test_the_writer_records_a_fluid_document(tmp_path: Path) -> None:
    page = _built_page(tmp_path, WrittenPageGeometry.fluid())

    assert page["width"] == "viewport"
    assert page["height"] == "continuous"


def test_a_project_that_says_nothing_is_paged_a4(tmp_path: Path) -> None:
    project = LimelightProject(title="Page", authors=["Test"])
    project.set_story_markdown("# Page\n\nProse.\n")
    package = tmp_path / "default.limelight"
    project.write_folder(package, overwrite=True)

    with open_limelight(package) as opened:
        page = opened.manifest_json()["story"]["page"]

    assert (page["width"], page["height"]) == (210.0, 297.0)


def test_a_project_that_says_nothing_has_the_default_spacing(tmp_path: Path) -> None:
    project = LimelightProject(title="Page", authors=["Test"])
    project.set_story_markdown("# Page\n\nProse.\n")
    package = tmp_path / "default.limelight"
    project.write_folder(package, overwrite=True)

    with open_limelight(package) as opened:
        spacing = opened.manifest_json()["story"]["spacing"]

    assert spacing["blockGap"] == DEFAULT_BLOCK_GAP_MM


def test_the_writer_records_the_story_spacing(tmp_path: Path) -> None:
    project = LimelightProject(
        title="Page",
        authors=["Test"],
        spacing=WrittenStorySpacing(
            block_gap_mm=2.0,
            figure_gap_mm=6.0,
            heading_gap_before_mm=7.0,
            heading_gap_after_mm=1.5,
        ),
    )
    project.set_story_markdown("# Page\n\nProse.\n")
    package = tmp_path / "spaced.limelight"
    project.write_folder(package, overwrite=True)

    with open_limelight(package) as opened:
        story = opened.manifest_json()["story"]

    spacing = _story_spacing_from_story(story)
    assert spacing == StorySpacing(2.0, 6.0, 7.0, 1.5)


def test_a_package_without_spacing_is_set_as_it_was() -> None:
    spacing = _story_spacing_from_story({})

    assert spacing.block_gap_mm == DEFAULT_BLOCK_GAP_MM


def test_spacing_becomes_pixels_at_the_screen_resolution() -> None:
    spacing = StorySpacing(block_gap_mm=3.0, figure_gap_mm=4.5, heading_gap_before_mm=5.0, heading_gap_after_mm=2.0)

    assert spacing.block_gap_px(96) == 11
    assert spacing.figure_gap_px(96) == 17


def test_the_rhythm_stylesheet_spaces_every_kind_of_block() -> None:
    css = story_rhythm_css(StorySpacing(2.0, 6.0, 7.0, 1.5))

    # Prose, headings and figures each get their own gap, and a document's
    # outer edges get none.
    assert "p, ul, ol, pre, blockquote, table, hr, .math-display {\n  margin-top: 2mm;\n  margin-bottom: 2mm;" in css
    assert "h1, h2, h3, h4, h5, h6 {\n  margin-top: 7mm;\n  margin-bottom: 1.5mm;" in css
    assert "figure.limelight-figure, figure.limelight-story-figure {\n  margin-top: 6mm;\n  margin-bottom: 6mm;" in css
    assert "body > :first-child {\n  margin-top: 0;" in css
    assert "body > :last-child {\n  margin-bottom: 0;" in css


def test_a_run_of_prose_gives_its_edge_heading_and_figure_their_extra() -> None:
    css = story_run_edge_css(StorySpacing(2.0, 6.0, 7.0, 1.5))

    # The widget gap already supplies the block gap; only the difference is
    # added back inside the run.
    assert "body > h1:first-child, body > h2:first-child" in css
    assert "margin-top: 5mm;" in css
    assert "body > figure.limelight-story-figure:last-child {\n  margin-bottom: 4mm;" in css


def test_a_heading_gap_smaller_than_the_block_gap_adds_nothing_back() -> None:
    css = story_run_edge_css(StorySpacing(4.0, 4.0, 2.0, 1.0))

    assert "margin-top: 0mm;" in css
