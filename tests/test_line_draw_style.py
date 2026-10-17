"""A line artist's drawStyle and linewidth.

A series that only changes at events (a balance, a rate) is drawn as steps,
not as straight segments between the samples, which would show slopes that
never happened. The tests reach the fields through the writer, the reader's
flattened manifest, the semantic checks and the matplotlib renderer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from matplotlib.figure import Figure

from limelight.app import LimelightRuntime
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import LimelightProject, LineArtist


def _package_with_line(tmp_path: Path, **line: object) -> Path:
    project = LimelightProject(title="Steps", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0, 2.0, 3.0], "y": [0.0, 10.0, 5.0, 20.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=[LineArtist("y", **line)])
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def _manifest(folder: Path) -> dict:
    with open_limelight(folder) as package:
        return package.manifest_json()


def _line_artist(manifest: dict) -> dict:
    [figure] = manifest["figures"]
    [axes_spec] = figure["axesSpecs"]
    [action] = axes_spec["actions"]
    payload = action.get("AxesActionAddData", action)
    return payload.get("line", payload)


def _drawn_line(folder: Path):
    from limelight.qt_app import _draw_plot_contents

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        runtime = LimelightRuntime(package, manifest)
        [axes_spec] = runtime.figure_specs["fig"]["axesSpecs"]
        axes = Figure().add_subplot(111)
        _draw_plot_contents(runtime, axes, "fig", axes_spec, figure_view_actions=[])
    [line] = axes.get_lines()
    return line


def test_the_writer_emits_both_fields() -> None:
    project = LimelightProject(title="Steps", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=[LineArtist("y", linewidth=4.0, draw_style="stepsPre")])
    [figure] = project.figure_specs
    text = figure.render()

    assert "linewidth = Some 4.0" in text
    assert "drawStyle = Some Limelight.DrawStyle.stepsPre" in text


def test_left_unset_both_fields_are_none() -> None:
    project = LimelightProject(title="Steps", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    [figure] = project.figure_specs
    text = figure.render()

    assert "linewidth = None Double" in text
    assert "drawStyle = None Limelight.DrawStyle" in text


def test_an_unknown_draw_style_is_refused_by_the_writer() -> None:
    project = LimelightProject(title="Steps", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=[LineArtist("y", draw_style="steps-pre")])
    [figure] = project.figure_specs

    with pytest.raises(ValueError, match="Unsupported DrawStyle"):
        figure.render()


def test_both_fields_round_trip_and_validate(tmp_path: Path) -> None:
    manifest = _manifest(_package_with_line(tmp_path, linewidth=4.0, draw_style="stepsPost"))

    validate_manifest_semantics(manifest)
    artist = _line_artist(manifest)
    assert artist["linewidth"] == 4.0
    assert artist["drawStyle"] == "stepsPost"


def test_a_non_positive_linewidth_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(_package_with_line(tmp_path, linewidth=0.0))

    with pytest.raises(LimelightError, match="linewidth"):
        validate_manifest_semantics(manifest)


@pytest.mark.parametrize(
    ("draw_style", "expected"),
    [(None, "default"), ("straight", "default"), ("stepsPre", "steps-pre"), ("stepsPost", "steps-post")],
)
def test_the_draw_style_reaches_matplotlib(tmp_path: Path, draw_style: str | None, expected: str) -> None:
    line = _drawn_line(_package_with_line(tmp_path, draw_style=draw_style))

    assert line.get_drawstyle() == expected


def test_the_linewidth_reaches_matplotlib_and_defaults_to_two(tmp_path: Path) -> None:
    assert _drawn_line(_package_with_line(tmp_path / "set", linewidth=5.0)).get_linewidth() == 5.0
    assert _drawn_line(_package_with_line(tmp_path / "unset")).get_linewidth() == 2.0


def test_a_log_y_axis_is_drawn_on_a_log_scale(tmp_path: Path) -> None:
    from limelight.qt_app import _draw_plot_contents
    from limelight.writer import AxisDataType

    project = LimelightProject(title="Log", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0, 2.0], "y": [1.0, 10.0, 100.0]})
    project.add_line_figure(
        id="fig", title="Fig", data="src", x="x", y=["y"], y_axis=AxisDataType.continuous(label="y", scale="Log")
    )
    project.write_folder(tmp_path / "pkg")

    with open_limelight(tmp_path / "pkg") as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        [axes_spec] = runtime.figure_specs["fig"]["axesSpecs"]
        axes = Figure().add_subplot(111)
        _draw_plot_contents(runtime, axes, "fig", axes_spec, figure_view_actions=[])

    assert axes.get_yscale() == "log"
    assert axes.get_xscale() == "linear"
    # Labelled at 1, 2 and 5 times each power of ten, as plain numbers, not 10².
    axes.set_ylim(1.0, 100.0)
    axes.figure.canvas.draw()
    labels = [label.get_text() for label in axes.get_yticklabels() if label.get_text()]
    assert {"1", "2", "5", "10", "20", "50", "100"} <= set(labels)
