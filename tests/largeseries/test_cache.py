from __future__ import annotations

import math
from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight.largeseries import cache as cache_module
from limelight.largeseries.cache import (
    CacheHandle,
    SourceSpec,
    build_or_get_cache,
    close_cache,
    open_existing_cache,
)
from limelight.largeseries.exceptions import CacheCorruptError


def _write_source(path: Path, values: np.ndarray, *, x_values: np.ndarray | None = None) -> None:
    with h5py.File(path, "w") as handle:
        handle.create_dataset("/y", data=values)
        if x_values is not None:
            handle.create_dataset("/x", data=x_values)


def test_build_uniform_grid_cache_matches_expected_buckets(tmp_path: Path) -> None:
    source_path = tmp_path / "source.h5"
    values = np.arange(37, dtype=np.float64)
    _write_source(source_path, values)

    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=4)

    try:
        assert handle.source_length == 37
        assert handle.chunk_size == 4
        assert handle.irregular_x is False
        expected_buckets = math.ceil(37 / 4)

        y_min = np.asarray(handle.level_group(0)["y_min"])
        y_max = np.asarray(handle.level_group(0)["y_max"])
        assert y_min.shape[0] == expected_buckets

        for bucket_index in range(expected_buckets):
            start = bucket_index * 4
            end = min(start + 4, 37)
            assert y_min[bucket_index] == values[start:end].min()
            assert y_max[bucket_index] == values[start:end].max()
    finally:
        close_cache(handle)


def test_build_irregular_x_cache_stores_x_min_max(tmp_path: Path) -> None:
    source_path = tmp_path / "source.h5"
    rng = np.random.default_rng(0)
    values = rng.normal(size=25)
    x_values = np.cumsum(rng.uniform(0.5, 2.0, size=25))
    _write_source(source_path, values, x_values=x_values)

    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y", x_dataset="/x", irregular_x=True)
    handle = build_or_get_cache(spec, cache_dir=tmp_path / "cache", chunk_size=4)

    try:
        assert handle.irregular_x is True
        for level in range(handle.n_levels):
            group = handle.level_group(level)
            assert "x_min" in group
            assert "x_max" in group
    finally:
        close_cache(handle)


def test_second_call_reuses_memo_without_rebuilding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "source.h5"
    _write_source(source_path, np.arange(200, dtype=np.float64))
    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    cache_dir = tmp_path / "cache"

    handle1 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    close_cache(handle1)

    build_calls = {"count": 0}
    original_feed = cache_module.LevelZeroBuilder.feed

    def _counting_feed(self, *args, **kwargs):
        build_calls["count"] += 1
        return original_feed(self, *args, **kwargs)

    monkeypatch.setattr(cache_module.LevelZeroBuilder, "feed", _counting_feed)

    handle2 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    close_cache(handle2)

    assert build_calls["count"] == 0


def test_source_change_invalidates_memo_and_rebuilds(tmp_path: Path) -> None:
    source_path = tmp_path / "source.h5"
    _write_source(source_path, np.arange(100, dtype=np.float64))
    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    cache_dir = tmp_path / "cache"

    handle1 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    hash1 = handle1.content_hash
    close_cache(handle1)

    _write_source(source_path, np.arange(100, 200, dtype=np.float64))

    handle2 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    hash2 = handle2.content_hash
    close_cache(handle2)

    assert hash1 != hash2
    assert (cache_dir / f"{hash1}.h5").exists()
    assert (cache_dir / f"{hash2}.h5").exists()


def test_force_always_rebuilds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = tmp_path / "source.h5"
    _write_source(source_path, np.arange(100, dtype=np.float64))
    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    cache_dir = tmp_path / "cache"

    handle1 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    close_cache(handle1)

    build_calls = {"count": 0}
    original_feed = cache_module.LevelZeroBuilder.feed

    def _counting_feed(self, *args, **kwargs):
        build_calls["count"] += 1
        return original_feed(self, *args, **kwargs)

    monkeypatch.setattr(cache_module.LevelZeroBuilder, "feed", _counting_feed)

    handle2 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8, force=True)
    close_cache(handle2)

    assert build_calls["count"] > 0


def test_corrupt_cache_file_triggers_rebuild(tmp_path: Path) -> None:
    source_path = tmp_path / "source.h5"
    _write_source(source_path, np.arange(100, dtype=np.float64))
    spec = SourceSpec(hdf5_path=source_path, y_dataset="/y")
    cache_dir = tmp_path / "cache"

    handle1 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    content_hash = handle1.content_hash
    close_cache(handle1)

    cache_path = cache_dir / f"{content_hash}.h5"
    with h5py.File(cache_path, "a") as handle:
        del handle["/levels/0/y_max"]

    with pytest.raises(CacheCorruptError):
        open_existing_cache(cache_dir, content_hash)

    handle2 = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8)
    close_cache(handle2)
    assert handle2.content_hash == content_hash

    handle3 = open_existing_cache(cache_dir, content_hash)
    assert "y_max" in handle3.level_group(0)
    close_cache(handle3)
