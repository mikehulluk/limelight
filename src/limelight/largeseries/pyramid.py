from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from .constants import MINMAX_DTYPE
from .status import MASK_DTYPE, NAN_KIND


# A NaN in the source is a missing sample, not a value: a bucket's envelope is
# the min/max of the samples it does have, and only a bucket with no samples at
# all is NaN (which matplotlib draws as a gap). numpy's plain min/max would
# instead make the whole bucket NaN, and then every level above it, so one bad
# reading in a chunk of 4096 blanked the chunk at every zoom. nanmin/nanmax warn
# on an all-NaN slice; that case is the intended result here, so the warning is
# silenced rather than the caller having to.
def _nan_min(values: np.ndarray, axis: int | None = None) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmin(values, axis=axis)


def _nan_max(values: np.ndarray, axis: int | None = None) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmax(values, axis=axis)


@dataclass(frozen=True)
class LevelArrays:
    """One pyramid level's bucket data. x_min/x_max are None for uniform-grid series."""

    y_min: np.ndarray
    y_max: np.ndarray
    x_min: np.ndarray | None
    x_max: np.ndarray | None
    # Per bucket, which kinds of missing sample it holds: bit k for kind k
    # (see status.py). None where it was not computed.
    missing: np.ndarray | None = None


def nan_bits(y_block: np.ndarray) -> np.ndarray:
    """Per-sample missing bits for a series with no status: NAN_KIND wherever y is NaN."""
    bits = np.zeros(y_block.shape[0], dtype=MASK_DTYPE)
    if y_block.dtype.kind == "f":
        bits[np.isnan(y_block)] = MASK_DTYPE(1 << NAN_KIND)
    return bits


def reduce_bits_to_buckets(bits: np.ndarray, chunk_size: int) -> np.ndarray:
    """OR of the per-sample missing bits over each chunk_size-wide bucket (a short last one included)."""
    length = bits.shape[0]
    n_full = length // chunk_size
    parts = []
    if n_full > 0:
        parts.append(np.bitwise_or.reduce(bits[: n_full * chunk_size].reshape(n_full, chunk_size), axis=1))
    if length > n_full * chunk_size:
        parts.append(np.array([np.bitwise_or.reduce(bits[n_full * chunk_size :])], dtype=MASK_DTYPE))
    if not parts:
        return np.array([], dtype=MASK_DTYPE)
    return np.concatenate(parts).astype(MASK_DTYPE, copy=False)


def reduce_block_to_buckets(
    y_block: np.ndarray,
    x_block: np.ndarray | None,
    chunk_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Vectorized min/max reduction of a 1-D block into chunk_size-wide buckets.

    The block need not be an exact multiple of chunk_size; any trailing
    remainder becomes one final, shorter bucket.
    """
    length = y_block.shape[0]
    n_full = length // chunk_size
    remainder = length - n_full * chunk_size

    y_min_parts = []
    y_max_parts = []
    x_min_parts: list[np.ndarray] = []
    x_max_parts: list[np.ndarray] = []

    if n_full > 0:
        full_len = n_full * chunk_size
        y_full = y_block[:full_len].reshape(n_full, chunk_size)
        y_min_parts.append(_nan_min(y_full, axis=1))
        y_max_parts.append(_nan_max(y_full, axis=1))
        if x_block is not None:
            x_full = x_block[:full_len].reshape(n_full, chunk_size)
            x_min_parts.append(x_full.min(axis=1))
            x_max_parts.append(x_full.max(axis=1))

    if remainder > 0:
        y_tail = y_block[n_full * chunk_size :]
        y_min_parts.append(np.array([_nan_min(y_tail)], dtype=MINMAX_DTYPE))
        y_max_parts.append(np.array([_nan_max(y_tail)], dtype=MINMAX_DTYPE))
        if x_block is not None:
            x_tail = x_block[n_full * chunk_size :]
            x_min_parts.append(np.array([x_tail.min()], dtype=x_block.dtype))
            x_max_parts.append(np.array([x_tail.max()], dtype=x_block.dtype))

    y_min = np.concatenate(y_min_parts).astype(MINMAX_DTYPE, copy=False)
    y_max = np.concatenate(y_max_parts).astype(MINMAX_DTYPE, copy=False)
    x_min = np.concatenate(x_min_parts) if x_block is not None else None
    x_max = np.concatenate(x_max_parts) if x_block is not None else None
    return y_min, y_max, x_min, x_max


class LevelZeroBuilder:
    """Consumes raw (y[, x]) blocks in order, accumulates chunk_size buckets."""

    def __init__(self, chunk_size: int, irregular_x: bool) -> None:
        self.chunk_size = chunk_size
        self.irregular_x = irregular_x
        self._y_min_parts: list[np.ndarray] = []
        self._y_max_parts: list[np.ndarray] = []
        self._x_min_parts: list[np.ndarray] = []
        self._x_max_parts: list[np.ndarray] = []
        self._missing_parts: list[np.ndarray] = []
        self._carry_y: np.ndarray | None = None
        self._carry_x: np.ndarray | None = None
        self._carry_bits: np.ndarray | None = None
        self._total_samples = 0

    def feed(self, y_block: np.ndarray, x_block: np.ndarray | None = None, bits: np.ndarray | None = None) -> None:
        """Take the next samples. `bits` are their missing-kind bits; without them a NaN is NAN_KIND."""
        if self.irregular_x and x_block is None:
            raise ValueError("irregular_x=True requires x_block on every feed() call")
        if bits is None:
            bits = nan_bits(y_block)

        self._total_samples += y_block.shape[0]

        if self._carry_y is not None:
            y_block = np.concatenate([self._carry_y, y_block])
            bits = np.concatenate([self._carry_bits, bits])
            if x_block is not None:
                x_block = np.concatenate([self._carry_x, x_block])

        length = y_block.shape[0]
        n_full = length // self.chunk_size
        full_len = n_full * self.chunk_size

        if full_len > 0:
            y_min, y_max, x_min, x_max = reduce_block_to_buckets(
                y_block[:full_len],
                x_block[:full_len] if x_block is not None else None,
                self.chunk_size,
            )
            self._y_min_parts.append(y_min)
            self._y_max_parts.append(y_max)
            self._missing_parts.append(reduce_bits_to_buckets(bits[:full_len], self.chunk_size))
            if x_min is not None:
                self._x_min_parts.append(x_min)
                self._x_max_parts.append(x_max)

        if full_len < length:
            self._carry_y = y_block[full_len:]
            self._carry_x = x_block[full_len:] if x_block is not None else None
            self._carry_bits = bits[full_len:]
        else:
            self._carry_y = None
            self._carry_x = None
            self._carry_bits = None

    def finish(self) -> LevelArrays:
        if self._carry_y is not None and self._carry_y.shape[0] > 0:
            y_min, y_max, x_min, x_max = reduce_block_to_buckets(
                self._carry_y, self._carry_x, self.chunk_size
            )
            self._y_min_parts.append(y_min)
            self._y_max_parts.append(y_max)
            self._missing_parts.append(reduce_bits_to_buckets(self._carry_bits, self.chunk_size))
            if x_min is not None:
                self._x_min_parts.append(x_min)
                self._x_max_parts.append(x_max)
            self._carry_y = None
            self._carry_x = None
            self._carry_bits = None

        y_min = np.concatenate(self._y_min_parts) if self._y_min_parts else np.array([], dtype=MINMAX_DTYPE)
        y_max = np.concatenate(self._y_max_parts) if self._y_max_parts else np.array([], dtype=MINMAX_DTYPE)
        x_min = np.concatenate(self._x_min_parts) if self.irregular_x and self._x_min_parts else None
        x_max = np.concatenate(self._x_max_parts) if self.irregular_x and self._x_max_parts else None
        missing = np.concatenate(self._missing_parts) if self._missing_parts else np.array([], dtype=MASK_DTYPE)
        return LevelArrays(y_min=y_min, y_max=y_max, x_min=x_min, x_max=x_max, missing=missing)


def cascade_levels(level0: LevelArrays, min_level_buckets: int) -> list[LevelArrays]:
    """Build the full pyramid from level0 by pairwise-combining adjacent buckets."""
    levels = [level0]
    current = level0

    while len(current.y_min) > 1 and len(current.y_min) > min_level_buckets:
        n = len(current.y_min)
        n_pairs = n // 2
        has_remainder = n % 2 == 1
        paired_len = 2 * n_pairs

        y_min = np.fmin(current.y_min[0:paired_len:2], current.y_min[1:paired_len:2])
        y_max = np.fmax(current.y_max[0:paired_len:2], current.y_max[1:paired_len:2])
        if has_remainder:
            y_min = np.concatenate([y_min, current.y_min[-1:]])
            y_max = np.concatenate([y_max, current.y_max[-1:]])

        x_min = x_max = None
        if current.x_min is not None:
            x_min = np.minimum(current.x_min[0:paired_len:2], current.x_min[1:paired_len:2])
            x_max = np.maximum(current.x_max[0:paired_len:2], current.x_max[1:paired_len:2])
            if has_remainder:
                x_min = np.concatenate([x_min, current.x_min[-1:]])
                x_max = np.concatenate([x_max, current.x_max[-1:]])

        missing = None
        if current.missing is not None:
            missing = np.bitwise_or(current.missing[0:paired_len:2], current.missing[1:paired_len:2])
            if has_remainder:
                missing = np.concatenate([missing, current.missing[-1:]])

        current = LevelArrays(y_min=y_min, y_max=y_max, x_min=x_min, x_max=x_max, missing=missing)
        levels.append(current)

    return levels
