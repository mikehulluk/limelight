class LargeSeriesError(Exception):
    """Base error for the limelight.largeseries subpackage."""


class InvalidSourceSpecError(LargeSeriesError):
    """Raised when a SourceSpec is internally inconsistent."""


class NonMonotonicXError(InvalidSourceSpecError):
    """Raised when an irregular x array is not sorted, which the cache relies on."""


class CacheCorruptError(LargeSeriesError):
    """Raised when an on-disk cache file fails its sanity check."""


class UnsupportedQueryError(LargeSeriesError):
    """Raised when a query cannot be answered (e.g. invalid range)."""
