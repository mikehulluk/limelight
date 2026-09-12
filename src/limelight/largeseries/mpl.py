from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D

from .cache import CacheHandle, SourceSpec
from .query import QueryResult, full_x_range, query_range
from .timing import TimingProbeLike

DEFAULT_TARGET_BUCKETS = 2000


def plot_envelope(
    ax: Axes,
    result: QueryResult,
    *,
    color: str | None = None,
    line_kwargs: dict | None = None,
    fill_kwargs: dict | None = None,
) -> tuple[Line2D, Line2D, PolyCollection]:
    line_kwargs = dict(line_kwargs or {})
    fill_kwargs = dict(fill_kwargs or {})
    if color is not None:
        line_kwargs.setdefault("color", color)
        fill_kwargs.setdefault("color", color)

    line_kwargs.setdefault("linewidth", 0.8)
    fill_kwargs.setdefault("alpha", 0.35)
    fill_kwargs.setdefault("linewidth", 0.0)

    (line_min,) = ax.plot(result.x, result.y_min, **line_kwargs)
    (line_max,) = ax.plot(result.x, result.y_max, **line_kwargs)
    fill = ax.fill_between(result.x, result.y_min, result.y_max, **fill_kwargs)

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


@dataclass
class ZoomSync:
    """Keeps a min/max envelope on an Axes in sync with the current xlim."""

    ax: Axes
    handle: CacheHandle
    spec: SourceSpec
    target_buckets: int = DEFAULT_TARGET_BUCKETS
    on_query: Callable[[QueryResult], None] | None = None
    timing: TimingProbeLike | None = None

    _line_min: Line2D | None = field(default=None, init=False, repr=False)
    _line_max: Line2D | None = field(default=None, init=False, repr=False)
    _fill: PolyCollection | None = field(default=None, init=False, repr=False)
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
        self._line_min, self._line_max, self._fill = plot_envelope(self.ax, result)
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
        if width and width > 1:
            return max(1, int(width))
        return self.target_buckets

    def disconnect(self) -> None:
        if self._cid is not None:
            self.ax.callbacks.disconnect(self._cid)
            self._cid = None
