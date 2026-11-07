"""Checks that an hdf source's manifest entries agree with the HDF5 file.

The manifest says which dataset (and, for a 2-D dataset, which column) backs
each array; only the file can say whether that dataset exists, has that many
columns, and is as long as the source's other arrays. `verify` runs this over
a package, and the writer runs it when a dataset is added, so a wrong binding
is reported where it is made rather than by the viewer at first draw.

An hdf source's file is either in the package, at its `path`, or - with a
`remote` entry - at a URL, downloaded into the reader's cache (see
remote_files); `hdf_file_path` says where it is either way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import h5py
import numpy as np

from .reader import LimelightError
from .remote_files import RemoteFile, RemoteFileNotDownloaded, download, remote_file_of


def hdf_file_path(package: Any, source: dict[str, Any]) -> Path:
    """Where an hdf source's file is; RemoteFileNotDownloaded if it is not here yet."""
    remote = remote_file_of(source)
    if remote is None:
        return package.package_path(source["path"])
    cached = remote.cached_path()
    if cached is None:
        raise RemoteFileNotDownloaded(remote, source["id"])
    return cached


STATUS_MEANINGS = ("valid", "skip", "absent")
# Bits 0 and 1 of a bucket's missing mask are NaN and undeclared codes; the
# declared missing codes have the rest (largeseries/status.py).
MAX_MISSING_CODES = 30
_STATUS_SCAN_BLOCK = 1 << 20


def check_status_codes(codes: list[dict[str, Any]], context: str) -> None:
    """Raise LimelightError for a status code table the viewer could not honour."""
    from matplotlib.colors import is_color_like

    seen: set[int] = set()
    missing = 0
    for entry in codes:
        code = int(entry["code"])
        if code in seen:
            raise LimelightError(f"{context}: status code {code} is declared twice")
        seen.add(code)
        if entry["meaning"] not in STATUS_MEANINGS:
            raise LimelightError(f"{context}: status code {code} has unknown meaning {entry['meaning']!r}")
        missing += entry["meaning"] == "absent"
        if not str(entry.get("label") or "").strip():
            raise LimelightError(f"{context}: status code {code} has no label")
        color = entry.get("color")
        if color is not None and not is_color_like(color):
            raise LimelightError(f"{context}: status code {code} has an invalid color {color!r}")
    if missing > MAX_MISSING_CODES:
        raise LimelightError(f"{context}: {missing} status codes mean absent; at most {MAX_MISSING_CODES} can")


def _check_status_binding(
    handle: h5py.File, hdf5_path: Path, name: str, status: dict[str, Any], length: int,
    declared: set[int], context: str,
) -> None:
    """The status dataset exists, is integer, is as long as its array, and uses only declared codes."""
    dataset_path = status["dataset"]
    column = status.get("column")
    dataset = handle.get(dataset_path)
    where = f"{context}: array {name!r} status {dataset_path!r}"
    if not isinstance(dataset, h5py.Dataset):
        raise LimelightError(f"{where} is not in {hdf5_path.name}")
    if dataset.dtype.kind not in "iu":
        raise LimelightError(f"{where} is {dataset.dtype}; a status dataset holds integer codes")
    if column is None and dataset.ndim != 1:
        raise LimelightError(f"{where} is {dataset.ndim}-D; a 2-D status dataset needs a column")
    if column is not None:
        if dataset.ndim != 2:
            raise LimelightError(f"{where} names column {column}, but is {dataset.ndim}-D, not 2-D")
        if not 0 <= column < dataset.shape[1]:
            raise LimelightError(f"{where} names column {column}, but has {dataset.shape[1]} columns")
    if int(dataset.shape[0]) != length:
        raise LimelightError(f"{where} has {dataset.shape[0]} rows; the array has {length}")
    undeclared: set[int] = set()
    for start in range(0, length, _STATUS_SCAN_BLOCK):
        block = dataset[start : start + _STATUS_SCAN_BLOCK] if column is None else dataset[start : start + _STATUS_SCAN_BLOCK, column]
        undeclared.update(int(code) for code in np.unique(block) if int(code) not in declared)
    if undeclared:
        shown = ", ".join(str(code) for code in sorted(undeclared)[:10])
        more = "" if len(undeclared) <= 10 else f" and {len(undeclared) - 10} more"
        raise LimelightError(f"{where} holds codes the source's statusCodes do not declare: {shown}{more}")


def check_hdf_bindings(
    hdf5_path: Path, y_arrays: list[dict[str, Any]], context: str, *, status_codes: list[dict[str, Any]] | None = None
) -> int:
    """Raise LimelightError on a binding the file cannot honour; return the row count."""
    if not hdf5_path.is_file():
        raise LimelightError(f"{context}: no such HDF5 file {hdf5_path}")
    declared = {int(entry["code"]) for entry in status_codes or []}
    lengths: dict[str, int] = {}
    with h5py.File(hdf5_path, "r") as handle:
        for entry in y_arrays:
            name = entry["schema"]["name"]
            dataset_path = entry["dataset"]
            column = entry.get("column")
            dataset = handle.get(dataset_path)
            if not isinstance(dataset, h5py.Dataset):
                raise LimelightError(f"{context}: array {name!r} names dataset {dataset_path!r}, which is not in {hdf5_path.name}")
            if column is None:
                if dataset.ndim != 1:
                    raise LimelightError(
                        f"{context}: array {name!r} names dataset {dataset_path!r}, which is {dataset.ndim}-D; "
                        f"a 2-D dataset needs a column"
                    )
            else:
                if dataset.ndim != 2:
                    raise LimelightError(
                        f"{context}: array {name!r} names column {column} of dataset {dataset_path!r}, "
                        f"which is {dataset.ndim}-D, not 2-D"
                    )
                if not 0 <= column < dataset.shape[1]:
                    raise LimelightError(
                        f"{context}: array {name!r} names column {column} of dataset {dataset_path!r}, "
                        f"which has {dataset.shape[1]} columns"
                    )
            lengths[name] = int(dataset.shape[0])
            status = entry.get("status")
            if status:
                if not declared:
                    raise LimelightError(f"{context}: array {name!r} has a status dataset, but the source declares no statusCodes")
                _check_status_binding(handle, hdf5_path, name, status, lengths[name], declared, context)
    if len(set(lengths.values())) > 1:
        described = ", ".join(f"{name}: {length}" for name, length in lengths.items())
        raise LimelightError(f"{context}: arrays differ in length ({described}); every array of a source shares its index")
    return next(iter(lengths.values()), 0)


def verify_hdf_sources(
    package: Any, manifest: dict[str, Any], *, allow_download: Callable[[RemoteFile], bool] | None = None
) -> list[str]:
    """Check every hdf source's bindings against its file.

    A remote file that is not downloaded yet is fetched if `allow_download`
    says so; otherwise it is left unchecked, and named in the returned list.
    """
    unchecked: list[str] = []
    for source in manifest["sources"]:
        if "yArrays" not in source:
            continue
        remote = remote_file_of(source)
        if remote is not None and remote.cached_path() is None:
            if allow_download is None or not allow_download(remote):
                unchecked.append(f"Source {source['id']!r}: {remote.url} is not downloaded, so its bindings are unchecked")
                continue
            download(remote)
        check_hdf_bindings(
            hdf_file_path(package, source), source["yArrays"], f"Source {source['id']!r}",
            status_codes=source.get("statusCodes") or [],
        )
    return unchecked
