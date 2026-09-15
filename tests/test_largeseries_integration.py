from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight import semantic
from limelight.reader import LimelightError, LimelightPackage
from limelight.writer import Index, LimelightProject, TimeSeriesArtist, hdf_array


def _hdf_package(tmp_path: Path, **artist_fields: object) -> Path:
    h5_path = tmp_path / "series.h5"
    with h5py.File(h5_path, "w") as handle:
        handle.create_dataset("/y", data=np.arange(1000, dtype=np.float64))

    project = LimelightProject(title="HDF Test", authors=["Test"])
    project.add_hdf_dataset(
        id="bigseries",
        hdf5_path=h5_path,
        y_arrays=[hdf_array("value", "/y")],
        index=Index.no_index(),
    )
    project.add_line_figure(
        id="fig1",
        title="Big Series",
        data="bigseries",
        x="unused",
        y=[],
        time_series=[TimeSeriesArtist(data="bigseries", array="value", **artist_fields)],
    )

    out_dir = tmp_path / "pkg"
    project.write_folder(out_dir)
    return out_dir


def _csv_package(tmp_path: Path) -> Path:
    project = LimelightProject(title="CSV Test", authors=["Test"])
    project.add_csv_dataset(id="csvsrc", arrays={"x": [1, 3], "y": [2, 4]})
    project.add_line_figure(id="fig2", title="CSV Fig", data="csvsrc", x="x", y=["y"])

    out_dir = tmp_path / "pkg"
    project.write_folder(out_dir)
    return out_dir


def test_writer_round_trip_produces_expected_hdf_source_and_artist_shape(tmp_path: Path) -> None:
    package_dir = _hdf_package(tmp_path)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    [source] = manifest["sources"]
    [y_array] = source["yArrays"]
    assert y_array["dataset"] == "/y"
    assert source["largeSeriesChunkSize"] > 0

    [figure] = manifest["figures"]
    [axes_spec] = figure["axesSpecs"]
    assert axes_spec["xAxis"]["dataType"] == {"calendar": ""}
    [artist] = axes_spec["actions"]
    assert artist["y"] == "bigseries['value']"
    assert artist["transform"] == "identity"


def test_timeseries_artist_colours_round_trip_and_validate(tmp_path: Path) -> None:
    package_dir = _hdf_package(tmp_path, color="#123456", fill_color="tab:orange", fill_alpha=0.4)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    [artist] = manifest["figures"][0]["axesSpecs"][0]["actions"]
    assert artist["color"] == "#123456"
    assert artist["fillColor"] == "tab:orange"
    assert artist["fillAlpha"] == 0.4
    semantic.validate_manifest_semantics(manifest)


@pytest.mark.parametrize(
    ("artist_fields", "message"),
    [
        ({"fill_color": "not-a-colour"}, "invalid fillColor"),
        ({"fill_alpha": 1.5}, "fillAlpha 1.5 is outside 0..1"),
    ],
)
def test_timeseries_artist_bad_fill_is_rejected(tmp_path: Path, artist_fields: dict, message: str) -> None:
    package_dir = _hdf_package(tmp_path, **artist_fields)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    with pytest.raises(LimelightError, match=message):
        semantic.validate_manifest_semantics(manifest)


def test_timeseries_artist_on_hdf_source_validates_cleanly(tmp_path: Path) -> None:
    package_dir = _hdf_package(tmp_path)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    semantic.validate_manifest_semantics(manifest)


def test_line_artist_on_hdf_source_is_rejected(tmp_path: Path) -> None:
    package_dir = _hdf_package(tmp_path)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    manifest["figures"][0]["axesSpecs"][0]["xAxis"]["dataType"] = {"unit": "", "scale": "Linear"}
    manifest["figures"][0]["axesSpecs"][0]["actions"] = [
        {
            "id": "bad-line",
            "x": "bigseries['value']",
            "y": "bigseries['value']",
            "label": None,
            "visibleWhen": None,
        }
    ]

    with pytest.raises(LimelightError, match="cannot reference hdf source"):
        semantic.validate_manifest_semantics(manifest)


def test_timeseries_artist_on_csv_source_is_rejected(tmp_path: Path) -> None:
    package_dir = _csv_package(tmp_path)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    manifest["figures"][0]["axesSpecs"][0]["xAxis"]["dataType"] = {"calendar": ""}
    manifest["figures"][0]["axesSpecs"][0]["actions"] = [
        {
            "id": "bad-timeseries",
            "y": "csvsrc['y']",
            "label": None,
            "transform": "identity",
            "targetBuckets": None,
            "visibleWhen": None,
        }
    ]

    with pytest.raises(LimelightError, match="must reference an hdf source"):
        semantic.validate_manifest_semantics(manifest)


def test_timeseries_artist_on_calendar_indexed_source_is_rejected(tmp_path: Path) -> None:
    package_dir = _hdf_package(tmp_path)
    with LimelightPackage.open(package_dir) as package:
        manifest = package.manifest_json()

    # Give the source a calendar index: months from January 2024.
    [source] = manifest["sources"]
    source["index"] = {"calendarStep": 1, "calendarUnit": "month", "calendarStart": "2024-01", "renderAnchor": "start"}

    with pytest.raises(LimelightError, match="calendar index a timeSeries artist cannot plot"):
        semantic.validate_manifest_semantics(manifest)
