from __future__ import annotations

import base64
import html
import io
import json
import logging
import signal
import sys
from calendar import month_abbr
from dataclasses import dataclass
from datetime import date, datetime
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Sequence

import matplotlib
import numpy as np

matplotlib.use("qtagg")

import matplotlib.dates as mdates
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.collections import PatchCollection
from matplotlib.figure import Figure as MatplotlibFigure
from matplotlib.patches import Polygon, Rectangle
from matplotlib.ticker import FuncFormatter, MultipleLocator
from PySide6.QtCore import (
    QBuffer,
    QEvent,
    QEventLoop,
    QByteArray,
    QIODevice,
    QObject,
    QPoint,
    QRunnable,
    Qt,
    QtMsgType,
    QSettings,
    QSize,
    QSignalBlocker,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    qInstallMessageHandler,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QKeySequence,
    QCursor,
    QDesktopServices,
    QFont,
    QColor,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QAbstractItemView,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import refs
from .app import (
    CSS_PIXELS_PER_INCH,
    MM_PER_INCH,
    STORY_FIGURE_CSS,
    STORY_FONT_PT,
    STORY_FONT_PX,
    STORY_IMAGE_CSS,
    STORY_LINE_HEIGHT,
    LimelightRuntime,
    PageGeometry,
    StorySection,
    StorySpacing,
    TablePreview,
    TimingProbe,
    _to_float,
    axis_label,
    axis_scale,
    figure_controls,
    figure_aspect,
    first_axes_spec,
    table_view_cell_styles,
    table_view_column_alignment,
    table_view_column_formats,
    table_view_header_styles,
    story_rhythm_css,
    story_run_edge_css,
)
from .semantic import (
    control_parameter_data_type_kind,
    control_parameter_data_type_payload,
    control_parameter_default_min_max,
)
from .logging_config import configure_logging, log_file_path
from .pdf_export import (
    story_font_family,
    PdfExportError,
    StoryPdfRenderers,
    build_story_pdf_html,
    default_pdf_path,
    printed_page,
    write_html_to_pdf,
)
from .reader import PACKAGE_SUFFIXES, LimelightError, LimelightPackage, open_limelight
from .semantic import (
    axis_data_type_kind,
    axis_limit_shape,
    is_axis_limit_payload,
    optional_field,
    validate_manifest_semantics,
)

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEnginePage = object
    QWebEngineSettings = None
    QWebEngineView = None

try:
    from markdown_it import MarkdownIt
    from mdit_py_plugins.dollarmath import dollarmath_plugin

    from .story_markdown import StoryMarkdownRenderer, story_image_references
except ImportError:
    MarkdownIt = None
    dollarmath_plugin = None
    StoryMarkdownRenderer = None

MATHJAX_SCRIPT = "https://cdn.jsdelivr.net/npm/mathjax@4/tex-svg.js"
STORY_TEXT_MIN_HEIGHT = 44

# A package is a folder while it is being authored and an archive once it is
# distributed, so both shapes need an entry point in the UI. A file dialog
# cannot offer both at once, hence the separate folder command.
PACKAGE_FILE_FILTER = (
    "Limelight packages (" + " ".join(f"*{suffix}" for suffix in PACKAGE_SUFFIXES) + ");;All files (*)"
)


@dataclass(frozen=True)
class StoryFigureViewState:
    figure_view_id: str
    figure_id: str
    index: int | None = None
    actions: tuple[dict[str, Any], ...] = ()


def _figure_view_state_from_payload(runtime: LimelightRuntime, payload: Any) -> StoryFigureViewState | None:
    figure_view_id = payload["figureView"]
    figure_view = runtime.figure_view_by_id(figure_view_id)
    return StoryFigureViewState(
        figure_view_id=figure_view_id,
        figure_id=figure_view["ref"],
        index=runtime.figure_view_number(figure_view),
        actions=tuple(figure_view["actions"]),
    )


def _figure_view_state_from_block(runtime: LimelightRuntime, block: Any) -> StoryFigureViewState | None:
    if "figureView" in block:
        return _figure_view_state_from_payload(runtime, block)
    return None


def _story_figure_view_states(runtime: LimelightRuntime, blocks: Sequence[Any]) -> list[StoryFigureViewState]:
    states: list[StoryFigureViewState] = []
    for block in blocks:
        state = _figure_view_state_from_block(runtime, block)
        if state is not None:
            states.append(state)
    return states


# The figure view a Figures-tab row stands for, where the row is a view rather
# than the spec above it.
FIGURE_VIEW_ID_ROLE = Qt.UserRole + 1

# The sizes a story zooms through, either side of 1.0.
STORY_ZOOM_STEPS = (0.5, 0.67, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)

# Ctrl with any of these zooms the story. `Equal` is the unshifted key most
# keyboards put `+` on, and is what people actually press.
STORY_ZOOM_KEYS = {
    Qt.Key.Key_Plus: 1,
    Qt.Key.Key_Equal: 1,
    Qt.Key.Key_Minus: -1,
    Qt.Key.Key_Underscore: -1,
    Qt.Key.Key_0: 0,
}

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "limelight-icon.svg"
WINDOWS_APP_USER_MODEL_ID = "Limelight.ProjectViewer"
logger = logging.getLogger(__name__)
_qt_message_handler_installed = False
_previous_qt_message_handler: Callable[..., object] | None = None
_owned_application: QApplication | None = None


def _ensure_application() -> tuple[QApplication, bool]:
    """Return the QApplication, creating it if this module has not already.

    The second element says whether we created it, and so whether we are the
    ones responsible for running the event loop. It has to be tracked across
    calls because the startup package prompt needs an application before the
    main window is built.
    """

    global _owned_application

    app = QApplication.instance()
    if app is not None:
        return app, app is _owned_application

    configure_logging()
    _install_qt_message_handler()
    _set_windows_app_user_model_id()

    app = QApplication(sys.argv[:1])
    QApplication.setOrganizationName("Limelight")
    QApplication.setApplicationName("Limelight")
    # The desktop entry the installers ship is limelight.desktop; naming it
    # lets a Wayland shell, which ignores window icons, find the icon and
    # group the windows by that entry.
    QApplication.setDesktopFileName("limelight")

    icon = _application_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    _owned_application = app
    _configure_figure_typography()
    return app, True


_figure_typography_configured = False


def _configure_figure_typography() -> None:
    """Set matplotlib to draw text the way the story does.

    Labels are the story's size in the story's face, so a plot's axes read
    as part of the page; the size is in points, and a figure is laid out at
    CSS_PIXELS_PER_INCH so those points come out as the story's pixels.
    Done once, for the process: rcParams are global, and figures are drawn
    from worker threads as well as the main one.
    """

    global _figure_typography_configured
    if _figure_typography_configured:
        return
    _figure_typography_configured = True
    matplotlib.rcParams.update(
        {
            "font.size": STORY_FONT_PT,
            "font.family": "sans-serif",
            "font.sans-serif": [story_font_family(), "Segoe UI", "DejaVu Sans"],
        }
    )


class StartupPackageDialog(QDialog):
    """Asks which package to open when Limelight is launched without one.

    Launching from a desktop shortcut gives us no window, and therefore no File
    menu, so this is the only place a folder package can be reached in that
    case. Archives come first because that is the shape packages are shipped in.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Open Limelight Package")
        self.setWindowIcon(_application_icon())
        self.selected_path: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        heading = QLabel("Open a Limelight package")
        heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(heading)

        description = QLabel(
            "Choose a .limelight or .ll package file, or the folder of a package you are working on."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        file_button = QPushButton("Open Package File...")
        file_button.setDefault(True)
        file_button.clicked.connect(self._choose_file)
        layout.addWidget(file_button)

        folder_button = QPushButton("Open Package Folder...")
        folder_button.clicked.connect(self._choose_folder)
        layout.addWidget(folder_button)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.reject)
        layout.addWidget(quit_button)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Limelight Package",
            "",
            PACKAGE_FILE_FILTER,
        )
        if path:
            self.selected_path = path
            self.accept()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Limelight Package Folder")
        if path:
            self.selected_path = path
            self.accept()


def prompt_for_package_path() -> str | None:
    """Ask which package to open, for launches that were given no path."""

    _ensure_application()
    dialog = StartupPackageDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_path


def story_pdf_renderers(runtime: LimelightRuntime) -> StoryPdfRenderers:
    """Build the rendering callables a story PDF export needs.

    Shared by the app's File -> Export to PDF and by ``limelight-cli pdf`` so the
    two produce the same document.
    """

    if StoryMarkdownRenderer is None:
        render_markdown_html: Callable[[str], str] = _plain_text_html
    else:
        render_markdown_html = story_markdown_renderer(runtime).render

    def figure_view_state_for_block(block: Any) -> StoryFigureViewState | None:
        return _figure_view_state_from_block(runtime, block)

    def render_figure_png(
        *,
        figure_id: str,
        figure_view_index: int | None,
        figure_view_actions: Sequence[dict[str, Any]],
        parameter_values: dict[str, Any],
        width: int,
        height: int,
        dpi: int,
    ) -> bytes:
        return _render_static_figure_view_png_bytes(
            runtime,
            figure_id,
            figure_view_index=figure_view_index,
            figure_view_actions=figure_view_actions,
            parameter_values=parameter_values,
            width=width,
            height=height,
            dpi=dpi,
        )

    return StoryPdfRenderers(
        render_markdown_html=render_markdown_html,
        figure_view_state_for_block=figure_view_state_for_block,
        render_figure_png=render_figure_png,
    )


@contextmanager
def _interrupt_closes(window: "LimelightWindow") -> Any:
    """Make Ctrl-C in the launching terminal close the window.

    Python runs a signal handler between bytecodes, and Qt's event loop is C++,
    so a handler installed here would not run until the loop happened to return
    to Python - which, with nothing else happening, is never. A short repeating
    timer gives the interpreter that chance a few times a second.

    Closing the window rather than quitting the application means the usual
    shutdown runs, so the package is closed and an extracted archive is cleaned
    up instead of being left in the temporary directory. A second Ctrl-C gives
    up on that and lets the default handler kill the process, for the case
    where shutting down is itself what is stuck.
    """

    previous_handler = signal.getsignal(signal.SIGINT)
    interrupted = False

    def handle_interrupt(signum: int, frame: Any) -> None:
        nonlocal interrupted
        if interrupted:
            logger.warning("Interrupted again while closing; exiting immediately")
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt

        interrupted = True
        logger.info("Interrupted; closing Limelight")
        window.close()

    signal.signal(signal.SIGINT, handle_interrupt)
    wakeup_timer = QTimer()
    wakeup_timer.timeout.connect(lambda: None)
    wakeup_timer.start(200)
    try:
        yield
    finally:
        wakeup_timer.stop()
        signal.signal(signal.SIGINT, previous_handler)


def run_qt_app(package: LimelightPackage, manifest: dict[str, Any], *, debug_timing: bool = False) -> None:
    logger.info("Starting Limelight Qt application for %s", package.path)
    app, owns_app = _ensure_application()

    window = LimelightWindow(package, manifest, debug_timing=debug_timing)
    window.resize(1120, 760)
    window.show()
    logger.info("Limelight main window shown for %s", package.path)

    if owns_app:
        with _interrupt_closes(window):
            app.exec()
        logger.info("Limelight Qt event loop exited")


def _install_qt_message_handler() -> None:
    global _previous_qt_message_handler, _qt_message_handler_installed
    if _qt_message_handler_installed:
        return

    _previous_qt_message_handler = qInstallMessageHandler(_log_qt_message)
    _qt_message_handler_installed = True


def _log_qt_message(mode: QtMsgType, context: object, message: str) -> None:
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(mode, logging.ERROR)
    location = _qt_message_location(context)
    if location:
        logger.log(level, "Qt: %s (%s)", message, location)
    else:
        logger.log(level, "Qt: %s", message)

    if _previous_qt_message_handler is not None:
        _previous_qt_message_handler(mode, context, message)


def _qt_message_location(context: object) -> str:
    try:
        file = context.file
    except AttributeError:
        file = None
    try:
        line = context.line
    except AttributeError:
        line = None
    try:
        function = context.function
    except AttributeError:
        function = None
    parts = []
    if file:
        parts.append(str(file))
    if line:
        parts.append(str(line))
    if function:
        parts.append(str(function))
    return ":".join(parts)


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except Exception as error:
        logger.warning("Could not set Windows AppUserModelID: %s", error)


# The sizes a window icon is published at. An SVG icon draws at any size but
# advertises none, and Qt's X11 backend hands the window system only the
# sizes an icon advertises - so a bare SVG icon reaches the taskbar as no
# icon at all, and GNOME shows its placeholder.
_ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def _application_icon() -> QIcon:
    source = QIcon(str(APP_ICON_PATH))
    if source.isNull():
        logger.warning("Limelight application icon could not be loaded from %s", APP_ICON_PATH)
        return source
    icon = QIcon()
    for size in _ICON_SIZES:
        icon.addPixmap(source.pixmap(QSize(size, size)))
    return icon


class StoryTextPanel(QWidget):
    anchorRequested = Signal(str)
    zoomRequested = Signal(int)

    def __init__(self, runtime: LimelightRuntime, worker_pool: QThreadPool) -> None:
        super().__init__()
        self._active = True
        self._blocks_key: tuple[str, ...] | None = None
        self._render_request_id = 0
        self.runtime = runtime
        self.timing = runtime.timing
        self.worker_pool = worker_pool
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(STORY_TEXT_MIN_HEIGHT)

        self.web_view: Any | None = None
        self.fallback: QTextBrowser | None = None
        self.render_markdown = False
        # The document's height in its own CSS pixels, as last measured.
        self._content_height: int | None = None

        # Reflows arrive in bursts - a resize drag, or MathJax finishing one
        # equation at a time - so measurements are coalesced.
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.setInterval(50)
        self._height_timer.timeout.connect(self._update_content_height)


        if QWebEngineView is not None and MarkdownIt is not None and dollarmath_plugin is not None:
            self.render_markdown = True
            self.web_view = QWebEngineView()
            self._page = StoryWebPage(self.web_view)
            self._page.linkClicked.connect(self._on_link_clicked)
            # A story block is sized to its content, so anything that reflows
            # it has to re-measure. MathJax typesetting and image decoding both
            # finish after the load does, and a window resize changes the
            # column width without any load at all.
            self._page.contentsSizeChanged.connect(self._request_content_height_update)
            self.web_view.setPage(self._page)
            # Chromium zooms the view under the cursor on Ctrl+wheel, which
            # would zoom one block of a document and leave the rest alone. The
            # gesture is caught here and handed to the story, which applies it
            # to every block at once.
            self._install_wheel_filter()
            # Stories inline their images as data URLs, and setHtml cannot
            # carry more than about 2 MB, so the document is loaded from a file
            # instead. That makes it local content, which cannot reach the
            # MathJax CDN unless it is allowed to - the same trade the PDF
            # exporter makes.
            settings = self.web_view.page().settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
            # A block is sized to its content, so a scrollbar here is always a
            # symptom rather than something to use - including the moment
            # between a block loading and its first measurement landing.
            settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            self.web_view.setMinimumSize(0, 0)
            self.web_view.setFixedHeight(STORY_TEXT_MIN_HEIGHT)
            self.web_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            self.web_view.loadFinished.connect(self._schedule_content_height_update)
            layout.addWidget(self.web_view)
        else:
            self.fallback = QTextBrowser()
            self.fallback.setReadOnly(True)
            self.fallback.setMinimumSize(0, 0)
            self.fallback.setFixedHeight(STORY_TEXT_MIN_HEIGHT)
            self.fallback.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            layout.addWidget(self.fallback)

    def set_blocks(self, blocks: Sequence[Any]) -> None:
        start_time = self.timing.start()
        blocks_key = tuple(_story_text_block_key(block) for block in blocks)
        if self._blocks_key == blocks_key:
            self.timing.log(
                "story.markdown.skip",
                start_time,
                [("blocks", len(blocks))],
            )
            return
        self._blocks_key = blocks_key
        self._content_height = None
        self._render_request_id += 1
        self.setVisible(True)
        task = StoryMarkdownRenderTask(
            request_id=self._render_request_id,
            blocks=list(blocks),
            blocks_key=blocks_key,
            render_markdown=self.render_markdown,
            timing=self.timing,
            runtime=self.runtime,
        )
        task.signals.finished.connect(self._apply_rendered_blocks)
        self.worker_pool.start(task)
        self.timing.log(
            "story.markdown.schedule",
            start_time,
            [("blocks", len(blocks))],
        )

    def _apply_rendered_blocks(self, result: object) -> None:
        if not isinstance(result, StoryMarkdownRenderResult):
            return
        if not self._active:
            return
        if result.request_id != self._render_request_id:
            return
        if result.blocks_key != self._blocks_key:
            return

        start_time = self.timing.start()
        if not result.has_content:
            self.setVisible(False)
            self.timing.log(
                "story.markdown.apply",
                start_time,
                [
                    ("blocks", result.block_count),
                    ("chars", result.char_count),
                    ("status", "empty"),
                ],
            )
            return

        self.setVisible(True)
        if self.web_view is not None:
            html_start_time = self.timing.start()
            self.setFixedHeight(STORY_TEXT_MIN_HEIGHT)
            self.web_view.setFixedHeight(STORY_TEXT_MIN_HEIGHT)
            self._load_story_html(_story_html(result.html_body, self.runtime.story_spacing))
            self.timing.log(
                "story.markdown.set_html",
                html_start_time,
                [
                    ("blocks", result.block_count),
                    ("chars", result.char_count),
                ],
            )
            self.timing.log(
                "story.markdown.apply",
                start_time,
                [
                    ("blocks", result.block_count),
                    ("chars", result.char_count),
                ],
            )
            return

        if self.fallback is not None:
            text_start_time = self.timing.start()
            self.fallback.setPlainText(result.plain_text)
            self._set_content_height(self.fallback.document().size().height() + 18)
            self.timing.log(
                "story.markdown.set_plain_text",
                text_start_time,
                [
                    ("blocks", result.block_count),
                    ("chars", result.char_count),
                ],
            )
        self.timing.log(
            "story.markdown.apply",
            start_time,
            [
                ("blocks", result.block_count),
                ("chars", result.char_count),
            ],
        )

    def _install_wheel_filter(self) -> None:
        if self.web_view is None:
            return
        # The widget that receives the wheel is a child the view creates for
        # itself, and it does not exist the instant the view does.
        proxy = self.web_view.focusProxy()
        if proxy is None:
            QTimer.singleShot(100, self._install_wheel_filter)
            return
        proxy.installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if not (event.type() in (QEvent.Type.Wheel, QEvent.Type.KeyPress)):
            return super().eventFilter(watched, event)
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.Wheel:
            self.zoomRequested.emit(1 if event.angleDelta().y() > 0 else -1)
            return True

        # Chromium zooms the focused view on these, which would zoom one block.
        direction = STORY_ZOOM_KEYS.get(event.key())
        if direction is None:
            return super().eventFilter(watched, event)
        self.zoomRequested.emit(direction)
        return True

    def set_zoom(self, factor: float) -> None:
        if self.web_view is None:
            return
        if self.web_view.page().zoomFactor() == factor:
            return
        self.web_view.page().setZoomFactor(factor)
        # A measured page zoomed is the same page seen closer: the document
        # keeps its CSS size, so its height on screen is known now, without
        # a round trip through the page. Sizing it at once means the column
        # is laid out once, rather than jumping as each run reports in. The
        # measurement still follows, for a page whose width does not zoom.
        if self._content_height is not None:
            self._apply_content_height(self._content_height, factor)
        self._request_content_height_update()

    def _on_link_clicked(self, url: QUrl) -> None:
        if url.host() == STORY_ANCHOR_HOST:
            self.anchorRequested.emit(url.path().lstrip("/"))
            return
        if not url.isLocalFile():
            QDesktopServices.openUrl(url)

    def _load_story_html(self, document_html: str) -> None:
        """Load the story document from a file rather than through setHtml.

        The file has to outlive the call, because loading is asynchronous, so
        the staging directory lives as long as the panel does.
        """

        if self.web_view is None:
            return

        source = _story_staging_file(f"block-{id(self):x}.html")
        source.write_text(document_html, encoding="utf-8")
        self.web_view.load(QUrl.fromLocalFile(str(source)))

    def dispose(self) -> None:
        self._active = False
        self._render_request_id += 1
        self.web_view = None
        self.fallback = None
        _story_staging_file(f"block-{id(self):x}.html").unlink(missing_ok=True)

    def _request_content_height_update(self, *args: Any) -> None:
        if self._active:
            self._height_timer.start()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        # Only width matters: it is what reflows the text. Reacting to height
        # would chase the change this panel just made itself.
        if event.oldSize().width() != event.size().width():
            self._request_content_height_update()

    def _schedule_content_height_update(self) -> None:
        if not self._active:
            return
        self._update_content_height()
        QTimer.singleShot(150, self._update_content_height)
        QTimer.singleShot(800, self._update_content_height)
        QTimer.singleShot(2000, self._update_content_height)

    def _update_content_height(self) -> None:
        if not self._active or self.web_view is None:
            return

        script = """
(() => {
  if (!document.body) {
    return 0;
  }

  // body is display:flow-root, so no child margin escapes it and its own
  // border box is the whole document. documentElement would be wrong here:
  // it never reports less than the viewport, so a block could grow but
  // never shrink again.
  return Math.ceil(document.body.getBoundingClientRect().height);
})()
"""
        self.web_view.page().runJavaScript(script, self._set_content_height)

    def _set_content_height(self, height: Any) -> None:
        if not self._active:
            return

        try:
            content_height = int(float(height))
        except (TypeError, ValueError):
            content_height = STORY_TEXT_MIN_HEIGHT

        self._content_height = content_height
        zoom = self.web_view.page().zoomFactor() if self.web_view is not None else 1.0
        self._apply_content_height(content_height, zoom)

    def _apply_content_height(self, content_height: int, zoom: float) -> None:
        # No upper bound: a text run is as tall as it needs to be, and the
        # story scrolls as a whole. Capping it clipped any block holding an
        # image, which is taller than a run of prose ever was.
        # The document reports CSS pixels, which stop being widget pixels the
        # moment the page is zoomed.
        panel_height = max(STORY_TEXT_MIN_HEIGHT, int(round(content_height * zoom)) + 4)
        if self.height() == panel_height:
            return

        self.setFixedHeight(panel_height)
        if self.web_view is not None:
            self.web_view.setFixedHeight(panel_height)
        if self.fallback is not None:
            self.fallback.setFixedHeight(panel_height)


_story_staging: TemporaryDirectory | None = None


def _story_staging_file(name: str) -> Path:
    """A path under one staging directory shared by every story block.

    Each block is loaded from a file rather than through setHtml, which cannot
    carry the data URLs the images become. One directory for all of them keeps
    a killed process from leaving a dozen behind, and TemporaryDirectory
    removes it on a normal exit.
    """

    global _story_staging

    if _story_staging is None:
        _story_staging = TemporaryDirectory(prefix="limelight-story-")
    return Path(_story_staging.name) / name


def _plain_text_html(text: str) -> str:
    escaped = html.escape(text).replace("\n", "<br>")
    return f"<p>{escaped}</p>"


class StoryWebPage(QWebEnginePage):
    """A story page that never navigates itself.

    The story panel renders each run of prose into its own document, so an
    in-page fragment cannot reach a figure that lives in another widget.
    Clicks are handed back to the panel, which scrolls the right widget into
    view instead. External links would otherwise replace the story with a web
    page, so they go to the browser.
    """

    linkClicked = Signal(QUrl)

    def acceptNavigationRequest(self, url: QUrl, navigation_type: Any, is_main_frame: bool) -> bool:
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self.linkClicked.emit(url)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


# Cross-reference links in the story panel are addressed to this host so the
# panel can intercept the click. Nothing is ever fetched from it.
STORY_ANCHOR_HOST = "limelight.internal"


def story_markdown_renderer(
    runtime: LimelightRuntime,
    *,
    intercept_anchors: bool = False,
) -> StoryMarkdownRenderer:
    """The story renderer, wired to resolve images out of the package.

    The story panel and the PDF exporter both use this, so an image renders
    identically on screen and on the page. They differ over cross-reference
    links: a PDF keeps real fragments, which its reader can follow, while the
    panel needs them rewritten into something it can intercept.
    """

    anchor_href = (
        (lambda anchor: f"https://{STORY_ANCHOR_HOST}/{anchor}") if intercept_anchors else None
    )
    return StoryMarkdownRenderer(
        runtime.image_data_url,
        runtime.image_number,
        runtime.image_anchor,
        anchor_href,
        runtime.image_display_width,
    )


def _pixmap_png_base64(pixmap: Any) -> str:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not pixmap.save(buffer, "PNG"):
            raise OSError("Could not encode screenshot as PNG")
    finally:
        buffer.close()
    return base64.b64encode(bytes(data)).decode("ascii")


def _story_text_block_key(block: Any) -> str:
    if isinstance(block, dict):
        return json.dumps(block, sort_keys=True)
    return str(block)


def _story_html(body: str, spacing: StorySpacing) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<script>
window.MathJax = {{
  tex: {{
    inlineMath: [['\\\\(', '\\\\)']],
    displayMath: [['\\\\[', '\\\\]']],
    processEscapes: true
  }},
  svg: {{ fontCache: 'global' }}
}};
</script>
<script defer src="{MATHJAX_SCRIPT}"></script>
<style>
html {{
  color-scheme: light;
}}
body {{
  box-sizing: border-box;
  display: flow-root;
  margin: 0;
  padding: 0;
  color: #202124;
  background: #ffffff;
  font: {STORY_FONT_PX}px/{STORY_LINE_HEIGHT} "{story_font_family()}", system-ui, sans-serif;
}}
code {{
  background: #f3f4f6;
  border-radius: 4px;
  padding: 0.1rem 0.25rem;
}}
pre {{
  background: #f3f4f6;
  border-radius: 6px;
  padding: 0.75rem;
  overflow-x: auto;
}}
.math-display {{
  overflow-x: auto;
}}
{STORY_IMAGE_CSS}
{STORY_FIGURE_CSS}
span.limelight-missing-image {{
  color: #b3261e;
  font-style: italic;
}}
{story_rhythm_css(spacing)}
{story_run_edge_css(spacing)}
</style>
</head>
<body>
{body}
</body>
</html>"""


@dataclass(frozen=True)
class StoryMarkdownRenderResult:
    request_id: int
    blocks_key: tuple[str, ...]
    html_body: str
    plain_text: str
    block_count: int
    char_count: int
    has_content: bool
    error: str | None = None


class StoryMarkdownRenderSignals(QObject):
    finished = Signal(object)


class StoryMarkdownRenderTask(QRunnable):
    def __init__(
        self,
        *,
        request_id: int,
        blocks: Sequence[Any],
        blocks_key: tuple[str, ...],
        render_markdown: bool,
        timing: TimingProbe,
        runtime: LimelightRuntime,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.blocks = list(blocks)
        self.blocks_key = blocks_key
        self.render_markdown = render_markdown
        self.timing = timing
        self.runtime = runtime
        self.signals = StoryMarkdownRenderSignals()

    def run(self) -> None:
        try:
            result = _render_story_markdown_payload(
                request_id=self.request_id,
                blocks=self.blocks,
                blocks_key=self.blocks_key,
                render_markdown=self.render_markdown,
                timing=self.timing,
                runtime=self.runtime,
            )
        except Exception as error:
            logger.exception("Story markdown render worker failed")
            result = StoryMarkdownRenderResult(
                request_id=self.request_id,
                blocks_key=self.blocks_key,
                html_body=_plain_text_html(f"Could not render story text: {error}"),
                plain_text=f"Could not render story text: {error}",
                block_count=len(self.blocks),
                char_count=0,
                has_content=True,
                error=str(error),
            )
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            # The panel this was rendering for went away while it ran, which
            # happens when the story is rebuilt or the window closes mid-render.
            logger.debug("Render finished after its panel went away")


def _render_story_markdown_payload(
    *,
    request_id: int,
    blocks: Sequence[Any],
    blocks_key: tuple[str, ...],
    render_markdown: bool,
    timing: TimingProbe,
    runtime: LimelightRuntime,
) -> StoryMarkdownRenderResult:
    start_time = timing.start()
    html_blocks: list[str] = []
    plain_blocks: list[str] = []
    renderer: StoryMarkdownRenderer | None = None

    for block in blocks:
        if isinstance(block, str):
            html_blocks.append(_plain_text_html(block))
            plain_blocks.append(block)
            continue

        if isinstance(block, dict) and block.get("markdown"):
            markdown = str(block["markdown"])
            if render_markdown:
                if renderer is None:
                    renderer = story_markdown_renderer(runtime, intercept_anchors=True)
                markdown_start_time = timing.start()
                html_blocks.append(renderer.render(markdown))
                timing.log(
                    "story.markdown.render",
                    markdown_start_time,
                    [("chars", len(markdown))],
                )
            else:
                html_blocks.append(_plain_text_html(markdown))
            plain_blocks.append(markdown)

    char_count = sum(len(item) for item in plain_blocks)
    timing.log(
        "story.markdown.compile",
        start_time,
        [
            ("blocks", len(blocks)),
            ("chars", char_count),
        ],
    )
    return StoryMarkdownRenderResult(
        request_id=request_id,
        blocks_key=blocks_key,
        html_body="\n".join(html_blocks),
        plain_text="\n\n".join(plain_blocks),
        block_count=len(blocks),
        char_count=char_count,
        has_content=bool(html_blocks),
    )


StaticFigureViewKey = tuple[str, int | None, str, tuple[tuple[str, str], ...], int, int, float]


@dataclass(frozen=True)
class StaticFigureViewRenderResult:
    request_id: int
    key: StaticFigureViewKey
    figure_id: str
    width: int
    height: int
    png_bytes: bytes
    error: str | None = None


class StaticFigureViewRenderSignals(QObject):
    finished = Signal(object)


class StaticFigureViewRenderTask(QRunnable):
    def __init__(
        self,
        *,
        request_id: int,
        key: StaticFigureViewKey,
        runtime: LimelightRuntime,
        figure_id: str,
        figure_view_index: int | None,
        figure_view_actions: Sequence[dict[str, Any]],
        parameter_values: dict[str, Any],
        width: int,
        height: int,
        layout_dpi: float,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.key = key
        self.runtime = runtime
        self.figure_id = figure_id
        self.figure_view_index = figure_view_index
        self.figure_view_actions = tuple(figure_view_actions)
        self.parameter_values = dict(parameter_values)
        self.width = width
        self.height = height
        self.layout_dpi = layout_dpi
        self.signals = StaticFigureViewRenderSignals()

    def run(self) -> None:
        try:
            png_bytes = _render_static_figure_view_png_bytes(
                self.runtime,
                self.figure_id,
                figure_view_index=self.figure_view_index,
                figure_view_actions=self.figure_view_actions,
                parameter_values=self.parameter_values,
                width=self.width,
                height=self.height,
                layout_dpi=self.layout_dpi,
            )
            result = StaticFigureViewRenderResult(
                request_id=self.request_id,
                key=self.key,
                figure_id=self.figure_id,
                width=self.width,
                height=self.height,
                png_bytes=png_bytes,
            )
        except Exception as error:
            logger.exception("Static figure view render worker failed for %s", self.figure_id)
            result = StaticFigureViewRenderResult(
                request_id=self.request_id,
                key=self.key,
                figure_id=self.figure_id,
                width=self.width,
                height=self.height,
                png_bytes=b"",
                error=str(error),
            )
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            # The panel this was rendering for went away while it ran, which
            # happens when the story is rebuilt or the window closes mid-render.
            logger.debug("Render finished after its panel went away")


def _parameter_signature(runtime: LimelightRuntime) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(parameter_id), str(value))
            for parameter_id, value in runtime.control_parameter_values.items()
        )
    )


def _static_figure_view_key(
    figure_id: str,
    *,
    figure_view_index: int | None = None,
    figure_view_actions: Sequence[dict[str, Any]],
    parameter_signature: tuple[tuple[str, str], ...],
    width: int,
    height: int,
    layout_dpi: float,
) -> StaticFigureViewKey:
    return (
        figure_id,
        figure_view_index,
        _figure_view_actions_signature(figure_view_actions),
        parameter_signature,
        width,
        height,
        layout_dpi,
    )


def _figure_view_actions_signature(actions: Sequence[dict[str, Any]]) -> str:
    return json.dumps(list(actions), sort_keys=True, separators=(",", ":"))


@dataclass
class _HoverPoint:
    axes: Any
    label: str
    x: Any
    y: Any
    x_categories: list[str] | None
    x_is_calendar: bool


_HOVER_PIXEL_THRESHOLD_SQ = 15.0**2


def _format_hover_x(x_value: float, x_categories: list[str] | None, x_is_calendar: bool) -> str:
    if x_categories:
        index = round(x_value)
        if 0 <= index < len(x_categories):
            return x_categories[index]
    if x_is_calendar:
        return mdates.num2date(x_value).strftime("%Y-%m-%d")
    return f"{x_value:.4g}"


def _render_static_figure_view_png_bytes(
    runtime: LimelightRuntime,
    figure_id: str,
    *,
    figure_view_index: int | None = None,
    figure_view_actions: Sequence[dict[str, Any]] = (),
    parameter_values: dict[str, Any] | None = None,
    width: int,
    height: int,
    layout_dpi: float = CSS_PIXELS_PER_INCH,
    dpi: int | None = None,
) -> bytes:
    """Draw one figure view, ``width`` by ``height`` pixels, to PNG.

    ``layout_dpi`` is what a point comes out as: at CSS_PIXELS_PER_INCH the
    labels are the story's text size, and a zoomed story passes a zoomed
    resolution so its figures zoom with it, the same figure seen closer. A
    higher output ``dpi`` only adds pixels, for the PDF.
    """

    start_time = runtime.timing.start()
    _configure_figure_typography()
    output_dpi = layout_dpi if dpi is None else dpi
    figure = MatplotlibFigure(
        figsize=(max(width, 1) / layout_dpi, max(height, 1) / layout_dpi),
        dpi=layout_dpi,
        constrained_layout=True,
    )
    FigureCanvasAgg(figure)
    _render_figure_view_to_matplotlib_figure(
        runtime,
        figure,
        figure_id,
        figure_view_index=figure_view_index,
        figure_view_actions=figure_view_actions,
        parameter_values=parameter_values,
    )

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=output_dpi)
    runtime.timing.log(
        "story.static_figure_view.render",
        start_time,
        [
            ("figure", figure_id),
            ("width", width),
            ("height", height),
            ("dpi", output_dpi),
        ],
    )
    return buffer.getvalue()


def _render_figure_view_to_matplotlib_figure(
    runtime: LimelightRuntime,
    figure: MatplotlibFigure,
    figure_id: str | None,
    *,
    figure_view_index: int | None = None,
    figure_view_actions: Sequence[dict[str, Any]] = (),
    parameter_values: dict[str, Any] | None = None,
    zoom_syncs: list[Any] | None = None,
    hover_sink: list[_HoverPoint] | None = None,
) -> None:
    figure.clear()

    if figure_id is None:
        axes = figure.add_subplot(111)
        axes.text(0.5, 0.5, "No figure selected", ha="center", va="center")
        axes.set_axis_off()
        return

    figure_spec = runtime.figure_specs.get(figure_id)
    if figure_spec is None:
        axes = figure.add_subplot(111)
        axes.text(0.5, 0.5, f"Unknown figure: {figure_id}", ha="center", va="center")
        axes.set_axis_off()
        return

    if figure_spec["mapSpecs"]:
        axes = figure.add_subplot(111)
        _draw_map_contents(runtime, axes, figure_id, figure_spec["mapSpecs"][0], figure_view_index=figure_view_index)
        return

    # Every axesSpec is one panel in a vertical stack. The AxesSpec `frame`
    # field is not consulted: the writer emits the same placeholder frame for
    # every panel, so honouring it would overlap them.
    axes_specs = figure_spec["axesSpecs"]
    first_axes_spec(figure_spec)  # raises on an empty list
    panels = figure.subplots(len(axes_specs), 1, squeeze=False)[:, 0]
    _share_x_axes(panels, axes_specs)

    last = len(axes_specs) - 1
    for index, (axes, axes_spec) in enumerate(zip(panels, axes_specs)):
        _draw_plot_contents(
            runtime,
            axes,
            figure_id,
            axes_spec,
            figure_view_index=figure_view_index,
            figure_view_actions=_actions_for_axes(figure_view_actions, axes_spec["id"]),
            parameter_values=parameter_values,
            zoom_syncs=zoom_syncs,
            show_title=index == 0,
            show_x_axis=index == last,
            hover_sink=hover_sink,
        )


def _share_x_axes(panels: Sequence[Any], axes_specs: Sequence[dict[str, Any]]) -> None:
    """Links the x-axes of panels whose xAxis specs name the same shareGroup.

    Panels with no shareGroup are still linked to each other: a stack of
    panels reads as one figure, and a package written before shareGroup was
    honoured expects to pan and zoom them together.
    """
    leaders: dict[str | None, Any] = {}
    for axes, axes_spec in zip(panels, axes_specs):
        group = axes_spec["xAxis"].get("shareGroup")
        leader = leaders.get(group)
        if leader is None:
            leaders[group] = axes
        else:
            axes.sharex(leader)


def _draw_scatter_artist(
    axes: Any,
    artist: dict[str, Any],
    series: Any,
    x_values: list[float],
    y_values: list[float],
    alpha: float | None,
    label: str,
    line_colors: dict[str, str],
    y_name: str,
) -> None:
    marker = artist.get("marker") or "o"
    alpha_value = 1.0 if alpha is None else alpha
    collection = None
    if series.color_kind == "continuous":
        collection = axes.scatter(
            x_values,
            y_values,
            label=label,
            s=series.sizes or 24.0,
            c=series.colors,
            cmap="viridis",
            marker=marker,
            edgecolors="white",
            linewidths=0.45,
            alpha=alpha_value,
            zorder=3,
        )
        axes.figure.colorbar(collection, ax=axes, label=series.color_label)
    elif series.color_kind == "categorical":
        palette = matplotlib.colormaps["tab10"].colors
        categories: list[str] = []
        seen: set[str] = set()
        for value in series.colors:
            if value not in seen:
                seen.add(value)
                categories.append(value)
        for category_index, category in enumerate(categories):
            indices = [index for index, value in enumerate(series.colors) if value == category]
            axes.scatter(
                [x_values[index] for index in indices],
                [y_values[index] for index in indices],
                label=str(category),
                s=[series.sizes[index] for index in indices] if series.sizes else 24.0,
                color=palette[category_index % len(palette)],
                marker=marker,
                edgecolors="white",
                linewidths=0.45,
                alpha=alpha_value,
                zorder=3,
            )
    else:
        color = artist.get("color") or line_colors.get(y_name)
        collection = axes.scatter(
            x_values,
            y_values,
            label=label,
            s=series.sizes or 24.0,
            color=color,
            marker=marker,
            edgecolors="white",
            linewidths=0.45,
            alpha=alpha_value,
            zorder=3,
        )
    if collection is not None and artist.get("sizeBy") is not None:
        handles, size_labels = collection.legend_elements(prop="sizes", num=4)
        size_legend = axes.legend(handles, size_labels, title=series.size_label, loc="lower right")
        axes.add_artist(size_legend)


def _draw_plot_contents(
    runtime: LimelightRuntime,
    axes: Any,
    figure_id: str,
    axes_spec: dict[str, Any],
    *,
    figure_view_index: int | None = None,
    figure_view_actions: Sequence[dict[str, Any]],
    parameter_values: dict[str, Any] | None = None,
    zoom_syncs: list[Any] | None = None,
    show_title: bool = True,
    show_x_axis: bool = True,
    hover_sink: list[_HoverPoint] | None = None,
) -> tuple[list[Any], list[str]]:
    active_parameter_values = runtime.control_parameter_values if parameter_values is None else parameter_values
    x_axis_binding = axes_spec["xAxis"]
    y_axis_binding = axes_spec["yAxis"]
    if axis_scale(x_axis_binding) == "Log":
        axes.set_xscale("log")
    axes_actions = _combined_axes_actions(axes_spec, figure_view_actions)
    x_window, y_window = _axes_action_windows(axes_actions, x_axis_binding, y_axis_binding)
    has_data = False
    line_colors: dict[str, str] = {}
    for action in axes_actions:
        artist = _axes_action_plot_artist(action)
        if artist is None:
            continue
        if not _artist_visible(artist, active_parameter_values):
            continue
        if _is_timeseries_artist(artist):
            artist_start_time = runtime.timing.start()
            zoom_sync = _install_timeseries_artist(runtime, axes, artist)
            if zoom_sync is not None:
                if zoom_syncs is not None:
                    zoom_syncs.append(zoom_sync)
                runtime.timing.log(
                    "plot.envelope.build",
                    artist_start_time,
                    [
                        ("figure", figure_id),
                        ("artist", artist.get("id") or artist["y"]),
                        ("kind", "timeSeries"),
                    ],
                )
                has_data = True
            continue
        series = runtime.plot_series(artist, axis_data_type_kind(x_axis_binding))
        if not series.points:
            continue
        artist_start_time = runtime.timing.start()
        x_values = _plot_x_values([x for x, _ in series.points], x_axis_binding)
        y_values = [y for _, y in series.points]
        if series.x_categories:
            axes.set_xticks(range(len(series.x_categories)))
            axes.set_xticklabels(series.x_categories)
        alpha = _to_float(artist.get("alpha"))
        y_name = artist.get("y") or ""
        if _is_scatter_artist(artist):
            label = artist.get("label") or "_nolegend_"
            _draw_scatter_artist(axes, artist, series, x_values, y_values, alpha, label, line_colors, y_name)
            artist_kind = "scatter"
            hover_label = label
        elif _is_stem_artist(artist):
            label = artist.get("label") or "_nolegend_"
            baseline = _to_float(artist.get("baseline")) or 0.0
            markerline, stemlines, baseline_artist = axes.stem(
                x_values,
                y_values,
                bottom=baseline,
                basefmt=" ",
            )
            markerline.set_label(label)
            markerline.set_alpha(1.0 if alpha is None else alpha)
            stemlines.set_alpha(1.0 if alpha is None else alpha)
            stem_color = artist.get("color")
            if stem_color:
                markerline.set_color(stem_color)
                stemlines.set_color(stem_color)
            artist_kind = "stem"
            hover_label = label
        else:
            (line,) = axes.plot(
                x_values,
                y_values,
                label=series.label,
                color=artist.get("color"),
                linestyle=artist.get("linestyle") or "-",
                marker=artist.get("marker"),
                linewidth=2.0,
                alpha=1.0 if alpha is None else alpha,
            )
            line_colors[y_name] = line.get_color()
            artist_kind = "line"
            hover_label = series.label
        if hover_sink is not None:
            hover_sink.append(
                _HoverPoint(
                    axes=axes,
                    label=hover_label,
                    x=np.asarray(x_values, dtype=float),
                    y=np.asarray(y_values, dtype=float),
                    x_categories=series.x_categories,
                    x_is_calendar=_axis_calendar(x_axis_binding) == "CalendarDay",
                )
            )
        runtime.timing.log(
            "plot.artist.build",
            artist_start_time,
            [
                ("figure", figure_id),
                ("artist", artist.get("id") or y_name),
                ("kind", artist_kind),
                ("points", len(series.points)),
            ],
        )
        has_data = True

    if not has_data:
        axes.text(0.5, 0.5, "No plottable data", ha="center", va="center")
        axes.set_axis_off()
        return [], []

    if show_x_axis and x_window is not None:
        axes.set_xlim(*_plot_window(x_window, x_axis_binding))
    if y_window is not None:
        axes.set_ylim(*y_window)

    _apply_axes_action_decorators(axes, axes_spec, axes_actions)
    axes.set_ylabel(axis_label(y_axis_binding))
    axes.grid(True, color="#dddddd", linewidth=0.8)
    if show_title:
        axes.set_title(runtime.figure_specs[figure_id]["title"])
    if show_x_axis:
        axes.set_xlabel(axis_label(x_axis_binding))
        _format_axis(axes, x_axis_binding, "x")
    else:
        axes.tick_params(axis="x", labelbottom=False)
    handles, labels = axes.get_legend_handles_labels()
    if handles:
        axes.legend(handles, labels, loc="best")
    return list(handles), list(labels)


def _draw_map_contents(
    runtime: LimelightRuntime,
    axes: Any,
    figure_id: str,
    map_spec: dict[str, Any],
    *,
    figure_view_index: int | None = None,
) -> None:
    has_data = False
    longitude_window, latitude_window = _map_action_windows(map_spec["actions"])
    for action in map_spec["actions"]:
        geojson_layer = _map_action_geojson_layer(action)
        if geojson_layer is not None:
            _draw_geojson_layer(runtime, axes, geojson_layer)
            has_data = True
            continue
        scatter = _map_action_dataset_scatter(action)
        if scatter is not None:
            map_series = _map_scatter_points(runtime, scatter)
            if not map_series.points:
                continue
            _draw_map_scatter(axes, scatter, map_series)
            has_data = True

    if not has_data:
        axes.text(0.5, 0.5, "No plottable map data", ha="center", va="center")
        axes.set_axis_off()
        return

    if longitude_window is not None:
        axes.set_xlim(*longitude_window)
    if latitude_window is not None:
        axes.set_ylim(*latitude_window)
    axes.set_title(runtime.figure_specs[figure_id]["title"])
    axes.set_xlabel("Longitude")
    axes.set_ylabel("Latitude")
    axes.set_aspect("equal", adjustable="box")
    axes.grid(True, color="#dddddd", linewidth=0.6)
    axes.legend(loc="best")


def _map_action_windows(actions: Sequence[dict[str, Any]]) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    longitude_window = None
    latitude_window = None
    for action in actions:
        limits = action.get("MapActionSetLimits")
        if limits is None and "longitudeLower" in action:
            limits = action
        if limits is None:
            continue
        longitude_window = (float(limits["longitudeLower"]), float(limits["longitudeUpper"]))
        latitude_window = (float(limits["latitudeLower"]), float(limits["latitudeUpper"]))
    return longitude_window, latitude_window


def _map_action_geojson_layer(action: dict[str, Any]) -> dict[str, Any] | None:
    layer = action.get("MapActionAddGeoJsonLayer")
    if layer is not None:
        return layer
    if "path" in action:
        return action
    return None


def _map_action_dataset_scatter(action: dict[str, Any]) -> dict[str, Any] | None:
    scatter = action.get("MapActionAddDatasetScatter")
    if scatter is not None:
        return scatter
    if "longitude" in action and "latitude" in action:
        return action
    return None


def _draw_map_scatter(axes: Any, scatter: dict[str, Any], series: "MapScatterSeries") -> None:
    longitudes = [longitude for longitude, _ in series.points]
    latitudes = [latitude for _, latitude in series.points]
    label = scatter.get("label") or "_nolegend_"
    if series.color_kind == "continuous":
        collection = axes.scatter(
            longitudes,
            latitudes,
            label=label,
            s=series.sizes or 32.0,
            c=series.colors,
            cmap="viridis",
            edgecolors="white",
            linewidths=0.5,
            zorder=4,
        )
        axes.figure.colorbar(collection, ax=axes, label=series.color_label)
    elif series.color_kind == "categorical":
        palette = matplotlib.colormaps["tab10"].colors
        categories: list[str] = []
        seen: set[str] = set()
        for value in series.colors:
            if value not in seen:
                seen.add(value)
                categories.append(value)
        for category_index, category in enumerate(categories):
            indices = [index for index, value in enumerate(series.colors) if value == category]
            axes.scatter(
                [longitudes[index] for index in indices],
                [latitudes[index] for index in indices],
                label=str(category),
                s=[series.sizes[index] for index in indices] if series.sizes else 32.0,
                color=palette[category_index % len(palette)],
                edgecolors="white",
                linewidths=0.5,
                zorder=4,
            )
    else:
        axes.scatter(
            longitudes,
            latitudes,
            label=label,
            s=series.sizes or 32.0,
            edgecolors="white",
            linewidths=0.5,
            zorder=4,
        )


def _draw_geojson_layer(runtime: LimelightRuntime, axes: Any, layer: dict[str, Any]) -> None:
    geojson = json.loads(runtime.package.read_text(layer["path"]))
    patches = []
    for ring in _geojson_polygon_rings(geojson):
        if len(ring) >= 3:
            patches.append(Polygon(ring, closed=True))
    if not patches:
        return
    collection = PatchCollection(
        patches,
        facecolor=layer.get("fill") or "#eeeeee",
        edgecolor=layer.get("stroke") or "#888888",
        linewidths=0.7,
        alpha=_to_float(layer.get("alpha")) if layer.get("alpha") is not None else 0.75,
        zorder=1,
        label=layer.get("label") or "_nolegend_",
    )
    axes.add_collection(collection)
    axes.autoscale_view()


def _geojson_polygon_rings(geojson: dict[str, Any]) -> list[list[tuple[float, float]]]:
    rings: list[list[tuple[float, float]]] = []
    geojson_type = geojson["type"]
    if geojson_type == "FeatureCollection":
        for feature in geojson["features"]:
            rings.extend(_geojson_polygon_rings(feature["geometry"]))
        return rings
    if geojson_type == "Feature":
        return _geojson_polygon_rings(geojson["geometry"])
    if geojson_type == "Polygon":
        return [_geojson_ring_to_points(ring) for ring in geojson["coordinates"][:1]]
    if geojson_type == "MultiPolygon":
        for polygon in geojson["coordinates"]:
            rings.extend(_geojson_ring_to_points(ring) for ring in polygon[:1])
        return rings
    raise TypeError(f"Unsupported GeoJSON geometry type {geojson_type!r}")


def _geojson_ring_to_points(ring: Sequence[Sequence[Any]]) -> list[tuple[float, float]]:
    points = []
    for coordinate in ring:
        if len(coordinate) < 2:
            continue
        longitude = _to_float(coordinate[0])
        latitude = _to_float(coordinate[1])
        if longitude is not None and latitude is not None:
            points.append((longitude, latitude))
    return points


@dataclass(frozen=True)
class MapScatterSeries:
    points: list[tuple[float, float]]
    sizes: list[float] | None = None
    colors: list[Any] | None = None
    color_kind: str | None = None
    color_label: str | None = None


def _map_scatter_points(runtime: LimelightRuntime, scatter: dict[str, Any]) -> MapScatterSeries:
    longitude_table, longitude_column = refs.parse_column_ref(scatter["longitude"])
    latitude_table, latitude_column = refs.parse_column_ref(scatter["latitude"])
    longitude_rows = runtime.rows_for_source(longitude_table)
    latitude_rows = (
        longitude_rows if latitude_table == longitude_table else runtime.rows_for_source(latitude_table)
    )

    size_ref = scatter.get("sizeBy")
    size_values: list[float | None] | None = None
    if size_ref is not None:
        size_table, size_column = refs.parse_column_ref(size_ref)
        size_rows = longitude_rows if size_table == longitude_table else runtime.rows_for_source(size_table)
        size_values = [_to_float(row.get(size_column)) for row in size_rows]

    color_ref = scatter.get("colorBy")
    color_values: list[Any] | None = None
    color_kind: str | None = None
    color_label: str | None = None
    if color_ref is not None:
        color_table, color_column = refs.parse_column_ref(color_ref)
        color_rows = longitude_rows if color_table == longitude_table else runtime.rows_for_source(color_table)
        raw_color_values = [row.get(color_column) for row in color_rows]
        color_label = color_column
        non_null = [value for value in raw_color_values if value is not None]
        if non_null and all(_to_float(value) is not None for value in non_null):
            color_kind = "continuous"
            color_values = [_to_float(value) for value in raw_color_values]
        else:
            color_kind = "categorical"
            color_values = [str(value) if value is not None else None for value in raw_color_values]

    count = min(len(longitude_rows), len(latitude_rows))
    if size_values is not None:
        count = min(count, len(size_values))
    if color_values is not None:
        count = min(count, len(color_values))

    points = []
    sizes = [] if size_values is not None else None
    colors = [] if color_values is not None else None
    for index in range(count):
        longitude = _to_float(longitude_rows[index].get(longitude_column))
        latitude = _to_float(latitude_rows[index].get(latitude_column))
        if longitude is not None and latitude is not None:
            points.append((longitude, latitude))
            if sizes is not None:
                sizes.append(size_values[index] or 32.0)
            if colors is not None:
                colors.append(color_values[index])
    return MapScatterSeries(points=points, sizes=sizes, colors=colors, color_kind=color_kind, color_label=color_label)


def _axes_action_plot_artist(action: Any) -> dict[str, Any] | None:
    add_data = action.get("AxesActionAddData")
    if add_data is not None:
        return add_data
    if "y" in action:
        return action
    return None


def _actions_for_axes(
    figure_view_actions: Sequence[dict[str, Any]],
    axes_id: Any,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        action_binding["action"]
        for action_binding in figure_view_actions
        if action_binding["ref"] == axes_id
    )


def _combined_axes_actions(
    axes_spec: dict[str, Any],
    figure_view_actions: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    base_actions = tuple(axes_spec["actions"])
    return (*base_actions, *tuple(figure_view_actions))


def _axes_action_windows(
    actions: Sequence[dict[str, Any]],
    x_axis_binding: dict[str, Any],
    y_axis_binding: dict[str, Any],
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    x_window = None
    y_window = None
    for action in actions:
        limit = action.get("AxesActionSetLimits")
        if limit is None and is_axis_limit_payload(action):
            limit = action
        if limit is None:
            continue
        limit_axis, window = _axis_limit_window(limit, x_axis_binding, y_axis_binding)
        if limit_axis == "x":
            x_window = window
        else:
            y_window = window
    return x_window, y_window


def _axis_limit_window(
    limit: dict[str, Any],
    x_axis_binding: dict[str, Any],
    y_axis_binding: dict[str, Any],
) -> tuple[str, tuple[float, float] | None]:
    shape = axis_limit_shape(limit)
    if shape.kind == "continuous":
        return (shape.axis, _float_limit_window(limit, shape.axis))
    axis_binding = x_axis_binding if shape.axis == "x" else y_axis_binding
    return (shape.axis, _utc_time_window(limit, axis_binding))


def _float_limit_window(limit: dict[str, Any], axis: str) -> tuple[float, float] | None:
    lower = _to_float(limit.get(f"{axis}Lower"))
    upper = _to_float(limit.get(f"{axis}Upper"))
    if lower is None or upper is None or lower == upper:
        return None
    return (min(lower, upper), max(lower, upper))


def _utc_time_window(
    limit: dict[str, Any],
    axis_binding: dict[str, Any],
) -> tuple[float, float] | None:
    axis = "x" if "xStart" in limit else "y"
    start = _time_limit_coordinate(limit.get(f"{axis}Start"), axis_binding)
    end = _time_limit_coordinate(limit.get(f"{axis}End"), axis_binding)
    if start is None or end is None or start == end:
        return None
    return (min(start, end), max(start, end))


def _axis_limit_point(
    limit: dict[str, Any],
    x_axis_binding: dict[str, Any],
    y_axis_binding: dict[str, Any],
) -> tuple[str, float] | None:
    """Like `_axis_limit_window`, but for the degenerate lower==upper case that
    `_float_limit_window`/`_utc_time_window` collapse to `None` (correct for
    `AxesActionSetLimits`, which must never set a zero-width axis range, but wrong
    for `AxesDecoratorVSpan`, which uses lower==upper to mean a single-point marker).
    """
    shape = axis_limit_shape(limit)
    axis = shape.axis
    if shape.kind == "continuous":
        lower = _to_float(limit.get(f"{axis}Lower"))
        upper = _to_float(limit.get(f"{axis}Upper"))
        if lower is not None and upper is not None and lower == upper:
            return (axis, lower)
        return None
    axis_binding = x_axis_binding if axis == "x" else y_axis_binding
    start = _time_limit_coordinate(limit.get(f"{axis}Start"), axis_binding)
    end = _time_limit_coordinate(limit.get(f"{axis}End"), axis_binding)
    if start is not None and end is not None and start == end:
        return (axis, start)
    return None


def _artist_visible(artist: dict[str, Any], parameter_values: dict[str, Any]) -> bool:
    visible_when = artist.get("visibleWhen")
    if visible_when is None:
        return True
    parameter_id = visible_when.get("controlParameter")
    expected_value = visible_when.get("value")
    if parameter_id is None:
        return True
    return parameter_values.get(parameter_id) == expected_value


def _apply_axes_action_decorators(
    axes: Any,
    axes_spec: dict[str, Any],
    axes_actions: Sequence[dict[str, Any]],
) -> None:
    for action in axes_actions:
        decorator = action.get("AxesActionAddDecorator")
        if decorator is None and ("xLimit" in action or "arrow" in action):
            decorator = action
        if decorator is None:
            continue
        annotation = decorator.get("AxesDecoratorAnnotation")
        if annotation is None and "arrow" in decorator:
            annotation = decorator
        if annotation is not None:
            _apply_arrow_annotation(axes, annotation)
            continue

        rect = decorator.get("AxesDecoratorRect")
        if rect is None and "yLimit" in decorator:
            rect = decorator
        if rect is not None:
            _apply_rect_decorator(axes, axes_spec, rect)
            continue

        vspan = decorator.get("AxesDecoratorVSpan")
        if vspan is None and "xLimit" in decorator:
            vspan = decorator
        if vspan is None:
            raise TypeError(f"Unknown AxesDecorator constructor: {decorator!r}")
        point = _axis_limit_point(vspan["xLimit"], axes_spec["xAxis"], axes_spec["yAxis"])
        if point is not None:
            limit_axis, marker_x = point
            if limit_axis != "x":
                raise TypeError(f"AxesDecoratorVSpan requires an x-axis AxisLimit: {vspan!r}")
            marker_x = _plot_window((marker_x, marker_x), axes_spec["xAxis"])[0]
            color = _DEFAULT_DECORATOR_COLOR
            axes.axvline(
                marker_x,
                color=color,
                linestyle=":",
                linewidth=1.7,
                alpha=0.95,
            )
            label = vspan.get("label")
            if label:
                axes.text(
                    marker_x,
                    0.98,
                    label,
                    ha="right",
                    va="top",
                    rotation=90,
                    transform=axes.get_xaxis_transform(),
                    color=color,
                    fontsize=8,
                )
            continue
        limit_axis, raw_window = _axis_limit_window(vspan["xLimit"], axes_spec["xAxis"], axes_spec["yAxis"])
        if limit_axis != "x":
            raise TypeError(f"AxesDecoratorVSpan requires an x-axis AxisLimit: {vspan!r}")
        if raw_window is not None:
            plot_window = _plot_window(raw_window, axes_spec["xAxis"])
            axes.axvspan(plot_window[0], plot_window[1], color="#f3d36b", alpha=0.35)
            label = vspan.get("label")
            if label:
                axes.text(
                    sum(plot_window) / 2,
                    0.96,
                    label,
                    ha="center",
                    va="top",
                    transform=axes.get_xaxis_transform(),
                )


def _apply_rect_decorator(axes: Any, axes_spec: dict[str, Any], rect: dict[str, Any]) -> None:
    """Shade the box between the rect's x and y limits, without moving the axes.

    The box is added as a plain artist rather than a patch so that it does not
    take part in autoscaling: a decorator marks out a region of the data, and
    the data decides what the axes show, as with a VSpan.
    """

    x_axis, x_raw = _axis_limit_window(rect["xLimit"], axes_spec["xAxis"], axes_spec["yAxis"])
    y_axis, y_raw = _axis_limit_window(rect["yLimit"], axes_spec["xAxis"], axes_spec["yAxis"])
    if x_axis != "x" or y_axis != "y":
        raise TypeError(f"AxesDecoratorRect needs an x-axis xLimit and a y-axis yLimit: {rect!r}")
    if x_raw is None or y_raw is None:
        return
    x_window = _plot_window(x_raw, axes_spec["xAxis"])
    y_window = _plot_window(y_raw, axes_spec["yAxis"])

    color = rect.get("color") or _DEFAULT_RECT_COLOR
    alpha = _to_float(rect.get("alpha"))
    box = Rectangle(
        (x_window[0], y_window[0]),
        x_window[1] - x_window[0],
        y_window[1] - y_window[0],
        facecolor=color,
        edgecolor=color,
        alpha=_DEFAULT_RECT_ALPHA if alpha is None else alpha,
        linewidth=1.2,
        # Under the data lines, as a VSpan is.
        zorder=1,
    )
    axes.add_artist(box)

    label = rect.get("label")
    if label:
        axes.text(
            (x_window[0] + x_window[1]) / 2,
            y_window[1],
            label,
            ha="center",
            va="bottom",
            fontsize=8,
            color=_DEFAULT_DECORATOR_COLOR,
            clip_on=True,
        )


def _apply_arrow_annotation(axes: Any, annotation: dict[str, Any]) -> None:
    arrow = annotation.get("arrow")
    if arrow is None:
        return
    start = _plot_point(arrow.get("start"))
    end = _plot_point(arrow.get("end"))
    if start is None or end is None:
        return

    label = annotation.get("label") or ""
    color = annotation.get("color") or _DEFAULT_DECORATOR_COLOR
    if abs(start[0] - end[0]) < 1e-12 and abs(start[1] - end[1]) < 1e-12:
        axes.scatter(
            [end[0]],
            [end[1]],
            color=color,
            edgecolors="white",
            linewidths=0.8,
            s=46,
            zorder=5,
        )
        if label:
            axes.annotate(
                text=label,
                xy=end,
                xytext=_point_label_offset(annotation),
                textcoords="offset points",
                color=color,
                fontsize=8,
                ha="left",
                va="center",
                annotation_clip=True,
            )
        return

    axes.annotate(
        text=label,
        xy=end,
        xytext=_arrow_label_position(start, end, label),
        textcoords="data",
        arrowprops={"arrowstyle": "->", "color": color, "linewidth": 1.8},
        color=color,
        fontsize=9,
        ha="center",
        va="center",
        annotation_clip=True,
    )


def _format_axis(axes: Any, axis_binding: dict[str, Any], orientation: str) -> None:
    calendar = _axis_calendar(axis_binding)
    if orientation == "x" and calendar == "monthOrdinal1970":
        _format_month_ordinal_axis(axes)
    if orientation == "x" and calendar in {"CalendarDay", "matplotlibDateNumber"}:
        _format_matplotlib_date_axis(axes)


def _month_ordinal_axis_step(axes: Any) -> None:
    lower, upper = axes.get_xlim()
    span = abs(upper - lower)

    if span <= 24:
        major_step = 1
        label_mode = "month"
    elif span <= 72:
        major_step = 3
        label_mode = "quarter"
    else:
        major_step = 12
        label_mode = "year"

    axes.xaxis.set_major_locator(MultipleLocator(major_step))
    axes.xaxis.set_minor_locator(MultipleLocator(1))
    axes.xaxis.set_major_formatter(
        FuncFormatter(lambda value, _: _month_ordinal_label(value, label_mode))
    )


def _format_month_ordinal_axis(axes: Any) -> None:
    _month_ordinal_axis_step(axes)
    axes.tick_params(axis="x", labelrotation=35)
    axes.grid(True, which="minor", axis="x", color="#eeeeee", linewidth=0.45)
    axes.callbacks.connect("xlim_changed", _month_ordinal_axis_step)


def _format_matplotlib_date_axis(axes: Any) -> None:
    locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    axes.xaxis.set_major_locator(locator)
    axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axes.tick_params(axis="x", labelrotation=35)


def _shortcuts(standard: QKeySequence.StandardKey, *extras: str) -> list[QKeySequence]:
    """The platform's bindings for ``standard``, plus any of ``extras`` it lacks.

    A key sequence registered twice on one action - which is what happens
    when a platform's standard binding is also listed by hand - is an
    ambiguous shortcut to Qt, and an ambiguous shortcut triggers nothing.
    """

    shortcuts = list(QKeySequence.keyBindings(standard))
    for extra in extras:
        sequence = QKeySequence(extra)
        if sequence not in shortcuts:
            shortcuts.append(sequence)
    return shortcuts


class CacheProgressSignals(QObject):
    """Carries a large-series cache build's progress onto the GUI thread.

    Builds run wherever a figure is first drawn - a story render worker or
    the main thread - so the report goes through a signal, which Qt queues
    across threads and calls directly on the main one.
    """

    progress = Signal(str, int, int)


class CacheProgressIndicator(QWidget):
    """A status-bar widget: which large series are being cached, and how far along.

    One entry per (source, column) build in flight; the bar shows the samples
    streamed across all of them, and the widget hides itself once every
    build has finished.
    """

    LINGER_MS = 1500

    def __init__(self) -> None:
        super().__init__()
        self._builds: dict[str, tuple[int, int]] = {}
        self.label = QLabel()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(120)
        self.bar.setFixedHeight(12)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(6)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_if_idle)
        self.hide()

    def report(self, series: str, done: int, total: int) -> None:
        self._builds[series] = (done, total)
        active = {name: (d, t) for name, (d, t) in self._builds.items() if d < t}
        done_all = sum(d for d, _ in self._builds.values())
        total_all = sum(t for _, t in self._builds.values())
        self.bar.setValue(int(1000 * done_all / total_all) if total_all else 0)
        if active:
            name = next(iter(active))
            others = f" (+{len(active) - 1} more)" if len(active) > 1 else ""
            percent = int(100 * done_all / total_all) if total_all else 0
            self.label.setText(f"Preparing large-series cache for {name}{others}: {percent}%")
            self._hide_timer.stop()
        else:
            self.label.setText("Large-series cache ready")
            self._hide_timer.start(self.LINGER_MS)
        self.show()

    def _hide_if_idle(self) -> None:
        if all(d >= t for d, t in self._builds.values()):
            self._builds.clear()
            self.hide()


class LimelightWindow(QMainWindow):
    def __init__(self, package: LimelightPackage, manifest: dict[str, Any], *, debug_timing: bool = False) -> None:
        super().__init__()
        self.debug_timing = debug_timing
        self.runtime = LimelightRuntime(package, manifest, debug_timing=debug_timing)
        self.setWindowIcon(_application_icon())
        self.figure_view_windows: dict[tuple[str, int | None], FigureViewWindow] = {}
        self.table_view_windows: dict[tuple[str, str | None], "TableViewWindow"] = {}
        self._recent_paths: list[str] = self._load_recent_paths()
        self._busy_cursor_active = False
        self.tabs: QTabWidget | None = None
        self.story_tree: QTreeWidget | None = None
        self.story_title: QLabel | None = None
        self.story_blocks: StoryBlockPanel | None = None
        self.figure_view_tree: QTreeWidget | None = None
        self.figure_view_title: QLabel | None = None
        self.figure_view_panel: InteractiveFigureViewPanel | None = None
        self.figure_view_caption: QLabel | None = None
        self._cache_progress = CacheProgressIndicator()
        self.statusBar().addPermanentWidget(self._cache_progress)
        self._cache_progress_signals = CacheProgressSignals()
        self._cache_progress_signals.progress.connect(self._on_cache_progress)
        self._update_window_title()
        self._build_menus()
        self._add_recent_path(package.path)

        self._build_central_tabs()

    def _report_cache_progress(self, source_id: str, column: str, done: int, total: int) -> None:
        # Runs on whichever thread is building the cache; only the signal crosses.
        self._cache_progress_signals.progress.emit(f"{source_id}['{column}']", done, total)

    def _on_cache_progress(self, series: str, done: int, total: int) -> None:
        self._cache_progress.report(series, done, total)
        # A build on the main thread (an interactive figure's first draw)
        # blocks the event loop, so nothing would repaint until it finished;
        # let paint events through, but not the user's clicks.
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _build_central_tabs(self) -> None:
        if self.story_blocks is not None:
            self.story_blocks.dispose()
            self.story_blocks = None

        # Every widget below belongs to the document being replaced. The story
        # tab is built first and asks the figures tab what is selected, so a
        # reference left here is a question about the old document answered
        # against the new runtime - which is how opening a second package used
        # to fail before it had drawn anything.
        self.story_tree = None
        self.story_title = None
        self.figure_view_tree = None
        self.figure_view_title = None
        self.figure_view_caption = None
        self.figure_view_panel = None

        runtime = self.runtime
        runtime.cache_progress = self._report_cache_progress
        tabs = QTabWidget()
        # Every tab is built, so the wiring between them holds, but a tab with
        # nothing to show is hidden rather than left empty: a package with no
        # parameters has no Parameters tab. Tabs are found by name, never by
        # index, so hiding one moves nothing.
        for widget, title, has_content in (
            (self._build_story_tab(), "Story", bool(runtime.story_blocks)),
            (self._build_figures_tab(), "Figures", bool(runtime.figure_spec_order)),
            (self._build_data_tab(), "Data", bool(runtime.sources)),
            (self._build_control_parameters_tab(), "Parameters", bool(runtime.control_parameters)),
        ):
            index = tabs.addTab(widget, title)
            tabs.setTabVisible(index, has_content)
        self.tabs = tabs
        self.setCentralWidget(tabs)

    def _update_window_title(self) -> None:
        version = f"v{self.runtime.document_version}" if self.runtime.document_version else "unversioned"
        sha8 = self.runtime.manifest_sha8
        self.setWindowTitle(f"Limelight - {self.runtime.project_title} ({version}, {sha8})")

    def _build_menus(self) -> None:
        self.file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open Package...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_package_from_dialog)
        self.file_menu.addAction(open_action)

        open_folder_action = QAction("Open Package &Folder...", self)
        open_folder_action.setShortcut("Ctrl+Shift+O")
        open_folder_action.triggered.connect(self._open_package_folder_from_dialog)
        self.file_menu.addAction(open_folder_action)

        self.recent_menu = self.file_menu.addMenu("&Recent")
        self.file_menu.addSeparator()

        reload_action = QAction("&Reload", self)
        reload_action.setShortcut("Ctrl+R")
        reload_action.triggered.connect(self._reload_package)
        self.file_menu.addAction(reload_action)

        self.file_menu.addSeparator()

        self.export_pdf_action = QAction("Export to &PDF...", self)
        self.export_pdf_action.setShortcut("Ctrl+E")
        self.export_pdf_action.triggered.connect(self._export_story_to_pdf)
        self.file_menu.addAction(self.export_pdf_action)

        self.file_menu.addSeparator()

        document_information_action = QAction("Document &Information...", self)
        document_information_action.triggered.connect(self._show_document_information)
        self.file_menu.addAction(document_information_action)

        self._build_view_menu()

        self.help_menu = QMenu("&Help", self)
        report_issue_action = QAction("&Report an Issue...", self)
        report_issue_action.setObjectName("report-issue")
        report_issue_action.triggered.connect(self._report_issue)
        self.help_menu.addAction(report_issue_action)

        self.help_menu.addSeparator()

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(about_action)

        self.help_button = QToolButton(self)
        self.help_button.setText("Help")
        self.help_button.setMenu(self.help_menu)
        self.help_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.help_button.setAutoRaise(True)
        # The menu-indicator arrow Qt adds after the text is, at menu-bar
        # size, a stray comma; the button reads as a menu without it.
        self.help_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")

        self.menuBar().setCornerWidget(self.help_button, Qt.Corner.TopRightCorner)

    def _open_package_from_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Limelight Package",
            str(self.runtime.package.path.parent),
            PACKAGE_FILE_FILTER,
        )
        if path:
            self._open_package_path(path)

    def _open_package_folder_from_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Open Limelight Package Folder",
            str(self.runtime.package.path.parent),
        )
        if path:
            self._open_package_path(path)

    def _reload_package(self) -> None:
        logger.info("Reloading Limelight package %s", self.runtime.package.path)
        self._open_package_path(self.runtime.package.path, error_title="Reload Limelight Package")

    def _open_package_path(self, path: str | Path, *, error_title: str = "Open Limelight Package") -> None:
        logger.info("%s: %s", error_title, path)
        try:
            package = open_limelight(path)
            manifest = package.manifest_json()
            validate_manifest_semantics(manifest)
        except (FileNotFoundError, LimelightError, OSError) as error:
            logger.exception("%s failed while reading %s", error_title, path)
            QMessageBox.critical(self, error_title, str(error))
            return

        old_runtime = self.runtime
        new_runtime: LimelightRuntime | None = None
        try:
            new_runtime = LimelightRuntime(package, manifest, debug_timing=self.debug_timing)
            self.runtime = new_runtime
            self._update_window_title()
            self._build_central_tabs()
        except Exception as error:
            self.runtime = old_runtime
            self._update_window_title()
            if new_runtime is not None:
                new_runtime.close()
            else:
                package.close()
            logger.exception("%s failed while rebuilding the UI for %s", error_title, path)
            QMessageBox.critical(self, error_title, str(error))
            return

        self._close_figure_view_windows()
        self._close_table_view_windows()
        self._add_recent_path(package.path)
        logger.info("%s completed for %s", error_title, package.path)

        if old_runtime.package is not package:
            old_runtime.close()

    @staticmethod
    def _load_recent_paths() -> list[str]:
        settings = QSettings()
        stored = settings.value("recentPackages", [])
        if isinstance(stored, str):
            stored = [stored]
        return [path for path in stored if Path(path).exists()]

    def _save_recent_paths(self) -> None:
        settings = QSettings()
        settings.setValue("recentPackages", self._recent_paths)
        settings.sync()

    def _add_recent_path(self, path: str | Path) -> None:
        resolved = str(Path(path).resolve())
        self._recent_paths = [
            recent_path
            for recent_path in self._recent_paths
            if recent_path != resolved
        ]
        self._recent_paths.insert(0, resolved)
        del self._recent_paths[10:]
        self._save_recent_paths()
        self._update_recent_menu()

    def _build_view_menu(self) -> None:
        """A View menu for the page the story is shown on.

        These change how the document is presented to this reader, not what it
        declares. The package says what page it was written for, and a reader
        who wants it wider says so here; nothing is written back, not least
        because a package can be signed.
        """

        view_menu = self.menuBar().addMenu("&View")

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcuts(
            _shortcuts(QKeySequence.StandardKey.ZoomIn, "Ctrl+=", "Ctrl++")
        )
        zoom_in_action.triggered.connect(lambda: self._zoom_story(1))
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcuts(
            _shortcuts(QKeySequence.StandardKey.ZoomOut, "Ctrl+-", "Ctrl+_")
        )
        zoom_out_action.triggered.connect(lambda: self._zoom_story(-1))
        view_menu.addAction(zoom_out_action)

        self.reset_zoom_action = QAction("&Actual Size", self)
        self.reset_zoom_action.setShortcut(QKeySequence("Ctrl+0"))
        self.reset_zoom_action.triggered.connect(lambda: self._zoom_story(0))
        view_menu.addAction(self.reset_zoom_action)

        view_menu.addSeparator()

        self._page_width_group = QActionGroup(self)
        self._page_width_group.setExclusive(True)

        self.page_width_a4_action = QAction("&A4 Width", self)
        self.page_width_a4_action.setCheckable(True)
        self.page_width_a4_action.triggered.connect(lambda: self._set_page_width(A4_WIDTH_MM))

        self.page_width_full_action = QAction("&Full Width", self)
        self.page_width_full_action.setCheckable(True)
        self.page_width_full_action.triggered.connect(lambda: self._set_page_width(None))

        self.page_width_custom_action = QAction("&Custom Width...", self)
        self.page_width_custom_action.setCheckable(True)
        self.page_width_custom_action.triggered.connect(self._ask_for_page_width)

        for action in (
            self.page_width_a4_action,
            self.page_width_full_action,
            self.page_width_custom_action,
        ):
            self._page_width_group.addAction(action)
            view_menu.addAction(action)

        view_menu.addSeparator()

        self.continuous_page_action = QAction("&Continuous Page", self)
        self.continuous_page_action.setCheckable(True)
        self.continuous_page_action.setStatusTip(
            "One page as long as the story, rather than broken into pages when exported"
        )
        self.continuous_page_action.triggered.connect(self._set_continuous_page)
        view_menu.addAction(self.continuous_page_action)

        view_menu.addSeparator()

        restore_action = QAction("&Restore Document Page", self)
        restore_action.triggered.connect(self._restore_declared_page)
        view_menu.addAction(restore_action)

        self._sync_view_menu()

    def _zoom_story(self, direction: int) -> None:
        if self.story_blocks is None:
            return
        self.story_blocks.zoom_by(direction)
        self._sync_view_menu()

    def _sync_view_menu(self) -> None:
        if self.story_blocks is not None:
            zoom = self.story_blocks.zoom
            self.reset_zoom_action.setText(
                "&Actual Size" if zoom == 1.0 else f"&Actual Size (now {zoom * 100:.0f}%)"
            )

        geometry = self.runtime.page_geometry
        width = geometry.width_mm
        self.page_width_full_action.setChecked(width is None)
        self.page_width_a4_action.setChecked(width == A4_WIDTH_MM)
        self.page_width_custom_action.setChecked(width is not None and width != A4_WIDTH_MM)
        if width is not None and width != A4_WIDTH_MM:
            self.page_width_custom_action.setText(f"&Custom Width... ({width:g}mm)")
        else:
            self.page_width_custom_action.setText("&Custom Width...")
        self.continuous_page_action.setChecked(geometry.height_mm is None)

    def _show_page_geometry(self, geometry: PageGeometry) -> None:
        self.runtime.page_geometry = geometry
        self._sync_view_menu()
        if self.story_blocks is not None:
            self.story_blocks.refresh_page_geometry()

    def _set_page_width(self, width_mm: float | None) -> None:
        current = self.runtime.page_geometry
        self._show_page_geometry(
            PageGeometry(
                width_mm=width_mm,
                height_mm=current.height_mm,
                margin_lr_mm=current.margin_lr_mm,
                margin_tb_mm=current.margin_tb_mm,
            )
        )

    def _ask_for_page_width(self) -> None:
        current = self.runtime.page_geometry
        starting = current.width_mm if current.width_mm is not None else A4_WIDTH_MM
        width_mm, accepted = QInputDialog.getDouble(
            self,
            "Custom Page Width",
            "Page width in millimetres, including its margins:",
            starting,
            40.0,
            2000.0,
            1,
        )
        if not accepted:
            # Leave the menu showing what is actually in force.
            self._sync_view_menu()
            return
        self._set_page_width(width_mm)

    def _set_continuous_page(self, continuous: bool) -> None:
        current = self.runtime.page_geometry
        declared = self.runtime.declared_page_geometry
        # Going back to paged uses the height the document declared, falling
        # back to A4 for a document that has only ever been continuous.
        paged_height = declared.height_mm if declared.height_mm is not None else A4_HEIGHT_MM
        self._show_page_geometry(
            PageGeometry(
                width_mm=current.width_mm,
                height_mm=None if continuous else paged_height,
                margin_lr_mm=current.margin_lr_mm,
                margin_tb_mm=current.margin_tb_mm,
            )
        )

    def _restore_declared_page(self) -> None:
        self._show_page_geometry(self.runtime.declared_page_geometry)

    def _update_recent_menu(self) -> None:
        self.recent_menu.clear()
        if not self._recent_paths:
            empty_action = QAction("No Recent Packages", self)
            empty_action.setEnabled(False)
            self.recent_menu.addAction(empty_action)
            return

        for path in self._recent_paths:
            action = QAction(path, self)
            action.triggered.connect(
                lambda checked=False, recent_path=path: self._open_package_path(recent_path)
            )
            self.recent_menu.addAction(action)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Limelight",
            f"Limelight\n\nCurrent project: {self.runtime.project_title}",
        )

    def _show_document_information(self) -> None:
        project = self.runtime.manifest["project"]
        authors = project.get("authors") or []
        lines = [
            f"Title: {project['title']}",
        ]
        subtitle = optional_field(project, "subtitle")
        if subtitle:
            lines.append(f"Subtitle: {subtitle}")
        description = optional_field(project, "description")
        if description:
            lines.append(f"Description: {description}")
        lines.append(f"Authors: {', '.join(authors) if authors else '(none)'}")
        lines.append(f"Created: {optional_field(project, 'created') or '(unknown)'}")
        lines.append(f"Updated: {optional_field(project, 'updated') or '(unknown)'}")
        document_version = optional_field(project, "documentVersion")
        lines.append(f"Document version: {f'v{document_version}' if document_version else '(unversioned)'}")
        lines.append(f"Limelight schema version: v{self.runtime.manifest['limelightVersion']}")
        lines.append(f"Manifest SHA: {self.runtime.manifest_sha8}")

        dialog = QDialog(self)
        dialog.setWindowTitle("Document Information")
        layout = QVBoxLayout(dialog)

        info_label = QLabel("\n".join(lines))
        layout.addWidget(info_label)

        sources = self.runtime.manifest.get("sources") or []
        provenance_rows = []
        for source in sources:
            provenance = source.get("provenance")
            if not provenance:
                continue
            provenance_rows.append(
                (
                    optional_field(source, "title") or source["id"],
                    optional_field(provenance, "origin") or "",
                    optional_field(provenance, "releaseDate") or "",
                    optional_field(provenance, "url") or "",
                )
            )

        if provenance_rows:
            sources_label = QLabel("Data sources")
            sources_label.setStyleSheet("font-weight: 600;")
            layout.addWidget(sources_label)

            table = QTableWidget(len(provenance_rows), 4)
            table.setHorizontalHeaderLabels(["Source", "Origin", "Release date", "URL"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            for row_index, (title, origin, release_date, url) in enumerate(provenance_rows):
                table.setItem(row_index, 0, QTableWidgetItem(title))
                table.setItem(row_index, 1, QTableWidgetItem(origin))
                table.setItem(row_index, 2, QTableWidgetItem(release_date))
                if url:
                    link_label = QLabel(f'<a href="{html.escape(url)}">{html.escape(url)}</a>')
                    link_label.setOpenExternalLinks(True)
                    table.setCellWidget(row_index, 3, link_label)
                else:
                    table.setItem(row_index, 3, QTableWidgetItem(""))
            table.resizeColumnsToContents()
            layout.addWidget(table, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.resize(560, 360 if provenance_rows else 200)
        dialog.exec()

    def _export_story_to_pdf(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export Story to PDF",
            str(default_pdf_path(self.runtime.package.path)),
            "PDF documents (*.pdf)",
        )
        if not selected:
            return

        destination = Path(selected)
        if destination.suffix.lower() != ".pdf":
            destination = destination.with_suffix(".pdf")

        renderers = story_pdf_renderers(self.runtime)

        progress = QProgressDialog("Rendering story...", "Cancel", 0, 0, self)
        progress.setWindowTitle("Export to PDF")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        def report_figure_progress(number: int, total: int) -> bool:
            if progress.wasCanceled():
                return False
            # One extra step covers laying the document out and writing the file.
            progress.setMaximum(total + 1)
            progress.setLabelText(f"Rendering figure {number} of {total}...")
            progress.setValue(number - 1)
            QApplication.processEvents()
            return True

        start_time = self.runtime.timing.start()
        try:
            document_html = build_story_pdf_html(
                self.runtime,
                renderers,
                on_figure_progress=report_figure_progress,
            )
            progress.setLabelText("Writing PDF...")
            progress.setValue(max(progress.maximum() - 1, 0))
            QApplication.processEvents()
            write_html_to_pdf(document_html, destination, printed_page(self.runtime))
        except PdfExportError as error:
            progress.close()
            if not progress.wasCanceled():
                QMessageBox.warning(self, "Export to PDF", str(error))
            return
        except (LimelightError, OSError) as error:
            progress.close()
            logger.exception("Could not export the story to PDF")
            QMessageBox.critical(
                self,
                "Export to PDF",
                f"Could not export the story to PDF:\n{error}",
            )
            return

        progress.close()
        self.runtime.timing.log(
            "story.pdf.export",
            start_time,
            [("path", str(destination))],
        )
        self.statusBar().showMessage(f"Exported story to {destination}", 8000)

    def _report_issue(self) -> None:
        comment, accepted = QInputDialog.getMultiLineText(
            self,
            "Report Issue",
            "Comment",
        )
        if not accepted:
            return

        QApplication.processEvents()
        try:
            report_path = self._write_issue_report(comment)
        except OSError as error:
            logger.exception("Could not save issue report")
            QMessageBox.critical(self, "Report Issue", f"Could not save issue report:\n{error}")
            return

        logger.info("Issue report saved to %s", report_path)
        QMessageBox.information(self, "Report Issue", f"Saved local issue report:\n{report_path}")

    def _write_issue_report(self, comment: str) -> Path:
        captured_at = datetime.now().astimezone()
        timestamp = captured_at.isoformat(timespec="seconds")
        filename_timestamp = captured_at.strftime("%Y%m%dT%H%M%S%f%z")
        report_dir = log_file_path().parent / "issue-reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        report_path = report_dir / f"issue-report-{filename_timestamp}.json"
        report = {
            "captured_at": timestamp,
            "comment": comment,
            "project_title": self.runtime.project_title,
            "package_path": str(self.runtime.package.path),
            "active_tab": self._active_tab_name(),
            "window": {
                "title": self.windowTitle(),
                "width": self.width(),
                "height": self.height(),
            },
            "screenshot": {
                "media_type": "image/png",
                "encoding": "base64",
                "data": _pixmap_png_base64(self.grab()),
            },
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report_path

    def _active_tab_name(self) -> str | None:
        if self.tabs is None:
            return None
        index = self.tabs.currentIndex()
        return self.tabs.tabText(index) if index >= 0 else None

    def _close_figure_view_windows(self) -> None:
        for window in list(self.figure_view_windows.values()):
            window.close()
        self.figure_view_windows.clear()

    def _close_table_view_windows(self) -> None:
        for window in list(self.table_view_windows.values()):
            window.close()
        self.table_view_windows.clear()

    def _build_story_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)

        self.story_tree = QTreeWidget()
        self.story_tree.setHeaderHidden(True)
        self.story_tree.itemSelectionChanged.connect(self._on_story_selected)
        splitter.addWidget(self.story_tree)

        content = QWidget()
        layout = QVBoxLayout(content)
        self.story_title = QLabel()
        self.story_title.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.story_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.story_title)

        self.story_blocks = StoryBlockPanel(
            self.runtime,
            on_parameter_changed=self._on_parameter_changed,
            on_popout_requested=self._popout_story_figure,
        )
        # The wheel and the keyboard zoom the story directly, so the menu hears
        # about it from the story rather than from whatever asked for it.
        self.story_blocks.zoomChanged.connect(lambda _: self._sync_view_menu())
        layout.addWidget(self.story_blocks, stretch=1)
        splitter.addWidget(content)
        splitter.setStretchFactor(1, 1)

        self._populate_story_tree()
        return splitter

    def _build_figures_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)

        self.figure_view_tree = QTreeWidget()
        self.figure_view_tree.setHeaderLabels(["Figures"])
        self.figure_view_tree.setRootIsDecorated(True)
        self.figure_view_tree.itemSelectionChanged.connect(self._on_figure_selected)
        self.figure_view_tree.itemDoubleClicked.connect(self._on_figure_double_clicked)
        splitter.addWidget(self.figure_view_tree)

        content = QWidget()
        layout = QVBoxLayout(content)
        self.figure_view_title = QLabel()
        self.figure_view_title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self.figure_view_title)

        self.figure_view_panel = InteractiveFigureViewPanel(self.runtime, on_parameter_changed=self._on_parameter_changed)
        layout.addWidget(self.figure_view_panel, stretch=1)

        self.figure_view_caption = QLabel()
        self.figure_view_caption.setWordWrap(True)
        layout.addWidget(self.figure_view_caption)
        splitter.addWidget(content)
        splitter.setStretchFactor(1, 1)

        self._populate_figure_view_tree()
        return splitter

    def _build_data_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        tree = QTreeWidget()
        tree.setHeaderLabels(["Name", "Kind", "Path/Unit", "Rows"])
        tree.itemDoubleClicked.connect(self._on_data_double_clicked)
        layout.addWidget(tree)

        for source in self.runtime.manifest["sources"]:
            if "yArrays" in source:
                source_item = QTreeWidgetItem(
                    [
                        optional_field(source, "title") or source["id"],
                        "hdf source",
                        source["path"],
                        "",
                    ]
                )
                source_item.setData(0, Qt.UserRole, ("table", source["id"]))
                tree.addTopLevelItem(source_item)
                for y_array in source["yArrays"]:
                    array_schema = y_array["schema"]
                    array_item = QTreeWidgetItem(
                        [
                            optional_field(array_schema, "label") or array_schema["name"],
                            array_schema["dtype"],
                            optional_field(array_schema, "unit") or "",
                            "",
                        ]
                    )
                    array_item.setData(0, Qt.UserRole, ("array", source["id"], array_schema["name"]))
                    source_item.addChild(array_item)
                source_item.setExpanded(True)
                continue

            source_item = QTreeWidgetItem(
                [
                    optional_field(source, "title") or source["id"],
                    "source",
                    source["path"],
                    "",
                ]
            )
            source_item.setData(0, Qt.UserRole, ("table", source["id"]))
            tree.addTopLevelItem(source_item)
            for array in source.get("schema") or []:
                array_item = QTreeWidgetItem(
                    [
                        optional_field(array, "label") or array["name"],
                        array["dtype"],
                        optional_field(array, "unit") or "",
                        "",
                    ]
                )
                array_item.setData(0, Qt.UserRole, ("array", source["id"], array["name"]))
                source_item.addChild(array_item)
            source_item.setExpanded(True)

        tree.resizeColumnToContents(0)
        return panel

    def _build_control_parameters_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        form = QFormLayout()
        layout.addLayout(form)
        layout.addStretch(1)

        for parameter_id, parameter in self.runtime.control_parameters.items():
            kind = control_parameter_data_type_kind(parameter)
            data_type_payload = control_parameter_data_type_payload(parameter)
            current_value = self.runtime.control_parameter_value(parameter_id)
            label_text = parameter.get("label") or parameter_id

            if kind == "discrete":
                combo = QComboBox()
                for option in data_type_payload["options"]:
                    combo.addItem(option["label"] or option["value"], option["value"])
                current_index = combo.findData(current_value)
                if current_index >= 0:
                    blocker = QSignalBlocker(combo)
                    combo.setCurrentIndex(current_index)
                    del blocker
                combo.currentIndexChanged.connect(
                    lambda index, control_combo=combo, selected_parameter=parameter_id: (
                        self._control_parameter_widget_changed(selected_parameter, control_combo.itemData(index))
                    )
                )
                form.addRow(label_text, combo)
                continue

            if kind == "integer":
                _, minimum, maximum = control_parameter_default_min_max(data_type_payload, kind)
                spin_box = QSpinBox()
                spin_box.setRange(
                    minimum if minimum is not None else -2_147_483_648,
                    maximum if maximum is not None else 2_147_483_647,
                )
                blocker = QSignalBlocker(spin_box)
                spin_box.setValue(int(current_value))
                del blocker
                spin_box.valueChanged.connect(
                    lambda value, selected_parameter=parameter_id: (
                        self._control_parameter_widget_changed(selected_parameter, value)
                    )
                )
                form.addRow(label_text, spin_box)
                continue

            if kind == "float":
                _, minimum, maximum = control_parameter_default_min_max(data_type_payload, kind)
                double_spin_box = QDoubleSpinBox()
                double_spin_box.setDecimals(6)
                double_spin_box.setRange(
                    _to_float(minimum) if minimum is not None else -1e18,
                    _to_float(maximum) if maximum is not None else 1e18,
                )
                blocker = QSignalBlocker(double_spin_box)
                double_spin_box.setValue(_to_float(current_value) or 0.0)
                del blocker
                double_spin_box.valueChanged.connect(
                    lambda value, selected_parameter=parameter_id: (
                        self._control_parameter_widget_changed(selected_parameter, value)
                    )
                )
                form.addRow(label_text, double_spin_box)
                continue

            if kind == "time":
                line_edit = QLineEdit(str(current_value) if current_value is not None else "")
                line_edit.editingFinished.connect(
                    lambda control_line_edit=line_edit, selected_parameter=parameter_id: (
                        self._control_parameter_widget_changed(selected_parameter, control_line_edit.text())
                    )
                )
                form.addRow(label_text, line_edit)
                continue

        return panel

    def _control_parameter_widget_changed(self, parameter_id: str, value: Any) -> None:
        if value is None:
            return
        try:
            changed = self.runtime.set_control_parameter_value(parameter_id, value)
        except (KeyError, ValueError, TypeError) as error:
            logger.exception("Invalid control parameter value for %s", parameter_id)
            QMessageBox.warning(self, "Invalid Control Value", str(error))
            return
        if not changed:
            return
        logger.info("Parameter %s set to %s", parameter_id, value)
        self._on_parameter_changed()

    def _on_data_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        entry = item.data(0, Qt.UserRole)
        if not entry:
            return
        if entry[0] == "table":
            self._open_or_focus_table(entry[1], None)
        elif entry[0] == "array":
            self._open_or_focus_table(entry[1], entry[2])

    def _populate_story_tree(self) -> None:
        if self.story_tree is None:
            return

        story_tree = self.story_tree
        story_tree.clear()
        items: dict[str, QTreeWidgetItem] = {}
        stack: list[tuple[int, QTreeWidgetItem]] = []

        for section in self.runtime.story_sections:
            item = QTreeWidgetItem([section.title])
            item.setData(0, Qt.UserRole, section.id)
            items[section.id] = item
            while stack and stack[-1][0] >= section.level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                story_tree.addTopLevelItem(item)
            stack.append((section.level, item))
            item.setExpanded(True)

        if self.runtime.story_sections:
            first_section = self.runtime.story_sections[0]
            blocker = QSignalBlocker(story_tree)
            story_tree.setCurrentItem(items[first_section.id])
            del blocker
        self._show_story_document()

    def _populate_figure_view_tree(self) -> None:
        """List the figure specs, each expanding to the views that render it.

        A spec is the reusable definition and carries no number; the views
        under it carry the numbers a reader sees, from the one story sequence
        that also numbers the images. Showing the relationship is the point:
        one plot appearing three times in a story is three numbered figures.
        """

        if self.figure_view_tree is None:
            return

        figure_view_tree = self.figure_view_tree
        self._figure_spec_order = self.runtime.figure_spec_order
        first_selectable: QTreeWidgetItem | None = None

        for figure_spec_id in self._figure_spec_order:
            spec_item = QTreeWidgetItem([self.runtime.figure_view_heading(figure_spec_id)])
            spec_item.setData(0, Qt.UserRole, figure_spec_id)
            figure_view_tree.addTopLevelItem(spec_item)
            if first_selectable is None:
                first_selectable = spec_item

            for figure_view in self.runtime.figure_views_for_spec(figure_spec_id):
                number = self.runtime.figure_view_number(figure_view)
                # An unnumbered view is one the story never shows, so its id is
                # the only thing that tells it from its siblings.
                label = f"Figure {number}" if number is not None else figure_view["id"]
                view_item = QTreeWidgetItem([label])
                view_item.setData(0, Qt.UserRole, figure_spec_id)
                view_item.setData(0, FIGURE_VIEW_ID_ROLE, figure_view["id"])
                spec_item.addChild(view_item)

        figure_view_tree.expandAll()
        figure_view_tree.resizeColumnToContents(0)

        # Open on a numbered view where there is one, so the first thing shown
        # is a figure as the story presents it.
        if first_selectable is not None:
            if first_selectable.childCount() > 0:
                first_selectable = first_selectable.child(0)
            blocker = QSignalBlocker(figure_view_tree)
            figure_view_tree.setCurrentItem(first_selectable)
            del blocker
            self._show_selected_figure(first_selectable)

    def _on_story_selected(self) -> None:
        if self.story_tree is None:
            return
        items = self.story_tree.selectedItems()
        if items:
            section_id = items[0].data(0, Qt.UserRole)
            if section_id and self.story_blocks is not None:
                self.story_blocks.scroll_to_section(section_id)

    def _figure_target(
        self, item: QTreeWidgetItem
    ) -> tuple[str | None, int | None, tuple[dict[str, Any], ...]]:
        """What a tree row stands for: a bare spec, or one view of it."""

        figure_spec_id = item.data(0, Qt.UserRole)
        figure_view_id = item.data(0, FIGURE_VIEW_ID_ROLE)
        if not figure_spec_id or figure_view_id is None:
            return figure_spec_id, None, ()

        figure_view = self.runtime.figure_views.get(figure_view_id)
        if figure_view is None:
            return figure_spec_id, None, ()
        return (
            figure_spec_id,
            self.runtime.figure_view_number(figure_view),
            tuple(figure_view["actions"]),
        )

    def _show_selected_figure(self, item: QTreeWidgetItem) -> None:
        figure_spec_id, figure_view_index, figure_view_actions = self._figure_target(item)
        if figure_spec_id:
            self._show_figure(
                figure_spec_id,
                figure_view_index=figure_view_index,
                figure_view_actions=figure_view_actions,
            )

    def _on_figure_selected(self) -> None:
        if self.figure_view_tree is None:
            return
        items = self.figure_view_tree.selectedItems()
        if not items:
            return
        self._show_selected_figure(items[0])

    def _on_figure_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        figure_spec_id, figure_view_index, figure_view_actions = self._figure_target(item)
        if figure_spec_id:
            self._open_or_focus_figure(
                figure_spec_id,
                figure_view_index=figure_view_index,
                figure_view_actions=figure_view_actions,
            )

    def _popout_story_figure(
        self,
        figure_id: str,
        figure_view_index: int | None,
        figure_view_actions: Sequence[dict[str, Any]],
    ) -> None:
        self._open_or_focus_figure(
            figure_id,
            figure_view_index=figure_view_index,
            figure_view_actions=figure_view_actions,
        )

    def _show_story_document(self) -> None:
        if self.story_title is None or self.story_blocks is None:
            return

        start_time = self.runtime.timing.start()
        self._begin_wait_state("Rendering story...")
        try:
            self.story_title.setText(self.runtime.project_title)
            blocks = self.runtime.story_blocks
            self.story_blocks.show_blocks(blocks, self.runtime.story_sections)
            self._apply_story_state_to_figures_tab()
        finally:
            self._end_wait_state()
            self.runtime.timing.log(
                "story.document.render",
                start_time,
                [
                    ("path", self.runtime.story_path),
                    ("blocks", len(blocks)),
                ],
            )

    def _show_figure(
        self,
        figure_id: str,
        *,
        figure_view_index: int | None = None,
        figure_view_actions: Sequence[dict[str, Any]] = (),
    ) -> None:
        if self.figure_view_title is None or self.figure_view_caption is None or self.figure_view_panel is None:
            return

        figure_spec = self.runtime.figure_specs[figure_id]
        self.figure_view_title.setText(
            self.runtime.figure_view_heading(figure_id, index=figure_view_index)
        )
        self.figure_view_caption.setText(figure_spec.get("caption") or "")
        self.figure_view_panel.show_figure(
            figure_id,
            figure_view_index=figure_view_index,
            figure_view_actions=figure_view_actions,
        )

    def _begin_wait_state(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if not self._busy_cursor_active:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._busy_cursor_active = True
        QApplication.processEvents()

    def _end_wait_state(self) -> None:
        if self._busy_cursor_active:
            QApplication.restoreOverrideCursor()
            self._busy_cursor_active = False
        self.statusBar().clearMessage()

    def _open_or_focus_figure(
        self,
        figure_id: str,
        *,
        figure_view_index: int | None = None,
        figure_view_actions: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        resolved_index = figure_view_index
        figure_actions = tuple(
            () if figure_view_actions is None else figure_view_actions
        )
        window_key = (figure_id, resolved_index)
        window = self.figure_view_windows.get(window_key)
        if window is None:
            window = FigureViewWindow(
                self.runtime,
                figure_id,
                figure_view_index=resolved_index,
                figure_view_actions=figure_actions,
                on_closed=lambda closed_id, closed_index: self.figure_view_windows.pop((closed_id, closed_index), None),
                on_parameter_changed=self._on_parameter_changed,
            )
            window.resize(900, 650)
            self.figure_view_windows[window_key] = window

        window.show()
        window.apply_story_state(
            figure_view_index=resolved_index,
            figure_view_actions=figure_actions,
        )
        window.raise_()
        window.activateWindow()

    def _open_or_focus_table(self, source_id: str, column: str | None) -> None:
        window_key = (source_id, column)
        window = self.table_view_windows.get(window_key)
        if window is None:
            window = TableViewWindow(
                self.runtime,
                source_id,
                column,
                on_closed=lambda closed_id, closed_column: self.table_view_windows.pop(
                    (closed_id, closed_column), None
                ),
            )
            window.resize(700, 500)
            self.table_view_windows[window_key] = window

        window.show()
        window.raise_()
        window.activateWindow()

    def _redraw_open_figure_view_windows(self) -> None:
        for window in list(self.figure_view_windows.values()):
            window.redraw()

    def _on_parameter_changed(self) -> None:
        if self.story_blocks is not None:
            self.story_blocks.redraw_figures()
        if self.figure_view_panel is not None:
            self.figure_view_panel.redraw()
        self._redraw_open_figure_view_windows()

    def _apply_story_state_to_figures_tab(self) -> None:
        if self.figure_view_tree is None or self.figure_view_panel is None:
            return

        items = self.figure_view_tree.selectedItems()
        if not items:
            return

        self._show_selected_figure(items[0])

    def closeEvent(self, event: Any) -> None:
        if self.story_blocks is not None:
            self.story_blocks.dispose()
            self.story_blocks = None
        self._close_figure_view_windows()
        self._close_table_view_windows()
        self.runtime.close()
        super().closeEvent(event)


_QT_ALIGNMENT = {
    "Left": Qt.AlignLeft | Qt.AlignVCenter,
    "Center": Qt.AlignCenter,
    "Right": Qt.AlignRight | Qt.AlignVCenter,
}


def _qt_font(styles: set[str]) -> QFont:
    font = QFont()
    if "Bold" in styles:
        font.setBold(True)
    if "Italic" in styles:
        font.setItalic(True)
    return font


def _populate_table_widget(
    table: QTableWidget, preview: TablePreview, table_view_spec: dict[str, Any] | None = None
) -> None:
    table.clear()
    table.setRowCount(len(preview.rows))
    table.setColumnCount(len(preview.columns))
    table.setHorizontalHeaderLabels(preview.columns)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)

    header_styles = table_view_header_styles(table_view_spec)
    if header_styles:
        header_font = _qt_font(header_styles)
        for column_index in range(len(preview.columns)):
            header_item = table.horizontalHeaderItem(column_index)
            if header_item is not None:
                header_item.setFont(header_font)

    for row_index, row in enumerate(preview.rows):
        for column_index, value in enumerate(row):
            item = QTableWidgetItem(value)
            alignment = table_view_column_alignment(table_view_spec, preview, column_index)
            item.setTextAlignment(_QT_ALIGNMENT[alignment])
            cell_styles = table_view_cell_styles(table_view_spec, row_index, column_index)
            if cell_styles:
                item.setFont(_qt_font(cell_styles))
            table.setItem(row_index, column_index, item)


def _is_table_view_figure(figure_spec: dict[str, Any]) -> bool:
    return bool(figure_spec["tableViewSpecs"])


class TableViewWindow(QMainWindow):
    def __init__(
        self,
        runtime: LimelightRuntime,
        source_id: str,
        column: str | None,
        *,
        on_closed: Callable[[str, str | None], None],
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.source_id = source_id
        self.column = column
        self._on_closed = on_closed
        self.setWindowIcon(_application_icon())

        preview = runtime.table_preview(source_id, column, limit=2000)
        self.setWindowTitle(f"{preview.title} - Limelight")

        content = QWidget()
        layout = QVBoxLayout(content)

        heading = QLabel(preview.title)
        heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(heading)

        if preview.truncated:
            status = QLabel(f"Showing {len(preview.rows):,} of {preview.total_rows:,} rows")
            layout.addWidget(status)

        table = QTableWidget()
        _populate_table_widget(table, preview)
        layout.addWidget(table, stretch=1)

        save_button = QPushButton("Save to CSV")
        save_button.clicked.connect(self._on_save_csv)
        layout.addWidget(save_button)

        self.setCentralWidget(content)

    def _on_save_csv(self) -> None:
        default_name = self.column or self.source_id
        path, _ = QFileDialog.getSaveFileName(self, "Save to CSV", f"{default_name}.csv", "CSV Files (*.csv)")
        if not path:
            return
        estimated_bytes = self.runtime.estimate_table_csv_size_bytes(self.source_id, self.column)
        if estimated_bytes > 100 * 1024 * 1024:
            estimated_mb = estimated_bytes / (1024 * 1024)
            answer = QMessageBox.question(
                self,
                "Save to CSV",
                f"This export is approximately {estimated_mb:.0f} MB. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        try:
            self.runtime.export_table_csv(self.source_id, self.column, path)
        except (OSError, LimelightError) as error:
            logger.exception("Save to CSV failed for %s", path)
            QMessageBox.critical(self, "Save to CSV", str(error))
            return
        self.statusBar().showMessage(f"Saved to {path}", 4000)

    def closeEvent(self, event: Any) -> None:
        self._on_closed(self.source_id, self.column)
        super().closeEvent(event)


class FigureViewWindow(QMainWindow):
    def __init__(
        self,
        runtime: LimelightRuntime,
        figure_id: str,
        *,
        figure_view_index: int | None = None,
        figure_view_actions: Sequence[dict[str, Any]] = (),
        on_closed: Callable[[str, int | None], None],
        on_parameter_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.figure_id = figure_id
        self.figure_view_index = figure_view_index
        self._on_closed = on_closed
        self.setWindowIcon(_application_icon())

        figure_spec = self.runtime.figure_specs[figure_id]
        title = self.runtime.figure_view_heading(figure_id, index=figure_view_index)
        self.setWindowTitle(f"{title} - Limelight")

        content = QWidget()
        layout = QVBoxLayout(content)

        self.heading = QLabel(title)
        self.heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self.heading)

        self.figure_view_panel = InteractiveFigureViewPanel(runtime, on_parameter_changed=on_parameter_changed)
        layout.addWidget(self.figure_view_panel, stretch=1)

        caption = QLabel(figure_spec.get("caption") or "")
        caption.setWordWrap(True)
        layout.addWidget(caption)

        self.setCentralWidget(content)
        self.figure_view_panel.show_figure(
            figure_id,
            figure_view_index=figure_view_index,
            figure_view_actions=figure_view_actions,
        )

    def apply_story_state(
        self,
        *,
        figure_view_index: int | None = None,
        figure_view_actions: Sequence[dict[str, Any]],
    ) -> None:
        self.figure_view_index = figure_view_index
        title = self.runtime.figure_view_heading(self.figure_id, index=figure_view_index)
        self.setWindowTitle(f"{title} - Limelight")
        self.heading.setText(title)
        self.figure_view_panel.show_figure(
            self.figure_id,
            figure_view_index=figure_view_index,
            figure_view_actions=figure_view_actions,
        )

    def closeEvent(self, event: Any) -> None:
        self.figure_view_panel.dispose()
        self._on_closed(self.figure_id, self.figure_view_index)
        super().closeEvent(event)

    def redraw(self) -> None:
        self.figure_view_panel.redraw()


class StoryFigureViewPanel(QWidget):
    def __init__(
        self,
        runtime: LimelightRuntime,
        *,
        image_cache: dict[StaticFigureViewKey, QPixmap],
        worker_pool: QThreadPool,
        on_parameter_changed: Callable[[], None] | None = None,
        on_popout_requested: Callable[
            [str, int | None, Sequence[dict[str, Any]]],
            None,
        ]
        | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.image_cache = image_cache
        self.worker_pool = worker_pool
        self.on_parameter_changed = on_parameter_changed
        self.on_popout_requested = on_popout_requested
        self.figure_id: str | None = None
        self.figure_view_index: int | None = None
        self.figure_view_actions: tuple[dict[str, Any], ...] = ()
        self.mode = "static"
        self._active = True
        self._render_request_id = 0
        self._pending_key: StaticFigureViewKey | None = None
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._render_static_image)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        layout = self._layout
        self._zoom = 1.0
        # A table has no drawn-in title the way a plot does, so it gets one
        # above it; the caption below is where every figure's number goes.
        self.heading = QLabel()
        self.heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.heading.setWordWrap(True)
        self.heading.hide()
        layout.addWidget(self.heading)
        self.image_label = QLabel("Rendering figure...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(0, 280)
        self.image_label.setMinimumHeight(280)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.image_label.setStyleSheet("color: #5f6368; background: #ffffff;")
        layout.addWidget(self.image_label)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.hide()
        layout.addWidget(self.table)
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.TextFormat.RichText)
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet("color: #3c4043; background: #ffffff;")
        self.caption.hide()
        layout.addWidget(self.caption)
        self._apply_caption_zoom()
        self.plot_panel: InteractiveFigureViewPanel | None = None

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._show_context_menu_at_label)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu_at_table)

    def set_figure(
        self,
        figure_id: str,
        *,
        figure_view_index: int | None = None,
        figure_view_actions: Sequence[dict[str, Any]],
    ) -> None:
        self.figure_id = figure_id
        self.figure_view_index = figure_view_index
        self.figure_view_actions = tuple(figure_view_actions)
        figure_spec = self.runtime.figure_specs.get(figure_id)
        caption = (
            self.runtime.figure_view_caption_markup(figure_id, index=figure_view_index)
            if figure_spec is not None
            else ""
        )
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        if figure_spec is not None and _is_table_view_figure(figure_spec):
            self.heading.setText(figure_spec["title"])
            self.heading.show()
            self._show_table(figure_spec)
            return
        self.heading.hide()
        self._show_static()
        self._schedule_static_render(0)

    def set_zoom(self, factor: float) -> None:
        """Show the figure at the zoom the story is shown at.

        The plot is redrawn at the zoomed resolution; a page whose width
        does not zoom gets no resize to prompt that, so it is asked for here.
        """

        if factor == self._zoom:
            return
        self._zoom = factor
        self._apply_caption_zoom()
        if self.mode == "static" and self.figure_id is not None:
            self._schedule_static_render(200)

    def _apply_caption_zoom(self) -> None:
        # The stylesheet's caption: 0.86 of the 14px story text, 0.4em above.
        # The plot itself is rendered to the zoomed column, so it follows on
        # its own.
        caption_font = self.caption.font()
        caption_font.setPixelSize(max(1, round(12 * self._zoom)))
        self.caption.setFont(caption_font)
        self.caption.setContentsMargins(0, round(5 * self._zoom), 0, 0)
        heading_font = self.heading.font()
        heading_font.setPixelSize(max(1, round(13 * self._zoom)))
        heading_font.setBold(True)
        self.heading.setFont(heading_font)
        self.heading.setContentsMargins(0, 0, 0, round(5 * self._zoom))

    def set_gap_padding(self, pixels: int) -> None:
        """Pad the figure above and below, beyond the gap the story gives every block."""

        if self._layout.contentsMargins().top() != pixels:
            self._layout.setContentsMargins(0, pixels, 0, pixels)

    def redraw(self) -> None:
        if self.mode == "interactive":
            if self.plot_panel is not None:
                self.plot_panel.redraw()
            return

        if self.mode == "table":
            figure_spec = self.runtime.figure_specs.get(self.figure_id) if self.figure_id is not None else None
            if figure_spec is not None:
                self._show_table(figure_spec)
            return

        self._schedule_static_render(0)

    def dispose(self) -> None:
        self._active = False
        self._render_request_id += 1
        self.render_timer.stop()
        if self.plot_panel is not None:
            self.plot_panel.dispose()
            self.plot_panel.deleteLater()
            self.plot_panel = None

    def contextMenuEvent(self, event: Any) -> None:
        self._show_context_menu(event.globalPos())

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.mode == "static" and self.figure_id is not None:
            self._fit_placeholder()
            self._schedule_static_render(200)

    def _static_size(self) -> tuple[int, int]:
        """The pixels a static render of this figure fills, at the current width."""

        figure_spec = self.runtime.figure_specs.get(self.figure_id) if self.figure_id is not None else None
        width = max(320, self.width())
        return width, max(260, int(width * figure_aspect(figure_spec)))

    def _fit_placeholder(self) -> None:
        """Take the size the next render will have, showing the last one scaled.

        The story is then laid out once, now, and the figure sharpens when
        the render lands, rather than the column shifting again under the
        reader when it does.
        """

        width, height = self._static_size()
        current = self.image_label.pixmap()
        if current is not None and not current.isNull() and current.width() != width:
            self.image_label.setPixmap(
                current.scaled(
                    width,
                    height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.image_label.setFixedHeight(height)

    def _show_context_menu_at_label(self, position: Any) -> None:
        self._show_context_menu(self.image_label.mapToGlobal(position))

    def _show_context_menu(self, global_position: Any) -> None:
        if self.figure_id is None:
            return

        menu = QMenu(self)
        popout_action = QAction("Popout", self)
        popout_action.triggered.connect(self._popout)
        menu.addAction(popout_action)
        if self.mode == "interactive":
            unexplore_action = QAction("Unexplore", self)
            unexplore_action.triggered.connect(self._show_static)
            menu.addAction(unexplore_action)
        elif self.mode != "table":
            explore_action = QAction("Explore", self)
            explore_action.triggered.connect(self._explore_inline)
            menu.addAction(explore_action)
        menu.exec(global_position)

    def _show_context_menu_at_table(self, position: Any) -> None:
        self._show_context_menu(self.table.mapToGlobal(position))

    def _popout(self) -> None:
        if self.figure_id is None or self.on_popout_requested is None:
            return
        self.on_popout_requested(
            self.figure_id,
            self.figure_view_index,
            self.figure_view_actions,
        )

    def _explore_inline(self) -> None:
        if self.figure_id is None:
            return
        if self.plot_panel is None:
            self.plot_panel = InteractiveFigureViewPanel(self.runtime, on_parameter_changed=self.on_parameter_changed)
            # The page is white whatever the desktop theme, and matplotlib
            # colours its toolbar icons for the background its palette
            # claims: on a dark theme that is white icons, invisible here. A
            # toolbar's background role is Button, so that is the colour the
            # check reads; Window is what the rest of the panel shows.
            palette = self.plot_panel.toolbar.palette()
            for role in (QPalette.ColorRole.Button, QPalette.ColorRole.Window, QPalette.ColorRole.Base):
                palette.setColor(role, QColor("#ffffff"))
            for role in (QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
                palette.setColor(role, QColor("#202124"))
            self.plot_panel.toolbar.setPalette(palette)
            self.plot_panel.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.plot_panel.customContextMenuRequested.connect(self._show_context_menu_at_plot_panel)
            self.plot_panel.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.plot_panel.canvas.customContextMenuRequested.connect(self._show_context_menu_at_canvas)
            # Above the caption, which stays the last thing in the figure.
            self._layout.insertWidget(self._layout.indexOf(self.caption), self.plot_panel)
        self.mode = "interactive"
        self.render_timer.stop()
        self.image_label.hide()
        self.plot_panel.show()
        self.plot_panel.show_figure(
            self.figure_id,
            figure_view_index=self.figure_view_index,
            figure_view_actions=self.figure_view_actions,
        )

    def _show_context_menu_at_plot_panel(self, position: Any) -> None:
        if self.plot_panel is not None:
            self._show_context_menu(self.plot_panel.mapToGlobal(position))

    def _show_context_menu_at_canvas(self, position: Any) -> None:
        if self.plot_panel is not None:
            self._show_context_menu(self.plot_panel.canvas.mapToGlobal(position))

    def _show_static(self) -> None:
        self.mode = "static"
        if self.plot_panel is not None:
            self.plot_panel.hide()
        self.table.hide()
        self.image_label.show()

    def _show_table(self, figure_spec: dict[str, Any]) -> None:
        self.mode = "table"
        self.render_timer.stop()
        if self.plot_panel is not None:
            self.plot_panel.hide()
        self.image_label.hide()
        table_view_spec = figure_spec["tableViewSpecs"][0]
        preview = self.runtime.table_preview(
            table_view_spec["data"],
            table_view_spec.get("columns"),
            column_formats=table_view_column_formats(table_view_spec),
        )
        _populate_table_widget(self.table, preview, table_view_spec)
        self.table.show()

    def _schedule_static_render(self, delay_ms: int) -> None:
        if not self._active:
            return
        self.render_timer.start(delay_ms)

    def _render_static_image(self) -> None:
        if not self._active or self.figure_id is None or self.mode != "static":
            return

        width, height = self._static_size()
        layout_dpi = CSS_PIXELS_PER_INCH * self._zoom
        parameter_signature = _parameter_signature(self.runtime)
        key = _static_figure_view_key(
            self.figure_id,
            figure_view_index=self.figure_view_index,
            figure_view_actions=self.figure_view_actions,
            parameter_signature=parameter_signature,
            width=width,
            height=height,
            layout_dpi=layout_dpi,
        )
        pixmap = self.image_cache.get(key)
        if pixmap is not None:
            self.runtime.timing.log(
                "story.static_figure_view.cache_hit",
                self.runtime.timing.start(),
                [("figure", self.figure_id)],
            )
            self._apply_static_pixmap(pixmap)
            return

        cache_start_time = self.runtime.timing.start()
        self._render_request_id += 1
        self._pending_key = key
        current_pixmap = self.image_label.pixmap()
        if current_pixmap is None or current_pixmap.isNull():
            self.image_label.setText("Rendering figure...")
        task = StaticFigureViewRenderTask(
            request_id=self._render_request_id,
            key=key,
            runtime=self.runtime,
            figure_id=self.figure_id,
            figure_view_index=self.figure_view_index,
            figure_view_actions=self.figure_view_actions,
            parameter_values=dict(self.runtime.control_parameter_values),
            width=width,
            height=height,
            layout_dpi=layout_dpi,
        )
        task.signals.finished.connect(self._apply_static_render_result)
        self.worker_pool.start(task)
        self.runtime.timing.log(
            "story.static_figure_view.cache_miss",
            cache_start_time,
            [("figure", self.figure_id)],
        )

    def _apply_static_render_result(self, result: object) -> None:
        if not isinstance(result, StaticFigureViewRenderResult):
            return
        if not self._active or self.mode != "static":
            return
        if result.request_id != self._render_request_id:
            return
        if result.key != self._pending_key:
            return
        if result.error is not None:
            self.image_label.setText(f"Could not render figure: {result.error}")
            self.image_label.setFixedHeight(280)
            return

        pixmap = QPixmap()
        pixmap.loadFromData(result.png_bytes, "PNG")
        if pixmap.isNull():
            self.image_label.setText("Could not render figure image")
            self.image_label.setFixedHeight(280)
            return

        self.image_cache[result.key] = pixmap
        self._apply_static_pixmap(pixmap)

    def _apply_static_pixmap(self, pixmap: QPixmap) -> None:
        self.image_label.setPixmap(pixmap)
        self.image_label.setFixedHeight(pixmap.height())
        self.updateGeometry()


class StoryPageCanvas(QWidget):
    """The sheet a story's blocks sit on.

    Each run of prose and each figure is a widget of its own, white where it
    draws, so without this the gaps between them and the margins around them
    show the scroll area through. The page is painted here, the width the
    story asks for, so the column reads as one sheet with the space around
    it, the way a PDF viewer shows a page.
    """

    def __init__(self) -> None:
        super().__init__()
        self._page_left = 0
        self._page_width = 0

    def set_page(self, left: int, width: int) -> None:
        if (left, width) == (self._page_left, self._page_width):
            return
        self._page_left = left
        self._page_width = width
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(self._page_left, 0, self._page_width, self.height(), Qt.GlobalColor.white)
        painter.end()
        super().paintEvent(event)


class StoryBlockPanel(QScrollArea):
    zoomChanged = Signal(float)

    def __init__(
        self,
        runtime: LimelightRuntime,
        *,
        on_parameter_changed: Callable[[], None] | None = None,
        on_popout_requested: Callable[
            [str, Sequence[str], Sequence[str], Sequence[dict[str, Any]]],
            None,
        ]
        | None = None,
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.on_parameter_changed = on_parameter_changed
        self.on_popout_requested = on_popout_requested
        self.image_cache: dict[StaticFigureViewKey, QPixmap] = {}
        self.markdown_pool = QThreadPool(self)
        self.markdown_pool.setMaxThreadCount(2)
        self.figure_pool = QThreadPool(self)
        self.figure_pool.setMaxThreadCount(1)
        self._disposed = False
        self.setWidgetResizable(True)

        self.content = StoryPageCanvas()
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self._applied_page: tuple[int, int, int, int, float] | None = None
        self._zoom = 1.0
        # The gap between widgets is the story's block gap, set with the page
        # in _apply_page_geometry, and each run of prose ends flush (see
        # story_run_edge_css), so a paragraph break and a widget boundary are
        # the same size: the space a reader sees does not depend on whether a
        # figure interrupts.
        self.setWidget(self.content)
        self._section_widgets: dict[str, QWidget] = {}
        # Cross-reference targets: a figure view id, or an image asset id, to
        # the widget holding it.
        self._anchor_widgets: dict[str, QWidget] = {}

    def show_blocks(
        self,
        blocks: Sequence[Any],
        sections: Sequence[StorySection] = (),
    ) -> None:
        if self._disposed:
            return

        self.markdown_pool.clear()
        self.figure_pool.clear()

        start_time = self.runtime.timing.start()
        update_states = [
            (self, self.updatesEnabled()),
            (self.viewport(), self.viewport().updatesEnabled()),
            (self.content, self.content.updatesEnabled()),
        ]
        for widget, _ in update_states:
            widget.setUpdatesEnabled(False)

        self._apply_page_geometry()
        section_by_block_index = {
            section.block_index: section
            for section in sections
        }
        specs = self._block_specs(blocks, section_by_block_index)
        self._section_widgets = {}
        self._anchor_widgets = {}
        rendered_any = False
        try:
            self._discard_layout_items_from(len(specs))

            for index, (kind, payload) in enumerate(specs):
                widget = self._widget_at(index)
                if kind == "text":
                    if not isinstance(widget, StoryTextPanel):
                        widget = self._replace_widget_at(
                            index,
                            StoryTextPanel(self.runtime, self.markdown_pool),
                        )
                        widget.anchorRequested.connect(self.scroll_to_anchor)
                        widget.zoomRequested.connect(self._on_zoom_requested)
                    widget.set_zoom(self._zoom)
                    first_block_index, text_payload = payload
                    widget.set_blocks(text_payload)
                    section = section_by_block_index.get(first_block_index)
                    if section is not None:
                        self._section_widgets[section.id] = widget
                    for block in text_payload:
                        self._record_image_anchors(block, widget)
                    rendered_any = rendered_any or not widget.isHidden()
                    continue

                if not isinstance(widget, StoryFigureViewPanel):
                    widget = self._replace_widget_at(
                        index,
                        StoryFigureViewPanel(
                            self.runtime,
                            image_cache=self.image_cache,
                            worker_pool=self.figure_pool,
                            on_parameter_changed=self.on_parameter_changed,
                            on_popout_requested=self.on_popout_requested,
                        ),
                    )
                widget.set_figure(
                    payload[1].figure_id,
                    figure_view_index=payload[1].index,
                    figure_view_actions=payload[1].actions,
                )
                widget.set_zoom(self._zoom)
                widget.set_gap_padding(self._figure_gap_padding_px())
                section = section_by_block_index.get(payload[0])
                if section is not None:
                    self._section_widgets[section.id] = widget
                self._anchor_widgets[payload[1].figure_view_id] = widget
                rendered_any = True

            self.layout.addStretch(1)
            self.setVisible(rendered_any)
        finally:
            for widget, enabled in reversed(update_states):
                widget.setUpdatesEnabled(enabled)
            self.content.update()
            self.viewport().update()
            self.runtime.timing.log(
                "story.blocks.render",
                start_time,
                [
                    ("blocks", len(blocks)),
                    ("widgets", len(specs)),
                    ("rendered", rendered_any),
                ],
            )

    def redraw_figures(self) -> None:
        if self._disposed:
            return

        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if isinstance(widget, StoryFigureViewPanel):
                widget.redraw()

    def refresh_page_geometry(self) -> None:
        """Re-lay the story out after its page changed."""

        if self._disposed:
            return
        self._applied_page = None
        self._apply_page_geometry()

    def _dots_per_inch(self) -> float:
        # Zooming a document makes everything bigger, the column included, so
        # it is the same page seen closer rather than the same pixels reflowed.
        return float(self.logicalDpiX()) * self._zoom

    def _figure_gap_padding_px(self) -> int:
        """What a figure adds above and below itself, beyond the widget gap."""

        spacing = self.runtime.story_spacing
        dots_per_inch = self._dots_per_inch()
        return max(0, spacing.figure_gap_px(dots_per_inch) - spacing.block_gap_px(dots_per_inch))

    def _apply_page_geometry(self) -> None:
        """Lay the story out on the page its manifest asks for.

        The page width caps the column and centres it; the page margins are
        the space around the content, and the block gap the space between
        its widgets. All are applied to the block layout rather than to the
        story stylesheet, so a figure sits in the same column, the same
        distance from the prose above it, as a paragraph would - a
        stylesheet only reaches the text.
        """

        if self._disposed:
            return

        geometry = self.runtime.page_geometry
        dots_per_inch = self._dots_per_inch()
        available = self.viewport().width()

        if geometry.width_mm is not None:
            # A page with a size keeps it. Zooming scales the page rather than
            # reflowing the text into whatever the window happens to be, so the
            # line breaks a reader is looking at do not move; if the page no
            # longer fits, the story scrolls sideways, as a PDF would.
            page_width = int(round(geometry.width_mm / MM_PER_INCH * dots_per_inch))
            self.content.setMinimumWidth(page_width)
        else:
            page_width = available
            self.content.setMinimumWidth(0)

        outer = max(0, (available - page_width) // 2)
        self.content.set_page(outer, page_width)
        side = outer + geometry.margin_lr_px(dots_per_inch)
        top = geometry.margin_tb_px(dots_per_inch)
        gap = self.runtime.story_spacing.block_gap_px(dots_per_inch)
        applied = (side, top, gap, available, self._zoom)
        if self._applied_page == applied:
            return

        self._applied_page = applied
        self.layout.setContentsMargins(side, top, side, top)
        self.layout.setSpacing(gap)
        figure_padding = self._figure_gap_padding_px()
        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if isinstance(widget, StoryFigureViewPanel):
                widget.set_gap_padding(figure_padding)

    def resizeEvent(self, event: Any) -> None:
        # Everything below runs on widgets this panel owns, and a disposed
        # panel is one whose widgets are about to be, or have already been,
        # destroyed by the tab holding it being replaced.
        if self._disposed:
            return
        super().resizeEvent(event)
        self._apply_page_geometry()

    def zoom_by(self, direction: int) -> None:
        """Step the story's zoom, or reset it when ``direction`` is zero."""

        self._on_zoom_requested(direction)

    @property
    def zoom(self) -> float:
        return self._zoom

    def _on_zoom_requested(self, direction: int) -> None:
        if self._disposed:
            return
        if direction == 0:
            self.set_zoom(1.0)
            return
        steps = STORY_ZOOM_STEPS
        # Snap to the nearest step, then move one, so a zoom from an odd
        # starting point still lands somewhere sensible.
        current = min(range(len(steps)), key=lambda i: abs(steps[i] - self._zoom))
        self.set_zoom(steps[max(0, min(len(steps) - 1, current + direction))])

    def set_zoom(self, factor: float) -> None:
        """Show the whole story at ``factor``, blocks and figures alike."""

        if self._disposed or factor == self._zoom:
            return

        # The same page seen closer keeps the reader in the same place on it:
        # how far down they were is a fraction of the whole, and stays so.
        bar = self.verticalScrollBar()
        fraction = bar.value() / bar.maximum() if bar.maximum() > 0 else 0.0

        self._zoom = factor
        self.zoomChanged.emit(factor)
        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if isinstance(widget, (StoryTextPanel, StoryFigureViewPanel)):
                widget.set_zoom(factor)
        # The column is measured against a zoomed resolution, and the figures
        # are rendered to the column, so they follow.
        self._apply_page_geometry()

        def restore_place() -> None:
            if not self._disposed:
                bar.setValue(int(round(fraction * bar.maximum())))

        # The blocks have taken their zoomed sizes, and the layout catches up
        # once control returns to the event loop.
        self.layout.activate()
        restore_place()
        QTimer.singleShot(0, restore_place)

    def scroll_to_section(self, section_id: str) -> None:
        self._scroll_to_widget(self._section_widgets.get(section_id))

    def scroll_to_anchor(self, anchor: str) -> None:
        """Bring a cross-reference target into view.

        An anchor on an image resolves to the run of prose containing it,
        because that whole run is one widget, so the scroll lands at the top of
        that run rather than exactly on the image.
        """

        self._scroll_to_widget(self._anchor_widgets.get(anchor))

    def _scroll_to_widget(self, widget: QWidget | None) -> None:
        """Scroll so the widget starts at the top of the viewport.

        A section heading jumped to from the outline should read as the start
        of the page, not merely be somewhere on screen, which is all
        ``ensureWidgetVisible`` guarantees.
        """
        if widget is None or self._disposed:
            return

        def scroll() -> None:
            # Deferred, so the story may have been replaced by the time it runs.
            if self._disposed:
                return
            top = widget.mapTo(self.content, QPoint(0, 0)).y()
            self.verticalScrollBar().setValue(max(0, top - 12))

        QTimer.singleShot(0, scroll)

    def _record_image_anchors(self, block: Any, widget: QWidget) -> None:
        if StoryMarkdownRenderer is None:
            return
        if not (isinstance(block, dict) and block.get("markdown")):
            return
        for reference in story_image_references(str(block["markdown"])):
            anchor = self.runtime.image_anchor(reference.src)
            if anchor is not None:
                self._anchor_widgets[anchor] = widget

    @staticmethod
    def _is_text_block(block: Any) -> bool:
        return isinstance(block, str) or (
            isinstance(block, dict) and bool(block.get("markdown"))
        )

    def _block_specs(
        self,
        blocks: Sequence[Any],
        section_by_block_index: Mapping[int, StorySection],
    ) -> list[tuple[str, Any]]:
        """Group the blocks into one widget per figure or run of prose.

        A run of prose is cut at every section heading, so each section has a
        widget of its own to scroll to; otherwise headings that follow prose
        rather than a figure would be buried mid-widget and unreachable from
        the outline.
        """
        specs: list[tuple[str, Any]] = []
        text_blocks: list[tuple[int, Any]] = []

        def flush_text_blocks() -> None:
            nonlocal text_blocks
            if text_blocks:
                specs.append(("text", (text_blocks[0][0], [block for _, block in text_blocks])))
                text_blocks = []

        for index, block in enumerate(blocks):
            if self._is_text_block(block):
                if index in section_by_block_index:
                    flush_text_blocks()
                text_blocks.append((index, block))
                continue

            figure_view_state = _figure_view_state_from_block(self.runtime, block)
            if figure_view_state is not None:
                flush_text_blocks()
                specs.append(("figure", (index, figure_view_state)))

        flush_text_blocks()
        return specs

    def _widget_at(self, index: int) -> QWidget | None:
        if index >= self.layout.count():
            return None
        return self.layout.itemAt(index).widget()

    def _replace_widget_at(self, index: int, new_widget: QWidget) -> QWidget:
        if index < self.layout.count():
            item = self.layout.takeAt(index)
            old_widget = item.widget()
            if old_widget is not None:
                self._dispose_widget(old_widget)
        self.layout.insertWidget(index, new_widget)
        return new_widget

    def _discard_layout_items_from(self, index: int) -> None:
        while self.layout.count() > index:
            item = self.layout.takeAt(index)
            widget = item.widget()
            if widget is not None:
                self._dispose_widget(widget)

    @staticmethod
    def _dispose_widget(widget: QWidget) -> None:
        if isinstance(widget, StoryTextPanel):
            widget.dispose()
        if isinstance(widget, StoryFigureViewPanel):
            widget.dispose()
        widget.deleteLater()

    def dispose(self) -> None:
        if self._disposed:
            return

        self._disposed = True
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._dispose_widget(widget)
        self.markdown_pool.clear()
        self.figure_pool.clear()
        self.markdown_pool.waitForDone()
        self.figure_pool.waitForDone()


class InteractiveFigureViewPanel(QWidget):
    def __init__(self, runtime: LimelightRuntime, *, on_parameter_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.on_parameter_changed = on_parameter_changed
        self.figure_id: str | None = None
        self.figure_view_index: int | None = None
        self.figure_view_actions: tuple[dict[str, Any], ...] = ()
        self.parameter_signature: tuple[tuple[str, str], ...] = ()
        self._zoom_syncs: list[Any] = []
        self._hover_points: list[_HoverPoint] = []

        layout = QVBoxLayout(self)
        self.controls_container = QWidget()
        self.controls_layout = QHBoxLayout(self.controls_container)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.setSpacing(8)
        self.controls_container.hide()
        layout.addWidget(self.controls_container)
        self.figure = MatplotlibFigure(figsize=(7.2, 4.8), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("motion_notify_event", self._on_hover)
        self.canvas.mpl_connect("figure_leave_event", lambda _event: QToolTip.hideText())
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, stretch=1)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.hide()
        layout.addWidget(self.table, stretch=1)

    def show_figure(
        self,
        figure_id: str | None,
        *,
        figure_view_index: int | None = None,
        figure_view_actions: Sequence[dict[str, Any]] = (),
    ) -> None:
        next_figure_view_actions = tuple(figure_view_actions)
        if (
            self.figure_id == figure_id
            and self.figure_view_index == figure_view_index
            and self.figure_view_actions == next_figure_view_actions
            and self.parameter_signature == self._parameter_signature()
        ):
            return
        self.figure_id = figure_id
        self.figure_view_index = figure_view_index
        self.figure_view_actions = next_figure_view_actions
        self.redraw()

    def redraw(self) -> None:
        start_time = self.runtime.timing.start()
        self.parameter_signature = self._parameter_signature()
        update_states = [
            (self, self.updatesEnabled()),
            (self.controls_container, self.controls_container.updatesEnabled()),
            (self.canvas, self.canvas.updatesEnabled()),
        ]
        for widget, _ in update_states:
            widget.setUpdatesEnabled(False)
        try:
            self._redraw_contents()
        finally:
            for widget, enabled in reversed(update_states):
                widget.setUpdatesEnabled(enabled)
        self.canvas.draw_idle()
        self.runtime.timing.log(
            "plot.redraw",
            start_time,
            [("figure", self.figure_id or "None")],
        )

    def _parameter_signature(self) -> tuple[tuple[str, str], ...]:
        return _parameter_signature(self.runtime)

    def _redraw_contents(self) -> None:
        self._disconnect_zoom_syncs()
        self._hover_points = []
        QToolTip.hideText()
        self._clear_controls()
        self.controls_container.hide()

        figure_spec = self.runtime.figure_specs.get(self.figure_id) if self.figure_id is not None else None
        if figure_spec is not None:
            self._show_controls(figure_controls(figure_spec))

        if figure_spec is not None and _is_table_view_figure(figure_spec):
            self.toolbar.hide()
            self.canvas.hide()
            table_view_spec = figure_spec["tableViewSpecs"][0]
            preview = self.runtime.table_preview(
                table_view_spec["data"],
                table_view_spec.get("columns"),
                column_formats=table_view_column_formats(table_view_spec),
            )
            _populate_table_widget(self.table, preview, table_view_spec)
            self.table.show()
            return

        self.table.hide()
        self.toolbar.show()
        self.canvas.show()
        _render_figure_view_to_matplotlib_figure(
            self.runtime,
            self.figure,
            self.figure_id,
            figure_view_index=self.figure_view_index,
            figure_view_actions=self.figure_view_actions,
            zoom_syncs=self._zoom_syncs,
            hover_sink=self._hover_points,
        )

    def _disconnect_zoom_syncs(self) -> None:
        for zoom_sync in self._zoom_syncs:
            zoom_sync.disconnect()
        self._zoom_syncs = []

    def _on_hover(self, event: Any) -> None:
        if event.inaxes is None or event.x is None or event.y is None:
            QToolTip.hideText()
            return

        candidates = [point for point in self._hover_points if point.axes is event.inaxes]
        if not candidates:
            QToolTip.hideText()
            return

        best_point: _HoverPoint | None = None
        best_index = -1
        best_distance_sq = float("inf")
        for point in candidates:
            if point.x.size == 0:
                continue
            display = point.axes.transData.transform(np.column_stack([point.x, point.y]))
            distances_sq = (display[:, 0] - event.x) ** 2 + (display[:, 1] - event.y) ** 2
            index = int(np.argmin(distances_sq))
            distance_sq = float(distances_sq[index])
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_point = point
                best_index = index

        if best_point is None or best_distance_sq > _HOVER_PIXEL_THRESHOLD_SQ:
            QToolTip.hideText()
            return

        x_value = float(best_point.x[best_index])
        y_value = float(best_point.y[best_index])
        x_display = _format_hover_x(x_value, best_point.x_categories, best_point.x_is_calendar)
        text = f"{best_point.label}\nx: {x_display}\ny: {y_value:.4g}"
        QToolTip.showText(QCursor.pos(), text, self.canvas)

    def dispose(self) -> None:
        self._disconnect_zoom_syncs()
        self._hover_points = []
        QToolTip.hideText()

    def _show_controls(self, form_specs: Sequence[dict[str, Any]]) -> None:
        self._clear_controls()

        rendered_any = False
        for form_spec in form_specs:
            if form_spec["title"]:
                title_label = QLabel(form_spec["title"])
                font = title_label.font()
                font.setBold(True)
                title_label.setFont(font)
                self.controls_layout.addWidget(title_label)
                rendered_any = True
            for control in form_spec["controls"]:
                if self._show_control(control):
                    rendered_any = True

        if rendered_any:
            self.controls_layout.addStretch(1)
        self.controls_container.setVisible(rendered_any)

    def _show_control(self, control: dict[str, Any]) -> bool:
        parameter_id = control["controlParameter"]
        parameter = self.runtime.control_parameters.get(parameter_id)
        if parameter is None:
            return False
        kind = control_parameter_data_type_kind(parameter)
        label = QLabel(control.get("label") or parameter.get("label") or parameter_id)

        if kind == "discrete":
            discrete_parameter = control_parameter_data_type_payload(parameter)
            combo = QComboBox()
            for option in discrete_parameter["options"]:
                combo.addItem(option["label"] or option["value"], option["value"])

            current_value = self.runtime.control_parameter_value(parameter_id)
            current_index = combo.findData(current_value)
            if current_index >= 0:
                blocker = QSignalBlocker(combo)
                combo.setCurrentIndex(current_index)
                del blocker

            combo.currentIndexChanged.connect(
                lambda index, control_combo=combo, selected_parameter=parameter_id: self._dropdown_changed(
                    selected_parameter,
                    control_combo.itemData(index),
                )
            )
            self.controls_layout.addWidget(label)
            self.controls_layout.addWidget(combo)
            return True

        if kind in ("integer", "float"):
            data_type_payload = control_parameter_data_type_payload(parameter)
            _, minimum, maximum = control_parameter_default_min_max(data_type_payload, kind)
            current_value = self.runtime.control_parameter_value(parameter_id)

            slider = QSlider(Qt.Horizontal)
            value_label = QLabel()
            row = QHBoxLayout()
            row.addWidget(slider)
            row.addWidget(value_label)
            row_container = QWidget()
            row_container.setLayout(row)

            if kind == "integer":
                lower = int(minimum) if minimum is not None else 0
                upper = int(maximum) if maximum is not None else lower + 100
                slider.setRange(lower, upper)
                current = int(current_value) if current_value is not None else lower
                blocker = QSignalBlocker(slider)
                slider.setValue(current)
                del blocker
                value_label.setText(str(current))

                def _integer_slider_changed(
                    value: int, value_label: QLabel = value_label, selected_parameter: str = parameter_id
                ) -> None:
                    value_label.setText(str(value))
                    self._slider_changed(selected_parameter, value)

                slider.valueChanged.connect(_integer_slider_changed)
            else:
                lower_f = _to_float(minimum) if minimum is not None else 0.0
                upper_f = _to_float(maximum) if maximum is not None else lower_f + 1.0
                steps = 1000

                def _step_to_value(step: int, lower_f: float = lower_f, upper_f: float = upper_f) -> float:
                    return lower_f + (step / steps) * (upper_f - lower_f) if upper_f > lower_f else lower_f

                def _value_to_step(value: float, lower_f: float = lower_f, upper_f: float = upper_f) -> int:
                    return round((value - lower_f) / (upper_f - lower_f) * steps) if upper_f > lower_f else 0

                slider.setRange(0, steps)
                current_f = _to_float(current_value) if current_value is not None else lower_f
                blocker = QSignalBlocker(slider)
                slider.setValue(_value_to_step(current_f))
                del blocker
                value_label.setText(f"{current_f:.3g}")

                def _float_slider_changed(
                    step: int,
                    value_label: QLabel = value_label,
                    selected_parameter: str = parameter_id,
                    to_value: Any = _step_to_value,
                ) -> None:
                    value = to_value(step)
                    value_label.setText(f"{value:.3g}")
                    self._slider_changed(selected_parameter, value)

                slider.valueChanged.connect(_float_slider_changed)

            self.controls_layout.addWidget(label)
            self.controls_layout.addWidget(row_container)
            return True

        return False

    def _clear_controls(self) -> None:
        while self.controls_layout.count():
            item = self.controls_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _dropdown_changed(self, parameter_id: str, value: Any) -> None:
        if value is None:
            return
        try:
            changed = self.runtime.set_control_parameter_value(parameter_id, str(value))
        except (KeyError, ValueError) as error:
            logger.exception("Invalid dropdown value for parameter %s", parameter_id)
            QMessageBox.warning(self, "Invalid Control Value", str(error))
            return
        if not changed:
            return
        logger.info("Parameter %s set to %s", parameter_id, value)
        if self.on_parameter_changed is not None:
            self.on_parameter_changed()
        else:
            self.redraw()

    def _slider_changed(self, parameter_id: str, value: Any) -> None:
        try:
            changed = self.runtime.set_control_parameter_value(parameter_id, value)
        except (KeyError, ValueError) as error:
            logger.exception("Invalid slider value for parameter %s", parameter_id)
            QMessageBox.warning(self, "Invalid Control Value", str(error))
            return
        if not changed:
            return
        logger.info("Parameter %s set to %s", parameter_id, value)
        if self.on_parameter_changed is not None:
            self.on_parameter_changed()
        else:
            self.redraw()

def _plot_x_values(values: Sequence[float], axis_binding: dict[str, Any]) -> list[float]:
    if _axis_calendar(axis_binding) == "CalendarDay":
        return [_calendar_day_to_matplotlib(value) for value in values]
    return list(values)


def _plot_window(window: tuple[float, float], axis_binding: dict[str, Any]) -> tuple[float, float]:
    if _axis_calendar(axis_binding) == "CalendarDay":
        return (
            _calendar_day_to_matplotlib(window[0]),
            _calendar_day_to_matplotlib(window[1]),
        )
    return window


def _axis_calendar(axis_binding: dict[str, Any]) -> str | None:
    data_type = axis_binding["dataType"]
    if not isinstance(data_type, dict):
        return None
    return data_type.get("calendar") or None


def _calendar_day_to_matplotlib(value: float) -> float:
    ordinal = round(value)
    return float(mdates.date2num(date.fromordinal(ordinal)))


def _time_limit_coordinate(value: Any, axis_binding: dict[str, Any]) -> float | None:
    parsed = _parse_time_limit(value)
    if parsed is None:
        return None
    if _axis_calendar(axis_binding) == "CalendarDay":
        return float(parsed.date().toordinal())
    return float(mdates.date2num(parsed))


def _parse_time_limit(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError:
            return None


def _plot_point(value: Any) -> tuple[float, float] | None:
    try:
        x = float(value["x"])
        y = float(value["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y)


def _is_scatter_artist(artist: dict[str, Any]) -> bool:
    return refs.artist_kind(artist) == "scatter"


def _is_stem_artist(artist: dict[str, Any]) -> bool:
    return refs.artist_kind(artist) == "stem"


def _is_timeseries_artist(artist: dict[str, Any]) -> bool:
    return refs.artist_kind(artist) == "timeSeries"


def _install_timeseries_artist(runtime: LimelightRuntime, axes: Any, artist: dict[str, Any]) -> Any | None:
    from . import largeseries_bridge

    source_id, column = refs.parse_column_ref(artist["y"])
    source = runtime.sources.get(source_id)
    if source is None:
        return None

    handle = largeseries_bridge.get_or_build_cache(runtime, source_id, column, source)
    spec = largeseries_bridge.hdf_source_spec(source, runtime.package, column)
    return largeseries_bridge.install_timeseries_artist(
        axes,
        handle,
        spec,
        target_buckets=artist.get("targetBuckets"),
        color=artist.get("color"),
        fill_color=artist.get("fillColor"),
        fill_alpha=artist.get("fillAlpha"),
        timing=runtime.timing,
    )


def _month_ordinal_label(value: float, mode: str) -> str:
    ordinal = round(value)
    if abs(value - ordinal) > 1e-6:
        return ""

    year = 1970 + ordinal // 12
    month = ordinal % 12 + 1
    if mode == "year":
        return str(year) if month == 1 else ""
    if mode == "quarter":
        return f"{month_abbr[month]} {year}" if month == 1 else month_abbr[month]
    if month == 1:
        return f"{month_abbr[month]}\n{year}"
    return month_abbr[month]


_DEFAULT_DECORATOR_COLOR = "#1f5f73"
# The VSpan's shade, so the two kinds of region read as the same thing.
_DEFAULT_RECT_COLOR = "#f3d36b"
_DEFAULT_RECT_ALPHA = 0.35


def _arrow_label_position(
    start: tuple[float, float],
    end: tuple[float, float],
    label: str,
) -> tuple[float, float]:
    if "lag" in label.lower():
        return ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    return start


def _point_label_offset(annotation: dict[str, Any]) -> tuple[float, float]:
    dx = annotation.get("labelOffsetDx")
    dy = annotation.get("labelOffsetDy")
    return (dx if dx is not None else 8.0, dy if dy is not None else 8.0)
