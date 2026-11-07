"""HDF5 files a package names by URL rather than holding, and fetching them.

A large hdf source need not travel inside its package: the manifest can give
the file's URL, its SHA-256 and its size in bytes instead. Nothing is fetched
without the reader's say-so - the app asks before any request to a host the reader
has not Allowed, and never contacts one they Blocked (see
``settings.host_decision``) - and a download is only kept if it is exactly
the bytes the manifest describes.

A host may want a sign-in (``remote_auth``): `download` then raises a typed
outcome - `NeedsSignIn`, `NoAccess`, `Changed` or `RetryAfter` - read from
the HTTP status before anything else, so the caller can act on it. A token
goes only to its own resource: it is sent as an unredirected header, put back
only on a redirect to the same origin, and a redirect to http is refused.

Downloads are stored by digest in the reader's cache folder, so a file shared
by several packages, or by several versions of one, is fetched once, and a
package opened a second time needs no network. A file in that folder is
trusted to be what its name says: it was checked when it was written.
Nothing here touches Qt: the window drives it.
"""

from __future__ import annotations

import email.utils
import hashlib
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import remote_auth
from .reader import LimelightError
from .remote_auth import Challenge, Token, TokenStore, ascii_host, origin, parse_bearer_challenge, resource_covers

DOWNLOAD_DIR_ENV = "LIMELIGHT_DOWNLOAD_DIR"
USER_AGENT = "limelight-app data download"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHUNK_BYTES = 1 << 20
_TIMEOUT_SECONDS = 30
MAX_TRIES = 3
MAX_RETRY_AFTER_SECONDS = 120


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


class NeedsSignIn(RemoteFileError):
    """The file's own origin answered 401 (or 403 insufficient_scope) with a Bearer challenge."""

    def __init__(self, url: str, challenge: Challenge, *, token_sent: bool, insufficient_scope: bool = False) -> None:
        host = ascii_host(urlsplit(url).hostname)
        if insufficient_scope:
            text = f"{host} needs a sign-in with more access to give you {url}"
        else:
            text = f"{host} needs you to sign in to give you {url}"
        super().__init__(text)
        self.url = url
        self.host = host
        self.challenge = challenge
        self.token_sent = token_sent
        self.insufficient_scope = insufficient_scope


class NoAccess(RemoteFileError):
    """403 or 404: this account (or no account) cannot read the file."""

    def __init__(self, url: str, status: int, account: str | None, signed_in: bool) -> None:
        host = ascii_host(urlsplit(url).hostname)
        if signed_in:
            who = f" {account}" if account else ""
            text = f"Your account{who} cannot read this data on {host} ({url}, HTTP {status})"
        else:
            text = f"{host} does not let you read this data ({url}, HTTP {status})"
        super().__init__(text)
        self.url = url
        self.host = host
        self.status = status


class Changed(RemoteFileError):
    """409: the data at the URL is no longer what the package was made from."""

    def __init__(self, url: str) -> None:
        host = ascii_host(urlsplit(url).hostname)
        super().__init__(f"This data has changed on {host} since the package was made: get a new package ({url})")
        self.url = url
        self.host = host


class RetryAfter(RemoteFileError):
    """429 or 503: the host is busy; `seconds` is its Retry-After, if it gave one."""

    def __init__(self, url: str, status: int, seconds: float | None) -> None:
        host = ascii_host(urlsplit(url).hostname)
        super().__init__(f"{host} is busy (HTTP {status}); try again later ({url})")
        self.url = url
        self.host = host
        self.status = status
        self.seconds = seconds


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Retry-After as seconds from now: delay-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(int(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


@dataclass(frozen=True)
class RemoteFile:
    url: str
    sha256: str
    size_bytes: int

    @property
    def host(self) -> str:
        """The exact host, as punycode for an IDN host."""
        return ascii_host(urlsplit(self.url).hostname)

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


def delete_downloaded_data() -> int:
    """Empty the download folder of what Limelight put there; how many files went."""
    folder = download_dir()
    removed = 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    for entry in entries:
        name = entry.name
        if entry.is_file() and (
            (name.endswith(".h5") and _SHA256.fullmatch(name[:-3])) or (name.endswith(".part") and _SHA256.match(name))
        ):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def normalize_host(text: str) -> str | None:
    """A host from text: a bare domain, or a URL to take it from; IDN as punycode."""
    text = text.strip()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    text = ascii_host(text)
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


class _TokenRedirects(urllib.request.HTTPRedirectHandler):
    """Follows https redirects only; puts the token back only on the token's own origin.

    `add_unredirected_header` already keeps the token off every redirect;
    this handler adds it again where it belongs, so a same-origin redirect
    keeps working and a store's presigned URL elsewhere gets no token.
    """

    def __init__(self, token: Token | None) -> None:
        super().__init__()
        self.token = token

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if urlsplit(newurl).scheme.lower() != "https":
            raise RemoteFileError(f"{req.full_url} redirected to {newurl}, which is not https; Limelight does not follow that")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and self.token is not None and self.token.access_token:
            if resource_covers(self.token.resource, newurl):
                new.add_unredirected_header("Authorization", f"Bearer {self.token.access_token}")
        return new


def _outcome(url: str, error: urllib.error.HTTPError, token: Token | None) -> RemoteFileError:
    """What an HTTP error answer means, read before it becomes a RemoteFileError."""
    status = error.code
    headers = error.headers
    failed_url = error.geturl() or url
    error.close()
    redirected = failed_url != url
    challenge = parse_bearer_challenge(headers.get_all("WWW-Authenticate") or []) if headers is not None else None
    signed_in = token is not None
    account = token.account if token is not None else None
    if status == 401:
        if redirected:
            # Never a sign-in from a redirect target (§5 rule 5).
            return RemoteFileError(
                f"{url} sent Limelight on to {ascii_host(urlsplit(failed_url).hostname)}, which refused it (HTTP 401); "
                "Limelight does not sign in to a site it was redirected to"
            )
        if challenge is None:
            return RemoteFileError(f"{ascii_host(urlsplit(url).hostname)} asks for a sign-in Limelight cannot do ({url})")
        return NeedsSignIn(url, challenge, token_sent=signed_in)
    if status == 403:
        if not redirected and challenge is not None and challenge.error == "insufficient_scope":
            return NeedsSignIn(url, challenge, token_sent=signed_in, insufficient_scope=True)
        return NoAccess(url, status, account, signed_in)
    if status == 404:
        return NoAccess(url, status, account, signed_in)
    if status == 409:
        return Changed(url)
    if status in (429, 503):
        return RetryAfter(url, status, parse_retry_after(headers.get("Retry-After") if headers is not None else None))
    return RemoteFileError(f"Could not download {url}: HTTP {status}")


def download(
    remote: RemoteFile,
    *,
    token: Token | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    opener: Callable[..., Any] | None = None,
    https_handler: urllib.request.HTTPSHandler | None = None,
) -> Path:
    """Fetch `remote` into the download folder and return where it is.

    The file is written beside its final name and moved there only once its
    size and digest match the manifest, so an interrupted or wrong download
    never looks like a good one. `progress` is called as (bytes_done, total);
    `cancelled` is polled between chunks. `token` is sent only if its
    resource covers the URL. An error answer raises one of the typed outcomes.
    """
    cached = remote.cached_path()
    if cached is not None:
        return cached
    if not remote_auth.is_https(remote.url):
        raise RemoteFileError(f"{remote.url} is not an https URL")
    if token is not None and not (token.access_token and resource_covers(token.resource, remote.url)):
        token = None

    target = remote.cache_path
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(remote.url, headers={"User-Agent": USER_AGENT})
    if token is not None:
        request.add_unredirected_header("Authorization", f"Bearer {token.access_token}")
    if opener is None:
        opener = remote_auth.build_opener(https_handler, _TokenRedirects(token)).open
    digest = hashlib.sha256()
    done = 0
    handle, partial_name = tempfile.mkstemp(dir=target.parent, prefix=f"{remote.sha256}.", suffix=".part")
    partial = Path(partial_name)
    try:
        with os.fdopen(handle, "wb") as output:
            try:
                response = opener(request, timeout=_TIMEOUT_SECONDS)
            except urllib.error.HTTPError as error:
                raise _outcome(remote.url, error, token) from None
            except OSError as error:
                raise RemoteFileError(f"Could not download {remote.url}: {_reason(error)}") from None
            with response:
                while True:
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled(f"The download of {remote.url} was cancelled")
                    try:
                        chunk = response.read(_CHUNK_BYTES)
                    except OSError as error:
                        raise RemoteFileError(f"Could not download {remote.url}: {_reason(error)}") from None
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


def _reason(error: OSError) -> str:
    reason = getattr(error, "reason", None)
    return str(reason if reason is not None else error)


def fetch(
    remote: RemoteFile,
    *,
    tokens: TokenStore | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_wait: Callable[[float, int], None] | None = None,
    https_handler: urllib.request.HTTPSHandler | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """`download` with this resource's token, a renewal on invalid_token, and Retry-After waits.

    A 429 or 503 with Retry-After is waited out (`on_wait(seconds, try)`), at
    most `MAX_RETRY_AFTER_SECONDS` each and `MAX_TRIES` tries in all. A
    `NeedsSignIn` that a renewed token does not answer is raised for the
    caller, which may sign in and call again.
    """
    cached = remote.cached_path()
    if cached is not None:
        return cached
    store = tokens if tokens is not None else remote_auth.default_store()
    token = store.ready(store.token_for(remote.url), https_handler=https_handler)
    renewed = False
    attempt = 0
    while True:
        attempt += 1
        try:
            return download(
                remote, token=token, progress=progress, cancelled=cancelled, https_handler=https_handler
            )
        except NeedsSignIn as need:
            if (
                not renewed
                and not need.insufficient_scope
                and token is not None
                and token.refresh_token
                and need.challenge.error == "invalid_token"
            ):
                renewed = True
                token.access_token = None
                token = store.ready(token, https_handler=https_handler)
                if token is not None and attempt < MAX_TRIES:
                    continue
            raise
        except RetryAfter as busy:
            if busy.seconds is None or busy.seconds > MAX_RETRY_AFTER_SECONDS or attempt >= MAX_TRIES:
                raise
            if on_wait is not None:
                on_wait(busy.seconds, attempt)
            waited = 0.0
            while waited < busy.seconds:
                if cancelled is not None and cancelled():
                    raise DownloadCancelled(f"The download of {remote.url} was cancelled") from None
                step = min(0.25, busy.seconds - waited)
                sleep(step)
                waited += step
