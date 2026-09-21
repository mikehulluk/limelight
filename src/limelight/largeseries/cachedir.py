from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from filelock import FileLock

CACHE_DIR_ENV_VAR = "LIMELIGHT_CACHE_DIR"


def resolve_cache_dir(cache_dir: str | Path | None = None) -> Path:
    """Resolve (and create) the limelight.largeseries cache directory."""
    if cache_dir is not None:
        path = Path(cache_dir).expanduser().resolve()
    else:
        env_path = os.environ.get(CACHE_DIR_ENV_VAR)
        if env_path:
            path = Path(env_path).expanduser().resolve()
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                path = Path(local_app_data).expanduser().resolve() / "Limelight" / "Cache"
            else:
                cache_home = os.environ.get("XDG_CACHE_HOME")
                if cache_home:
                    path = Path(cache_home).expanduser().resolve() / "limelight"
                else:
                    path = Path.home() / ".cache" / "limelight"

    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_lock(cache_dir: Path, name: str) -> "FileLock":
    """A lock other processes honour, for one thing in the cache directory.

    The cache is shared by every Limelight on the machine - two windows on
    one package, the app and `LL` at once - and each of its files is made
    by reading it, working, and writing it back, so a writer takes the lock
    that names what it is writing and a second process waits for it rather
    than doing the same work over or losing the first's. The lock is a file
    beside the cache, taken with `fcntl` or `msvcrt` as the platform has.
    """

    from filelock import FileLock

    locks = cache_dir / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    return FileLock(locks / f"{name}.lock")
