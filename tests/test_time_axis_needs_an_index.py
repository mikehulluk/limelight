"""A line on a time axis takes its x from its source's index, so that index
has to be a time or calendar one. This is the mistake example00 shipped with:
a calendar axis over a noIndex source, which drew row numbers as dates and
crashed the app on the first one."""

from __future__ import annotations

from pathlib import Path

import pytest

from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import AxisDataType, Index, LimelightProject


def _package(tmp_path: Path, *, index: Index | None, x: str = "") -> dict:
    project = LimelightProject(title="Time axis", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0, 2.0], "y": [1.0, 2.0, 3.0]}, index=index)
    project.add_line_figure(
        id="fig", title="Fig", data="src", x=x, y=["y"],
        x_axis=AxisDataType.time_series(label="Day", calendar="CalendarDay"),
    )
    project.write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        return package.manifest_json()


def test_a_calendar_axis_over_an_unindexed_source_is_rejected(tmp_path: Path) -> None:
    manifest = _package(tmp_path, index=None)

    with pytest.raises(LimelightError, match="has no index to take its x values from"):
        validate_manifest_semantics(manifest)


def test_a_calendar_index_satisfies_it(tmp_path: Path) -> None:
    manifest = _package(tmp_path, index=Index.regular_calendar(calendar="prolepticGregorian", unit="day", start_ordinal=739828, step=1, render_anchor="periodStart"))

    validate_manifest_semantics(manifest)


def test_an_explicit_x_column_satisfies_it(tmp_path: Path) -> None:
    # With xOverride the index is not consulted, so noIndex is fine.
    manifest = _package(tmp_path, index=None, x="t")

    validate_manifest_semantics(manifest)
