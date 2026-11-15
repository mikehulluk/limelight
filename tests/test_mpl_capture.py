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
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from limelight import LimelightProject, MplCaptureWarning, mpl_capture, mpl_subplots, open_limelight
from limelight.app import LimelightRuntime
from limelight.mpl_capture import CapturingAxes, CapturingDataset
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


def test_fill_between_without_its_plot_is_drawn_in_the_preview_and_warns() -> None:
    _, ax = mpl_subplots()
    with pytest.warns(MplCaptureWarning, match="no plot"):
        ax.fill_between([0.0, 1.0], [1.0, 2.0])
    assert len(ax.mpl_axes.collections) == 1


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
    with mpl_capture.capture() as figures:
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


# --- matplotlib's own call shapes ------------------------------------------


def test_plot_takes_format_strings_bare_y_and_several_lines(tmp_path: Path) -> None:
    t = np.linspace(0.0, 1.0, 5)
    fig, ax = mpl_subplots()
    returned = ax.plot(t, t, "r--", t, t**2, "g^")
    ax.plot(t**3)
    assert isinstance(returned, list) and len(returned) == 2
    dashed, triangles, bare = ax.lines
    assert (dashed.color, dashed.linestyle, dashed.marker) == ("r", "--", None)
    assert (triangles.color, triangles.linestyle, triangles.marker) == ("g", "none", "^")
    assert bare.x == "index"

    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    figure, _ = _write_and_render(project, tmp_path, "fig")
    first = figure.axes[0].get_lines()[0]
    assert (first.get_linestyle(), first.get_color()) == ("--", "r")


def test_keyword_aliases_are_captured() -> None:
    _, ax = mpl_subplots()
    ax.plot([0.0, 1.0], [1.0, 2.0], lw=3, ls=":", c="k", ds="steps-mid")
    [line] = ax.lines
    assert (line.linewidth, line.linestyle, line.color, line.draw_style) == (3, ":", "k", "stepsMid")


def test_limits_take_every_matplotlib_form() -> None:
    _, ax = mpl_subplots()
    ax.set_xlim((2.0, 3.0))
    ax.set_ylim(bottom=-1.0, top=4.0)
    ax.set_xlim(left=0.0)
    rendered = [str(action) for action in ax.actions]
    assert "xLower = 2.0" in rendered[0] and "xUpper = 3.0" in rendered[0]
    assert "yLower = -1.0" in rendered[1] and "yUpper = 4.0" in rendered[1]
    assert "xLower = 0.0" in rendered[2] and "xUpper = 3.0" in rendered[2]


def test_subplots_returns_matplotlibs_shape() -> None:
    _, grid = mpl_subplots(2, 2)
    assert grid.shape == (2, 2)
    assert isinstance(grid[0, 1], CapturingAxes)
    assert all(isinstance(axes, CapturingAxes) for axes in grid.flat)
    _, column = mpl_subplots(3, 1)
    assert column.shape == (3,)
    _, single = mpl_subplots()
    assert isinstance(single, CapturingAxes)


# --- nothing changes the figure silently ------------------------------------


def test_uncapturable_keywords_reach_the_preview_and_warn() -> None:
    _, ax = mpl_subplots()
    with pytest.warns(MplCaptureWarning, match="zorder=9") as caught:
        [line] = ax.plot([0.0, 1.0], [1.0, 2.0], lw=5, zorder=9)
    assert line.get_zorder() == 9 and line.get_linewidth() == 5
    assert ax.lines[0].linewidth == 5
    # The warning points at this file, not at mpl_capture.
    assert caught[0].filename == __file__


def test_scatter_captures_alpha_size_and_per_point_colour(tmp_path: Path) -> None:
    x = np.linspace(0.0, 1.0, 6)
    y = x**2
    fig, ax = mpl_subplots()
    ax.scatter(x, y, s=80, c=y, alpha=0.4, ec="k", lw=0.5, name="pts")
    [scatter] = ax.scatters
    assert (scatter.size, scatter.alpha, scatter.edge_color, scatter.edge_width) == (80.0, 0.4, "k", 0.5)
    # c is y itself, so it reuses y's column rather than copying it.
    assert scatter.color_by == scatter.array == "pts"

    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    figure, _ = _write_and_render(project, tmp_path, "fig")
    [points] = [c for c in figure.axes[0].collections if len(c.get_offsets()) == 6]
    assert points.get_sizes()[0] == 80.0
    assert points.get_alpha() == pytest.approx(0.4)


def test_fidelity_losses_all_warn() -> None:
    _, ax = mpl_subplots()
    with pytest.warns(MplCaptureWarning, match="symlog"):
        ax.set_yscale("symlog")
    with pytest.warns(MplCaptureWarning, match="marker='.'"):
        ax.plot([0.0, 1.0], [1.0, 2.0], "k.")
    with pytest.warns(MplCaptureWarning, match="xmin=/xmax="):
        ax.axhline(0.5, 0.0, 0.5)
    with pytest.warns(MplCaptureWarning, match="linestyle"):
        ax.axvline(0.5, linestyle="--")
    assert ax.y_scale == "Linear"


def test_steps_mid_round_trips(tmp_path: Path) -> None:
    fig, ax = mpl_subplots()
    ax.plot([0.0, 1.0, 2.0], [1.0, 3.0, 2.0], drawstyle="steps-mid")
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    figure, _ = _write_and_render(project, tmp_path, "fig")
    assert figure.axes[0].get_lines()[0].get_drawstyle() == "steps-mid"


def test_fill_between_on_a_y_two_lines_drew_is_not_guessed() -> None:
    t, y = np.arange(3.0), np.array([1.0, 2.0, 1.0])
    _, ax = mpl_subplots()
    ax.plot(t, y, label="first")
    ax.plot(t, y, label="second")
    with pytest.warns(MplCaptureWarning, match="ambiguous"):
        ax.fill_between(t, y, color="g")
    assert [line.fill_color for line in ax.lines] == [None, None]


def test_fill_between_without_a_colour_captures_the_one_matplotlib_picked() -> None:
    t, y = np.arange(3.0), np.array([1.0, 2.0, 1.0])
    _, ax = mpl_subplots()
    ax.plot(t, y)
    fill = ax.fill_between(t, y, alpha=0.3)
    assert ax.lines[0].fill_color == to_hex(fill.get_facecolor()[0], keep_alpha=False)


# --- layout and data ---------------------------------------------------------


def _linked(figure: Figure, axis: str) -> bool:
    first, second = figure.axes
    siblings = (first.get_shared_x_axes() if axis == "x" else first.get_shared_y_axes()).get_siblings(first)
    return second in siblings


@pytest.mark.parametrize(
    ("sharex", "sharey", "x_linked", "y_linked"),
    [(False, False, False, False), (True, False, True, False), (False, True, False, True), ("all", "all", True, True)],
)
def test_shared_axes_link_in_the_app_as_in_matplotlib(tmp_path: Path, sharex, sharey, x_linked, y_linked) -> None:
    fig, (top, bottom) = mpl_subplots(2, 1, sharex=sharex, sharey=sharey)
    top.plot([0.0, 1.0], [0.0, 1.0])
    bottom.plot([0.0, 2.0], [0.0, 5.0])
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    figure, _ = _write_and_render(project, tmp_path, "fig")
    assert (_linked(figure, "x"), _linked(figure, "y")) == (x_linked, y_linked)
    preview = fig.mpl_figure
    assert (_linked(preview, "x"), _linked(preview, "y")) == (x_linked, y_linked)


def test_height_ratios_become_panel_heights() -> None:
    fig, (top, bottom) = mpl_subplots(2, 1, height_ratios=[3, 1])
    top.plot([0.0, 1.0], [0.0, 1.0])
    bottom.plot([0.0, 1.0], [1.0, 0.0])
    panels = [axes.to_panel(data="d") for axes in fig.panels]
    assert [panel.height for panel in panels] == [3.0, 1.0]


def test_panels_with_different_lengths_get_a_dataset_per_length(tmp_path: Path) -> None:
    fig, (top, bottom) = mpl_subplots(2, 1)
    top.plot(np.linspace(0.0, 1.0, 5), np.zeros(5))
    bottom.plot(np.linspace(0.0, 1.0, 9), np.ones(9))
    project = LimelightProject(title="T", authors=["Test"])
    fig.add_to(project, id="fig", title="Fig")
    assert [dataset.id for dataset in project.datasets] == ["fig-data", "fig-data-n9"]
    figure, _ = _write_and_render(project, tmp_path, "fig")
    assert len(figure.axes[1].get_lines()[0].get_xdata()) == 9


def test_a_shared_dataset_takes_columns_captured_after_it_was_added(tmp_path: Path) -> None:
    t = np.linspace(0.0, 1.0, 4)
    shared = CapturingDataset()
    first, ax1 = mpl_subplots(dataset=shared)
    ax1.plot(t, t)
    project = LimelightProject(title="T", authors=["Test"])
    first.add_to(project, id="first", title="First")
    second, ax2 = mpl_subplots(dataset=shared)
    ax2.plot(t, t * 3)
    second.add_to(project, id="second", title="Second")
    assert [dataset.id for dataset in project.datasets] == ["first-data"]
    figure, _ = _write_and_render(project, tmp_path, "second")
    assert np.allclose(figure.axes[0].get_lines()[0].get_ydata(), t * 3)


def test_panels_from_different_datasets_are_refused() -> None:
    project = LimelightProject(title="T", authors=["Test"])
    with pytest.raises(ValueError, match="different CapturingDatasets"):
        project.add_mpl_figure(id="fig", title="Fig", panels=[CapturingAxes(Figure().add_subplot()), CapturingAxes(Figure().add_subplot())])


def test_to_panel_defaults_to_the_dataset_id() -> None:
    fig, ax = mpl_subplots()
    ax.plot([0.0, 1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="dataset id"):
        ax.to_panel()
    fig.configure(dataset_id="d")
    assert ax.to_panel().data == "d"


def test_limelight_mpl_capture_is_the_module() -> None:
    import limelight
    import limelight.mpl_capture as module

    assert limelight.mpl_capture is module
    assert limelight.mpl_subplots is module.subplots
