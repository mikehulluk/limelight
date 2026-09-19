"""A time index with an absolute UTC origin draws on a calendar axis, in the
same coordinate space (matplotlib date numbers: days since 1970-01-01 UTC) as
the UTC AxisLimits; a relative one is elapsed time in its unit, on a number
line. Both the CSV path (refs.resolve_index_x) and the large-series path
(largeseries_bridge.hdf_source_spec) follow the one rule in refs."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight import refs
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import AxisDataType, EpochOffset, Index, LimelightProject, TimeSeriesArtist, hdf_array

# 2026-09-19T00:00:00Z = 1_789_776_000 s since the epoch = 20714.5 days.
_T0_S = 1_789_776_000
_T0_DAYS = _T0_S / 86_400


def _regular(origin: EpochOffset | None) -> dict:
    project = LimelightProject(title="t", authors=["Test"])
    project.add_csv_dataset(
        id="src", arrays={"y": [1.0, 2.0, 3.0]},
        index=Index.regular_time(step_nom=1, step_denom=1000, step_unit="s", epoch_offset=origin),
    )
    return project


def test_absolute_regular_time_resolves_to_days_since_epoch() -> None:
    index = {
        "timeOrigin": {"epochOffsetGs": 0, "epochOffsetS": _T0_S, "epochOffsetNs": 0},
        "timeStepNom": 1, "timeStepDenom": 1000, "timeStepUnit": "s",
    }
    x = refs.resolve_index_x(index, None, 3)
    assert x[0] == pytest.approx(_T0_DAYS)
    assert x[2] - x[0] == pytest.approx(2 * 0.001 / 86_400)
    assert refs.time_index_is_absolute(index)


def test_relative_regular_time_stays_in_its_unit() -> None:
    index = {"timeOrigin": "relative", "timeStepNom": 2500, "timeStepDenom": 1, "timeStepUnit": "us"}
    assert refs.resolve_index_x(index, None, 3) == [0.0, 2500.0, 5000.0]
    assert not refs.time_index_is_absolute(index)


def test_irregular_time_uses_its_unit_and_origin() -> None:
    index = {
        "irregularTimeCoordArray": "t",
        "irregularTimeOrigin": {"epochOffsetGs": 0, "epochOffsetS": _T0_S, "epochOffsetNs": 0},
        "irregularTimeUnit": "us",
    }
    rows = [{"t": 0}, {"t": 86_400_000_000}]
    x = refs.resolve_index_x(index, rows, 2)
    assert x == pytest.approx([_T0_DAYS, _T0_DAYS + 1.0])


def test_hdf_source_spec_puts_an_absolute_index_in_days(tmp_path: Path) -> None:
    from limelight import largeseries_bridge

    h5 = tmp_path / "s.h5"
    with h5py.File(h5, "w") as f:
        f["/y"] = np.zeros(10)
        f["/t"] = np.arange(10, dtype=np.int64) * 1000  # µs
    project = LimelightProject(title="t", authors=["Test"])
    project.add_hdf_dataset(
        id="reg", hdf5_path=h5, y_arrays=[hdf_array("y", "/y")],
        index=Index.regular_time(step_nom=1, step_denom=1, step_unit="us", epoch_offset=EpochOffset.from_unix_s(_T0_S)),
    )
    project.add_hdf_dataset(
        id="irr", hdf5_path=h5, y_arrays=[hdf_array("y", "/y"), hdf_array("t", "/t", dtype="integer")],
        index=Index.irregular_index_time("t", origin=EpochOffset.from_unix_s(_T0_S), unit="us"),
    )
    project.add_line_figure(id="f1", title="f", data="reg", x="", y=[], time_series=[TimeSeriesArtist(data="reg", array="y")])
    project.add_line_figure(id="f2", title="f", data="irr", x="", y=[], time_series=[TimeSeriesArtist(data="irr", array="y")])
    project.write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        sources = {s["id"]: s for s in manifest["sources"]}
        reg = largeseries_bridge.hdf_source_spec(sources["reg"], package, "y")
        irr = largeseries_bridge.hdf_source_spec(sources["irr"], package, "y")

    us_per_day = 86_400e6
    assert reg.x0 == pytest.approx(_T0_DAYS) and reg.dx == pytest.approx(1 / us_per_day)
    assert irr.x0 == pytest.approx(_T0_DAYS) and irr.dx == pytest.approx(1 / us_per_day)
    # Sample 5 of each lands on the same instant.
    assert reg.x_of_coord(5) == pytest.approx(irr.x_of_coord(5000))
    # Both figures inferred a timeSeries axis from the absolute index.
    for figure in manifest["figures"]:
        assert figure["axesSpecs"][0]["xAxis"]["dataType"] == {"calendar": ""}


def test_a_relative_index_on_a_time_axis_is_refused(tmp_path: Path) -> None:
    project = _regular(None)
    project.add_line_figure(
        id="f", title="f", data="src", x="", y=["y"],
        x_axis=AxisDataType.time_series(label="Time"),
    )
    project.write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        with pytest.raises(LimelightError, match="relative time index"):
            validate_manifest_semantics(package.manifest_json())


def test_an_absolute_index_infers_a_time_axis_and_a_relative_one_a_number_line(tmp_path: Path) -> None:
    for origin, expected in ((EpochOffset.from_unix_s(_T0_S), {"calendar": ""}), (None, {"unit": "", "scale": "Linear"})):
        project = _regular(origin)
        project.add_line_figure(id="f", title="f", data="src", x="", y=["y"])
        out = tmp_path / ("abs" if origin else "rel")
        project.write_folder(out)
        with open_limelight(out) as package:
            manifest = package.manifest_json()
            validate_manifest_semantics(manifest)
            assert manifest["figures"][0]["axesSpecs"][0]["xAxis"]["dataType"] == expected


def test_epoch_offset_splits_and_round_trips() -> None:
    from datetime import datetime, timezone

    ns = 1_789_776_000_123_456_789
    offset = EpochOffset.from_unix_ns(ns)
    assert (offset.epoch_offset_gs, offset.epoch_offset_s, offset.epoch_offset_ns) == (1, 789_776_000, 123_456_789)
    assert offset.to_unix_ns() == ns
    assert EpochOffset.from_unix_us(ns // 1000).to_unix_ns() == ns - 789
    when = datetime(2026, 9, 19, 0, 0, 0, 123456, tzinfo=timezone.utc)
    assert EpochOffset.from_datetime(when).to_unix_ns() == 1_789_776_000_123_456_000
    with pytest.raises(ValueError):
        EpochOffset.from_unix_ns(-1)
