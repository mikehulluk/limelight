"""Stacked panels: a figure whose axesSpecs render as vertically stacked plots.

`y2=` on add_line_figure is the original two-panel form (a Bode plot); the
renderer, PDF sizing and writer must all treat that as one case of N panels.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from matplotlib.figure import Figure

from limelight.app import LimelightRuntime
from limelight.reader import open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import LimelightProject


def _two_panel_package(tmp_path: Path) -> Path:
    project = LimelightProject(title="Bode", authors=["Test"])
    project.add_csv_dataset(
        id="src",
        arrays={"f": [1.0, 10.0, 100.0], "mag": [0.0, -3.0, -20.0], "phase": [0.0, -45.0, -90.0]},
    )
    project.add_line_figure(id="bode", title="Bode", data="src", x="f", y=["mag"], y2=["phase"])
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def _render(package_dir: Path, figure_id: str) -> tuple[Figure, dict]:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    with open_limelight(package_dir) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        runtime = LimelightRuntime(package, manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(runtime, figure, figure_id)
    return figure, manifest


def test_y2_renders_two_stacked_panels_sharing_x(tmp_path: Path) -> None:
    figure, manifest = _render(_two_panel_package(tmp_path), "bode")

    [figure_spec] = manifest["figures"]
    assert len(figure_spec["axesSpecs"]) == 2

    top, bottom = figure.axes
    assert top.get_position().y0 > bottom.get_position().y0
    assert bottom in top.get_shared_x_axes().get_siblings(top)
    assert top.get_title() == "Bode"
    assert bottom.get_title() == ""
    # Only the bottom panel labels the shared x axis.
    assert top.get_xlabel() == ""
    assert bottom.get_xlabel() == "f"


def _three_panel_package(tmp_path: Path) -> Path:
    from limelight.writer import AxisDataType, Panel

    project = LimelightProject(title="Three", authors=["Test"])
    project.add_csv_dataset(
        id="src",
        arrays={"t": [0.0, 1.0, 2.0], "a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0], "c": [0.0, 1.0, 0.0]},
    )
    project.add_line_figure(
        id="stack",
        title="Stack",
        data="src",
        x="t",
        y=["a"],
        panels=[
            Panel(lines=[("b", "B")], y_axis=AxisDataType.continuous(label="B value")),
            Panel(lines=[("c", "C")], id="custom-third"),
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def test_panels_write_one_axes_spec_each_in_one_share_group(tmp_path: Path) -> None:
    with open_limelight(_three_panel_package(tmp_path)) as package:
        manifest = package.manifest_json()
    validate_manifest_semantics(manifest)

    [figure_spec] = manifest["figures"]
    axes_specs = figure_spec["axesSpecs"]
    assert [spec["id"] for spec in axes_specs] == ["stack-plot", "stack-plot2", "custom-third"]
    assert len({spec["xAxis"]["shareGroup"] for spec in axes_specs}) == 1
    assert axes_specs[1]["yAxis"]["label"] == "B value"
    assert [action["y"] for action in axes_specs[2]["actions"]] == ["src['c']"]


def test_three_panels_render_as_a_stack(tmp_path: Path) -> None:
    figure, _ = _render(_three_panel_package(tmp_path), "stack")

    top, middle, bottom = figure.axes
    assert top.get_position().y0 > middle.get_position().y0 > bottom.get_position().y0
    siblings = top.get_shared_x_axes().get_siblings(top)
    assert middle in siblings and bottom in siblings
    assert top.get_title() == "Stack"
    assert [axes.get_xlabel() for axes in figure.axes] == ["", "", "t"]
    assert middle.get_ylabel() == "B value"


def test_y2_and_panels_combine_in_order(tmp_path: Path) -> None:
    from limelight.writer import Panel

    project = LimelightProject(title="Mixed", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0], "b": [2.0, 1.0], "c": [0.0, 1.0]})
    project.add_line_figure(id="mixed", title="Mixed", data="src", x="t", y=["a"], y2=["b"], panels=[Panel(lines=["c"])])
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
    [figure_spec] = manifest["figures"]
    assert [spec["actions"][0]["y"] for spec in figure_spec["axesSpecs"]] == ["src['a']", "src['b']", "src['c']"]


def test_panels_only_share_x_within_their_share_group(tmp_path: Path) -> None:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    with open_limelight(_three_panel_package(tmp_path)) as package:
        manifest = package.manifest_json()
        # Detach the last panel from the group the writer put it in.
        manifest["figures"][0]["axesSpecs"][2]["xAxis"]["shareGroup"] = "other"
        runtime = LimelightRuntime(package, manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(runtime, figure, "stack")

    top, middle, bottom = figure.axes
    siblings = top.get_shared_x_axes().get_siblings(top)
    assert middle in siblings
    assert bottom not in siblings


def test_figure_aspect_grows_with_each_panel() -> None:
    from limelight.app import figure_aspect

    def spec(n: int) -> dict:
        return {"axesSpecs": [{} for _ in range(n)]}

    assert figure_aspect(None) == 0.58
    assert figure_aspect(spec(1)) == 0.58
    assert figure_aspect(spec(2)) == pytest.approx(1.05)
    assert figure_aspect(spec(3)) > figure_aspect(spec(2))
