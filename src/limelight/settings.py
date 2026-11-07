from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "limelight-setting.json"
# What the app itself writes, kept apart from limelight-setting.json, which
# the reader writes by hand and may keep in a working folder.
APP_SETTINGS_FILENAME = "app-settings.json"
# Before RSI: hosts always downloaded from, each covering its subdomains.
# Read only to migrate it (host_decisions).
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


ALLOWED = "allowed"
BLOCKED = "blocked"
# {"allowed": [host, ...], "blocked": [host, ...]}: exact hosts, never their subdomains.
DOWNLOAD_HOSTS_KEY = "downloadHosts"


def _exact_host(host: str) -> str | None:
    from .remote_files import normalize_host

    return normalize_host(host)


def _host_lists(settings: dict[str, Any]) -> tuple[list[str], list[str]]:
    decisions = settings.get(DOWNLOAD_HOSTS_KEY)
    decisions = decisions if isinstance(decisions, dict) else {}
    lists = []
    for key in (ALLOWED, BLOCKED):
        value = decisions.get(key) or []
        hosts = [] if not isinstance(value, list) else [h for h in (_exact_host(str(item)) for item in value) if h]
        lists.append(sorted(dict.fromkeys(hosts)))
    return lists[0], lists[1]


def _migrated_app_settings() -> dict[str, Any]:
    """The app's settings, with an old trustedDownloadHosts list turned into Allowed exact hosts.

    Before RSI a trusted host covered its subdomains; now each entry is
    Allowed for itself only. Only app-settings.json is migrated: a
    hand-written limelight-setting.json no longer decides anything here.
    """
    settings = load_app_settings()
    if TRUSTED_DOWNLOAD_HOSTS_KEY not in settings:
        return settings
    old = settings.pop(TRUSTED_DOWNLOAD_HOSTS_KEY) or []
    allowed, blocked = _host_lists(settings)
    if isinstance(old, list):
        allowed = sorted({*allowed, *(h for h in (_exact_host(str(item)) for item in old) if h)})
    settings[DOWNLOAD_HOSTS_KEY] = {ALLOWED: allowed, BLOCKED: blocked}
    save_app_settings(settings)
    return settings


def host_decisions() -> dict[str, str]:
    """Every remembered host and its decision; Blocked wins over Allowed.

    Read only from the user's config folder (RV-7): a settings file in the
    folder Limelight runs in can never allow a host.
    """
    allowed, blocked = _host_lists(_migrated_app_settings())
    decisions = {host: ALLOWED for host in allowed}
    decisions.update({host: BLOCKED for host in blocked})
    return dict(sorted(decisions.items()))


def host_decision(host: str) -> str | None:
    """ALLOWED, BLOCKED or None for the URL's exact host."""
    exact = _exact_host(host)
    return host_decisions().get(exact) if exact else None


def set_host_decision(host: str, decision: str | None) -> None:
    """Remember ALLOWED or BLOCKED for exactly `host`; None forgets it."""
    if decision not in (ALLOWED, BLOCKED, None):
        raise ValueError(f"unknown host decision {decision!r}")
    exact = _exact_host(host)
    if not exact:
        raise ValueError(f"{host!r} is not a host name")
    settings = _migrated_app_settings()
    allowed, blocked = _host_lists(settings)
    allowed = [item for item in allowed if item != exact]
    blocked = [item for item in blocked if item != exact]
    if decision == ALLOWED:
        allowed.append(exact)
    elif decision == BLOCKED:
        blocked.append(exact)
    settings[DOWNLOAD_HOSTS_KEY] = {ALLOWED: sorted(allowed), BLOCKED: sorted(blocked)}
    save_app_settings(settings)
