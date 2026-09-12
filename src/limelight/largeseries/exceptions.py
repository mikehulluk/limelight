class LargeSeriesError(Exception):
    """Base error for the limelight.largeseries subpackage."""


class InvalidSourceSpecError(LargeSeriesError):
    """Raised when a SourceSpec is internally inconsistent."""


class CacheCorruptError(LargeSeriesError):
    """Raised when an on-disk cache file fails its sanity check."""


class UnsupportedQueryError(LargeSeriesError):
    """Raised when a query cannot be answered (e.g. invalid range)."""
