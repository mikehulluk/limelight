"""Render a Limelight story to a paginated PDF document.

The story's Markdown is written out as Typst (see story_typst), each figure
view is drawn by matplotlib straight to a PDF of its own, and Typst lays the
whole thing out and writes the document. A figure drawn to PDF stays vector,
and carries its fonts inside it, so its labels print in the document's faces
at the document's sizes.

Figures are drawn by the Qt layer, so the drawing is injected through
:class:`StoryPdfRenderers`. That keeps this module free of widget imports and
lets the document be built and checked without a running application.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Sequence

from .app import (
    CSS_PIXELS_PER_INCH,
    DEFAULT_PAGE_HEIGHT_MM,
    DEFAULT_PAGE_WIDTH_MM,
    MM_PER_INCH,
    LimelightRuntime,
    figure_aspect,
    figure_width_px,
    table_view_cell_styles,
    table_view_column_alignment,
    table_view_column_formats,
    table_view_header_styles,
)
from .story_typst import (
    StoryTypstRenderer,
    TypstImage,
    TypstPage,
    TypstSpacing,
    typst_caption,
    typst_preamble,
    typst_string,
    typst_text,
)

logger = logging.getLogger(__name__)

# A viewport width has no physical size, so a PDF of one falls back to the
# default page: A4.
FALLBACK_PAGE_WIDTH_MM = DEFAULT_PAGE_WIDTH_MM
FALLBACK_PAGE_HEIGHT_MM = DEFAULT_PAGE_HEIGHT_MM

# Where the document keeps what it draws on, beside its source.
FIGURES_DIR = "figures"
IMAGES_DIR = "images"
STORY_SOURCE = "story.typ"


# A figure's artists are vector unless one is this many points or more: past
# that a vector path costs more to store and to draw than it shows, and a
# reader's viewer crawls. Below it, which is every ordinary plot, the figure
# stays sharp at any zoom.
DEFAULT_RASTERIZE_ABOVE_POINTS = 5000
# The resolution a rasterised artist is drawn at. The axes, labels and text
# around it stay vector.
DEFAULT_RASTER_DPI = 300


class PdfExportError(Exception):
    """Raised when a story could not be written to PDF."""


@dataclass(frozen=True)
class FigurePdfOptions:
    """How a story's figures are drawn into its PDF.

    An artist - a line, a scatter, a filled band - with more than
    ``rasterize_above_points`` points is drawn as an image at ``raster_dpi``;
    None keeps every artist vector, however large.
    """

    rasterize_above_points: int | None = DEFAULT_RASTERIZE_ABOVE_POINTS
    raster_dpi: int = DEFAULT_RASTER_DPI

    def __post_init__(self) -> None:
        if self.rasterize_above_points is not None and self.rasterize_above_points < 0:
            raise ValueError(f"rasterizeAbovePoints {self.rasterize_above_points!r} must not be negative")
        if self.raster_dpi <= 0:
            raise ValueError(f"rasterDpi {self.raster_dpi!r} must be positive")

    @classmethod
    def from_settings(cls, pdf_settings: Mapping[str, Any]) -> "FigurePdfOptions":
        """The options a settings file's `pdf` block asks for, defaults for what it leaves out.

        ``"rasterizeAbovePoints": null`` keeps every figure vector.
        """

        threshold = pdf_settings.get("rasterizeAbovePoints", DEFAULT_RASTERIZE_ABOVE_POINTS)
        return cls(
            rasterize_above_points=None if threshold is None else int(threshold),
            raster_dpi=int(pdf_settings.get("rasterDpi", DEFAULT_RASTER_DPI)),
        )


def artist_point_count(artist: Any) -> int:
    """How many points an artist draws: what makes it costly as vector."""

    from matplotlib.collections import Collection
    from matplotlib.lines import Line2D

    if isinstance(artist, Line2D):
        return len(artist.get_xydata())
    if isinstance(artist, Collection):
        # A scatter is one marker path at many offsets; a filled band or a
        # line collection is many vertices in few paths.
        offsets = artist.get_offsets()
        if len(offsets) > 1:
            return len(offsets)
        return sum(len(path.vertices) for path in artist.get_paths())
    return 0


def rasterize_large_artists(figure: Any, above_points: int | None) -> int:
    """Mark every artist in ``figure`` with more than ``above_points`` points to be rasterised.

    Returns how many were. Only the artist itself becomes an image; the
    axes, ticks, labels and legend stay vector.
    """

    if above_points is None:
        return 0
    rasterized = 0
    for axes in figure.axes:
        for artist in axes.get_children():
            if artist_point_count(artist) > above_points:
                artist.set_rasterized(True)
                rasterized += 1
    return rasterized


@dataclass(frozen=True)
class StoryPdfRenderers:
    """Rendering callables supplied by the Qt layer.

    ``figure_view_state_for_block`` maps a story block onto a figure view state
    exposing ``figure_view_id``, ``figure_id``, ``index`` and ``actions``, or
    ``None``.
    ``render_figure_pdf`` draws one figure view to a PDF at ``output_path``,
    rasterising its large artists as its :class:`FigurePdfOptions` say.
    """

    figure_view_state_for_block: Callable[[Any], Any]
    render_figure_pdf: Callable[..., Any]


def is_story_text_block(block: Any) -> bool:
    return isinstance(block, str) or (isinstance(block, dict) and bool(block.get("markdown")))


def _story_block_markdown(block: Any) -> str:
    if isinstance(block, str):
        return block
    return str(block["markdown"])


@dataclass(frozen=True)
class PrintedPage:
    """The physical page a story prints on, resolved from its geometry.

    A ``height_mm`` of None is one sheet as long as the story, which is what
    a continuous story already is on screen.
    """

    width_mm: float
    height_mm: float | None
    margin_lr_mm: float
    margin_tb_mm: float

    @property
    def content_width_mm(self) -> float:
        return max(1.0, self.width_mm - 2 * self.margin_lr_mm)

    @property
    def content_width_px(self) -> int:
        """The column in CSS pixels: what a figure is laid out to."""

        return max(1, int(round(self.content_width_mm / MM_PER_INCH * CSS_PIXELS_PER_INCH)))


def printed_page(runtime: LimelightRuntime) -> PrintedPage:
    """Resolve a story's page geometry to something a printer can use.

    A viewport width has no physical size, so it prints on A4.
    """

    geometry = runtime.page_geometry
    return PrintedPage(
        width_mm=geometry.width_mm if geometry.width_mm is not None else FALLBACK_PAGE_WIDTH_MM,
        height_mm=geometry.height_mm,
        margin_lr_mm=geometry.margin_lr_mm,
        margin_tb_mm=geometry.margin_tb_mm,
    )


def build_story_typst(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    root: Path,
    *,
    on_figure_progress: Callable[[int, int], bool] | None = None,
) -> str:
    """Build the Typst source for ``runtime``'s story, drawing what it needs under ``root``.

    Figures are drawn to ``root/figures`` and story images copied to
    ``root/images``; the source names both relative to ``root``, which is
    the root it is compiled against.

    ``on_figure_progress`` is called before each figure is rendered with the
    1-based figure number and the total figure count; returning ``False``
    cancels the export.
    """

    blocks = list(runtime.story_blocks)
    figure_total = sum(1 for block in blocks if not is_story_text_block(block))
    page = printed_page(runtime)
    column_px = page.content_width_px

    states = [
        renderers.figure_view_state_for_block(block)
        for block in blocks
        if not is_story_text_block(block)
    ]
    anchors = {state.figure_view_id for state in states if state is not None}
    anchors.update(_image_anchors(runtime))
    text_renderer = StoryTypstRenderer(
        _StoryImageFiles(runtime, root),
        runtime.image_number,
        runtime.image_anchor,
        runtime.image_display_width,
        anchors,
        paged=page.height_mm is not None,
    )

    parts: list[str] = []
    if _needs_document_title(blocks):
        parts.append(f"= {typst_text(runtime.project_title)}")

    figure_number = 0
    state_iter = iter(states)
    for block in blocks:
        if is_story_text_block(block):
            parts.append(text_renderer.render(_story_block_markdown(block)))
            continue

        state = next(state_iter)
        if state is None:
            continue

        figure_number += 1
        if on_figure_progress is not None and not on_figure_progress(figure_number, figure_total):
            raise PdfExportError("Export cancelled")
        parts.append(_figure_typst(runtime, renderers, state, column_px, root) + text_renderer.label(state.figure_view_id))

    preamble = typst_preamble(
        title=runtime.project_title,
        page=TypstPage(page.width_mm, page.height_mm, page.margin_lr_mm, page.margin_tb_mm),
        spacing=TypstSpacing(
            block_gap_mm=runtime.story_spacing.block_gap_mm,
            figure_gap_mm=runtime.story_spacing.figure_gap_mm,
            heading_gap_before_mm=runtime.story_spacing.heading_gap_before_mm,
            heading_gap_after_mm=runtime.story_spacing.heading_gap_after_mm,
        ),
        typography=runtime.typography,
        fonts=runtime.fonts,
    )
    return preamble + "\n\n" + "\n\n".join(part for part in parts if part) + "\n"


def _needs_document_title(blocks: Sequence[Any]) -> bool:
    """Only add a title heading when the story does not already open with one."""

    for block in blocks:
        if is_story_text_block(block):
            return not _story_block_markdown(block).lstrip().startswith("# ")
    return True


def _image_anchors(runtime: LimelightRuntime) -> set[str]:
    anchors = set()
    for src in runtime.image_assets_by_path:
        anchor = runtime.image_anchor(src)
        if anchor is not None:
            anchors.add(anchor)
    return anchors


class _StoryImageFiles:
    """Copies each story image out of the package, once, for the document to draw."""

    def __init__(self, runtime: LimelightRuntime, root: Path) -> None:
        self._runtime = runtime
        self._root = root
        self._placed: dict[str, TypstImage | None] = {}

    def __call__(self, src: str) -> TypstImage | None:
        if src not in self._placed:
            self._placed[src] = self._place(src)
        return self._placed[src]

    def _place(self, src: str) -> TypstImage | None:
        data = self._runtime.image_bytes(src)
        if data is None:
            return None

        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            width_px = image.width
            extension = ".png" if image.format == "PNG" else ".jpg"
        relative = f"{IMAGES_DIR}/image-{len(self._placed)}{extension}"
        target = self._root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        # The story panel draws an image at its pixels in CSS pixels, and so
        # does the page.
        return TypstImage(relative, width_px / CSS_PIXELS_PER_INCH * MM_PER_INCH)


def _figure_typst(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    state: Any,
    column_px: int,
    root: Path,
) -> str:
    figure_spec = runtime.figure_specs.get(state.figure_id)
    if figure_spec is None:
        return _notice_typst(f"Unknown figure {state.figure_id!r}")

    caption = typst_caption(state.index, figure_spec.get("caption") or figure_spec["title"])
    caption_argument = f", caption: [{caption}]" if caption else ""
    heading = runtime.figure_view_heading(state.figure_id, index=state.index)
    try:
        if figure_spec["tableViewSpecs"]:
            # Tables carry no drawn-in title, so they get one of their own.
            body = (
                f"[#ll-table-heading[{typst_text(figure_spec['title'])}]\n"
                f"{_table_view_typst(runtime, figure_spec)}]"
            )
            width = "100%"
        else:
            body, width = _figure_image_typst(runtime, renderers, state, figure_spec, column_px, root)
    except Exception as error:
        logger.exception("Could not render figure %s for PDF export", state.figure_id)
        return _notice_typst(f"Could not render {heading}: {error}")

    return f"#ll-figure({body}, width: {width}{caption_argument})"


def _figure_image_typst(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    state: Any,
    figure_spec: dict[str, Any],
    column_px: int,
    root: Path,
) -> tuple[str, str]:
    # Laid out at CSS resolution to the column, so a label drawn at the
    # body's points is the body's size on the page.
    width = figure_width_px(figure_spec, column_px, CSS_PIXELS_PER_INCH)
    height = int(width * figure_aspect(figure_spec))
    relative = f"{FIGURES_DIR}/{state.figure_view_id}.pdf"
    output_path = root / relative
    output_path.parent.mkdir(parents=True, exist_ok=True)
    renderers.render_figure_pdf(
        figure_id=state.figure_id,
        output_path=output_path,
        figure_view_index=state.index,
        figure_view_actions=state.actions,
        parameter_values=dict(runtime.control_parameter_values),
        width=width,
        height=height,
    )
    # The column is the figure's 100%; a narrower figure is that share of
    # it, so it keeps its size relative to the page whatever the page is.
    percent = 100.0 * width / column_px
    return f"image({typst_string(relative)}, width: 100%)", f"{percent:.2f}%"


def _table_view_typst(runtime: LimelightRuntime, figure_spec: dict[str, Any]) -> str:
    table_view_spec = figure_spec["tableViewSpecs"][0]
    preview = runtime.table_preview(
        table_view_spec["data"],
        table_view_spec.get("columns"),
        column_formats=table_view_column_formats(table_view_spec),
    )

    alignments = ", ".join(
        _alignment_typst(table_view_column_alignment(table_view_spec, preview, column_index))
        for column_index in range(len(preview.columns))
    )
    header_styles = table_view_header_styles(table_view_spec)
    header = ", ".join(
        f"[#ll-table-header[{_styled(typst_text(column), header_styles)}]]" for column in preview.columns
    )
    cells = [
        f"[#ll-table-cell[{_styled(typst_text(value), table_view_cell_styles(table_view_spec, row_index, column_index))}]]"
        for row_index, row in enumerate(preview.rows)
        for column_index, value in enumerate(row)
    ]
    table = (
        f"#table(\n  columns: {len(preview.columns)},\n  align: ({alignments},),\n"
        f"  fill: (x, y) => if y == 0 {{ rgb(\"#f3f4f6\") }},\n"
        f"  table.header({header}),\n  "
        + ",\n  ".join(cells)
        + ",\n)"
    )
    if preview.truncated:
        table += (
            f"\n#ll-table-note[Showing {len(preview.rows)} of {preview.total_rows} rows\\.]"
        )
    return table


def _styled(markup: str, styles: set[str]) -> str:
    if "Bold" in styles:
        markup = f"#text(weight: 700)[{markup}]"
    if "Italic" in styles:
        markup = f"#emph[{markup}]"
    return markup


def _alignment_typst(alignment: str) -> str:
    return {"Left": "left", "Center": "center", "Right": "right"}[alignment]


def _notice_typst(message: str) -> str:
    return f"#ll-notice[{typst_text(message)}]"


def _font_paths(runtime: LimelightRuntime) -> list[str]:
    """Every directory holding one of the document's font files, for Typst to search."""

    directories = {
        str(face.path.parent)
        for font in runtime.fonts.values()
        for face in font.faces
        if face.path.is_file()
    }
    return sorted(directories)


def write_story_pdf(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    destination: Path,
    *,
    on_figure_progress: Callable[[int, int], bool] | None = None,
    keep_source: Path | None = None,
) -> None:
    """Write ``runtime``'s story to ``destination`` as a PDF.

    ``keep_source``, when given, is a directory the Typst source and what it
    draws on are left in, for looking at how a document was set.
    """

    import typst

    with TemporaryDirectory(prefix="limelight-pdf-") as staging_directory:
        root = Path(staging_directory) if keep_source is None else Path(keep_source)
        root.mkdir(parents=True, exist_ok=True)
        source = build_story_typst(runtime, renderers, root, on_figure_progress=on_figure_progress)
        source_path = root / STORY_SOURCE
        source_path.write_text(source, encoding="utf-8")

        destination.parent.mkdir(parents=True, exist_ok=True)
        start_time = runtime.timing.start()
        try:
            _, warnings = typst.compile_with_warnings(
                str(source_path),
                output=str(destination),
                root=str(root),
                font_paths=_font_paths(runtime),
            )
        except typst.TypstError as error:
            raise PdfExportError(f"Typst could not set the story: {error}") from error
        for warning in warnings:
            logger.warning("Typst: %s", warning)
        runtime.timing.log("story.pdf.typst", start_time, [("path", str(destination))])


def default_pdf_path(package_path: Path) -> Path:
    return package_path.with_suffix(".pdf")


__all__ = [
    "DEFAULT_RASTERIZE_ABOVE_POINTS",
    "DEFAULT_RASTER_DPI",
    "FigurePdfOptions",
    "PdfExportError",
    "PrintedPage",
    "StoryPdfRenderers",
    "build_story_typst",
    "default_pdf_path",
    "is_story_text_block",
    "printed_page",
    "rasterize_large_artists",
    "write_story_pdf",
]
