"""A UTC axis labels every tick on two lines - the full time over the date -
so a label reads on its own wherever the view is; a view of whole days keeps
only the date, and a view of a few seconds shows milliseconds."""

from __future__ import annotations

from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pytest

from limelight.qt_app import _format_matplotlib_date_axis, _utc_tick_label


def _axes_over(start: datetime, end: datetime):
    fig, axes = plt.subplots()
    _format_matplotlib_date_axis(axes)
    axes.set_xlim(mdates.date2num(start), mdates.date2num(end))
    fig.canvas.draw()  # places the major ticks
    return fig, axes


@pytest.mark.parametrize(
    "start, end, at, expected",
    [
        # Minutes: full time over the date.
        (datetime(2026, 9, 20, 16, 1, tzinfo=timezone.utc), datetime(2026, 9, 20, 16, 4, tzinfo=timezone.utc),
         datetime(2026, 9, 20, 16, 2, 30, tzinfo=timezone.utc), "16:02:30\n2026-09-20"),
        # Seconds: milliseconds shown.
        (datetime(2026, 9, 20, 16, 2, 30, tzinfo=timezone.utc), datetime(2026, 9, 20, 16, 2, 32, tzinfo=timezone.utc),
         datetime(2026, 9, 20, 16, 2, 30, 250000, tzinfo=timezone.utc), "16:02:30.250\n2026-09-20"),
        # Weeks: every tick is a midnight, so the date alone.
        (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 30, tzinfo=timezone.utc),
         datetime(2026, 9, 14, tzinfo=timezone.utc), "2026-09-14"),
    ],
)
def test_tick_label_modes(start, end, at, expected) -> None:
    fig, axes = _axes_over(start, end)
    try:
        assert _utc_tick_label(mdates.date2num(at), axes) == expected
        # No corner offset text: the date is on every tick already.
        assert axes.xaxis.get_major_formatter().get_offset() == ""
    finally:
        plt.close(fig)
