"""Discrete axes: their categories, and a discrete y-axis.

A discrete axis numbers its categories 0, 1, 2... once for the whole axes,
so every artist on it puts a category in the same place. The axis may fix
the order of its categories; otherwise they follow first appearance. The
tests reach the axis through the writer, the reader's flattened manifest,
the semantic checks and the matplotlib renderer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from matplotlib.figure import Figure

from limelight.app import LimelightRuntime
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import axis_categories, axis_data_type_kind, validate_manifest_semantics
from limelight.writer import AxisDataType, LimelightProject, LineArtist, Panel, ScatterArtist, StemArtist

EVENTS = ["pay", "interest"]


def _event_package(tmp_path: Path, *, categories: list[str] | None = None, values: dict[str, str] | None = None) -> Path:
    """Two event series on a discrete y-axis, each one category at a few times."""
    values = values or {"a": "interest", "b": "pay"}
    project = LimelightProject(title="Events", authors=["Test"])
    for dataset_id, value in values.items():
        project.add_csv_dataset(id=dataset_id, arrays={"t": [0.0, 1.0, 2.0], "event": [value] * 3})
    project.add_line_figure(
        id="fig",
        title="Fig",
        data="a",
        x="t",
        y=[LineArtist("event", data=dataset_id, label=dataset_id) for dataset_id in values],
        x_axis=AxisDataType.continuous(label="t"),
        y_axis=AxisDataType.discrete(label="Event", categories=categories),
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def _manifest(folder: Path) -> dict:
    with open_limelight(folder) as package:
        return package.manifest_json()


def _drawn_axes(folder: Path):
    from limelight.qt_app import _draw_plot_contents

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        runtime = LimelightRuntime(package, manifest)
        [axes_spec] = runtime.figure_specs["fig"]["axesSpecs"]
        axes = Figure().add_subplot(111)
        _draw_plot_contents(runtime, axes, "fig", axes_spec, figure_view_actions=[])
    return axes


def _y_ticks(axes) -> list[tuple[float, str]]:
    return [(tick, label.get_text()) for tick, label in zip(axes.get_yticks(), axes.get_yticklabels())]


def test_the_writer_emits_the_categories_and_an_empty_list_without_them() -> None:
    text = AxisDataType.discrete(categories=EVENTS).render()

    assert text.startswith("Limelight.AxisDataType.discrete")
    assert text.index('"pay"') < text.index('"interest"')
    assert "categories = [] : List Text" in AxisDataType.discrete().render()


@pytest.mark.parametrize("categories", [[], ["pay", "pay"]])
def test_the_writer_refuses_empty_or_repeated_categories(categories: list[str]) -> None:
    with pytest.raises(ValueError):
        AxisDataType.discrete(categories=categories)


def test_categories_round_trip_and_validate(tmp_path: Path) -> None:
    manifest = _manifest(_event_package(tmp_path, categories=EVENTS))

    validate_manifest_semantics(manifest)
    y_axis = manifest["figures"][0]["axesSpecs"][0]["yAxis"]
    assert axis_data_type_kind(y_axis) == "discrete"
    assert axis_categories(y_axis) == EVENTS


def test_no_categories_round_trip_as_first_appearance(tmp_path: Path) -> None:
    manifest = _manifest(_event_package(tmp_path))

    validate_manifest_semantics(manifest)
    y_axis = manifest["figures"][0]["axesSpecs"][0]["yAxis"]
    assert axis_data_type_kind(y_axis) == "discrete"
    assert axis_categories(y_axis) is None


def test_a_bare_discrete_from_an_older_package_still_reads() -> None:
    axis_spec = {"id": "old", "dataType": "discrete"}

    assert axis_data_type_kind(axis_spec) == "discrete"
    assert axis_categories(axis_spec) is None


def test_declared_categories_set_the_order_of_the_rows(tmp_path: Path) -> None:
    axes = _drawn_axes(_event_package(tmp_path, categories=EVENTS))

    assert _y_ticks(axes) == [(0.0, "pay"), (1.0, "interest")]
    lines = {line.get_label(): set(line.get_ydata()) for line in axes.get_lines()}
    assert lines == {"a": {1.0}, "b": {0.0}}


def test_without_categories_rows_follow_first_appearance_across_artists(tmp_path: Path) -> None:
    axes = _drawn_axes(_event_package(tmp_path))

    # Each artist draws one category, yet they get rows of their own:
    assert _y_ticks(axes) == [(0.0, "interest"), (1.0, "pay")]
    lines = {line.get_label(): set(line.get_ydata()) for line in axes.get_lines()}
    assert lines == {"a": {0.0}, "b": {1.0}}


def test_a_declared_category_nothing_draws_still_has_its_row(tmp_path: Path) -> None:
    axes = _drawn_axes(_event_package(tmp_path, categories=["pay", "fee", "interest"]))

    assert _y_ticks(axes) == [(0.0, "pay"), (1.0, "fee"), (2.0, "interest")]


def test_a_value_outside_the_declared_categories_is_an_error(tmp_path: Path) -> None:
    folder = _event_package(tmp_path, categories=["pay"])

    with pytest.raises(LimelightError, match="'interest' is not one of the categories"):
        _drawn_axes(folder)


def test_a_stem_artist_cannot_use_a_discrete_y_axis(tmp_path: Path) -> None:
    project = LimelightProject(title="Events", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "event": ["pay", "pay"]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[Panel(
            stems=[StemArtist("event")], data="src", x="t",
            x_axis=AxisDataType.continuous(), y_axis=AxisDataType.discrete(categories=["pay"]),
        )],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with pytest.raises(LimelightError, match="discrete y-axis"):
        validate_manifest_semantics(_manifest(folder))


def test_a_line_with_no_linestyle_draws_its_markers_alone(tmp_path: Path) -> None:
    project = LimelightProject(title="Events", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "event": ["pay", "pay"]})
    project.add_line_figure(
        id="fig", title="Fig", data="src", x="t",
        y=[LineArtist("event", linestyle="none", marker="x")],
        x_axis=AxisDataType.continuous(), y_axis=AxisDataType.discrete(categories=["pay"]),
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    validate_manifest_semantics(_manifest(folder))

    [line] = _drawn_axes(folder).get_lines()
    assert line.get_linestyle() == "None"
    assert line.get_marker() == "x"


def _stacked_package(tmp_path: Path, top_axis: AxisDataType, bottom_axis: AxisDataType) -> Path:
    """Two stacked panels, each a line over its own categories on the x-axis."""
    project = LimelightProject(title="Stack", authors=["Test"])
    project.add_csv_dataset(id="top", arrays={"k": ["a", "b"], "v": [1.0, 2.0]})
    project.add_csv_dataset(id="bottom", arrays={"k": ["b", "c"], "v": [3.0, 4.0]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[
            Panel(lines=[LineArtist("v", x="k", data=data)], data=data, x="k", x_axis=x_axis, y_axis=AxisDataType.continuous())
            for data, x_axis in (("top", top_axis), ("bottom", bottom_axis))
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)
    return folder


def test_stacked_panels_number_a_shared_discrete_x_axis_once(tmp_path: Path) -> None:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    folder = _stacked_package(tmp_path, AxisDataType.discrete(), AxisDataType.discrete())
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(LimelightRuntime(package, manifest), figure, "fig")

    top, bottom = figure.axes
    assert top.get_lines()[0].get_xdata().tolist() == [0.0, 1.0]
    assert bottom.get_lines()[0].get_xdata().tolist() == [1.0, 2.0]
    assert [label.get_text() for label in bottom.get_xticklabels()] == ["a", "b", "c"]


def test_stacked_panels_with_different_categories_are_not_linked(tmp_path: Path) -> None:
    from limelight.qt_app import _render_figure_view_to_matplotlib_figure

    folder = _stacked_package(
        tmp_path, AxisDataType.discrete(categories=["a", "b"]), AxisDataType.discrete(categories=["b", "c"])
    )
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        figure = Figure()
        _render_figure_view_to_matplotlib_figure(LimelightRuntime(package, manifest), figure, "fig")

    top, bottom = figure.axes
    assert [label.get_text() for label in top.get_xticklabels()] == ["a", "b"]
    assert [label.get_text() for label in bottom.get_xticklabels()] == ["b", "c"]
    assert bottom.get_lines()[0].get_xdata().tolist() == [0.0, 1.0]


def test_a_category_axis_keeps_an_x_window_set_on_a_linked_panel(tmp_path: Path) -> None:
    from limelight.qt_app import _draw_plot_contents

    folder = _stacked_package(tmp_path, AxisDataType.discrete(), AxisDataType.discrete())
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        runtime = LimelightRuntime(package, manifest)
        first_spec, second_spec = runtime.figure_specs["fig"]["axesSpecs"]
        figure = Figure()
        first, second = figure.add_subplot(211), figure.add_subplot(212)
        second.sharex(first)
        _draw_plot_contents(runtime, first, "fig", first_spec, figure_view_actions=[])
        first.set_xlim(0.25, 0.75)  # as a panel's x window would
        _draw_plot_contents(runtime, second, "fig", second_spec, figure_view_actions=[])

    assert first.get_xlim() == (0.25, 0.75)


def test_a_row_that_draws_nothing_numbers_no_category(tmp_path: Path) -> None:
    project = LimelightProject(title="Events", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, None, 2.0], "event": ["pay", "audit", "pay"]})
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[Panel(
            scatters=[ScatterArtist("event", x="t")], data="src", x="t",
            x_axis=AxisDataType.continuous(), y_axis=AxisDataType.discrete(),
        )],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    assert [label for _, label in _y_ticks(_drawn_axes(folder))] == ["pay"]


def test_a_line_with_no_linestyle_needs_a_marker(tmp_path: Path) -> None:
    project = LimelightProject(title="Events", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "event": ["pay", "pay"]})
    project.add_line_figure(
        id="fig", title="Fig", data="src", x="t",
        y=[LineArtist("event", linestyle="none")],
        x_axis=AxisDataType.continuous(), y_axis=AxisDataType.discrete(),
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with pytest.raises(LimelightError, match="no marker"):
        validate_manifest_semantics(_manifest(folder))


def test_panels_in_share_groups_of_their_own_keep_their_own_categories(tmp_path: Path) -> None:
    folder = _stacked_package(
        tmp_path,
        AxisDataType.discrete(categories=["a", "b"], share_group="top"),
        AxisDataType.discrete(categories=["b", "c"], share_group="bottom"),
    )

    validate_manifest_semantics(_manifest(folder))
