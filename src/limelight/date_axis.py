"""Date axes: as many ticks as fit, each labelled no more fully than it needs.

Two pieces, for any matplotlib x-axis whose values are dates:

* `FittingDateLocator` places matplotlib's usual round-number date ticks
  (whole years, months, days, hours, ...) but no more of them than the axis
  has room for: it measures the labels the formatter would give them and
  steps to a coarser interval until no two labels run into each other. A
  narrow story column gets fewer ticks than a full-width window, and a zoom
  or a resize recounts.

* `DateTickFormatter` labels the ticks at their own resolution - the year
  for yearly ticks, the month for monthly ones, the day, the time - and
  says the larger unit once, where it starts or changes, rather than on
  every tick: `Jan 2025 · Apr · Jul · Oct · Jan 2026`, or `12:00` over
  `5 Nov 2026` and then times alone until the day turns. Dates are written
  day-month-year with the month's short name, which reads the same to
  every reader.

`MonthOrdinalLocator` and `MonthOrdinalFormatter` do the same for an axis
counted in months since January 1970 (a monthly calendar series).
"""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Any, Sequence

import matplotlib.dates as mdates
import numpy as np
from matplotlib.textpath import TextToPath
from matplotlib.ticker import Formatter, Locator

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MAX_TICKS = 9
# Space kept clear between neighbouring labels, in multiples of the label
# font's size: about the width of two characters.
LABEL_GAP_EM = 1.2

_TEXT_TO_PATH = TextToPath()


# --- labels -------------------------------------------------------------------


def day_label(when: datetime) -> str:
    return f"{when.day} {MONTHS[when.month - 1]} {when.year}"


def _is_midnight(when: datetime) -> bool:
    return when.hour == 0 and when.minute == 0 and when.second == 0 and when.microsecond == 0


def tick_resolution(ticks: Sequence[datetime], span_days: float = 0.0) -> str:
    """The finest unit the ticks need: "year", "month", "day", "minute", "second" or "millisecond".

    Ticks all on midnight are dates - unless the view is under a day and
    there is only the one, which is then a time like any other.
    """
    if (len(ticks) > 1 or span_days >= 1.0) and ticks and all(_is_midnight(tick) for tick in ticks):
        if all(tick.month == 1 and tick.day == 1 for tick in ticks):
            return "year"
        if all(tick.day == 1 for tick in ticks):
            return "month"
        return "day"
    if any(tick.microsecond for tick in ticks):
        return "millisecond"
    if any(tick.second for tick in ticks):
        return "second"
    return "minute"


def date_tick_labels(ticks: Sequence[datetime], span_days: float = 0.0) -> list[str]:
    """Each tick's label: its own unit, and the larger one where it starts or changes.

    The first tick always carries the larger unit, so the axis reads from
    its left end; after that it appears only when it turns.
    """
    resolution = tick_resolution(ticks, span_days)
    if len(ticks) == 1 and resolution in ("year", "month", "day"):
        # A lone tick has no neighbours to say which unit it counts in.
        return [day_label(ticks[0])]
    labels: list[str] = []
    previous: datetime | None = None
    for tick in ticks:
        month = MONTHS[tick.month - 1]
        new_year = previous is None or tick.year != previous.year
        if resolution == "year":
            label = str(tick.year)
        elif resolution == "month":
            label = f"{month} {tick.year}" if new_year else month
        elif resolution == "day":
            label = f"{tick.day} {month}" + (f"\n{tick.year}" if new_year else "")
        else:
            if resolution == "millisecond":
                time = tick.strftime("%H:%M:%S.%f")[:-3]
            elif resolution == "second":
                time = tick.strftime("%H:%M:%S")
            else:
                time = tick.strftime("%H:%M")
            new_day = previous is None or tick.date() != previous.date()
            label = time + (f"\n{day_label(tick)}" if new_day else "")
        labels.append(label)
        previous = tick
    return labels


def _in_view(values: Sequence[float], axis: Any) -> list[float]:
    """The values inside the axis' view: a locator also returns ticks just beyond it, never drawn."""
    if axis is None:
        return list(values)
    lower, upper = sorted(axis.get_view_interval())
    span = upper - lower
    slack = span * 1e-9
    return [value for value in values if lower - slack <= value <= upper + slack]


class DateTickFormatter(Formatter):
    """Labels date ticks at their resolution; see `date_tick_labels`."""

    def format_ticks(self, values: Sequence[float]) -> list[str]:
        values = list(values)
        axis = getattr(self, "axis", None)
        visible = set(_in_view(values, axis))
        shown = [value for value in values if value in visible]
        span_days = abs(float(np.subtract(*axis.get_view_interval()))) if axis is not None else 0.0
        labels = dict(zip(shown, date_tick_labels([mdates.num2date(value) for value in shown], span_days)))
        return [labels.get(value, "") for value in values]

    def __call__(self, value: float, pos: int | None = None) -> str:
        return self.format_data(value)

    def format_data(self, value: float) -> str:
        """One value on its own (a cursor read-out): the whole date and time."""
        when = mdates.num2date(value)
        if _is_midnight(when):
            return day_label(when)
        return f"{day_label(when)} {when.strftime('%H:%M:%S')}"

    def format_data_short(self, value: float) -> str:
        return self.format_data(value)


# --- fitting the ticks to the axis ----------------------------------------------


def _tick_label(axis: Any) -> Any:
    """A tick label of the axis, as its labels are drawn: its font and size.

    `axis.majorTicks[0]` makes a tick without asking the locator where the
    ticks are - `get_major_ticks()` would, and the locator is what is asking.
    """
    return axis.majorTicks[0].label1


def _dpi(axis: Any) -> float:
    return axis.get_figure(root=True).dpi


def _label_width_px(label: str, axis: Any) -> float:
    """How wide a label is drawn, in pixels: its widest line, in the tick labels' font."""
    prop = _tick_label(axis).get_fontproperties()
    dpi = _dpi(axis)
    widest = 0.0
    for line in label.split("\n"):
        if line:
            width_pt, _, _ = _TEXT_TO_PATH.get_text_width_height_descent(line, prop, ismath=False)
            widest = max(widest, width_pt)
    return widest * dpi / 72.0


def labels_fit(values: Sequence[float], labels: Sequence[str], axis: Any) -> bool:
    """Whether labels at these values leave a gap between every neighbouring pair."""
    if len(values) < 2:
        return True
    axes = axis.axes
    lower, upper = axis.get_view_interval()
    if upper == lower:
        return True
    extent = axes.get_window_extent()
    length_px = extent.width if axis is axes.xaxis else extent.height
    if length_px <= 0:
        return True
    gap_px = LABEL_GAP_EM * _tick_label(axis).get_fontsize() * _dpi(axis) / 72.0
    widths = [_label_width_px(label, axis) for label in labels]
    positions = [(value - lower) / (upper - lower) * length_px for value in values]
    for (x0, w0), (x1, w1) in zip(zip(positions, widths), zip(positions[1:], widths[1:])):
        if abs(x1 - x0) < (w0 + w1) / 2.0 + gap_px:
            return False
    return True


class FittingDateLocator(mdates.AutoDateLocator):
    """matplotlib's automatic date ticks, thinned until their labels fit (see the module)."""

    def __init__(self, max_ticks: int = MAX_TICKS) -> None:
        super().__init__(minticks=min(5, max_ticks), maxticks=max_ticks, interval_multiples=True)
        self._max_ticks = max_ticks

    def __call__(self) -> np.ndarray:
        # AutoDateLocator's own __call__ goes straight to an inner locator,
        # past tick_values; this is that path, fitted.
        dmin, dmax = self.viewlim_to_dt()
        return self._fitted(lambda: self.get_locator(dmin, dmax)())

    def tick_values(self, vmin: Any, vmax: Any) -> np.ndarray:
        return self._fitted(lambda: super(FittingDateLocator, self).tick_values(vmin, vmax))

    def _fitted(self, ticks_now: Any) -> np.ndarray:
        """The densest of matplotlib's tick choices, from MAX_TICKS down, whose labels fit."""
        axis = getattr(self, "axis", None)
        formatter = axis.get_major_formatter() if axis is not None else None
        best: np.ndarray | None = None
        for count in range(self._max_ticks, 1, -1):
            # Room below the cap lets matplotlib step up a unit (seconds to
            # minutes) rather than stretch an interval it has no round value for.
            self.minticks = max(2, min(5, count - 2))
            self.maxticks = {frequency: count for frequency in self.maxticks}
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                ticks = np.asarray(ticks_now())
            if caught:
                # No round interval at this count: matplotlib would fall back
                # to an arbitrary one, off whole units. Not a choice to offer.
                continue
            best = ticks
            if axis is None or not isinstance(formatter, DateTickFormatter):
                break
            shown = _in_view(list(ticks), axis)
            if labels_fit(shown, formatter.format_ticks(shown), axis):
                break
        else:
            # Nothing matplotlib offers fits (a very narrow axis): keep every
            # second, third, ... of the coarsest round ticks until it does.
            if best is not None and axis is not None and isinstance(formatter, DateTickFormatter):
                shown = _in_view(list(best), axis)
                for stride in range(2, max(len(shown), 2) + 1):
                    thinned = shown[::stride]
                    if labels_fit(thinned, formatter.format_ticks(thinned), axis):
                        return np.asarray(thinned)
        if best is None:
            self.minticks, self.maxticks = 5, {frequency: self._max_ticks for frequency in self.maxticks}
            best = np.asarray(ticks_now())
        return best


def format_date_axis(axis: Any) -> None:
    """Give a date axis fitting ticks and resolution labels."""
    axis.set_major_formatter(DateTickFormatter())
    axis.set_major_locator(FittingDateLocator())


# --- months since January 1970 ------------------------------------------------

# Steps between month ticks, in months: a tick every month, two, quarter,
# half-year, year, and then whole numbers of years.
_MONTH_STEPS = (1, 2, 3, 6, 12, 24, 60, 120, 240, 600, 1200)


def month_ordinal_labels(ordinals: Sequence[int]) -> list[str]:
    """Labels for month ticks: the year alone at yearly steps, else the month, with its year where it changes."""
    if len(ordinals) > 1 and all(ordinal % 12 == 0 for ordinal in ordinals):
        return [str(1970 + ordinal // 12) for ordinal in ordinals]
    labels: list[str] = []
    previous_year: int | None = None
    for ordinal in ordinals:
        year, month = 1970 + ordinal // 12, MONTHS[ordinal % 12]
        labels.append(f"{month} {year}" if year != previous_year else month)
        previous_year = year
    return labels


class MonthOrdinalFormatter(Formatter):
    def format_ticks(self, values: Sequence[float]) -> list[str]:
        values = list(values)
        visible = set(_in_view(values, getattr(self, "axis", None)))
        shown = [value for value in values if value in visible]
        labels = dict(zip(shown, month_ordinal_labels([round(value) for value in shown])))
        return [labels.get(value, "") for value in values]

    def __call__(self, value: float, pos: int | None = None) -> str:
        ordinal = round(value)
        return f"{MONTHS[ordinal % 12]} {1970 + ordinal // 12}"


class MonthOrdinalLocator(Locator):
    """Whole months, at the finest step from `_MONTH_STEPS` whose labels fit, aligned to January."""

    def __call__(self) -> np.ndarray:
        lower, upper = self.axis.get_view_interval()
        return self.tick_values(lower, upper)

    def tick_values(self, vmin: float, vmax: float) -> np.ndarray:
        lower, upper = sorted((vmin, vmax))
        axis = getattr(self, "axis", None)
        formatter = axis.get_major_formatter() if axis is not None else None
        ticks = np.array([])
        for step in _MONTH_STEPS:
            first = int(np.ceil(lower / step)) * step
            ticks = np.arange(first, np.floor(upper) + 1, step, dtype=float)
            if len(ticks) > MAX_TICKS:
                continue
            if axis is None or not isinstance(formatter, MonthOrdinalFormatter):
                break
            if labels_fit(list(ticks), formatter.format_ticks(list(ticks)), axis):
                break
        return ticks


def format_month_ordinal_axis(axis: Any) -> None:
    axis.set_major_formatter(MonthOrdinalFormatter())
    axis.set_major_locator(MonthOrdinalLocator())
