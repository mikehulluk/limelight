"""Write normal matplotlib code; get a real interactive Limelight figure too.

    from limelight import mpl_capture

    fig, (ax1, ax2) = mpl_capture.subplots(2, 1, sharex=True)
    ax1.plot(t, voltage, "b-", label="V")
    ax2.scatter(t, current, s=8)
    fig.savefig("preview.png")                    # an ordinary matplotlib preview
    project.add_figure(fig.configure(id="vi", title="V and I"))

(`limelight.mpl_subplots` is the same `subplots` function, for a shorter
import.)

`CapturingAxes` stands in for a matplotlib `Axes`. The calls a build script
mostly uses - `plot`, `scatter`, `fill_between` (as the fill of a line
`plot` drew), `axvspan`/`axhspan`, `axvline`/`axhline`, the limit, label,
title and scale setters - each do two things from one call: draw on the
real `Axes`, and record the equivalent `LineArtist`/`ScatterArtist`/
decorator, so `LimelightProject.add_figure(fig)` builds the interactive
figure from the same calls. They take matplotlib's own call shapes:
positional format strings (`plot(x, y, "r--")`), `plot(y)`, several lines
in one `plot` call, keyword aliases (`lw=`, `c=`, `ec=`, ...), `set_xlim((0,
1))` and `set_xlim(left=0)`.

The preview is always what matplotlib itself would draw. Whatever a call
asks for that the interactive figure cannot show - a keyword with no
Limelight field (`zorder=`, `hatch=`), a value outside what a package can
hold (marker `"."`, a symlog scale), a call it has no equivalent for
(`fill_between` with a varying lower bound) - still reaches the preview,
and raises an `MplCaptureWarning` saying what the interactive figure will
show differently. Nothing changes the figure silently. Turn the warnings
into errors (`warnings.simplefilter("error", MplCaptureWarning)`) to insist
on an exact match.

Every other Axes method (`text`, `annotate`, `bar`, `tick_params`, ...)
passes straight through to the real Axes: it draws in the preview and is
not recorded at all.

Large series - millions of points, which neither matplotlib nor a CSV
handles well - go through lightspeed instead: drawn as its zoomable min/max
envelope in the preview, through the same code the app uses, and recorded as
a `TimeSeriesArtist` over an HDF5 source. Three ways in, one result:

    ax.series(source, "voltage")     # a `limelight.hdf_source(...)` the script declared
    ax.plot(t, v)                    # LARGE_SERIES_POINTS or more: written to HDF5 for you
    lightspeed.mpl.plot_series(ax, spec)   # an unmodified lightspeed script (the hook)

Arrays passed to `plot`/`scatter` become columns of a `CapturingDataset`
shared by every panel of one figure, named from `name=` or auto-numbered,
deduplicated by array identity - the same array reused across panels (a
shared x-axis, say) becomes one column, not a copy per panel.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _datetime
import functools
import itertools
import math
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Iterator, Sequence

import matplotlib._pylab_helpers as _pylab_helpers
import matplotlib.collections as _mcollections
import matplotlib.dates as _mdates
import matplotlib.lines as _mlines
import matplotlib.patches as _mpatches
import matplotlib.pyplot as _plt
import numpy as np
from matplotlib import cbook
from matplotlib.axes._base import _process_plot_format
from matplotlib.colors import is_color_like, to_hex

import lightspeed
from lightspeed.mpl import plot_series as _plot_series

from . import panel_render, refs
from .cachedir import resolve_cache_dir
from .largeseries_bridge import hdf_source_spec_at, missing_styles_of
from .semantic import _VALID_LINESTYLES, _VALID_MARKERS
from .writer import (
    STATUS_MARKERS,
    AxisDataType,
    EpochOffset,
    HdfArraySpec,
    HdfDataset,
    Index,
    LineArtist,
    Panel,
    ScatterArtist,
    StatusCode,
    TimeSeriesArtist,
    hdf_source,
)

# A plot() of at least this many points is drawn as a large series - written
# to HDF5 and drawn as lightspeed's zoomable min/max envelope, in the preview
# and in the app - rather than as an ordinary line. `plot(..., large=False)`
# keeps one line as a line; `large=True` makes a shorter one a series.
LARGE_SERIES_POINTS = 200_000

# Captured once, at import time, before `capture()` (below) ever has a
# chance to replace `matplotlib.pyplot.subplots` - `subplots()` always calls
# through this name, never the (possibly patched) live module attribute, so
# patching can never make it recurse into itself.
_REAL_SUBPLOTS = _plt.subplots

_MPL_TO_DRAW_STYLE = {
    None: "straight",
    "default": "straight",
    "steps": "stepsPre",
    "steps-pre": "stepsPre",
    "steps-post": "stepsPost",
    "steps-mid": "stepsMid",
}

_MPL_FILL_STEP_TO_DRAW_STYLE = {None: "straight", "pre": "stepsPre", "post": "stepsPost", "mid": "stepsMid"}

_MPL_TO_LINESTYLE = {
    "-": "-",
    "solid": "-",
    "--": "--",
    "dashed": "--",
    "-.": "-.",
    "dashdot": "-.",
    ":": ":",
    "dotted": ":",
    "None": "none",
    "none": "none",
    " ": "none",
    "": "none",
}

_MPL_TO_AXIS_SCALE = {"linear": "Linear", "log": "Log"}

_share_counter = itertools.count()

# pyplot's drawing functions `capture()` sends to the current CapturingAxes.
_PYPLOT_ROUTES = {
    "plot": "plot",
    "scatter": "scatter",
    "fill_between": "fill_between",
    "axvline": "axvline",
    "axhline": "axhline",
    "axvspan": "axvspan",
    "axhspan": "axhspan",
    "xlim": "set_xlim",
    "ylim": "set_ylim",
    "xlabel": "set_xlabel",
    "ylabel": "set_ylabel",
    "title": "set_title",
    "xscale": "set_xscale",
    "yscale": "set_yscale",
    "legend": "legend",
}


class MplCaptureWarning(UserWarning):
    """The preview draws something the interactive figure will not show."""


def _warn(message: str) -> None:
    # Point the warning at the caller's own line, however deep inside this
    # module it was raised.
    frame = sys._getframe(1)
    level = 2
    while frame is not None and frame.f_code.co_filename in (__file__, contextlib.__file__):
        frame = frame.f_back
        level += 1
    warnings.warn(message, MplCaptureWarning, stacklevel=level)


class _Uncaptured:
    """The parts of one call the interactive figure cannot show, reported
    as one warning when the call is done."""

    def __init__(self, call: str) -> None:
        self.call = call
        self.items: list[str] = []

    def add(self, item: str) -> None:
        self.items.append(item)

    def keywords(self, kwargs: dict[str, Any]) -> None:
        for key, value in kwargs.items():
            self.items.append(f"{key}={value!r}")

    def report(self) -> None:
        if self.items:
            _warn(f"{self.call}: the interactive Limelight figure will differ from the preview - {'; '.join(self.items)}")


def _capturable_color(value: Any) -> str | None:
    """A colour as a package can hold it (a matplotlib colour string), or
    None when `value` is not one colour."""

    if isinstance(value, str):
        return value if is_color_like(value) else None
    if is_color_like(value):
        return to_hex(value, keep_alpha=True)
    return None


def _capturable_marker(marker: Any, uncaptured: "_Uncaptured") -> str | None:
    """`marker` as a package can hold it - any of matplotlib's one-character
    codes - or None, with a warning, for one it cannot (a `$...$` mathtext
    marker, a Path, a vertex list)."""

    if marker is None or marker in _VALID_MARKERS:
        return marker
    uncaptured.add(f"marker={marker!r} (a package's markers are matplotlib's one-character codes)")
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool)


def _as_sized(values: Any) -> Any:
    return values if hasattr(values, "__len__") else np.asarray(list(values))


def _looks_like_same_data(a: Sequence[Any], b: Sequence[Any]) -> bool:
    """A cheap, sampled equality check - same length, and a handful of
    positions (all of them, for a short array) either exactly equal or
    numerically close. Only meant to catch an accidental duplicate capture
    of the same data under a second name; a false negative (two equal
    arrays this misses) just means no warning, not a wrong result."""

    if len(a) != len(b) or len(a) == 0:
        return False
    # Positional, whatever the container (a pandas Series indexes by label).
    a, b = np.asarray(a, dtype=object), np.asarray(b, dtype=object)
    indices = range(len(a)) if len(a) <= 20 else (0, len(a) // 4, len(a) // 2, (3 * len(a)) // 4, len(a) - 1)
    for i in indices:
        x, y = a[i], b[i]
        if _is_number(x) and _is_number(y):
            if not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12):
                return False
        elif x != y:
            return False
    return True


def _split_plot_args(args: tuple[Any, ...]) -> list[tuple[Any, Any, str | None]]:
    """`plot`'s positional arguments as matplotlib reads them: repeated
    groups of `[x], y, [fmt]`."""

    groups: list[tuple[Any, Any, str | None]] = []
    rest = list(args)
    while rest:
        if len(rest) >= 2 and isinstance(rest[1], str):
            groups.append((None, rest[0], rest[1]))
            rest = rest[2:]
        elif len(rest) >= 3 and isinstance(rest[2], str):
            groups.append((rest[0], rest[1], rest[2]))
            rest = rest[3:]
        elif len(rest) >= 2:
            groups.append((rest[0], rest[1], None))
            rest = rest[2:]
        else:
            groups.append((None, rest[0], None))
            rest = rest[1:]
    return groups


def _date_numbers(values: Any) -> np.ndarray | None:
    """`values` as matplotlib date numbers (days since 1970-01-01 UTC, the
    coordinates a Limelight time axis draws in), or None if they are not
    dates."""

    array = np.asarray(values)
    if array.dtype.kind == "M" or (
        array.dtype.kind == "O" and array.size and isinstance(array.flat[0], (_datetime.date, np.datetime64))
    ):
        return np.asarray(_mdates.date2num(array), dtype=float)
    return None


def _date_ns(values: Any) -> np.ndarray | None:
    """`values` as integer nanoseconds since 1970-01-01 UTC, or None if they
    are not dates: exact for numpy datetimes, to the microsecond or so for
    Python ones (which go through matplotlib's date numbers)."""

    array = np.asarray(values)
    if array.dtype.kind == "M":
        return array.astype("datetime64[ns]").astype(np.int64)
    numbers = _date_numbers(array)
    return None if numbers is None else np.round(numbers * refs.NS_PER_DAY).astype(np.int64)


def _date_number(value: Any) -> float | None:
    if isinstance(value, (_datetime.date, np.datetime64)):
        return float(_mdates.date2num(value))
    return None


def _utc_text(date_number: float) -> str:
    return _mdates.num2date(date_number).isoformat()


@dataclasses.dataclass
class _XWindow:
    """An x-axis limit, or an x span or line, written once `to_panel` knows
    whether the axis is a time axis."""

    kind: str  # "limits" or "window"
    lower: float
    upper: float
    style: dict[str, Any]


def _source_from_spec(spec: Any, missing_styles: Any, chunk_size: int, uncaptured: "_Uncaptured") -> HdfDataset:
    """An hdf source over the file a lightspeed `SourceSpec` reads, its x
    turned back into a Limelight index. A uniform x is a regular float
    index; an irregular one is its stored coordinates, placed in time when
    the spec's x0/dx are a time unit from an instant. Any other placing of
    an irregular x is not something an index can say: the interactive
    figure then draws the stored coordinates, and `uncaptured` says so."""

    arrays = [
        HdfArraySpec("y", spec.y_dataset, column=spec.y_column, status=spec.status_dataset, status_column=spec.status_column)
    ]
    if not spec.irregular_x:
        index = Index.regular_float(spec.x0, spec.dx)
    else:
        arrays.append(HdfArraySpec("x", spec.x_dataset, column=spec.x_column))
        unit = next(
            (name for name, ns in refs._UNIT_NS.items() if math.isclose(spec.dx * refs.NS_PER_DAY, ns, rel_tol=1e-9)),
            None,
        )
        if (spec.x0, spec.dx) == (0.0, 1.0):
            index = Index.irregular_index_array("x")
        elif unit is not None and spec.x0 >= 0:
            index = Index.irregular_index_time(
                "x", origin=EpochOffset.from_unix_ns(round(spec.x0 * refs.NS_PER_DAY)), unit=unit
            )
        else:
            uncaptured.add(
                f"x placed at {spec.x0} + coordinate * {spec.dx} (the interactive figure draws the stored x coordinates)"
            )
            index = Index.irregular_index_array("x")

    status_codes: list[StatusCode] = []
    if spec.status_map is not None:
        styles = missing_styles or {}
        meanings = {lightspeed.status.VALID: "valid", lightspeed.status.SKIP: "skip"}
        # Absent codes in the order of their kinds: the order the app numbers them in.
        for code, kind in sorted(zip(spec.status_map.codes, spec.status_map.kinds), key=lambda pair: (pair[1] >= 0, pair[1])):
            style = styles.get(kind)
            status_codes.append(
                StatusCode(
                    code=int(code),
                    meaning=meanings.get(kind, "absent"),
                    label=(style.label if style is not None and style.label else f"status {code}"),
                    marker=style.marker if style is not None and style.marker in STATUS_MARKERS else None,
                    color=style.color if style is not None else None,
                )
            )
    return hdf_source(
        id="series", hdf5_path=spec.hdf5_path, y_arrays=arrays, index=index, chunk_size=chunk_size, status_codes=status_codes
    )


def _recorded(mpl_name: str) -> Any:
    """A recording method that, once its panel's figure has been added to a
    project, only draws on the preview (as `Axes.<mpl_name>`) and warns:
    the interactive figure was written from what was recorded by then."""

    def wrap(method: Any) -> Any:
        @functools.wraps(method)
        def inner(self: "CapturingAxes", *args: Any, **kwargs: Any) -> Any:
            if self._added_as is None:
                return method(self, *args, **kwargs)
            for key in ("name", "x_name", "fill_color", "fill_alpha", "fill_baseline"):
                kwargs.pop(key, None)
            _warn(
                f"{mpl_name}: figure {self._added_as!r} was already added to a project, so this draws in "
                "the preview only - make every call before add_to()/add_figure()"
            )
            return getattr(self.mpl_axes, mpl_name)(*args, **kwargs)

        return inner

    return wrap


class CapturingDataset:
    """The dataset backing one or more `CapturingFigure`s: one registry of
    `{column_name: values}`.

    Pass the same `CapturingDataset` to `subplots()` for two figures that
    should share their data (instead of each capturing its own copy): every
    panel of a figure built this way already shares one (so the same array
    plotted on two panels - a shared x-axis, say - becomes one column, not
    a copy per panel), and two figures sharing the same `CapturingDataset`
    additionally dedupe *across* figures - a figure that re-plots the exact
    same array object another figure already captured just gets back that
    same column name, no `name=` needed, and `dataset_id` (see below) only
    needs setting once for both to end up pointing at the one dataset a
    project registers.

    A package's CSV dataset holds columns of one length, so columns of
    another length than the first one captured go into a dataset of their
    own when the figure is added (see `partition`).

    `register()` is what `CapturingAxes.scatter()`/`.plot()` call to get a
    column name for an array, deduplicated by identity. `name_for()` is the
    reverse lookup - "what did you call this array" - for anything that
    still needs the name explicitly (building a `Panel` by hand, say).
    """

    def __init__(self, *, build_dir: str | Path | None = None) -> None:
        self.columns: dict[str, Any] = {}
        # Where the HDF5 files of large series held only in memory are
        # written until the package is: `build_dir`, or a temporary
        # directory made the first time one is needed (and left in place, so
        # the files are still there whenever the package is written).
        self._build_dir = Path(build_dir) if build_dir is not None else None
        # Large series: hdf sources the script declared, by id, and ones
        # written (or adopted from plot_series) here, by key - their ids are
        # `<dataset_id>-<key>`, settled when a figure is added.
        self._declared_sources: dict[str, HdfDataset] = {}
        self._series_sources: dict[str, HdfDataset] = {}
        self._series_counter = itertools.count()
        # The id a project actually registered this dataset under, written
        # back by the first `CapturingFigure.add_to` that uses this dataset;
        # every other figure sharing it then defaults to that same id
        # instead of each computing (and registering) its own.
        self.dataset_id: str | None = None
        self._by_identity: dict[int, str] = {}
        self._counter = itertools.count()
        self._indices: dict[int, np.ndarray] = {}
        # Dates captured as date numbers, by the identity of the array the
        # script passed; the original is kept so its id stays its own.
        self._as_date_numbers: dict[int, tuple[Any, np.ndarray]] = {}

    def register(self, values: Sequence[Any], *, name: str | None = None) -> str:
        key = id(values)
        cached = self._by_identity.get(key)
        if cached is not None:
            return cached
        self._warn_if_similar_to_existing(values, name)
        column = name or f"col{next(self._counter)}"
        while column in self.columns:
            column = f"{column}_{next(self._counter)}"
        self.columns[column] = values
        self._by_identity[key] = column
        return column

    def register_dates(self, values: Any, *, name: str | None = None) -> str | None:
        """Registers `values` as date numbers, if they are dates (None if
        not). The same array object always comes back as the same column."""

        cached = self._as_date_numbers.get(id(values))
        if cached is not None:
            return self.register(cached[1], name=name)
        numbers = _date_numbers(values)
        if numbers is None:
            return None
        self._as_date_numbers[id(values)] = (values, numbers)
        return self.register(numbers, name=name)

    def series_key(self, name: str | None) -> str:
        key = name or f"series{next(self._series_counter)}"
        while key in self._series_sources:
            key = f"{key}_{next(self._series_counter)}"
        return key

    def build_path(self, key: str) -> Path:
        if self._build_dir is None:
            self._build_dir = Path(tempfile.mkdtemp(prefix="limelight-capture-"))
        self._build_dir.mkdir(parents=True, exist_ok=True)
        path = self._build_dir / f"{key}.h5"
        counter = itertools.count(1)
        while path.exists():
            path = self._build_dir / f"{key}-{next(counter)}.h5"
        return path

    def add_series_source(self, key: str, source: HdfDataset) -> None:
        self._series_sources[key] = source

    def add_declared_source(self, source: HdfDataset) -> None:
        existing = self._declared_sources.get(source.id)
        if existing is not None and existing is not source:
            raise ValueError(f"two different hdf sources are both called {source.id!r}")
        self._declared_sources[source.id] = source

    def source_id(self, ref: tuple[str, str], dataset_id: str) -> str:
        kind, name = ref
        return name if kind == "declared" else f"{dataset_id}-{name}"

    def hdf_sources(self, dataset_id: str) -> list[HdfDataset]:
        """Every hdf source this dataset's figures plot, with its final id."""
        for key, source in self._series_sources.items():
            source.id = f"{dataset_id}-{key}"
        return [*self._declared_sources.values(), *self._series_sources.values()]

    def index_for(self, length: int) -> np.ndarray:
        """The implicit x of `plot(y)` - 0, 1, 2, ... - one array per length,
        so every `plot(y)` of that length shares one column."""

        index = self._indices.get(length)
        if index is None:
            index = np.arange(length, dtype=float)
            self._indices[length] = index
            self.register(index, name="index")
        return index

    def name_for(self, values: Sequence[Any]) -> str:
        """The column name `values` was registered under - found by the same
        identity `register()` dedupes on, not by guessing a string. Lets
        another figure bind to this dataset (via its `dataset_id`) instead
        of capturing a duplicate copy of the same array."""

        try:
            return self._by_identity[id(values)]
        except KeyError:
            raise ValueError(
                "this dataset never captured that array - pass the exact object scatter()/plot() was given"
            ) from None

    def partition(self, dataset_id: str) -> dict[str, dict[str, Any]]:
        """The columns grouped into the package datasets they go into:
        `dataset_id` for the first length captured, `<dataset_id>-n<length>`
        for each other length. Stable as columns are added, since the first
        length never changes."""

        groups: dict[str, dict[str, Any]] = {}
        for column, values in self.columns.items():
            groups.setdefault(self.dataset_of(column, dataset_id), {})[column] = values
        return groups

    def dataset_of(self, column: str, dataset_id: str) -> str:
        length = len(self.columns[column])
        first_length = len(next(iter(self.columns.values())))
        return dataset_id if length == first_length else f"{dataset_id}-n{length}"

    def _warn_if_similar_to_existing(self, values: Sequence[Any], name: str | None) -> None:
        for existing_name, existing_values in self.columns.items():
            if existing_values is values:
                continue
            if _looks_like_same_data(values, existing_values):
                _warn(
                    f"column {name or '<auto>'!r} looks like a duplicate of already-registered column "
                    f"{existing_name!r} - same length, same (or near-identical) values. If this is "
                    f"the same data, pass the same array object so it dedupes instead of being copied."
                )
                return


@dataclasses.dataclass
class _Plotted:
    """One `plot()` line, kept so a later `fill_between` can find it."""

    x: Any
    y: Any
    artist: LineArtist
    mpl_line: Any


class CapturingAxes:
    def __init__(
        self,
        mpl_axes: Any,
        *,
        dataset: "CapturingDataset | None" = None,
        share_x: str | None = None,
    ) -> None:
        self.mpl_axes = mpl_axes
        # Shared with every other panel of the same CapturingFigure when
        # `subplots()` built this one; standalone construction gets its own,
        # so column registration still works, just with nothing to share.
        self.dataset = dataset if dataset is not None else CapturingDataset()
        self.lines: list[LineArtist] = []
        self.scatters: list[ScatterArtist] = []
        self.actions: list[Any] = []
        self.title: str | None = None
        self.x_label: str | None = None
        self.y_label: str | None = None
        self.y_scale: str = "Linear"
        self.x_scale: str = "Linear"
        # The panel's x share group in the real figure: panels with the same
        # one pan/zoom together, as matplotlib's sharex links them. Never
        # left unset - a Limelight panel with no share group joins every
        # other such panel, which is not what an unshared matplotlib Axes
        # does.
        self.share_x = share_x if share_x is not None else f"panel-{next(_share_counter)}"
        # The panel's y share group, as matplotlib's sharey links them;
        # unset, its y-axis is its own (as a Limelight y-axis is by default).
        self.share_y: str | None = None
        # An explicit Panel id, for view_action/to_panel; left unset, both
        # fall back to the figure's default <figure id>-plot<n> naming.
        self.panel_id: str | None = None
        # This panel's height relative to the others in a stacked figure
        # (`subplots(height_ratios=...)` sets it); unset is 1.
        self.height: float | None = None
        # The panel's exact place in the figure, (left, bottom, width,
        # height) as figure fractions, for a grid that is not one column -
        # `CapturingFigure.add_to` sets it from where matplotlib put it.
        self.frame: tuple[float, float, float, float] | None = None
        self._plotted: list[_Plotted] = []
        # True once dates are drawn on x: the panel's x-axis is a time axis.
        self.x_time = False
        # The id of the figure this panel was added to a project as; after
        # that, its calls only draw on the preview.
        self._added_as: str | None = None
        # Each recorded scatter's preview collection, to see at add time
        # whether the script gave it a colour bar.
        self._scatter_collections: list[Any] = []
        # Large series drawn on this panel: each artist, with where its
        # source's id comes from (`CapturingDataset.source_id`).
        self._series: list[tuple[TimeSeriesArtist, tuple[str, str]]] = []

    @property
    def columns(self) -> dict[str, Any]:
        return self.dataset.columns

    def _register(self, values: Sequence[Any], *, name: str | None) -> str:
        return self.dataset.register(values, name=name)

    def _register_x(self, values: Sequence[Any], *, name: str | None) -> str:
        column = self.dataset.register_dates(values, name=name)
        if column is not None:
            self.x_time = True
            return column
        return self._register(values, name=name)

    def _x_number(self, value: Any) -> Any:
        """An x position as the axis holds it: a date becomes a date number
        (and makes this a time axis)."""

        number = _date_number(value)
        if number is None:
            return value
        self.x_time = True
        return number

    # -- plot ---------------------------------------------------------------

    @_recorded("plot")
    def plot(
        self,
        *args: Any,
        name: str | None = None,
        x_name: str | None = None,
        fill_color: str | None = None,
        fill_alpha: float | None = None,
        fill_baseline: float = 0.0,
        large: bool | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """`Axes.plot`, recording each line as a `LineArtist`.

        `name=`/`x_name=` name the y and x columns (auto-numbered when
        unset); `fill_color`/`fill_alpha`/`fill_baseline` fill the line
        down to a baseline, as `LineArtist` can (or call `fill_between`
        straight after). Returns the list of lines, as matplotlib does.
        """

        if kwargs.get("data") is not None:
            uncaptured = _Uncaptured("plot")
            uncaptured.add("data=")
            uncaptured.report()
            return self.mpl_axes.plot(*args, **kwargs)
        kwargs.pop("data", None)
        kwargs = cbook.normalize_kwargs(kwargs, _mlines.Line2D)
        groups: list[tuple[Any, Any, str | None]] = []
        for x, y, fmt in _split_plot_args(args):
            y = _as_sized(y)
            if np.ndim(y) == 2:
                # A 2-D y is one line per column, as matplotlib draws it.
                y_array = np.asarray(y)
                groups.extend((x, y_array[:, column], fmt) for column in range(y_array.shape[1]))
            else:
                groups.append((x, y, fmt))
        lines = []
        for index, (x, y, fmt) in enumerate(groups):
            group_name = name if name is None or len(groups) == 1 else f"{name}_{index}"
            group_x_name = x_name if x_name is None or len(groups) == 1 else f"{x_name}_{index}"
            lines.append(
                self._plot_one(
                    x,
                    y,
                    fmt,
                    dict(kwargs),
                    name=group_name,
                    x_name=group_x_name,
                    fill_color=fill_color,
                    fill_alpha=fill_alpha,
                    fill_baseline=fill_baseline,
                    large=large,
                )
            )
        return lines

    def _plot_one(
        self,
        x: Any,
        y: Any,
        fmt: str | None,
        kwargs: dict[str, Any],
        *,
        name: str | None,
        x_name: str | None,
        fill_color: str | None,
        fill_alpha: float | None,
        fill_baseline: float,
        large: bool | None = None,
    ) -> Any:
        if large or (large is None and len(y) >= LARGE_SERIES_POINTS):
            line = self._plot_large(
                x, y, fmt, kwargs, name=name, explicit=bool(large), fill=fill_color is not None or fill_alpha is not None
            )
            if line is not None:
                return line
        uncaptured = _Uncaptured("plot")
        if x is None:
            x = self.dataset.index_for(len(y))
        x = _as_sized(x)
        if np.ndim(x) != 1 or _date_numbers(y) is not None:
            uncaptured.add("a 2-D x" if np.ndim(x) != 1 else "dates on the y-axis")
            uncaptured.report()
            return self.mpl_axes.plot(x, y, *([fmt] if fmt else []), **kwargs)[0]

        fmt_linestyle, fmt_marker, fmt_color = _process_plot_format(fmt) if fmt else (None, None, None)
        label = kwargs.pop("label", None)
        color = kwargs.pop("color", fmt_color)
        linestyle = kwargs.pop("linestyle", fmt_linestyle)
        marker = kwargs.pop("marker", fmt_marker)
        linewidth = kwargs.pop("linewidth", None)
        alpha = kwargs.pop("alpha", None)
        drawstyle = kwargs.pop("drawstyle", None)
        for key in ("scalex", "scaley"):
            if kwargs.pop(key, True) is not True:
                uncaptured.add(f"{key}=False")

        captured_color = _capturable_color(color) if color is not None else None
        if color is not None and captured_color is None:
            uncaptured.add(f"color={color!r}")
        captured_linestyle = _MPL_TO_LINESTYLE.get(linestyle) if isinstance(linestyle, str) else None
        if linestyle is not None and captured_linestyle not in _VALID_LINESTYLES:
            captured_linestyle = None
            uncaptured.add(f"linestyle={linestyle!r}")
        captured_marker = None if marker in (None, "None", "none", "") else marker
        captured_marker = _capturable_marker(captured_marker, uncaptured)
        if captured_linestyle == "none" and captured_marker is None and marker not in (None, "None", "none", ""):
            # A markers-only line whose marker a package cannot hold would draw
            # nothing at all; plain circles are the nearest thing that draws.
            captured_marker = "o"
        captured_linewidth = linewidth if linewidth is not None and _is_number(linewidth) and linewidth > 0 else None
        if linewidth is not None and captured_linewidth is None:
            uncaptured.add(f"linewidth={linewidth!r}")
        captured_alpha = alpha if alpha is not None and _is_number(alpha) and 0.0 <= alpha <= 1.0 else None
        if alpha is not None and captured_alpha is None:
            uncaptured.add(f"alpha={alpha!r}")
        draw_style = _MPL_TO_DRAW_STYLE.get(drawstyle) if drawstyle is None or isinstance(drawstyle, str) else None
        if draw_style is None:
            uncaptured.add(f"drawstyle={drawstyle!r}")
            kwargs["drawstyle"] = drawstyle
        captured_fill = _capturable_color(fill_color) if fill_color is not None else None
        if fill_color is not None and captured_fill is None:
            uncaptured.add(f"fill_color={fill_color!r}")
        uncaptured.keywords({key: value for key, value in kwargs.items() if key != "drawstyle"})

        # The preview first: if matplotlib rejects the call, nothing is recorded.
        line = panel_render.draw_line(
            self.mpl_axes,
            x,
            y,
            label=label,
            color=color,
            linestyle=linestyle,
            linewidth=linewidth,
            draw_style=draw_style,
            marker=marker,
            alpha=alpha,
            fill_color=fill_color,
            fill_alpha=fill_alpha,
            fill_baseline=fill_baseline,
        )
        if kwargs:
            line.set(**kwargs)

        x_col = self._register_x(x, name=x_name if x_name is not None else (f"{name}_x" if name is not None else None))
        y_col = self._register(y, name=name)
        artist = LineArtist(
            array=y_col,
            x=x_col,
            label=None if label is None else str(label),
            color=captured_color,
            linestyle=captured_linestyle,
            linewidth=captured_linewidth,
            draw_style=draw_style or "straight",
            marker=captured_marker,
            alpha=captured_alpha,
            fill_color=captured_fill,
            fill_alpha=fill_alpha if captured_fill is not None else None,
            fill_baseline=fill_baseline if captured_fill is not None else None,
        )
        self.lines.append(artist)
        self._plotted.append(_Plotted(x=x, y=y, artist=artist, mpl_line=line))
        uncaptured.report()
        return line

    # -- large series -------------------------------------------------------

    def series(
        self,
        source: HdfDataset,
        column: str,
        *,
        label: str | None = None,
        color: str | None = None,
        fill_color: str | None = None,
        fill_alpha: float | None = None,
        target_buckets: int | None = None,
        missing_marker: str | None = None,
    ) -> Any:
        """Plots `column` of an hdf source (`limelight.hdf_source(...)`) as a
        large series: lightspeed's zoomable min/max envelope, drawn in the
        preview through the same code the app uses, and recorded as a
        `TimeSeriesArtist`. Adding the figure adds the source to the
        project. Returns the preview's `lightspeed.mpl.ZoomSync` (None for a
        remote source with no local copy, which the preview cannot draw)."""

        names = [array.name for array in source.y_arrays]
        if column not in names:
            raise ValueError(f"hdf source {source.id!r} has no array {column!r}; it has {', '.join(names)}")
        style = dict(label=label, color=color, fill_color=fill_color, fill_alpha=fill_alpha, target_buckets=target_buckets)
        zoom = self._draw_series(source, column, missing_marker=missing_marker, **style)
        if self._added_as is not None:
            _warn(f"series: figure {self._added_as!r} was already added to a project, so this draws in the preview only")
            return zoom
        self.dataset.add_declared_source(source)
        self._record_series(("declared", source.id), column, missing_marker=missing_marker, time=source.index.absolute_time, **style)
        return zoom

    def __lightspeed_plot_series__(
        self,
        spec: Any,
        *,
        label: str | None = None,
        color: str | None = None,
        fill_color: str | None = None,
        fill_alpha: float | None = None,
        target_buckets: int | None = None,
        missing_marker: str = "cross",
        missing_styles: Any = None,
        chunk_size: int = lightspeed.DEFAULT_CHUNK_SIZE,
        **preview_only: Any,
    ) -> Any:
        """`lightspeed.mpl.plot_series(ax, spec, ...)` on this panel: drawn
        in the preview as plot_series draws it, and recorded as a large
        series over the same file. The spec's x is turned back into a
        Limelight index; when that cannot be done exactly, an
        `MplCaptureWarning` says what the interactive figure shows instead."""

        if preview_only.get("cache_dir") is None:
            # Limelight's cache directory, which the app reads, so the cache
            # built for the preview is the one the package opens with.
            preview_only["cache_dir"] = resolve_cache_dir()
        zoom = _plot_series(
            self.mpl_axes, spec, label=label, color=color, fill_color=fill_color, fill_alpha=fill_alpha,
            target_buckets=target_buckets, missing_marker=missing_marker, missing_styles=missing_styles,
            chunk_size=chunk_size, **preview_only,
        )
        if self._added_as is not None:
            _warn(f"plot_series: figure {self._added_as!r} was already added to a project, so this draws in the preview only")
            return zoom
        uncaptured = _Uncaptured("plot_series")
        source = _source_from_spec(spec, missing_styles, chunk_size, uncaptured)
        key = self.dataset.series_key(None)
        self.dataset.add_series_source(key, source)
        self._record_series(
            ("written", key), "y", label=label, color=_capturable_color(color) if color is not None else None,
            fill_color=_capturable_color(fill_color) if fill_color is not None else None, fill_alpha=fill_alpha,
            target_buckets=target_buckets, missing_marker=missing_marker, time=source.index.absolute_time,
        )
        uncaptured.report()
        return zoom

    def _draw_series(self, source: HdfDataset, column: str, *, missing_marker: str | None, **style: Any) -> Any:
        if source.hdf5_source_path is None:
            _warn(
                f"series: hdf source {source.id!r} is a remote file with no local copy, so the preview leaves it "
                "out; the interactive figure draws it once the file is downloaded"
            )
            return None
        manifest = source.manifest_source()
        spec = hdf_source_spec_at(manifest, source.hdf5_source_path, column)
        if source.index.absolute_time:
            self.mpl_axes.xaxis_date()
        return _plot_series(
            self.mpl_axes, spec, missing_marker=missing_marker or "cross", missing_styles=missing_styles_of(manifest),
            cache_dir=resolve_cache_dir(), chunk_size=source.chunk_size, **style,
        )

    def _record_series(
        self,
        ref: tuple[str, str],
        column: str,
        *,
        label: str | None,
        color: str | None,
        fill_color: str | None,
        fill_alpha: float | None,
        target_buckets: int | None,
        missing_marker: str | None,
        time: bool,
    ) -> None:
        artist = TimeSeriesArtist(
            data="",
            array=column,
            label=None if label is None else str(label),
            target_buckets=target_buckets,
            color=color,
            fill_color=fill_color,
            fill_alpha=fill_alpha,
            missing_marker=missing_marker,
        )
        self._series.append((artist, ref))
        if time:
            self.x_time = True

    def _plot_large(
        self, x: Any, y: Any, fmt: str | None, kwargs: dict[str, Any], *, name: str | None, explicit: bool, fill: bool
    ) -> Any:
        """A `plot()` line drawn as a large series instead; None (with a
        warning) for one that cannot be, which is then drawn as a line."""

        try:
            y_values = np.asarray(y, dtype=float)
            # Dates as integer nanoseconds, so their spacing is compared exactly.
            dates = _date_ns(x) if x is not None else None
            x_values = dates if dates is not None else (None if x is None else np.asarray(x, dtype=float))
        except (TypeError, ValueError):
            y_values = None
        if y_values is None or y_values.ndim != 1 or (x_values is not None and x_values.shape != y_values.shape):
            _warn("plot: these points cannot be a large series (not 1-D numbers), so they are drawn as a plain line")
            return None
        if x_values is not None and np.any(x_values[1:] < x_values[:-1]):
            _warn(
                f"plot: {len(y_values)} points with an unsorted x cannot be a large series (lightspeed needs x "
                "non-decreasing), so they are drawn as a plain line - sort them to plot them as one"
            )
            return None

        uncaptured = _Uncaptured("plot (a large series, drawn as a min/max envelope)")
        fmt_linestyle, fmt_marker, fmt_color = _process_plot_format(fmt) if fmt else (None, None, None)
        label = kwargs.pop("label", None)
        color = kwargs.pop("color", fmt_color)
        for key, value in (("linestyle", kwargs.pop("linestyle", fmt_linestyle)), ("marker", kwargs.pop("marker", fmt_marker))):
            if value not in (None, "None", "none", "", "-", "solid"):
                uncaptured.add(f"{key}={value!r}")
        drawstyle = kwargs.pop("drawstyle", None)
        if drawstyle not in (None, "default"):
            uncaptured.add(f"drawstyle={drawstyle!r}")
        if fill:
            uncaptured.add("fill_color=/fill_alpha= (a large series' band is between its min and max)")
        for key in ("scalex", "scaley"):
            kwargs.pop(key, None)
        uncaptured.keywords(kwargs)

        if dates is not None and len(dates) and dates[0] < 0:
            _warn("plot: dates before 1970 cannot be a large series' time index, so they are drawn as a plain line")
            return None
        key = self.dataset.series_key(name)
        path = self.dataset.build_path(key)
        steps = np.diff(x_values) if x_values is not None and len(x_values) > 1 else None
        if dates is not None:
            uniform = steps is not None and steps[0] > 0 and bool(np.all(steps == steps[0]))
        else:
            uniform = x_values is None or (
                steps is not None and steps[0] > 0 and np.allclose(steps, steps[0], rtol=1e-9, atol=0.0)
            )
        x0 = 0.0 if x_values is None else float(x_values[0])
        dx = 1.0 if steps is None else float(steps[0])
        if dates is not None and uniform:
            lightspeed.write_series(path, y_values)
            arrays = [HdfArraySpec("y", "/y")]
            index = Index.regular_time(
                step_nom=int(steps[0]), step_denom=1, step_unit="ns", epoch_offset=EpochOffset.from_unix_ns(int(dates[0]))
            )
        elif dates is not None:
            # Microseconds from the first sample: exact in a float for centuries.
            lightspeed.write_series(path, y_values, (dates - dates[0]) / 1_000.0)
            arrays = [HdfArraySpec("y", "/y"), HdfArraySpec("x", "/x")]
            index = Index.irregular_index_time("x", origin=EpochOffset.from_unix_ns(int(dates[0])), unit="us")
        elif uniform:
            lightspeed.write_series(path, y_values)
            arrays = [HdfArraySpec("y", "/y")]
            index = Index.regular_float(x0, dx)
        else:
            lightspeed.write_series(path, y_values, x_values)
            arrays = [HdfArraySpec("y", "/y"), HdfArraySpec("x", "/x")]
            index = Index.irregular_index_array("x")
        source = hdf_source(id=key, hdf5_path=path, y_arrays=arrays, index=index)

        zoom = self._draw_series(source, "y", missing_marker=None, label=label, color=color)
        self.dataset.add_series_source(key, source)
        self._record_series(
            ("written", key), "y", label=label, color=_capturable_color(color) if color is not None else None,
            fill_color=None, fill_alpha=None, target_buckets=None, missing_marker=None, time=dates is not None,
        )
        uncaptured.report()
        return zoom.lines[0]

    # -- fill_between ---------------------------------------------------------

    @_recorded("fill_between")
    def fill_between(
        self,
        x: Sequence[float],
        y1: Sequence[float],
        y2: Any = 0,
        where: Any = None,
        interpolate: bool = False,
        step: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Captured as the fill of the line a `plot()` call on this panel
        drew from the same `y1` array object (and the same x): the fill
        down to a constant `y2` that `LineArtist` can express. Anything else
        - a varying `y2`, `where=`, a `y1` no line was drawn from, or one
        two lines were drawn from - is drawn in the preview only, with an
        `MplCaptureWarning`."""

        reason = None
        target: _Plotted | None = None
        if kwargs.get("data") is not None:
            reason = "data="
        elif where is not None or interpolate:
            reason = "where=/interpolate="
        elif not _is_number(y2):
            reason = "a y2 that is not one number"
        else:
            matches = [plotted for plotted in self._plotted if plotted.y is y1]
            if not matches:
                reason = "a y1 no plot() on this panel drew"
            elif len(matches) > 1:
                reason = f"a y1 {len(matches)} plot() lines drew - which one's fill it is is ambiguous"
            elif matches[0].artist.fill_color is not None:
                reason = "a second fill for the same line"
            elif not (matches[0].x is x or np.array_equal(np.asarray(matches[0].x), np.asarray(x))):
                reason = "an x other than its line's"
            else:
                target = matches[0]
        if target is None:
            uncaptured = _Uncaptured("fill_between")
            uncaptured.add(f"the fill ({reason})")
            uncaptured.report()
            return self.mpl_axes.fill_between(x, y1, y2, where=where, interpolate=interpolate, step=step, **kwargs)

        uncaptured = _Uncaptured("fill_between")
        kwargs.pop("data", None)
        kwargs = cbook.normalize_kwargs(kwargs, _mcollections.PolyCollection)
        color = kwargs.pop("color", None)
        facecolor = kwargs.pop("facecolor", None)
        color = color if color is not None else facecolor
        alpha = kwargs.pop("alpha", None)
        label = kwargs.pop("label", None)
        artist = target.artist
        draw_style = artist.draw_style or "straight"
        if step is not None and _MPL_FILL_STEP_TO_DRAW_STYLE.get(step) != draw_style:
            # The interactive fill follows its line's steps; so does the
            # preview, so the two agree.
            uncaptured.add(f"step={step!r} (the fill follows its line's drawstyle)")
        if alpha is not None and not (_is_number(alpha) and 0.0 <= alpha <= 1.0):
            uncaptured.add(f"alpha={alpha!r}")
            alpha = None
        if label is not None and artist.label is not None and str(label) != artist.label:
            uncaptured.add(f"label={label!r} (the line's label {artist.label!r} labels its fill)")
        elif label is not None:
            artist.label = str(label)
        uncaptured.keywords(kwargs)

        # Matches panel_render.draw_line: the legend's handle follows
        # whichever artist carries the label, so move it from the line (now
        # that it's filled, usually too thin for a legend swatch) onto the
        # fill, which is the one that visually matches the filled color.
        target.mpl_line.set_label(None)
        fill = self.mpl_axes.fill_between(
            x,
            float(y2),
            y1,
            step=panel_render.MPL_FILL_STEPS[draw_style],
            color=color,
            alpha=1.0 if alpha is None else alpha,
            linewidth=0,
            label=artist.label,
        )
        if kwargs:
            fill.set(**kwargs)
        # Unset, matplotlib picked the colour; the interactive figure needs it said.
        captured_color = _capturable_color(color) if color is not None else None
        if captured_color is None:
            if color is not None:
                uncaptured.add(f"color={color!r}")
            captured_color = to_hex(fill.get_facecolor()[0], keep_alpha=False)
        artist.fill_color = captured_color
        artist.fill_alpha = alpha
        artist.fill_baseline = float(y2)
        uncaptured.report()
        return fill

    # -- scatter --------------------------------------------------------------

    @_recorded("scatter")
    def scatter(
        self,
        x: Sequence[float],
        y: Sequence[float],
        s: Any = None,
        c: Any = None,
        marker: Any = None,
        cmap: Any = None,
        norm: Any = None,
        vmin: float | None = None,
        vmax: float | None = None,
        alpha: Any = None,
        linewidths: Any = None,
        *,
        edgecolors: Any = None,
        plotnonfinite: bool = False,
        label: str | None = None,
        color: Any = None,
        name: str | None = None,
        x_name: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """`Axes.scatter`, recording a `ScatterArtist`: a numeric `c` with a
        value per point becomes its `colorBy` column (viridis, as matplotlib
        defaults to), an `s` with a value per point its `sizeBy` column."""

        if kwargs.get("data") is not None:
            uncaptured = _Uncaptured("scatter")
            uncaptured.add("data=")
            uncaptured.report()
            return self.mpl_axes.scatter(
                x, y, s, c, marker, cmap, norm, vmin, vmax, alpha, linewidths,
                edgecolors=edgecolors, plotnonfinite=plotnonfinite, label=label, color=color, **kwargs,
            )
        kwargs.pop("data", None)
        uncaptured = _Uncaptured("scatter")
        x, y = _as_sized(x), _as_sized(y)
        for alias in ("lw", "linewidth"):
            if alias in kwargs and linewidths is None:
                linewidths = kwargs.pop(alias)
        for alias in ("ec", "edgecolor"):
            if alias in kwargs and edgecolors is None:
                edgecolors = kwargs.pop(alias)
        for alias in ("fc", "facecolor", "facecolors"):
            if alias in kwargs and color is None:
                color = kwargs.pop(alias)

        # Colour: one colour, a value per point (colorBy), or neither.
        colour_source = color if color is not None else c
        captured_color: str | None = None
        color_values: Any = None
        if colour_source is not None:
            as_array = np.asarray(colour_source) if not isinstance(colour_source, str) else None
            if (
                color is None
                and as_array is not None
                and as_array.ndim == 1
                and as_array.dtype.kind in "fiu"
                and len(as_array) == len(x)
            ):
                color_values = c
                if cmap not in (None, "viridis"):
                    uncaptured.add(f"cmap={cmap!r} (the interactive figure uses viridis)")
            else:
                captured_color = _capturable_color(colour_source)
                if captured_color is None:
                    uncaptured.add("per-point colours")
        for key, value in (("norm", norm), ("vmin", vmin), ("vmax", vmax)):
            if value is not None:
                uncaptured.add(f"{key}={value!r}")

        # Size: one size, a value per point (sizeBy), or the default.
        captured_size: float | None = None
        size_values: Any = None
        if s is not None:
            if _is_number(s) and s > 0:
                captured_size = float(s)
            elif not _is_number(s) and np.ndim(s) == 1 and len(s) == len(x):
                size_values = s
            else:
                uncaptured.add(f"s={s!r}")

        captured_marker = None if marker is None else marker
        captured_marker = _capturable_marker(captured_marker, uncaptured)
        captured_alpha = alpha if alpha is not None and _is_number(alpha) and 0.0 <= alpha <= 1.0 else None
        if alpha is not None and captured_alpha is None:
            uncaptured.add("alpha (a package's scatter takes one alpha)")
        captured_edge_color = _capturable_color(edgecolors) if edgecolors is not None else None
        if edgecolors is not None and captured_edge_color is None:
            uncaptured.add(f"edgecolors={edgecolors!r}")
        captured_edge_width = linewidths if linewidths is not None and _is_number(linewidths) and linewidths >= 0 else None
        if linewidths is not None and captured_edge_width is None:
            uncaptured.add("linewidths (a package's scatter takes one edge width)")
        if plotnonfinite:
            uncaptured.add("plotnonfinite=True")
        uncaptured.keywords(kwargs)

        # The preview first, drawn the way the app will draw what was
        # captured, then everything that was not, on top.
        collection = panel_render.draw_scatter(
            self.mpl_axes,
            x,
            y,
            label=label,
            color=captured_color,
            marker=marker,
            alpha=captured_alpha,
            size=24.0 if s is None else s,
            edge_color=captured_edge_color,
            edge_width=captured_edge_width,
            color_values=color_values,
        )
        overrides: dict[str, Any] = dict(kwargs)
        if colour_source is not None and captured_color is None and color_values is None:
            overrides["facecolor"] = colour_source
        if cmap is not None and color_values is not None:
            overrides["cmap"] = cmap
        if norm is not None:
            overrides["norm"] = norm
        if alpha is not None and captured_alpha is None:
            overrides["alpha"] = alpha
        if edgecolors is not None and captured_edge_color is None:
            overrides["edgecolor"] = edgecolors
        if linewidths is not None and captured_edge_width is None:
            overrides["linewidth"] = linewidths
        if overrides:
            collection.set(**overrides)
        if vmin is not None or vmax is not None:
            collection.set_clim(vmin, vmax)

        x_col = self._register_x(x, name=x_name if x_name is not None else (f"{name}_x" if name is not None else None))
        y_col = self._register(y, name=name)
        color_by = self._register(color_values, name=f"{name}_color" if name else None) if color_values is not None else None
        size_by = self._register(size_values, name=f"{name}_size" if name else None) if size_values is not None else None
        self.scatters.append(
            ScatterArtist(
                array=y_col,
                x=x_col,
                label=None if label is None else str(label),
                color=captured_color,
                color_by=color_by,
                size_by=size_by,
                marker=captured_marker,
                alpha=captured_alpha,
                size=captured_size,
                edge_color=captured_edge_color,
                edge_width=captured_edge_width,
            )
        )
        self._scatter_collections.append(collection)
        uncaptured.report()
        return collection

    # -- decorators -----------------------------------------------------------

    def _decorator_style(self, kwargs: dict[str, Any], uncaptured: _Uncaptured, *, patch: bool) -> dict[str, Any]:
        kwargs.pop("data", None)
        normalized = cbook.normalize_kwargs(kwargs, _mpatches.Patch if patch else _mlines.Line2D)
        kwargs.clear()
        kwargs.update(normalized)
        color = kwargs.pop("color", None)
        if patch and color is None:
            color = kwargs.pop("facecolor", None)
        style: dict[str, Any] = {"label": kwargs.pop("label", None)}
        captured_color = _capturable_color(color) if color is not None else None
        if color is not None and captured_color is None:
            uncaptured.add(f"color={color!r}")
            kwargs["color"] = color
        style["color"] = captured_color
        alpha = kwargs.pop("alpha", None)
        if alpha is not None and not (_is_number(alpha) and 0.0 <= alpha <= 1.0):
            uncaptured.add(f"alpha={alpha!r}")
            kwargs["alpha"] = alpha
            alpha = None
        style["alpha"] = alpha
        if not patch:
            linewidth = kwargs.pop("linewidth", None)
            if linewidth is not None and not (_is_number(linewidth) and linewidth > 0):
                uncaptured.add(f"linewidth={linewidth!r}")
                kwargs["linewidth"] = linewidth
                linewidth = None
            style["linewidth"] = linewidth
        uncaptured.keywords({key: value for key, value in kwargs.items() if key not in ("color", "alpha", "linewidth")})
        return style

    @_recorded("axvspan")
    def axvspan(self, xmin: float, xmax: float, ymin: float = 0, ymax: float = 1, **kwargs: Any) -> Any:
        uncaptured = _Uncaptured("axvspan")
        if (ymin, ymax) != (0, 1):
            uncaptured.add("the span (ymin=/ymax= other than the full height)")
            uncaptured.report()
            return self.mpl_axes.axvspan(xmin, xmax, ymin, ymax, **kwargs)
        style = self._decorator_style(kwargs, uncaptured, patch=True)
        xmin, xmax = self._x_number(xmin), self._x_number(xmax)
        artist = panel_render.draw_vspan_decorator(self.mpl_axes, (xmin, xmax), **style)
        if kwargs:
            artist.set(**kwargs)
        self.actions.append(_XWindow("window", xmin, xmax, style))
        uncaptured.report()
        return artist

    @_recorded("axhspan")
    def axhspan(self, ymin: float, ymax: float, xmin: float = 0, xmax: float = 1, **kwargs: Any) -> Any:
        from .writer import axes_action_add_y_axis_window_decorator

        uncaptured = _Uncaptured("axhspan")
        if (xmin, xmax) != (0, 1):
            uncaptured.add("the span (xmin=/xmax= other than the full width)")
            uncaptured.report()
            return self.mpl_axes.axhspan(ymin, ymax, xmin, xmax, **kwargs)
        style = self._decorator_style(kwargs, uncaptured, patch=True)
        artist = panel_render.draw_hspan_decorator(self.mpl_axes, (ymin, ymax), **style)
        if kwargs:
            artist.set(**kwargs)
        self.actions.append(axes_action_add_y_axis_window_decorator(lower=ymin, upper=ymax, **style))
        uncaptured.report()
        return artist

    @_recorded("axvline")
    def axvline(self, x: float = 0, ymin: float = 0, ymax: float = 1, **kwargs: Any) -> Any:
        uncaptured = _Uncaptured("axvline")
        if (ymin, ymax) != (0, 1):
            uncaptured.add("the line (ymin=/ymax= other than the full height)")
            uncaptured.report()
            return self.mpl_axes.axvline(x, ymin, ymax, **kwargs)
        style = self._decorator_style(kwargs, uncaptured, patch=False)
        x = self._x_number(x)
        artist = panel_render.draw_vspan_decorator(self.mpl_axes, (x, x), **style)
        if kwargs:
            artist.set(**kwargs)
        self.actions.append(_XWindow("window", x, x, style))
        uncaptured.report()
        return artist

    @_recorded("axhline")
    def axhline(self, y: float = 0, xmin: float = 0, xmax: float = 1, **kwargs: Any) -> Any:
        from .writer import axes_action_add_y_axis_window_decorator

        uncaptured = _Uncaptured("axhline")
        if (xmin, xmax) != (0, 1):
            uncaptured.add("the line (xmin=/xmax= other than the full width)")
            uncaptured.report()
            return self.mpl_axes.axhline(y, xmin, xmax, **kwargs)
        style = self._decorator_style(kwargs, uncaptured, patch=False)
        artist = panel_render.draw_hspan_decorator(self.mpl_axes, (y, y), **style)
        if kwargs:
            artist.set(**kwargs)
        self.actions.append(axes_action_add_y_axis_window_decorator(lower=y, upper=y, **style))
        uncaptured.report()
        return artist

    # -- limits, labels, scales -------------------------------------------

    @_recorded("set_xlim")
    def set_xlim(self, *args: Any, **kwargs: Any) -> Any:
        """`Axes.set_xlim` in any of its forms - `(0, 1)`, `((0, 1))`,
        `left=0` - recording the limits it leaves the axes with."""

        result = self.mpl_axes.set_xlim(*args, **kwargs)
        if any(_date_number(value) is not None for value in (*args, *kwargs.values())) or (
            args and not _is_number(args[0]) and args[0] is not None
            and any(_date_number(value) is not None for value in np.ravel(np.asarray(args[0], dtype=object)))
        ):
            self.x_time = True
        lower, upper = self.mpl_axes.get_xlim()
        self.actions.append(_XWindow("limits", float(lower), float(upper), {}))
        return result

    @_recorded("set_ylim")
    def set_ylim(self, *args: Any, **kwargs: Any) -> Any:
        from .writer import axes_action_set_y_float_limits

        result = self.mpl_axes.set_ylim(*args, **kwargs)
        lower, upper = self.mpl_axes.get_ylim()
        self.actions.append(axes_action_set_y_float_limits(float(lower), float(upper)))
        return result

    def _set_text(self, call: str, value: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        uncaptured = _Uncaptured(call)
        if args:
            uncaptured.add(", ".join(repr(arg) for arg in args))
        uncaptured.keywords(kwargs)
        result = getattr(self.mpl_axes, call)(value, *args, **kwargs)
        uncaptured.report()
        return result

    @_recorded("set_xlabel")
    def set_xlabel(self, xlabel: Any, *args: Any, **kwargs: Any) -> Any:
        self.x_label = str(xlabel)
        return self._set_text("set_xlabel", xlabel, args, kwargs)

    @_recorded("set_ylabel")
    def set_ylabel(self, ylabel: Any, *args: Any, **kwargs: Any) -> Any:
        self.y_label = str(ylabel)
        return self._set_text("set_ylabel", ylabel, args, kwargs)

    @_recorded("set_title")
    def set_title(self, label: Any, *args: Any, **kwargs: Any) -> Any:
        self.title = str(label)
        return self._set_text("set_title", label, args, kwargs)

    def _set_scale(self, call: str, value: Any, kwargs: dict[str, Any]) -> str:
        uncaptured = _Uncaptured(call)
        getattr(self.mpl_axes, call)(value, **kwargs)
        scale = _MPL_TO_AXIS_SCALE.get(value) if isinstance(value, str) else None
        if scale is None:
            uncaptured.add(f"the {value!r} scale (a package's axes are linear or log)")
            scale = "Linear"
        uncaptured.keywords(kwargs)
        uncaptured.report()
        return scale

    @_recorded("set_yscale")
    def set_yscale(self, value: Any, **kwargs: Any) -> None:
        self.y_scale = self._set_scale("set_yscale", value, kwargs)

    @_recorded("set_xscale")
    def set_xscale(self, value: Any, **kwargs: Any) -> None:
        self.x_scale = self._set_scale("set_xscale", value, kwargs)

    @_recorded("legend")
    def legend(self, *args: Any, **kwargs: Any) -> Any:
        """Whether a panel has a legend carries over; where it sits and how it
        looks do not - the interactive figure places it itself."""

        uncaptured = _Uncaptured("legend")
        if args:
            uncaptured.add("handles/labels given to legend()")
        uncaptured.keywords(kwargs)
        result = self.mpl_axes.legend(*args, **kwargs)
        uncaptured.report()
        return result

    def grid(self, *args: Any, **kwargs: Any) -> Any:
        return self.mpl_axes.grid(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Anything not explicitly captured above (text, annotate, bar, hist,
        # tick_params, twinx, ...) passes straight through to the real Axes:
        # it draws for real - an existing debug script keeps working
        # untouched, wrapped or not - it just isn't recorded as a Limelight
        # artist. __getattr__ only fires once normal lookup (this class's
        # own methods, then instance attributes) has already failed, so this
        # never shadows anything defined above.
        if name == "mpl_axes":
            # Not set yet (a copy or unpickle in progress): don't recurse.
            raise AttributeError(name)
        return getattr(self.mpl_axes, name)

    @staticmethod
    def _with_colorbar(scatter: ScatterArtist, collection: Any) -> ScatterArtist:
        """A per-point-coloured scatter has a colour bar in the interactive
        figure exactly when the script gave it one (`fig.colorbar(sc)` or
        `plt.colorbar(sc)`), labelled as the script labelled it."""

        if scatter.color_by is None:
            return scatter
        colorbar = getattr(collection, "colorbar", None)
        if colorbar is None:
            return dataclasses.replace(scatter, colorbar=False)
        label = colorbar.ax.get_ylabel() if colorbar.orientation == "vertical" else colorbar.ax.get_xlabel()
        return dataclasses.replace(scatter, colorbar=True, color_label=label)

    def to_panel(self, *, data: str | None = None, height: float | None = None) -> Panel:
        """This panel as a `Panel`, bound to the package dataset its arrays
        went into - `data`, or the `dataset_id` its `CapturingDataset` was
        given. A column of another length than the dataset's first goes into
        that length's own dataset (see `CapturingDataset.partition`), and
        each artist on it says so."""

        data = data if data is not None else self.dataset.dataset_id
        if data is None:
            raise ValueError(
                "to_panel needs the dataset id - pass data=, or add the figure to a project first (add_to sets it)"
            )

        def bound(artist: Any) -> Any:
            artist_data = self.dataset.dataset_of(artist.array, data)
            return artist if artist_data == data else dataclasses.replace(artist, data=artist_data)

        def resolved(action: Any) -> Any:
            from .writer import (
                axes_action_add_axis_window_decorator,
                axes_action_add_utc_time_window_decorator,
                axes_action_set_x_float_limits,
                axes_action_set_x_utc_time_limits,
            )

            if not isinstance(action, _XWindow):
                return action
            if self.x_time:
                start, end = _utc_text(action.lower), _utc_text(action.upper)
                if action.kind == "limits":
                    return axes_action_set_x_utc_time_limits(start, end)
                return axes_action_add_utc_time_window_decorator(start=start, end=end, **action.style)
            if action.kind == "limits":
                return axes_action_set_x_float_limits(action.lower, action.upper)
            return axes_action_add_axis_window_decorator(lower=action.lower, upper=action.upper, **action.style)

        x_axis = (
            AxisDataType.time_series(label=self.x_label, share_group=self.share_x)
            if self.x_time
            else AxisDataType.continuous(label=self.x_label, scale=self.x_scale, share_group=self.share_x)
        )

        return Panel(
            lines=[bound(line) for line in self.lines],
            scatters=[bound(self._with_colorbar(scatter, collection)) for scatter, collection in zip(self.scatters, self._scatter_collections)],
            time_series=[dataclasses.replace(artist, data=self.dataset.source_id(ref, data)) for artist, ref in self._series],
            actions=[resolved(action) for action in self.actions],
            data=data,
            title=self.title,
            id=self.panel_id,
            height=height if height is not None else self.height,
            frame=self.frame,
            legend=None if self.mpl_axes.get_legend() is not None else False,
            x_axis=x_axis,
            y_axis=AxisDataType.continuous(label=self.y_label, scale=self.y_scale, share_group=self.share_y),
        )


class CapturingFigure:
    """The `(fig, axes)` pair `subplots()` below gives: a real matplotlib
    `Figure` plus every one of its Axes already wrapped as a `CapturingAxes`.

    A builder that doesn't need a `LimelightProject` at all - just data and
    maybe an output path for a preview - can set this figure's own id/title/
    etc with `configure()` and return it; the caller then just says
    `project.add_figure(cap_fig)`. `add_to`/`add_figure` both fill in
    anything left unset from whatever `configure()` already set, so either
    style (configure now, or pass everything at the call site) works.
    """

    def __init__(
        self,
        mpl_figure: Any,
        panels: list[CapturingAxes],
        *,
        dataset: "CapturingDataset | None" = None,
    ) -> None:
        self.mpl_figure = mpl_figure
        self.panels = panels
        # The in-memory registry behind this figure's data. Two figures
        # built from the same CapturingDataset (pass it to both their
        # subplots() calls) share their data and their dataset_id - see
        # that class.
        self.dataset = dataset if dataset is not None else (panels[0].dataset if panels else CapturingDataset())
        self.id: str | None = None
        self.title: str | None = None
        self.caption: str | None = None
        self.size: Any | None = None
        self.provenance: Any | None = None

    def _follow_the_real_figure(self) -> None:
        """Takes what the matplotlib figure itself says - however its Axes
        were made (`subplots()`, or `plt.subplot()` under `capture()`) - about
        the panels' order, which axes are linked, and their heights."""

        order = {id(axes): index for index, axes in enumerate(self.mpl_figure.axes)}
        self.panels.sort(key=lambda panel: order.get(id(panel.mpl_axes), len(order)))
        by_axes = {id(panel.mpl_axes): panel for panel in self.panels}
        for panel in self.panels:
            x_linked = [by_axes[id(a)] for a in panel.mpl_axes.get_shared_x_axes().get_siblings(panel.mpl_axes) if id(a) in by_axes]
            if len(x_linked) > 1 and all(p.share_x.startswith("panel-") for p in x_linked):
                group = min(p.share_x for p in x_linked)
                for linked in x_linked:
                    linked.share_x = group
            y_linked = [by_axes[id(a)] for a in panel.mpl_axes.get_shared_y_axes().get_siblings(panel.mpl_axes) if id(a) in by_axes]
            if len(y_linked) > 1 and all(p.share_y is None for p in y_linked):
                group = f"share-y-{next(_share_counter)}"
                for linked in y_linked:
                    linked.share_y = group
            spec = panel.mpl_axes.get_subplotspec()
            if panel.height is None and spec is not None:
                ratios = spec.get_gridspec().get_height_ratios()
                if ratios is not None:
                    panel.height = float(ratios[spec.rowspan.start])

    def _needs_frames(self) -> bool:
        """A single column is stacked, as the app stacks panels; anything
        else - a grid, or Axes placed by hand - keeps matplotlib's layout."""

        for panel in self.panels:
            spec = panel.mpl_axes.get_subplotspec()
            if spec is None or spec.get_gridspec().ncols > 1:
                return True
        return False

    def place_panels(self) -> None:
        """Sets each panel's `frame` to where matplotlib laid it out - after
        any `tight_layout()` or layout engine. A colour bar goes beside the
        frame in the interactive figure, as it does here."""

        self.mpl_figure.draw_without_rendering()
        for panel in self.panels:
            left, bottom, width, height = panel.mpl_axes.get_position().bounds
            panel.frame = (float(left), float(bottom), float(width), float(height))

    @property
    def dataset_id(self) -> str | None:
        return self.dataset.dataset_id

    @dataset_id.setter
    def dataset_id(self, value: str | None) -> None:
        self.dataset.dataset_id = value

    def name_for(self, values: Sequence[Any]) -> str:
        """The column name this figure's dataset registered `values` under -
        see `CapturingDataset.name_for`. Only needed for something that still
        wants the name explicitly (a hand-built `Panel`, say); a second
        figure built from the same `CapturingDataset` (passed to both
        figures' `subplots()` calls) resolves this on its own, since
        plotting the same array object there already dedupes to this name.
        """

        return self.dataset.name_for(values)

    def configure(
        self,
        *,
        id: str | None = None,
        title: str | None = None,
        caption: str | None = None,
        size: Any | None = None,
        dataset_id: str | None = None,
        provenance: Any | None = None,
    ) -> "CapturingFigure":
        if id is not None:
            self.id = id
        if title is not None:
            self.title = title
        if caption is not None:
            self.caption = caption
        if size is not None:
            self.size = size
        if dataset_id is not None:
            self.dataset_id = dataset_id
        if provenance is not None:
            self.provenance = provenance
        return self

    def view_action(self, panel_index: int, action: Any) -> Any:
        """A `FigureViewAction` aimed at `self.panels[panel_index]`, without
        having to know or compute the panel's id string yourself. Needs
        `configure(id=...)` (or an `id` given to `add_to`/`add_figure`)
        first, since a panel's id is `<figure id>-plot<n>` by default."""

        from .writer import FigureViewAction, panel_axes_id

        if self.id is None:
            raise ValueError("view_action needs this figure's id - call configure(id=...) first")
        panel = self.panels[panel_index]
        panel_id = panel.panel_id or panel_axes_id(self.id, panel_index + 1)
        return FigureViewAction(ref=panel_id, action=action)

    def view(self, id: str | None = None) -> Any:
        """A `FigureViewBuilder` for this figure - `.xlim()`, `.shade_regions()`,
        `.hline()`/`.vline()`, `.annotate()`, chained, instead of building a
        raw `figure_view_action(...)` list by hand. Needs `configure(id=...)`
        (or an `id` already given to `add_to`/`add_figure`) first.

        Its methods take `panel=1` (the first) by default, matching this
        figure's own panels in the order they were captured; a panel given
        an explicit `panel_id` of its own isn't resolved by panel number
        here - use `view_action` for that case.
        """

        from .writer import figure_view_builder

        if self.id is None:
            raise ValueError("view() needs this figure's id - call configure(id=...) first")
        return figure_view_builder(self.id, id=id)

    def savefig(self, path: Any, **kwargs: Any) -> None:
        self.mpl_figure.savefig(path, **kwargs)

    def add_to(
        self,
        project: Any,
        *,
        id: str | None = None,
        title: str | None = None,
        caption: str | None = None,
        size: Any | None = None,
        dataset_id: str | None = None,
        provenance: Any | None = None,
    ) -> Any:
        self.configure(id=id, title=title, caption=caption, size=size, dataset_id=dataset_id, provenance=provenance)
        if self.id is None or self.title is None:
            raise ValueError("add_to/add_figure needs an id and a title, directly or via configure()")
        if self.dataset_id is None:
            # add_mpl_figure's own default, written back so name_for's
            # caller can read the dataset id this figure actually ended up
            # registered under, rather than guess it matches that default.
            self.dataset_id = f"{self.id}-data"
        self._follow_the_real_figure()
        if self._needs_frames():
            self.place_panels()
        figure_spec = project.add_mpl_figure(
            id=self.id,
            title=self.title,
            caption=self.caption,
            size=self.size,
            panels=self.panels,
            dataset_id=self.dataset_id,
            provenance=self.provenance,
        )
        for panel in self.panels:
            panel._added_as = self.id
        return figure_spec

    def __getattr__(self, name: str) -> Any:
        # Same passthrough as CapturingAxes: fig.tight_layout(), fig.suptitle(...),
        # fig.subplots_adjust(...) and the like just work, unrecorded.
        if name == "mpl_figure":
            raise AttributeError(name)
        return getattr(self.mpl_figure, name)


def _share_groups(nrows: int, ncols: int, share: Any, share_name: str | None, *, axis: str) -> np.ndarray:
    """Each panel's share group on one axis, as matplotlib's `sharex`/
    `sharey` links them: all panels (`True`/`"all"`), each column
    (`"col"`), each row (`"row"`), or none (`False`/`"none"`, unless
    `share_name` names one group for all). An unshared x-axis gets a group
    of its own, since a Limelight x-axis with none joins every other; an
    unshared y-axis is left with none."""

    token = next(_share_counter)
    base = share_name if share_name is not None else f"share-{axis}-{token}"
    groups = np.empty((nrows, ncols), dtype=object)
    for row in range(nrows):
        for column in range(ncols):
            if share is True or share == "all":
                groups[row, column] = base
            elif share == "col":
                groups[row, column] = f"{base}-col{column}"
            elif share == "row":
                groups[row, column] = f"{base}-row{row}"
            elif share_name is not None:
                groups[row, column] = share_name
            elif axis == "x":
                groups[row, column] = f"panel-{token}-{row}-{column}"
            else:
                groups[row, column] = None
    return groups


def subplots(
    nrows: int = 1,
    ncols: int = 1,
    *,
    sharex: bool | str = False,
    sharey: bool | str = False,
    share_x: str | None = None,
    share_y: str | None = None,
    squeeze: bool = True,
    dataset: "CapturingDataset | None" = None,
    **kwargs: Any,
) -> tuple[CapturingFigure, Any]:
    """`plt.subplots()`, with every returned Axes wrapped as a `CapturingAxes`.

    Returns matplotlib's own shape - a single Axes, or a numpy array of them
    squeezed as `nrows`/`ncols`/`squeeze` say - so existing code
    (`fig, (ax1, ax2) = subplots(2, 1)`, `axs[0, 1]`, `axs.flat`) is
    unaffected. `sharex`/`sharey` link the panels' axes in the real figure
    as they do in matplotlib (`True`, `"col"`, `"row"`); `share_x`/
    `share_y` name that group explicitly. A single column's `height_ratios`
    become the panels' heights; a grid of more than one column keeps the
    layout matplotlib gives it, each panel placed where the preview has it.

    `dataset`, given, is a `CapturingDataset` this figure's panels register
    their arrays into instead of a fresh one - pass the same `CapturingDataset`
    to two `subplots()` calls to make those two figures share their data
    (and, once added to a project, their `dataset_id`) rather than each
    capturing its own copy. Left unset, a new one is created.
    """

    mpl_fig, mpl_axes = _REAL_SUBPLOTS(nrows, ncols, sharex=sharex, sharey=sharey, squeeze=False, **kwargs)

    uncaptured = _Uncaptured("subplots")
    height_ratios = kwargs.get("height_ratios") or (kwargs.get("gridspec_kw") or {}).get("height_ratios")

    x_groups = _share_groups(nrows, ncols, sharex, share_x, axis="x")
    y_groups = _share_groups(nrows, ncols, sharey, share_y, axis="y")
    dataset = dataset if dataset is not None else CapturingDataset()
    wrapped = np.empty((nrows, ncols), dtype=object)
    panels: list[CapturingAxes] = []
    for row in range(nrows):
        for column in range(ncols):
            panel = CapturingAxes(mpl_axes[row, column], dataset=dataset, share_x=x_groups[row, column])
            panel.share_y = y_groups[row, column]
            if height_ratios is not None and ncols == 1:
                panel.height = float(height_ratios[row])
            wrapped[row, column] = panel
            panels.append(panel)
    uncaptured.report()

    capturing_fig = CapturingFigure(mpl_fig, panels, dataset=dataset)
    if squeeze:
        return capturing_fig, wrapped.item() if wrapped.size == 1 else wrapped.squeeze()
    return capturing_fig, wrapped


@contextlib.contextmanager
def capture() -> Iterator[list[CapturingFigure]]:
    """Turns an existing, unmodified matplotlib debug script into one that
    also records Limelight figures, with no change inside the block at all:

        from limelight import mpl_capture

        with mpl_capture.capture() as figures:
            fig, ax = plt.subplots()          # literally unmodified
            ax.plot(x, y, "r--")              # literally unmodified
            plt.show()                        # still works - real Axes underneath

        project.add_figure(figures[0].configure(id="...", title="..."))

    Patches `plt.subplots` for the block's duration only, to return the same
    `(CapturingFigure, wrapped axes)` pair `subplots()` above does; any other
    matplotlib call the script makes (`ax.text`, `fig.tight_layout`,
    `plt.show`, ...) is untouched, including `plt.show()` - the Figure/Axes
    `subplots()` builds underneath are the real ones pyplot already tracks,
    wrapped or not. Yields the `CapturingFigure`s produced, in creation
    order, so you can pick which ones (if any) to pull into a project
    afterwards - the rest stay exactly what they were: plots you looked at
    while debugging.

    pyplot's own drawing functions (`plt.plot`, `plt.scatter`, `plt.xlim`,
    `plt.title`, ...) are captured too, on whatever Axes is current - so
    `plt.figure(); plt.subplot(211); plt.plot(...)` scripts work as well.
    Axes capture cannot see - made with `fig.add_subplot()`/`fig.add_axes()`
    and drawn on directly, or a `twinx()` - still draw, and an
    `MplCaptureWarning` at the end of the block says they were left out.
    """

    captured: list[CapturingFigure] = []
    wrappers: dict[int, CapturingAxes] = {}
    figures: dict[int, CapturingFigure] = {}
    figures_before = set(_plt.get_fignums())

    def adopt(fig: CapturingFigure) -> None:
        captured.append(fig)
        figures[id(fig.mpl_figure)] = fig
        for panel in fig.panels:
            wrappers[id(panel.mpl_axes)] = panel

    def patched_subplots(*args: Any, **kwargs: Any) -> Any:
        fig, axes = subplots(*args, **kwargs)
        adopt(fig)
        return fig, axes

    def current_axes() -> CapturingAxes | None:
        """The CapturingAxes for pyplot's current Axes, wrapping it (and its
        figure) the first time - so `plt.figure(); plt.subplot(211);
        plt.plot(...)` is captured too. None for a twin (`twinx`), which
        overlays another panel rather than being one."""

        axes = _plt.gca()
        wrapper = wrappers.get(id(axes))
        if wrapper is not None:
            return wrapper
        if any(other is not axes and other.get_position().bounds == axes.get_position().bounds for other in axes.figure.axes):
            return None
        fig = figures.get(id(axes.figure))
        if fig is None:
            fig = CapturingFigure(axes.figure, [], dataset=CapturingDataset())
            adopt(fig)
        wrapper = CapturingAxes(axes, dataset=fig.dataset)
        fig.panels.append(wrapper)
        wrappers[id(axes)] = wrapper
        return wrapper

    def routed(pyplot_name: str, method: str, original: Any) -> Any:
        @functools.wraps(original)
        def call(*args: Any, **kwargs: Any) -> Any:
            if pyplot_name in ("xlim", "ylim") and not args and not kwargs:
                return original()
            target = current_axes()
            if target is None:
                _warn(f"plt.{pyplot_name}: drawing on a twinned Axes is not captured - it draws in the preview only")
                return original(*args, **kwargs)
            result = getattr(target, method)(*args, **kwargs)
            if pyplot_name == "scatter":
                # As pyplot.scatter does, so a bare plt.colorbar() finds it.
                _plt.sci(result)
            return result

        return call

    originals = {"subplots": _plt.subplots}
    _plt.subplots = patched_subplots
    for pyplot_name, method in _PYPLOT_ROUTES.items():
        originals[pyplot_name] = getattr(_plt, pyplot_name)
        setattr(_plt, pyplot_name, routed(pyplot_name, method, originals[pyplot_name]))
    try:
        yield captured
    finally:
        for pyplot_name, original in originals.items():
            setattr(_plt, pyplot_name, original)
        _warn_about_uncaptured_drawing(figures_before, wrappers)


def _warn_about_uncaptured_drawing(figures_before: set[int], wrappers: dict[int, CapturingAxes]) -> None:
    """Anything drawn, during a `capture()` block, on Axes that capture never
    saw - made by `fig.add_subplot`, `fig.add_axes`, `ax.twinx`, ... - is in
    no captured figure: say so, rather than leave it to be found missing."""

    for number in _plt.get_fignums():
        if number in figures_before:
            continue
        manager = _pylab_helpers.Gcf.get_fig_manager(number)
        if manager is None:
            continue
        for axes in manager.canvas.figure.axes:
            if id(axes) in wrappers or getattr(axes, "_colorbar", None) is not None:
                continue
            if axes.lines or axes.collections or axes.patches or axes.images or axes.texts:
                _warn(
                    f"capture(): figure {number} has Axes drawn on through calls capture() does not see "
                    "(fig.add_subplot, fig.add_axes, ax.twinx, ...) - they are in no captured figure"
                )
                break
