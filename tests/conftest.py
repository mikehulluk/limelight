"""Shared fixtures: no test touches the real keychain or the network.

`https_server` is a local https server with its own CA. It answers for any
host name in its certificate (data.example.org, auth.example.org, ...): the
client's handler connects every request to it, and checks the certificate
against the test CA, so the code under test speaks real TLS to real names.
"""

from __future__ import annotations

import datetime
import functools
import http.client
import http.server
import ipaddress
import json
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from limelight import remote_auth

HOSTS = [
    "data.example.org",
    "auth.example.org",
    "other.example.org",
    "store.example.com",
    "sso.university.example.net",
    "xn--bcher-kva.example.org",
]


class _RefuseNetwork(urllib.request.HTTPSHandler):
    def https_open(self, req):  # type: ignore[override]
        raise urllib.error.URLError(f"tests do not use the network ({req.full_url})")


@pytest.fixture(autouse=True)
def _no_keychain_and_no_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(remote_auth, "https_handler_factory", _RefuseNetwork)
    monkeypatch.delenv(remote_auth.TOKEN_ENV, raising=False)
    monkeypatch.delenv(remote_auth.RESOURCE_ENV, raising=False)
    remote_auth.set_default_store(remote_auth.TokenStore(keyring_backend=None))
    yield
    remote_auth.set_default_store(None)


class FakeKeyring:
    """A keychain in a dict, with the keyring backend's three calls."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.entries.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        self.entries[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        self.entries.pop((service, user), None)


@pytest.fixture
def fake_keyring() -> FakeKeyring:
    return FakeKeyring()


# ---------------------------------------------------------------- certificates


def _make_certificates(directory: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Limelight test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(host) for host in HOSTS] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = directory / "ca.pem", directory / "server.pem", directory / "server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return ca_path, cert_path, key_path


# ---------------------------------------------------------------- the server


@dataclass
class Seen:
    method: str
    host: str
    path: str
    headers: dict[str, str]
    form: dict[str, str]


@dataclass
class Reply:
    status: int = 200
    body: bytes | dict | list = b""
    headers: dict[str, str] = field(default_factory=dict)


Route = Callable[[Seen], Reply]


class LocalHTTPSServer:
    def __init__(self, server: http.server.ThreadingHTTPServer, context: ssl.SSLContext) -> None:
        self.server = server
        self.client_context = context
        self.routes: dict[tuple[str, str, str], Route | Reply] = {}
        self.seen: list[Seen] = []

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def route(self, method: str, url: str, reply: Route | Reply) -> None:
        parts = urllib.parse.urlsplit(url)
        self.routes[(method, parts.hostname or "", parts.path)] = reply

    def handler(self) -> urllib.request.HTTPSHandler:
        return _LocalHTTPSHandler(self.port, self.client_context)

    def requests_to(self, path: str) -> list[Seen]:
        return [seen for seen in self.seen if seen.path == path]


class _LocalConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, local_port: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._local_port = local_port

    def connect(self) -> None:
        sock = socket.create_connection(("127.0.0.1", self._local_port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _LocalHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, port: int, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self._port = port
        self._ctx = context

    def https_open(self, req):  # type: ignore[override]
        return self.do_open(functools.partial(_LocalConnection, local_port=self._port), req, context=self._ctx)


def _make_request_handler(state: dict[str, LocalHTTPSServer]) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _serve(self, method: str) -> None:
            server = state["server"]
            parts = urllib.parse.urlsplit(self.path)
            host = (self.headers.get("Host") or "").split(":")[0]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            form = dict(urllib.parse.parse_qsl(raw.decode("ascii"))) if raw else {}
            seen = Seen(method, host, parts.path, {k: v for k, v in self.headers.items()}, form)
            server.seen.append(seen)
            route = server.routes.get((method, host, parts.path))
            reply = Reply(404, b"not here") if route is None else route(seen) if callable(route) else route
            body = reply.body
            headers = dict(reply.headers)
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
                headers.setdefault("Content-Type", "application/json")
            self.send_response(reply.status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

    return Handler


@pytest.fixture
def https_server(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    ca, cert, key = _make_certificates(tmp_path_factory.mktemp("certs"))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    state: dict[str, LocalHTTPSServer] = {}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_request_handler(state))
    httpd.daemon_threads = True
    httpd.socket = server_context.wrap_socket(httpd.socket, server_side=True)
    client_context = ssl.create_default_context(cafile=str(ca))
    local = LocalHTTPSServer(httpd, client_context)
    state["server"] = local
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    # Everything the app builds an opener for goes to this server, over TLS.
    monkeypatch.setattr(remote_auth, "https_handler_factory", local.handler)
    yield local
    httpd.shutdown()
    httpd.server_close()
