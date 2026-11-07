from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "limelight-setting.json"
# What the app itself writes, kept apart from limelight-setting.json, which
# the reader writes by hand and may keep in a working folder.
APP_SETTINGS_FILENAME = "app-settings.json"
TRUSTED_DOWNLOAD_HOSTS_KEY = "trustedDownloadHosts"


def config_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "limelight" if appdata else Path.home() / "AppData" / "Roaming" / "limelight"
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg_config_home) if xdg_config_home else Path.home() / ".config") / "limelight"


def _settings_search_paths() -> list[Path]:
    return [Path.cwd() / SETTINGS_FILENAME, config_dir() / SETTINGS_FILENAME, Path.home() / SETTINGS_FILENAME]


def load_settings() -> dict[str, Any]:
    for path in _settings_search_paths():
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _app_settings_path() -> Path:
    return config_dir() / APP_SETTINGS_FILENAME


def load_app_settings() -> dict[str, Any]:
    path = _app_settings_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_app_settings(settings: dict[str, Any]) -> None:
    path = _app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def trusted_download_hosts() -> list[str]:
    """The hosts the reader has said always to download data from.

    A host here covers its subdomains too. The list is the app's to write
    (Settings in the app), and a hand-written limelight-setting.json can add
    to it under the same key.
    """
    hosts: list[str] = []
    for settings in (load_app_settings(), load_settings()):
        value = settings.get(TRUSTED_DOWNLOAD_HOSTS_KEY) or []
        if isinstance(value, list):
            hosts.extend(str(host) for host in value if str(host) not in hosts)
    return hosts


def set_trusted_download_hosts(hosts: list[str]) -> None:
    """Replace the app's own list (a hand-written settings file is left alone)."""
    settings = load_app_settings()
    settings[TRUSTED_DOWNLOAD_HOSTS_KEY] = sorted(dict.fromkeys(hosts))
    save_app_settings(settings)


def add_trusted_download_host(host: str) -> None:
    settings = load_app_settings()
    hosts = settings.get(TRUSTED_DOWNLOAD_HOSTS_KEY) or []
    if host not in hosts:
        set_trusted_download_hosts([*hosts, host])
