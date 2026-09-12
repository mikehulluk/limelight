"""The built-in Dhall evaluator must produce exactly what dhall-to-json does.

Two layers: unit tests pin each construct and each of dhall-to-json's JSON
conventions, and the golden tests run every package this machine has built
through both evaluators and compare them field by field, with types - so a
``210.0`` that came back as a float where the tool says ``210`` is a failure,
even though ``==`` would let it through.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from limelight import dhall_subset
from limelight.dhall_subset import (
    DhallEvaluationError,
    DhallSyntaxError,
    UnsupportedDhall,
    load,
    loads,
)
from limelight.reader import LimelightError, dhall_tool_path, open_limelight
from limelight.writer import Index, LimelightProject

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILT_EXAMPLES = sorted(REPO_ROOT.glob("_build/examples/*.limelight/project.dhall"))


def _dhall_to_json_tool() -> str | None:
    tool = dhall_tool_path("dhall-to-json")
    if Path(tool).is_file() or shutil.which(tool):
        return tool
    return None


def _reference_json(tool: str, manifest: Path) -> Any:
    completed = subprocess.run(
        [tool, "--file", str(manifest)], capture_output=True, text=True, check=True, cwd=manifest.parent
    )
    return json.loads(completed.stdout)


def _assert_identical(mine: Any, theirs: Any, path: str = "$") -> None:
    assert type(mine) is type(theirs), f"{path}: {type(mine).__name__} != {type(theirs).__name__} ({mine!r} vs {theirs!r})"
    if isinstance(mine, dict):
        assert mine.keys() == theirs.keys(), f"{path}: keys differ: {sorted(mine.keys() ^ theirs.keys())}"
        for key in mine:
            _assert_identical(mine[key], theirs[key], f"{path}.{key}")
    elif isinstance(mine, list):
        assert len(mine) == len(theirs), f"{path}: length {len(mine)} != {len(theirs)}"
        for index, (left, right) in enumerate(zip(mine, theirs)):
            _assert_identical(left, right, f"{path}[{index}]")
    else:
        assert mine == theirs, f"{path}: {mine!r} != {theirs!r}"


# ---------------------------------------------------------------------------
# Golden comparison against the real tool


@pytest.mark.parametrize("manifest", BUILT_EXAMPLES, ids=lambda path: path.parent.name)
def test_matches_dhall_to_json_on_built_examples(manifest: Path) -> None:
    tool = _dhall_to_json_tool()
    if tool is None:
        pytest.skip("dhall-to-json is not available for the golden comparison")
    _assert_identical(load(manifest), _reference_json(tool, manifest))


def test_matches_dhall_to_json_on_written_package(tmp_path: Path) -> None:
    tool = _dhall_to_json_tool()
    if tool is None:
        pytest.skip("dhall-to-json is not available for the golden comparison")

    project = LimelightProject(title="Evaluator Test", authors=["Test"], subtitle="With a subtitle")
    project.add_csv_dataset(
        id="readings",
        arrays={"value": [1.5, 2.0, 3.25], "count": [1, 2, 3]},
        index=Index.no_index(),
    )
    package_root = tmp_path / "pkg"
    project.write_folder(package_root)

    manifest = package_root / "project.dhall"
    _assert_identical(load(manifest), _reference_json(tool, manifest))


def test_reader_uses_the_evaluator_without_a_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = LimelightProject(title="No Tool", authors=["Test"])
    package_root = tmp_path / "pkg"
    project.write_folder(package_root)

    # Point the tool lookup at nothing so a fallback would fail loudly.
    monkeypatch.setenv("LIMELIGHT_DHALL_TO_JSON", str(tmp_path / "missing-dhall-to-json"))
    with open_limelight(package_root) as package:
        manifest = package.manifest_json()
    assert manifest["project"]["title"] == "No Tool"


# ---------------------------------------------------------------------------
# Constructs


def test_let_bindings_records_and_field_access() -> None:
    result = loads(
        """
        let a = { x = 1, y = "two" }
        let b = a.x
        in { first = b, second = a.y, nested = { inner = a } }
        """
    )
    assert result == {"first": 1, "second": "two", "nested": {"inner": {"x": 1, "y": "two"}}}


def test_literals() -> None:
    result = loads(
        """
        { natural = 12, integerPos = +3, integerNeg = -3, double = 2.5, exponent = 1e-5
        , negativeDouble = -0.5, yes = True, no = False
        , text = "quote \\" backslash \\\\ newline \\n tab \\t unicode \\u00e9 astral \\u{1F600} dollar \\$"
        , empty = {=}, emptyList = [] : List Natural }
        """
    )
    assert result == {
        "natural": 12,
        "integerPos": 3,
        "integerNeg": -3,
        "double": 2.5,
        "exponent": 1e-5,
        "negativeDouble": -0.5,
        "yes": True,
        "no": False,
        "text": 'quote " backslash \\ newline \n tab \t unicode é astral 😀 dollar $',
        "empty": {},
        "emptyList": [],
    }
    assert isinstance(result["natural"], int)
    assert isinstance(result["yes"], bool)


def test_unions_follow_dhall_to_json_conventions() -> None:
    result = loads(
        """
        let Shape = < circle | square : { side : Double } | label : Text >
        in { bare = Shape.circle, record = Shape.square { side = 2.0 }, text = Shape.label "x" }
        """
    )
    # A bare alternative is its name; one with a payload is the payload alone.
    assert result == {"bare": "circle", "record": {"side": 2}, "text": "x"}


def test_optionals_and_null_omission() -> None:
    result = loads(
        """
        { present = Some 1, absent = None Text, nested = { alsoAbsent = None Natural, kept = Some "v" }
        , inList = [ None Natural, Some 2 ], someNone = Some (None Text) }
        """
    )
    # Null record fields disappear, null list elements do not.
    assert result == {"present": 1, "nested": {"kept": "v"}, "inList": [None, 2]}


def test_whole_number_doubles_become_integers() -> None:
    result = loads("{ a = 210.0, b = 0.0, c = -0.0, d = 1e15, e = 1.5e300, f = 0.1, g = 1e7 }")
    assert result == {"a": 210, "b": 0, "c": 0, "d": 10**15, "e": 15 * 10**299, "f": 0.1, "g": 10**7}
    assert all(isinstance(result[key], int) for key in "abcdeg")
    assert isinstance(result["f"], float)


def test_record_prefer_merges_right_over_left() -> None:
    result = loads("{ a = 1, b = 2 } // { b = 3, c = 4 }")
    assert result == {"a": 1, "b": 3, "c": 4}


def test_type_annotations_and_types_as_values_are_accepted() -> None:
    result = loads(
        """
        let Id = Text
        let Point = { x : Double, y : Double }
        let Wrapped = < some : Optional (List Point) | none >
        let value : Point = { x = 1.0, y = 2.0 }
        let types = { Id = Id, Point = Point, Wrapped = Wrapped }
        in { value = value, list = [] : List types.Point, name = "n" : types.Id }
        """
    )
    assert result == {"value": {"x": 1, "y": 2}, "list": [], "name": "n"}


def test_comments_and_leading_separators() -> None:
    result = loads(
        """
        -- line comment
        { {- block {- nested -} comment -} a = [ , 1, 2 ]
        , b = < | x | y >.x
        }
        """
    )
    assert result == {"a": [1, 2], "b": "x"}


def test_relative_imports_resolve_against_the_importing_file(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "core.dhall").write_text("{ Version = Text, defaults = { size = 3 } }", encoding="utf-8")
    (tmp_path / "lib" / "all.dhall").write_text(
        "let Core = ./core.dhall in Core // { extra = Core.defaults.size }", encoding="utf-8"
    )
    (tmp_path / "project.dhall").write_text(
        'let L = ./lib/all.dhall in { version = "1" : L.Version, extra = L.extra, again = (./lib/all.dhall).extra }',
        encoding="utf-8",
    )
    assert load(tmp_path / "project.dhall") == {"version": "1", "extra": 3, "again": 3}


def test_import_cycle_is_reported(tmp_path: Path) -> None:
    (tmp_path / "a.dhall").write_text("./b.dhall", encoding="utf-8")
    (tmp_path / "b.dhall").write_text("./a.dhall", encoding="utf-8")
    with pytest.raises(DhallEvaluationError, match="import cycle"):
        load(tmp_path / "a.dhall")


def test_missing_import_is_reported(tmp_path: Path) -> None:
    (tmp_path / "a.dhall").write_text("./nowhere.dhall", encoding="utf-8")
    with pytest.raises(DhallEvaluationError, match="cannot read import"):
        load(tmp_path / "a.dhall")


# ---------------------------------------------------------------------------
# Errors


@pytest.mark.parametrize(
    "source, message",
    [
        ("{ a = 1 }.b", "no field 'b'"),
        ("missingName", "unbound variable missingName"),
        ("let U = < a > in U.b", "no alternative 'b'"),
        ("let U = < a > in U.a 1", "takes no payload"),
        ("{ a = 1, a = 2 }", "duplicate record field"),
        ("{ a = 1 } // 2", "record on both sides"),
        ("Text", "cannot convert Text to JSON"),
        ("[ 1 2 ]", "cannot apply a int"),
        ("let f = { g = 1 } in f.g 2", "cannot apply a int"),
    ],
)
def test_evaluation_errors_name_the_problem(source: str, message: str) -> None:
    with pytest.raises(DhallEvaluationError, match=message):
        loads(source)


@pytest.mark.parametrize(
    "source",
    [
        "{ a = 1",
        "{ a = }",
        "{ a : Text, b = 1 }",
        '{ a = "unterminated }',
        '{ a = "\\ud83d" }',
        "let x = 1",
    ],
)
def test_syntax_errors(source: str) -> None:
    with pytest.raises(DhallSyntaxError):
        loads(source)


def test_syntax_errors_carry_the_location() -> None:
    with pytest.raises(DhallSyntaxError, match=r"manifest\.dhall:2:7"):
        loads("{ a = 1\n, b = }", filename="manifest.dhall")


@pytest.mark.parametrize(
    "source, construct",
    [
        ("\\(x : Natural) -> x", "lambdas"),
        ("if True then 1 else 2", "if/then/else"),
        ('"value ${x}"', "text interpolation"),
        ("''\nblock\n''", "multi-line text"),
        ("https://example.com/x.dhall", "remote"),
        ("env:HOME", "remote"),
        ("./x.dhall sha256:0000000000000000000000000000000000000000000000000000000000000000", "import qualifiers"),
        ("./x.dhall as Text", "import qualifiers"),
        ("1 + 2", r"the \+ operator"),
        ("{ a = 1 } with a = 2", "with-expressions"),
        ("merge {=} <>.x", "merge"),
        ("{ a.b = 1 }", "dotted record fields"),
        ("let a = 1 in { a }", "record punning"),
        ("{ a = 1 }.{ a }", "record projection"),
        ("Natural/show 1", "built-in function Natural/show"),
    ],
)
def test_unsupported_constructs_are_named(source: str, construct: str) -> None:
    with pytest.raises(UnsupportedDhall, match=construct):
        loads(source)


def test_reader_falls_back_to_the_tool_for_unsupported_dhall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = _dhall_to_json_tool()
    if tool is None:
        pytest.skip("dhall-to-json is not available to fall back to")
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "project.dhall").write_text('{ title = "a" ++ "b" }', encoding="utf-8")

    monkeypatch.setenv("LIMELIGHT_DHALL_TO_JSON", tool)
    with open_limelight(package_root) as package:
        assert package.manifest_json() == {"title": "ab"}


def test_reader_reports_the_evaluator_error_when_no_tool_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "project.dhall").write_text('{ title = "a" ++ "b" }', encoding="utf-8")

    monkeypatch.setenv("LIMELIGHT_DHALL_TO_JSON", str(tmp_path / "missing-dhall-to-json"))
    with open_limelight(package_root) as package:
        with pytest.raises(LimelightError, match=r"the \+\+ operator"):
            package.manifest_json()


def test_module_has_no_dependency_on_the_rest_of_limelight() -> None:
    # The evaluator is meant to stay a leaf module: nothing here should pull
    # in Qt, matplotlib or the writer.
    source = Path(dhall_subset.__file__).read_text(encoding="utf-8")
    assert "from ." not in source and "import limelight" not in source
