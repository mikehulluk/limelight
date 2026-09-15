"""Checking for, and applying, a newer Limelight.

The newest release is whatever GitHub lists as the latest; how to move to it
depends on how this copy was installed, which the app can tell:

* a PyInstaller bundle (``sys.frozen``) came from one of the installers, and
  on Windows the Inno Setup installer can be run silently over the top;
* otherwise this is a Python package, upgraded with ``uv tool upgrade`` or
  ``pip install --upgrade`` depending on which put it here.

On macOS and Linux the bundles have no in-place path, so the app can only
point at the download. Nothing here touches Qt: the window drives it.
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as installed_version
from pathlib import Path
from typing import Callable

from packaging.version import InvalidVersion, Version

DISTRIBUTION = "limelight-app"
REPOSITORY = "mikehulluk/limelight"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases"
CHECKSUMS_ASSET = "SHA256SUMS"
USER_AGENT = "limelight-app update check"


class InstallKind(enum.Enum):
    WINDOWS_INSTALLER = "windows-installer"
    MACOS_BUNDLE = "macos-bundle"
    LINUX_APPIMAGE = "linux-appimage"
    LINUX_PACKAGE = "linux-package"
    PYTHON_UV_TOOL = "uv-tool"
    PYTHON_PACKAGE = "python-package"

    @property
    def updates_in_place(self) -> bool:
        """Whether the app itself can carry out the update."""
        return self in (InstallKind.WINDOWS_INSTALLER, InstallKind.PYTHON_UV_TOOL, InstallKind.PYTHON_PACKAGE)


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    page_url: str
    notes: str
    assets: dict[str, str] = field(default_factory=dict)  # file name -> download URL


class UpdateError(Exception):
    """Raised when a release could not be fetched or applied."""


def current_version() -> str:
    try:
        return installed_version(DISTRIBUTION)
    except PackageNotFoundError:
        return "0"


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later version than `current`.

    A development build (0.0.8.dev3) counts as older than the release it is
    heading for, so a checkout is offered the release it does not yet have.
    An unparsable version is never newer.
    """
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return False


def install_kind() -> InstallKind:
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            return InstallKind.WINDOWS_INSTALLER
        if sys.platform == "darwin":
            return InstallKind.MACOS_BUNDLE
        if os.environ.get("APPIMAGE"):
            return InstallKind.LINUX_APPIMAGE
        return InstallKind.LINUX_PACKAGE
    # uv writes a receipt at the root of every environment it manages as a
    # tool; a plain venv, whoever made it, has none.
    if (Path(sys.prefix) / "uv-receipt.toml").is_file():
        return InstallKind.PYTHON_UV_TOOL
    return InstallKind.PYTHON_PACKAGE


def fetch_latest_release(timeout: float = 10.0) -> Release:
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except OSError as error:
        raise UpdateError(f"Could not reach GitHub: {error}") from error
    return release_from_payload(payload)


def release_from_payload(payload: dict) -> Release:
    tag = str(payload.get("tag_name") or "")
    version = tag[1:] if tag.startswith("v") else tag
    if not version:
        raise UpdateError("The latest release has no tag")
    assets = {
        str(asset["name"]): str(asset["browser_download_url"])
        for asset in payload.get("assets") or []
        if asset.get("name") and asset.get("browser_download_url")
    }
    return Release(
        version=version,
        tag=tag,
        page_url=str(payload.get("html_url") or RELEASES_PAGE_URL),
        notes=str(payload.get("body") or ""),
        assets=assets,
    )


def installer_asset(release: Release, kind: InstallKind) -> tuple[str, str] | None:
    """The (name, URL) of the release asset that installs on this machine."""
    if kind is InstallKind.WINDOWS_INSTALLER:
        wanted = "-windows-x64.exe"
    elif kind is InstallKind.MACOS_BUNDLE:
        wanted = ".dmg"
    elif kind is InstallKind.LINUX_APPIMAGE:
        wanted = ".AppImage"
    elif kind is InstallKind.LINUX_PACKAGE:
        wanted = ".deb"
    else:
        return None
    for name, url in release.assets.items():
        if name.endswith(wanted):
            return name, url
    return None


def python_upgrade_command(kind: InstallKind, version: str | None = None) -> list[str]:
    """The command that upgrades a Python-package install of Limelight.

    With a `version`, that exact release; without, whatever is newest.
    """
    if kind is InstallKind.PYTHON_UV_TOOL:
        return ["uv", "tool", "install", "--force", f"{DISTRIBUTION}=={version}"] if version else [
            "uv",
            "tool",
            "upgrade",
            DISTRIBUTION,
        ]
    spec = f"{DISTRIBUTION}=={version}" if version else DISTRIBUTION
    return [sys.executable, "-m", "pip", "install", "--upgrade", spec]


def download(
    url: str,
    destination: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 30.0,
) -> Path:
    """Fetch `url` to `destination`, reporting (bytes so far, total or -1)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or -1)
            done = 0
            while True:
                block = response.read(1 << 16)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if progress is not None:
                    progress(done, total)
    except OSError as error:
        raise UpdateError(f"Download failed: {error}") from error
    return destination


def expected_sha256(release: Release, asset_name: str, timeout: float = 10.0) -> str | None:
    """The asset's SHA-256 from the release's checksum file, if it publishes one."""
    url = release.assets.get(CHECKSUMS_ASSET)
    if url is None:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except OSError as error:
        raise UpdateError(f"Could not fetch {CHECKSUMS_ASSET}: {error}") from error
    return parse_sha256sums(text).get(asset_name)


def parse_sha256sums(text: str) -> dict[str, str]:
    """`sha256sum` output: a hex digest, whitespace, a name (a leading `*` is binary mode)."""
    digests: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            continue
        digests[name.lstrip("*").strip()] = digest.lower()
    return digests


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download_directory() -> Path:
    directory = Path(tempfile.gettempdir()) / "limelight-updates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def windows_installer_command(installer: Path, relaunch: Path | None) -> list[str]:
    """Run the Inno Setup installer silently, then start the installed app.

    Inno's own post-install launch is skipped in silent mode, so a shell does
    the relaunch once the installer returns. `/CLOSEAPPLICATIONS` has the
    installer close a running Limelight rather than fail on its files.
    """
    setup = f'"{installer}" /SILENT /CLOSEAPPLICATIONS /NORESTART /SUPPRESSMSGBOXES'
    if relaunch is not None:
        setup += f' && start "" "{relaunch}"'
    return ["cmd", "/c", setup]


def launch_windows_installer(installer: Path, relaunch: Path | None = None) -> None:
    creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(windows_installer_command(installer, relaunch), creationflags=creation_flags, close_fds=True)
    except OSError as error:
        raise UpdateError(f"Could not start the installer: {error}") from error


def relaunch_command() -> list[str]:
    """How to start this app again, as it was started."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def describe_install(kind: InstallKind) -> str:
    return {
        InstallKind.WINDOWS_INSTALLER: "the Windows installer",
        InstallKind.MACOS_BUNDLE: "the macOS disk image",
        InstallKind.LINUX_APPIMAGE: "an AppImage",
        InstallKind.LINUX_PACKAGE: "a Linux package",
        InstallKind.PYTHON_UV_TOOL: "uv (as a tool)",
        InstallKind.PYTHON_PACKAGE: f"pip into {platform.python_implementation()} {platform.python_version()}",
    }[kind]
