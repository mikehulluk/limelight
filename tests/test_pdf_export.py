from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pytest

from limelight.app import PageGeometry, StorySpacing, TablePreview, TimingProbe
from limelight.typography import Typography, resolve_fonts
from limelight.pdf_export import (
    DEFAULT_RASTER_DPI,
    DEFAULT_RASTERIZE_ABOVE_POINTS,
    FigurePdfOptions,
    PdfExportError,
    StoryPdfRenderers,
    artist_point_count,
    build_story_typst,
    default_pdf_path,
    is_story_text_block,
    rasterize_large_artists,
    write_story_pdf,
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
        page_geometry: PageGeometry | None = None,
    ) -> None:
        self.story_blocks = list(story_blocks)
        self.figure_specs = {} if figure_specs is None else figure_specs
        self.project_title = project_title
        self.page_geometry = page_geometry or PageGeometry(
            width_mm=210.0, height_mm=297.0, margin_lr_mm=15.0, margin_tb_mm=20.0
        )
        self.story_spacing = StorySpacing(
            block_gap_mm=3.0, figure_gap_mm=4.5, heading_gap_before_mm=5.0, heading_gap_after_mm=2.0
        )
        self.control_parameter_values: dict[str, Any] = {}
        self.typography = Typography()
        self.fonts = resolve_fonts({"story": {}}, None)
        self.timing = TimingProbe()
        self.image_assets_by_path: dict[str, dict[str, Any]] = {}
        self._table_preview = table_preview

    def figure_view_heading(self, figure_spec_id: str, *, index: int | None = None) -> str:
        title = self.figure_specs[figure_spec_id]["title"]
        if index is None:
            return title
        return f"Figure {index}. {title}"

    def image_bytes(self, src: str) -> bytes | None:
        return None

    def image_number(self, src: str) -> int | None:
        return None

    def image_anchor(self, src: str) -> str | None:
        return None

    def image_display_width(self, src: str) -> str | None:
        return None

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
    calls: list[dict[str, Any]] | None = None,
) -> StoryPdfRenderers:
    def figure_view_state_for_block(block: Any) -> FakeFigureViewState | None:
        if "figureView" in block:
            return FakeFigureViewState(
                figure_id=block["figureView"],
                index=block.get("index", 1),
                figure_view_id=f"view-{block['figureView']}",
            )
        return None

    def render_figure_pdf(**kwargs: Any) -> Path:
        if calls is not None:
            calls.append(kwargs)
        _write_tiny_pdf_figure(kwargs["output_path"], kwargs["width"], kwargs["height"])
        return kwargs["output_path"]

    return StoryPdfRenderers(
        figure_view_state_for_block=figure_view_state_for_block,
        render_figure_pdf=render_figure_pdf,
    )


def _write_tiny_pdf_figure(path: Path, width: int, height: int) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width / 96, height / 96), dpi=96)
    FigureCanvasAgg(figure)
    figure.add_subplot(111).plot([0, 1], [1, 0])
    figure.savefig(path, format="pdf")


def test_is_story_text_block_accepts_strings_and_markdown_payloads():
    assert is_story_text_block("plain text")
    assert is_story_text_block({"markdown": "# Heading"})
    assert not is_story_text_block({"figureView": "figure-view-0"})
    assert not is_story_text_block({"markdown": ""})


def test_story_opening_with_a_heading_does_not_get_a_second_title(tmp_path):
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "# Predator Prey\n\nSome text."}],
        project_title="Predator Prey",
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert source.count("= Predator Prey") == 1


def test_story_without_a_heading_gets_the_project_title(tmp_path):
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "Just a paragraph."}],
        project_title="Predator Prey",
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert "\n= Predator Prey\n" in source


def test_figures_are_drawn_to_pdf_and_placed_with_their_caption(tmp_path):
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "# Title"}, {"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations", caption="Prey lags predator.")},
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert (tmp_path / "figures" / "view-populations.pdf").is_file()
    assert '#ll-figure(image("figures/view-populations.pdf", width: 100%), width: 100.00%' in source
    # The number is written under the figure, in the label's style, then the caption.
    assert "caption: [#ll-caption[#ll-caption-label[Figure 1.] Prey lags predator.]]" in source
    # A cross-reference reaches the figure by its view's id.
    assert '#label("view-populations")' in source


def test_figures_are_laid_out_to_the_text_column(tmp_path):
    calls: list[dict[str, Any]] = []
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    build_story_typst(runtime, make_renderers(calls=calls), tmp_path)

    assert len(calls) == 1
    # 180 mm of column at CSS resolution, so a point in the figure is a point on the page.
    assert calls[0]["width"] == 680
    assert calls[0]["figure_id"] == "populations"


def test_a_figure_shown_twice_is_labelled_once(tmp_path):
    # Typst refuses a label used twice; a cross-reference goes to the first.
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}, {"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert source.count('#label("view-populations")') == 1


def test_figure_without_a_caption_is_captioned_with_its_title(tmp_path):
    # The number a cross-reference points at has to be printed somewhere.
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations", caption=None)},
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert "#ll-caption[#ll-caption-label[Figure 1.] Populations over time]" in source


def test_table_view_figures_render_as_typst_tables(tmp_path):
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

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert "#table(\n  columns: 2,\n  align: (left, right,)," in source
    assert "[#ll-table-header[#text(weight: 700)[statistic]]]" in source
    assert "[#ll-table-cell[#emph[min]]]" in source
    assert "[#ll-table-cell[0\\.51]]" in source
    # Tables have no drawn-in title, so they do get a heading; the number
    # stays under the figure with the caption, as for every other kind.
    assert "#ll-table-heading[Prey summary]" in source
    assert "#ll-caption-label[Figure 1.] Computed at build time." in source


def test_unknown_figure_becomes_a_notice_instead_of_raising(tmp_path):
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "missing"}],
        figure_specs={},
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert "#ll-notice[Unknown figure 'missing']" in source


def test_a_failing_figure_does_not_abort_the_document(tmp_path):
    def render_figure_pdf(**kwargs: Any) -> Path:
        raise ValueError("no data for you")

    renderers = StoryPdfRenderers(
        figure_view_state_for_block=make_renderers().figure_view_state_for_block,
        render_figure_pdf=render_figure_pdf,
    )
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}, {"markdown": "Following text."}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    source = build_story_typst(runtime, renderers, tmp_path)

    assert "no data for you" in source
    assert "Following text." in source


def test_progress_callback_reports_every_figure_and_can_cancel(tmp_path):
    seen: list[tuple[int, int]] = []
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}, {"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    def progress(number: int, total: int) -> bool:
        seen.append((number, total))
        return True

    build_story_typst(runtime, make_renderers(), tmp_path, on_figure_progress=progress)
    assert seen == [(1, 2), (2, 2)]

    def cancel_immediately(number: int, total: int) -> bool:
        return False

    with pytest.raises(PdfExportError):
        build_story_typst(runtime, make_renderers(), tmp_path, on_figure_progress=cancel_immediately)


def test_default_pdf_path_sits_beside_the_package(tmp_path):
    package = tmp_path / "example01-lotka-volterra.limelight"
    assert default_pdf_path(package) == tmp_path / "example01-lotka-volterra.pdf"


def test_the_page_margins_and_block_spacing_reach_the_document(tmp_path):
    runtime = FakeRuntime(story_blocks=[{"markdown": "Just a paragraph."}])

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert "#set page(width: 210mm, height: 297mm, margin: (x: 15mm, y: 20mm))" in source
    assert "#set block(spacing: 3mm)" in source
    assert "above: 4.5mm, below: 4.5mm," in source
    assert "#show heading: set block(above: 5mm, below: 2mm)" in source


def test_a_continuous_page_runs_as_long_as_the_story(tmp_path):
    runtime = FakeRuntime(
        story_blocks=[{"markdown": "Just a paragraph.\n\n---\n\nMore."}],
        page_geometry=PageGeometry(width_mm=170.0, height_mm=None, margin_lr_mm=12.0, margin_tb_mm=8.0),
    )

    source = build_story_typst(runtime, make_renderers(), tmp_path)

    assert "#set page(width: 170mm, height: auto," in source
    # A sheet with no pages has nowhere to break to, so a rule is a rule.
    assert "#pagebreak" not in source
    assert "#line(length: 100%" in source


def test_the_story_is_written_to_a_pdf(tmp_path):
    pytest.importorskip("typst")
    runtime = FakeRuntime(
        story_blocks=[
            {"markdown": "# Title\n\nProse with $x^2$ and a link to [Figure 1](#view-populations)."},
            {"figureView": "populations"},
            {"markdown": "| a | b |\n|---|--:|\n| 1 | 2 |\n\n$$\n\\frac{a}{b}\n$$"},
        ],
        figure_specs={"populations": plot_figure_spec("populations")},
    )
    destination = tmp_path / "story.pdf"

    write_story_pdf(runtime, make_renderers(), destination, keep_source=tmp_path / "source")

    assert destination.read_bytes().startswith(b"%PDF")
    assert (tmp_path / "source" / "story.typ").is_file()


def test_a_story_typst_cannot_set_is_an_export_error(tmp_path):
    pytest.importorskip("typst")

    def render_figure_pdf(**kwargs: Any) -> Path:
        kwargs["output_path"].write_bytes(b"not a pdf")
        return kwargs["output_path"]

    renderers = StoryPdfRenderers(
        figure_view_state_for_block=make_renderers().figure_view_state_for_block,
        render_figure_pdf=render_figure_pdf,
    )
    runtime = FakeRuntime(
        story_blocks=[{"figureView": "populations"}],
        figure_specs={"populations": plot_figure_spec("populations")},
    )

    with pytest.raises(PdfExportError, match="Typst could not set the story"):
        write_story_pdf(runtime, renderers, tmp_path / "story.pdf")


def _figure_with(points: int) -> Any:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure()
    FigureCanvasAgg(figure)
    axes = figure.add_subplot(111)
    axes.plot(range(points), range(points))
    axes.scatter(range(10), range(10))
    return figure


def test_only_an_artist_past_the_threshold_is_rasterised():
    figure = _figure_with(6000)

    assert rasterize_large_artists(figure, 5000) == 1
    line, scatter = figure.axes[0].lines[0], figure.axes[0].collections[0]
    assert line.get_rasterized()
    assert not scatter.get_rasterized()
    # The axes around it stay vector.
    assert not figure.axes[0].get_rasterized()


def test_no_threshold_keeps_every_artist_vector():
    figure = _figure_with(100_000)

    assert rasterize_large_artists(figure, None) == 0
    assert not figure.axes[0].lines[0].get_rasterized()


def test_a_scatter_counts_its_markers():
    figure = _figure_with(10)

    assert artist_point_count(figure.axes[0].collections[0]) == 10
    assert artist_point_count(figure.axes[0].lines[0]) == 10


def test_figure_options_default_to_rasterising_large_artists():
    options = FigurePdfOptions.from_settings({})

    assert options.rasterize_above_points == DEFAULT_RASTERIZE_ABOVE_POINTS
    assert options.raster_dpi == DEFAULT_RASTER_DPI


def test_figure_options_come_from_the_settings_pdf_block():
    assert FigurePdfOptions.from_settings({"rasterizeAbovePoints": 200, "rasterDpi": 150}) == FigurePdfOptions(200, 150)
    # null is how a settings file says "never".
    assert FigurePdfOptions.from_settings({"rasterizeAbovePoints": None}).rasterize_above_points is None


def test_figure_options_refuse_nonsense():
    with pytest.raises(ValueError):
        FigurePdfOptions(rasterize_above_points=-1)
    with pytest.raises(ValueError):
        FigurePdfOptions(raster_dpi=0)


def test_the_command_line_overrides_the_settings_file():
    from limelight.cli import _NO_THRESHOLD, _figure_pdf_options

    settings = {"rasterizeAbovePoints": 200, "rasterDpi": 150}

    assert _figure_pdf_options(settings, None, None) == FigurePdfOptions(200, 150)
    assert _figure_pdf_options(settings, 800, None) == FigurePdfOptions(800, 150)
    assert _figure_pdf_options(settings, _NO_THRESHOLD, 600) == FigurePdfOptions(None, 600)


def test_the_command_line_takes_never_or_a_number():
    import argparse

    from limelight.cli import _NO_THRESHOLD, _rasterize_threshold

    assert _rasterize_threshold("never") is _NO_THRESHOLD
    assert _rasterize_threshold("2500") == 2500
    with pytest.raises(argparse.ArgumentTypeError):
        _rasterize_threshold("lots")


@pytest.fixture(scope="module")
def qt_app():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import limelight.qt_app  # noqa: F401  (QtWebEngine before the application)
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def test_the_window_exports_its_story_to_pdf(qt_app, tmp_path, monkeypatch):
    pytest.importorskip("typst")
    from limelight import qt_app as qt_module
    from limelight.reader import open_limelight
    from limelight.writer import LimelightProject

    project = LimelightProject(title="Window", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [1.0, 2.0, 3.0], "y": [1.0, 4.0, 9.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    view = project.story_figure(ref="fig")
    project.set_story_markdown(f"# Story\n\nProse with $x^2$.\n\n{view}\n", base_dir=tmp_path)
    package_path = project.write_folder(tmp_path / "pkg")
    destination = tmp_path / "out" / "story.pdf"

    monkeypatch.setattr(qt_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), ""))
    warnings: list[str] = []
    monkeypatch.setattr(qt_module.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(qt_module.QMessageBox, "critical", lambda *args: warnings.append(args[-1]))

    with open_limelight(package_path) as package:
        window = qt_module.LimelightWindow(package, package.manifest_json())
        try:
            window._export_story_to_pdf()
        finally:
            window.close()

    assert warnings == []
    assert destination.read_bytes().startswith(b"%PDF")
