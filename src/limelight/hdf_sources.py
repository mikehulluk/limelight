"""Checks that an hdf source's manifest entries agree with the HDF5 file.

The manifest says which dataset (and, for a 2-D dataset, which column) backs
each array; only the file can say whether that dataset exists, has that many
columns, and is as long as the source's other arrays. `verify` runs this over
a package, and the writer runs it when a dataset is added, so a wrong binding
is reported where it is made rather than by the viewer at first draw.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py

from .reader import LimelightError


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


def verify_hdf_sources(package: Any, manifest: dict[str, Any]) -> None:
    for source in manifest["sources"]:
        if "yArrays" not in source:
            continue
        check_hdf_bindings(package.package_path(source["path"]), source["yArrays"], f"Source {source['id']!r}")
