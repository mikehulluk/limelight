from __future__ import annotations

import numpy as np

DEFAULT_CHUNK_SIZE: int = 4096
MIN_LEVEL_BUCKETS: int = 8
SCHEMA_VERSION: int = 1
HASH_STREAM_BLOCK: int = 1 << 20
MINMAX_DTYPE = np.float64
CONTENT_HASH_DIGEST_SIZE: int = 16


def is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def aligned_stream_block(stream_block: int, chunk_size: int) -> int:
    """Round stream_block down to the nearest positive multiple of chunk_size."""
    if stream_block < chunk_size:
        return chunk_size
    return (stream_block // chunk_size) * chunk_size
