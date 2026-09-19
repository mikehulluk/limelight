"""An hdf array can be one column of a 2-D (rows x columns) dataset, so a
file laid out that way is referenced as it stands rather than split into one
dataset per array. The binding is checked against the file when the dataset
is added and by `verify`."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight import largeseries, largeseries_bridge
from limelight.hdf_sources import verify_hdf_sources
from limelight.reader import LimelightError, open_limelight
from limelight.writer import Index, LimelightProject, TimeSeriesArtist, hdf_array


def _wide_file(path: Path, rows: int = 10_000, cols: int = 3) -> Path:
    with h5py.File(path, "w") as f:
        values = np.arange(rows * cols, dtype=np.float64).reshape(rows, cols)
        f.create_dataset("/values", data=values, chunks=(1024, cols))
        f["/t"] = np.arange(rows, dtype=np.int64)
    return path


def _project(h5: Path, arrays) -> LimelightProject:
    project = LimelightProject(title="wide", authors=["Test"])
    project.add_hdf_dataset(id="rec", hdf5_path=h5, y_arrays=arrays, index=Index.regular_int(0, 1))
    return project


def test_a_column_of_a_2d_dataset_reads_as_that_column(tmp_path: Path) -> None:
    h5 = _wide_file(tmp_path / "w.h5")
    source = largeseries.HdfArraySource(h5, "/values", column=1)
    try:
        assert source.length == 10_000
        np.testing.assert_array_equal(source.read_range(0, 4), [1.0, 4.0, 7.0, 10.0])
        blocks = list(source.iter_blocks(4096))
        assert [b.shape[0] for b in blocks] == [4096, 4096, 1808]
    finally:
        source.close()


def test_columns_of_one_dataset_get_separate_caches(tmp_path: Path) -> None:
    h5 = _wide_file(tmp_path / "w.h5")
    spec1 = largeseries.SourceSpec.uniform(h5, "/values", x0=0.0, dx=1.0, y_column=1)
    spec2 = largeseries.SourceSpec.uniform(h5, "/values", x0=0.0, dx=1.0, y_column=2)
    h1 = largeseries.build_or_get_cache(spec1, cache_dir=tmp_path / "cache", chunk_size=1024)
    h2 = largeseries.build_or_get_cache(spec2, cache_dir=tmp_path / "cache", chunk_size=1024)
    try:
        assert h1.path != h2.path
        r1 = largeseries.query_range(h1, spec1, 0, 9999, 4)
        r2 = largeseries.query_range(h2, spec2, 0, 9999, 4)
        # Column 2 is column 1 plus one, everywhere.
        np.testing.assert_allclose(r2.y_min - r1.y_min, 1.0)
    finally:
        largeseries.close_cache(h1)
        largeseries.close_cache(h2)


def test_manifest_binding_round_trips_through_the_bridge(tmp_path: Path) -> None:
    h5 = _wide_file(tmp_path / "w.h5")
    project = _project(h5, [hdf_array("a", "/values", column=0), hdf_array("c", "/values", column=2)])
    project.add_line_figure(id="f", title="f", data="rec", x="", y=[], time_series=[TimeSeriesArtist(data="rec", array="c")])
    project.write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        manifest = package.manifest_json()
        verify_hdf_sources(package, manifest)
        [source] = manifest["sources"]
        assert [(e["dataset"], e["column"]) for e in source["yArrays"]] == [("/values", 0), ("/values", 2)]
        spec = largeseries_bridge.hdf_source_spec(source, package, "c")
    assert (spec.y_dataset, spec.y_column, spec.y_selector) == ("/values", 2, "/values[2]")


def test_a_bad_binding_is_refused_where_it_is_made(tmp_path: Path) -> None:
    h5 = _wide_file(tmp_path / "w.h5")
    with pytest.raises(LimelightError, match="has 3 columns"):
        _project(h5, [hdf_array("x", "/values", column=3)])
    with pytest.raises(LimelightError, match="a 2-D dataset needs a column"):
        _project(h5, [hdf_array("x", "/values")])
    with pytest.raises(LimelightError, match="not 2-D"):
        _project(h5, [hdf_array("x", "/t", column=0)])
    with pytest.raises(LimelightError, match="not in w.h5"):
        _project(h5, [hdf_array("x", "/nope")])
