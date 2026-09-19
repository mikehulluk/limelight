"""Shared helpers for parsing `table_id['column_name']` refs and resolving Index x-values.

dhall-to-json flattens union alternatives that carry a record payload directly
into the JSON object (no tag key), and renders a no-argument alternative as a
bare string. Shape-detection here (and in semantic.py/qt_app.py) relies on
that encoding.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

_REF_PATTERN = re.compile(r"^(.+)\['(.+)'\]$")

_UNIT_NS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "Gs": 1_000_000_000_000_000_000,
    "gs": 1_000_000_000_000_000_000,
}


def parse_column_ref(ref: str) -> tuple[str, str]:
    match = _REF_PATTERN.match(ref)
    if match is None:
        raise ValueError(f"Malformed column ref {ref!r}; expected table_id['column_name']")
    return match.group(1), match.group(2)


def artist_kind(artist: dict[str, Any]) -> str:
    if "transform" in artist:
        return "timeSeries"
    if "baseline" in artist:
        return "stem"
    if "x" in artist:
        return "scatter"
    return "line"


def _epoch_offset_ns(epoch_offset: dict[str, Any]) -> int:
    return (
        epoch_offset["epochOffsetGs"] * 1_000_000_000_000_000_000
        + epoch_offset["epochOffsetS"] * 1_000_000_000
        + epoch_offset["epochOffsetNs"]
    )


# A UTC axis is drawn in matplotlib date numbers: days since 1970-01-01 UTC.
# That is the coordinate space the UTC AxisLimits and decorators already use
# (see qt_app._time_limit_coordinate), so a time index with an absolute origin
# resolves into it too, and everything on a timeSeries axis agrees.
NS_PER_DAY = 86_400 * 1_000_000_000


def time_index_origin(index: Any) -> Any | None:
    """The TimeOrigin of a regularTime/irregularIndexTime index; None otherwise."""
    if not isinstance(index, dict):
        return None
    if "timeStepNom" in index:
        return index["timeOrigin"]
    if "irregularTimeCoordArray" in index:
        return index["irregularTimeOrigin"]
    return None


def time_index_is_absolute(index: Any) -> bool:
    """True when the index's values are UTC instants (and so draw on a calendar axis)."""
    origin = time_index_origin(index)
    return origin is not None and origin != "relative"


def time_axis_affine(index: Any) -> tuple[float, float]:
    """(x0, dx) taking a time index's own values onto the axis it is drawn on.

    Given `t`, the elapsed time in the index's own unit (`i * step` for
    regularTime, the stored coordinate for irregularIndexTime), the axis
    position is `x0 + t * dx`: days since the epoch for an absolute origin,
    `t` unchanged for a relative one.
    """
    origin = time_index_origin(index)
    if origin is None:
        raise ValueError(f"Not a time index: {index!r}")
    if origin == "relative":
        return 0.0, 1.0
    unit = index["timeStepUnit"] if "timeStepNom" in index else index["irregularTimeUnit"]
    unit_ns = _UNIT_NS[unit]
    return _epoch_offset_ns(origin) / NS_PER_DAY, unit_ns / NS_PER_DAY


def resolve_index_x(
    index: Any,
    rows: list[dict[str, Any]] | None,
    row_count: int | None,
) -> list[Any]:
    if index == "noIndex":
        return list(range(row_count or 0))

    if not isinstance(index, dict):
        raise ValueError(f"Unknown Index payload {index!r}")

    if "intOrigin" in index:
        origin = index["intOrigin"]
        step = index["intStep"]
        return [origin + i * step for i in range(row_count or 0)]

    if "timeStepNom" in index:
        nom = index["timeStepNom"]
        denom = index["timeStepDenom"]
        x0, dx = time_axis_affine(index)
        return [x0 + (i * nom / denom) * dx for i in range(row_count or 0)]

    if "calendarStep" in index:
        start = index["startOrdinal"]
        step = index["calendarStep"]
        unit = index["calendarUnit"]
        render_anchor = index["renderAnchor"]
        return [
            _calendar_period_ordinal(unit, start + i * step, render_anchor)
            for i in range(row_count or 0)
        ]

    if "irregularTimeCoordArray" in index:
        x0, dx = time_axis_affine(index)
        return [
            None if value is None else x0 + float(value) * dx
            for value in _lookup_coordinate_array(rows, index["irregularTimeCoordArray"])
        ]

    if "irregularCalendarCoordArray" in index:
        unit = index["irregularCalendarUnit"]
        render_anchor = index["irregularRenderAnchor"]
        return [
            _calendar_period_ordinal(unit, period_index, render_anchor)
            for period_index in _lookup_coordinate_array(rows, index["irregularCalendarCoordArray"])
        ]

    if "irregularArrayCoordArray" in index:
        return _lookup_coordinate_array(rows, index["irregularArrayCoordArray"])

    raise ValueError(f"Unknown Index payload {index!r}")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _period_bounds(unit: str, period_index: int) -> tuple[date, date]:
    """Proleptic-Gregorian date range covered by `period_index` units of `unit`
    since the epoch (period 0 begins at 0001-01-01)."""
    if unit == "day":
        start = date.fromordinal(1 + period_index)
        return start, start
    if unit == "week":
        start = date.fromordinal(1 + period_index * 7)
        return start, date.fromordinal(start.toordinal() + 6)
    if unit == "month":
        months = period_index
        return _month_bounds(1 + months // 12, 1 + months % 12)
    if unit == "quarter":
        months = period_index * 3
        start, _ = _month_bounds(1 + months // 12, 1 + months % 12)
        end_months = months + 2
        _, end = _month_bounds(1 + end_months // 12, 1 + end_months % 12)
        return start, end
    if unit == "year":
        year = 1 + period_index
        return date(year, 1, 1), date(year, 12, 31)
    raise ValueError(f"Unknown CalendarUnit {unit!r}")


def _calendar_period_ordinal(unit: str, period_index: int, render_anchor: str) -> int:
    start, end = _period_bounds(unit, period_index)
    if render_anchor == "periodStart":
        return start.toordinal()
    if render_anchor == "periodEnd":
        return end.toordinal()
    if render_anchor == "periodMidpoint":
        return start.toordinal() + (end.toordinal() - start.toordinal()) // 2
    raise ValueError(f"Unknown PeriodRenderAnchor {render_anchor!r}")


def _lookup_coordinate_array(rows: list[dict[str, Any]] | None, column: str) -> list[Any]:
    if rows is None:
        raise ValueError(f"Cannot resolve irregular Index coordinate array {column!r} without row data")
    return [row.get(column) for row in rows]
