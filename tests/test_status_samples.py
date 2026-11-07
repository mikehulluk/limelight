"""Sample status: a code per sample saying whether it is a value, skipped
(no sample expected; the line joins across it) or absent (expected and not
there, for a declared reason). The fixture is shaped like a logger's
recording - `/values` and `/status`, rows x channels - with one channel read
on every tenth tick only, some bad reads, and a stretch of dropped rows."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import h5py
import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from limelight import largeseries_bridge  # noqa: E402
from limelight.app import LimelightRuntime  # noqa: E402
from limelight.hdf_sources import verify_hdf_sources  # noqa: E402
from limelight.largeseries import SourceSpec, build_or_get_cache, close_cache, query_range  # noqa: E402
from limelight.largeseries.mpl import plot_envelope  # noqa: E402
from limelight.largeseries.pyramid import LevelZeroBuilder, cascade_levels  # noqa: E402
from limelight.largeseries.status import (  # noqa: E402
    NAN_KIND,
    SKIP,
    UNKNOWN_KIND,
    VALID,
    StatusMap,
    kind_bits,
    sample_kinds,
)
from limelight.reader import LimelightError, open_limelight  # noqa: E402
from limelight.semantic import validate_manifest_semantics  # noqa: E402
from limelight.writer import Index, LimelightProject, Panel, StatusCode, TimeSeriesArtist, hdf_array, AxisDataType  # noqa: E402

N = 20_000
VALID_CODE, BAD_READ, NOT_SCHEDULED, ROW_GAP = 0, 1, 2, 3
CODES = [
    StatusCode(VALID_CODE, "valid", "Valid"),
    StatusCode(BAD_READ, "absent", "Bad read", marker="cross", color="#d62728"),
    StatusCode(NOT_SCHEDULED, "skip", "Not scheduled"),
    StatusCode(ROW_GAP, "absent", "Row gap", marker="none"),
]
CODE_TABLE = [(c.code, c.meaning) for c in CODES]


def _recording(path: Path) -> Path:
    t = np.arange(N, dtype=np.float64)
    values = np.stack([np.sin(t / 300), np.cos(t / 300), t / N], axis=1)
    status = np.zeros((N, 3), dtype=np.uint8)
    status[[100, 5_000, 5_001], 0] = BAD_READ          # channel 0: three bad reads
    status[200, 1] = BAD_READ                           # channel 1: one
    status[12_000:12_500, :] = ROW_GAP                  # every channel: dropped rows
    status[:, 2] = NOT_SCHEDULED                        # channel 2: read every tenth tick
    status[::10, 2] = VALID_CODE
    status[12_000:12_500, 2] = ROW_GAP
    values[status != VALID_CODE] = np.nan               # as a converter writes them; status decides anyway
    with h5py.File(path, "w") as handle:
        handle["/values"] = values
        handle["/status"] = status
    return path


def _project(h5: Path, codes=CODES, **status_kw) -> LimelightProject:
    project = LimelightProject(title="status", authors=["Test"])
    project.add_hdf_dataset(
        id="rec",
        hdf5_path=h5,
        index=Index.regular_int(0, 1),
        status_codes=codes,
        y_arrays=[
            hdf_array(f"ch{j}", "/values", column=j, status=status_kw.get("status", "/status"), status_column=j)
            for j in range(3)
        ],
    )
    return project


# --- the status layer -------------------------------------------------------


def test_codes_classify_into_valid_skip_and_numbered_kinds_of_absence() -> None:
    status_map = StatusMap.from_meanings(CODE_TABLE)
    kinds = status_map.classify(np.array([0, 1, 2, 3, 7]))
    assert kinds.tolist() == [VALID, 2, SKIP, 3, UNKNOWN_KIND]
    y = np.array([1.0, np.nan, 2.0, 3.0, 4.0, np.nan])
    kinds = sample_kinds(y, np.array([0, 0, 2, 1, 7, 2]), status_map)
    # A NaN under a valid code is the plain kind; status decides everything else.
    assert kinds.tolist() == [VALID, NAN_KIND, SKIP, 2, UNKNOWN_KIND, SKIP]
    assert kind_bits(kinds).tolist() == [0, 1, 0, 4, 2, 0]


def test_a_code_table_rejects_repeats_and_too_many_absent_codes() -> None:
    with pytest.raises(ValueError, match="declared twice"):
        StatusMap.from_meanings([(1, "valid"), (1, "skip")])
    with pytest.raises(ValueError, match="at most 30"):
        StatusMap.from_meanings([(code, "absent") for code in range(31)])


def test_bucket_masks_are_ored_up_the_pyramid() -> None:
    builder = LevelZeroBuilder(chunk_size=2, irregular_x=False)
    bits = np.array([0, 4, 0, 0, 1, 0, 0, 8], dtype=np.uint32)
    builder.feed(np.arange(8, dtype=np.float64), None, bits)
    levels = cascade_levels(builder.finish(), min_level_buckets=1)
    assert levels[0].missing.tolist() == [4, 0, 1, 8]
    assert levels[1].missing.tolist() == [4, 9]
    assert levels[2].missing.tolist() == [13]


# --- the engine ------------------------------------------------------------


def _spec(h5: Path, column: int) -> SourceSpec:
    return SourceSpec.uniform(h5, "/values", x0=0.0, dx=1.0, y_column=column).with_status(
        "/status", StatusMap.from_meanings(CODE_TABLE), column=column
    )


def test_a_decimated_channel_draws_as_one_line_zoomed_out_and_in(tmp_path: Path) -> None:
    h5 = _recording(tmp_path / "rec.h5")
    spec = _spec(h5, 2)
    handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=4)
    try:
        envelope = query_range(handle, spec, 0, N - 1, target_buckets=200)
        assert not envelope.raw
        # Every bucket has a value except across the dropped rows: skipped
        # ticks make no gap and no mark.
        gaps = envelope.x[np.isnan(envelope.y_min)]
        assert gaps.size and gaps.min() >= 12_000 - 100 and gaps.max() <= 12_500 + 100

        raw = query_range(handle, spec, 0, 99, target_buckets=1_000)
        assert raw.raw
        # Only the read ticks are points: the line joins sample 0 to sample 10.
        assert raw.x.tolist() == [float(i) for i in range(0, 100, 10)]
        assert not np.isnan(raw.y_min).any() and not raw.missing.any()
    finally:
        close_cache(handle)


def test_buckets_say_which_kinds_of_absence_they_hold(tmp_path: Path) -> None:
    h5 = _recording(tmp_path / "rec.h5")
    spec = _spec(h5, 0)
    handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=64)
    try:
        envelope = query_range(handle, spec, 0, N - 1, target_buckets=150)
        bad_read, row_gap = 1 << 2, 1 << 3
        assert {int(x) // 1000 for x in envelope.x[(envelope.missing & bad_read) != 0]} == {0, 5}
        gap_x = envelope.x[(envelope.missing & row_gap) != 0]
        assert gap_x.min() >= 12_000 - 200 and gap_x.max() <= 12_500 + 200

        raw = query_range(handle, spec, 95, 105, target_buckets=100)
        assert raw.x[np.isnan(raw.y_min)].tolist() == [100.0]
        assert raw.missing[raw.x == 100.0].tolist() == [bad_read]
    finally:
        close_cache(handle)


def test_the_same_values_read_through_another_code_table_are_another_cache(tmp_path: Path) -> None:
    h5 = _recording(tmp_path / "rec.h5")
    plain = SourceSpec.uniform(h5, "/values", x0=0.0, dx=1.0, y_column=0)
    with_status = _spec(h5, 0)
    other_table = plain.with_status("/status", StatusMap.from_meanings([(0, "valid"), (1, "skip"), (3, "skip")]), column=0)
    handles = [build_or_get_cache(s, cache_dir=tmp_path / "cache", chunk_size=64) for s in (plain, with_status, other_table)]
    try:
        assert len({h.content_hash for h in handles}) == 3
        again = build_or_get_cache(with_status, cache_dir=tmp_path / "cache", chunk_size=64)
        assert again.content_hash == handles[1].content_hash
        close_cache(again)
    finally:
        for handle in handles:
            close_cache(handle)


def test_each_kind_present_gets_its_own_marks_and_a_legend_entry(tmp_path: Path) -> None:
    h5 = _recording(tmp_path / "rec.h5")
    spec = _spec(h5, 0)
    handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=64)
    fig, ax = plt.subplots()
    try:
        styles = largeseries_bridge.missing_styles_of({"statusCodes": [c.__dict__ for c in CODES]})
        result = query_range(handle, spec, 0, N - 1, target_buckets=150)
        _, _, _, marks = plot_envelope(ax, result, missing_styles=styles)
        # Row gap is marker "none": a gap, never marked. Bad read is marked.
        assert list(marks.points) == [2]
        assert marks.points[2].get_label() == "Bad read"
        assert len(marks.rugs[2].get_xdata()) > 0 and marks.get_visible()
    finally:
        plt.close(fig)
        close_cache(handle)


# --- the manifest, the writer and verify ------------------------------------


def test_status_round_trips_through_the_manifest(tmp_path: Path) -> None:
    _project(_recording(tmp_path / "rec.h5")).write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        verify_hdf_sources(package, manifest)
    [source] = manifest["sources"]
    assert [(c["code"], c["meaning"], c["label"]) for c in source["statusCodes"]] == [
        (0, "valid", "Valid"), (1, "absent", "Bad read"), (2, "skip", "Not scheduled"), (3, "absent", "Row gap")
    ]
    assert source["statusCodes"][3]["marker"] == "none"
    assert source["yArrays"][2]["status"] == {"dataset": "/status", "column": 2}


def test_a_package_without_status_reads_as_before(tmp_path: Path) -> None:
    project = LimelightProject(title="plain", authors=["Test"])
    project.add_hdf_dataset(id="rec", hdf5_path=_recording(tmp_path / "rec.h5"), y_arrays=[hdf_array("ch0", "/values", column=0)])
    project.write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        [source] = package.manifest_json()["sources"]
    assert source["statusCodes"] == [] and "status" not in source["yArrays"][0]


@pytest.mark.parametrize(
    ("codes", "match"),
    [
        (CODES[:3], "do not declare: 3"),
        ([], "declares no statusCodes"),
        ([*CODES, StatusCode(1, "skip", "Again")], "declared twice"),
        ([*CODES, StatusCode(9, "absent", "")], "no label"),
        ([*CODES, StatusCode(9, "absent", "Odd", color="not-a-colour")], "invalid color"),
        ([*CODES[:1], *[StatusCode(c, "absent", f"c{c}") for c in range(1, 32)]], "at most 30"),
    ],
)
def test_the_writer_refuses_a_code_table_the_file_or_viewer_cannot_honour(tmp_path: Path, codes, match) -> None:
    with pytest.raises(LimelightError, match=match):
        _project(_recording(tmp_path / "rec.h5"), codes=codes)


def test_a_status_dataset_must_be_integer_and_as_long_as_its_array(tmp_path: Path) -> None:
    h5 = _recording(tmp_path / "rec.h5")
    with h5py.File(h5, "a") as handle:
        handle["/float_status"] = np.zeros((N, 3))
        handle["/short_status"] = np.zeros((10, 3), dtype=np.uint8)
    with pytest.raises(LimelightError, match="integer codes"):
        _project(h5, status="/float_status")
    with pytest.raises(LimelightError, match="has 10 rows"):
        _project(h5, status="/short_status")


def test_verify_fails_on_a_code_the_source_does_not_declare(tmp_path: Path) -> None:
    _project(_recording(tmp_path / "rec.h5")).write_folder(tmp_path / "pkg")
    with h5py.File(tmp_path / "pkg" / "data" / "rec.h5", "a") as handle:
        handle["/status"][7, 1] = 42
    with open_limelight(tmp_path / "pkg") as package:
        with pytest.raises(LimelightError, match="do not declare: 42"):
            verify_hdf_sources(package, package.manifest_json())


# --- the app: figures, tables and CSV ----------------------------------------


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    project = _project(_recording(tmp_path / "rec.h5"))
    project.add_figure(
        id="both",
        title="Two channels",
        panels=[Panel(
            time_series=[TimeSeriesArtist(data="rec", array="ch0", label="A"), TimeSeriesArtist(data="rec", array="ch1", label="B")],
            data="rec",
            x_axis=AxisDataType.continuous(label="Sample"),
            y_axis=AxisDataType.continuous(label="Value"),
        )],
    )
    project.write_folder(tmp_path / "pkg")
    package = open_limelight(tmp_path / "pkg")
    runtime = LimelightRuntime(package, package.manifest_json())
    yield runtime
    runtime.close()


def test_a_panel_names_each_kind_of_absence_once(runtime) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from matplotlib.figure import Figure

    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    figure = Figure(figsize=(6, 3))
    _render_figure_view_to_matplotlib_figure(runtime, figure, "both")
    [axes] = [a for a in figure.axes if a.get_legend() is not None]
    labels = [text.get_text() for text in axes.get_legend().get_texts()]
    # Both channels have bad reads, named once; row gaps are never marked.
    # (A time series' own label is not a legend entry, with or without status.)
    assert labels == ["Bad read"]


def test_a_table_shows_why_a_cell_is_empty(runtime) -> None:
    preview = runtime.table_preview("rec", None, limit=110)
    by_name = dict(zip(preview.columns, zip(*preview.rows)))
    assert by_name["ch0"][100] == "Bad read"
    assert by_name["ch2"][1] == "" and by_name["ch2"][10] != ""


def test_a_csv_export_leaves_skipped_and_absent_cells_empty(runtime, tmp_path: Path) -> None:
    out = tmp_path / "rec.csv"
    runtime.export_table_csv("rec", "ch0", str(out))
    rows = list(csv.reader(out.open()))
    assert rows[0] == ["ch0"] and rows[101] == [""] and rows[1] != [""]
    assert all(row == [""] for row in rows[12_001:12_501])
