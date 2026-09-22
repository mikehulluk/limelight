"""A figure's forms: the controls beside it, each form a whole FormSpec."""

from __future__ import annotations

from pathlib import Path

from limelight.app import LimelightRuntime, figure_controls
from limelight.reader import open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import DropdownCtrlSpec, FormSpec, LimelightProject, Panel, SliderCtrlSpec


def _project() -> LimelightProject:
    project = LimelightProject(title="Forms", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"t": [0.0, 1.0], "a": [1.0, 2.0]})
    project.add_discrete_control_parameter(
        id="pick", label="Pick", options=[("one", "One"), ("two", "Two")], default="one"
    )
    project.add_float_control_parameter(id="level", label="Level", default=1.0, min=0.0, max=2.0)
    return project


def test_a_figure_carries_more_than_one_form(tmp_path: Path) -> None:
    project = _project()
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[Panel(lines=["a"], data="src", x="t")],
        forms=[
            FormSpec(controls=[DropdownCtrlSpec(id="pick-control", control_parameter="pick")], title="Series"),
            FormSpec(
                controls=[SliderCtrlSpec(id="level-control", control_parameter="level")],
                title="Display",
                caption="How it is drawn.",
                id="display-form",
            ),
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        runtime = LimelightRuntime(package, manifest)

    [figure_spec] = manifest["figures"]
    first, second = figure_spec["formSpecs"]
    # The first form is named for the figure; the second said its own name.
    assert first["id"] == "fig-controls" and second["id"] == "display-form"
    assert first["title"] == "Series" and second["caption"] == "How it is drawn."

    # And the app reads both, in order.
    forms = figure_controls(runtime.figure_specs["fig"])
    assert [form["id"] for form in forms] == ["fig-controls", "display-form"]
    assert [control["controlParameter"] for form in forms for control in form["controls"]] == ["pick", "level"]


def test_a_form_can_be_placed_by_a_frame(tmp_path: Path) -> None:
    project = _project()
    project.add_figure(
        id="fig",
        title="Fig",
        panels=[Panel(lines=["a"], data="src", x="t")],
        forms=[
            FormSpec(
                controls=[DropdownCtrlSpec(id="pick-control", control_parameter="pick")],
                frame=(0.7, 0.1, 0.25, 0.2),
            )
        ],
    )
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)

    [figure_spec] = manifest["figures"]
    [form_spec] = figure_spec["formSpecs"]
    assert form_spec["frame"] == {"left": 0.7, "bottom": 0.1, "width": 0.25, "height": 0.2}


def test_the_builders_controls_are_one_form_and_no_controls_is_none(tmp_path: Path) -> None:
    project = _project()
    project.add_line_figure(
        id="with",
        title="With",
        data="src",
        x="t",
        y=["a"],
        controls=[DropdownCtrlSpec(id="pick-control", control_parameter="pick")],
        form_title="Controls",
    )
    project.add_line_figure(id="without", title="Without", data="src", x="t", y=["a"])
    folder = tmp_path / "pkg"
    project.write_folder(folder)

    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)

    by_id = {figure["id"]: figure for figure in manifest["figures"]}
    [form_spec] = by_id["with"]["formSpecs"]
    assert form_spec["id"] == "with-controls" and form_spec["title"] == "Controls"
    assert "frame" not in form_spec
    assert by_id["without"]["formSpecs"] == []
