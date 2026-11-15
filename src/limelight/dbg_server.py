"""A `--dbg-server` loopback HTTP server for the desktop app.

Meant for an agent (or a script) driving the open window from the outside:
tell it to reload a package that was just rebuilt, scroll to a figure or
section, pop a figure out into its own window, and read back recent logs and
timing entries. Commands arrive on the HTTP server's own worker thread;
`DebugCommandBridge` marshals each one onto the Qt main thread with a
BlockingQueuedConnection, so by the time the HTTP handler sends its response
the command has actually run (or raised) there - safe for touching widgets,
and safe for the handler to report the real outcome.

Nothing here is reachable except from localhost (the server only binds
127.0.0.1), and there is no authentication - this is a local development
aid, not a service to expose on a network.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, Qt, Signal

logger = logging.getLogger("limelight.dbg_server")

HELP_PAYLOAD = {
    "ok": True,
    "commands": [
        "POST /reload",
        "POST /scroll-to  {\"id\": \"<figure-or-section-id>\"}  (or ?id=...)",
        "POST /open-figure  {\"id\": \"<figure-id>\"}  (or ?id=...)",
        "GET /logs?lines=200",
        "GET /timing?lines=200",
    ],
}


class RingBufferLogHandler(logging.Handler):
    """Keeps the last `capacity` formatted log records in memory.

    Independent of file logging, so /logs and /timing work whether or not
    `--log-file`/the default log file is in play.
    """

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self._lines.append(line)

    def tail(self, count: int, *, name_prefix: str | None = None) -> list[str]:
        with self._lock:
            lines = list(self._lines)
        if name_prefix is not None:
            needle = f"[{name_prefix}"
            lines = [line for line in lines if needle in line]
        return lines[-count:]


class DebugCommandBridge(QObject):
    """Runs debug-server commands on the Qt main thread."""

    reload_requested = Signal()
    scroll_requested = Signal(str)
    open_figure_requested = Signal(str)

    def __init__(
        self,
        *,
        on_reload: Callable[[], None],
        on_scroll_to: Callable[[str], bool],
        on_open_figure: Callable[[str], bool],
    ) -> None:
        super().__init__()
        self._on_reload = on_reload
        self._on_scroll_to = on_scroll_to
        self._on_open_figure = on_open_figure
        self._last_error: str | None = None
        self._last_result: bool = True
        self.reload_requested.connect(self._handle_reload, Qt.ConnectionType.BlockingQueuedConnection)
        self.scroll_requested.connect(self._handle_scroll, Qt.ConnectionType.BlockingQueuedConnection)
        self.open_figure_requested.connect(self._handle_open_figure, Qt.ConnectionType.BlockingQueuedConnection)

    def reload(self) -> None:
        self._dispatch(self.reload_requested, ())

    def scroll_to(self, target: str) -> bool:
        return self._dispatch(self.scroll_requested, (target,))

    def open_figure(self, figure_id: str) -> bool:
        return self._dispatch(self.open_figure_requested, (figure_id,))

    def _dispatch(self, signal: Signal, args: tuple[Any, ...]) -> bool:
        self._last_error = None
        self._last_result = True
        signal.emit(*args)
        if self._last_error is not None:
            raise RuntimeError(self._last_error)
        return self._last_result

    def _handle_reload(self) -> None:
        try:
            self._on_reload()
        except Exception as error:  # noqa: BLE001 - reported back to the HTTP caller
            logger.exception("dbg-server reload failed")
            self._last_error = str(error)

    def _handle_scroll(self, target: str) -> None:
        try:
            self._last_result = self._on_scroll_to(target)
        except Exception as error:  # noqa: BLE001
            logger.exception("dbg-server scroll-to failed")
            self._last_error = str(error)

    def _handle_open_figure(self, figure_id: str) -> None:
        try:
            self._last_result = self._on_open_figure(figure_id)
        except Exception as error:  # noqa: BLE001
            logger.exception("dbg-server open-figure failed")
            self._last_error = str(error)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _make_handler(bridge: DebugCommandBridge, log_handler: RingBufferLogHandler) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("%s - %s", self.address_string(), format % args)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else {}

        def _id_param(self, body: dict[str, Any]) -> str | None:
            if "id" in body:
                return str(body["id"])
            query = parse_qs(urlsplit(self.path).query)
            if "id" in query:
                return query["id"][0]
            return None

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            query = parse_qs(urlsplit(self.path).query)
            try:
                if path == "/":
                    _json_response(self, 200, HELP_PAYLOAD)
                elif path == "/logs":
                    count = int(query.get("lines", ["200"])[0])
                    _json_response(self, 200, {"ok": True, "lines": log_handler.tail(count)})
                elif path == "/timing":
                    count = int(query.get("lines", ["200"])[0])
                    _json_response(
                        self,
                        200,
                        {"ok": True, "lines": log_handler.tail(count, name_prefix="limelight.timing")},
                    )
                else:
                    _json_response(self, 404, {"ok": False, "error": f"unknown path {path!r}"})
            except Exception as error:  # noqa: BLE001
                _json_response(self, 500, {"ok": False, "error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                body = self._read_json_body()
                if path == "/reload":
                    bridge.reload()
                    _json_response(self, 200, {"ok": True})
                elif path == "/scroll-to":
                    target = self._id_param(body)
                    if not target:
                        _json_response(self, 400, {"ok": False, "error": "missing 'id'"})
                        return
                    found = bridge.scroll_to(target)
                    _json_response(self, 200 if found else 404, {"ok": found})
                elif path == "/open-figure":
                    figure_id = self._id_param(body)
                    if not figure_id:
                        _json_response(self, 400, {"ok": False, "error": "missing 'id'"})
                        return
                    found = bridge.open_figure(figure_id)
                    _json_response(self, 200 if found else 404, {"ok": found})
                else:
                    _json_response(self, 404, {"ok": False, "error": f"unknown path {path!r}"})
            except Exception as error:  # noqa: BLE001
                _json_response(self, 500, {"ok": False, "error": str(error)})

    return Handler


class _StrictHTTPServer(ThreadingHTTPServer):
    # http.server.HTTPServer defaults allow_reuse_address to True, which lets
    # a second bind to an in-use port succeed instead of failing - on Windows,
    # SO_REUSEADDR's semantics are looser than POSIX's (it can let two
    # processes share a listening port rather than one erroring out). A
    # second --dbg-server on the same port must fail loudly, never silently
    # share it or fall back to another port.
    allow_reuse_address = False


class DebugServer:
    """Starts on construction; `stop()` shuts the HTTP thread down cleanly."""

    def __init__(
        self,
        *,
        port: int,
        on_reload: Callable[[], None],
        on_scroll_to: Callable[[str], bool],
        on_open_figure: Callable[[str], bool],
    ) -> None:
        self.bridge = DebugCommandBridge(on_reload=on_reload, on_scroll_to=on_scroll_to, on_open_figure=on_open_figure)
        self.log_handler = RingBufferLogHandler()
        logging.getLogger("limelight").addHandler(self.log_handler)
        handler_cls = _make_handler(self.bridge, self.log_handler)
        try:
            self._httpd = _StrictHTTPServer(("127.0.0.1", port), handler_cls)
        except OSError as error:
            logging.getLogger("limelight").removeHandler(self.log_handler)
            raise OSError(
                f"debug server could not bind to 127.0.0.1:{port} ({error}); "
                "is another --dbg-server already running on that port? pick a different --dbg-server PORT"
            ) from error
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="limelight-dbg-server", daemon=True)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> None:
        self._thread.start()
        logger.info("Debug server listening on http://127.0.0.1:%d", self.port)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)
        logging.getLogger("limelight").removeHandler(self.log_handler)
