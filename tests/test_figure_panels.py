"""Stacked panels: a figure whose axesSpecs render as vertically stacked plots.

A figure is a list of panels, each said in full (a Bode plot is two); the
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
    from limelight.writer import Panel

    project = LimelightProject(title="Bode", authors=["Test"])
    project.add_csv_dataset(
        id="src",
        arrays={"f": [1.0, 10.0, 100.0], "mag": [0.0, -3.0, -20.0], "phase": [0.0, -45.0, -90.0]},
    )
    project.add_figure(
        id="bode",
        title="Bode",
        panels=[
            Panel(lines=["mag"], data="src", x="f"),
            Panel(lines=["phase"], data="src", x="f"),
        ],
    )
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


def test_stacked_panels_line_their_y_labels_up(tmp_path: Path) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from limelight.writer import Panel

    project = LimelightProject(title="Wide and narrow", authors=["Test"])
    # The top panel's tick labels are far wider than the bottom's, which is
    # what pushes one y label further from its axis than the other.
    project.add_csv_dataset(
        id="src",
        arrays={"f": [1.0, 2.0, 3.0], "wide": [-123456.0, 0.0, 123456.0], "narrow": [0.0, 1.0, 2.0]},
    )
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[Panel(lines=["wide"], data="src", x="f"), Panel(lines=["narrow"], data="src", x="f")],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    figure, _ = _render(folder, "fig")
    FigureCanvasAgg(figure).draw()
    top, bottom = figure.axes
    top_x = top.yaxis.label.get_window_extent().x0
    bottom_x = bottom.yaxis.label.get_window_extent().x0
    assert abs(top_x - bottom_x) < 0.5


def _three_panel_package(tmp_path: Path) -> Path:
    from limelight.writer import AxisDataType, Panel

    project = LimelightProject(title="Three", authors=["Test"])
    project.add_csv_dataset(
        id="src",
        arrays={"t": [0.0, 1.0, 2.0], "a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0], "c": [0.0, 1.0, 0.0]},
    )
    project.add_figure(
        id="stack",
        title="Stack",
        panels=[
            Panel(lines=["a"], data="src", x="t"),
            Panel(lines=[("b", "B")], data="src", x="t", y_axis=AxisDataType.continuous(label="B value")),
            Panel(lines=[("c", "C")], data="src", x="t", id="custom-third"),
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
    # None names a share group, so the renderer reads them as one stack.
    assert all("shareGroup" not in spec["xAxis"] for spec in axes_specs)
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


def test_panels_render_in_the_order_they_are_given(tmp_path: Path) -> None:
    from limelight.writer import Panel

    project = LimelightProject(title="Mixed", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0], "b": [2.0, 1.0], "c": [0.0, 1.0]})
    project.add_figure(
        id="mixed",
        title="Mixed",
        panels=[Panel(lines=[name], data="src", x="t") for name in ("a", "b", "c")],
    )
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


def _sized_package(tmp_path: Path, *, panels=None, **figure_kwargs: object) -> Path:
    from limelight.writer import Panel

    project = LimelightProject(title="Sized", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0, 2.0], "a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]})
    if panels is None:
        panels = [Panel(lines=["a"], data="src", x="t")]
    project.add_figure(id="fig", title="Fig", panels=panels, **figure_kwargs)
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def test_height_ratios_size_the_stacked_panels(tmp_path: Path) -> None:
    from limelight.writer import Panel

    package = _sized_package(
        tmp_path,
        panels=[
            Panel(lines=["a"], data="src", x="t", height=3.0),
            Panel(lines=["b"], data="src", x="t"),
        ],
    )
    figure, manifest = _render(package, "fig")

    [figure_spec] = manifest["figures"]
    assert [spec.get("heightRatio") for spec in figure_spec["axesSpecs"]] == [3.0, None]
    assert all(spec.get("frame") is None for spec in figure_spec["axesSpecs"])
    top, bottom = figure.axes
    ratio = top.get_position().height / bottom.get_position().height
    assert ratio == pytest.approx(3.0, rel=0.05)


def test_frames_place_panels_exactly_and_side_by_side(tmp_path: Path) -> None:
    from limelight.writer import Panel

    folder = _sized_package(
        tmp_path,
        panels=[
            Panel(lines=["a"], data="src", x="t", frame=(0.1, 0.15, 0.35, 0.75)),
            Panel(lines=["b"], data="src", x="t", frame=(0.6, 0.15, 0.35, 0.75)),
        ],
    )
    figure, manifest = _render(folder, "fig")

    [figure_spec] = manifest["figures"]
    assert figure_spec["axesSpecs"][1]["frame"] == {"left": 0.6, "bottom": 0.15, "width": 0.35, "height": 0.75}
    left, right = figure.axes
    assert left.get_position().bounds == pytest.approx((0.1, 0.15, 0.35, 0.75))
    assert right.get_position().bounds == pytest.approx((0.6, 0.15, 0.35, 0.75))
    # Neither sits below the other, so both label the x-axis.
    assert [axes.get_xlabel() for axes in figure.axes] == ["t", "t"]
    # An exact layout is the author's; nothing re-fits it.
    assert figure.get_layout_engine() is None


def test_figure_size_sets_width_and_aspect(tmp_path: Path) -> None:
    from limelight.app import figure_aspect, figure_width_px
    from limelight.writer import FigureSize, ImageWidth

    folder = _sized_package(tmp_path, size=FigureSize(width=ImageWidth.percent(50), aspect=1.25))
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
    validate_manifest_semantics(manifest)
    [figure_spec] = manifest["figures"]
    assert figure_spec["size"] == {"width": {"value": 50.0, "unit": "percent"}, "aspect": 1.25}
    assert figure_aspect(figure_spec) == 1.25
    assert figure_width_px(figure_spec, 800, 96.0) == 400

    folder = _sized_package(tmp_path / "mm", size=FigureSize(width=ImageWidth.millimetres(50.8)))
    with open_limelight(folder) as package:
        [figure_spec] = package.manifest_json()["figures"]
    assert figure_width_px(figure_spec, 800, 96.0) == 192  # 2 inches at 96 dpi
    assert figure_width_px(figure_spec, 800, 192.0) == 384  # and at a zoomed resolution
    assert figure_width_px(figure_spec, 100, 96.0) == 100  # never wider than the column
    assert figure_aspect(figure_spec) == pytest.approx(0.58)  # no aspect: from the one panel


@pytest.mark.parametrize(
    ("figure_kwargs", "message"),
    [
        ({"size": "FigureSize(width=ImageWidth.percent(120))"}, "wider than the column"),
        ({"size": "FigureSize(aspect=0)"}, "aspect 0 must be positive"),
        ({"panels": '[Panel(lines=["a"], data="src", x="t", height=-1.0)]'}, "heightRatio -1 must be positive"),
        (
            {"panels": '[Panel(lines=["a"], data="src", x="t", frame=(0.5, 0.5, 0.6, 0.4))]'},
            "within the figure",
        ),
        (
            {"panels": '[Panel(lines=["a"], data="src", x="t", frame=(0.1, 0.1, 0.0, 0.5))]'},
            "positive width and height",
        ),
    ],
)
def test_bad_sizes_are_rejected(tmp_path: Path, figure_kwargs: dict, message: str) -> None:
    from limelight.reader import LimelightError
    # Named in the parametrised source, which is evaluated here.
    from limelight.writer import FigureSize, ImageWidth, Panel  # noqa: F401

    kwargs = {key: (eval(value) if isinstance(value, str) else value) for key, value in figure_kwargs.items()}
    with open_limelight(_sized_package(tmp_path, **kwargs)) as package:
        manifest = package.manifest_json()
    with pytest.raises(LimelightError, match=message):
        validate_manifest_semantics(manifest)


# --- A Panel is a whole AxesSpec ------------------------------------------


def test_a_panel_carries_every_kind_of_artist_not_just_lines(tmp_path: Path) -> None:
    from limelight.writer import Panel, ScatterArtist, StemArtist

    project = LimelightProject(title="Kinds", authors=["Test"])
    project.add_csv_dataset(
        id="src",
        arrays={"t": [0.0, 1.0, 2.0], "a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0], "c": [0.0, 1.0, 0.0]},
    )
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[
            Panel(lines=["a"], data="src", x="t"),
            Panel(scatters=[ScatterArtist(array="b", label="B")], data="src", x="t"),
            Panel(stems=[StemArtist(array="c", label="C")], data="src", x="t"),
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    figure, manifest = _render(folder, "fig")
    validate_manifest_semantics(manifest)

    [figure_spec] = manifest["figures"]
    scatter_panel, stem_panel = figure_spec["axesSpecs"][1:]
    assert [action["x"] for action in scatter_panel["actions"]] == ["src['t']"]
    assert [action["y"] for action in scatter_panel["actions"]] == ["src['b']"]
    assert [action["baseline"] for action in stem_panel["actions"]] == [0.0]
    # All three drew something.
    assert all(axes.collections or axes.lines for axes in figure.axes)


def test_a_panel_titles_itself_and_the_figure_titles_the_first(tmp_path: Path) -> None:
    from limelight.writer import Panel

    project = LimelightProject(title="Titled", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0], "b": [2.0, 1.0]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[
            Panel(lines=["a"], data="src", x="t"),
            Panel(lines=["b"], data="src", x="t", title="Below"),
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    figure, manifest = _render(folder, "fig")
    [figure_spec] = manifest["figures"]
    assert figure_spec["axesSpecs"][1]["title"] == "Below"
    top, bottom = figure.axes
    assert top.get_title() == "Fig"
    assert bottom.get_title() == "Below"


def test_a_panel_plots_another_dataset_when_it_names_one(tmp_path: Path) -> None:
    from limelight.writer import Panel

    project = LimelightProject(title="Two sources", authors=["Test"])
    project.add_csv_dataset(id="one", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0]})
    project.add_csv_dataset(id="two", arrays={"u": [0.0, 1.0], "b": [5.0, 6.0]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[
            Panel(lines=["a"], data="one", x="t"),
            Panel(lines=["b"], data="two", x="u"),
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
    validate_manifest_semantics(manifest)

    [figure_spec] = manifest["figures"]
    [action] = figure_spec["axesSpecs"][1]["actions"]
    assert action["y"] == "two['b']" and action["xOverride"] == "two['u']"


def test_a_panel_carries_its_own_limits_and_decorators(tmp_path: Path) -> None:
    from limelight.writer import Panel, axes_action_add_rect_decorator, axes_action_set_y_float_limits

    project = LimelightProject(title="Marked", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0], "b": [2.0, 1.0]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[
            Panel(lines=["a"], data="src", x="t"),
            Panel(
                lines=["b"],
                data="src",
                x="t",
                actions=[
                    axes_action_set_y_float_limits(0.0, 10.0),
                    axes_action_add_rect_decorator(
                        x_lower=0.1, x_upper=0.4, y_lower=1.0, y_upper=2.0, label="Window"
                    ),
                ],
            )
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    figure, manifest = _render(folder, "fig")
    validate_manifest_semantics(manifest)

    [figure_spec] = manifest["figures"]
    actions = figure_spec["axesSpecs"][1]["actions"]
    # The line, then the limits and the rectangle the panel itself carries.
    assert len(actions) == 3
    bottom = figure.axes[1]
    assert bottom.get_ylim() == (0.0, 10.0)
    assert bottom.patches


def test_a_panel_with_its_own_x_axis_stands_apart_from_the_stack(tmp_path: Path) -> None:
    from limelight.writer import AxisDataType, Panel

    project = LimelightProject(title="Own x", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0], "u": [5.0, 6.0], "b": [2.0, 1.0]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[
            Panel(lines=["a"], data="src", x="t", x_axis=AxisDataType.continuous(label="t", share_group="together")),
            Panel(
                lines=["b"],
                data="src",
                x="u",
                x_axis=AxisDataType.continuous(label="Its own x", share_group="apart"),
            ),
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    figure, manifest = _render(folder, "fig")
    validate_manifest_semantics(manifest)

    [figure_spec] = manifest["figures"]
    top_spec, bottom_spec = figure_spec["axesSpecs"]
    assert top_spec["xAxis"]["shareGroup"] == "together"
    assert bottom_spec["xAxis"]["shareGroup"] == "apart"
    assert bottom_spec["xAxis"]["label"] == "Its own x"

    top, bottom = figure.axes
    assert bottom not in top.get_shared_x_axes().get_siblings(top)
    # Standing apart, it labels its own x, and so does the panel above it.
    assert top.get_xlabel() == "t" and bottom.get_xlabel() == "Its own x"


def test_one_line_figure_is_one_panel(tmp_path: Path) -> None:
    project = LimelightProject(title="One", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0]})
    project.add_line_figure(id="fig", title="Fig", data="src", x="t", y=["a"])
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    figure, manifest = _render(folder, "fig")
    [figure_spec] = manifest["figures"]
    [axes_spec] = figure_spec["axesSpecs"]
    assert axes_spec["id"] == "fig-plot"
    assert axes_spec["xAxis"]["id"] == "fig-x-axis" and axes_spec["yAxis"]["id"] == "fig-y-axis"
    [axes] = figure.axes
    assert axes.get_title() == "Fig" and axes.get_xlabel() == "t"
