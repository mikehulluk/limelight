"""HDF5 files a package names by URL rather than holding, and fetching them.

A large hdf source need not travel inside its package: the manifest can give
the file's URL, its SHA-256 and its size in bytes instead. Nothing is fetched
without the reader's say-so - the app asks before each download, unless the
URL's host is one the reader has said always to allow (see
``settings.trusted_download_hosts``) - and a download is only kept if it is
exactly the bytes the manifest describes.

Downloads are stored by digest in the reader's cache folder, so a file shared
by several packages, or by several versions of one, is fetched once, and a
package opened a second time needs no network. A file in that folder is
trusted to be what its name says: it was checked when it was written.
Nothing here touches Qt: the window drives it.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .reader import LimelightError

DOWNLOAD_DIR_ENV = "LIMELIGHT_DOWNLOAD_DIR"
USER_AGENT = "limelight-app data download"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHUNK_BYTES = 1 << 20
_TIMEOUT_SECONDS = 30


class RemoteFileError(LimelightError):
    """A remote file could not be fetched, or was not what the manifest says."""


class RemoteFileNotDownloaded(LimelightError):
    """A remote file is needed but has not been downloaded (or was declined)."""

    def __init__(self, remote: "RemoteFile", source_id: str) -> None:
        super().__init__(
            f"The data for source {source_id!r} is at {remote.url} "
            f"({format_size(remote.size_bytes)}), which has not been downloaded"
        )
        self.remote = remote
        self.source_id = source_id


class DownloadCancelled(RemoteFileError):
    pass


@dataclass(frozen=True)
class RemoteFile:
    url: str
    sha256: str
    size_bytes: int

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()

    @property
    def cache_path(self) -> Path:
        return download_dir() / f"{self.sha256}.h5"

    def cached_path(self) -> Path | None:
        """The downloaded file, if it is here; a file of the wrong size is not."""
        path = self.cache_path
        try:
            if path.is_file() and path.stat().st_size == self.size_bytes:
                return path
        except OSError:
            pass
        return None


def remote_file_of(source: dict[str, Any]) -> RemoteFile | None:
    """The remote file an hdf source names, or None for a bundled one."""
    remote = source.get("remote")
    if not remote:
        return None
    return RemoteFile(url=remote["url"], sha256=remote["sha256"], size_bytes=int(remote["sizeBytes"]))


def validate_remote(remote: dict[str, Any], context: str) -> None:
    """Raise LimelightError for a remote file entry the app could not honour."""
    parts = urlsplit(remote["url"])
    if parts.scheme != "https" or not parts.hostname:
        raise LimelightError(f"{context}: remote url {remote['url']!r} must be an https:// URL with a host")
    if parts.username or parts.password:
        raise LimelightError(f"{context}: remote url must not carry a user name or password")
    if not _SHA256.fullmatch(remote["sha256"]):
        raise LimelightError(f"{context}: remote sha256 must be 64 lower-case hexadecimal characters")
    if int(remote["sizeBytes"]) <= 0:
        raise LimelightError(f"{context}: remote sizeBytes must be more than zero")


def download_dir() -> Path:
    override = os.environ.get(DOWNLOAD_DIR_ENV)
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "limelight" / "downloads"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "limelight" / "downloads"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "limelight" / "downloads"


def host_is_trusted(host: str, trusted_hosts: list[str]) -> bool:
    """Whether `host` is one of `trusted_hosts`, or a subdomain of one."""
    host = host.lower().rstrip(".")
    for trusted in trusted_hosts:
        trusted = trusted.lower().strip().rstrip(".")
        if trusted and (host == trusted or host.endswith("." + trusted)):
            return True
    return False


def normalize_host(text: str) -> str | None:
    """A host from what a reader typed: a bare domain, or a URL to take it from."""
    text = text.strip()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    text = text.lower().rstrip(".")
    if not text or not re.fullmatch(r"[a-z0-9.-]+", text) or ".." in text or text.startswith((".", "-")):
        return None
    return text


def format_size(size_bytes: int) -> str:
    if size_bytes < 1000:
        return f"{size_bytes} bytes"
    size = float(size_bytes)
    for unit in ("KB", "MB", "GB"):
        size /= 1000
        if size < 1000:
            break
    return f"{size:.1f} {unit}"


def download(
    remote: RemoteFile,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    opener: Callable[..., Any] | None = None,
) -> Path:
    """Fetch `remote` into the download folder and return where it is.

    The file is written beside its final name and moved there only once its
    size and digest match the manifest, so an interrupted or wrong download
    never looks like a good one. `progress` is called as (bytes_done, total);
    `cancelled` is polled between chunks.
    """
    cached = remote.cached_path()
    if cached is not None:
        return cached

    target = remote.cache_path
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(remote.url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    done = 0
    handle, partial_name = tempfile.mkstemp(dir=target.parent, prefix=f"{remote.sha256}.", suffix=".part")
    partial = Path(partial_name)
    try:
        with os.fdopen(handle, "wb") as output:
            try:
                response = (opener or urllib.request.urlopen)(request, timeout=_TIMEOUT_SECONDS)
            except OSError as error:
                raise RemoteFileError(f"Could not download {remote.url}: {error}") from error
            with response:
                while True:
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled(f"The download of {remote.url} was cancelled")
                    try:
                        chunk = response.read(_CHUNK_BYTES)
                    except OSError as error:
                        raise RemoteFileError(f"Could not download {remote.url}: {error}") from error
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > remote.size_bytes:
                        raise RemoteFileError(
                            f"{remote.url} is larger than the {remote.size_bytes} bytes the package says it is"
                        )
                    digest.update(chunk)
                    output.write(chunk)
                    if progress is not None:
                        progress(done, remote.size_bytes)
        if done != remote.size_bytes:
            raise RemoteFileError(f"{remote.url} is {done} bytes, not the {remote.size_bytes} the package says")
        if digest.hexdigest() != remote.sha256:
            raise RemoteFileError(f"{remote.url} does not have the SHA-256 the package gives for it")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target
