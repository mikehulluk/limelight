"""Write normal matplotlib code; get a real interactive Limelight figure too.

`CapturingAxes` stands in for a matplotlib `Axes`. The handful of calls a
build script actually uses - `scatter`, `plot`, `fill_between` (as a
baseline fill following `plot`), `axvspan`/`axhspan`, `axvline`/`axhline`,
and the label/scale setters - each do two things from one call: draw on a
real `Axes` (so `plt.savefig()` gives an instant preview while iterating on),
and record the equivalent `ScatterArtist`/`LineArtist`/decorator (so
`LimelightProject.add_mpl_figure` can build the real, interactive figure
from the same calls once you're happy with them). Arrays passed to
`scatter`/`plot` become columns of a `CapturingDataset` shared by every
panel of one figure, named from `name=` or auto-numbered, deduplicated by
array identity - the same array reused across panels (a shared x-axis,
say) becomes one column, not a copy per panel.

This only covers the calls above - colorbars, 3D, and anything else written
directly against `self.mpl_axes` stays preview-only and has no interactive
equivalent.

`subplots()` is `plt.subplots()` itself, with every returned Axes already
wrapped: `fig, (ax1, ax2) = subplots(2, 1, sharex=True)` reads exactly like
the matplotlib call it replaces. `fig` comes back as a `CapturingFigure`,
which bundles every panel so one `fig.add_to(project, id=..., title=...)`
call replaces building a `panels=[...]` list by hand.
"""

from __future__ import annotations

import contextlib
import itertools
import math
import warnings
from typing import Any, Iterator, Sequence

import matplotlib.pyplot as _plt

from . import panel_render
from .writer import AxisDataType, LineArtist, Panel, ScatterArtist

# Captured once, at import time, before `capture()` (below) ever has a
# chance to replace `matplotlib.subplots` - `subplots()` always calls
# through this name, never the (possibly patched) live module attribute, so
# patching can never make it recurse into itself.
_REAL_SUBPLOTS = _plt.subplots

_MPL_TO_DRAW_STYLE = {
    None: "straight",
    "default": "straight",
    "steps": "stepsPre",
    "steps-pre": "stepsPre",
    "steps-post": "stepsPost",
    "steps-mid": "stepsPost",
}


def _normalize_draw_style(value: str | None) -> str:
    if value in ("straight", "stepsPre", "stepsPost"):
        return value
    return _MPL_TO_DRAW_STYLE.get(value, "straight")


def _looks_like_same_data(a: Sequence[Any], b: Sequence[Any]) -> bool:
    """A cheap, sampled equality check - same length, and a handful of
    positions (all of them, for a short array) either exactly equal or
    numerically close. Only meant to catch an accidental duplicate capture
    of the same data under a second name; a false negative (two equal
    arrays this misses) just means no warning, not a wrong result."""

    if len(a) != len(b) or len(a) == 0:
        return False
    indices = range(len(a)) if len(a) <= 20 else (0, len(a) // 4, len(a) // 2, (3 * len(a)) // 4, len(a) - 1)
    for i in indices:
        x, y = a[i], b[i]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12):
                return False
        elif x != y:
            return False
    return True


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

    `register()` is what `CapturingAxes.scatter()`/`.plot()` call to get a
    column name for an array, deduplicated by identity. `name_for()` is the
    reverse lookup - "what did you call this array" - for anything that
    still needs the name explicitly (building a `Panel` by hand, say).
    """

    def __init__(self) -> None:
        self.columns: dict[str, Any] = {}
        # The id a project actually registered this dataset under, written
        # back by the first `CapturingFigure.add_to` that uses this dataset;
        # every other figure sharing it then defaults to that same id
        # instead of each computing (and registering) its own.
        self.dataset_id: str | None = None
        self._by_identity: dict[int, str] = {}
        self._counter = itertools.count()

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

    def _warn_if_similar_to_existing(self, values: Sequence[Any], name: str | None) -> None:
        for existing_name, existing_values in self.columns.items():
            if existing_values is values:
                continue
            if _looks_like_same_data(values, existing_values):
                warnings.warn(
                    f"column {name or '<auto>'!r} looks like a duplicate of already-registered column "
                    f"{existing_name!r} - same length, same (or near-identical) values. If this is "
                    f"the same data, pass the same array object so it dedupes instead of being copied.",
                    stacklevel=4,
                )
                return


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
        # Mirrors matplotlib's own sharex=True on plt.subplots(): panels with
        # the same share_group pan/zoom together in the real figure too.
        self.share_x = share_x
        # An explicit Panel id, for view_action/to_panel; left unset, both
        # fall back to the figure's default <figure id>-plot<n> naming.
        self.panel_id: str | None = None
        self._last_line_by_identity: dict[int, LineArtist] = {}
        self._last_mpl_line_by_identity: dict[int, Any] = {}

    @property
    def columns(self) -> dict[str, Any]:
        return self.dataset.columns

    def _register(self, values: Sequence[Any], *, name: str | None) -> str:
        return self.dataset.register(values, name=name)

    def scatter(
        self,
        x: Sequence[float],
        y: Sequence[float],
        *,
        label: str | None = None,
        color: str | None = None,
        marker: str | None = None,
        alpha: float | None = None,
        s: float | None = None,
        edgecolors: str | None = None,
        linewidths: float | None = None,
        name: str | None = None,
        x_name: str | None = None,
        **_ignored: Any,
    ) -> Any:
        x_col = self._register(x, name=x_name if x_name is not None else (f"{name}_x" if name is not None else None))
        y_col = self._register(y, name=name)
        self.scatters.append(
            ScatterArtist(
                array=y_col,
                x=x_col,
                label=label,
                color=color,
                marker=marker,
                edge_color=edgecolors,
                edge_width=linewidths,
            )
        )
        return panel_render.draw_scatter(
            self.mpl_axes,
            x,
            y,
            label=label,
            color=color,
            marker=marker,
            alpha=alpha,
            size=s or 24.0,
            edge_color=edgecolors,
            edge_width=linewidths,
        )

    def plot(
        self,
        x: Sequence[float],
        y: Sequence[Any],
        *,
        label: str | None = None,
        color: str | None = None,
        linestyle: str | None = None,
        linewidth: float | None = None,
        marker: str | None = None,
        alpha: float | None = None,
        drawstyle: str | None = None,
        fill_color: str | None = None,
        fill_alpha: float | None = None,
        fill_baseline: float = 0.0,
        name: str | None = None,
        x_name: str | None = None,
        **_ignored: Any,
    ) -> Any:
        x_col = self._register(x, name=x_name if x_name is not None else (f"{name}_x" if name is not None else None))
        y_col = self._register(y, name=name)
        draw_style = _normalize_draw_style(drawstyle)
        artist = LineArtist(
            array=y_col,
            x=x_col,
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
        self.lines.append(artist)
        self._last_line_by_identity[id(y)] = artist
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
        self._last_mpl_line_by_identity[id(y)] = line
        return line

    def fill_between(
        self,
        x: Sequence[float],
        y1: Sequence[float],
        y2: float = 0.0,
        *,
        color: str | None = None,
        alpha: float | None = None,
        step: str | None = None,
        **_ignored: Any,
    ) -> None:
        """Only a scalar baseline (`y2`) is supported, and only immediately
        after the `plot()` call that drew `y1` - it retrofits that line's
        fill rather than being its own artist, matching what `LineArtist`
        can express. For anything else, pass `fill_color`/`fill_alpha` to
        `plot()` directly instead."""

        if not isinstance(y2, (int, float)):
            raise NotImplementedError(
                "CapturingAxes.fill_between only supports a scalar baseline (y2); "
                "pass fill_color=/fill_alpha= to plot() instead for anything else"
            )
        artist = self._last_line_by_identity.get(id(y1))
        if artist is None:
            raise ValueError(
                "fill_between must immediately follow the plot() call that drew the same y data; "
                "pass fill_color=/fill_alpha=/fill_baseline= to plot() instead"
            )
        artist.fill_color = color
        artist.fill_alpha = alpha
        artist.fill_baseline = float(y2)
        draw_style = artist.draw_style or "straight"
        # Matches panel_render.draw_line: the legend's handle follows
        # whichever artist carries the label, so move it from the line (now
        # that it's filled, usually too thin for a legend swatch) onto the
        # fill, which is the one that visually matches the filled color.
        mpl_line = self._last_mpl_line_by_identity.get(id(y1))
        if mpl_line is not None:
            mpl_line.set_label(None)
        self.mpl_axes.fill_between(
            x,
            float(y2),
            y1,
            step=panel_render.MPL_FILL_STEPS[draw_style],
            color=color,
            alpha=1.0 if alpha is None else alpha,
            linewidth=0,
            label=artist.label,
        )

    def axvspan(self, xmin: float, xmax: float, *, color: str | None = None, alpha: float | None = None, label: str | None = None, **_ignored: Any) -> None:
        from .writer import axes_action_add_axis_window_decorator

        self.actions.append(
            axes_action_add_axis_window_decorator(lower=xmin, upper=xmax, label=label, color=color, alpha=alpha)
        )
        panel_render.draw_vspan_decorator(self.mpl_axes, (xmin, xmax), color=color, alpha=alpha, label=label)

    def axhspan(self, ymin: float, ymax: float, *, color: str | None = None, alpha: float | None = None, label: str | None = None, **_ignored: Any) -> None:
        from .writer import axes_action_add_y_axis_window_decorator

        self.actions.append(
            axes_action_add_y_axis_window_decorator(lower=ymin, upper=ymax, label=label, color=color, alpha=alpha)
        )
        panel_render.draw_hspan_decorator(self.mpl_axes, (ymin, ymax), color=color, alpha=alpha, label=label)

    def axvline(
        self,
        x: float,
        *,
        color: str | None = None,
        alpha: float | None = None,
        linewidth: float | None = None,
        label: str | None = None,
        **_ignored: Any,
    ) -> None:
        from .writer import axes_action_add_axis_window_decorator

        self.actions.append(
            axes_action_add_axis_window_decorator(
                lower=x, upper=x, label=label, color=color, alpha=alpha, linewidth=linewidth
            )
        )
        panel_render.draw_vspan_decorator(self.mpl_axes, (x, x), color=color, alpha=alpha, linewidth=linewidth, label=label)

    def axhline(
        self,
        y: float,
        *,
        color: str | None = None,
        alpha: float | None = None,
        linewidth: float | None = None,
        label: str | None = None,
        **_ignored: Any,
    ) -> None:
        from .writer import axes_action_add_y_axis_window_decorator

        self.actions.append(
            axes_action_add_y_axis_window_decorator(
                lower=y, upper=y, label=label, color=color, alpha=alpha, linewidth=linewidth
            )
        )
        panel_render.draw_hspan_decorator(self.mpl_axes, (y, y), color=color, alpha=alpha, linewidth=linewidth, label=label)

    def set_xlim(self, lower: float, upper: float, **_ignored: Any) -> None:
        from .writer import axes_action_set_x_float_limits

        self.actions.append(axes_action_set_x_float_limits(lower, upper))
        self.mpl_axes.set_xlim(lower, upper)

    def set_ylim(self, lower: float, upper: float, **_ignored: Any) -> None:
        from .writer import axes_action_set_y_float_limits

        self.actions.append(axes_action_set_y_float_limits(lower, upper))
        self.mpl_axes.set_ylim(lower, upper)

    def set_xlabel(self, label: str, **_ignored: Any) -> None:
        self.x_label = label
        self.mpl_axes.set_xlabel(label)

    def set_ylabel(self, label: str, **_ignored: Any) -> None:
        self.y_label = label
        self.mpl_axes.set_ylabel(label)

    def set_title(self, title: str, **_ignored: Any) -> None:
        self.title = title
        self.mpl_axes.set_title(title)

    def set_yscale(self, scale: str, **_ignored: Any) -> None:
        self.y_scale = "Log" if scale == "log" else "Linear"
        self.mpl_axes.set_yscale(scale)

    def set_xscale(self, scale: str, **_ignored: Any) -> None:
        self.x_scale = "Log" if scale == "log" else "Linear"
        self.mpl_axes.set_xscale(scale)

    def legend(self, *args: Any, **kwargs: Any) -> Any:
        return self.mpl_axes.legend(*args, **kwargs)

    def grid(self, *args: Any, **kwargs: Any) -> Any:
        return self.mpl_axes.grid(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Anything not explicitly captured above (set_xlim, text, annotate,
        # bar, hist, tick_params, twinx, ...) passes straight through to the
        # real Axes: it draws for real - an existing debug script keeps
        # working untouched, wrapped or not - it just isn't recorded as a
        # Limelight artist. __getattr__ only fires once normal lookup (this
        # class's own methods, then instance attributes) has already failed,
        # so this never shadows anything defined above.
        return getattr(self.mpl_axes, name)

    def to_panel(self, *, data: str, height: float | None = None) -> Panel:
        return Panel(
            lines=self.lines,
            scatters=self.scatters,
            actions=self.actions,
            data=data,
            title=self.title,
            id=self.panel_id,
            height=height,
            x_axis=AxisDataType.continuous(label=self.x_label, scale=self.x_scale, share_group=self.share_x),
            y_axis=AxisDataType.continuous(label=self.y_label, scale=self.y_scale),
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
        """`figure_view_action(<that panel's id>, action)`, without having to
        know or compute the id string yourself. Needs `configure(id=...)`
        (or an `id` given to `add_to`/`add_figure`) first, since a panel's id
        is `<figure id>-plot<n>` by default."""

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
        return project.add_mpl_figure(
            id=self.id,
            title=self.title,
            caption=self.caption,
            size=self.size,
            panels=self.panels,
            dataset_id=self.dataset_id,
            provenance=self.provenance,
        )

    def __getattr__(self, name: str) -> Any:
        # Same passthrough as CapturingAxes: fig.tight_layout(), fig.suptitle(...),
        # fig.subplots_adjust(...) and the like just work, unrecorded.
        return getattr(self.mpl_figure, name)


_share_x_counter = itertools.count()


def subplots(
    nrows: int = 1,
    ncols: int = 1,
    *,
    sharex: bool = False,
    sharey: bool = False,
    share_x: str | None = None,
    squeeze: bool = True,
    dataset: "CapturingDataset | None" = None,
    **kwargs: Any,
) -> tuple[CapturingFigure, Any]:
    """`plt.subplots()`, with every returned Axes wrapped as a `CapturingAxes`.

    Shares matplotlib's own return shape (a single Axes, a flat list, or a
    nested list, depending on `nrows`/`ncols`/`squeeze`) so existing
    unpacking code (`fig, (ax1, ax2) = subplots(2, 1)`) is unaffected.
    `sharex=True` (matplotlib's own pan/zoom link) also links the panels in
    the real figure: `share_x` names that group explicitly; left unset, one
    is generated so the panels still share it without the caller having to
    name it.

    `dataset`, given, is a `CapturingDataset` this figure's panels register
    their arrays into instead of a fresh one - pass the same `CapturingDataset`
    to two `subplots()` calls to make those two figures share their data
    (and, once added to a project, their `dataset_id`) rather than each
    capturing its own copy. Left unset, a new one is created, as before.
    """

    mpl_fig, mpl_axes = _REAL_SUBPLOTS(nrows, ncols, sharex=sharex, sharey=sharey, squeeze=False, **kwargs)

    group = share_x if share_x is not None else (f"share-x-{next(_share_x_counter)}" if sharex else None)
    dataset = dataset if dataset is not None else CapturingDataset()

    wrapped_rows: list[list[CapturingAxes]] = []
    panels: list[CapturingAxes] = []
    for row in mpl_axes:
        wrapped_row = [CapturingAxes(mpl_ax, dataset=dataset, share_x=group) for mpl_ax in row]
        panels.extend(wrapped_row)
        wrapped_rows.append(wrapped_row)

    capturing_fig = CapturingFigure(mpl_fig, panels, dataset=dataset)

    if squeeze:
        if nrows == 1 and ncols == 1:
            return capturing_fig, wrapped_rows[0][0]
        if nrows == 1:
            return capturing_fig, wrapped_rows[0]
        if ncols == 1:
            return capturing_fig, [row[0] for row in wrapped_rows]
    return capturing_fig, wrapped_rows


@contextlib.contextmanager
def capture() -> Iterator[list[CapturingFigure]]:
    """Turns an existing, unmodified matplotlib debug script into one that
    also records Limelight figures, with no change inside the block at all:

        with mpl_capture.capture() as figures:
            fig, ax = plt.subplots()          # literally unmodified
            ax.scatter(x, y)                  # literally unmodified
            plt.show()                        # still works - real Axes underneath

        figures[0].configure(id="...", title="...")
        project.add_figure(figures[0])

    Patches `plt.subplots` for the block's duration only, to return the same
    `(CapturingFigure, wrapped axes)` pair `subplots()` above does; any other
    matplotlib call the script makes (`ax.set_xlim`, `fig.tight_layout`,
    `plt.show`, ...) is untouched, including `plt.show()` - the Figure/Axes
    `subplots()` builds underneath are the real ones pyplot already tracks,
    wrapped or not. Yields the `CapturingFigure`s produced, in creation
    order, so you can pick which ones (if any) to pull into a project
    afterwards - the rest stay exactly what they were: plots you looked at
    while debugging.

    Only `plt.subplots` is patched - `plt.figure()`/`fig.add_subplot()` and
    other figure-creation entry points aren't covered.
    """

    captured: list[CapturingFigure] = []

    def patched_subplots(*args: Any, **kwargs: Any) -> Any:
        fig, axes = subplots(*args, **kwargs)
        captured.append(fig)
        return fig, axes

    original_subplots = _plt.subplots
    _plt.subplots = patched_subplots
    try:
        yield captured
    finally:
        _plt.subplots = original_subplots
