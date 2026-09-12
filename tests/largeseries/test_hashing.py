from __future__ import annotations

import hashlib

import numpy as np

from limelight.largeseries.hashing import StreamingHasher


def test_single_update_matches_blake2b_reference() -> None:
    arr = np.arange(100, dtype=np.float64)
    hasher = StreamingHasher(digest_size=16)
    hasher.update(arr)

    expected = hashlib.blake2b(arr.tobytes(), digest_size=16).hexdigest()
    assert hasher.hexdigest() == expected


def test_streaming_updates_match_single_update() -> None:
    arr = np.arange(180, dtype=np.float64)

    one_shot = StreamingHasher(digest_size=16)
    one_shot.update(arr)

    streamed = StreamingHasher(digest_size=16)
    for chunk in (arr[:5], arr[5:47], arr[47:180]):
        streamed.update(chunk)

    assert streamed.hexdigest() == one_shot.hexdigest()


def test_different_dtype_changes_hash() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    hasher_f64 = StreamingHasher(digest_size=16)
    hasher_f64.update(np.array(values, dtype=np.float64))

    hasher_f32 = StreamingHasher(digest_size=16)
    hasher_f32.update(np.array(values, dtype=np.float32))

    assert hasher_f64.hexdigest() != hasher_f32.hexdigest()
