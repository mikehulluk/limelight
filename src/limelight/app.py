from __future__ import annotations

import base64
import csv
import hashlib
import io
import math
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Sequence

import h5py

from . import refs
from .logging_config import configure_logging
from .reader import LimelightError, LimelightPackage, open_limelight
from .semantic import (
    control_parameter_data_type_kind,
    control_parameter_data_type_payload,
    control_parameter_default_min_max,
    optional_field,
    validate_manifest_semantics,
)
from .story_markdown import figure_caption_markup, story_image_references

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("limelight.timing")


def run_app(path: str | None = None, *, debug_timing: bool = False) -> None:
    log_path = configure_logging()
    logger.info("Starting Limelight app for %s; log file is %s", path, log_path)
    try:
        from .qt_app import prompt_for_package_path, run_qt_app
    except ImportError as error:
        logger.exception("Could not import Limelight Qt application dependencies")
        raise LimelightError(
            "The Limelight app requires PySide6 and matplotlib. "
            "Install the project in your virtual environment with `python -m pip install -e .`."
        ) from error

    if path is None:
        # Launching from a desktop shortcut passes no package, so ask for one.
        path = prompt_for_package_path()
        if path is None:
            logger.info("No package selected; exiting")
            return

    logger.debug("Opening Limelight package %s", path)
    package = open_limelight(path)
    try:
        logger.debug("Reading manifest for %s", package.path)
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        logger.info("Launching Limelight Qt app for %s", package.path)
        run_qt_app(package, manifest, debug_timing=debug_timing)
    finally:
        logger.debug("Closing Limelight package %s", package.path)
        package.close()


class TimingProbe:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def start(self) -> float:
        return perf_counter()

    def log(self, label: str, start_time: float, fields: Sequence[tuple[str, object]] = ()) -> None:
        if not self.enabled:
            return

        elapsed_ms = (perf_counter() - start_time) * 1000.0
        field_text = " ".join(f"{name}={value}" for name, value in fields)
        suffix = f" {field_text}" if field_text else ""
        timing_logger.info("%s elapsed_ms=%.3f%s", label, elapsed_ms, suffix)


@dataclass(frozen=True)
class PlotSeries:
    label: str
    points: list[tuple[float, float]]
    sizes: list[float] | None = None
    size_label: str | None = None
    colors: list[Any] | None = None
    color_kind: str | None = None
    color_label: str | None = None
    x_categories: list[str] | None = None


@dataclass(frozen=True)
class TablePreview:
    title: str
    columns: list[str]
    rows: list[list[str]]
    total_rows: int
    truncated: bool
    column_kinds: list[str]


def _is_regular_index(index: Any) -> bool:
    if index == "noIndex":
        return True
    return isinstance(index, dict) and (
        "intOrigin" in index or "timeStepNom" in index or "calendarStep" in index
    )


def _format_cell(value: Any, format_spec: str | None = None) -> str:
    if value is None:
        return ""
    if format_spec:
        try:
            return format(value, format_spec)
        except (ValueError, TypeError):
            return str(value)
    return str(value)


def _column_kind(values: Sequence[Any]) -> str:
    for value in values:
        if value is not None:
            is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            return "numeric" if is_numeric else "text"
    return "text"


def _normalize_columns_filter(column: str | Sequence[str] | None) -> list[str] | None:
    if column is None:
        return None
    if isinstance(column, str):
        return [column]
    return list(column)


def table_view_column_formats(table_view_spec: dict[str, Any] | None) -> dict[str, str]:
    if table_view_spec is None:
        return {}
    return {
        column_format["column"]: format_spec
        for column_format in table_view_spec.get("columnFormats", [])
        if (format_spec := column_format.get("format")) is not None
    }


def table_view_column_alignment(
    table_view_spec: dict[str, Any] | None, preview: TablePreview, column_index: int
) -> str:
    column_name = preview.columns[column_index]
    if table_view_spec is not None:
        for column_format in table_view_spec.get("columnFormats", []):
            if column_format["column"] == column_name:
                alignment = column_format.get("alignment")
                if alignment is not None:
                    return alignment
                break
    return "Right" if preview.column_kinds[column_index] == "numeric" else "Left"


def table_view_header_styles(table_view_spec: dict[str, Any] | None) -> set[str]:
    if table_view_spec is None:
        return set()
    return set(table_view_spec.get("headerStyle", []))


def table_view_cell_styles(
    table_view_spec: dict[str, Any] | None, row_index: int, column_index: int
) -> set[str]:
    if table_view_spec is None:
        return set()
    styles: set[str] = set()
    for rule in table_view_spec.get("cellStyles", []):
        selector = rule["selector"]
        row_match = selector.get("row") is None or selector["row"] == row_index
        column_match = selector.get("column") is None or selector["column"] == column_index
        if row_match and column_match:
            styles.update(rule["styles"])
    return styles


_HDF_EXPORT_BLOCK_SIZE = 1_000_000
_CSV_SIZE_ESTIMATE_SAMPLE_ROWS = 200


def _regular_index_value_at(index: Any, row_index: int) -> Any:
    if index == "noIndex":
        return row_index
    if "intOrigin" in index:
        return index["intOrigin"] + row_index * index["intStep"]
    if "timeStepNom" in index:
        nom = index["timeStepNom"]
        denom = index["timeStepDenom"]
        time_origin = index["timeOrigin"]
        offset = 0.0
        if time_origin != "relative":
            offset = refs._epoch_offset_ns(time_origin) / refs._UNIT_NS[index["timeStepUnit"]]
        return offset + row_index * nom / denom
    if "calendarStep" in index:
        return index["startOrdinal"] + row_index * index["calendarStep"]
    raise ValueError(f"Unknown regular Index payload {index!r}")


# What a package built before page geometry existed was laid out as: A4 in the
# PDF, and whatever the window gave it on screen.
DEFAULT_PAGE_WIDTH_MM = 210.0
DEFAULT_PAGE_HEIGHT_MM = 297.0
DEFAULT_PAGE_MARGIN_MM = 15.0

# What a package built before spacing existed was set with: close to the
# stylesheet constants both renderers used, so its look does not change.
DEFAULT_BLOCK_GAP_MM = 3.0
DEFAULT_FIGURE_GAP_MM = 4.5
DEFAULT_HEADING_GAP_BEFORE_MM = 5.0
DEFAULT_HEADING_GAP_AFTER_MM = 2.0

MM_PER_INCH = 25.4

# The story's type. 14 CSS pixels on screen is 10.5 points, and the page
# prints at the same 10.5 points, so screen and paper set the same text; a
# figure's labels are drawn at the same size, so a plot reads as part of the
# story rather than as something pasted into it. CSS_PIXELS_PER_INCH is what
# makes a point 4/3 of a pixel: it is the resolution a figure is laid out at
# so that its points come out as the story's pixels.
STORY_FONT_PX = 14
STORY_FONT_PT = 10.5
STORY_LINE_HEIGHT = 1.5
CSS_PIXELS_PER_INCH = 96.0


def mm_to_px(millimetres: float, dots_per_inch: float) -> int:
    return int(round(millimetres / MM_PER_INCH * dots_per_inch))


@dataclass(frozen=True)
class PageGeometry:
    """The page a story is laid out on, as the runtime sees it.

    ``width_mm`` of None means the page fills whatever it is shown in;
    ``height_mm`` of None means it runs as long as its content. Both are the
    whole page, with the margins inside them: ``margin_lr_mm`` either side of
    the column, ``margin_tb_mm`` above and below it.
    """

    width_mm: float | None
    height_mm: float | None
    margin_lr_mm: float
    margin_tb_mm: float

    @property
    def content_width_mm(self) -> float | None:
        """The text column: the page less its side margins."""

        if self.width_mm is None:
            return None
        return max(0.0, self.width_mm - 2 * self.margin_lr_mm)

    def content_width_px(self, dots_per_inch: float) -> int | None:
        width_mm = self.content_width_mm
        if width_mm is None:
            return None
        return mm_to_px(width_mm, dots_per_inch)

    def margin_lr_px(self, dots_per_inch: float) -> int:
        return mm_to_px(self.margin_lr_mm, dots_per_inch)

    def margin_tb_px(self, dots_per_inch: float) -> int:
        return mm_to_px(self.margin_tb_mm, dots_per_inch)


def _page_geometry_from_story(story: dict[str, Any]) -> PageGeometry:
    page = optional_field(story, "page")
    if page is None:
        return PageGeometry(
            width_mm=DEFAULT_PAGE_WIDTH_MM,
            height_mm=DEFAULT_PAGE_HEIGHT_MM,
            margin_lr_mm=DEFAULT_PAGE_MARGIN_MM,
            margin_tb_mm=DEFAULT_PAGE_MARGIN_MM,
        )

    if "marginLR" in page:
        margin_lr_mm = float(page["marginLR"])
        margin_tb_mm = float(page["marginTB"])
    else:
        # A page written before the margin was split had one `margin` for
        # all four sides.
        margin_lr_mm = margin_tb_mm = float(page["margin"])

    # dhall-to-json renders a union alternative as its payload, or as the
    # alternative's own name when it carries none - the same shape
    # StoryFormat.markdown arrives in. So a measurement is a number and the
    # absence of one is a string.
    return PageGeometry(
        width_mm=_millimetres_or_none(page["width"]),
        height_mm=_millimetres_or_none(page["height"]),
        margin_lr_mm=margin_lr_mm,
        margin_tb_mm=margin_tb_mm,
    )


@dataclass(frozen=True)
class StorySpacing:
    """The vertical rhythm of the column: how far apart its blocks sit.

    Each gap is the whole distance between the two blocks it separates. All
    in millimetres, like the page, so the story keeps the same spacing on
    screen as on paper and zoom scales it with everything else.
    """

    block_gap_mm: float
    figure_gap_mm: float
    heading_gap_before_mm: float
    heading_gap_after_mm: float

    def block_gap_px(self, dots_per_inch: float) -> int:
        return mm_to_px(self.block_gap_mm, dots_per_inch)

    def figure_gap_px(self, dots_per_inch: float) -> int:
        return mm_to_px(self.figure_gap_mm, dots_per_inch)


def _story_spacing_from_story(story: dict[str, Any]) -> StorySpacing:
    spacing = optional_field(story, "spacing")
    if spacing is None:
        return StorySpacing(
            block_gap_mm=DEFAULT_BLOCK_GAP_MM,
            figure_gap_mm=DEFAULT_FIGURE_GAP_MM,
            heading_gap_before_mm=DEFAULT_HEADING_GAP_BEFORE_MM,
            heading_gap_after_mm=DEFAULT_HEADING_GAP_AFTER_MM,
        )

    return StorySpacing(
        block_gap_mm=float(spacing["blockGap"]),
        figure_gap_mm=float(spacing["figureGap"]),
        heading_gap_before_mm=float(spacing["headingGapBefore"]),
        heading_gap_after_mm=float(spacing["headingGapAfter"]),
    )


def figure_view_caption_markup(figure_spec: dict[str, Any], number: int | None) -> str:
    """The caption under a figure view: its number in bold, then its caption.

    A view with no caption is captioned with its title instead, so the number
    a cross-reference points at is always printed somewhere a reader can see.
    """

    return figure_caption_markup(number, figure_spec.get("caption") or figure_spec["title"])


# Story images are sized to fit rather than filled to the column: a small
# diagram should stay small. Rich text cannot honour this rule, so the
# fallback PDF writer drops it.
STORY_IMAGE_CSS = """img.limelight-story-image {
  display: block;
  max-width: 100%;
  height: auto;
}"""

# A figure is a box in the column, centred, and its caption hangs from its
# left edge no wider than it is. A story image's figure shrinks to the image,
# and the caption is kept from widening it: with no width of its own it adds
# nothing to the box, and the minimum then stretches it to whatever the image
# made the box. A width the author wrote is the figure's, not the image's -
# a percentage has to mean a share of the column, which a box shrunk to its
# image cannot give it - and the image fills the figure. A figure view fills
# the column, so its caption does too.
STORY_FIGURE_CSS = """figure.limelight-figure, figure.limelight-story-figure {
  margin-left: auto;
  margin-right: auto;
  text-align: left;
}
figure.limelight-story-figure {
  width: fit-content;
  max-width: 100%;
}
figure.limelight-sized-figure > img.limelight-story-image {
  width: 100%;
}
figure.limelight-story-figure > figcaption {
  width: 0;
  min-width: 100%;
}
figcaption {
  margin-top: 0.4em;
  font-size: 0.86em;
  color: #3c4043;
}"""

# The blocks of prose that `blockGap` separates. Headings and figures have
# gaps of their own.
_PROSE_BLOCK_SELECTOR = "p, ul, ol, pre, blockquote, table, hr, .math-display"
_HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6"
_FIGURE_SELECTOR = "figure.limelight-figure, figure.limelight-story-figure"


def story_rhythm_css(spacing: StorySpacing) -> str:
    """The stylesheet rules that space a story's blocks apart.

    Shared by the story panel and the PDF, so the two agree. Every block
    carries its gap as both a top and a bottom margin; adjacent margins
    collapse to the larger, which is exactly "the gap between a paragraph
    and the heading after it is the heading's gap". Only the vertical
    margins are set: a figure centres itself with the horizontal ones.
    """

    return f"""{_PROSE_BLOCK_SELECTOR} {{
  margin-top: {spacing.block_gap_mm:g}mm;
  margin-bottom: {spacing.block_gap_mm:g}mm;
}}
{_HEADING_SELECTOR} {{
  margin-top: {spacing.heading_gap_before_mm:g}mm;
  margin-bottom: {spacing.heading_gap_after_mm:g}mm;
}}
{_FIGURE_SELECTOR} {{
  margin-top: {spacing.figure_gap_mm:g}mm;
  margin-bottom: {spacing.figure_gap_mm:g}mm;
}}
body > :first-child {{
  margin-top: 0;
}}
body > :last-child {{
  margin-bottom: 0;
}}"""


def story_run_edge_css(spacing: StorySpacing) -> str:
    """Rules for a run of prose that is one widget among several.

    On screen the story is a column of widgets a ``blockGap`` apart, with
    each run of prose a document of its own, so a run's outer margins are
    zeroed by ``story_rhythm_css`` and the widget gap stands in for them. A
    heading or figure at a run's edge wants more than that, and gets the
    difference back here.
    """

    heading_extra = max(0.0, spacing.heading_gap_before_mm - spacing.block_gap_mm)
    figure_extra = max(0.0, spacing.figure_gap_mm - spacing.block_gap_mm)
    heading_first = ", ".join(f"body > {tag}:first-child" for tag in _HEADING_SELECTOR.split(", "))
    figure_first = ", ".join(f"body > {tag}:first-child" for tag in _FIGURE_SELECTOR.split(", "))
    figure_last = ", ".join(f"body > {tag}:last-child" for tag in _FIGURE_SELECTOR.split(", "))
    return f"""{heading_first} {{
  margin-top: {heading_extra:g}mm;
}}
{figure_first} {{
  margin-top: {figure_extra:g}mm;
}}
{figure_last} {{
  margin-bottom: {figure_extra:g}mm;
}}"""


def _millimetres_or_none(value: Any) -> float | None:
    return None if isinstance(value, str) else float(value)


@dataclass(frozen=True)
class StorySection:
    id: str
    title: str
    level: int
    block_index: int


FIGURE_DIRECTIVE_PATTERN = re.compile(r"^\s*@figure\(([^)]+)\)\s*$")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class LimelightRuntime:
    def __init__(
        self,
        package: LimelightPackage,
        manifest: dict[str, Any],
        *,
        debug_timing: bool = False,
    ) -> None:
        self.package = package
        self.manifest = manifest
        self.timing = TimingProbe(debug_timing)
        self.control_parameters = {item["id"]: item for item in manifest["controlParameters"]}
        self.control_parameter_values = {
            control_parameter_id: self._default_control_parameter_value(control_parameter)
            for control_parameter_id, control_parameter in self.control_parameters.items()
            if control_parameter_id
        }
        self.sources = {item["id"]: item for item in manifest["sources"]}
        manifest_figure_specs = manifest["figures"]
        self.figure_specs = {item["id"]: item for item in manifest_figure_specs}
        self.figures = self.figure_specs
        self.figure_views = {item["id"]: item for item in manifest["figureViews"]}
        self.figure_spec_order = [
            figure_spec["id"]
            for figure_spec in manifest_figure_specs
        ]
        self.figure_order = self.figure_spec_order
        self.figure_spec_indices = {
            figure_spec_id: index
            for index, figure_spec_id in enumerate(self.figure_spec_order, start=1)
        }
        self.figure_indices = self.figure_spec_indices

        story = manifest["story"]
        self.story_path = story["documentPath"]
        self.story_format = story["format"]
        if self.story_format != "markdown":
            raise LimelightError(f"Unsupported story format {self.story_format!r}")
        # What the document asks for, and what it is being shown at. The two
        # differ when a reader changes the page from the View menu; the
        # declared one is kept so it can be restored.
        self.declared_page_geometry = _page_geometry_from_story(story)
        self.page_geometry = self.declared_page_geometry
        self.story_spacing = _story_spacing_from_story(story)
        self.story_markdown = package.read_text(self.story_path)
        self.story_blocks, self.story_sections = parse_story_markdown(self.story_markdown)
        self.story_figure_view_ids = [
            block["figureView"]
            for block in self.story_blocks
            if _is_story_figure_block(block)
        ]
        for figure_view_id in self.story_figure_view_ids:
            if figure_view_id not in self.figure_views:
                raise LimelightError(f"Story document {self.story_path!r} references unknown FigureView {figure_view_id!r}")

        # Packages built before assets existed have no such key at all.
        self.image_assets = {asset["id"]: asset for asset in (manifest.get("assets") or [])}
        self.image_assets_by_path = {
            asset["path"]: asset for asset in self.image_assets.values()
        }
        for reference in story_image_references(self.story_markdown):
            if reference.src not in self.image_assets_by_path:
                raise LimelightError(
                    f"Story document {self.story_path!r} references image {reference.src!r}, "
                    "which is not a declared asset"
                )
        # Story images are read from the package and base64 encoded on demand,
        # from the markdown render worker, so the cache needs a lock like the
        # others here.
        self._image_data_urls: dict[str, str] = {}
        self._image_data_urls_lock = RLock()
        self._source_rows: dict[str, list[dict[str, str]]] = {}
        self._source_rows_lock = RLock()
        self._largeseries_caches: dict[str, Any] = {}
        self._largeseries_caches_lock = RLock()
        # Called as (source_id, column, samples_done, source_length) while a
        # large-series cache is being built, from whichever thread is building
        # it; the GUI sets this to show the build's progress.
        self.cache_progress: Callable[[str, str, int, int], None] | None = None

    def _default_control_parameter_value(self, control_parameter: dict[str, Any]) -> str | float | None:
        data_type = control_parameter["dataType"]
        kind = control_parameter_data_type_kind(control_parameter)
        data_type_payload = control_parameter_data_type_payload(control_parameter)
        if kind == "discrete":
            discrete = data_type_payload
            default = discrete["default"]
            if default is not None:
                return default
            options = discrete["options"]
            if not options:
                raise LimelightError(f"Discrete control parameter {control_parameter['id']!r} has no options")
            return options[0]["value"]

        if kind == "integer":
            default, minimum, _ = control_parameter_default_min_max(data_type_payload, kind)
            if default is not None:
                return int(default)
            return minimum

        if kind == "float":
            default, minimum, _ = control_parameter_default_min_max(data_type_payload, kind)
            if default is not None:
                return _to_float(default)
            return _to_float(minimum)

        if kind == "time":
            default, minimum, _ = control_parameter_default_min_max(data_type_payload, kind)
            if default is not None:
                return default
            return minimum

        raise TypeError(f"Unknown ControlParameterDataType payload for {control_parameter['id']!r}: {data_type!r}")

    def close(self) -> None:
        from .largeseries_bridge import close_runtime_caches

        close_runtime_caches(self)
        self.package.close()

    def control_parameter_value(self, control_parameter_id: str) -> str | float | None:
        return self.control_parameter_values.get(control_parameter_id)

    def set_control_parameter_value(self, control_parameter_id: str, value: str | float) -> bool:
        control_parameter = self.control_parameters.get(control_parameter_id)
        if control_parameter is None:
            raise KeyError(f"Unknown control parameter {control_parameter_id!r}")

        normalized_value: str | float
        data_type = control_parameter["dataType"]
        kind = control_parameter_data_type_kind(control_parameter)
        data_type_payload = control_parameter_data_type_payload(control_parameter)
        if kind == "discrete":
            text_value = str(value)
            allowed = {str(option["value"]) for option in data_type_payload["options"]}
            if text_value not in allowed:
                raise ValueError(f"{text_value!r} is not a valid value for control parameter {control_parameter_id!r}")
            normalized_value = text_value
        elif kind == "integer":
            if isinstance(value, bool):
                raise ValueError(f"{value!r} is not a valid integer for control parameter {control_parameter_id!r}")
            integer_value = int(value)
            self._validate_control_parameter_bounds(control_parameter_id, integer_value, data_type_payload, kind)
            normalized_value = integer_value
        elif kind == "float":
            float_value = _to_float(value)
            if float_value is None:
                raise ValueError(f"{value!r} is not a valid float for control parameter {control_parameter_id!r}")
            self._validate_control_parameter_bounds(control_parameter_id, float_value, data_type_payload, kind)
            normalized_value = float_value
        elif kind == "time":
            text_value = str(value)
            self._validate_control_parameter_bounds(control_parameter_id, text_value, data_type_payload, kind)
            normalized_value = text_value
        else:
            raise TypeError(f"Unknown ControlParameterDataType payload for {control_parameter_id!r}: {data_type!r}")

        if self.control_parameter_values.get(control_parameter_id) == normalized_value:
            return False
        self.control_parameter_values[control_parameter_id] = normalized_value
        return True

    def _validate_control_parameter_bounds(
        self,
        control_parameter_id: str,
        value: int | float | str,
        control_parameter_data: dict[str, Any],
        kind: str,
    ) -> None:
        _, minimum, maximum = control_parameter_default_min_max(control_parameter_data, kind)
        if minimum is not None and value < minimum:
            raise ValueError(f"{value!r} is below min {minimum!r} for control parameter {control_parameter_id!r}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{value!r} is above max {maximum!r} for control parameter {control_parameter_id!r}")

    def artist_visible(self, artist: dict[str, Any]) -> bool:
        visible_when = artist.get("visibleWhen")
        if visible_when is None:
            return True
        control_parameter_id = visible_when.get("controlParameter")
        expected_value = visible_when.get("value")
        if control_parameter_id is None:
            return True
        return self.control_parameter_values.get(control_parameter_id) == expected_value

    @property
    def project_title(self) -> str:
        return self.manifest["project"]["title"]

    @property
    def document_version(self) -> str | None:
        return self.manifest["project"].get("documentVersion")

    @property
    def manifest_sha8(self) -> str:
        return hashlib.sha256(self.package.manifest_text().encode("utf-8")).hexdigest()[:8]

    def rows_for_source(self, source_id: str) -> list[dict[str, str]]:
        with self._source_rows_lock:
            if source_id not in self._source_rows:
                start_time = self.timing.start()
                source = self.sources.get(source_id)
                if source is None:
                    raise KeyError(f"Unknown source {source_id!r}")
                self._source_rows[source_id] = self.package.read_csv(source["path"])
                self.timing.log(
                    "data.read_csv",
                    start_time,
                    [
                        ("source", source_id),
                        ("rows", len(self._source_rows[source_id])),
                    ],
                )
            return self._source_rows[source_id]

    def table_preview(
        self,
        source_id: str,
        column: str | Sequence[str] | None,
        *,
        limit: int = 2000,
        column_formats: dict[str, str] | None = None,
    ) -> TablePreview:
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(f"Unknown source {source_id!r}")

        columns_filter = _normalize_columns_filter(column)
        if columns_filter is not None and len(columns_filter) == 1:
            title = f"{source_id}['{columns_filter[0]}']"
        else:
            title = optional_field(source, "title") or source_id
        if "yArrays" in source:
            columns, rows, total_rows = self._hdf_table_rows(source, columns_filter, limit)
        else:
            columns, rows, total_rows = self._csv_table_rows(source, columns_filter, limit)

        column_formats = column_formats or {}
        column_kinds = [_column_kind([row[index] for row in rows]) for index in range(len(columns))]

        return TablePreview(
            title=title,
            columns=columns,
            rows=[
                [_format_cell(value, column_formats.get(columns[index])) for index, value in enumerate(row)]
                for row in rows
            ],
            total_rows=total_rows,
            truncated=total_rows > len(rows),
            column_kinds=column_kinds,
        )

    def _csv_table_rows(
        self, source: dict[str, Any], columns_filter: list[str] | None, limit: int
    ) -> tuple[list[str], list[list[Any]], int]:
        rows = self.rows_for_source(source["id"])
        if columns_filter is not None:
            table_rows = [[row.get(name) for name in columns_filter] for row in rows[:limit]]
            return list(columns_filter), table_rows, len(rows)

        columns = [array["name"] for array in source.get("schema") or []]
        limited_rows = rows[:limit]
        table_rows = [[row.get(name) for name in columns] for row in limited_rows]
        if _is_regular_index(source["index"]):
            index_values = refs.resolve_index_x(source["index"], limited_rows, len(limited_rows))
            columns = ["index", *columns]
            table_rows = [[index_value, *row] for index_value, row in zip(index_values, table_rows)]
        return columns, table_rows, len(rows)

    def _hdf_table_rows(
        self, source: dict[str, Any], columns_filter: list[str] | None, limit: int
    ) -> tuple[list[str], list[list[Any]], int]:
        y_arrays = source["yArrays"]
        if columns_filter is not None:
            by_name = {entry["schema"]["name"]: entry for entry in y_arrays}
            entries = []
            for name in columns_filter:
                entry = by_name.get(name)
                if entry is None:
                    raise KeyError(f"Unknown yArrays column {name!r} in source {source['id']!r}")
                entries.append(entry)
        else:
            entries = y_arrays

        hdf5_path = self.package.package_path(source["path"])
        columns = [entry["schema"]["name"] for entry in entries]
        total_rows = 0
        columns_data: list[list[Any]] = []
        with h5py.File(hdf5_path, "r") as handle:
            for entry in entries:
                dataset = handle[entry["dataset"]]
                total_rows = max(total_rows, dataset.shape[0])
                columns_data.append(dataset[:limit].tolist())

        row_count = min(limit, total_rows)
        table_rows = [
            [columns_data[column_index][row_index] for column_index in range(len(columns))]
            for row_index in range(row_count)
        ]
        if columns_filter is None and _is_regular_index(source["index"]):
            index_values = refs.resolve_index_x(source["index"], None, row_count)
            columns = ["index", *columns]
            table_rows = [[index_value, *row] for index_value, row in zip(index_values, table_rows)]
        return columns, table_rows, total_rows

    def estimate_table_csv_size_bytes(self, source_id: str, column: str | None) -> int:
        preview = self.table_preview(source_id, column, limit=_CSV_SIZE_ESTIMATE_SAMPLE_ROWS)

        header_buffer = io.StringIO()
        csv.writer(header_buffer).writerow(preview.columns)
        header_bytes = len(header_buffer.getvalue().encode("utf-8"))
        if not preview.rows:
            return header_bytes

        sample_buffer = io.StringIO()
        sample_writer = csv.writer(sample_buffer)
        for row in preview.rows:
            sample_writer.writerow(row)
        average_row_bytes = len(sample_buffer.getvalue().encode("utf-8")) / len(preview.rows)
        return header_bytes + int(average_row_bytes * preview.total_rows)

    def export_table_csv(self, source_id: str, column: str | None, dest_path: str) -> None:
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(f"Unknown source {source_id!r}")

        with open(dest_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if "yArrays" in source:
                self._export_hdf_table_csv(writer, source, column)
            else:
                self._export_csv_table_csv(writer, source, column)

    def _export_csv_table_csv(self, writer: Any, source: dict[str, Any], column: str | None) -> None:
        rows = self.rows_for_source(source["id"])
        if column is not None:
            writer.writerow([column])
            for row in rows:
                writer.writerow([_format_cell(row.get(column))])
            return

        columns = [array["name"] for array in source.get("schema") or []]
        if _is_regular_index(source["index"]):
            index_values = refs.resolve_index_x(source["index"], rows, len(rows))
            writer.writerow(["index", *columns])
            for index_value, row in zip(index_values, rows):
                writer.writerow([_format_cell(index_value), *(_format_cell(row.get(name)) for name in columns)])
            return

        writer.writerow(columns)
        for row in rows:
            writer.writerow([_format_cell(row.get(name)) for name in columns])

    def _export_hdf_table_csv(self, writer: Any, source: dict[str, Any], column: str | None) -> None:
        y_arrays = source["yArrays"]
        if column is not None:
            entries = [entry for entry in y_arrays if entry["schema"]["name"] == column]
            if not entries:
                raise KeyError(f"Unknown yArrays column {column!r} in source {source['id']!r}")
        else:
            entries = y_arrays

        columns = [entry["schema"]["name"] for entry in entries]
        include_index = column is None and _is_regular_index(source["index"])
        header = ["index", *columns] if include_index else columns
        writer.writerow(header)

        hdf5_path = self.package.package_path(source["path"])
        with h5py.File(hdf5_path, "r") as handle:
            datasets = [handle[entry["dataset"]] for entry in entries]
            total_rows = max((dataset.shape[0] for dataset in datasets), default=0)
            for start in range(0, total_rows, _HDF_EXPORT_BLOCK_SIZE):
                end = min(start + _HDF_EXPORT_BLOCK_SIZE, total_rows)
                block_columns = [dataset[start:end].tolist() for dataset in datasets]
                for offset in range(end - start):
                    row = [column_values[offset] for column_values in block_columns]
                    if include_index:
                        row = [_regular_index_value_at(source["index"], start + offset), *row]
                    writer.writerow([_format_cell(value) for value in row])

    def plot_series(self, artist: dict[str, Any], x_axis_kind: str | None = None) -> PlotSeries:
        start_time = self.timing.start()
        kind = refs.artist_kind(artist)
        y_table, y_column = refs.parse_column_ref(artist["y"])
        y_rows = self.rows_for_source(y_table)
        y_values = [_to_float(row.get(y_column)) for row in y_rows]

        x_override = artist.get("xOverride")
        if kind in ("scatter", "stem"):
            x_table, x_column = refs.parse_column_ref(artist["x"])
            x_rows = y_rows if x_table == y_table else self.rows_for_source(x_table)
            raw_x_values = [row.get(x_column) for row in x_rows]
        elif x_override is not None:
            x_table, x_column = refs.parse_column_ref(x_override)
            x_rows = y_rows if x_table == y_table else self.rows_for_source(x_table)
            raw_x_values = [row.get(x_column) for row in x_rows]
        else:
            source = self.sources[y_table]
            raw_x_values = refs.resolve_index_x(source["index"], y_rows, len(y_rows))

        x_categories: list[str] | None = None
        if x_axis_kind == "discrete":
            x_categories = []
            category_index: dict[str, int] = {}
            x_values: list[float | None] = []
            for value in raw_x_values:
                if value is None:
                    x_values.append(None)
                    continue
                category = str(value)
                if category not in category_index:
                    category_index[category] = len(x_categories)
                    x_categories.append(category)
                x_values.append(float(category_index[category]))
        else:
            x_values = [_to_plot_coordinate(value) for value in raw_x_values]

        size_values: list[float | None] | None = None
        size_label: str | None = None
        size_ref = artist.get("sizeBy")
        if size_ref is not None:
            size_table, size_column = refs.parse_column_ref(size_ref)
            size_rows = y_rows if size_table == y_table else self.rows_for_source(size_table)
            size_values = [_to_float(row.get(size_column)) for row in size_rows]
            size_label = size_column

        color_values: list[Any | None] | None = None
        color_kind: str | None = None
        color_label: str | None = None
        color_ref = artist.get("colorBy")
        if color_ref is not None:
            color_table, color_column = refs.parse_column_ref(color_ref)
            color_rows = y_rows if color_table == y_table else self.rows_for_source(color_table)
            raw_color_values = [row.get(color_column) for row in color_rows]
            color_label = color_column
            non_null = [value for value in raw_color_values if value is not None]
            if non_null and all(_to_float(value) is not None for value in non_null):
                color_kind = "continuous"
                color_values = [_to_float(value) for value in raw_color_values]
            else:
                color_kind = "categorical"
                color_values = [str(value) if value is not None else None for value in raw_color_values]

        count = min(len(x_values), len(y_values))
        if size_values is not None:
            count = min(count, len(size_values))
        if color_values is not None:
            count = min(count, len(color_values))
        points = []
        sizes = [] if size_values is not None else None
        colors = [] if color_values is not None else None
        for index in range(count):
            x = x_values[index]
            y = y_values[index]
            if x is not None and y is not None:
                points.append((x, y))
                if sizes is not None:
                    sizes.append(size_values[index] or 24.0)
                if colors is not None:
                    colors.append(color_values[index])
        series = PlotSeries(
            label=artist.get("label") or y_column,
            points=points,
            sizes=sizes,
            size_label=size_label,
            colors=colors,
            color_kind=color_kind,
            color_label=color_label,
            x_categories=x_categories,
        )
        self.timing.log(
            "data.plot_series",
            start_time,
            [
                ("source", y_table),
                ("y", y_column),
                ("points", len(points)),
            ],
        )
        return series

    def figure_spec_index(self, figure_spec_id: str) -> int | None:
        return self.figure_spec_indices.get(figure_spec_id)

    def figure_index(self, figure_id: str) -> int | None:
        return self.figure_spec_index(figure_id)

    def image_number(self, src: str) -> int | None:
        """The figure number of a story image, if it has one.

        An image written inline within a sentence is not a figure and is not
        numbered.
        """

        asset = self.image_assets_by_path.get(src)
        if asset is None:
            return None
        return optional_field(asset, "storyNumber")

    def image_anchor(self, src: str) -> str | None:
        """The id a cross-reference link navigates to for a story image."""

        asset = self.image_assets_by_path.get(src)
        return asset["id"] if asset is not None else None

    def image_display_width(self, src: str) -> str | None:
        """The CSS width an asset asks to be drawn at, if it asks for one.

        A width written at the reference in the story overrides this; the
        renderer decides that, since only it sees both.
        """

        asset = self.image_assets_by_path.get(src)
        if asset is None:
            return None

        width = optional_field(asset, "displayWidth")
        if width is None:
            return None
        suffix = "mm" if width["unit"] == "millimetres" else "%"
        return f"{width['value']}{suffix}"

    def image_data_url(self, src: str) -> str | None:
        """A packaged image as a ``data:`` URI, or None if it is not declared.

        Inlining is what lets one renderer serve the story panel, the WebEngine
        PDF path and the rich text PDF fallback: the first has no usable base
        URL, the second is loaded from a temporary directory, and the third
        resolves nothing but ``data:``. It also means this method, rather than
        a base URL, decides what a package can reach.
        """

        asset = self.image_assets_by_path.get(src)
        if asset is None:
            return None

        with self._image_data_urls_lock:
            cached = self._image_data_urls.get(src)
            if cached is not None:
                return cached

        media_type = "image/png" if asset["format"] == "png" else "image/jpeg"
        encoded = base64.b64encode(self.package.read_bytes(asset["path"])).decode("ascii")
        data_url = f"data:{media_type};base64,{encoded}"
        with self._image_data_urls_lock:
            self._image_data_urls[src] = data_url
        return data_url

    def figure_view_number(self, figure_view: dict[str, Any]) -> int | None:
        """Resolve the number printed for one figure view.

        `storyNumber` is assigned by the builder from story order. `index` is
        the field it supersedes, kept in the chain so packages published before
        `storyNumber` existed keep the numbering they shipped with.

        Both are optional, and dhall-to-json omits an absent optional rather
        than writing a null, so neither key can be indexed directly.
        """

        story_number = optional_field(figure_view, "storyNumber")
        if story_number is not None:
            return story_number
        return optional_field(figure_view, "index")

    def figure_view_heading(self, figure_spec_id: str, *, index: int | None = None) -> str:
        """How one figure is titled, with its number when it has one.

        A FigureSpec on its own has no number. Numbers belong to the story
        sequence, and a spec can be rendered by several figure views at several
        different numbers, so falling back to the spec's position in the
        manifest would print a second, unrelated "Figure N" for the same plot -
        which is exactly what the Figures tab used to show.
        """

        figure_spec = self.figure_specs[figure_spec_id]
        title = figure_spec["title"]
        if index is None:
            return title
        return f"Figure {index}. {title}"

    def figure_heading(self, figure_id: str) -> str:
        return self.figure_view_heading(figure_id)

    def figure_view_caption_markup(self, figure_spec_id: str, *, index: int | None = None) -> str:
        """What is written under one figure view, as ``figure_view_caption_markup`` makes it."""

        return figure_view_caption_markup(self.figure_specs[figure_spec_id], index)

    def figure_views_for_spec(self, figure_spec_id: str) -> list[dict[str, Any]]:
        """Every figure view rendering one spec, in the order a reader meets them.

        Views the story never shows have no number and come last.
        """

        views = [
            figure_view
            for figure_view in self.figure_views.values()
            if figure_view["ref"] == figure_spec_id
        ]
        return sorted(
            views,
            key=lambda view: (
                self.figure_view_number(view) is None,
                self.figure_view_number(view) or 0,
            ),
        )

    def figure_view_by_id(self, figure_view_id: str) -> dict[str, Any]:
        figure_view = self.figure_views.get(figure_view_id)
        if figure_view is None:
            raise LimelightError(f"Unknown FigureView {figure_view_id!r}")
        return figure_view


def parse_story_markdown(markdown: str) -> tuple[list[Any], list[StorySection]]:
    blocks: list[Any] = []
    sections: list[StorySection] = []
    text_lines: list[str] = []

    def flush_text() -> None:
        nonlocal text_lines
        text = "\n".join(text_lines).strip()
        if text:
            blocks.append({"markdown": text})
        text_lines = []

    for line in markdown.splitlines():
        figure_match = FIGURE_DIRECTIVE_PATTERN.match(line)
        if figure_match is not None:
            flush_text()
            blocks.append({"figureView": figure_match.group(1).strip()})
            continue

        heading_match = HEADING_PATTERN.match(line)
        if heading_match is not None:
            flush_text()
            heading_text = heading_match.group(2).strip()
            section_id = _story_section_id(heading_text, len(sections))
            sections.append(
                StorySection(
                    id=section_id,
                    title=heading_text,
                    level=len(heading_match.group(1)),
                    block_index=len(blocks),
                )
            )
        text_lines.append(line)

    flush_text()
    return blocks, sections


def _story_section_id(title: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = "section"
    return f"{slug}-{index}"


def _is_story_figure_block(block: Any) -> bool:
    return isinstance(block, dict) and "figureView" in block


def first_axes_spec(figure_spec: dict[str, Any]) -> dict[str, Any] | None:
    axes_specs = figure_spec["axesSpecs"]
    if not axes_specs:
        raise TypeError(f"FigureSpec {figure_spec['id']!r} has no axesSpecs")
    return axes_specs[0]


# A single panel renders at this height:width; each further stacked panel adds
# a little less than a whole one, since the panels share one x-axis strip.
FIGURE_PANEL_ASPECT = 0.58
FIGURE_EXTRA_PANEL_ASPECT = 0.47


def figure_aspect(figure_spec: dict[str, Any] | None) -> float:
    """Height as a fraction of width for a figure, from how many panels it stacks."""
    panels = len(figure_spec["axesSpecs"]) if figure_spec is not None else 1
    return FIGURE_PANEL_ASPECT + FIGURE_EXTRA_PANEL_ASPECT * max(0, panels - 1)


def figure_controls(figure_spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": form_spec["id"],
            "title": form_spec.get("title"),
            "caption": form_spec.get("caption"),
            "controls": form_spec["controls"],
        }
        for form_spec in figure_spec["formSpecs"]
    ]


def axis_label(axis_binding: dict[str, Any]) -> str:
    label = axis_binding["label"] or ""
    data_type = axis_binding["dataType"]
    unit = data_type.get("unit") if isinstance(data_type, dict) else None
    return f"{label} ({unit})" if unit else label


def axis_scale(axis_binding: dict[str, Any]) -> str:
    data_type = axis_binding["dataType"]
    if not isinstance(data_type, dict):
        return "Linear"
    return data_type.get("scale", "Linear")


def _to_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _to_plot_coordinate(value: Any) -> float | None:
    result = _to_float(value)
    if result is not None:
        return result
    if not isinstance(value, str):
        return None
    try:
        return float(date.fromisoformat(value).toordinal())
    except ValueError:
        try:
            return float(datetime.fromisoformat(value.replace("Z", "+00:00")).date().toordinal())
        except ValueError:
            return None
