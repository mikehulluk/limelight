from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .cache import CacheHandle, SourceSpec
from .constants import MINMAX_DTYPE
from .exceptions import UnsupportedQueryError
from .pyramid import reduce_block_to_buckets
from .timing import TimingProbeLike


@dataclass(frozen=True)
class QueryResult:
    x: np.ndarray
    y_min: np.ndarray
    y_max: np.ndarray
    level: int
    from_cache: bool
    # True when every point is an unreduced source sample (so y_min == y_max
    # and the series should be drawn as a plain line, not an envelope).
    raw: bool = False
    # Number of source samples covered by the query (before any reduction).
    sample_count: int = 0


def query_range(
    handle: CacheHandle,
    spec: SourceSpec,
    x_start: float,
    x_end: float,
    target_buckets: int,
    *,
    timing: TimingProbeLike | None = None,
) -> QueryResult:
    if target_buckets <= 0:
        raise UnsupportedQueryError("target_buckets must be positive")
    if x_start > x_end:
        raise UnsupportedQueryError(f"x_start ({x_start}) must be <= x_end ({x_end})")

    start = timing.start() if timing is not None else None

    i0, i1 = sample_index_range_for_x(handle, spec, x_start, x_end)
    i0 = max(0, i0)
    i1 = min(handle.source_length, i1)
    if i0 >= i1:
        empty = np.array([], dtype=MINMAX_DTYPE)
        result = QueryResult(x=empty, y_min=empty, y_max=empty, level=-1, from_cache=False)
    else:
        requested_samples = i1 - i0
        target_bucket_width = requested_samples / target_buckets

        log2_chunk = handle.chunk_size.bit_length() - 1
        if target_bucket_width >= handle.chunk_size:
            level = int(math.floor(math.log2(target_bucket_width) - log2_chunk))
            level = min(level, handle.n_levels - 1)
        else:
            level = -1

        if level >= 0:
            bucket_width = handle.chunk_size << level
            bucket_id0 = i0 // bucket_width
            bucket_id1 = -(-i1 // bucket_width)  # ceil division

            group = handle.level_group(level)
            y_min = np.asarray(group["y_min"][bucket_id0:bucket_id1])
            y_max = np.asarray(group["y_max"][bucket_id0:bucket_id1])

            if handle.irregular_x:
                x_min = np.asarray(group["x_min"][bucket_id0:bucket_id1])
                x_max = np.asarray(group["x_max"][bucket_id0:bucket_id1])
                x = (x_min + x_max) / 2.0
            else:
                centers = (np.arange(bucket_id0, bucket_id1, dtype=np.float64) + 0.5) * bucket_width
                x = spec.x0 + centers * spec.dx

            result = QueryResult(
                x=x, y_min=y_min, y_max=y_max, level=level, from_cache=True, sample_count=requested_samples
            )
        else:
            result = _raw_fallback(handle, spec, i0, i1, target_buckets)

    if timing is not None and start is not None:
        timing.log(
            "limelight.largeseries.query",
            start,
            (
                ("x_start", x_start),
                ("x_end", x_end),
                ("target_buckets", target_buckets),
                ("level", result.level),
                ("from_cache", result.from_cache),
                ("points", int(result.x.shape[0])),
            ),
        )

    return result


def _raw_fallback(
    handle: CacheHandle,
    spec: SourceSpec,
    i0: int,
    i1: int,
    target_buckets: int,
) -> QueryResult:
    y_array = spec.open_y()
    try:
        y_raw = y_array.read_range(i0, i1)
    finally:
        y_array.close()

    x_raw = None
    x_array = spec.open_x()
    if x_array is not None:
        try:
            x_raw = x_array.read_range(i0, i1)
        finally:
            x_array.close()

    sample_count = y_raw.shape[0]

    if x_raw is not None:
        x = x_raw.astype(np.float64, copy=False)
    else:
        x = spec.x0 + (np.arange(i0, i1, dtype=np.float64)) * spec.dx

    if sample_count <= target_buckets:
        y = y_raw.astype(MINMAX_DTYPE, copy=False)
        return QueryResult(
            x=x, y_min=y, y_max=y, level=-1, from_cache=False, raw=True, sample_count=sample_count
        )

    bucket_width = -(-sample_count // target_buckets)  # ceil division
    y_min, y_max, x_min, x_max = reduce_block_to_buckets(y_raw, x_raw, bucket_width)

    if x_raw is not None and x_min is not None and x_max is not None:
        bucket_x = (x_min.astype(np.float64) + x_max.astype(np.float64)) / 2.0
    else:
        n_buckets = y_min.shape[0]
        bucket_centers = (np.arange(n_buckets, dtype=np.float64) + 0.5) * bucket_width
        bucket_x = spec.x0 + (i0 + bucket_centers) * spec.dx

    return QueryResult(
        x=bucket_x, y_min=y_min, y_max=y_max, level=-1, from_cache=False, sample_count=sample_count
    )


def full_x_range(handle: CacheHandle, spec: SourceSpec) -> tuple[float, float]:
    if handle.source_length == 0:
        return (0.0, 1.0)

    if not handle.irregular_x:
        x_start = spec.x0
        x_end = spec.x0 + (handle.source_length - 1) * spec.dx
        return (x_start, x_end)

    top_level = handle.n_levels - 1
    group = handle.level_group(top_level)
    x_min = np.asarray(group["x_min"])
    x_max = np.asarray(group["x_max"])
    return (float(x_min[0]), float(x_max[-1]))


def sample_index_range_for_x(
    handle: CacheHandle,
    spec: SourceSpec,
    x_start: float,
    x_end: float,
) -> tuple[int, int]:
    if not handle.irregular_x:
        i0 = math.floor((x_start - spec.x0) / spec.dx)
        i1 = math.ceil((x_end - spec.x0) / spec.dx) + 1
        return i0, i1

    top_level = handle.n_levels - 1
    group = handle.level_group(top_level)
    x_min = np.asarray(group["x_min"])
    x_max = np.asarray(group["x_max"])
    bucket_width = handle.chunk_size << top_level

    bucket_lo = int(np.searchsorted(x_max, x_start, side="left"))
    bucket_hi = int(np.searchsorted(x_min, x_end, side="right"))
    bucket_lo = max(0, min(bucket_lo, len(x_min) - 1))
    bucket_hi = max(bucket_lo, min(bucket_hi, len(x_min)))

    i0 = bucket_lo * bucket_width
    i1 = bucket_hi * bucket_width
    return i0, i1
