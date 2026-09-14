"""The rect decorator: a shaded box bounded on both axes.

It is a VSpan with a yLimit, and that field is what tells the two apart once
dhall-to-json has flattened the union, so the tests reach the decorator both
through the writer and as the flattened dictionary the reader hands on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from limelight.app import LimelightRuntime
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import LimelightProject, axes_action_add_rect_decorator, figure_view_action


def _package_with_rect(tmp_path: Path, **rect: object) -> Path:
    project = LimelightProject(title="Rect", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0, 2.0, 3.0], "y": [0.0, 10.0, 5.0, 20.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="x", y=["y"])
    bounds = {"x_lower": 1.0, "x_upper": 2.0, "y_lower": 4.0, "y_upper": 12.0}
    bounds.update(rect)
    project.add_figure_view(id="view", ref="fig", actions=[figure_view_action("fig", axes_action_add_rect_decorator(**bounds))])
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def _flattened_rect(manifest: dict) -> dict:
    [view] = manifest["figureViews"]
    [action] = view["actions"]
    payload = action["action"]
    return payload.get("AxesActionAddDecorator", payload)


def test_the_writer_emits_the_rect_constructor_with_both_limits() -> None:
    action = axes_action_add_rect_decorator(x_lower=1.0, x_upper=2.0, y_lower=3.0, y_upper=4.0, label="box", color="red", alpha=0.5)
    text = action.render() if hasattr(action, "render") else str(action)

    assert "Limelight.AxesDecorator.AxesDecoratorRect" in text
    assert "Limelight.AxisLimit.AxisLimitXFloat" in text
    assert "Limelight.AxisLimit.AxisLimitYFloat" in text
    assert "xLower = 1.0" in text and "yUpper = 4.0" in text


def test_date_bounds_become_a_utc_time_limit() -> None:
    text = str(axes_action_add_rect_decorator(x_lower="2024-01-01", x_upper="2024-02-01", y_lower=0.0, y_upper=1.0))

    assert "AxisLimitXUtcTime" in text
    assert 'xStart = "2024-01-01"' in text


def test_mixed_date_and_number_bounds_on_one_axis_are_refused() -> None:
    with pytest.raises(TypeError):
        axes_action_add_rect_decorator(x_lower="2024-01-01", x_upper=5.0, y_lower=0.0, y_upper=1.0)


def test_a_rect_round_trips_and_validates(tmp_path: Path) -> None:
    with open_limelight(_package_with_rect(tmp_path, label="box", color="#2a9d8f", alpha=0.25)) as package:
        manifest = package.manifest_json()

    validate_manifest_semantics(manifest)
    rect = _flattened_rect(manifest)
    assert rect["xLimit"] == {"xLower": 1.0, "xUpper": 2.0}
    assert rect["yLimit"] == {"yLower": 4.0, "yUpper": 12.0}
    assert rect["label"] == "box"
    assert rect["alpha"] == 0.25


def test_a_rect_with_no_height_is_rejected(tmp_path: Path) -> None:
    with open_limelight(_package_with_rect(tmp_path, y_lower=5.0, y_upper=5.0)) as package:
        manifest = package.manifest_json()

    with pytest.raises(LimelightError, match="equal bounds"):
        validate_manifest_semantics(manifest)


def test_a_rect_with_a_bad_colour_is_rejected(tmp_path: Path) -> None:
    with open_limelight(_package_with_rect(tmp_path, color="not-a-colour")) as package:
        manifest = package.manifest_json()

    with pytest.raises(LimelightError, match="invalid color"):
        validate_manifest_semantics(manifest)


def test_a_rect_is_drawn_without_moving_the_axes(tmp_path: Path) -> None:
    from limelight.qt_app import _actions_for_axes, _draw_plot_contents

    with open_limelight(_package_with_rect(tmp_path, x_lower=-50.0, x_upper=50.0, y_lower=-50.0, y_upper=50.0, label="box")) as package:
        manifest = package.manifest_json()
        runtime = LimelightRuntime(package, manifest)
        [view] = manifest["figureViews"]
        [axes_spec] = runtime.figure_specs["fig"]["axesSpecs"]

        axes = Figure().add_subplot(111)
        actions = _actions_for_axes(view["actions"], axes_spec["id"])
        assert actions, "the view's action should be bound to the figure's axes"
        _draw_plot_contents(runtime, axes, "fig", axes_spec, figure_view_actions=actions)

    [box] = [artist for artist in axes.get_children() if isinstance(artist, Rectangle) and artist is not axes.patch]
    assert box.get_xy() == (-50.0, -50.0)
    assert (box.get_width(), box.get_height()) == (100.0, 100.0)
    assert any(text.get_text() == "box" for text in axes.texts)

    # The data runs 0..3 and 0..20; a box far outside it must not stretch the view.
    x_lower, x_upper = axes.get_xlim()
    y_lower, y_upper = axes.get_ylim()
    assert x_lower > -10 and x_upper < 10
    assert y_lower > -10 and y_upper < 30
