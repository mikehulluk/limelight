from __future__ import annotations

import json
from pathlib import Path

from limelight.largeseries.memo import MemoEntry, load_memo, lookup_memo, update_memo


def _entry(content_hash: str = "abc123") -> MemoEntry:
    return MemoEntry(mtime_ns=1_000, size=2_000, chunk_size=4096, content_hash=content_hash, irregular_x=False)


def test_lookup_hits_when_all_fields_match(tmp_path: Path) -> None:
    source = tmp_path / "source.h5"
    update_memo(tmp_path, source, "/y", _entry())

    hit = lookup_memo(tmp_path, source, "/y", mtime_ns=1_000, size=2_000, chunk_size=4096)

    assert hit is not None
    assert hit.content_hash == "abc123"


def test_lookup_misses_on_mtime_change(tmp_path: Path) -> None:
    source = tmp_path / "source.h5"
    update_memo(tmp_path, source, "/y", _entry())

    assert lookup_memo(tmp_path, source, "/y", mtime_ns=1_001, size=2_000, chunk_size=4096) is None


def test_lookup_misses_on_size_change(tmp_path: Path) -> None:
    source = tmp_path / "source.h5"
    update_memo(tmp_path, source, "/y", _entry())

    assert lookup_memo(tmp_path, source, "/y", mtime_ns=1_000, size=2_001, chunk_size=4096) is None


def test_lookup_misses_on_chunk_size_change(tmp_path: Path) -> None:
    source = tmp_path / "source.h5"
    update_memo(tmp_path, source, "/y", _entry())

    assert lookup_memo(tmp_path, source, "/y", mtime_ns=1_000, size=2_000, chunk_size=1024) is None


def test_load_memo_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert load_memo(tmp_path) == {}


def test_load_memo_returns_empty_on_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / "memo.json").write_text("{not valid json", encoding="utf-8")
    assert load_memo(tmp_path) == {}


def test_independent_entries_per_dataset_path(tmp_path: Path) -> None:
    source = tmp_path / "source.h5"
    update_memo(tmp_path, source, "/y", _entry(content_hash="hash-y"))
    update_memo(tmp_path, source, "/other", _entry(content_hash="hash-other"))

    hit_y = lookup_memo(tmp_path, source, "/y", mtime_ns=1_000, size=2_000, chunk_size=4096)
    hit_other = lookup_memo(tmp_path, source, "/other", mtime_ns=1_000, size=2_000, chunk_size=4096)

    assert hit_y is not None and hit_y.content_hash == "hash-y"
    assert hit_other is not None and hit_other.content_hash == "hash-other"


def test_update_memo_preserves_previous_entries(tmp_path: Path) -> None:
    source_a = tmp_path / "a.h5"
    source_b = tmp_path / "b.h5"

    update_memo(tmp_path, source_a, "/y", _entry(content_hash="hash-a"))
    update_memo(tmp_path, source_b, "/y", _entry(content_hash="hash-b"))

    entries = load_memo(tmp_path)
    assert len(entries) == 2

    memo_json = json.loads((tmp_path / "memo.json").read_text(encoding="utf-8"))
    assert memo_json["version"] == 1
