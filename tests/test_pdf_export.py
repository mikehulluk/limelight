from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import pytest

from limelight.app import PageGeometry, StorySpacing, TablePreview
from limelight.typography import Typography, resolve_fonts
from limelight.pdf_export import (
    PdfExportError,
    StoryPdfRenderers,
    build_story_pdf_html,
    default_pdf_path,
    is_story_text_block,
)


@dataclass(frozen=True)
class FakeFigureViewState:
    figure_id: str
    index: int | None = None
    actions: tuple[dict[str, Any], ...] = ()
    figure_view_id: str = "figure-view-0"


class FakeRuntime:
    """The slice of LimelightRuntime the PDF builder actually reads."""

    def __init__(
        self,
        *,
        story_blocks: Sequence[Any],
        figure_specs: dict[str, dict[str, Any]] | None = None,
        project_title: str = "Test Project",
        table_preview: TablePreview | None = None,
    ) -> None:
        self.story_blocks = list(story_blocks)
        self.figure_specs = {} if figure_specs is None else figure_specs
        self.project_title = project_title
        self.page_geometry = PageGeometry(
            width_mm=210.0, height_mm=297.0, margin_lr_mm=15.0, margin_tb_mm=20.0
        )
        self.story_spacing = StorySpacing(
            block_gap_mm=3.0, figure_gap_mm=4.5, heading_gap_before_mm=5.0, heading_gap_after_mm=2.0
        )
        self.control_parameter_values: dict[str, Any] = {}
        self.typography = Typography()
        self.fonts = resolve_fonts({"story": {}}, None)
        self._table_preview = table_preview

    def figure_view_heading(self, figure_spec_id: str, *, index: int | None = None) -> str:
        title = self.figure_specs[figure_spec_id]["title"]
        if index is None:
            return title
        return f"Figure {index}. {title}"

    def table_preview(
        self,
        source_id: str,
        columns: Any = None,
        *,
        column_formats: dict[str, str] | None = None,
    ) -> TablePreview:
        assert self._table_preview is not None
        return self._table_preview


def plot_figure_spec(figure_id: str, *, caption: str | None = None) -> dict[str, Any]:
    return {
        "id": figure_id,
        "title": "Populations over time",
        "caption": caption,
        "tableViewSpecs": [],
        "axesSpecs": [],
    }


def make_renderers(
    *,
    png_bytes: bytes = b"\x89PNG-not-a-real-image",
    calls: list[dict[str, Any]] | None = None,
) -> StoryPdfRenderers:
    def render_markdown_html(markdown: str) -> str:
        return f"<p>{markdown}</p>"

    def figure_view_state_for_block(block: Any) -> FakeFigureViewState | None:
        if "figureView" in block:
            return FakeFigureViewState(figure_id=block["figureView"], index=block.get("index", 1))
        return None

    def render_figure_png(**kwargs: Any) -> bytes:
        if calls is not None:
            calls.append(kwargs)
        return png_bytes

    return StoryPdfRenderers(
        render_markdown_html=render_markdown_html,
        figure_view_state_for_block=figure_view_state_for_block,
        render_figure_png=render_figure_png,
    )


def test_is_story_text_block_accepts_strings_and_markdown_payloads():
    assert is_story_text_block("plain text")
    assert is_story_text_block({"markdown": "# Heading"})
    assert not is_story_text_block({"figureView": "figure-view-0"})
    assert not is_story_text_block({"markdown": ""})


def test_story_opening_with_a_heading_does_not_get_a_second_title():
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "# Predator Prey\n\nSome text."}],
        project_title="Predator Prey",
    )

    document = build_story_pdf_html(runtime, make_renderers())

    assert document.count("Predator Prey") == 2  # <title> and the markdown heading
    assert "<h1>Predator Prey</h1>" not in document


def test_story_without_a_heading_gets_the_project_title():
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "Just a paragraph."}],
        project_title="Predator Prey",
    )

    document = build_story_pdf_html(runtime, make_renderers())

    assert "<h1>Predator Prey</h1>" in document


def test_figures_are_embedded_as_images_with_their_caption():
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "# Title"}, {"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations", caption="Prey lags predator.")},
    )

    document = build_story_pdf_html(runtime, make_renderers())

    assert 'src="data:image/png;base64,' in document
    # The number is written under the figure, bold, then the caption.
    assert '<figcaption><span class=\"limelight-caption-label\">Figure 1.</span> Prey lags predator.</figcaption>' in document
    # The title is drawn into the image itself, so it must not repeat here.
    assert "limelight-figure-heading" not in document.split("</style>")[1]


def test_figures_render_at_the_requested_export_dpi():
    calls: list[dict[str, Any]] = []
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    build_story_pdf_html(runtime, make_renderers(calls=calls))

    assert len(calls) == 1
    assert calls[0]["dpi"] > 110
    assert calls[0]["figure_id"] == "populations"


def test_figure_without_a_caption_is_captioned_with_its_title():
    # The number a cross-reference points at has to be printed somewhere.
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations", caption=None)},
    )

    document = build_story_pdf_html(runtime, make_renderers())

    assert '<figcaption><span class=\"limelight-caption-label\">Figure 1.</span> Populations over time</figcaption>' in document


def test_table_view_figures_render_as_html_tables():
    figure_spec = {
        "id": "summary",
        "title": "Prey summary",
        "caption": "Computed at build time.",
        "tableViewSpecs": [
            {
                "data": "prey",
                "columns": None,
                "columnFormats": [],
                "headerStyle": ["Bold"],
                "cellStyles": [{"selector": {"row": None, "column": 0}, "styles": ["Italic"]}],
            }
        ],
    }
    preview = TablePreview(
        title="Prey summary",
        columns=["statistic", "prey"],
        rows=[["min", "0.51"], ["max", "13.68"]],
        total_rows=2,
        truncated=False,
        column_kinds=["text", "numeric"],
    )
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "summary"}],
        figure_specs={"summary": figure_spec},
        table_preview=preview,
    )

    document = build_story_pdf_html(runtime, make_renderers())

    assert '<table class="limelight-table">' in document
    assert "<th class=\"limelight-bold\"" in document
    assert "limelight-italic" in document
    assert "text-align: right" in document
    # Tables have no drawn-in title, so they do get a heading; the number
    # stays under the figure with the caption, as for every other kind.
    assert '<p class="limelight-figure-heading">Prey summary</p>' in document
    assert '<figcaption><span class=\"limelight-caption-label\">Figure 1.</span> Computed at build time.</figcaption>' in document


def test_unknown_figure_becomes_a_notice_instead_of_raising():
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "missing"}],
        figure_specs={},
    )

    document = build_story_pdf_html(runtime, make_renderers())

    assert "limelight-notice" in document
    assert "missing" in document


def test_a_failing_figure_does_not_abort_the_document():
    def exploding_renderers() -> StoryPdfRenderers:
        renderers = make_renderers()

        def render_figure_png(**kwargs: Any) -> bytes:
            raise ValueError("no data for you")

        return StoryPdfRenderers(
            render_markdown_html=renderers.render_markdown_html,
            figure_view_state_for_block=renderers.figure_view_state_for_block,
            render_figure_png=render_figure_png,
        )

    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}, {"markdown": "Following text."}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    document = build_story_pdf_html(runtime, exploding_renderers())

    assert "no data for you" in document
    assert "Following text." in document


def test_progress_callback_reports_every_figure_and_can_cancel():
    seen: list[tuple[int, int]] = []
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}, {"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    def progress(number: int, total: int) -> bool:
        seen.append((number, total))
        return True

    build_story_pdf_html(runtime, make_renderers(), on_figure_progress=progress)
    assert seen == [(1, 2), (2, 2)]

    def cancel_immediately(number: int, total: int) -> bool:
        return False

    with pytest.raises(PdfExportError):
        build_story_pdf_html(runtime, make_renderers(), on_figure_progress=cancel_immediately)


def test_default_pdf_path_sits_beside_the_package(tmp_path):
    package = tmp_path / "example01-lotka-volterra.limelight"
    assert default_pdf_path(package) == tmp_path / "example01-lotka-volterra.pdf"


def test_the_page_margins_and_block_spacing_reach_the_print_stylesheet():
    runtime = FakeRuntime(story_blocks=[{"markdown": "Just a paragraph."}])

    document = build_story_pdf_html(runtime, make_renderers())

    # Top and bottom first, then the sides, as CSS reads a two-value margin.
    assert "margin: 20.0mm 15.0mm;" in document
    assert "p, ul, ol, pre, blockquote, table, hr, .math-display {\n  margin-top: 3mm;" in document
    assert "figure.limelight-figure, figure.limelight-story-figure {\n  margin-top: 4.5mm;" in document
