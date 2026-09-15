from __future__ import annotations

from typing import Any

import pytest

from limelight.app import LimelightRuntime
from limelight.semantic import LimelightError, _SemanticValidator
from limelight.writer import LimelightProject


def _figure_view(**overrides: Any) -> dict[str, Any]:
    figure_view: dict[str, Any] = {
        "id": "figure-view-0",
        "ref": "prey",
        "storyNumber": None,
        "index": None,
        "actions": [],
    }
    figure_view.update(overrides)
    return figure_view


class _NumberingRuntime:
    """The slice of LimelightRuntime figure_view_number actually touches."""

    figure_view_number = LimelightRuntime.figure_view_number


def test_story_number_is_used_when_present() -> None:
    assert _NumberingRuntime().figure_view_number(_figure_view(storyNumber=3, index=99)) == 3


def test_index_is_used_when_a_package_predates_story_number() -> None:
    # Packages built before storyNumber existed have no key at all, and must
    # keep the numbering they were published with.
    legacy = _figure_view(index=0)
    del legacy["storyNumber"]

    assert _NumberingRuntime().figure_view_number(legacy) == 0


def test_an_unnumbered_view_has_no_number() -> None:
    assert _NumberingRuntime().figure_view_number(_figure_view()) is None


def test_story_figures_are_numbered_from_one() -> None:
    project = LimelightProject(title="Numbering", authors=["Test"])

    first = project.story_figure("prey")
    second = project.story_figure("predator")
    project.set_story_markdown(f"# Numbering\n\n{first}\n\n{second}\n", base_dir=".")
    project.render_project_dhall()

    assert [view.story_number for view in project.figure_views] == [1, 2]
    # The superseded field is left empty rather than carrying a second number.
    assert [view.index for view in project.figure_views] == [None, None]


def test_numbering_follows_the_document_not_the_call_order() -> None:
    project = LimelightProject(title="Numbering", authors=["Test"])

    first = project.story_figure("prey")
    second = project.story_figure("predator")
    # Written into the story the other way round.
    project.set_story_markdown(f"# Numbering\n\n{second}\n\n{first}\n", base_dir=".")
    project.render_project_dhall()

    numbers = {view.ref: view.story_number for view in project.figure_views}
    assert numbers == {"predator": 1, "prey": 2}


def test_a_repeated_figure_keeps_its_first_number() -> None:
    project = LimelightProject(title="Numbering", authors=["Test"])

    first = project.story_figure("prey")
    second = project.story_figure("predator")
    project.set_story_markdown(f"# Numbering\n\n{first}\n\n{second}\n\n{first}\n", base_dir=".")
    project.render_project_dhall()

    numbers = {view.ref: view.story_number for view in project.figure_views}
    assert numbers == {"prey": 1, "predator": 2}


def test_an_explicit_number_overrides_story_order() -> None:
    project = LimelightProject(title="Numbering", authors=["Test"])

    directive = project.story_figure("prey", number=7)
    project.set_story_markdown(f"# Numbering\n\n{directive}\n", base_dir=".")
    project.render_project_dhall()

    assert project.figure_views[0].story_number == 7


def _validator(figure_views: list[dict[str, Any]]) -> _SemanticValidator:
    return _SemanticValidator(
        {
            "controlParameters": [],
            "sources": [],
            "figures": [{"id": "prey", "axesSpecs": [], "mapSpecs": [], "tableViewSpecs": []}],
            "figureViews": figure_views,
        }
    )


def test_duplicate_story_numbers_are_rejected() -> None:
    validator = _validator(
        [
            _figure_view(id="figure-view-0", storyNumber=1),
            _figure_view(id="figure-view-1", storyNumber=1),
        ]
    )

    with pytest.raises(LimelightError, match="storyNumber"):
        validator._validate_figure_views()


def test_a_zero_story_number_is_rejected() -> None:
    validator = _validator([_figure_view(storyNumber=0)])

    with pytest.raises(LimelightError, match="one or greater"):
        validator._validate_figure_views()


class _HeadingRuntime:
    """The slice of LimelightRuntime the heading and lookup helpers touch."""

    figure_view_heading = LimelightRuntime.figure_view_heading
    figure_views_for_spec = LimelightRuntime.figure_views_for_spec
    figure_view_number = LimelightRuntime.figure_view_number

    def __init__(self, figure_views: list[dict[str, Any]]) -> None:
        self.figure_specs = {"prey": {"id": "prey", "title": "Prey over time"}}
        self.figure_views = {view["id"]: view for view in figure_views}


def test_a_spec_with_no_number_is_titled_without_one() -> None:
    # A spec can be rendered by several views at several numbers, so numbering
    # it by its position in the manifest would print a second, unrelated
    # "Figure N" for the same plot.
    runtime = _HeadingRuntime([])

    assert runtime.figure_view_heading("prey") == "Prey over time"
    assert runtime.figure_view_heading("prey", index=4) == "Figure 4. Prey over time"


def test_views_of_a_spec_come_back_in_the_order_a_reader_meets_them() -> None:
    runtime = _HeadingRuntime(
        [
            _figure_view(id="v-c", storyNumber=7),
            _figure_view(id="v-unused"),
            _figure_view(id="v-a", storyNumber=2),
            {"id": "v-other", "ref": "predator", "storyNumber": 1, "index": None, "actions": []},
        ]
    )

    views = runtime.figure_views_for_spec("prey")

    # Ordered by number, with the view the story never shows last, and nothing
    # belonging to another spec.
    assert [view["id"] for view in views] == ["v-a", "v-c", "v-unused"]
