"""Render a Limelight story to a paginated PDF document.

The Qt layer owns the markdown renderer and the matplotlib figure renderer, so
those are injected through :class:`StoryPdfRenderers`. That keeps this module
free of widget imports and lets the HTML builder be tested without a running
application.
"""

from __future__ import annotations

import base64
import binascii
import html
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Sequence
from urllib.parse import unquote_to_bytes

from PySide6.QtCore import QEventLoop, QMarginsF, QObject, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QFontInfo,
    QGuiApplication,
    QImage,
    QPageLayout,
    QPageSize,
    QPdfWriter,
    QTextDocument,
)

from .app import (
    CSS_PIXELS_PER_INCH,
    MM_PER_INCH,
    STORY_FIGURE_CSS,
    STORY_FONT_PT,
    STORY_IMAGE_CSS,
    STORY_LINE_HEIGHT,
    LimelightRuntime,
    StorySpacing,
    figure_view_caption_markup,
    secondary_axes_spec,
    story_rhythm_css,
    table_view_cell_styles,
    table_view_column_alignment,
    table_view_column_formats,
    table_view_header_styles,
)

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
except ImportError:
    QWebEnginePage = None
    QWebEngineSettings = None

logger = logging.getLogger(__name__)

MATHJAX_SCRIPT = "https://cdn.jsdelivr.net/npm/mathjax@4/tex-svg.js"

# Figures are laid out at the width of the text column, so their labels print
# at the story's size, then rasterised at a higher dpi so the page stays crisp.
FIGURE_OUTPUT_DPI = 220
FIGURE_SECONDARY_AXES_ASPECT = 1.05
FIGURE_DEFAULT_ASPECT = 0.58

PAGE_MARGIN_MM = 15.0
# A page that runs as long as its content still needs a number to print at.
# This is tall enough that no story reaches the end of it, and Chromium trims
# the sheet to the content rather than padding it out.
CONTINUOUS_PAGE_HEIGHT_MM = 5000.0
# A viewport width has no physical size, so a PDF of one falls back to A4.
FALLBACK_PAGE_WIDTH_MM = 210.0
FALLBACK_PAGE_HEIGHT_MM = 297.0
DOCUMENT_READY_TIMEOUT_MS = 20000
DOCUMENT_READY_POLL_MS = 100
PRINT_TIMEOUT_MS = 60000

# The fallback writer has to size figures in device pixels, so it rewrites this
# exact opening tag. Both sides are kept together deliberately. STORY_IMAGE_CSS
# is dropped alongside this by the fallback writer, which cannot honour either.
FIGURE_IMAGE_TAG_PREFIX = '<img class="limelight-figure-image"'
FIGURE_IMAGE_CSS = """img.limelight-figure-image {
  width: 100%;
  height: auto;
}"""
# Rich text lays out in logical (~96 dpi) coordinates regardless of the
# writer's device resolution, so fallback figures are scaled to a printable
# column width expressed in those same units.
FALLBACK_IMAGE_WIDTH_PX = 540


class PdfExportError(Exception):
    """Raised when a story could not be written to PDF."""


@dataclass(frozen=True)
class StoryPdfRenderers:
    """Rendering callables supplied by the Qt layer.

    ``render_markdown_html`` turns one markdown block into an HTML fragment.
    ``figure_view_state_for_block`` maps a story block onto a figure view state
    exposing ``figure_view_id``, ``figure_id``, ``index`` and ``actions``, or
    ``None``.
    ``render_figure_png`` rasterises one figure view to PNG bytes.
    """

    render_markdown_html: Callable[[str], str]
    figure_view_state_for_block: Callable[[Any], Any]
    render_figure_png: Callable[..., bytes]


def is_story_text_block(block: Any) -> bool:
    return isinstance(block, str) or (isinstance(block, dict) and bool(block.get("markdown")))


def _story_block_markdown(block: Any) -> str:
    if isinstance(block, str):
        return block
    return str(block["markdown"])


def build_story_pdf_html(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    *,
    on_figure_progress: Callable[[int, int], bool] | None = None,
) -> str:
    """Build the complete print-ready HTML document for ``runtime``'s story.

    ``on_figure_progress`` is called before each figure is rendered with the
    1-based figure number and the total figure count; returning ``False``
    cancels the export.
    """

    blocks = list(runtime.story_blocks)
    figure_blocks = [block for block in blocks if not is_story_text_block(block)]
    figure_total = len(figure_blocks)
    page = printed_page(runtime)
    column_px = page.content_width_px

    parts: list[str] = []
    if _needs_document_title(blocks):
        parts.append(f"<h1>{html.escape(runtime.project_title)}</h1>")

    figure_number = 0
    for block in blocks:
        if is_story_text_block(block):
            parts.append(renderers.render_markdown_html(_story_block_markdown(block)))
            continue

        state = renderers.figure_view_state_for_block(block)
        if state is None:
            continue

        figure_number += 1
        if on_figure_progress is not None and not on_figure_progress(figure_number, figure_total):
            raise PdfExportError("Export cancelled")
        parts.append(_figure_html(runtime, renderers, state, column_px))

    return _pdf_document_html(
        "\n".join(parts),
        title=runtime.project_title,
        page=page,
        spacing=runtime.story_spacing,
    )


def _needs_document_title(blocks: Sequence[Any]) -> bool:
    """Only add a title heading when the story does not already open with one."""

    for block in blocks:
        if is_story_text_block(block):
            return not _story_block_markdown(block).lstrip().startswith("# ")
    return True


def _figure_html(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    state: Any,
    column_px: int,
) -> str:
    figure_spec = runtime.figure_specs.get(state.figure_id)
    if figure_spec is None:
        return _notice_html(f"Unknown figure {state.figure_id!r}")

    heading = runtime.figure_view_heading(state.figure_id, index=state.index)
    try:
        if figure_spec["tableViewSpecs"]:
            # Tables carry no drawn-in title, so they get one of their own.
            body = _heading_html(figure_spec["title"]) + _table_view_html(runtime, figure_spec)
        else:
            body = _figure_image_html(runtime, renderers, state, figure_spec, heading, column_px)
    except Exception as error:
        logger.exception("Could not render figure %s for PDF export", state.figure_id)
        body = _notice_html(f"Could not render {heading}: {error}")

    anchor = html.escape(state.figure_view_id, quote=True)
    caption = figure_view_caption_markup(figure_spec, state.index)
    return (
        f'<figure class="limelight-figure" id="{anchor}">\n'
        f'{body}\n<figcaption>{caption}</figcaption>\n</figure>'
    )


def _heading_html(heading: str) -> str:
    return f'<p class="limelight-figure-heading">{html.escape(heading)}</p>'


def _figure_image_html(
    runtime: LimelightRuntime,
    renderers: StoryPdfRenderers,
    state: Any,
    figure_spec: dict[str, Any],
    heading: str,
    column_px: int,
) -> str:
    aspect = (
        FIGURE_SECONDARY_AXES_ASPECT
        if secondary_axes_spec(figure_spec) is not None
        else FIGURE_DEFAULT_ASPECT
    )
    width = column_px
    height = int(width * aspect)
    png_bytes = renderers.render_figure_png(
        figure_id=state.figure_id,
        figure_view_index=state.index,
        figure_view_actions=state.actions,
        parameter_values=dict(runtime.control_parameter_values),
        width=width,
        height=height,
        dpi=FIGURE_OUTPUT_DPI,
    )
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return (
        f'{FIGURE_IMAGE_TAG_PREFIX} alt="{html.escape(heading)}" '
        f'src="data:image/png;base64,{encoded}">'
    )


def _table_view_html(runtime: LimelightRuntime, figure_spec: dict[str, Any]) -> str:
    table_view_spec = figure_spec["tableViewSpecs"][0]
    preview = runtime.table_preview(
        table_view_spec["data"],
        table_view_spec.get("columns"),
        column_formats=table_view_column_formats(table_view_spec),
    )

    header_styles = table_view_header_styles(table_view_spec)
    header_cells = "".join(
        f'<th class="{_style_class(header_styles)}" style="text-align: '
        f'{_alignment_css(table_view_column_alignment(table_view_spec, preview, column_index))};">'
        f"{html.escape(column)}</th>"
        for column_index, column in enumerate(preview.columns)
    )

    body_rows: list[str] = []
    for row_index, row in enumerate(preview.rows):
        cells = "".join(
            f'<td class="{_style_class(table_view_cell_styles(table_view_spec, row_index, column_index))}" '
            f'style="text-align: '
            f'{_alignment_css(table_view_column_alignment(table_view_spec, preview, column_index))};">'
            f"{html.escape(value)}</td>"
            for column_index, value in enumerate(row)
        )
        body_rows.append(f"<tr>{cells}</tr>")

    truncated_note = ""
    if preview.truncated:
        truncated_note = (
            f'<p class="limelight-table-note">Showing {len(preview.rows)} of '
            f"{preview.total_rows} rows.</p>"
        )

    return (
        '<table class="limelight-table">'
        f"<thead><tr>{header_cells}</tr></thead>"
        f'<tbody>{"".join(body_rows)}</tbody>'
        "</table>"
        f"{truncated_note}"
    )


def _style_class(styles: set[str]) -> str:
    classes = []
    if "Bold" in styles:
        classes.append("limelight-bold")
    if "Italic" in styles:
        classes.append("limelight-italic")
    return " ".join(classes)


def _alignment_css(alignment: str) -> str:
    return {"Left": "left", "Center": "center", "Right": "right"}[alignment]


def _notice_html(message: str) -> str:
    return f'<p class="limelight-notice">{html.escape(message)}</p>'


@dataclass(frozen=True)
class PrintedPage:
    """The physical page a story prints on, resolved from its geometry."""

    width_mm: float
    height_mm: float
    margin_lr_mm: float
    margin_tb_mm: float

    @property
    def content_width_mm(self) -> float:
        return max(1.0, self.width_mm - 2 * self.margin_lr_mm)

    @property
    def content_width_px(self) -> int:
        """The column in CSS pixels: what a figure is laid out to."""

        return max(1, int(round(self.content_width_mm / MM_PER_INCH * CSS_PIXELS_PER_INCH)))

    @property
    def margins(self) -> QMarginsF:
        return QMarginsF(self.margin_lr_mm, self.margin_tb_mm, self.margin_lr_mm, self.margin_tb_mm)


def printed_page(runtime: LimelightRuntime) -> PrintedPage:
    """Resolve a story's page geometry to something a printer can use.

    A viewport width has no physical size, so it prints on A4. A continuous
    height prints as one long sheet, which is what the story already is on
    screen.
    """

    geometry = runtime.page_geometry
    return PrintedPage(
        width_mm=geometry.width_mm if geometry.width_mm is not None else FALLBACK_PAGE_WIDTH_MM,
        height_mm=(
            geometry.height_mm if geometry.height_mm is not None else CONTINUOUS_PAGE_HEIGHT_MM
        ),
        margin_lr_mm=geometry.margin_lr_mm,
        margin_tb_mm=geometry.margin_tb_mm,
    )


def story_font_family() -> str:
    """The face the story is set in: what the application's own text uses.

    Named outright in the stylesheets and handed to matplotlib, so prose,
    captions and plot labels share one face rather than each asking the
    platform for "the system font" and getting three answers.
    """

    application = QGuiApplication.instance()
    if application is None:
        return "system-ui"
    return QFontInfo(application.font()).family()


def _pdf_document_html(body: str, *, title: str, page: PrintedPage, spacing: StorySpacing) -> str:
    font_family = story_font_family()
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<script>
window.__limelightPdfReady = false;
window.MathJax = {{
  tex: {{
    inlineMath: [['\\\\(', '\\\\)']],
    displayMath: [['\\\\[', '\\\\]']],
    processEscapes: true
  }},
  svg: {{ fontCache: 'none' }},
  startup: {{
    pageReady: () => MathJax.startup.defaultPageReady().then(() => {{
      window.__limelightPdfReady = true;
    }})
  }}
}};
</script>
<script defer src="{MATHJAX_SCRIPT}"></script>
<style>
@page {{
  size: {page.width_mm}mm {page.height_mm}mm;
  margin: {page.margin_tb_mm}mm {page.margin_lr_mm}mm;
}}
html {{
  color-scheme: light;
}}
body {{
  margin: 0;
  color: #202124;
  background: #ffffff;
  font: {STORY_FONT_PT}pt/{STORY_LINE_HEIGHT} "{font_family}", system-ui, sans-serif;
}}
h1, h2, h3, h4 {{
  break-after: avoid;
  page-break-after: avoid;
}}
h1 {{
  font-size: 1.7em;
}}
h2 {{
  font-size: 1.32em;
}}
h3 {{
  font-size: 1.12em;
}}
p {{
  orphans: 3;
  widows: 3;
}}
code {{
  background: #f3f4f6;
  border-radius: 4px;
  padding: 0.1rem 0.25rem;
  font-size: 0.92em;
}}
pre {{
  background: #f3f4f6;
  border-radius: 6px;
  padding: 0.7rem;
  break-inside: avoid;
  page-break-inside: avoid;
  white-space: pre-wrap;
  word-wrap: break-word;
}}
figure.limelight-figure, figure.limelight-story-figure {{
  break-inside: avoid;
  page-break-inside: avoid;
}}
{FIGURE_IMAGE_CSS}
{STORY_IMAGE_CSS}
{STORY_FIGURE_CSS}
span.limelight-missing-image {{
  color: #b3261e;
  font-style: italic;
}}
p.limelight-figure-heading {{
  margin: 0 0 0.35em;
  font-size: 0.95em;
  font-weight: 600;
  text-align: center;
}}
table.limelight-table {{
  border-collapse: collapse;
  width: 100%;
  font-size: 0.86em;
}}
table.limelight-table th, table.limelight-table td {{
  border: 1px solid #d0d3d6;
  padding: 3px 7px;
}}
table.limelight-table thead th {{
  background: #f3f4f6;
}}
table.limelight-table tr {{
  break-inside: avoid;
  page-break-inside: avoid;
}}
.limelight-bold {{
  font-weight: 700;
}}
.limelight-italic {{
  font-style: italic;
}}
.limelight-table-note, .limelight-notice {{
  font-size: 0.85em;
  color: #5f6368;
}}
{story_rhythm_css(spacing)}
</style>
</head>
<body>
{body}
</body>
</html>"""


def write_html_to_pdf(
    html_text: str,
    destination: Path,
    page: PrintedPage | None = None,
) -> None:
    """Write ``html_text`` to ``destination`` as a PDF on ``page``."""

    resolved = page if page is not None else PrintedPage(
        width_mm=FALLBACK_PAGE_WIDTH_MM,
        height_mm=FALLBACK_PAGE_HEIGHT_MM,
        margin_lr_mm=PAGE_MARGIN_MM,
        margin_tb_mm=PAGE_MARGIN_MM,
    )
    if QWebEnginePage is None:
        _write_pdf_with_text_document(html_text, destination, resolved)
        return
    _write_pdf_with_web_engine(html_text, destination, resolved)


class _WebEnginePdfPrinter(QObject):
    def __init__(self, html_text: str, destination: Path, page: PrintedPage) -> None:
        super().__init__()
        self._html_text = html_text
        self._destination = destination
        self._page_geometry = page
        self._loop = QEventLoop()
        self._page = QWebEnginePage(self)
        self._elapsed_ms = 0
        self._error: str | None = None
        self._finished = False
        # Stories embed their figures as data URLs and routinely exceed the 2 MB
        # ceiling on setHtml, so the document is loaded from a file instead. That
        # makes it local content, which cannot reach the MathJax CDN by default.
        settings = self._page.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self._page.loadFinished.connect(self._on_load_finished)
        self._page.pdfPrintingFinished.connect(self._on_print_finished)

    def run(self) -> None:
        guard = QTimer(self)
        guard.setSingleShot(True)
        guard.timeout.connect(self._on_timeout)
        guard.start(DOCUMENT_READY_TIMEOUT_MS + PRINT_TIMEOUT_MS)

        with TemporaryDirectory(prefix="limelight-pdf-") as staging_directory:
            source = Path(staging_directory) / "story.html"
            source.write_text(self._html_text, encoding="utf-8")
            self._page.load(QUrl.fromLocalFile(str(source)))
            self._loop.exec()
        guard.stop()

        if self._error is not None:
            raise PdfExportError(self._error)

    def _finish(self, error: str | None) -> None:
        if self._finished:
            return
        self._finished = True
        self._error = error
        self._loop.quit()

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self._finish("The story document could not be laid out for printing")
            return
        self._poll_document_ready()

    def _poll_document_ready(self) -> None:
        if self._finished:
            return
        self._page.runJavaScript(
            "document.readyState === 'complete' && window.__limelightPdfReady === true",
            self._on_document_ready_result,
        )

    def _on_document_ready_result(self, ready: object) -> None:
        if self._finished:
            return
        if ready is True:
            self._start_print()
            return

        self._elapsed_ms += DOCUMENT_READY_POLL_MS
        if self._elapsed_ms >= DOCUMENT_READY_TIMEOUT_MS:
            # MathJax is fetched from a CDN, so an offline machine never reports
            # ready. Print what rendered rather than failing the whole export.
            logger.warning("Timed out waiting for maths typesetting; printing anyway")
            self._start_print()
            return

        QTimer.singleShot(DOCUMENT_READY_POLL_MS, self._poll_document_ready)

    def _start_print(self) -> None:
        geometry = self._page_geometry
        layout = QPageLayout(
            QPageSize(
                QSizeF(geometry.width_mm, geometry.height_mm),
                QPageSize.Unit.Millimeter,
            ),
            QPageLayout.Orientation.Portrait,
            geometry.margins,
            QPageLayout.Unit.Millimeter,
        )
        self._page.printToPdf(str(self._destination), layout)

    def _on_print_finished(self, file_path: str, success: bool) -> None:
        if success:
            self._finish(None)
            return
        self._finish(f"Could not write {file_path}")

    def _on_timeout(self) -> None:
        self._finish("Timed out while rendering the story to PDF")


def _write_pdf_with_web_engine(html_text: str, destination: Path, page: PrintedPage) -> None:
    printer = _WebEnginePdfPrinter(html_text, destination, page)
    printer.run()


class _DataUrlTextDocument(QTextDocument):
    """QTextDocument that resolves the ``data:`` image URLs we embed.

    Rich text has no usable percentage sizing for images, so each figure is
    scaled down to the printable column width as it is decoded.
    """

    def __init__(self, max_image_width: int) -> None:
        super().__init__()
        self._max_image_width = max_image_width

    def loadResource(self, resource_type: int, url: QUrl) -> Any:
        if url.scheme() == "data":
            image = _image_from_data_url(url)
            if image is not None:
                if image.width() <= self._max_image_width:
                    return image
                return image.scaledToWidth(
                    self._max_image_width, Qt.TransformationMode.SmoothTransformation
                )
        return super().loadResource(resource_type, url)


def _image_from_data_url(url: QUrl) -> QImage | None:
    payload = url.toString()[len("data:") :]
    separator = payload.find(",")
    if separator < 0:
        return None

    header = payload[:separator]
    data = payload[separator + 1 :]
    try:
        if header.endswith(";base64"):
            raw = base64.b64decode(data)
        else:
            raw = unquote_to_bytes(data)
    except (binascii.Error, ValueError):
        logger.warning("Could not decode an embedded image while writing the PDF")
        return None

    image = QImage()
    if not image.loadFromData(raw):
        return None
    return image


def _write_pdf_with_text_document(html_text: str, destination: Path, page: PrintedPage) -> None:
    """Fallback used when Qt WebEngine is unavailable.

    Layout is coarser and TeX maths stays as source text, but the story, figures
    and tables still reach the page.
    """

    logger.warning("Qt WebEngine is unavailable; writing the PDF with a reduced layout")
    writer = QPdfWriter(str(destination))
    writer.setPageSize(QPageSize(QSizeF(page.width_mm, page.height_mm), QPageSize.Unit.Millimeter))
    writer.setPageMargins(page.margins, QPageLayout.Unit.Millimeter)

    # Rich text cannot honour the percentage width the print stylesheet uses, so
    # the rule is dropped and the images themselves are scaled to fit instead.
    document_html = html_text.replace(FIGURE_IMAGE_CSS, "").replace(STORY_IMAGE_CSS, "")

    document = _DataUrlTextDocument(FALLBACK_IMAGE_WIDTH_PX)
    document.setHtml(document_html)
    # Leave the page size unset so print_ derives it from the writer's layout.
    document.print_(writer)


def default_pdf_path(package_path: Path) -> Path:
    return package_path.with_suffix(".pdf")


__all__ = [
    "PdfExportError",
    "StoryPdfRenderers",
    "build_story_pdf_html",
    "default_pdf_path",
    "is_story_text_block",
    "write_html_to_pdf",
]
