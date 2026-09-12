from __future__ import annotations

import hashlib

import numpy as np

from .constants import CONTENT_HASH_DIGEST_SIZE


class StreamingHasher:
    """Incrementally hashes raw array bytes as they are read, block by block."""

    def __init__(self, digest_size: int = CONTENT_HASH_DIGEST_SIZE) -> None:
        self._hash = hashlib.blake2b(digest_size=digest_size)

    def update(self, block: np.ndarray) -> None:
        self._hash.update(np.ascontiguousarray(block).tobytes())

    def hexdigest(self) -> str:
        return self._hash.hexdigest()
