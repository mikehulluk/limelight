from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D
from matplotlib.text import Text

from .cache import CacheHandle, SourceSpec
from .query import QueryResult, full_x_range, query_range
from .status import NAN_KIND, UNKNOWN_KIND, kinds_in_mask
from .timing import TimingProbeLike

# Buckets across the axes when its pixel width is not known (a figure that
# has not been drawn yet); once drawn, one bucket per pixel column.
DEFAULT_TARGET_BUCKETS = 2000
INDICATOR_GID = "limelight-envelope-indicator"
DEFAULT_FILL_ALPHA = 1.0

# How a missing sample (a NaN in the source) is pointed out once the view is
# zoomed to raw samples: a small red cross at the sample's x, at the height of
# the last good sample before it, so a single bad reading is seen rather than
# just leaving a hole in the line. Zoomed out, a bucket with some missing
# samples draws its good ones and there is nothing to mark.
MISSING_MARKERS = ("cross", "none")
DEFAULT_MISSING_MARKER = "cross"
MISSING_MARK_COLOR = "#d62728"
MISSING_MARK_SIZE_PT = 5.0

# The shapes a kind of missing sample can be marked with, as matplotlib markers.
MARK_SHAPES = {"cross": "x", "circle": "o", "square": "s", "triangle": "^", "diamond": "D", "none": None}
# Zoomed out, a bucket holding a kind of missing sample gets a tick of that
# kind's colour along the foot of the axes, this far up (axes fraction).
RUG_Y = 0.0
RUG_MARKER_SIZE_PT = 6.0
MISSING_GID = "limelight-missing"


@dataclass(frozen=True)
class MissingStyle:
    """How one kind of missing sample is shown: its marker shape and colour,
    and its name in the legend (None: no legend entry)."""

    label: str | None = None
    marker: str = "cross"
    color: str = MISSING_MARK_COLOR


# The two kinds every series can have. A plain NaN keeps the red cross it
# always had, unnamed; a status code no one declared is grey.
DEFAULT_MISSING_STYLES: dict[int, MissingStyle] = {
    NAN_KIND: MissingStyle(),
    UNKNOWN_KIND: MissingStyle(label="Unknown status", color="#7f7f7f"),
}


def missing_sample_marks(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where to draw the marks for the NaNs in `y`: their x, at the last good y.

    A missing sample before any good one takes the first good value after it
    instead; with no good samples at all there is nothing to place them on.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    missing = np.isnan(y)
    if not missing.any() or missing.all():
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    good = np.flatnonzero(~missing)
    # Index of the last good sample at or before each position; positions
    # before the first good sample get the first good one.
    last_good = good[np.clip(np.searchsorted(good, np.arange(len(y)), side="right") - 1, 0, None)]
    return x[missing], y[last_good[missing]]


class MissingMarks:
    """The marks for a series' missing samples, one pair of lines per kind of missing.

    Zoomed in to raw samples, each missing sample gets its kind's marker at
    the height of the last good sample before it (as `missing_sample_marks`).
    Zoomed out, a sample cannot be picked out, so each bucket that holds a
    kind of missing gets a tick in that kind's colour along the foot of the
    axes instead: a reader sees where data is missing, and which kind, at
    any zoom.

    Lines are made for the kinds `kinds_present` names (a series' whole
    extent, which the first, zoomed-out query shows), so the legend - built
    once the figure is drawn - names every kind the series has.
    """

    def __init__(
        self,
        ax: Axes,
        *,
        kinds_present: list[int],
        styles: dict[int, MissingStyle] | None = None,
        enabled: bool = True,
    ) -> None:
        self.ax = ax
        self.enabled = enabled
        all_styles = {**DEFAULT_MISSING_STYLES, **(styles or {})}
        self.points: dict[int, Line2D] = {}
        self.rugs: dict[int, Line2D] = {}
        if not enabled:
            return
        for kind in kinds_present:
            style = all_styles.get(kind, MissingStyle())
            shape = MARK_SHAPES.get(style.marker, "x")
            if shape is None:
                continue
            filled = shape != "x"
            (points,) = ax.plot(
                [], [],
                linestyle="none",
                marker=shape,
                markersize=MISSING_MARK_SIZE_PT,
                markeredgewidth=1.0,
                color=style.color,
                markerfacecolor=style.color if filled else "none",
                label=style.label or "_nolegend_",
                zorder=4,
                gid=MISSING_GID,
            )
            (rug,) = ax.plot(
                [], [],
                linestyle="none",
                marker="|",
                markersize=RUG_MARKER_SIZE_PT,
                markeredgewidth=1.0,
                color=style.color,
                transform=ax.get_xaxis_transform(),
                label="_nolegend_",
                zorder=4,
                clip_on=False,
            )
            # Marks sit inside the axes; they must not move its layout (and an
            # empty, unclipped line would give tight_layout nonsense bounds).
            points.set_in_layout(False)
            rug.set_in_layout(False)
            self.points[kind] = points
            self.rugs[kind] = rug

    def update(self, result: QueryResult) -> None:
        missing = result.missing
        for kind in self.points:
            points, rug = self.points[kind], self.rugs[kind]
            if missing is None or not len(result.x):
                points.set_data([], [])
                rug.set_data([], [])
                continue
            has_kind = (missing >> kind & 1).astype(bool)
            if result.raw:
                mx, my = missing_kind_marks(result.x, result.y_min, has_kind)
                points.set_data(mx, my)
                rug.set_data([], [])
            else:
                points.set_data([], [])
                bucket_x = result.x[has_kind]
                rug.set_data(bucket_x, np.full(bucket_x.shape, RUG_Y))
            # Both stay visible, holding no data when not in use: a hidden
            # line would also hide its symbol in the legend.

    def get_visible(self) -> bool:
        """Whether anything is marked now."""
        return any(len(line.get_xdata()) for line in [*self.points.values(), *self.rugs.values()])


def missing_kind_marks(x: np.ndarray, y: np.ndarray, is_kind: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """As `missing_sample_marks`, for the samples `is_kind` picks out (which are NaN in `y`)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    missing = np.isnan(y)
    if not is_kind.any() or missing.all():
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    good = np.flatnonzero(~missing)
    last_good = good[np.clip(np.searchsorted(good, np.arange(len(y)), side="right") - 1, 0, None)]
    return x[is_kind], y[last_good[is_kind]]


def kinds_present(result: QueryResult) -> list[int]:
    """Every kind of missing in a query's points: for a query over a whole series, the series'."""
    if result.missing is None or not len(result.missing):
        return []
    return kinds_in_mask(int(np.bitwise_or.reduce(result.missing)))


def plot_envelope(
    ax: Axes,
    result: QueryResult,
    *,
    color: str | None = None,
    fill_color: str | None = None,
    fill_alpha: float | None = None,
    line_kwargs: dict | None = None,
    fill_kwargs: dict | None = None,
    missing_marker: str = DEFAULT_MISSING_MARKER,
    missing_styles: dict[int, MissingStyle] | None = None,
) -> tuple[Line2D, Line2D, PolyCollection, MissingMarks]:
    """Draws a min/max envelope: two edge lines and the band between them,
    plus the marks for missing samples (see `MissingMarks`).

    `color` is the edge colour and, unless `fill_color` is given, the band
    colour too; the band is opaque unless `fill_alpha` says otherwise. With no
    colour given at all the series takes one step of the axes' colour cycle,
    so all three artists read as a single series rather than one colour per line.
    """
    line_kwargs = dict(line_kwargs or {})
    fill_kwargs = dict(fill_kwargs or {})
    if color is None:
        color = line_kwargs.get("color") or ax._get_lines.get_next_color()
    line_kwargs["color"] = color
    fill_kwargs["color"] = fill_color if fill_color is not None else color
    if fill_alpha is not None:
        fill_kwargs["alpha"] = fill_alpha

    line_kwargs.setdefault("linewidth", 0.8)
    fill_kwargs.setdefault("alpha", DEFAULT_FILL_ALPHA)
    fill_kwargs.setdefault("linewidth", 0.0)

    (line_min,) = ax.plot(result.x, result.y_min, **line_kwargs)
    (line_max,) = ax.plot(result.x, result.y_max, **line_kwargs)
    fill = ax.fill_between(result.x, result.y_min, result.y_max, **fill_kwargs)
    marks = MissingMarks(
        ax, kinds_present=kinds_present(result), styles=missing_styles, enabled=missing_marker != "none"
    )
    _apply_envelope_mode(line_max, fill, marks, result)

    return line_min, line_max, fill, marks


def add_missing_marks(ax: Axes, *, visible: bool = True) -> Line2D:
    """An (initially empty) marker-only line for missing-sample marks."""
    (marks,) = ax.plot(
        [], [],
        linestyle="none",
        marker="x",
        markersize=MISSING_MARK_SIZE_PT,
        markeredgewidth=1.0,
        color=MISSING_MARK_COLOR,
        label="_nolegend_",
        zorder=4,
    )
    marks.set_visible(visible)
    # Remembered here so the mode switch below can hide/show without losing it.
    marks._limelight_marks_enabled = visible  # type: ignore[attr-defined]
    return marks


def update_envelope_artists(
    line_min: Line2D,
    line_max: Line2D,
    fill: PolyCollection,
    result: QueryResult,
    marks: MissingMarks | None = None,
) -> None:
    line_min.set_data(result.x, result.y_min)
    line_max.set_data(result.x, result.y_max)
    # fill is a FillBetweenPolyCollection; set_data() (not set_verts()) is required
    # so its cached _bbox used by get_datalim()/relim() is refreshed too.
    fill.set_data(result.x, result.y_min, result.y_max)
    _apply_envelope_mode(line_max, fill, marks, result)


def _apply_envelope_mode(
    line_max: Line2D, fill: PolyCollection, marks: MissingMarks | None, result: QueryResult
) -> None:
    """Show the envelope only when there is one.

    Once the query returns raw samples (zoomed in far enough that every point is
    a single source value) y_min and y_max coincide, so the max line and the
    fill would just retrace the min line; hide them and let the min line stand
    as the plain data line. The missing-sample marks follow the same switch
    (see `MissingMarks`).
    """
    show_envelope = not result.raw
    line_max.set_visible(show_envelope)
    fill.set_visible(show_envelope)
    if marks is not None:
        marks.update(result)


def indicator_label(result: QueryResult) -> str:
    """Short description of what the viewer is looking at: raw samples or an envelope."""
    if result.raw:
        return f"raw · {result.sample_count:,} samples"
    points = int(result.x.shape[0])
    per_bucket = result.sample_count / points if points else 0.0
    return f"min/max · ~{per_bucket:,.0f} samples/bucket"


def add_indicator(ax: Axes, color: str, font: Any | None = None) -> Text:
    """Adds a small corner badge to the axes, stacked below any earlier badges.

    ``font`` is a matplotlib FontProperties; without one the badge is drawn
    at rcParams' "x-small".
    """
    existing = sum(1 for text in ax.texts if text.get_gid() == INDICATOR_GID)
    font_kwargs: dict[str, Any] = {"fontproperties": font} if font is not None else {"fontsize": "x-small"}
    return ax.text(
        0.99,
        0.98 - 0.08 * existing,
        "",
        transform=ax.transAxes,
        ha="right",
        va="top",
        color=color,
        **font_kwargs,
        gid=INDICATOR_GID,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": color, "alpha": 0.8},
        zorder=10,
    )


_SERIES_X_RANGE = "_limelight_series_x_range"


def series_x_union(ax: Axes, x_start: float, x_end: float) -> tuple[float, float]:
    """Record a series' x extent on `ax`, and return the extent of every series
    on `ax` and on the axes sharing its x: the range the axes should open on."""
    own = getattr(ax, _SERIES_X_RANGE, None)
    if own is not None:
        x_start, x_end = min(own[0], x_start), max(own[1], x_end)
    setattr(ax, _SERIES_X_RANGE, (x_start, x_end))
    for sibling in ax.get_shared_x_axes().get_siblings(ax):
        other = getattr(sibling, _SERIES_X_RANGE, None)
        if other is not None:
            x_start, x_end = min(other[0], x_start), max(other[1], x_end)
    return x_start, x_end


@dataclass
class ZoomSync:
    """Keeps a min/max envelope on an Axes in sync with the current xlim."""

    ax: Axes
    handle: CacheHandle
    spec: SourceSpec
    # A cap on the buckets across the axes, for a series that need not be
    # drawn at pixel resolution; None draws one bucket per pixel column.
    target_buckets: int | None = None
    on_query: Callable[[QueryResult], None] | None = None
    timing: TimingProbeLike | None = None
    # Edge colour (None: next in the axes' cycle), band colour (None: same as
    # the edges) and band opacity (None: opaque).
    color: str | None = None
    fill_color: str | None = None
    fill_alpha: float | None = None
    # Show a corner badge saying whether the axes currently shows raw samples
    # or a min/max envelope, so a viewer can tell how far they have zoomed in.
    indicator: bool = True
    # The badge's type, a matplotlib FontProperties; None is rcParams' "x-small".
    indicator_font: Any | None = None
    # "cross" marks each missing sample in the raw view; "none" leaves the gap.
    missing_marker: str = DEFAULT_MISSING_MARKER
    # How each kind of missing sample is marked, by kind (status.py); kinds
    # not named here take DEFAULT_MISSING_STYLES, else a red cross.
    missing_styles: dict[int, MissingStyle] | None = None

    _line_min: Line2D | None = field(default=None, init=False, repr=False)
    _line_max: Line2D | None = field(default=None, init=False, repr=False)
    _fill: PolyCollection | None = field(default=None, init=False, repr=False)
    _marks: MissingMarks | None = field(default=None, init=False, repr=False)
    _indicator: Text | None = field(default=None, init=False, repr=False)
    _cid: int | None = field(default=None, init=False, repr=False)
    # The (x_start, x_end, buckets) the artists currently show, so a
    # notification that asks for what is already drawn costs nothing.
    _drawn: tuple[float, float, int] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._install_initial_artists()
        self._connect()

    def _install_initial_artists(self) -> None:
        # The axes open on every series they show - this one, and any already
        # on them or on axes sharing their x - not on this series alone:
        # several recordings, one source each, sit side by side on one axis.
        x_start, x_end = series_x_union(self.ax, *full_x_range(self.handle, self.spec))
        # Setting the limits notifies every series already on these axes, and
        # every panel sharing them, which redraw for the wider range. A series
        # told the range it is already drawn at does nothing (`_on_xlim_changed`).
        self.ax.set_xlim(x_start, x_end)
        buckets = self._estimate_target_buckets()
        result = query_range(self.handle, self.spec, x_start, x_end, buckets, timing=self.timing)
        self._drawn = (x_start, x_end, buckets)
        self._line_min, self._line_max, self._fill, self._marks = plot_envelope(
            self.ax, result, color=self.color, fill_color=self.fill_color, fill_alpha=self.fill_alpha,
            missing_marker=self.missing_marker, missing_styles=self.missing_styles,
        )
        if self.indicator:
            self._indicator = add_indicator(self.ax, self._line_min.get_color(), self.indicator_font)
            self._indicator.set_text(indicator_label(result))
        self.ax.relim()
        self.ax.autoscale_view(scalex=False, scaley=True)
        if self.on_query is not None:
            self.on_query(result)

    def _connect(self) -> None:
        self._cid = self.ax.callbacks.connect("xlim_changed", self._on_xlim_changed)

    def _on_xlim_changed(self, ax: Axes) -> None:
        x_start, x_end = ax.get_xlim()
        buckets = self._estimate_target_buckets()
        if self._drawn == (x_start, x_end, buckets):
            # Already showing exactly this. Another series installing itself
            # on these axes, or a panel sharing them, sets the limits we are
            # at; requerying would draw the same picture again.
            return

        redraw_start = self.timing.start() if self.timing is not None else None
        result = query_range(self.handle, self.spec, x_start, x_end, buckets, timing=self.timing)
        self._drawn = (x_start, x_end, buckets)
        update_envelope_artists(self._line_min, self._line_max, self._fill, result, self._marks)
        if self._indicator is not None:
            self._indicator.set_text(indicator_label(result))
        ax.relim()
        ax.autoscale_view(scalex=False, scaley=True)
        if self.on_query is not None:
            self.on_query(result)
        ax.figure.canvas.draw_idle()
        if self.timing is not None and redraw_start is not None:
            self.timing.log(
                "limelight.largeseries.zoomsync.redraw",
                redraw_start,
                (
                    ("x_start", x_start),
                    ("x_end", x_end),
                    ("level", result.level),
                    ("points", int(result.x.shape[0])),
                ),
            )

    def _estimate_target_buckets(self) -> int:
        try:
            width = self.ax.get_window_extent().width
        except Exception:
            width = 0
        buckets = max(1, int(width)) if width and width > 1 else DEFAULT_TARGET_BUCKETS
        if self.target_buckets is not None:
            buckets = min(buckets, max(1, self.target_buckets))
        return buckets

    def disconnect(self) -> None:
        if self._cid is not None:
            self.ax.callbacks.disconnect(self._cid)
            self._cid = None
