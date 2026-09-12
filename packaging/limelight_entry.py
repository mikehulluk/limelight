"""Entry point for the PyInstaller bundle.

This runs after every PyInstaller runtime hook and before any Limelight module is
imported, which is the only place that can correct the matplotlib cache
directory (see :func:`_use_persistent_matplotlib_cache`). Everything else is
delegated straight to the normal GUI entry point.
"""

from __future__ import annotations

import os
from pathlib import Path


def _matplotlib_cache_directory() -> Path:
    """Pick a per-user cache directory, matching largeseries.cachedir."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).expanduser().resolve() / "Limelight" / "Cache" / "matplotlib"

    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        return Path(cache_home).expanduser().resolve() / "limelight" / "matplotlib"

    return Path.home() / ".cache" / "limelight" / "matplotlib"


def _use_persistent_matplotlib_cache() -> None:
    """Stop matplotlib rebuilding its font list on every launch.

    PyInstaller's matplotlib runtime hook points MPLCONFIGDIR at a brand new
    temporary directory each time the app starts. That is the right call for a
    one-file build, whose extraction path changes every run, but this is a
    one-directory build living at a stable path. Left alone it costs roughly
    2.8 seconds of font scanning on *every* start, which is most of the app's
    startup time.
    """

    try:
        cache_directory = _matplotlib_cache_directory()
        cache_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # No writable cache: keep PyInstaller's temporary directory. Slow, but
        # a slow start beats failing to start.
        return

    os.environ["MPLCONFIGDIR"] = str(cache_directory)


_use_persistent_matplotlib_cache()

from limelight.gui_cli import main  # noqa: E402  (must follow the cache fix above)

raise SystemExit(main())
