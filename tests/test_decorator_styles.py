"""Styled decorators, filled lines and scatter edges, end to end.

Each is written with the writer, read back as a manifest, validated, and
drawn by the app - so a field the dhall schema, the validator and the
renderer disagree about fails here rather than in a reader's window.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from matplotlib.collections import PathCollection
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure

from limelight.app import LimelightRuntime
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import (
    LimelightProject,
    LineArtist,
    Panel,
    ScatterArtist,
    axes_action_add_axis_window_decorator,
    axes_action_add_y_axis_window_decorator,
)


def _render(tmp_path: Path, *, lines=(), scatters=(), actions=()) -> tuple[Figure, dict]:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    project = LimelightProject(title="Styles", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0, 2.0, 3.0], "y": [1.0, 3.0, 2.0, 4.0]})
    project.add_figure(id="fig", title="Fig", panels=[Panel(lines=lines, scatters=scatters, actions=actions, data="src", x="x")])
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        runtime = LimelightRuntime(package, manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(runtime, figure, "fig")
    return figure, manifest


def _manifest(tmp_path: Path, **panel: object) -> dict:
    project = LimelightProject(title="Styles", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    project.add_figure(id="fig", title="Fig", panels=[Panel(data="src", x="x", **panel)])
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    with open_limelight(folder) as package:
        return package.manifest_json()


def test_a_horizontal_line_is_drawn_at_its_y_value_in_its_style(tmp_path: Path) -> None:
    figure, _ = _render(
        tmp_path,
        lines=["y"],
        actions=[axes_action_add_y_axis_window_decorator(lower=2.5, upper=2.5, label="limit", color="#ff0000", linewidth=0.9)],
    )
    [axes] = figure.axes
    [hline] = [line for line in axes.get_lines() if line.get_linestyle() == ":"]
    assert list(hline.get_ydata()) == [2.5, 2.5]
    assert hline.get_color() == "#ff0000"
    assert hline.get_linewidth() == pytest.approx(0.9)
    assert any(text.get_text() == "limit" for text in axes.texts)


def test_a_horizontal_band_shades_between_its_y_values(tmp_path: Path) -> None:
    figure, _ = _render(
        tmp_path,
        lines=["y"],
        actions=[axes_action_add_y_axis_window_decorator(lower=1.5, upper=2.5, label="band", alpha=0.2)],
    )
    [axes] = figure.axes
    [band] = axes.patches
    # x runs in axes coordinates, y in data: the band spans the panel's width.
    _, y_lower, _, y_upper = band.get_bbox().extents
    assert (y_lower, y_upper) == pytest.approx((1.5, 2.5))
    assert band.get_alpha() == pytest.approx(0.2)


def test_a_vertical_line_takes_its_colour(tmp_path: Path) -> None:
    figure, manifest = _render(
        tmp_path,
        lines=["y"],
        actions=[axes_action_add_axis_window_decorator(lower=1.5, upper=1.5, label="mark", color="#00ff00")],
    )
    [axes] = figure.axes
    [vline] = [line for line in axes.get_lines() if line.get_linestyle() == ":"]
    assert list(vline.get_xdata()) == [1.5, 1.5]
    assert vline.get_color() == "#00ff00"
    actions = manifest["figures"][0]["axesSpecs"][0]["actions"]
    [decorator] = [action.get("AxesActionAddDecorator", action) for action in actions if "xLimit" in action.get("AxesActionAddDecorator", action)]
    assert decorator["floatingLabel"] == "mark"


def test_a_filled_line_shades_down_to_its_baseline_and_carries_the_legend_label(tmp_path: Path) -> None:
    figure, _ = _render(
        tmp_path,
        lines=[LineArtist(array="y", label="Y", draw_style="stepsPost", fill_color="#336699", fill_alpha=0.5, fill_baseline=0.5)],
    )
    [axes] = figure.axes
    [fill] = axes.collections
    vertices = np.concatenate([path.vertices for path in fill.get_paths()])
    assert vertices[:, 1].min() == pytest.approx(0.5)
    assert vertices[:, 1].max() == pytest.approx(4.0)
    assert fill.get_label() == "Y"
    assert fill.get_alpha() == pytest.approx(0.5)
    # The label moved to the fill, so the legend does not show the line as well.
    assert axes.get_lines()[0].get_label().startswith("_")


def test_scatter_edges_default_to_thin_white_and_can_be_turned_off(tmp_path: Path) -> None:
    figure, _ = _render(tmp_path, scatters=[ScatterArtist(array="y"), ScatterArtist(array="y", id="plain", edge_width=0.0, edge_color="black")])
    [axes] = figure.axes
    default, plain = [collection for collection in axes.collections if isinstance(collection, PathCollection)]
    assert tuple(default.get_edgecolor()[0]) == to_rgba("white")
    assert default.get_linewidths()[0] == pytest.approx(0.45)
    assert plain.get_linewidths()[0] == 0.0
    assert tuple(plain.get_edgecolor()[0]) == to_rgba("black")


def test_a_negative_scatter_edge_width_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, scatters=[ScatterArtist(array="y", edge_width=-1.0)])
    with pytest.raises(LimelightError, match="edgeWidth"):
        validate_manifest_semantics(manifest)


def test_a_horizontal_band_with_a_bad_alpha_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, lines=["y"], actions=[axes_action_add_y_axis_window_decorator(lower=0.0, upper=1.0, alpha=2.0)])
    with pytest.raises(LimelightError, match="AxesDecoratorHSpan alpha"):
        validate_manifest_semantics(manifest)
