"""Ordinary matplotlib calls, captured as a Limelight figure.

The preview a script draws and the figure a reader opens come from the same
calls, so these tests build a figure through `mpl_capture`, write the
package, and check what the app draws from it.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest
from matplotlib.figure import Figure

from limelight import LimelightProject, mpl_capture, mpl_subplots, open_limelight
from limelight.app import LimelightRuntime
from limelight.mpl_capture import CapturingDataset
from limelight.semantic import validate_manifest_semantics


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def _write_and_render(project: LimelightProject, tmp_path: Path, figure_id: str) -> tuple[Figure, dict]:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    folder = tmp_path / "pkg"
    project.write_folder(folder)
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        runtime = LimelightRuntime(package, manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(runtime, figure, figure_id)
    return figure, manifest


def test_a_two_panel_figure_round_trips_with_one_shared_x_column(tmp_path: Path) -> None:
    t = np.linspace(0.0, 1.0, 20)
    a, b = np.sin(t), np.cos(t)
    fig, (top, bottom) = mpl_subplots(2, 1, sharex=True)
    top.plot(t, a, label="a", name="a", x_name="t")
    top.set_title("Top")
    bottom.scatter(t, b, label="b", name="b")
    bottom.set_yscale("log")

    # One column per array, however many panels plot it.
    assert list(fig.dataset.columns) == ["t", "a", "b"]

    project = LimelightProject(title="T", authors=["Test"])
    fig.configure(id="fig", title="Fig")
    project.add_figure(fig)
    figure, _ = _write_and_render(project, tmp_path, "fig")

    upper, lower = figure.axes
    [line] = upper.get_lines()
    assert np.allclose(line.get_ydata(), a)
    assert lower.get_yscale() == "log"
    assert lower in upper.get_shared_x_axes().get_siblings(upper)


def test_fill_between_after_plot_becomes_that_lines_fill(tmp_path: Path) -> None:
    t = np.arange(5.0)
    y = np.array([1.0, 3.0, 2.0, 4.0, 1.0])
    fig, ax = mpl_subplots()
    ax.plot(t, y, label="y", drawstyle="steps-post")
    ax.fill_between(t, y, 0.5, color="#336699", alpha=0.5)

    [artist] = ax.lines
    assert (artist.fill_color, artist.fill_alpha, artist.fill_baseline) == ("#336699", 0.5, 0.5)
    assert artist.draw_style == "stepsPost"
    # The preview's legend label moved onto the fill, as the app's does.
    [preview_fill] = ax.mpl_axes.collections
    assert preview_fill.get_label() == "y"

    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    figure, _ = _write_and_render(project, tmp_path, "fig")
    [fill] = figure.axes[0].collections
    assert fill.get_label() == "y"


def test_fill_between_without_its_plot_is_refused() -> None:
    _, ax = mpl_subplots()
    with pytest.raises(ValueError, match="immediately follow"):
        ax.fill_between([0.0, 1.0], [1.0, 2.0])


def test_spans_and_lines_are_recorded_as_decorators(tmp_path: Path) -> None:
    fig, ax = mpl_subplots()
    ax.plot([0.0, 1.0, 2.0], [0.0, 1.0, 0.5])
    ax.axvspan(0.2, 0.4, label="window")
    ax.axhline(0.75, color="#ff0000", label="limit")
    assert len(ax.actions) == 2

    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    figure, _ = _write_and_render(project, tmp_path, "fig")
    texts = {text.get_text() for text in figure.axes[0].texts}
    assert {"window", "limit"} <= texts


def test_two_figures_sharing_a_dataset_register_it_once(tmp_path: Path) -> None:
    t = np.linspace(0.0, 1.0, 10)
    y = t**2
    shared = CapturingDataset()
    first, ax1 = mpl_subplots(dataset=shared)
    ax1.plot(t, y, name="y", x_name="t")
    second, ax2 = mpl_subplots(dataset=shared)
    ax2.scatter(t, y)

    project = LimelightProject(title="T", authors=["Test"])
    first.add_to(project, id="first", title="First")
    second.add_to(project, id="second", title="Second")
    assert second.dataset_id == "first-data"
    assert [dataset.id for dataset in project.datasets] == ["first-data"]
    assert second.name_for(y) == "y"


def test_capture_wraps_plt_subplots_only_inside_the_block() -> None:
    with mpl_capture() as figures:
        fig, ax = plt.subplots()
        ax.plot([0.0, 1.0], [1.0, 2.0])
        ax.bar([0.5], [1.0])  # not captured, but still drawn
    assert figures == [fig]
    assert len(ax.lines) == 1
    assert len(ax.mpl_axes.patches) == 1
    _, plain = plt.subplots()
    assert not hasattr(plain, "mpl_axes")


def test_view_builder_clips_regions_to_its_window(tmp_path: Path) -> None:
    fig, ax = mpl_subplots(2, 1)
    ax[0].plot([0.0, 1.0], [0.0, 1.0])
    ax[1].plot([0.0, 1.0], [1.0, 0.0])
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")

    view = (
        fig.view(id="zoom")
        .xlim(0.1, 0.6)
        .shade_regions([(0.0, 0.2, "a"), (0.5, 0.9, "b"), (0.3, 0.4, "unlisted")], colors={"a": "#ff0000", "b": "#00ff00"}, y=(0.0, 1.0))
        .hline(0.5, label="half", panel=2)
    )
    assert view.story(project) == "@figure(zoom)"
    refs = [action.ref for action in view.actions]
    assert refs == ["fig-plot", "fig-plot", "fig-plot", "fig-plot2"]
    assert "xLower = 0.1" in str(view.actions[1].action) and "xUpper = 0.2" in str(view.actions[1].action)
    assert "xLower = 0.5" in str(view.actions[2].action) and "xUpper = 0.6" in str(view.actions[2].action)
    assert fig.view_action(1, view.actions[3].action).ref == "fig-plot2"


def test_add_figure_without_id_or_title_is_refused() -> None:
    project = LimelightProject(title="T", authors=["Test"])
    fig, _ = mpl_subplots()
    with pytest.raises(ValueError, match="needs an id and a title"):
        project.add_figure(fig)
    with pytest.raises(TypeError, match="needs id and title"):
        project.add_figure(panels=[])
