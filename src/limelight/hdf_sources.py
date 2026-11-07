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


def check_hdf_bindings(hdf5_path: Path, y_arrays: list[dict[str, Any]], context: str) -> int:
    """Raise LimelightError on a binding the file cannot honour; return the row count."""
    if not hdf5_path.is_file():
        raise LimelightError(f"{context}: no such HDF5 file {hdf5_path}")
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
        check_hdf_bindings(hdf_file_path(package, source), source["yArrays"], f"Source {source['id']!r}")
    return unchecked
