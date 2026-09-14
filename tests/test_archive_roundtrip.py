"""Cover the archive form of a package.

A package is a folder while it is authored and an archive once it is shipped, so
the archive path is the one end users actually open. Nothing else in the test
suite exercises ``write_archive``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from limelight.reader import LimelightError, open_limelight
from limelight.writer import Index, LimelightProject


def build_project() -> LimelightProject:
    project = LimelightProject(title="Archive Round Trip", authors=["Test"])
    project.add_csv_dataset(
        id="prices",
        arrays={"value": [1, 2, 3]},
        index=Index.no_index(),
    )
    return project


def test_write_archive_produces_a_zip_with_the_manifest_at_the_root(tmp_path: Path) -> None:
    archive = build_project().write_archive(tmp_path / "round-trip.limelight")

    assert archive.is_file()
    assert zipfile.is_zipfile(archive)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    assert "project.dhall" in names


def test_archive_and_folder_forms_read_back_identically(tmp_path: Path) -> None:
    project = build_project()
    folder = project.write_folder(tmp_path / "folder-form.limelight")
    archive = project.write_archive(tmp_path / "archive-form.limelight")

    with open_limelight(folder) as folder_package:
        folder_manifest = folder_package.manifest_json()
    with open_limelight(archive) as archive_package:
        archive_manifest = archive_package.manifest_json()

    assert archive_manifest == folder_manifest
    assert archive_manifest["project"]["title"] == "Archive Round Trip"


def test_opening_an_archive_exposes_its_datasets(tmp_path: Path) -> None:
    archive = build_project().write_archive(tmp_path / "round-trip.limelight")

    with open_limelight(archive) as package:
        manifest = package.manifest_json()
        rows = package.read_csv(manifest["sources"][0]["path"])

    assert [row["value"] for row in rows] == ["1", "2", "3"]


def test_write_archive_refuses_to_overwrite_without_permission(tmp_path: Path) -> None:
    destination = tmp_path / "round-trip.limelight"
    build_project().write_archive(destination)

    with pytest.raises(FileExistsError):
        build_project().write_archive(destination)

    # ...and replaces it when overwriting is allowed.
    assert build_project().write_archive(destination, overwrite=True).is_file()


def test_opening_a_non_package_file_reports_a_clear_error(tmp_path: Path) -> None:
    not_a_package = tmp_path / "notes.limelight"
    not_a_package.write_text("this is not a zip", encoding="utf-8")

    with pytest.raises(LimelightError):
        open_limelight(not_a_package)


@pytest.mark.parametrize("suffix", [".limelight", ".ll"])
def test_both_extensions_open_as_folder_and_archive(tmp_path: Path, suffix: str) -> None:
    project = build_project()
    folder = project.write_folder(tmp_path / f"folder-form{suffix}")
    archive = project.write_archive(tmp_path / f"archive-form{suffix}")

    for path in (folder, archive):
        with open_limelight(path) as package:
            assert package.manifest_json()["project"]["title"] == "Archive Round Trip"


@pytest.mark.parametrize("suffix", [".limelight", ".ll"])
def test_overwrite_is_allowed_only_for_package_suffixed_folders(tmp_path: Path, suffix: str) -> None:
    project = build_project()
    target = tmp_path / f"pkg{suffix}"
    project.write_folder(target)
    project.write_folder(target, overwrite=True)

    plain = tmp_path / "pkg.data"
    plain.mkdir()
    with pytest.raises(ValueError, match=r"\.limelight, \.ll"):
        project.write_folder(plain, overwrite=True)
