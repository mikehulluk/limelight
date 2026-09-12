from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

import h5py
import numpy as np


@runtime_checkable
class ArraySource(Protocol):
    """Disk-backed, random-seek 1-D numeric array data."""

    @property
    def length(self) -> int: ...

    @property
    def dtype(self) -> np.dtype: ...

    def read_range(self, start: int, end: int) -> np.ndarray: ...

    def iter_blocks(self, block_size: int) -> Iterator[np.ndarray]:
        """Sequential streaming pass over the whole array, in order."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SourceIdentity:
    """Enough information to detect whether an on-disk array has changed."""

    path: Path
    mtime_ns: int
    size: int


class HdfArraySource:
    """Concrete ArraySource backed by one h5py Dataset in one HDF5 file."""

    def __init__(self, hdf5_path: Path, dataset_path: str) -> None:
        self.hdf5_path = Path(hdf5_path)
        self.dataset_path = dataset_path
        self._file = h5py.File(self.hdf5_path, "r")
        self._dataset = self._file[dataset_path]

    @property
    def length(self) -> int:
        return int(self._dataset.shape[0])

    @property
    def dtype(self) -> np.dtype:
        return self._dataset.dtype

    def read_range(self, start: int, end: int) -> np.ndarray:
        return np.asarray(self._dataset[start:end])

    def iter_blocks(self, block_size: int) -> Iterator[np.ndarray]:
        length = self.length
        for offset in range(0, length, block_size):
            end = min(offset + block_size, length)
            yield np.asarray(self._dataset[offset:end])

    def identity(self) -> SourceIdentity:
        resolved = self.hdf5_path.resolve()
        stat = resolved.stat()
        return SourceIdentity(path=resolved, mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "HdfArraySource":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
