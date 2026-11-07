"""A writer with no copy of a remote HDF5 file describes it by URL, SHA-256,
size and each dataset's declared dtype and shape. The manifest it writes is
the one a writer holding the file would write, and `LL verify` checks the
declared bindings against the real file once it is downloaded."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight import cli, remote_files
from limelight.reader import LimelightError, open_limelight
from limelight.remote_files import RemoteFile
from limelight.semantic import validate_manifest_semantics
from limelight.signing import generate_ed25519_keypair, verify_manifest_signatures
from limelight.writer import (
    EpochOffset,
    Index,
    LimelightProject,
    StatusCode,
    TimeSeriesArtist,
    hdf_array,
)

URL = "https://data.example.org/recordings/rec.h5"
T, R = 500, 3
T0_US = 1_762_000_000_000_000
CODES = [
    StatusCode(code=0, meaning="valid", label="OK"),
    StatusCode(code=1, meaning="skip", label="Not sampled"),
    StatusCode(code=2, meaning="absent", label="Sensor fault"),
]
SHAPES = {"/values": ("f8", (T, R)), "/status": ("u1", (T, R)), "/time_us": ("i8", (T,))}


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote_files.DOWNLOAD_DIR_ENV, str(tmp_path / "downloads"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)


def _h5(path: Path, *, values_dtype=np.float64, status_dtype=np.uint8, columns: int = R, codes=(0, 1, 2)) -> Path:
    rng = np.random.default_rng(7)
    with h5py.File(path, "w") as handle:
        handle["/values"] = rng.normal(size=(T, columns)).astype(values_dtype)
        handle["/status"] = rng.choice(np.array(codes), size=(T, columns)).astype(status_dtype)
        handle["/time_us"] = T0_US + np.cumsum(rng.integers(40_000, 60_000, size=T)).astype(np.int64)
    return path


def _remote_of(h5: Path, url: str = URL) -> RemoteFile:
    body = h5.read_bytes()
    return RemoteFile(url=url, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))


def _bigo_arrays(columns: int = R) -> list:
    arrays = [
        hdf_array(f"s{c}", "/values", column=c, status="/status", status_column=c, unit="degC", label=f"Sensor {c}")
        for c in range(columns)
    ]
    return [*arrays, hdf_array("time_us", "/time_us", dtype="integer")]


def _bigo_index() -> Index:
    return Index.irregular_index_time("time_us", origin=EpochOffset.from_unix_us(0), unit="us")


def _project(*, columns: int = R, signers=(), **source) -> LimelightProject:
    project = LimelightProject(title="recording", authors=["Test"])
    project.add_hdf_dataset(
        id="rec",
        y_arrays=_bigo_arrays(columns),
        index=_bigo_index(),
        status_codes=CODES,
        path="data/rec.h5",
        title="Recording",
        signers=signers,
        **source,
    )
    project.add_line_figure(
        id="f", title="f", data="rec", x="", y=[],
        time_series=[TimeSeriesArtist(data="rec", array=f"s{c}") for c in range(columns)],
    )
    return project


def _seed_cache(h5: Path, remote: RemoteFile) -> None:
    remote.cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(h5, remote.cache_path)


def test_the_manifest_is_the_one_a_writer_holding_the_file_writes(tmp_path: Path) -> None:
    h5 = _h5(tmp_path / "rec.h5")
    _, private_key = generate_ed25519_keypair()
    signers = [("lab", private_key)]
    with_file = _project(hdf5_path=h5, url=URL, signers=signers)
    without = _project(remote=_remote_of(h5), shapes=SHAPES, signers=signers)
    assert without.render_project_dhall() == with_file.render_project_dhall()

    with_file.write_folder(tmp_path / "a.ll")
    without.write_folder(tmp_path / "b.ll")
    assert (tmp_path / "b.ll" / "project.dhall").read_bytes() == (tmp_path / "a.ll" / "project.dhall").read_bytes()
    assert not (tmp_path / "b.ll" / "data").exists()


def test_a_package_written_without_the_file_verifies_against_the_downloaded_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    h5 = _h5(tmp_path / "rec.h5")
    remote = _remote_of(h5)
    _, private_key = generate_ed25519_keypair()
    _project(remote=remote, shapes=SHAPES, signers=[("lab", private_key)]).write_folder(tmp_path / "pkg.ll")
    with open_limelight(tmp_path / "pkg.ll") as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        [check] = verify_manifest_signatures(package, manifest)
    assert check.ok
    [source] = manifest["sources"]
    assert source["remote"] == {"url": URL, "sha256": remote.sha256, "sizeBytes": remote.size_bytes}

    _seed_cache(h5, remote)
    assert cli.main(["verify", str(tmp_path / "pkg.ll")]) == 0
    out = capsys.readouterr().out
    assert f"OK {tmp_path / 'pkg.ll'}" in out
    assert "UNCHECKED" not in out and "INVALID" not in out


@pytest.mark.parametrize(
    ("make_file", "match"),
    [
        (lambda path: _h5(path, columns=R - 1), r"names column 2 of dataset '/values', which has 2 columns"),
        (lambda path: _h5(path, status_dtype=np.float32), r"status '/status' is float32; a status dataset holds integer codes"),
        (lambda path: _h5(path, codes=(0, 1, 2, 9)), r"holds codes the source's statusCodes do not declare: 9"),
    ],
    ids=["fewer-columns", "float-status", "undeclared-code"],
)
def test_verify_fails_when_the_real_file_is_not_what_was_declared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_file, match: str
) -> None:
    h5 = make_file(tmp_path / "rec.h5")
    remote = _remote_of(h5)
    _project(remote=remote, shapes=SHAPES).write_folder(tmp_path / "pkg.ll")
    _seed_cache(h5, remote)
    assert cli.main(["verify", str(tmp_path / "pkg.ll")]) == 1
    out = capsys.readouterr().out
    assert out.startswith(f"INVALID {tmp_path / 'pkg.ll'}: Source 'rec': array 's")
    assert re.search(match, out)


def test_exactly_one_of_hdf5_path_and_remote(tmp_path: Path) -> None:
    h5 = _h5(tmp_path / "rec.h5")
    with pytest.raises(ValueError, match="exactly one of hdf5_path and remote"):
        _project()
    with pytest.raises(ValueError, match="exactly one of hdf5_path and remote"):
        _project(hdf5_path=h5, remote=_remote_of(h5), shapes=SHAPES)
    with pytest.raises(ValueError, match="needs `shapes`"):
        _project(remote=_remote_of(h5))
    with pytest.raises(ValueError, match="only for a remote file"):
        _project(hdf5_path=h5, shapes=SHAPES)
    with pytest.raises(ValueError, match="url goes with hdf5_path"):
        _project(remote=_remote_of(h5), shapes=SHAPES, url=URL)


GOOD_SHA = "0" * 64


@pytest.mark.parametrize(
    ("remote", "match"),
    [
        (RemoteFile("http://data.example.org/a.h5", GOOD_SHA, 10), "https"),
        (RemoteFile("https:///a.h5", GOOD_SHA, 10), "with a host"),
        (RemoteFile("https://me:pw@data.example.org/a.h5", GOOD_SHA, 10), "user name or password"),
        (RemoteFile(URL, "A" * 64, 10), "64 lower-case hexadecimal"),
        (RemoteFile(URL, "0" * 63, 10), "64 lower-case hexadecimal"),
        (RemoteFile(URL, GOOD_SHA, 0), "more than zero"),
        (RemoteFile(URL, GOOD_SHA, 10.5), "whole number"),  # type: ignore[arg-type]
    ],
)
def test_the_remote_file_is_validated(remote: RemoteFile, match: str) -> None:
    with pytest.raises(LimelightError, match=match):
        _project(remote=remote, shapes=SHAPES)


def _arrays_with(**overrides) -> list:
    arrays = _bigo_arrays()
    arrays[0] = hdf_array("s0", **{"dataset": "/values", "column": 0, "status": "/status", "status_column": 0, **overrides})
    return arrays


@pytest.mark.parametrize(
    ("shapes", "arrays", "match"),
    [
        ({**SHAPES, "/values": ("f8", (T, 2))}, None, r"names column 2 of dataset '/values', which has 2 columns"),
        ({**SHAPES, "/values": ("f8", (T,))}, None, r"names column 0 of dataset '/values', which is 1-D, not 2-D"),
        ({k: v for k, v in SHAPES.items() if k != "/status"}, None, r"status '/status' is not in rec.h5"),
        ({**SHAPES, "/status": ("f4", (T, R))}, None, r"is float32; a status dataset holds integer codes"),
        ({**SHAPES, "/status": ("u1", (T - 1, R))}, None, rf"has {T - 1} rows; the array has {T}"),
        ({**SHAPES, "/time_us": ("i8", (T + 1,))}, None, r"arrays differ in length"),
        ({**SHAPES, "/time_us": ("i8", (T, 1))}, None, r"'/time_us', which is 2-D; a 2-D dataset needs a column"),
        (SHAPES, _arrays_with(dataset="/missing"), r"names dataset '/missing', which is not in rec.h5"),
        ({**SHAPES, "/values": ("f8", (T, -1))}, None, r"none negative"),
        ({**SHAPES, "/values": ("nope", (T, R))}, None, r"not a numpy dtype"),
        ({**SHAPES, "/values": ("U8", (T, R))}, None, r"are numeric"),
        ({**SHAPES, "/values": "f8"}, None, r"must be \(dtype, shape\)"),
    ],
)
def test_bindings_are_checked_against_the_declared_shapes(tmp_path: Path, shapes, arrays, match: str) -> None:
    remote = RemoteFile(URL, GOOD_SHA, 1000)
    project = LimelightProject(title="x", authors=["Test"])
    with pytest.raises(LimelightError, match=match):
        project.add_hdf_dataset(
            id="rec", remote=remote, shapes=shapes, y_arrays=arrays or _bigo_arrays(), index=_bigo_index(),
            status_codes=CODES,
        )


def test_a_declared_mistake_reads_as_the_same_mistake_in_a_file(tmp_path: Path) -> None:
    columns_in_file = R - 1
    h5 = _h5(tmp_path / "rec.h5", columns=columns_in_file)
    with pytest.raises(LimelightError) as from_file:
        _project(hdf5_path=h5)
    with pytest.raises(LimelightError) as from_shapes:
        _project(remote=_remote_of(h5), shapes={**SHAPES, "/values": ("f8", (T, columns_in_file))})
    assert str(from_shapes.value) == str(from_file.value)


def test_dataset_paths_need_not_lead_with_a_slash(tmp_path: Path) -> None:
    h5 = _h5(tmp_path / "rec.h5")
    shapes = {key.lstrip("/"): value for key, value in SHAPES.items()}
    with_file = _project(hdf5_path=h5, url=URL)
    without = _project(remote=_remote_of(h5), shapes=shapes)
    assert without.render_project_dhall() == with_file.render_project_dhall()


def test_a_regular_1d_source_matches_too(tmp_path: Path) -> None:
    h5 = tmp_path / "rec.h5"
    with h5py.File(h5, "w") as handle:
        handle["/v"] = np.arange(1000, dtype=np.float64)

    def build(**source) -> str:
        project = LimelightProject(title="remote", authors=["Test"])
        project.add_hdf_dataset(id="rec", y_arrays=[hdf_array("v", "/v")], index=Index.regular_int(0, 1), **source)
        return project.render_project_dhall()

    assert build(remote=_remote_of(h5), shapes={"/v": ("f8", 1000)}) == build(hdf5_path=h5, url=URL)
