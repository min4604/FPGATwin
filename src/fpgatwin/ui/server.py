"""Local web UI server.

Routes
  GET  /                -> static/index.html
  GET  /static/<file>   -> packaged static assets
  GET  /api/board       -> board definition + engine info (JSON)
  GET  /api/events      -> Server-Sent Events stream of engine snapshots
  POST /api/pin         -> {"pin": "D14", "value": 1} sets an input pin

Server -> browser updates use SSE; browser -> server input uses plain POST. Both are in the
standard library, so the UI adds no dependencies.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

from fpgatwin.engine import DemoCounterEngine, EngineRunner

_STATIC = resources.files("fpgatwin.ui").joinpath("static")
# Windows may map .js/.css to text/plain via the registry; browsers then refuse them.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
_SSE_INTERVAL_S = 1 / 30
_SSE_HEARTBEAT_S = 0.5


class UIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, board: dict[str, Any], runner: EngineRunner, port: int = 8600):
        self.board = board
        self.runner = runner
        self.shutting_down = threading.Event()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    def close(self) -> None:
        self.shutting_down.set()
        self.shutdown()
        self.server_close()
        self.runner.stop()


class _Handler(BaseHTTPRequestHandler):
    server: UIServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass  # keep the console quiet

    # ---- helpers ------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    # ---- GET ----------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path == "/api/board":
            engine = self.server.runner.engine
            return self._json(
                {
                    "board": self.server.board,
                    "engine": {
                        "name": engine.name,
                        "clock": engine.clock["name"],
                        "clock_hz": engine.clock_hz,
                    },
                }
            )
        if path == "/api/events":
            return self._events()
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _static(self, name: str) -> None:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        res = _STATIC.joinpath(name)
        if not res.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, res.read_bytes(), ctype)

    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        runner = self.server.runner
        last_pins: dict[str, int] | None = None
        last_sent = 0.0
        try:
            while not self.server.shutting_down.is_set():
                snap = runner.engine.snapshot()
                snap["rate_hz"] = runner.rate_hz
                now = time.monotonic()
                if snap["pins"] != last_pins or now - last_sent >= _SSE_HEARTBEAT_S:
                    self.wfile.write(f"data: {json.dumps(snap)}\n\n".encode())
                    self.wfile.flush()
                    last_pins = snap["pins"]
                    last_sent = now
                time.sleep(_SSE_INTERVAL_S)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # browser closed the stream

    # ---- POST ---------------------------------------------------------------
    def do_POST(self) -> None:
        if self.path != "/api/pin":
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            pin, value = str(data["pin"]), int(data["value"])
        except (ValueError, KeyError, TypeError):
            return self._error(HTTPStatus.BAD_REQUEST, "expected JSON {pin, value}")
        try:
            self.server.runner.engine.set_pin(pin, value)
        except KeyError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc.args[0]))
        self._json({"ok": True})


def create_server(
    board: dict[str, Any], port: int = 8600, speed: float = 1.0, clock: str | None = None
) -> UIServer:
    """Build a server with a running demo engine. Call ``serve_forever()`` then ``close()``."""
    runner = EngineRunner(DemoCounterEngine(board, clock=clock), speed=speed)
    server = UIServer(board, runner, port=port)
    runner.start()
    return server
