"""Text the writer quotes must come back through the manifest unchanged.

json.dumps is nearly right for a Dhall text literal, but its surrogate-pair
escapes for emoji are not Dhall, and it leaves "${" alone where Dhall would
read an interpolation. Both broke real manifests.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from limelight.reader import dhall_tool_path, open_limelight
from limelight.writer import LimelightProject

AWKWARD_TITLE = 'Results 😀 for "Q3" — cost ${total}\\n with \\ and \t tab'


def _write(tmp_path: Path) -> Path:
    project = LimelightProject(title=AWKWARD_TITLE, authors=["Tést ${author}"], subtitle="é ")
    package_root = tmp_path / "pkg"
    project.write_folder(package_root)
    return package_root


def test_awkward_text_round_trips_through_the_evaluator(tmp_path: Path) -> None:
    with open_limelight(_write(tmp_path)) as package:
        project = package.manifest_json()["project"]
    assert project["title"] == AWKWARD_TITLE
    assert project["authors"] == ["Tést ${author}"]
    assert project["subtitle"] == "é "


def test_awkward_text_is_also_valid_for_dhall_to_json(tmp_path: Path) -> None:
    tool = dhall_tool_path("dhall-to-json")
    if not (Path(tool).is_file() or shutil.which(tool)):
        pytest.skip("dhall-to-json is not available")
    with open_limelight(_write(tmp_path)) as package:
        project = package.manifest_json(dhall_to_json=tool)["project"]
    assert project["title"] == AWKWARD_TITLE
    assert project["authors"] == ["Tést ${author}"]
