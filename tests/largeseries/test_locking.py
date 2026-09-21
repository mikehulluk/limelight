"""The cache directory is shared between processes, and its writers take turns.

Each test starts a real second process, since the locks are only meant
to hold between processes: two windows on one package, or the app and
`LL` at once.
"""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import h5py
import numpy as np

from limelight.largeseries import cache as cache_module
from limelight.largeseries.cache import SourceSpec, build_or_get_cache, close_cache
from limelight.largeseries.cachedir import cache_lock
from limelight.largeseries.memo import MemoEntry, load_memo, update_memo


def _record_entry(cache_dir: Path, index: int) -> None:
    entry = MemoEntry(mtime_ns=index, size=index, chunk_size=8, content_hash=f"hash{index}", irregular_x=False)
    update_memo(cache_dir, cache_dir / f"source{index}.h5", "/y", entry)


def test_memo_updates_from_many_processes_all_survive(tmp_path: Path) -> None:
    # Each process reads the memo, adds its entry and writes it back; without
    # the lock, two doing so at once leave one entry behind.
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_record_entry, args=(tmp_path, index)) for index in range(8)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    assert {entry.content_hash for entry in load_memo(tmp_path).values()} == {f"hash{i}" for i in range(8)}


def _build_in_process(source_path: Path, cache_dir: Path, results: "multiprocessing.Queue") -> None:
    reports: list[tuple[int, int]] = []
    spec = SourceSpec.uniform(source_path, "/y", x0=0.0, dx=1.0)
    handle = build_or_get_cache(spec, cache_dir=cache_dir, chunk_size=8, progress=lambda d, t: reports.append((d, t)))
    built_at = handle._h5.attrs["built_at"]
    close_cache(handle)
    results.put((str(built_at), reports))


def test_a_second_process_waits_for_the_build_and_then_finds_it(tmp_path: Path) -> None:
    source_path = tmp_path / "source.h5"
    n = 200
    with h5py.File(source_path, "w") as handle:
        handle.create_dataset("/y", data=np.arange(n, dtype=np.float64))
    spec = SourceSpec.uniform(source_path, "/y", x0=0.0, dx=1.0)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    context = multiprocessing.get_context("spawn")
    results: multiprocessing.Queue = context.Queue()
    lock = cache_lock(cache_dir, cache_module._build_lock_name(source_path.resolve(), spec))
    with lock:
        # This process stands in for one mid-build: it holds the series'
        # lock, and the other process has to wait for it.
        other = context.Process(target=_build_in_process, args=(source_path, cache_dir, results))
        other.start()
        time.sleep(1.5)
        assert other.is_alive() and results.empty()

        # The build finishes here while the other waits.
        stat = source_path.stat()
        handle = cache_module._build_cache(
            spec, cache_dir, source_path.resolve(),
            mtime_ns=stat.st_mtime_ns, size=stat.st_size, chunk_size=8,
            stream_block=cache_module.HASH_STREAM_BLOCK, timing=None, progress=None,
        )
        built_at = str(handle._h5.attrs["built_at"])
        close_cache(handle)

    other.join(timeout=60)
    assert other.exitcode == 0
    other_built_at, reports = results.get(timeout=5)
    # It opened the file this process wrote rather than building its own...
    assert other_built_at == built_at
    # ...and showed the wait as a build at zero that then completed.
    assert reports == [(0, n), (n, n)]
    assert len(load_memo(cache_dir)) == 1
