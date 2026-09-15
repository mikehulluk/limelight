from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D
from matplotlib.text import Text

from .cache import CacheHandle, SourceSpec
from .query import QueryResult, full_x_range, query_range
from .timing import TimingProbeLike

# Buckets across the axes when its pixel width is not known (a figure that
# has not been drawn yet); once drawn, one bucket per pixel column.
DEFAULT_TARGET_BUCKETS = 2000
INDICATOR_GID = "limelight-envelope-indicator"
DEFAULT_FILL_ALPHA = 1.0


def plot_envelope(
    ax: Axes,
    result: QueryResult,
    *,
    color: str | None = None,
    fill_color: str | None = None,
    fill_alpha: float | None = None,
    line_kwargs: dict | None = None,
    fill_kwargs: dict | None = None,
) -> tuple[Line2D, Line2D, PolyCollection]:
    """Draws a min/max envelope: two edge lines and the band between them.

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
    _apply_envelope_mode(line_max, fill, result)

    return line_min, line_max, fill


def update_envelope_artists(
    line_min: Line2D,
    line_max: Line2D,
    fill: PolyCollection,
    result: QueryResult,
) -> None:
    line_min.set_data(result.x, result.y_min)
    line_max.set_data(result.x, result.y_max)
    # fill is a FillBetweenPolyCollection; set_data() (not set_verts()) is required
    # so its cached _bbox used by get_datalim()/relim() is refreshed too.
    fill.set_data(result.x, result.y_min, result.y_max)
    _apply_envelope_mode(line_max, fill, result)


def _apply_envelope_mode(line_max: Line2D, fill: PolyCollection, result: QueryResult) -> None:
    """Show the envelope only when there is one.

    Once the query returns raw samples (zoomed in far enough that every point is
    a single source value) y_min and y_max coincide, so the max line and the
    fill would just retrace the min line; hide them and let the min line stand
    as the plain data line.
    """
    show_envelope = not result.raw
    line_max.set_visible(show_envelope)
    fill.set_visible(show_envelope)


def indicator_label(result: QueryResult) -> str:
    """Short description of what the viewer is looking at: raw samples or an envelope."""
    if result.raw:
        return f"raw · {result.sample_count:,} samples"
    points = int(result.x.shape[0])
    per_bucket = result.sample_count / points if points else 0.0
    return f"min/max · ~{per_bucket:,.0f} samples/bucket"


def add_indicator(ax: Axes, color: str) -> Text:
    """Adds a small corner badge to the axes, stacked below any earlier badges."""
    existing = sum(1 for text in ax.texts if text.get_gid() == INDICATOR_GID)
    return ax.text(
        0.99,
        0.98 - 0.08 * existing,
        "",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize="x-small",
        color=color,
        gid=INDICATOR_GID,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": color, "alpha": 0.8},
        zorder=10,
    )


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

    _line_min: Line2D | None = field(default=None, init=False, repr=False)
    _line_max: Line2D | None = field(default=None, init=False, repr=False)
    _fill: PolyCollection | None = field(default=None, init=False, repr=False)
    _indicator: Text | None = field(default=None, init=False, repr=False)
    _cid: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._install_initial_artists()
        self._connect()

    def _install_initial_artists(self) -> None:
        x_start, x_end = full_x_range(self.handle, self.spec)
        self.ax.set_xlim(x_start, x_end)
        result = query_range(
            self.handle, self.spec, x_start, x_end, self._estimate_target_buckets(), timing=self.timing
        )
        self._line_min, self._line_max, self._fill = plot_envelope(
            self.ax, result, color=self.color, fill_color=self.fill_color, fill_alpha=self.fill_alpha
        )
        if self.indicator:
            self._indicator = add_indicator(self.ax, self._line_min.get_color())
            self._indicator.set_text(indicator_label(result))
        self.ax.relim()
        self.ax.autoscale_view(scalex=False, scaley=True)
        if self.on_query is not None:
            self.on_query(result)

    def _connect(self) -> None:
        self._cid = self.ax.callbacks.connect("xlim_changed", self._on_xlim_changed)

    def _on_xlim_changed(self, ax: Axes) -> None:
        redraw_start = self.timing.start() if self.timing is not None else None
        x_start, x_end = ax.get_xlim()
        result = query_range(
            self.handle, self.spec, x_start, x_end, self._estimate_target_buckets(), timing=self.timing
        )
        update_envelope_artists(self._line_min, self._line_max, self._fill, result)
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
