"""Package metadata: named, typed facts in the manifest, read out by `LL meta`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from limelight import cli, metadata
from limelight.reader import LimelightError, open_limelight
from limelight.semantic import validate_manifest_semantics
from limelight.writer import LimelightProject


@pytest.mark.parametrize(
    ("type_name", "text", "value"),
    [
        ("int", "42", 42),
        ("int", " -7 ", -7),
        ("float", "2.5", 2.5),
        ("float", "1e3", 1000.0),
        ("datetime", "2026-08-08T09:30:00+01:00", "2026-08-08T09:30:00+01:00"),
        ("datetime", "2026-08-08T09:30:00Z", "2026-08-08T09:30:00Z"),
        ("datetime", "2026-08-08", "2026-08-08"),
        ("string", "  anything at all ", "  anything at all "),
        ("version", "1.4.0", "1.4.0"),
        ("version", "2.0.0-rc.1+build.7", "2.0.0-rc.1+build.7"),
    ],
)
def test_values_read_as_their_type(type_name: str, text: str, value: object) -> None:
    assert metadata.parse_metadata_value(type_name, text) == value


@pytest.mark.parametrize(
    ("type_name", "text"),
    [
        ("int", "4.0"),
        ("int", "four"),
        ("float", "nan"),
        ("float", "inf"),
        ("datetime", "8 Aug 2026"),
        ("version", "1.4"),
        ("version", "v1.4.0"),
        ("version", "01.4.0"),
        ("colour", "red"),
    ],
)
def test_values_that_do_not_read_are_refused(type_name: str, text: str) -> None:
    with pytest.raises(metadata.MetadataError):
        metadata.parse_metadata_value(type_name, text)


def _package(tmp_path: Path, **entries: object) -> Path:
    project = LimelightProject(title="Meta", authors=["Test"])
    project.add_csv_dataset(id="src", arrays={"x": [1.0], "y": [2.0]})
    for name, value in entries.items():
        if isinstance(value, tuple):
            project.add_metadata(name, value[0], type=value[1])
        else:
            project.add_metadata(name, value)
    folder = tmp_path / "pkg.ll"
    project.write_folder(folder)
    return folder


def test_writer_types_metadata_from_the_value_and_it_round_trips(tmp_path: Path) -> None:
    folder = _package(
        tmp_path,
        publishdate=datetime(2026, 8, 8, 9, 30, tzinfo=timezone.utc),
        run=12,
        gain=0.75,
        campaign="bench",
        pipeline=("1.4.0", "version"),
    )
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
    validate_manifest_semantics(manifest)

    assert metadata.metadata_entries(manifest) == [
        {"name": "publishdate", "type": "datetime", "value": "2026-08-08T09:30:00+00:00"},
        {"name": "run", "type": "int", "value": 12},
        {"name": "gain", "type": "float", "value": 0.75},
        {"name": "campaign", "type": "string", "value": "bench"},
        {"name": "pipeline", "type": "version", "value": "1.4.0"},
    ]


def test_writer_refuses_what_will_not_verify() -> None:
    project = LimelightProject(title="Meta", authors=["Test"])
    with pytest.raises(ValueError, match="not a semantic version"):
        project.add_metadata("pipeline", "1.4", type="version")
    with pytest.raises(TypeError, match="bool"):
        project.add_metadata("flag", True)
    project.add_metadata("run", 1)
    with pytest.raises(ValueError, match="already declared"):
        project.add_metadata("run", 2)


def test_verify_checks_the_manifest_entries(tmp_path: Path) -> None:
    folder = _package(tmp_path, run=12)
    with open_limelight(folder) as package:
        manifest = package.manifest_json()

    bad = dict(manifest)
    bad["metadata"] = [{"name": "run", "type": "int", "value": "twelve"}]
    with pytest.raises(LimelightError, match="Metadata 'run': 'twelve' is not an int"):
        validate_manifest_semantics(bad)

    bad["metadata"] = [{"name": "run", "type": "int", "value": "1"}, {"name": "run", "type": "int", "value": "2"}]
    with pytest.raises(LimelightError, match="declared more than once"):
        validate_manifest_semantics(bad)

    bad["metadata"] = [{"name": "run", "type": "colour", "value": "red"}]
    with pytest.raises(LimelightError, match="unknown type 'colour'"):
        validate_manifest_semantics(bad)

    # A manifest from before metadata existed simply has none.
    old = {key: value for key, value in manifest.items() if key != "metadata"}
    validate_manifest_semantics(old)
    assert metadata.metadata_entries(old) == []


def test_ll_meta_prints_typed_json(tmp_path: Path, capsys) -> None:
    folder = _package(tmp_path, run=12, pipeline=("1.4.0", "version"))
    assert cli.main(["meta", str(folder)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == [
        {"name": "run", "type": "int", "value": 12},
        {"name": "pipeline", "type": "version", "value": "1.4.0"},
    ]
    assert "meta" in cli.COMMANDS
