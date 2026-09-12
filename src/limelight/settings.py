from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "limelight-setting.json"


def _settings_search_paths() -> list[Path]:
    paths = [Path.cwd() / SETTINGS_FILENAME]

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "limelight" / SETTINGS_FILENAME)
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        config_dir = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
        paths.append(config_dir / "limelight" / SETTINGS_FILENAME)

    paths.append(Path.home() / SETTINGS_FILENAME)
    return paths


def load_settings() -> dict[str, Any]:
    for path in _settings_search_paths():
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}
