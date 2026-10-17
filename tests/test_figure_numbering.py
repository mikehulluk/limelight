"""Figure and table numbers: worked out from the story, never stored.

A reader numbers each figure view and each figure image in the order the
story shows them, as "Figure N."; tables are counted on their own, as
"Table N.". The manifest carries no number, so the tests build a package and
ask the runtime that reads it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from limelight.app import LimelightRuntime, figure_view_caption_markup
from limelight.reader import open_limelight
from limelight.writer import LimelightProject, TableViewSpec


def _project() -> LimelightProject:
    project = LimelightProject(title="Numbering", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [0.0, 1.0], "y": [1.0, 2.0]})
    for spec_id in ("prey", "predator"):
        project.add_line_figure(id=spec_id, title=spec_id, data="src", x="x", y=["y"])
    return project


def _numbers(tmp_path: Path, project: LimelightProject) -> dict[str, int | None]:
    """Each figure view's number, as the runtime reading the written package gives it."""

    project.write_folder(tmp_path / "pkg", overwrite=True)
    with open_limelight(tmp_path / "pkg") as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        return {view_id: runtime.figure_view_number(view) for view_id, view in runtime.figure_views.items()}


def test_story_figures_are_numbered_from_one(tmp_path: Path) -> None:
    project = _project()
    first = project.story_figure("prey", id="view-prey")
    second = project.story_figure("predator", id="view-predator")
    project.set_story_markdown(f"# Numbering\n\n{first}\n\n{second}\n", base_dir=tmp_path)

    assert _numbers(tmp_path, project) == {"view-prey": 1, "view-predator": 2}


def test_numbering_follows_the_document_not_the_call_order(tmp_path: Path) -> None:
    project = _project()
    first = project.story_figure("prey", id="view-prey")
    second = project.story_figure("predator", id="view-predator")
    # Written into the story the other way round.
    project.set_story_markdown(f"# Numbering\n\n{second}\n\n{first}\n", base_dir=tmp_path)

    assert _numbers(tmp_path, project) == {"view-predator": 1, "view-prey": 2}


def test_a_repeated_figure_keeps_its_first_number(tmp_path: Path) -> None:
    project = _project()
    first = project.story_figure("prey", id="view-prey")
    second = project.story_figure("predator", id="view-predator")
    project.set_story_markdown(f"# Numbering\n\n{first}\n\n{second}\n\n{first}\n", base_dir=tmp_path)

    assert _numbers(tmp_path, project) == {"view-prey": 1, "view-predator": 2}


def test_a_view_the_story_never_shows_has_no_number(tmp_path: Path) -> None:
    project = _project()
    project.add_figure_view(id="view-unused", ref="predator")
    shown = project.story_figure("prey", id="view-prey")
    project.set_story_markdown(f"# Numbering\n\n{shown}\n", base_dir=tmp_path)

    assert _numbers(tmp_path, project) == {"view-unused": None, "view-prey": 1}


def test_the_manifest_stores_no_number(tmp_path: Path) -> None:
    project = _project()
    shown = project.story_figure("prey")
    project.set_story_markdown(f"# Numbering\n\n{shown}\n", base_dir=tmp_path)
    project.write_folder(tmp_path / "pkg")

    manifest = (tmp_path / "pkg" / "project.dhall").read_text()
    assert "storyNumber" not in manifest


# Tables are counted on their own: Figure 1, Table 1, Figure 2, Table 2.


def _story_with_figures_and_tables(tmp_path: Path) -> LimelightProject:
    project = _project()
    for spec_id in ("table-a", "table-b"):
        project.add_figure(id=spec_id, title=spec_id, table_views=[TableViewSpec(id=f"{spec_id}-view", data="src")])
    story = "\n\n".join(
        [
            "# Counts",
            project.story_figure("prey", id="view-plot-a"),
            project.story_figure("table-a", id="view-table-a"),
            project.story_figure("predator", id="view-plot-b"),
            project.story_figure("table-b", id="view-table-b"),
            "See [Table](#view-table-b) and [Figure](#view-plot-b).",
        ]
    )
    project.set_story_markdown(story, base_dir=tmp_path)
    return project


def test_tables_and_figures_have_separate_counters(tmp_path: Path) -> None:
    numbers = _numbers(tmp_path, _story_with_figures_and_tables(tmp_path))

    assert numbers == {"view-plot-a": 1, "view-table-a": 1, "view-plot-b": 2, "view-table-b": 2}


def test_a_cross_reference_to_a_table_gets_the_table_number(tmp_path: Path) -> None:
    project = _story_with_figures_and_tables(tmp_path)

    markdown = project.resolve_story().markdown
    assert "See [Table 2](#view-table-b) and [Figure 2](#view-plot-b)." in markdown


class _HeadingRuntime:
    """The slice of LimelightRuntime the heading and lookup helpers touch."""

    figure_view_heading = LimelightRuntime.figure_view_heading
    figure_views_for_spec = LimelightRuntime.figure_views_for_spec
    figure_view_number = LimelightRuntime.figure_view_number

    def __init__(self, figure_views: list[dict[str, Any]], numbers: dict[str, int] | None = None) -> None:
        self.figure_specs = {"prey": {"id": "prey", "title": "Prey over time"}}
        self.figure_views = {view["id"]: view for view in figure_views}
        self._figure_view_numbers = numbers or {}


def _figure_view(view_id: str, ref: str = "prey") -> dict[str, Any]:
    return {"id": view_id, "ref": ref, "actions": []}


def test_a_spec_with_no_number_is_titled_without_one() -> None:
    # A spec can be rendered by several views at several numbers, so numbering
    # it by its position in the manifest would print a second, unrelated
    # "Figure N" for the same plot.
    runtime = _HeadingRuntime([])

    assert runtime.figure_view_heading("prey") == "Prey over time"
    assert runtime.figure_view_heading("prey", index=4) == "Figure 4. Prey over time"


def test_views_of_a_spec_come_back_in_the_order_a_reader_meets_them() -> None:
    runtime = _HeadingRuntime(
        [_figure_view("v-c"), _figure_view("v-unused"), _figure_view("v-a"), _figure_view("v-other", ref="predator")],
        numbers={"v-c": 7, "v-a": 2, "v-other": 1},
    )

    views = runtime.figure_views_for_spec("prey")

    # Ordered by number, with the view the story never shows last, and nothing
    # belonging to another spec.
    assert [view["id"] for view in views] == ["v-a", "v-c", "v-unused"]


def test_a_table_is_headed_and_captioned_as_a_table() -> None:
    runtime = _HeadingRuntime([])
    runtime.figure_specs["summary"] = {"id": "summary", "title": "Summary", "tableViewSpecs": [{"id": "t"}]}

    assert runtime.figure_view_heading("summary", index=2) == "Table 2. Summary"
    assert runtime.figure_view_heading("prey", index=2) == "Figure 2. Prey over time"
    assert ">Table 2.</span> Summary" in figure_view_caption_markup(runtime.figure_specs["summary"], 2)
