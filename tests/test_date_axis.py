"""Date axes: as many ticks as fit, each labelled at its own resolution, the
larger unit said where it starts or changes, dates written day-month-year.

The fitting is checked the way a reader sees it: the drawn labels' boxes
must not touch, over spans from milliseconds to centuries and widths from a
narrow column to a wide window."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from limelight.date_axis import (  # noqa: E402
    DateTickFormatter,
    date_tick_labels,
    format_date_axis,
    format_month_ordinal_axis,
    month_ordinal_labels,
)

UTC = timezone.utc


def _at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


@pytest.mark.parametrize(
    ("ticks", "expected"),
    [
        # Years: the year alone.
        ([_at(2028, 1, 1), _at(2032, 1, 1), _at(2036, 1, 1)], ["2028", "2032", "2036"]),
        # Months: the month, with its year where the year starts or turns.
        ([_at(2024, 10, 1), _at(2025, 1, 1), _at(2025, 4, 1)], ["Oct 2024", "Jan 2025", "Apr"]),
        # Days: day and month, the year once below and again at a new year.
        ([_at(2026, 12, 15), _at(2027, 1, 1), _at(2027, 1, 15)], ["15 Dec\n2026", "1 Jan\n2027", "15 Jan"]),
        # Times: the date under the first tick and wherever the day changes.
        ([_at(2026, 11, 5, 12), _at(2026, 11, 5, 18), _at(2026, 11, 6, 0), _at(2026, 11, 6, 6)],
         ["12:00\n5 Nov 2026", "18:00", "00:00\n6 Nov 2026", "06:00"]),
        # Seconds and milliseconds only when a tick needs them.
        ([_at(2026, 11, 5, 8, 3, 0), _at(2026, 11, 5, 8, 3, 30)], ["08:03:00\n5 Nov 2026", "08:03:30"]),
        ([_at(2026, 11, 5, 8, 2, 46, 500_000), _at(2026, 11, 5, 8, 2, 47)],
         ["08:02:46.500\n5 Nov 2026", "08:02:47.000"]),
    ],
)
def test_ticks_are_labelled_at_their_resolution(ticks, expected) -> None:
    assert date_tick_labels(ticks) == expected


def test_a_lone_midnight_tick_says_its_whole_date() -> None:
    assert date_tick_labels([_at(2027, 1, 1)], span_days=92) == ["1 Jan 2027"]
    # Under a day, a midnight is a time like any other.
    assert date_tick_labels([_at(2027, 1, 1)], span_days=0.5) == ["00:00\n1 Jan 2027"]


def test_month_ordinals_read_as_years_or_months() -> None:
    jan_2016 = (2016 - 1970) * 12
    assert month_ordinal_labels([jan_2016, jan_2016 + 12, jan_2016 + 24]) == ["2016", "2017", "2018"]
    assert month_ordinal_labels([jan_2016 - 3, jan_2016, jan_2016 + 3]) == ["Oct 2015", "Jan 2016", "Apr"]


def test_a_single_value_reads_in_full() -> None:
    formatter = DateTickFormatter()
    assert formatter.format_data(mdates.date2num(_at(2026, 11, 5, 8, 3, 15))) == "5 Nov 2026 08:03:15"
    assert formatter.format_data(mdates.date2num(_at(2026, 11, 5))) == "5 Nov 2026"


def _drawn_labels(width_in: float, setup) -> tuple[list[str], bool]:
    fig, axes = plt.subplots(figsize=(width_in, 3), dpi=96, constrained_layout=True)
    try:
        setup(axes)
        fig.canvas.draw()
        labels = [label for label in axes.get_xticklabels() if label.get_text()]
        renderer = fig.canvas.get_renderer()
        boxes = [label.get_window_extent(renderer) for label in labels]
        overlap = any(left.x1 > right.x0 for left, right in zip(boxes, boxes[1:]))
        return [label.get_text() for label in labels], overlap
    finally:
        plt.close(fig)


START = _at(2026, 11, 5, 8, 2, 46, 123_456)
SPANS = [
    timedelta(milliseconds=50), timedelta(seconds=2), timedelta(minutes=2), timedelta(hours=2),
    timedelta(hours=36), timedelta(days=10), timedelta(days=92), timedelta(days=730),
    timedelta(days=365 * 30), timedelta(days=365 * 300),
]


@pytest.mark.parametrize("width_in", [2.5, 3.8, 7.0, 12.0])
@pytest.mark.parametrize("span", SPANS, ids=str)
def test_date_labels_never_touch(span: timedelta, width_in: float) -> None:
    def setup(axes) -> None:
        format_date_axis(axes.xaxis)
        axes.set_xlim(mdates.date2num(START), mdates.date2num(START + span))

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no arbitrary off-unit intervals either
        labels, overlap = _drawn_labels(width_in, setup)
    assert labels and not overlap, labels


@pytest.mark.parametrize("width_in", [2.5, 3.8, 7.0])
@pytest.mark.parametrize("months", [6, 30, 130, 600])
def test_month_labels_never_touch(months: int, width_in: float) -> None:
    def setup(axes) -> None:
        format_month_ordinal_axis(axes.xaxis)
        axes.set_xlim(670, 670 + months)

    labels, overlap = _drawn_labels(width_in, setup)
    assert labels and not overlap, labels


def test_a_wider_axis_gets_more_ticks() -> None:
    def setup(axes) -> None:
        format_date_axis(axes.xaxis)
        axes.set_xlim(mdates.date2num(START), mdates.date2num(START + timedelta(hours=2)))

    narrow, _ = _drawn_labels(3.8, setup)
    wide, _ = _drawn_labels(7.0, setup)
    assert len(wide) > len(narrow) >= 2
