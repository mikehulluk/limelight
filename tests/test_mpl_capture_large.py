"""Large series through mpl_capture: a declared hdf source plotted with
`ax.series`, a big `plot()` turned into a series, and a lightspeed script's
`plot_series` taken in by a CapturingAxes. Each is checked in the preview
and in what the app draws from the written package."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import h5py
import lightspeed
import matplotlib.pyplot as plt
import numpy as np
import pytest
from lightspeed.mpl import ZoomSync, plot_series
from lightspeed.status import StatusMap
from lightspeed.view import MissingStyle
from matplotlib.figure import Figure

from limelight import Index, LimelightProject, MplCaptureWarning, hdf_array, hdf_source, mpl_capture, mpl_subplots, open_limelight
from limelight import mpl_capture as capture_module
from limelight.app import LimelightRuntime
from limelight.semantic import validate_manifest_semantics

N = 5_000


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LIMELIGHT_CACHE_DIR", str(tmp_path / "cache"))
    # Small enough for a quick test, large enough to be reduced to an envelope.
    monkeypatch.setattr(capture_module, "LARGE_SERIES_POINTS", 2_000)
    yield
    plt.close("all")


def _render(project: LimelightProject, tmp_path: Path, figure_id: str) -> tuple[Figure, dict]:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    folder = tmp_path / f"pkg-{figure_id}"
    project.write_folder(folder)
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(LimelightRuntime(package, manifest), figure, figure_id)
    return figure, manifest


def _recording(tmp_path: Path) -> Path:
    path = tmp_path / "rec.h5"
    with h5py.File(path, "w") as handle:
        handle["/t"] = np.linspace(0.0, 100.0, N)
        handle["/v"] = np.sin(np.linspace(0.0, 60.0, N))
    return path


def _envelope_points(axes) -> int:
    return max(len(line.get_xdata()) for line in axes.get_lines())


def test_a_declared_source_is_plotted_as_a_series_and_added_with_the_figure(tmp_path: Path) -> None:
    source = hdf_source(
        id="rec", hdf5_path=_recording(tmp_path), y_arrays=[hdf_array("v", "/v"), hdf_array("t", "/t")],
        index=Index.irregular_index_array("t"),
    )
    fig, (top, bottom) = mpl_subplots(2, 1, sharex=True)
    zoom = top.series(source, "v", label="V", color="C1")
    bottom.plot([0.0, 50.0, 100.0], [1.0, 0.0, 1.0])
    assert isinstance(zoom, ZoomSync)
    assert _envelope_points(top.mpl_axes) < N

    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    assert [dataset.id for dataset in project.hdf_datasets] == ["rec"]
    figure, manifest = _render(project, tmp_path, "fig")
    series = manifest["figures"][0]["axesSpecs"][0]["actions"]
    assert any("transform" in action.get("AxesActionAddData", action) for action in series)
    assert figure.axes[0].get_xlim() == pytest.approx(top.mpl_axes.get_xlim())


def test_two_figures_plotting_one_source_add_it_once(tmp_path: Path) -> None:
    source = hdf_source(id="rec", hdf5_path=_recording(tmp_path), y_arrays=[hdf_array("v", "/v")], index=Index.regular_int(0, 1))
    project = LimelightProject(title="T", authors=["Test"])
    for figure_id in ("a", "b"):
        fig, ax = mpl_subplots()
        ax.series(source, "v")
        fig.add_to(project, id=figure_id, title=figure_id)
    assert [dataset.id for dataset in project.hdf_datasets] == ["rec"]


def test_a_big_plot_becomes_a_series_on_a_float_index(tmp_path: Path) -> None:
    x = np.linspace(0.0, 2.0, N)
    fig, ax = mpl_subplots()
    [line] = ax.plot(x, np.sin(x * 40), "g", label="big", name="wave")
    assert len(line.get_xdata()) < N  # an envelope, not N points

    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert source.id == "fig-data-wave"
    assert source.index.manifest["floatOrigin"] == 0.0
    assert source.index.manifest["floatStep"] == pytest.approx(2.0 / (N - 1))
    assert project.datasets == []  # nothing went to CSV
    figure, _ = _render(project, tmp_path, "fig")
    assert figure.axes[0].get_xlim() == pytest.approx(ax.mpl_axes.get_xlim())


def test_a_big_plot_with_irregular_x_keeps_its_x(tmp_path: Path) -> None:
    x = np.cumsum(np.random.default_rng(1).uniform(0.5, 1.5, N))
    fig, ax = mpl_subplots()
    ax.plot(x, np.cos(x))
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert source.index.manifest == {"irregularArrayCoordArray": "x", "irregularArrayUnit": None}
    with h5py.File(source.hdf5_source_path) as handle:
        assert np.array_equal(handle["/x"][:], x)


def test_a_big_plot_over_dates_is_a_series_on_a_time_axis(tmp_path: Path) -> None:
    days = np.datetime64("2026-01-01") + np.arange(N).astype("timedelta64[m]")
    fig, ax = mpl_subplots()
    ax.plot(days, np.sin(np.arange(N) / 100))
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert source.index.absolute_time
    assert source.index.manifest["timeStepNom"] == 60_000_000_000  # one minute, in ns
    figure, manifest = _render(project, tmp_path, "fig")
    assert "calendar" in manifest["figures"][0]["axesSpecs"][0]["xAxis"]["dataType"]
    assert figure.axes[0].get_xlim() == pytest.approx(ax.mpl_axes.get_xlim())


def test_a_big_plot_over_irregular_dates_keeps_each_instant(tmp_path: Path) -> None:
    gaps = np.random.default_rng(3).integers(1, 120, N).astype("timedelta64[s]")
    instants = np.datetime64("2026-03-01T00:00:00") + np.cumsum(gaps)
    fig, ax = mpl_subplots()
    ax.plot(instants, np.arange(float(N)))
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert source.index.manifest["irregularTimeUnit"] == "us"
    figure, _ = _render(project, tmp_path, "fig")
    assert figure.axes[0].get_xlim() == pytest.approx(ax.mpl_axes.get_xlim())


def test_unsorted_or_opted_out_points_stay_a_line(tmp_path: Path) -> None:
    x = np.random.default_rng(2).uniform(0.0, 1.0, N)
    _, ax = mpl_subplots()
    with pytest.warns(MplCaptureWarning, match="unsorted"):
        ax.plot(x, x)
    ax.plot(np.sort(x), x, large=False)
    assert len(ax.lines) == 2 and not ax._series


def test_line_styling_a_series_cannot_show_warns() -> None:
    _, ax = mpl_subplots()
    with pytest.warns(MplCaptureWarning, match="marker='o'"):
        ax.plot(np.arange(float(N)), np.zeros(N), "o")


def test_plot_series_in_an_unmodified_lightspeed_script_is_captured(tmp_path: Path) -> None:
    spec = lightspeed.write_series(tmp_path / "raw.h5", np.random.default_rng(0).normal(size=N), x0=5.0, dx=0.25)
    with mpl_capture.capture() as figures:
        _, ax = plt.subplots()
        sync = plot_series(ax, spec, label="noise")
    assert isinstance(sync, ZoomSync)

    project = LimelightProject(title="T", authors=["Test"])
    figures[0].add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert source.index.manifest["floatOrigin"] == 5.0 and source.index.manifest["floatStep"] == 0.25
    figure, _ = _render(project, tmp_path, "fig")
    assert figure.axes[0].get_xlim() == pytest.approx(ax.mpl_axes.get_xlim())


def test_plot_series_over_a_time_coordinate_becomes_a_time_index(tmp_path: Path) -> None:
    origin_s = 1_789_776_000  # 2026-09-19
    t_us = np.arange(N, dtype=float) * 1_000.0
    spec = lightspeed.write_series(
        tmp_path / "t.h5", np.sin(t_us), t_us, x0=origin_s / 86_400, dx=1e-6 / 86_400
    )
    fig, ax = mpl_subplots()
    plot_series(ax, spec)
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert source.index.manifest["irregularTimeUnit"] == "us"
    origin = source.index.manifest["irregularTimeOrigin"]
    assert origin["epochOffsetGs"] * 1_000_000_000 + origin["epochOffsetS"] == origin_s


def test_plot_series_with_an_x_placing_no_index_can_say_warns(tmp_path: Path) -> None:
    x = np.arange(float(N))
    spec = lightspeed.write_series(tmp_path / "w.h5", np.sin(x), x, x0=3.0, dx=7.0)
    _, ax = mpl_subplots()
    with pytest.warns(MplCaptureWarning, match="stored x coordinates"):
        plot_series(ax, spec)


def test_plot_series_keeps_status_codes_and_their_styles(tmp_path: Path) -> None:
    path = tmp_path / "s.h5"
    with h5py.File(path, "w") as handle:
        handle["/y"] = np.arange(float(N))
        handle["/status"] = np.where(np.arange(N) % 100 == 0, 2, 0).astype("i1")
    spec = lightspeed.SourceSpec.uniform(path, "/y", x0=0.0, dx=1.0).with_status(
        "/status", StatusMap.from_meanings([(0, "valid"), (2, "absent")])
    )
    fig, ax = mpl_subplots()
    plot_series(ax, spec, missing_styles={2: MissingStyle(label="dropout", marker="circle", color="#ff0000")})
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    [source] = project.hdf_datasets
    assert [(code.code, code.meaning, code.label, code.marker) for code in source.status_codes] == [
        (0, "valid", "status 0", None),
        (2, "absent", "dropout", "circle"),
    ]


def test_zoomsync_on_a_capturing_axes_points_to_plot_series(tmp_path: Path) -> None:
    spec = lightspeed.write_series(tmp_path / "z.h5", np.arange(10.0))
    handle = lightspeed.build_or_get_cache(spec, cache_dir=tmp_path / "cache")
    _, ax = mpl_subplots()
    with pytest.raises(TypeError, match="plot_series"):
        ZoomSync(ax, handle, spec)


def test_a_series_after_add_to_draws_in_the_preview_only(tmp_path: Path) -> None:
    source = hdf_source(id="rec", hdf5_path=_recording(tmp_path), y_arrays=[hdf_array("v", "/v")], index=Index.regular_int(0, 1))
    fig, ax = mpl_subplots()
    ax.plot([0.0, 1.0], [0.0, 1.0])
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    with pytest.warns(MplCaptureWarning, match="already added"):
        ax.series(source, "v")
    assert project.hdf_datasets == []


def test_a_regular_float_index_places_csv_rows(tmp_path: Path) -> None:
    from limelight import refs

    index = Index.regular_float(1.5, 0.25)
    assert refs.resolve_index_x(index.manifest, None, 3) == [1.5, 1.75, 2.0]
    with pytest.raises(ValueError, match="positive step"):
        Index.regular_float(0.0, 0.0)
