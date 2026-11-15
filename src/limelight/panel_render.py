"""Pure matplotlib drawing for Panel artists and decorators.

This is the one place a line's draw style, fill, and decorator shapes are
defined. `qt_app.py` calls it to render a loaded package's figures;
`mpl_capture.py` calls it to draw a panel straight out of a script that is
building one, before that panel ever goes through a package's dhall
manifest. Both call sites end up drawing the same pixels from the same code.

Nothing here knows about `LimelightRuntime`, loaded packages, or the writer's
dataclasses - it only takes plain arrays and plain keyword styling, the
common ground between "an artist resolved from a manifest" and "an artist
object a script just built".
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
from matplotlib.patches import Rectangle

# A line artist's drawStyle, as matplotlib's drawstyle names it.
MPL_DRAW_STYLES = {"straight": "default", "stepsPre": "steps-pre", "stepsPost": "steps-post", "stepsMid": "steps-mid"}

# A filled line artist's drawStyle, as matplotlib's fill_between `step` names it.
MPL_FILL_STEPS: dict[str, str | None] = {"straight": None, "stepsPre": "pre", "stepsPost": "post", "stepsMid": "mid"}

DEFAULT_RECT_COLOR = "#f3d36b"
DEFAULT_RECT_ALPHA = 0.35
DEFAULT_DECORATOR_COLOR = "#1f5f73"
DEFAULT_SCATTER_EDGE_COLOR = "white"
DEFAULT_SCATTER_EDGE_WIDTH = 0.45


def draw_line(
    axes: Any,
    x_values: Sequence[float],
    y_values: Sequence[Any],
    *,
    label: str | None = None,
    color: str | None = None,
    linestyle: str | None = None,
    linewidth: float | None = None,
    draw_style: str | None = None,
    marker: str | None = None,
    alpha: float | None = None,
    fill_color: str | None = None,
    fill_alpha: float | None = None,
    fill_baseline: float = 0.0,
) -> Any:
    # A legend takes its handle from whichever artist carries the label. A
    # thin or zero-width line (the usual case once it's filled, for a
    # histogram-style look) makes a near-invisible swatch, so the label goes
    # on the fill instead when there is one - the legend then shows the
    # actual filled color, not a sliver of the line underneath it.
    (line,) = axes.plot(
        x_values,
        y_values,
        label=None if fill_color else label,
        color=color,
        linestyle=linestyle or "-",
        marker=marker,
        linewidth=2.0 if linewidth is None else linewidth,
        drawstyle=MPL_DRAW_STYLES[draw_style or "straight"],
        alpha=1.0 if alpha is None else alpha,
    )
    if fill_color:
        y_fill = np.array([np.nan if y is None else y for y in y_values], dtype=float)
        axes.fill_between(
            x_values,
            fill_baseline,
            y_fill,
            step=MPL_FILL_STEPS[draw_style or "straight"],
            color=fill_color,
            alpha=1.0 if fill_alpha is None else fill_alpha,
            linewidth=0,
            label=label,
        )
    return line


def draw_scatter(
    axes: Any,
    x_values: Sequence[float],
    y_values: Sequence[float],
    *,
    label: str | None = None,
    color: str | None = None,
    marker: str | None = None,
    alpha: float | None = None,
    size: float | Sequence[float] = 24.0,
    edge_color: str | None = None,
    edge_width: float | None = None,
    color_values: Sequence[float] | None = None,
) -> Any:
    # `color_values`, given, colours each point from the viridis colormap,
    # as a continuous colorBy column does.
    colour_kwargs: dict[str, Any] = {"c": color_values, "cmap": "viridis"} if color_values is not None else {"color": color}
    return axes.scatter(
        x_values,
        y_values,
        label=label,
        s=size,
        **colour_kwargs,
        marker=marker or "o",
        edgecolors=DEFAULT_SCATTER_EDGE_COLOR if edge_color is None else edge_color,
        linewidths=DEFAULT_SCATTER_EDGE_WIDTH if edge_width is None else edge_width,
        alpha=1.0 if alpha is None else alpha,
        zorder=3,
    )


def draw_stem(
    axes: Any,
    x_values: Sequence[float],
    y_values: Sequence[float],
    *,
    label: str | None = None,
    color: str | None = None,
    baseline: float = 0.0,
    alpha: float | None = None,
) -> tuple[Any, Any]:
    markerline, stemlines, _ = axes.stem(x_values, y_values, bottom=baseline, basefmt=" ")
    markerline.set_label(label)
    markerline.set_alpha(1.0 if alpha is None else alpha)
    stemlines.set_alpha(1.0 if alpha is None else alpha)
    if color:
        markerline.set_color(color)
        stemlines.set_color(color)
    return markerline, stemlines


def draw_hspan_decorator(
    axes: Any,
    y_window: tuple[float, float],
    *,
    color: str | None = None,
    alpha: float | None = None,
    linewidth: float | None = None,
    label: str | None = None,
    label_font: Any = None,
) -> Any:
    """Like `draw_vspan_decorator`, but a horizontal line/band at a y value
    or range instead of a vertical one at an x value/range."""

    lower, upper = y_window
    text_kwargs = {"fontproperties": label_font} if label_font is not None else {}
    if lower == upper:
        line_color = color or DEFAULT_DECORATOR_COLOR
        artist = axes.axhline(
            lower,
            color=line_color,
            linestyle=":",
            linewidth=1.7 if linewidth is None else linewidth,
            alpha=0.95 if alpha is None else alpha,
        )
        if label:
            axes.text(
                0.98,
                lower,
                label,
                ha="right",
                va="bottom",
                transform=axes.get_yaxis_transform(),
                color=line_color,
                **text_kwargs,
            )
        return artist
    artist = axes.axhspan(lower, upper, color=color or DEFAULT_RECT_COLOR, alpha=DEFAULT_RECT_ALPHA if alpha is None else alpha)
    if label:
        axes.text(
            0.96,
            sum(y_window) / 2,
            label,
            ha="right",
            va="center",
            transform=axes.get_yaxis_transform(),
            **text_kwargs,
        )
    return artist


def draw_rect_decorator(
    axes: Any,
    x_window: tuple[float, float],
    y_window: tuple[float, float],
    *,
    color: str | None = None,
    alpha: float | None = None,
    label: str | None = None,
    label_font: Any = None,
) -> Any:
    box = Rectangle(
        (x_window[0], y_window[0]),
        x_window[1] - x_window[0],
        y_window[1] - y_window[0],
        facecolor=color or DEFAULT_RECT_COLOR,
        edgecolor=color or DEFAULT_RECT_COLOR,
        alpha=DEFAULT_RECT_ALPHA if alpha is None else alpha,
        linewidth=1.2,
        # Under the data lines, as a VSpan is.
        zorder=1,
    )
    axes.add_artist(box)
    if label:
        text_kwargs = {"fontproperties": label_font} if label_font is not None else {}
        axes.text(
            (x_window[0] + x_window[1]) / 2,
            y_window[1],
            label,
            ha="center",
            va="bottom",
            color=DEFAULT_DECORATOR_COLOR,
            clip_on=True,
            **text_kwargs,
        )
    return box


def draw_vspan_decorator(
    axes: Any,
    x_window: tuple[float, float],
    *,
    color: str | None = None,
    alpha: float | None = None,
    linewidth: float | None = None,
    label: str | None = None,
    label_font: Any = None,
) -> Any:
    """`x_window` of equal bounds draws a single vertical marker line; unequal
    bounds shade the span between them. Returns the line or the shaded patch."""

    lower, upper = x_window
    text_kwargs = {"fontproperties": label_font} if label_font is not None else {}
    if lower == upper:
        line_color = color or DEFAULT_DECORATOR_COLOR
        artist = axes.axvline(
            lower,
            color=line_color,
            linestyle=":",
            linewidth=1.7 if linewidth is None else linewidth,
            alpha=0.95 if alpha is None else alpha,
        )
        if label:
            axes.text(
                lower,
                0.98,
                label,
                ha="right",
                va="top",
                rotation=90,
                transform=axes.get_xaxis_transform(),
                color=line_color,
                **text_kwargs,
            )
        return artist
    artist = axes.axvspan(lower, upper, color=color or DEFAULT_RECT_COLOR, alpha=DEFAULT_RECT_ALPHA if alpha is None else alpha)
    if label:
        axes.text(
            sum(x_window) / 2,
            0.96,
            label,
            ha="center",
            va="top",
            transform=axes.get_xaxis_transform(),
            **text_kwargs,
        )
    return artist
