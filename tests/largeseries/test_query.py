from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight.largeseries.cache import SourceSpec, build_or_get_cache, close_cache
from limelight.largeseries.exceptions import UnsupportedQueryError
from limelight.largeseries.query import query_range, sample_index_range_for_x


def _build_uniform_handle(tmp_path: Path, n: int = 4096, chunk_size: int = 8):
    source_path = tmp_path / "source.h5"
    values = np.arange(n, dtype=np.float64)
    with h5py.File(source_path, "w") as handle:
        handle.create_dataset("/y", data=values)

    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    cache_handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=chunk_size)
    return spec, cache_handle, values


def test_level_selection_picks_expected_level(tmp_path: Path) -> None:
    spec, handle, values = _build_uniform_handle(tmp_path, n=8192, chunk_size=8)
    try:
        # chunk_size=8 -> level widths: 8, 16, 32, ...
        # target_bucket_width just below 16 should still select level 0 (8 <= width < 16)
        result = query_range(handle, spec, 0, 8191, target_buckets=8192 // 15)
        assert result.level == 0

        # target_bucket_width >= 16 should select level 1
        result = query_range(handle, spec, 0, 8191, target_buckets=8192 // 16)
        assert result.level == 1
    finally:
        close_cache(handle)


def test_level_zero_range_covers_requested_span(tmp_path: Path) -> None:
    spec, handle, values = _build_uniform_handle(tmp_path, n=100, chunk_size=8)
    try:
        result = query_range(handle, spec, 10, 20, target_buckets=1)
        assert result.level == 0
        # Buckets are chunk-aligned; requested [10, 20] must be fully covered.
        assert result.x.min() - 4 <= 10
        assert result.x.max() + 4 >= 20
    finally:
        close_cache(handle)


def test_raw_fallback_returns_exact_samples_when_small(tmp_path: Path) -> None:
    spec, handle, values = _build_uniform_handle(tmp_path, n=100, chunk_size=64)
    try:
        result = query_range(handle, spec, 0, 9, target_buckets=10_000)
        assert result.level == -1
        assert result.from_cache is False
        np.testing.assert_array_equal(result.y_min, result.y_max)
    finally:
        close_cache(handle)


def test_raw_fallback_thins_when_still_too_large(tmp_path: Path) -> None:
    spec, handle, values = _build_uniform_handle(tmp_path, n=100, chunk_size=64)
    try:
        result = query_range(handle, spec, 0, 63, target_buckets=8)
        assert result.level == -1
        assert result.from_cache is False
        assert result.y_min.shape[0] <= 8
        assert np.any(result.y_min < result.y_max)
    finally:
        close_cache(handle)


def test_empty_range_returns_empty_result(tmp_path: Path) -> None:
    spec, handle, values = _build_uniform_handle(tmp_path, n=100, chunk_size=8)
    try:
        result = query_range(handle, spec, 500, 600, target_buckets=10)
        assert result.x.shape[0] == 0
    finally:
        close_cache(handle)


def test_invalid_range_raises(tmp_path: Path) -> None:
    spec, handle, values = _build_uniform_handle(tmp_path, n=100, chunk_size=8)
    try:
        with pytest.raises(UnsupportedQueryError):
            query_range(handle, spec, 50, 10, target_buckets=10)
    finally:
        close_cache(handle)


def test_irregular_x_index_range_boundaries(tmp_path: Path) -> None:
    source_path = tmp_path / "source.h5"
    values = np.arange(40, dtype=np.float64)
    x_values = np.arange(40, dtype=np.float64) * 2.0  # 0, 2, 4, ..., 78
    with h5py.File(source_path, "w") as handle:
        handle.create_dataset("/y", data=values)
        handle.create_dataset("/x", data=x_values)

    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y", x_dataset="/x", irregular_x=True)
    cache_handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=4)
    try:
        i0, i1 = sample_index_range_for_x(cache_handle, spec, 4.0, 20.0)
        assert i0 == 2  # sample index 2 has x=4.0
        assert i1 == 11  # sample index 10 has x=20.0, and the range is half-open

        # Bounds that fall between samples still bracket exactly the samples inside them.
        assert sample_index_range_for_x(cache_handle, spec, 4.5, 19.5) == (3, 10)
        # An empty span between two samples yields an empty range.
        i0, i1 = sample_index_range_for_x(cache_handle, spec, 4.5, 5.5)
        assert i0 >= i1
    finally:
        close_cache(cache_handle)


def _build_irregular_handle(tmp_path: Path, n: int, chunk_size: int):
    source_path = tmp_path / "source.h5"
    rng = np.random.default_rng(1)
    x_values = np.cumsum(rng.uniform(0.5, 1.5, size=n))
    values = np.sin(x_values)
    with h5py.File(source_path, "w") as handle:
        handle.create_dataset("/y", data=values)
        handle.create_dataset("/x", data=x_values)
    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y", x_dataset="/x", irregular_x=True)
    cache_handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=chunk_size)
    return spec, cache_handle, x_values


def test_irregular_x_zoomed_in_query_reaches_raw_samples(tmp_path: Path) -> None:
    spec, cache_handle, x_values = _build_irregular_handle(tmp_path, n=100_000, chunk_size=64)
    try:
        # Deep inside the series, a span of ~20 samples: nothing coarser than
        # the samples themselves should come back.
        x_start, x_end = float(x_values[50_000]), float(x_values[50_020])
        result = query_range(cache_handle, spec, x_start, x_end, target_buckets=1000)
        assert result.raw is True
        assert result.sample_count == 21
        np.testing.assert_array_equal(result.x, x_values[50_000:50_021])
    finally:
        close_cache(cache_handle)


def test_irregular_x_index_lookup_reads_only_boundary_chunks(tmp_path: Path, monkeypatch) -> None:
    from limelight.largeseries import arraysource

    spec, cache_handle, x_values = _build_irregular_handle(tmp_path, n=100_000, chunk_size=64)
    reads: list[int] = []
    original = arraysource.HdfArraySource.read_range

    def counting_read_range(self, start, end):
        reads.append(end - start)
        return original(self, start, end)

    monkeypatch.setattr(arraysource.HdfArraySource, "read_range", counting_read_range)
    try:
        # Zoomed all the way out: the whole series is in range, but locating
        # it must not read the whole x array.
        sample_index_range_for_x(cache_handle, spec, float(x_values[0]), float(x_values[-1]))
        assert reads and max(reads) <= 64
    finally:
        close_cache(cache_handle)
