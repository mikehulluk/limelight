"""Signing in to fetch remote data (RSI): the challenge, discovery, the device flow and the tokens.

A server that wants a token for a file answers ``401`` with
``WWW-Authenticate: Bearer resource_metadata="..."`` (RFC 6750, RFC 9728).
From there:

- the protected resource's metadata (RFC 9728) says what the resource is and
  who issues its tokens;
- the issuer's metadata (RFC 8414) gives its endpoints;
- the reader signs in with the device authorization grant (RFC 8628), asking
  for a token for that resource only (RFC 8707);
- signing out revokes the token (RFC 7009).

The rules are tight on purpose (``doc/design/remote-sign-in.md`` §5): https
only; metadata from the refused request's own origin; a ``resource`` that
covers the refused URL on path-segment boundaries; an authorization server,
and all its endpoints, on the resource's registrable domain, with no
override; and a token sent only to its own resource. Tokens are kept in the OS
keychain when the reader asks, otherwise in memory; never in a file, a log or
an exception's text. Nothing here touches Qt.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from .reader import LimelightError

logger = logging.getLogger("limelight.sign_in")

CLIENT_ID = "limelight"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
USER_AGENT = "limelight-app sign-in"
KEYRING_SERVICE = "limelight-app sign-in"
# The keychain has no way to list entries, so the resources signed in to are
# kept in one more entry (no secret in it), rather than in a file.
_KEYRING_INDEX_USER = "(resources)"
TOKEN_ENV = "LIMELIGHT_BEARER_TOKEN"
RESOURCE_ENV = "LIMELIGHT_BEARER_RESOURCE"

DEFAULT_INTERVAL_SECONDS = 5
SLOW_DOWN_STEP_SECONDS = 5
_TIMEOUT_SECONDS = 30
_MAX_JSON_BYTES = 1 << 20
_MAX_TOKEN_CHARS = 16_384
_MAX_WELL_KNOWN_TRIES = 8
_RESOURCE_METADATA_WELL_KNOWN = "/.well-known/oauth-protected-resource"
_AUTHORIZATION_SERVER_WELL_KNOWN = "/.well-known/oauth-authorization-server"
_OPENID_WELL_KNOWN = "/.well-known/openid-configuration"


class SignInError(LimelightError):
    """Signing in did not work; the text is for the reader."""


class SignInRefused(SignInError):
    """The site broke one of the §5 rules, so Limelight will not sign in to it."""


class SignInDenied(SignInError):
    pass


class SignInExpired(SignInError):
    pass


class SignInCancelled(SignInError):
    pass


class NoKeychain(SignInError):
    pass


# ---------------------------------------------------------------- hosts


def ascii_host(host: str | None) -> str:
    """A host as compared and stored: lower case, no trailing dot, IDN as punycode."""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        host.encode("ascii")
        return host
    except UnicodeEncodeError:
        pass
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def unicode_host(host: str) -> str | None:
    """The Unicode form of a punycode host, or None when it has none."""
    host = ascii_host(host)
    if "xn--" not in host:
        return None
    try:
        decoded = host.encode("ascii").decode("idna")
    except UnicodeError:
        return None
    return decoded if decoded != host else None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


@functools.lru_cache(maxsize=1)
def _public_suffix_list() -> Any:
    from publicsuffixlist import PublicSuffixList

    # The private section too (github.io, ...): two of its sites are not one domain.
    return PublicSuffixList()


def registrable_domain(host: str) -> str:
    """The part of `host` that one party registered (super-easy-sensors.com).

    An IP address, a bare public suffix or a single-label name is its own
    registrable domain, so it matches only itself.
    """
    host = ascii_host(host)
    if not host or _is_ip(host):
        return host
    return _public_suffix_list().privatesuffix(host) or host


def same_registrable_domain(host_a: str, host_b: str) -> bool:
    a, b = registrable_domain(host_a), registrable_domain(host_b)
    return bool(a) and a == b


def origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, ascii_host(parts.hostname), port or {"https": 443, "http": 80}.get(scheme, 0)


def is_https(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme.lower() == "https" and bool(parts.hostname) and not (parts.username or parts.password)


def _has_dot_segment(path: str) -> bool:
    return any(urllib.parse.unquote(segment) in (".", "..") for segment in path.split("/"))


def resource_covers(resource: str, url: str) -> bool:
    """Whether a token for `resource` may go to `url` (RV-2: a prefix on path-segment boundaries).

    ``https://h/api/v1`` covers ``https://h/api/v1/files/x`` but not
    ``https://h/api/v1-evil``; the origin must be the same, and neither may
    play tricks with ``.`` or ``..`` segments.
    """
    if not (is_https(resource) and is_https(url)):
        return False
    resource_parts, url_parts = urlsplit(resource), urlsplit(url)
    if resource_parts.query or resource_parts.fragment:
        return False
    if origin(resource) != origin(url):
        return False
    if _has_dot_segment(resource_parts.path) or _has_dot_segment(url_parts.path):
        return False
    prefix = resource_parts.path.rstrip("/")
    path = url_parts.path or "/"
    return path == prefix or path.startswith(prefix + "/")


def mask_token(token: str | None) -> str:
    """How a token may appear in a log: ``Bearer …abcd``."""
    if not token:
        return "Bearer (none)"
    return "Bearer …" + (token[-4:] if len(token) >= 12 else "")


# ---------------------------------------------------------------- the challenge

_TOKEN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_SCHEME = re.compile(rf"[\s,]*({_TOKEN})(?=\s|,|$)")
_PARAM = re.compile(rf"\s*({_TOKEN})\s*=\s*(?:\"((?:[^\"\\]|\\.)*)\"|({_TOKEN}))\s*")
_TOKEN68 = re.compile(r"\s*[A-Za-z0-9\-._~+/]+=*\s*(?=,|$)")


@dataclass(frozen=True)
class Challenge:
    """A ``WWW-Authenticate: Bearer`` challenge's parameters, names lower-cased."""

    params: dict[str, str] = field(default_factory=dict)

    @property
    def error(self) -> str | None:
        return self.params.get("error")

    @property
    def scope(self) -> str | None:
        return self.params.get("scope")

    @property
    def resource_metadata(self) -> str | None:
        return self.params.get("resource_metadata")


def parse_challenges(header: str) -> list[tuple[str, dict[str, str]] | None]:
    """Every challenge in one header value, as (scheme, params); None for a malformed one."""
    challenges: list[tuple[str, dict[str, str]] | None] = []
    index = 0
    while index < len(header):
        scheme_match = _SCHEME.match(header, index)
        if scheme_match is None:
            break
        scheme = scheme_match.group(1)
        index = scheme_match.end()
        params: dict[str, str] = {}
        malformed = False
        if not _PARAM.match(header, index):
            token68 = _TOKEN68.match(header, index)
            if token68 is not None:
                index = token68.end()
        while True:
            param = _PARAM.match(header, index)
            if param is None:
                break
            name = param.group(1).lower()
            value = re.sub(r"\\(.)", r"\1", param.group(2)) if param.group(2) is not None else param.group(3)
            # RFC 6750 §3: each attribute at most once.
            malformed = malformed or name in params
            params[name] = value
            index = param.end()
            if index < len(header) and header[index] == ",":
                if _PARAM.match(header, index + 1):
                    index += 1
                    continue
                index += 1
            break
        challenges.append(None if malformed else (scheme, params))
        if index >= len(header):
            break
        if header[index:].strip(" ,\t") and not _SCHEME.match(header, index):
            break
    return challenges


def parse_bearer_challenge(values: Iterable[str]) -> Challenge | None:
    """The first well-formed Bearer challenge in the WWW-Authenticate header values."""
    for value in values:
        for challenge in parse_challenges(value or ""):
            if challenge is not None and challenge[0].lower() == "bearer":
                return Challenge(params=challenge[1])
    return None


# ---------------------------------------------------------------- transport


def _default_https_handler() -> urllib.request.HTTPSHandler:
    return urllib.request.HTTPSHandler(context=ssl.create_default_context())


# Tests point this at a local server with its own CA; everything here and in
# remote_files builds its opener from it unless given a handler.
https_handler_factory: Callable[[], urllib.request.HTTPSHandler] = _default_https_handler


class _NoPlainHttp(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> Any:
        raise urllib.error.URLError(f"refused {req.full_url}: only https is allowed")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Metadata and token endpoints are fetched where they are named, or not at all."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, f"redirected to {newurl}; not followed", headers, fp)


def build_opener(
    https_handler: urllib.request.HTTPSHandler | None, redirect_handler: urllib.request.BaseHandler
) -> urllib.request.OpenerDirector:
    """An opener that speaks https only, with the redirect rules given."""
    return urllib.request.build_opener(https_handler or https_handler_factory(), _NoPlainHttp(), redirect_handler)


def _read_json(response: Any, url: str) -> dict[str, Any]:
    body = response.read(_MAX_JSON_BYTES + 1)
    if len(body) > _MAX_JSON_BYTES:
        raise SignInError(f"{url} answered with more than {_MAX_JSON_BYTES} bytes")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SignInError(f"{url} did not answer with JSON") from None
    if not isinstance(value, dict):
        raise SignInError(f"{url} did not answer with a JSON object")
    return value


class _NotThere(Exception):
    pass


def _get_json(url: str, https_handler: urllib.request.HTTPSHandler | None) -> dict[str, Any]:
    if not is_https(url):
        raise SignInRefused(f"Limelight only signs in over https, and {url} is not")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    opener = build_opener(https_handler, _NoRedirects())
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            return _read_json(response, url)
    except urllib.error.HTTPError as error:
        error.close()
        if 400 <= error.code < 500:
            raise _NotThere(f"HTTP {error.code}") from None
        raise SignInError(f"{url} answered HTTP {error.code}") from None
    except OSError as error:
        raise SignInError(f"Could not reach {url}: {error}") from None


def _post_form(
    url: str, fields: dict[str, str], https_handler: urllib.request.HTTPSHandler | None
) -> tuple[int, dict[str, Any]]:
    """POST a form; (status, JSON body). An OAuth error answer (4xx with JSON) is returned, not raised."""
    if not is_https(url):
        raise SignInRefused(f"Limelight only signs in over https, and {url} is not")
    data = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    opener = build_opener(https_handler, _NoRedirects())
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, _read_json(response, url)
    except urllib.error.HTTPError as error:
        try:
            if 400 <= error.code < 500:
                try:
                    return error.code, _read_json(error, url)
                except SignInError:
                    return error.code, {}
            raise SignInError(f"{url} answered HTTP {error.code}") from None
        finally:
            error.close()
    except OSError as error:
        raise SignInError(f"Could not reach {url}: {error}") from None


# ---------------------------------------------------------------- discovery


@dataclass(frozen=True)
class SignInPlan:
    """What discovery found: where and how to sign in for one resource."""

    resource: str
    issuer: str
    device_authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    scope: str | None

    @property
    def host(self) -> str:
        return origin(self.resource)[1]


def resource_metadata_urls(url: str) -> list[str]:
    """The RFC 9728 §3 well-known URIs to try for `url`: its whole path, then each parent."""
    parts = urlsplit(url)
    netloc = parts.netloc.rsplit("@", 1)[-1]
    segments = [segment for segment in parts.path.split("/") if segment]
    urls = []
    for count in range(len(segments), -1, -1):
        path = "".join("/" + segment for segment in segments[:count])
        urls.append(f"https://{netloc}{_RESOURCE_METADATA_WELL_KNOWN}{path}")
    if len(urls) > _MAX_WELL_KNOWN_TRIES:
        urls = urls[: _MAX_WELL_KNOWN_TRIES - 1] + urls[-1:]
    return urls


def authorization_server_metadata_urls(issuer: str) -> list[str]:
    """RFC 8414 §3.1: the well-known segment between host and path; then OIDC's, appended."""
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    return [
        f"https://{parts.netloc}{_AUTHORIZATION_SERVER_WELL_KNOWN}{path}",
        f"{issuer.rstrip('/')}{_OPENID_WELL_KNOWN}",
    ]


def _https_on_domain(url: Any, host: str, what: str) -> str:
    if not isinstance(url, str) or not is_https(url):
        raise SignInRefused(f"{host}'s {what} is not an https URL")
    other = origin(url)[1]
    if not same_registrable_domain(other, host):
        raise SignInRefused(
            f"{host}'s {what} is at {registrable_domain(other)}, another domain; Limelight does not allow that"
        )
    return url


def discover(
    refused_url: str,
    challenge: Challenge | None,
    *,
    https_handler: urllib.request.HTTPSHandler | None = None,
) -> SignInPlan:
    """Find out how to sign in for `refused_url`, checking §5 rules 2-4 on the way."""
    if not is_https(refused_url):
        raise SignInRefused(f"Limelight only signs in over https, and {refused_url} is not")
    host = origin(refused_url)[1]

    if challenge is not None and challenge.resource_metadata:
        metadata_url = challenge.resource_metadata
        if not is_https(metadata_url) or origin(metadata_url) != origin(refused_url):
            raise SignInRefused(
                f"{host} names its sign-in details on another site ({origin(metadata_url)[1] or metadata_url}); "
                "Limelight only reads them from the site itself"
            )
        candidates = [metadata_url]
    else:
        candidates = resource_metadata_urls(refused_url)

    metadata: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            metadata = _get_json(candidate, https_handler)
            break
        except _NotThere:
            continue
    if metadata is None:
        raise SignInError(f"{host} asks you to sign in, but does not say where (no protected-resource metadata)")

    resource = metadata.get("resource")
    if not isinstance(resource, str) or not resource_covers(resource, refused_url):
        raise SignInRefused(f"{host}'s sign-in details are for {resource!r}, which does not cover {refused_url}")

    servers = metadata.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not all(isinstance(server, str) for server in servers):
        raise SignInError(f"{host} does not say who signs you in")
    same_domain = [server for server in servers if is_https(server) and same_registrable_domain(origin(server)[1], host)]
    if not same_domain:
        other = registrable_domain(origin(servers[0])[1]) or servers[0]
        raise SignInRefused(f"This site sends you to sign in at {other}; Limelight does not allow that")
    issuer = same_domain[0]
    issuer_parts = urlsplit(issuer)
    if issuer_parts.query or issuer_parts.fragment:
        raise SignInRefused(f"{host}'s sign-in server {issuer} is not a valid issuer")

    scope: str | None
    if challenge is not None and challenge.error == "insufficient_scope" and challenge.scope:
        scope = challenge.scope
    else:
        scopes = metadata.get("scopes_supported")
        scope = " ".join(str(item) for item in scopes) if isinstance(scopes, list) and scopes else None

    server = authorization_server_metadata(issuer, https_handler=https_handler)
    revocation = server.get("revocation_endpoint")
    return SignInPlan(
        resource=resource,
        issuer=issuer,
        device_authorization_endpoint=_https_on_domain(
            server.get("device_authorization_endpoint"), host, "device sign-in address"
        ),
        token_endpoint=_https_on_domain(server.get("token_endpoint"), host, "token address"),
        revocation_endpoint=_https_on_domain(revocation, host, "sign-out address") if revocation else None,
        scope=scope,
    )


def authorization_server_metadata(
    issuer: str, *, https_handler: urllib.request.HTTPSHandler | None = None
) -> dict[str, Any]:
    """The issuer's RFC 8414 metadata, with `issuer` equal and the device grant offered."""
    for url in authorization_server_metadata_urls(issuer):
        try:
            metadata = _get_json(url, https_handler)
        except _NotThere:
            continue
        if metadata.get("issuer") != issuer:
            raise SignInRefused(f"The sign-in server's details are for {metadata.get('issuer')!r}, not {issuer}")
        grants = metadata.get("grant_types_supported")
        if not (isinstance(grants, list) and DEVICE_CODE_GRANT in grants) or not metadata.get(
            "device_authorization_endpoint"
        ):
            raise SignInError(f"{origin(issuer)[1]} does not offer the device sign-in Limelight uses")
        return metadata
    raise SignInError(f"Could not find {origin(issuer)[1]}'s sign-in server details")


# ---------------------------------------------------------------- the device flow


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def start_device_authorization(
    plan: SignInPlan, *, https_handler: urllib.request.HTTPSHandler | None = None
) -> DeviceAuthorization:
    fields = {"client_id": CLIENT_ID, "resource": plan.resource}
    if plan.scope:
        fields["scope"] = plan.scope
    status, body = _post_form(plan.device_authorization_endpoint, fields, https_handler)
    if status != 200:
        raise SignInError(f"{plan.host} would not start a sign-in ({_error_code(body, status)})")
    device_code, user_code = body.get("device_code"), body.get("user_code")
    if not (isinstance(device_code, str) and device_code and isinstance(user_code, str) and user_code):
        raise SignInError(f"{plan.host} started a sign-in without a code")
    if len(user_code) > 64 or not user_code.isprintable():
        raise SignInError(f"{plan.host} sent a code Limelight cannot show")
    # Never verification_uri_complete: the reader types the code (§4.2).
    verification_uri = body.get("verification_uri")
    if not isinstance(verification_uri, str) or not is_https(verification_uri):
        raise SignInRefused(f"{plan.host}'s sign-in page is not an https address; Limelight does not allow that")
    if not same_registrable_domain(origin(verification_uri)[1], origin(plan.issuer)[1]):
        raise SignInRefused(
            f"{plan.host} asks you to sign in at {registrable_domain(origin(verification_uri)[1])}; "
            "Limelight does not allow that"
        )
    try:
        expires_in = int(body["expires_in"])
        interval = int(body.get("interval", DEFAULT_INTERVAL_SECONDS))
    except (KeyError, TypeError, ValueError):
        raise SignInError(f"{plan.host} started a sign-in without saying how long it lasts") from None
    if expires_in <= 0:
        raise SignInError(f"{plan.host} started a sign-in that had already expired")
    return DeviceAuthorization(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        expires_in=expires_in,
        interval=max(interval, 1),
    )


def _error_code(body: dict[str, Any], status: int) -> str:
    error = body.get("error")
    return str(error) if isinstance(error, str) and error.isprintable() and len(error) < 100 else f"HTTP {status}"


def _sleep(seconds: float) -> bool:
    time.sleep(seconds)
    return False


def poll_for_token(
    plan: SignInPlan,
    authorization: DeviceAuthorization,
    *,
    https_handler: urllib.request.HTTPSHandler | None = None,
    wait: Callable[[float], bool] = _sleep,
    clock: Callable[[], float] = time.monotonic,
) -> "Token":
    """Poll the token endpoint until the reader approves, denies, or the code expires.

    `wait(seconds)` sleeps and returns True if the sign-in was cancelled.
    `slow_down` adds 5 s to this and every later wait (RFC 8628 §3.5).
    """
    deadline = clock() + authorization.expires_in
    interval = authorization.interval
    fields = {
        "grant_type": DEVICE_CODE_GRANT,
        "device_code": authorization.device_code,
        "client_id": CLIENT_ID,
        "resource": plan.resource,
    }
    while True:
        if wait(interval):
            raise SignInCancelled("The sign-in was cancelled")
        if clock() >= deadline:
            raise SignInExpired("Expired: start again")
        status, body = _post_form(plan.token_endpoint, fields, https_handler)
        if status == 200:
            return token_from_response(plan, body)
        error = _error_code(body, status)
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += SLOW_DOWN_STEP_SECONDS
            continue
        if error == "access_denied":
            raise SignInDenied("Denied")
        if error == "expired_token":
            raise SignInExpired("Expired: start again")
        raise SignInError(f"{plan.host} refused the sign-in ({error})")


# ---------------------------------------------------------------- tokens


@dataclass(eq=False)
class Token:
    """A token for one resource. Its repr and str never show the secret."""

    resource: str
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    token_endpoint: str | None = None
    revocation_endpoint: str | None = None
    account: str | None = None
    expires_at: float | None = None
    remembered: bool = False
    from_environment: bool = False

    def __repr__(self) -> str:
        return (
            f"Token(resource={self.resource!r}, access={mask_token(self.access_token)}, "
            f"refresh={'yes' if self.refresh_token else 'no'}, account={self.account!r})"
        )

    __str__ = __repr__

    @property
    def usable(self) -> bool:
        return bool(self.access_token) and (self.expires_at is None or time.time() < self.expires_at - 30)


def _token_text(value: Any) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= _MAX_TOKEN_CHARS and value.isprintable() and " " not in value:
        return value
    return None


def token_from_response(plan: SignInPlan, body: dict[str, Any], previous: Token | None = None) -> Token:
    if str(body.get("token_type", "")).lower() != "bearer":
        raise SignInError(f"{plan.host} gave a token Limelight cannot use (not a Bearer token)")
    access = _token_text(body.get("access_token"))
    if access is None:
        raise SignInError(f"{plan.host} gave no usable token")
    expires_at = None
    try:
        if body.get("expires_in") is not None:
            expires_at = time.time() + int(body["expires_in"])
    except (TypeError, ValueError):
        pass
    # Not a standard field: shown as "signed in as <who>" when a server sends it.
    account = body.get("account")
    account = account if isinstance(account, str) and account.isprintable() and len(account) <= 200 else None
    return Token(
        resource=plan.resource,
        access_token=access,
        refresh_token=_token_text(body.get("refresh_token")) or (previous.refresh_token if previous else None),
        token_endpoint=plan.token_endpoint,
        revocation_endpoint=plan.revocation_endpoint,
        account=account or (previous.account if previous else None),
        expires_at=expires_at,
        remembered=previous.remembered if previous else False,
    )


def refresh(token: Token, *, https_handler: urllib.request.HTTPSHandler | None = None) -> Token:
    """A new access token from the refresh token; SignInError if the server says no."""
    if not token.refresh_token or not token.token_endpoint:
        raise SignInError("This sign-in cannot be renewed")
    if not same_registrable_domain(origin(token.token_endpoint)[1], origin(token.resource)[1]):
        raise SignInRefused("This sign-in's token address is on another domain")
    plan = SignInPlan(
        resource=token.resource,
        issuer="",
        device_authorization_endpoint="",
        token_endpoint=token.token_endpoint,
        revocation_endpoint=token.revocation_endpoint,
        scope=None,
    )
    status, body = _post_form(
        token.token_endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": CLIENT_ID,
            "resource": token.resource,
        },
        https_handler,
    )
    if status != 200:
        raise SignInError(f"{plan.host} would not renew the sign-in ({_error_code(body, status)})")
    return token_from_response(plan, body, previous=token)


def revoke(token: Token, *, https_handler: urllib.request.HTTPSHandler | None = None) -> bool:
    """RFC 7009: revoke the refresh token and the access token, if the server offers it."""
    endpoint = token.revocation_endpoint
    if not endpoint or not is_https(endpoint):
        return False
    if not same_registrable_domain(origin(endpoint)[1], origin(token.resource)[1]):
        return False
    ok = True
    for value, hint in ((token.refresh_token, "refresh_token"), (token.access_token, "access_token")):
        if not value:
            continue
        try:
            status, _ = _post_form(
                endpoint, {"token": value, "token_type_hint": hint, "client_id": CLIENT_ID}, https_handler
            )
            ok = ok and status == 200
        except SignInError as error:
            logger.warning("Revoking the sign-in for %s failed: %s", token.resource, error)
            ok = False
    return ok


def environment_token(environ: dict[str, str] | os._Environ | None = None) -> Token | None:
    """LIMELIGHT_BEARER_TOKEN for LIMELIGHT_BEARER_RESOURCE: exactly one token, one resource (CI)."""
    environ = os.environ if environ is None else environ
    value, resource = environ.get(TOKEN_ENV), environ.get(RESOURCE_ENV)
    if not value and not resource:
        return None
    if not value or not resource:
        logger.warning("%s and %s must be set together; neither is used", TOKEN_ENV, RESOURCE_ENV)
        return None
    parts = urlsplit(resource)
    if not is_https(resource) or parts.query or parts.fragment or _token_text(value) is None:
        logger.warning("%s is not an https resource (or the token is unusable); the token is not used", RESOURCE_ENV)
        return None
    return Token(resource=resource, access_token=value, from_environment=True)


_DEFAULT_BACKEND = object()


def system_keyring() -> Any | None:
    """The OS keychain, or None when there is none that works (headless Linux, say)."""
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        try:
            from keyring.backends import null

            if isinstance(backend, null.Keyring):
                return None
        except ImportError:
            pass
        chained = getattr(backend, "backends", None)
        if chained is not None and not chained:
            return None
        return backend
    except Exception as error:  # a broken keyring install is no keychain
        logger.warning("No keychain: %s", type(error).__name__)
        return None


class TokenStore:
    """Tokens by resource: in memory, and in the keychain for a remembered sign-in.

    A remembered sign-in keeps its refresh token in the keychain (or, with no
    refresh token, the access token); access tokens otherwise live in memory
    only. There is no file fallback.
    """

    def __init__(self, keyring_backend: Any = _DEFAULT_BACKEND, *, environ: Any = None) -> None:
        self._backend_arg = keyring_backend
        self._backend: Any = None
        self._environ = environ
        self._memory: dict[str, Token] = {}
        self._loaded = False
        self._lock = threading.RLock()

    def keychain(self) -> Any | None:
        with self._lock:
            if self._backend_arg is _DEFAULT_BACKEND:
                self._backend_arg = system_keyring()
            return self._backend_arg

    def keychain_available(self) -> bool:
        return self.keychain() is not None

    def _index(self, backend: Any) -> list[str]:
        raw = backend.get_password(KEYRING_SERVICE, _KEYRING_INDEX_USER)
        if not raw:
            return []
        value = json.loads(raw)
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    def _write_index(self, backend: Any) -> None:
        remembered = sorted(resource for resource, token in self._memory.items() if token.remembered)
        backend.set_password(KEYRING_SERVICE, _KEYRING_INDEX_USER, json.dumps(remembered))

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        backend = self.keychain()
        if backend is None:
            return
        try:
            for resource in self._index(backend):
                raw = backend.get_password(KEYRING_SERVICE, resource)
                if not raw or resource in self._memory:
                    continue
                entry = json.loads(raw)
                secret = _token_text(entry.get("token"))
                if secret is None or not is_https(resource):
                    continue
                is_refresh = entry.get("kind") == "refresh"
                self._memory[resource] = Token(
                    resource=resource,
                    access_token=None if is_refresh else secret,
                    refresh_token=secret if is_refresh else None,
                    token_endpoint=entry.get("token_endpoint"),
                    revocation_endpoint=entry.get("revocation_endpoint"),
                    account=entry.get("account"),
                    remembered=True,
                )
        except Exception as error:
            logger.warning("Could not read sign-ins from the keychain: %s", type(error).__name__)

    def token_for(self, url: str) -> Token | None:
        """The token for the resource covering `url` (the longest), or None."""
        env = environment_token(self._environ)
        if env is not None and resource_covers(env.resource, url):
            return env
        with self._lock:
            self._load()
            covering = [token for resource, token in self._memory.items() if resource_covers(resource, url)]
        return max(covering, key=lambda token: len(token.resource), default=None)

    def peek(self, url: str) -> Token | None:
        """`token_for` without reading the keychain: what this session already holds."""
        env = environment_token(self._environ)
        if env is not None and resource_covers(env.resource, url):
            return env
        with self._lock:
            covering = [token for resource, token in self._memory.items() if resource_covers(resource, url)]
        return max(covering, key=lambda token: len(token.resource), default=None)

    def put(self, token: Token, *, remember: bool) -> bool:
        """Keep `token`; True if it went to the keychain as asked."""
        with self._lock:
            self._load()
            token.remembered = False
            self._memory[token.resource] = token
            backend = self.keychain()
            if backend is None:
                return not remember
            try:
                if remember:
                    secret, kind = (
                        (token.refresh_token, "refresh") if token.refresh_token else (token.access_token, "access")
                    )
                    entry = {
                        "kind": kind,
                        "token": secret,
                        "token_endpoint": token.token_endpoint,
                        "revocation_endpoint": token.revocation_endpoint,
                        "account": token.account,
                    }
                    backend.set_password(KEYRING_SERVICE, token.resource, json.dumps(entry))
                    token.remembered = True
                else:
                    self._delete_entry(backend, token.resource)
                self._write_index(backend)
            except Exception as error:
                logger.warning("Could not keep the sign-in for %s in the keychain: %s", token.resource, type(error).__name__)
                token.remembered = False
                return False
            return token.remembered == remember

    @staticmethod
    def _delete_entry(backend: Any, resource: str) -> None:
        try:
            backend.delete_password(KEYRING_SERVICE, resource)
        except Exception:
            pass

    def replace(self, token: Token) -> None:
        """Keep a renewed token, in the keychain too if it was remembered."""
        with self._lock:
            if token.from_environment:
                return
            remembered = token.remembered
            self.put(token, remember=remembered)

    def forget(self, resource: str) -> None:
        with self._lock:
            self._load()
            self._memory.pop(resource, None)
            backend = self.keychain()
            if backend is not None:
                try:
                    self._delete_entry(backend, resource)
                    self._write_index(backend)
                except Exception as error:
                    logger.warning("Could not forget the sign-in for %s: %s", resource, type(error).__name__)

    def sign_ins(self) -> list[Token]:
        with self._lock:
            self._load()
            return sorted(self._memory.values(), key=lambda token: token.resource)

    def get(self, resource: str) -> Token | None:
        with self._lock:
            self._load()
            return self._memory.get(resource)

    def sign_out(self, resource: str, *, https_handler: urllib.request.HTTPSHandler | None = None) -> bool:
        """Revoke (RFC 7009, if offered) and forget. True if there was a sign-in."""
        token = self.get(resource)
        if token is None:
            return False
        if token.access_token is None and token.refresh_token is None:
            self.forget(resource)
            return True
        try:
            revoke(token, https_handler=https_handler)
        finally:
            self.forget(resource)
        return True

    def ready(self, token: Token | None, *, https_handler: urllib.request.HTTPSHandler | None = None) -> Token | None:
        """`token` with a usable access token, renewing it if need be; None if it cannot be."""
        if token is None or token.usable or token.from_environment:
            return token
        if not token.refresh_token:
            return None
        try:
            renewed = refresh(token, https_handler=https_handler)
        except SignInError as error:
            logger.info("Renewing the sign-in for %s failed: %s", token.resource, error)
            return None
        self.replace(renewed)
        return renewed


_default_store: TokenStore | None = None
_default_store_lock = threading.Lock()


def default_store() -> TokenStore:
    """The app's one token store (the system keychain)."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = TokenStore()
        return _default_store


def set_default_store(store: TokenStore | None) -> None:
    global _default_store
    with _default_store_lock:
        _default_store = store


def describe_host(host: str) -> tuple[str, str | None, str]:
    """(exact host as punycode, its Unicode form or None, its registrable domain)."""
    ascii_form = ascii_host(host)
    return ascii_form, unicode_host(ascii_form), registrable_domain(ascii_form)


__all__ = [
    "Challenge",
    "DeviceAuthorization",
    "NoKeychain",
    "SignInCancelled",
    "SignInDenied",
    "SignInError",
    "SignInExpired",
    "SignInPlan",
    "SignInRefused",
    "Token",
    "TokenStore",
    "default_store",
    "describe_host",
    "discover",
    "parse_bearer_challenge",
    "poll_for_token",
    "registrable_domain",
    "resource_covers",
    "revoke",
    "start_device_authorization",
]
