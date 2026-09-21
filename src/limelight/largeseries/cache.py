from __future__ import annotations

import datetime
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import h5py
import numpy as np

from .arraysource import ArraySource, HdfArraySource
from .cachedir import cache_lock, resolve_cache_dir
from .constants import (
    DEFAULT_CHUNK_SIZE,
    HASH_STREAM_BLOCK,
    MIN_LEVEL_BUCKETS,
    SCHEMA_VERSION,
    aligned_stream_block,
    is_power_of_two,
)
from .exceptions import CacheCorruptError, InvalidSourceSpecError, NonMonotonicXError
from .hashing import StreamingHasher
from .memo import MemoEntry, lookup_memo, update_memo
from .pyramid import LevelArrays, LevelZeroBuilder, cascade_levels
from .timing import TimingProbeLike


@dataclass(frozen=True)
class SourceSpec:
    """Describes where a raw series lives and how to interpret its x-axis.

    Every field is stated: a series is either on a uniform grid or has its x
    in a dataset of its own, and `x0`/`dx` map what the file holds onto the
    axis it is drawn on: `x = x0 + i * dx` for a uniform grid (i the sample
    index), `x = x0 + coord * dx` for an irregular one (coord the value read
    from `x_dataset`). The cache only ever stores what the file holds, so the
    mapping can change - a time axis shown in days rather than in the file's
    microseconds - without the cache being rebuilt. `SourceSpec.uniform` and
    `SourceSpec.irregular` say which kind without spelling out the other's
    fields.
    """

    hdf5_path: Path
    y_dataset: str
    x_dataset: str | None
    irregular_x: bool
    x0: float
    dx: float
    # Column of a 2-D dataset for y and x respectively; None for a 1-D one.
    y_column: int | None = None
    x_column: int | None = None

    def __post_init__(self) -> None:
        if self.irregular_x and self.x_dataset is None:
            raise InvalidSourceSpecError("irregular_x=True requires an x_dataset")
        if not self.irregular_x and self.x_dataset is not None:
            raise InvalidSourceSpecError("x_dataset is only used when irregular_x=True")
        if not self.dx > 0:
            raise InvalidSourceSpecError(f"dx must be positive, got {self.dx}")

    @classmethod
    def uniform(
        cls, hdf5_path: str | Path, y_dataset: str, *, x0: float, dx: float, y_column: int | None = None
    ) -> "SourceSpec":
        """A series sampled at `x0 + i * dx`."""
        return cls(
            hdf5_path=Path(hdf5_path), y_dataset=y_dataset, x_dataset=None, irregular_x=False, x0=x0, dx=dx,
            y_column=y_column,
        )

    @classmethod
    def irregular(
        cls,
        hdf5_path: str | Path,
        y_dataset: str,
        x_dataset: str,
        *,
        x0: float = 0.0,
        dx: float = 1.0,
        y_column: int | None = None,
        x_column: int | None = None,
    ) -> "SourceSpec":
        """A series whose x values are read from `x_dataset`, which must be sorted.

        `x0` and `dx` place those values on the axis: `x = x0 + coord * dx`.
        """
        return cls(
            hdf5_path=Path(hdf5_path), y_dataset=y_dataset, x_dataset=x_dataset, irregular_x=True, x0=x0, dx=dx,
            y_column=y_column, x_column=x_column,
        )

    @property
    def y_selector(self) -> str:
        """The y array as one string, for cache keys and messages: `/values[3]` or `/y`."""
        return _selector(self.y_dataset, self.y_column)

    def x_of_coord(self, coord: np.ndarray | float) -> np.ndarray | float:
        """Axis position of a raw coordinate (irregular) or sample index (uniform)."""
        return self.x0 + coord * self.dx

    def coord_of_x(self, x: float) -> float:
        """Inverse of `x_of_coord`: what the file's x holds at axis position `x`."""
        return (x - self.x0) / self.dx

    def open_y(self) -> ArraySource:
        return HdfArraySource(self.hdf5_path, self.y_dataset, self.y_column)

    def open_x(self) -> ArraySource | None:
        if not self.irregular_x:
            return None
        return HdfArraySource(self.hdf5_path, self.x_dataset, self.x_column)


def _selector(dataset: str, column: int | None) -> str:
    return dataset if column is None else f"{dataset}[{column}]"


@dataclass(frozen=True)
class CacheHandle:
    """An open cache file plus enough metadata to answer queries without re-opening."""

    path: Path
    content_hash: str
    source_length: int
    chunk_size: int
    irregular_x: bool
    n_levels: int
    dtype: np.dtype
    x_dtype: np.dtype | None
    _h5: h5py.File = field(repr=False, compare=False)
    # Per-handle memo of small arrays read from the cache file (see
    # query.level0_x_bounds); the handle is frozen but the dict is not.
    _memo: dict = field(default_factory=dict, repr=False, compare=False)

    def level_group(self, level: int) -> h5py.Group:
        return self._h5[f"/levels/{level}"]


def build_or_get_cache(
    spec: SourceSpec,
    *,
    cache_dir: Path | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    force: bool = False,
    stream_block: int = HASH_STREAM_BLOCK,
    timing: TimingProbeLike | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> CacheHandle:
    """Opens the cache for `spec`, building it first if there is none.

    A build streams the whole source once; `progress(samples_done, source_length)`
    is called after each block and once more when the cache is written, so a
    caller can show how far along a long build is. A cache hit reports nothing.

    The cache directory is shared between processes - two windows on one
    package, say - so a build takes a lock named for the series. A second
    process wanting the same cache waits for the first, reporting the wait as
    a build at zero, and then finds the cache in the memo rather than making
    it again.
    """
    if not is_power_of_two(chunk_size):
        raise InvalidSourceSpecError(f"chunk_size must be a power of two, got {chunk_size}")

    resolved_cache_dir = resolve_cache_dir(cache_dir)
    source_path = spec.hdf5_path.resolve()
    stat = source_path.stat()
    mtime_ns = stat.st_mtime_ns
    size = stat.st_size

    def memoised() -> CacheHandle | None:
        if force:
            return None
        return _open_memoised_cache(resolved_cache_dir, source_path, spec, mtime_ns, size, chunk_size, timing)

    handle = memoised()
    if handle is not None:
        return handle

    lock = cache_lock(resolved_cache_dir, _build_lock_name(source_path, spec))
    waited = _acquire_build_lock(lock, spec, progress)
    try:
        handle = memoised()
        if handle is not None:
            if waited and progress is not None:
                progress(handle.source_length, handle.source_length)
            return handle
        return _build_cache(
            spec,
            resolved_cache_dir,
            source_path,
            mtime_ns=mtime_ns,
            size=size,
            chunk_size=chunk_size,
            stream_block=stream_block,
            timing=timing,
            progress=progress,
        )
    finally:
        lock.release()


def _build_lock_name(source_path: Path, spec: SourceSpec) -> str:
    key = f"{source_path}::{spec.y_selector}".encode("utf-8", "surrogateescape")
    return "build-" + hashlib.sha256(key).hexdigest()[:16]


def _acquire_build_lock(lock: Any, spec: SourceSpec, progress: Callable[[int, int], None] | None) -> bool:
    """Take the build lock; returns whether another process was holding it.

    A wait is reported as a build at zero, so the caller's indicator shows
    the series being prepared - by whoever is preparing it.
    """
    from filelock import Timeout

    try:
        lock.acquire(timeout=0)
        return False
    except Timeout:
        pass
    if progress is not None:
        y_array = spec.open_y()
        try:
            progress(0, y_array.length)
        finally:
            y_array.close()
    lock.acquire()
    return True


def _open_memoised_cache(
    cache_dir: Path,
    source_path: Path,
    spec: SourceSpec,
    mtime_ns: int,
    size: int,
    chunk_size: int,
    timing: TimingProbeLike | None,
) -> CacheHandle | None:
    """The cache the memo records for this source as it is now, if it is there and sound."""
    entry = lookup_memo(cache_dir, source_path, spec.y_selector, mtime_ns, size, chunk_size)
    if entry is None:
        return None
    dest = cache_dir / f"{entry.content_hash}.h5"
    if not dest.exists():
        return None
    hit_start = timing.start() if timing is not None else None
    try:
        handle = open_existing_cache(cache_dir, entry.content_hash)
    except CacheCorruptError:
        return None
    if timing is not None and hit_start is not None:
        timing.log(
            "limelight.largeseries.cache.hit",
            hit_start,
            (
                ("source_length", handle.source_length),
                ("chunk_size", handle.chunk_size),
                ("n_levels", handle.n_levels),
                ("cache_bytes", dest.stat().st_size),
                ("content_hash", handle.content_hash),
            ),
        )
    return handle


def _build_cache(
    spec: SourceSpec,
    resolved_cache_dir: Path,
    source_path: Path,
    *,
    mtime_ns: int,
    size: int,
    chunk_size: int,
    stream_block: int,
    timing: TimingProbeLike | None,
    progress: Callable[[int, int], None] | None,
) -> CacheHandle:
    """Stream the source, write its pyramid to the cache and record it in the memo."""
    start = timing.start() if timing is not None else None

    block = aligned_stream_block(stream_block, chunk_size)
    hasher = StreamingHasher()
    builder = LevelZeroBuilder(chunk_size, spec.irregular_x)

    y_array = spec.open_y()
    x_array = spec.open_x()
    try:
        source_length = y_array.length
        dtype = y_array.dtype
        x_dtype = x_array.dtype if x_array is not None else None

        samples_done = 0
        if x_array is not None:
            # Every range query searches the x bounds, so x has to be sorted;
            # the one pass over it that the build makes is where to be sure.
            previous_last: float | None = None
            for y_block, x_block in zip(y_array.iter_blocks(block), x_array.iter_blocks(block)):
                _require_non_decreasing(x_block, previous_last, samples_done, spec)
                if x_block.shape[0]:
                    previous_last = float(x_block[-1])
                hasher.update(y_block)
                hasher.update(x_block)
                builder.feed(y_block, x_block)
                samples_done += y_block.shape[0]
                if progress is not None:
                    progress(samples_done, source_length)
        else:
            for y_block in y_array.iter_blocks(block):
                hasher.update(y_block)
                builder.feed(y_block, None)
                samples_done += y_block.shape[0]
                if progress is not None:
                    progress(samples_done, source_length)
    finally:
        y_array.close()
        if x_array is not None:
            x_array.close()

    level0 = builder.finish()
    content_hash = hasher.hexdigest()

    levels = cascade_levels(level0, MIN_LEVEL_BUCKETS)

    dest_path = resolved_cache_dir / f"{content_hash}.h5"
    _write_cache_file(
        dest_path,
        spec=spec,
        chunk_size=chunk_size,
        content_hash=content_hash,
        source_length=source_length,
        levels=levels,
        dtype=dtype,
        x_dtype=x_dtype,
    )

    update_memo(
        resolved_cache_dir,
        source_path,
        spec.y_selector,
        MemoEntry(
            mtime_ns=mtime_ns,
            size=size,
            chunk_size=chunk_size,
            content_hash=content_hash,
            irregular_x=spec.irregular_x,
        ),
    )

    if progress is not None:
        progress(source_length, source_length)

    if timing is not None and start is not None:
        timing.log(
            "limelight.largeseries.cache.build",
            start,
            (
                ("source_length", source_length),
                ("chunk_size", chunk_size),
                ("n_levels", len(levels)),
                ("cache_bytes", dest_path.stat().st_size),
                ("content_hash", content_hash),
            ),
        )

    return open_existing_cache(resolved_cache_dir, content_hash)


def _require_non_decreasing(x_block: np.ndarray, previous_last: float | None, offset: int, spec: SourceSpec) -> None:
    """Raise NonMonotonicXError at the first sample that steps backwards."""
    if x_block.shape[0] == 0:
        return
    if previous_last is not None and x_block[0] < previous_last:
        bad = offset
    else:
        steps = np.flatnonzero(x_block[1:] < x_block[:-1])
        if steps.size == 0:
            return
        bad = offset + int(steps[0]) + 1
    raise NonMonotonicXError(
        f"{spec.hdf5_path}:{spec.x_dataset} is not sorted: sample {bad} is less than the one before it; "
        f"a large-series x array must be non-decreasing"
    )


def open_existing_cache(cache_dir: Path, content_hash: str) -> CacheHandle:
    path = Path(cache_dir) / f"{content_hash}.h5"
    if not path.is_file():
        raise FileNotFoundError(path)

    handle_file = h5py.File(path, "r")
    try:
        attrs = handle_file.attrs
        if attrs.get("content_hash") != content_hash:
            raise CacheCorruptError(f"{path} content_hash attribute does not match filename")
        if int(attrs.get("schema_version", -1)) != SCHEMA_VERSION:
            raise CacheCorruptError(f"{path} has an unsupported schema_version")

        irregular_x = bool(attrs["irregular_x"])
        n_levels = int(attrs["n_levels"])
        for level in range(n_levels):
            if f"/levels/{level}/y_min" not in handle_file or f"/levels/{level}/y_max" not in handle_file:
                raise CacheCorruptError(f"{path} is missing level {level} data")
            if irregular_x and (
                f"/levels/{level}/x_min" not in handle_file or f"/levels/{level}/x_max" not in handle_file
            ):
                raise CacheCorruptError(f"{path} is missing level {level} x data")

        return CacheHandle(
            path=path,
            content_hash=content_hash,
            source_length=int(attrs["source_length"]),
            chunk_size=int(attrs["chunk_size"]),
            irregular_x=irregular_x,
            n_levels=n_levels,
            dtype=np.dtype(attrs["dtype"]),
            x_dtype=np.dtype(attrs["x_dtype"]) if irregular_x else None,
            _h5=handle_file,
        )
    except Exception:
        handle_file.close()
        raise


def close_cache(handle: CacheHandle) -> None:
    handle._h5.close()


def _write_cache_file(
    dest_path: Path,
    *,
    spec: SourceSpec,
    chunk_size: int,
    content_hash: str,
    source_length: int,
    levels: list[LevelArrays],
    dtype: np.dtype,
    x_dtype: np.dtype | None,
) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix="cache-", suffix=".h5.tmp", dir=dest_path.parent)
    os.close(fd)
    try:
        with h5py.File(tmp_name, "w") as handle:
            handle.attrs["schema_version"] = SCHEMA_VERSION
            handle.attrs["content_hash"] = content_hash
            handle.attrs["source_length"] = source_length
            handle.attrs["chunk_size"] = chunk_size
            handle.attrs["irregular_x"] = np.uint8(1 if spec.irregular_x else 0)
            handle.attrs["dtype"] = str(dtype)
            if spec.irregular_x and x_dtype is not None:
                handle.attrs["x_dtype"] = str(x_dtype)
            handle.attrs["n_levels"] = len(levels)
            handle.attrs["source_path_hint"] = str(spec.hdf5_path)
            handle.attrs["dataset_path_hint"] = spec.y_selector
            handle.attrs["built_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            for level_index, level in enumerate(levels):
                group = handle.create_group(f"/levels/{level_index}")
                group.create_dataset("y_min", data=level.y_min, compression="lzf")
                group.create_dataset("y_max", data=level.y_max, compression="lzf")
                if spec.irregular_x:
                    group.create_dataset("x_min", data=level.x_min, compression="lzf")
                    group.create_dataset("x_max", data=level.x_max, compression="lzf")

        try:
            os.replace(tmp_name, dest_path)
        except PermissionError:
            # Windows refuses to replace a file another process has open.
            # The file there is named by the same content hash, so it holds
            # what was just built; the new copy is not needed.
            if not dest_path.is_file():
                raise
            os.remove(tmp_name)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
