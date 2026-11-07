"""Signing in to fetch remote data (RSI, doc/design/remote-sign-in.md).

Everything runs against the local https test server in conftest.py, which
has its own CA and answers for several host names, so discovery, the device
flow, redirects and the token rules are exercised over real TLS.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest
from conftest import FakeKeyring, Reply, Seen

from limelight import cli, remote_auth, remote_files, settings
from limelight.remote_auth import (
    SignInDenied,
    SignInError,
    SignInExpired,
    SignInRefused,
    Token,
    TokenStore,
    parse_bearer_challenge,
    registrable_domain,
    resource_covers,
)
from limelight.remote_files import Changed, NeedsSignIn, NoAccess, RemoteFile, RemoteFileError, RetryAfter
from limelight.settings import ALLOWED, BLOCKED

HOST = "https://data.example.org"
RESOURCE = f"{HOST}/api/v1"
METADATA_URL = f"{HOST}/.well-known/oauth-protected-resource/api/v1"
BODY = b"hdf5 bytes " * 1000
SHA = hashlib.sha256(BODY).hexdigest()
FILE_URL = f"{RESOURCE}/files/node7/{SHA}"
TOKEN = "good-access-token-0123456789-SECRET"
CHALLENGE = f'Bearer resource_metadata="{METADATA_URL}"'


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote_files.DOWNLOAD_DIR_ENV, str(tmp_path / "downloads"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)


def _remote(url: str = FILE_URL, body: bytes = BODY) -> RemoteFile:
    return RemoteFile(url=url, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))


def _protected(token: str = TOKEN, body: bytes = BODY):
    def serve(seen: Seen) -> Reply:
        if seen.headers.get("Authorization") == f"Bearer {token}":
            return Reply(200, body)
        error = ', error="invalid_token"' if "Authorization" in seen.headers else ""
        return Reply(401, b"", {"WWW-Authenticate": CHALLENGE + error})

    return serve


def _token_replies(*replies: Reply):
    queue = list(replies)

    def serve(seen: Seen) -> Reply:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return serve


GRANTED = Reply(200, {"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600, "account": "x@y"})
PENDING = Reply(400, {"error": "authorization_pending"})


def _site(
    server,
    *,
    resource: str = RESOURCE,
    authorization_servers: list[str] | None = None,
    issuer: str | None = None,
    verification_uri: str = f"{HOST}/show/devices/approve",
    token_replies: list[Reply] | None = None,
    device_extra: dict | None = None,
) -> None:
    """A BigO-like site: the protected file, its metadata, the issuer and its endpoints."""
    server.route("GET", FILE_URL, _protected())
    server.route(
        "GET",
        METADATA_URL,
        Reply(200, {"resource": resource, "authorization_servers": authorization_servers or [HOST], "scopes_supported": ["read"]}),
    )
    server.route(
        "GET",
        f"{HOST}/.well-known/oauth-authorization-server",
        Reply(
            200,
            {
                "issuer": issuer or HOST,
                "device_authorization_endpoint": f"{HOST}/oauth/device_authorization",
                "token_endpoint": f"{HOST}/oauth/token",
                "revocation_endpoint": f"{HOST}/oauth/revoke",
                "grant_types_supported": [remote_auth.DEVICE_CODE_GRANT],
            },
        ),
    )
    device = {
        "device_code": "device-code-SECRET",
        "user_code": "BKDF-QRTW",
        "verification_uri": verification_uri,
        "verification_uri_complete": verification_uri + "?user_code=BKDF-QRTW",
        "expires_in": 600,
        "interval": 5,
    }
    device.update(device_extra or {})
    server.route("POST", f"{HOST}/oauth/device_authorization", Reply(200, device))
    server.route("POST", f"{HOST}/oauth/token", _token_replies(*(token_replies or [PENDING, GRANTED])))
    server.route("POST", f"{HOST}/oauth/revoke", Reply(200, b""))


def _no_wait(waits: list[float] | None = None):
    def wait(seconds: float) -> bool:
        if waits is not None:
            waits.append(seconds)
        return False

    return wait


def _sign_in(need: NeedsSignIn, waits: list[float] | None = None) -> Token:
    plan = remote_auth.discover(need.url, need.challenge)
    authorization = remote_auth.start_device_authorization(plan)
    return remote_auth.poll_for_token(plan, authorization, wait=_no_wait(waits))


# ---------------------------------------------------------------- good path


def test_the_whole_chain_end_to_end(https_server) -> None:
    _site(https_server)
    store = TokenStore(keyring_backend=FakeKeyring())
    remote = _remote()
    with pytest.raises(NeedsSignIn) as caught:
        remote_files.fetch(remote, tokens=store)
    need = caught.value
    assert need.challenge.resource_metadata == METADATA_URL and not need.token_sent

    plan = remote_auth.discover(need.url, need.challenge)
    assert plan.resource == RESOURCE and plan.scope == "read"
    authorization = remote_auth.start_device_authorization(plan)
    # Never verification_uri_complete.
    assert authorization.verification_uri == f"{HOST}/show/devices/approve"
    assert authorization.user_code == "BKDF-QRTW"
    token = remote_auth.poll_for_token(plan, authorization, wait=_no_wait())
    assert token.account == "x@y"
    assert store.put(token, remember=True)

    path = remote_files.fetch(remote, tokens=store)
    assert path.read_bytes() == BODY
    # RFC 8707: resource on the device authorization and on every poll.
    posts = [seen for seen in https_server.seen if seen.method == "POST"]
    assert posts and all(seen.form["resource"] == RESOURCE for seen in posts)
    assert all(seen.form["client_id"] == "limelight" for seen in posts)
    gets = https_server.requests_to(f"/api/v1/files/node7/{SHA}")
    assert "Authorization" not in gets[0].headers
    assert gets[-1].headers["Authorization"] == f"Bearer {TOKEN}"


# ---------------------------------------------------------------- discovery


def test_a_resource_that_does_not_cover_the_url_is_refused(https_server) -> None:
    _site(https_server, resource=f"{HOST}/api/v2")
    with pytest.raises(NeedsSignIn) as caught:
        remote_files.download(_remote())
    with pytest.raises(SignInRefused, match="does not cover"):
        remote_auth.discover(caught.value.url, caught.value.challenge)


def test_an_authorization_server_on_another_domain_is_refused(https_server) -> None:
    _site(https_server, authorization_servers=["https://sso.university.example.net"])
    with pytest.raises(NeedsSignIn) as caught:
        remote_files.download(_remote())
    with pytest.raises(SignInRefused, match="sign in at example.net; Limelight does not allow that"):
        remote_auth.discover(caught.value.url, caught.value.challenge)
    # Nothing was asked of the other domain.
    assert not [seen for seen in https_server.seen if seen.host == "sso.university.example.net"]


def test_an_authorization_server_on_a_sibling_host_of_the_same_domain_is_fine(https_server) -> None:
    _site(https_server, authorization_servers=["https://auth.example.org"])
    issuer = "https://auth.example.org"
    https_server.route(
        "GET",
        f"{issuer}/.well-known/oauth-authorization-server",
        Reply(
            200,
            {
                "issuer": issuer,
                "device_authorization_endpoint": f"{issuer}/device",
                "token_endpoint": f"{issuer}/token",
                "grant_types_supported": [remote_auth.DEVICE_CODE_GRANT],
            },
        ),
    )
    plan = remote_auth.discover(FILE_URL, parse_bearer_challenge([CHALLENGE]))
    assert plan.issuer == issuer and plan.token_endpoint == f"{issuer}/token"


def test_a_wrong_issuer_is_refused(https_server) -> None:
    _site(https_server, issuer="https://other.example.org")
    with pytest.raises(SignInRefused, match="not https://data.example.org"):
        remote_auth.discover(FILE_URL, parse_bearer_challenge([CHALLENGE]))


def test_an_issuer_without_the_device_grant_is_refused(https_server) -> None:
    _site(https_server)
    https_server.route(
        "GET",
        f"{HOST}/.well-known/oauth-authorization-server",
        Reply(200, {"issuer": HOST, "token_endpoint": f"{HOST}/oauth/token", "grant_types_supported": ["authorization_code"]}),
    )
    with pytest.raises(SignInError, match="device sign-in"):
        remote_auth.discover(FILE_URL, parse_bearer_challenge([CHALLENGE]))


def test_resource_metadata_on_another_origin_is_not_fetched(https_server) -> None:
    _site(https_server)
    challenge = parse_bearer_challenge(['Bearer resource_metadata="https://other.example.org/.well-known/x"'])
    with pytest.raises(SignInRefused, match="another site"):
        remote_auth.discover(FILE_URL, challenge)
    assert https_server.seen == []


def test_a_401_without_resource_metadata_falls_back_to_the_well_known_uri(https_server) -> None:
    _site(https_server)
    plan = remote_auth.discover(FILE_URL, parse_bearer_challenge(['Bearer realm="api"']))
    assert plan.resource == RESOURCE
    tried = [seen.path for seen in https_server.seen if seen.path.startswith("/.well-known/oauth-protected")]
    assert tried[0] == f"/.well-known/oauth-protected-resource/api/v1/files/node7/{SHA}"
    assert tried[-1] == "/.well-known/oauth-protected-resource/api/v1"


def test_no_metadata_anywhere_fails_with_a_message(https_server) -> None:
    with pytest.raises(SignInError, match="does not say where"):
        remote_auth.discover(FILE_URL, None)


def test_issuer_metadata_urls_follow_rfc_8414() -> None:
    assert remote_auth.authorization_server_metadata_urls("https://h.example/tenant/a") == [
        "https://h.example/.well-known/oauth-authorization-server/tenant/a",
        "https://h.example/tenant/a/.well-known/openid-configuration",
    ]


def test_a_resource_covers_on_path_segment_boundaries_only() -> None:
    assert resource_covers("https://h.example/api/v1", "https://h.example/api/v1/files/x")
    assert resource_covers("https://h.example/api/v1/", "https://h.example/api/v1/files/x")
    assert resource_covers("https://h.example/api/v1", "https://h.example/api/v1")
    assert not resource_covers("https://h.example/data", "https://h.example/data-evil/x")
    assert not resource_covers("https://h.example/api", "https://g.example/api/x")
    assert not resource_covers("https://h.example/api", "https://h.example:8443/api/x")
    assert not resource_covers("https://h.example/api", "http://h.example/api/x")
    assert not resource_covers("https://h.example/api", "https://h.example/api/../admin")
    assert not resource_covers("https://h.example/api", "https://h.example/api/%2e%2e/admin")


def test_registrable_domains_come_from_the_public_suffix_list() -> None:
    assert registrable_domain("dev0.super-easy-sensors.com") == "super-easy-sensors.com"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("one.github.io") == "one.github.io"
    assert registrable_domain("127.0.0.1") == "127.0.0.1"
    assert not remote_auth.same_registrable_domain("a.github.io", "b.github.io")


# ---------------------------------------------------------------- redirects


def test_a_redirect_to_http_is_refused(https_server) -> None:
    https_server.route("GET", FILE_URL, Reply(302, b"", {"Location": "http://data.example.org/plain.h5"}))
    with pytest.raises(RemoteFileError, match="not https"):
        remote_files.download(_remote())


def test_a_same_origin_redirect_keeps_the_token(https_server) -> None:
    moved = f"{RESOURCE}/files/node7/moved"
    https_server.route("GET", FILE_URL, Reply(302, b"", {"Location": moved}))
    https_server.route("GET", moved, _protected())
    token = Token(resource=RESOURCE, access_token=TOKEN)
    assert remote_files.download(_remote(), token=token).read_bytes() == BODY
    [seen] = https_server.requests_to("/api/v1/files/node7/moved")
    assert seen.headers["Authorization"] == f"Bearer {TOKEN}"


def test_a_cross_origin_redirect_drops_the_token(https_server) -> None:
    presigned = "https://store.example.com/bucket/object?X-Signature=abc"
    https_server.route("GET", FILE_URL, Reply(307, b"", {"Location": presigned}))
    https_server.route("GET", "https://store.example.com/bucket/object", Reply(200, BODY))
    token = Token(resource=RESOURCE, access_token=TOKEN)
    assert remote_files.download(_remote(), token=token).read_bytes() == BODY
    [seen] = https_server.requests_to("/bucket/object")
    assert "Authorization" not in seen.headers


def test_a_401_from_a_redirect_target_never_starts_a_sign_in(https_server) -> None:
    https_server.route("GET", FILE_URL, Reply(302, b"", {"Location": "https://other.example.org/x"}))
    https_server.route("GET", "https://other.example.org/x", Reply(401, b"", {"WWW-Authenticate": CHALLENGE}))
    with pytest.raises(RemoteFileError, match="does not sign in to a site it was redirected to") as caught:
        remote_files.download(_remote())
    assert not isinstance(caught.value, NeedsSignIn)


def test_a_token_is_never_sent_outside_its_resource(https_server) -> None:
    url = f"{HOST}/elsewhere/file.h5"
    https_server.route("GET", url, Reply(200, BODY))
    remote_files.download(_remote(url), token=Token(resource=RESOURCE, access_token=TOKEN))
    [seen] = https_server.requests_to("/elsewhere/file.h5")
    assert "Authorization" not in seen.headers


# ---------------------------------------------------------------- the device flow


def test_slow_down_adds_five_seconds_to_this_and_every_later_poll(https_server) -> None:
    slow = Reply(400, {"error": "slow_down"})
    _site(https_server, token_replies=[PENDING, slow, PENDING, slow, GRANTED])
    waits: list[float] = []
    _sign_in(NeedsSignIn(FILE_URL, parse_bearer_challenge([CHALLENGE]), token_sent=False), waits)
    assert waits == [5, 5, 10, 10, 15]


def test_the_interval_defaults_to_five_seconds(https_server) -> None:
    _site(https_server, device_extra={"interval": None}, token_replies=[GRANTED])
    https_server.routes[("POST", "data.example.org", "/oauth/device_authorization")] = Reply(
        200, {"device_code": "d", "user_code": "U", "verification_uri": f"{HOST}/approve", "expires_in": 60}
    )
    waits: list[float] = []
    _sign_in(NeedsSignIn(FILE_URL, parse_bearer_challenge([CHALLENGE]), token_sent=False), waits)
    assert waits == [5]


def test_a_denied_sign_in_says_so(https_server) -> None:
    _site(https_server, token_replies=[PENDING, Reply(400, {"error": "access_denied"})])
    with pytest.raises(SignInDenied):
        _sign_in(NeedsSignIn(FILE_URL, parse_bearer_challenge([CHALLENGE]), token_sent=False))


def test_an_expired_code_says_start_again(https_server) -> None:
    _site(https_server, token_replies=[PENDING, Reply(400, {"error": "expired_token"})])
    with pytest.raises(SignInExpired, match="start again"):
        _sign_in(NeedsSignIn(FILE_URL, parse_bearer_challenge([CHALLENGE]), token_sent=False))


def test_polling_stops_at_expires_in(https_server) -> None:
    _site(https_server, token_replies=[PENDING])
    plan = remote_auth.discover(FILE_URL, parse_bearer_challenge([CHALLENGE]))
    authorization = remote_auth.start_device_authorization(plan)
    now = [0.0]

    def wait(seconds: float) -> bool:
        now[0] += seconds
        return False

    with pytest.raises(SignInExpired):
        remote_auth.poll_for_token(plan, authorization, wait=wait, clock=lambda: now[0])
    polls = [seen for seen in https_server.seen if seen.path == "/oauth/token"]
    assert len(polls) == 600 // 5 - 1


def test_insufficient_scope_signs_in_again_for_that_scope(https_server) -> None:
    _site(https_server)
    https_server.route(
        "GET",
        FILE_URL,
        Reply(403, b"", {"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="read export"'}),
    )
    with pytest.raises(NeedsSignIn) as caught:
        remote_files.download(_remote(), token=Token(resource=RESOURCE, access_token=TOKEN))
    assert caught.value.insufficient_scope
    plan = remote_auth.discover(caught.value.url, caught.value.challenge)
    assert plan.scope == "read export"
    remote_auth.start_device_authorization(plan)
    [device] = https_server.requests_to("/oauth/device_authorization")
    assert device.form["scope"] == "read export"


@pytest.mark.parametrize(
    "uri", ["http://data.example.org/approve", "https://evil.example.net/approve", "https://data.example.org.evil.net/x"]
)
def test_a_verification_uri_off_https_or_off_the_domain_is_refused(https_server, uri: str) -> None:
    _site(https_server, verification_uri=uri)
    plan = remote_auth.discover(FILE_URL, parse_bearer_challenge([CHALLENGE]))
    with pytest.raises(SignInRefused):
        remote_auth.start_device_authorization(plan)


def test_an_invalid_token_is_renewed_with_the_refresh_token(https_server) -> None:
    _site(https_server)
    https_server.route(
        "POST",
        f"{HOST}/oauth/token",
        Reply(200, {"access_token": TOKEN, "token_type": "Bearer", "refresh_token": "refresh-2-SECRET"}),
    )
    keychain = FakeKeyring()
    store = TokenStore(keyring_backend=keychain)
    stale = Token(
        resource=RESOURCE, access_token="stale-token-SECRET-0000", refresh_token="refresh-1-SECRET",
        token_endpoint=f"{HOST}/oauth/token",
    )
    store.put(stale, remember=True)
    assert remote_files.fetch(_remote(), tokens=store).read_bytes() == BODY
    [renewal] = https_server.requests_to("/oauth/token")
    assert renewal.form["grant_type"] == "refresh_token" and renewal.form["resource"] == RESOURCE
    assert "refresh-2-SECRET" in str(keychain.entries)


# ---------------------------------------------------------------- errors


@pytest.mark.parametrize("status", [403, 404])
def test_no_access_names_the_account_and_host(https_server, status: int) -> None:
    https_server.route("GET", FILE_URL, Reply(status, b""))
    with pytest.raises(NoAccess, match="Your account x@y cannot read this data on data.example.org"):
        remote_files.download(_remote(), token=Token(resource=RESOURCE, access_token=TOKEN, account="x@y"))


def test_changed_data_asks_for_a_new_package(https_server) -> None:
    https_server.route("GET", FILE_URL, Reply(409, {"error": "superseded"}))
    with pytest.raises(Changed, match="has changed on data.example.org since the package was made"):
        remote_files.download(_remote())


@pytest.mark.parametrize("status", [429, 503])
def test_busy_waits_out_retry_after(https_server, status: int) -> None:
    replies = [Reply(status, b"", {"Retry-After": "2"}), Reply(200, BODY)]
    https_server.route("GET", FILE_URL, lambda seen: replies.pop(0))
    slept: list[float] = []
    waits: list[tuple[float, int]] = []
    path = remote_files.fetch(_remote(), sleep=slept.append, on_wait=lambda s, a: waits.append((s, a)))
    assert path.read_bytes() == BODY
    assert waits == [(2.0, 1)] and sum(slept) == pytest.approx(2.0)


def test_busy_gives_up_after_three_tries(https_server) -> None:
    https_server.route("GET", FILE_URL, Reply(503, b"", {"Retry-After": "1"}))
    with pytest.raises(RetryAfter, match="busy"):
        remote_files.fetch(_remote(), sleep=lambda _s: None)
    assert len(https_server.requests_to(f"/api/v1/files/node7/{SHA}")) == 3


def test_a_retry_after_over_two_minutes_is_not_waited(https_server) -> None:
    https_server.route("GET", FILE_URL, Reply(429, b"", {"Retry-After": "121"}))
    with pytest.raises(RetryAfter):
        remote_files.fetch(_remote(), sleep=lambda _s: pytest.fail("waited"))


# ---------------------------------------------------------------- challenges


def test_bearer_challenges_are_parsed() -> None:
    challenge = parse_bearer_challenge(
        ['Basic realm="x", Bearer realm="api", error="invalid_token", resource_metadata="https://h/m", scope="a b"']
    )
    assert challenge is not None
    assert challenge.error == "invalid_token" and challenge.resource_metadata == "https://h/m" and challenge.scope == "a b"
    assert parse_bearer_challenge(['Basic realm="x"']) is None
    assert parse_bearer_challenge(["Bearer"]) is not None
    assert parse_bearer_challenge(['Bearer error="a", error="b"']) is None
    assert parse_bearer_challenge(['Bearer realm="a \\"quoted\\" one"']).params["realm"] == 'a "quoted" one'


# ---------------------------------------------------------------- tokens


def test_tokens_never_reach_logs_or_exceptions(https_server, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _site(https_server)
    store = TokenStore(keyring_backend=FakeKeyring())
    token = Token(
        resource=RESOURCE, access_token=TOKEN, refresh_token="refresh-SECRET-abcdef", account="x@y",
        revocation_endpoint=f"{HOST}/oauth/revoke",
    )
    store.put(token, remember=True)
    texts = [repr(token), str(token), repr(store.sign_ins())]
    https_server.route("GET", FILE_URL, Reply(404, b""))
    with pytest.raises(NoAccess) as caught:
        remote_files.fetch(_remote(), tokens=store)
    texts += [str(caught.value), repr(caught.value)]
    https_server.route("GET", FILE_URL, Reply(302, b"", {"Location": "http://data.example.org/x"}))
    with pytest.raises(RemoteFileError) as caught:
        remote_files.download(_remote(), token=token)
    texts += [str(caught.value)]
    store.sign_out(RESOURCE)
    texts.append(caplog.text)
    for text in texts:
        assert TOKEN not in text and "refresh-SECRET" not in text
    assert remote_auth.mask_token(TOKEN) == "Bearer …CRET"
    # Revoked on sign-out (RFC 7009), refresh token first.
    revokes = https_server.requests_to("/oauth/revoke")
    assert [seen.form["token_type_hint"] for seen in revokes] == ["refresh_token", "access_token"]


def test_a_remembered_sign_in_keeps_only_the_refresh_token_in_the_keychain() -> None:
    keychain = FakeKeyring()
    store = TokenStore(keyring_backend=keychain)
    assert store.put(Token(resource=RESOURCE, access_token=TOKEN, refresh_token="refresh-1-SECRET"), remember=True)
    assert TOKEN not in str(keychain.entries) and "refresh-1-SECRET" in str(keychain.entries)
    again = TokenStore(keyring_backend=keychain)
    [loaded] = again.sign_ins()
    assert loaded.resource == RESOURCE and loaded.access_token is None and loaded.remembered
    again.forget(RESOURCE)
    assert TokenStore(keyring_backend=keychain).sign_ins() == []


def test_an_unremembered_sign_in_stays_in_memory() -> None:
    keychain = FakeKeyring()
    store = TokenStore(keyring_backend=keychain)
    store.put(Token(resource=RESOURCE, access_token=TOKEN), remember=False)
    assert store.token_for(FILE_URL).access_token == TOKEN
    assert TOKEN not in str(keychain.entries)
    assert TokenStore(keyring_backend=keychain).sign_ins() == []


def test_without_a_keychain_tokens_are_memory_only() -> None:
    store = TokenStore(keyring_backend=None)
    assert not store.keychain_available()
    assert store.put(Token(resource=RESOURCE, access_token=TOKEN), remember=True) is False
    assert store.token_for(FILE_URL) is not None


def test_the_environment_gives_one_token_for_one_resource() -> None:
    environ = {remote_auth.TOKEN_ENV: TOKEN, remote_auth.RESOURCE_ENV: RESOURCE}
    store = TokenStore(keyring_backend=None, environ=environ)
    assert store.token_for(FILE_URL).access_token == TOKEN
    assert store.token_for("https://other.example.org/api/v1/x") is None
    assert TokenStore(keyring_backend=None, environ={remote_auth.TOKEN_ENV: TOKEN}).token_for(FILE_URL) is None


# ---------------------------------------------------------------- the dialogs' decisions


@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_blocked_beats_allowed_and_unknown_hosts_are_asked(qt_app) -> None:
    from limelight import qt_downloads

    settings.set_host_decision("a.example.org", ALLOWED)
    app = settings.load_app_settings()
    app[settings.DOWNLOAD_HOSTS_KEY]["blocked"] = ["a.example.org"]
    settings.save_app_settings(app)
    settings.set_host_decision("b.example.org", ALLOWED)
    wanted = [
        ("one", _remote("https://a.example.org/x.h5")),
        ("two", _remote("https://b.example.org/y.h5")),
        ("three", _remote("https://c.example.org/z.h5")),
    ]
    plan = qt_downloads.plan_hosts(wanted)
    assert list(plan.blocked) == ["a.example.org"]
    assert list(plan.allowed) == ["b.example.org"]
    assert list(plan.to_ask) == ["c.example.org"]


def test_the_consent_dialog_decisions(qt_app) -> None:
    from limelight import qt_downloads

    assert qt_downloads.consent_decisions(True, ["a.example.org"]) == {"a.example.org": ALLOWED}
    assert qt_downloads.consent_decisions(False, ["a.example.org"]) == {"a.example.org": BLOCKED}
    assert qt_downloads.consent_decisions(True, []) == {}

    dialog = qt_downloads.ConsentDialog(
        None,
        {
            "dev0.super-easy-sensors.com": [("rec", _remote("https://dev0.super-easy-sensors.com/a.h5"))],
            "xn--bcher-kva.example.org": [("b", _remote("https://xn--bcher-kva.example.org/b.h5"))],
        },
    )
    # No default button: Enter does nothing.
    assert not dialog.download_button.isDefault() and not dialog.download_button.autoDefault()
    assert not dialog.dont_button.isDefault() and not dialog.dont_button.autoDefault()
    assert dialog.host_labels["dev0.super-easy-sensors.com"].text() == "dev0.<b>super-easy-sensors.com</b>"
    assert dialog.host_labels["xn--bcher-kva.example.org"].text() == "xn--bcher-kva.<b>example.org</b> (bücher.example.org)"
    dialog.remember_boxes["dev0.super-easy-sensors.com"].setChecked(True)
    assert dialog.decisions(False) == {"dev0.super-easy-sensors.com": BLOCKED}


def _plan_and_authorization(uri: str = f"{HOST}/show/devices/approve"):
    plan = remote_auth.SignInPlan(
        resource=RESOURCE, issuer=HOST, device_authorization_endpoint=f"{HOST}/d",
        token_endpoint=f"{HOST}/t", revocation_endpoint=None, scope="read",
    )
    authorization = remote_auth.DeviceAuthorization("d", "BKDF-QRTW", uri, 600, 5)
    return plan, authorization


def _wait_for(dialog, qt_app) -> None:
    import time

    deadline = time.monotonic() + 5
    while dialog.isVisible() and dialog.status_label.text().startswith("Waiting") and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


def test_the_sign_in_dialog_opens_the_plain_link_and_takes_the_token(qt_app) -> None:
    from limelight import qt_downloads

    plan, authorization = _plan_and_authorization()
    opened: list[str] = []
    dialog = qt_downloads.SignInDialog(
        None, plan, authorization, keychain_available=True, open_url=opened.append,
        poll=lambda *a, **k: Token(resource=RESOURCE, access_token=TOKEN),
    )
    assert not dialog.cancel_button.isDefault() and not dialog.copy_button.autoDefault()
    assert dialog.code_label.text() == "BKDF-QRTW"
    dialog.show()
    _wait_for(dialog, qt_app)
    assert opened == [f"{HOST}/show/devices/approve"]
    assert dialog.token is not None and dialog.result() == dialog.DialogCode.Accepted


def test_the_sign_in_dialog_shows_a_denial(qt_app) -> None:
    from limelight import qt_downloads

    plan, authorization = _plan_and_authorization()

    def denied(*_a, **_k):
        raise SignInDenied("Denied")

    dialog = qt_downloads.SignInDialog(None, plan, authorization, keychain_available=False, open_url=lambda _u: None, poll=denied)
    assert not dialog.remember_box.isEnabled() and not dialog.remember()
    dialog.show()
    _wait_for(dialog, qt_app)
    assert dialog.status_label.text() == "Denied" and dialog.token is None
    dialog.reject()


def test_the_sign_in_dialog_refuses_a_link_off_the_domain(qt_app) -> None:
    from limelight import qt_downloads

    plan, authorization = _plan_and_authorization("https://evil.example.net/approve")
    with pytest.raises(SignInRefused):
        qt_downloads.SignInDialog(None, plan, authorization, keychain_available=True)


def test_the_app_signs_in_on_the_gui_thread_and_retries_the_file(qt_app, https_server, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from limelight import qt_downloads

    _site(https_server, token_replies=[GRANTED])
    settings.set_host_decision("data.example.org", ALLOWED)
    store = TokenStore(keyring_backend=None)
    shown: list[str] = []

    def approve(dialog) -> int:
        shown.append(dialog.authorization.user_code)
        dialog.token = remote_auth.poll_for_token(dialog.plan, dialog.authorization, wait=_no_wait())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(qt_downloads.SignInDialog, "exec", approve)

    class Runtime:
        def remote_files(self):
            return [({"id": "rec"}, _remote())]

    bar = qt_downloads.RemoteDataBar()
    assert qt_downloads.fetch_remote_files(None, Runtime(), bar=bar, store=store) is True
    assert shown == ["BKDF-QRTW"]
    assert _remote().cached_path() is not None
    assert "signed in as x@y" in bar.text() and RESOURCE in bar.sign_out_buttons


# ---------------------------------------------------------------- the command line


def test_blocked_hosts_are_refused_even_with_allow_downloads(https_server, capsys) -> None:
    settings.set_host_decision("data.example.org", BLOCKED)
    allow = cli._download_policy(True)
    assert allow(_remote()) is False
    assert https_server.seen == []
    assert "Blocked" in capsys.readouterr().err


def test_the_cli_says_to_log_in_on_a_401(https_server, capsys) -> None:
    _site(https_server)
    allow = cli._download_policy(True)
    with pytest.raises(RemoteFileError, match=f"run: LL login {FILE_URL}"):
        allow(_remote())
    assert FILE_URL in capsys.readouterr().err


def test_the_cli_uses_a_stored_token(https_server, capsys) -> None:
    _site(https_server)
    remote_auth.default_store().put(Token(resource=RESOURCE, access_token=TOKEN), remember=False)
    settings.set_host_decision("data.example.org", ALLOWED)
    assert cli._download_policy(False)(_remote()) is True
    assert _remote().cached_path() is not None


def test_login_fails_without_a_keychain(capsys) -> None:
    assert cli.main(["login", FILE_URL]) == 1
    assert "no keychain" in capsys.readouterr().err


def test_login_and_logout_with_a_keychain(https_server, capsys, monkeypatch) -> None:
    _site(https_server, token_replies=[GRANTED])
    keychain = FakeKeyring()
    remote_auth.set_default_store(TokenStore(keyring_backend=keychain))
    monkeypatch.setattr(remote_auth, "_sleep", lambda _s: False)
    monkeypatch.setattr(remote_auth.poll_for_token, "__kwdefaults__", {**remote_auth.poll_for_token.__kwdefaults__, "wait": lambda _s: False})
    assert cli.main(["login", FILE_URL]) == 0
    err = capsys.readouterr().err
    assert "BKDF-QRTW" in err and f"{HOST}/show/devices/approve" in err and TOKEN not in err
    assert TokenStore(keyring_backend=keychain).get(RESOURCE) is not None
    assert cli.main(["logout", RESOURCE]) == 0
    assert TokenStore(keyring_backend=keychain).get(RESOURCE) is None
    assert https_server.requests_to("/oauth/revoke")
