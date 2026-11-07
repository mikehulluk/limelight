"""What each sample of a series is: a value to draw, one to step over, or a missing one, and why.

A series can come with a status array alongside its values - one integer code
per sample - and a table saying what each code means:

* ``valid``: the sample is its value. A NaN value is still a missing sample,
  of the plain "no value" kind.
* ``skip``: there is no sample here, and none was expected - a sensor that
  is read on every tenth tick has nine skipped ticks in ten. The line joins
  the samples either side, and nothing is marked.
* ``missing``: a sample was expected and there is none; the line breaks, and
  the gap is marked as this code's kind of missing.

Each kind of missing is a bit of a per-bucket mask, so the pyramid can say,
at every zoom, which kinds of missing a bucket holds. Bit 0 is a NaN under a
valid code (or in a series with no status at all), bit 1 a code the table
does not name, and the declared missing codes take the bits after that, in
the order they are declared.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

# Per-sample kind: these two, or the bit number of a kind of missing.
VALID = -1
SKIP = -2

NAN_KIND = 0
UNKNOWN_KIND = 1
FIRST_DECLARED_KIND = 2
MASK_DTYPE = np.uint32
MAX_KINDS = 32
MAX_DECLARED_MISSING = MAX_KINDS - FIRST_DECLARED_KIND

MEANINGS = ("valid", "skip", "missing")


@dataclass(frozen=True)
class StatusMap:
    """Status code -> VALID, SKIP or a missing kind's bit; any other code is UNKNOWN_KIND."""

    codes: tuple[int, ...]
    kinds: tuple[int, ...]

    @classmethod
    def from_meanings(cls, entries: list[tuple[int, str]]) -> "StatusMap":
        """From (code, meaning) pairs in declaration order; missing codes take bits in that order."""
        pairs: dict[int, int] = {}
        next_kind = FIRST_DECLARED_KIND
        for code, meaning in entries:
            if code in pairs:
                raise ValueError(f"status code {code} is declared twice")
            if meaning == "valid":
                pairs[code] = VALID
            elif meaning == "skip":
                pairs[code] = SKIP
            elif meaning == "missing":
                if next_kind >= MAX_KINDS:
                    raise ValueError(f"at most {MAX_DECLARED_MISSING} status codes can mean missing")
                pairs[code] = next_kind
                next_kind += 1
            else:
                raise ValueError(f"status meaning must be one of {', '.join(MEANINGS)}, not {meaning!r}")
        ordered = sorted(pairs)
        return cls(codes=tuple(ordered), kinds=tuple(pairs[code] for code in ordered))

    def classify(self, status: np.ndarray) -> np.ndarray:
        """Each code's kind (VALID, SKIP or a missing bit), as int8."""
        status = np.asarray(status)
        result = np.full(status.shape, UNKNOWN_KIND, dtype=np.int8)
        if not self.codes or status.size == 0:
            return result
        codes = np.asarray(self.codes, dtype=np.int64)
        kinds = np.asarray(self.kinds, dtype=np.int8)
        values = status.astype(np.int64, copy=False)
        positions = np.clip(np.searchsorted(codes, values), 0, len(codes) - 1)
        known = codes[positions] == values
        result[known] = kinds[positions[known]]
        return result

    def fingerprint(self) -> str:
        """Stable text naming this mapping, for cache identity."""
        text = ",".join(f"{code}:{kind}" for code, kind in zip(self.codes, self.kinds))
        return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


def sample_kinds(y: np.ndarray, status: np.ndarray | None, status_map: StatusMap | None) -> np.ndarray:
    """Each sample's kind: from its status code where there is one, and a NaN under VALID is NAN_KIND."""
    y = np.asarray(y)
    if status is None or status_map is None:
        kinds = np.full(y.shape, VALID, dtype=np.int8)
    else:
        kinds = status_map.classify(status)
    if y.dtype.kind == "f":
        kinds[(kinds == VALID) & np.isnan(y)] = NAN_KIND
    return kinds


def masked_values(y: np.ndarray, kinds: np.ndarray) -> np.ndarray:
    """The values as float64, NaN wherever a sample is not VALID: what min/max are taken over."""
    values = np.asarray(y).astype(np.float64, copy=True)
    values[kinds != VALID] = np.nan
    return values


def kind_bits(kinds: np.ndarray) -> np.ndarray:
    """Each sample's missing-kind bit (0 for valid and skipped samples), as the mask dtype."""
    bits = np.zeros(kinds.shape, dtype=MASK_DTYPE)
    missing = kinds >= 0
    bits[missing] = np.left_shift(MASK_DTYPE(1), kinds[missing].astype(MASK_DTYPE))
    return bits


def kinds_in_mask(mask: int) -> list[int]:
    return [kind for kind in range(MAX_KINDS) if mask >> kind & 1]
