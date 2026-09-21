from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .cachedir import cache_lock

MEMO_FILENAME = "memo.json"
MEMO_VERSION = 1


@dataclass(frozen=True)
class MemoEntry:
    mtime_ns: int
    size: int
    chunk_size: int
    content_hash: str
    irregular_x: bool

    def to_json(self) -> dict[str, object]:
        return {
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "chunk_size": self.chunk_size,
            "content_hash": self.content_hash,
            "irregular_x": self.irregular_x,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "MemoEntry":
        return cls(
            mtime_ns=int(data["mtime_ns"]),
            size=int(data["size"]),
            chunk_size=int(data["chunk_size"]),
            content_hash=str(data["content_hash"]),
            irregular_x=bool(data["irregular_x"]),
        )


def memo_key(source_path: Path, dataset_path: str) -> str:
    return f"{source_path.resolve()}::{dataset_path}"


def load_memo(cache_dir: Path) -> dict[str, MemoEntry]:
    memo_path = cache_dir / MEMO_FILENAME
    try:
        raw = json.loads(memo_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
    result: dict[str, MemoEntry] = {}
    for key, value in entries.items():
        try:
            result[key] = MemoEntry.from_json(value)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def lookup_memo(
    cache_dir: Path,
    source_path: Path,
    dataset_path: str,
    mtime_ns: int,
    size: int,
    chunk_size: int,
) -> MemoEntry | None:
    entries = load_memo(cache_dir)
    entry = entries.get(memo_key(source_path, dataset_path))
    if entry is None:
        return None
    if entry.mtime_ns != mtime_ns or entry.size != size or entry.chunk_size != chunk_size:
        return None
    return entry


def update_memo(
    cache_dir: Path,
    source_path: Path,
    dataset_path: str,
    entry: MemoEntry,
) -> None:
    """Record `entry`; the memo is read and written back whole, so writers take turns."""

    with cache_lock(cache_dir, "memo"):
        entries = load_memo(cache_dir)
        entries[memo_key(source_path, dataset_path)] = entry

        payload = {
            "version": MEMO_VERSION,
            "entries": {key: value.to_json() for key, value in entries.items()},
        }

        memo_path = cache_dir / MEMO_FILENAME
        fd, tmp_name = tempfile.mkstemp(prefix="memo-", suffix=".json.tmp", dir=cache_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp_name, memo_path)
        except BaseException:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
            raise
