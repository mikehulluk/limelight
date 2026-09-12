from __future__ import annotations

import os
from pathlib import Path

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
