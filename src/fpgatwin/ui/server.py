"""Local web UI server.

Routes
  GET  /                -> static/index.html (with this session's token)
  GET  /static/<file>   -> packaged static assets
  GET  /api/board       -> board definition + engine info (JSON)
  GET  /api/events      -> Server-Sent Events stream of engine snapshots
  POST /api/pin         -> {"pin": "D14", "value": 1} sets an input pin
  POST /api/load        -> {"files": [{"name", "content"}], "top"?} builds and runs a design
  POST /api/demo        -> switch back to the demo engine
  POST /api/uart        -> {"id": "uart0", "data"?, "hex"?, "baud"?} send to the design / set baud

The event stream carries ``uart_rx: {id: hex}`` with the bytes each UART transmitted since the
previous event (a new connection first gets everything still buffered).

Security: the server binds 127.0.0.1 only, but any web page open in the browser can still send
requests to localhost. A design can run shell commands (``$system``), so every POST must carry
the per-session token that is embedded in our own index.html (``X-FPGATwin-Token``; a custom
header also forces a CORS preflight, which we never approve). The ``Host`` header is checked on
every request to defeat DNS rebinding.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from fpgatwin.engine import DemoCounterEngine, EngineRunner

_STATIC = resources.files("fpgatwin.ui").joinpath("static")
# Windows may map .js/.css to text/plain via the registry; browsers then refuse them.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
_SSE_INTERVAL_S = 1 / 30
_SSE_HEARTBEAT_S = 0.5

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_UPLOAD_FILES = 500
MAX_UART_SEND = 64 * 1024
_WAVE_PATH_RE = re.compile(r"^/api/wave/(\d+-\d+)\.vcd$")
_HDL_EXTS = {".v", ".sv", ".vh", ".svh", ".vinc", ".inc", ".mem", ".hex", ".dat"}
_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,200}$")


class UIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        board: dict[str, Any],
        runner: EngineRunner,
        port: int = 8600,
        loader: Callable[[list[Path], Path, str | None], Any] | None = None,
    ):
        self.board = board
        self.runner = runner
        self.speed = runner.speed
        self.loader = loader  # builds files into an engine; None disables /api/load
        self.token = secrets.token_urlsafe(24)
        self.shutting_down = threading.Event()
        self.build_lock = threading.Lock()
        self._serving = False
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def set_engine(self, engine: Any) -> None:
        """Swap in a new engine: start it, then stop (and close) the old one."""
        old = self.runner
        new = EngineRunner(engine, speed=self.speed)
        new.start()
        self.runner = new
        old.stop()

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._serving = True
        try:
            super().serve_forever(poll_interval)
        finally:
            self._serving = False

    def close(self) -> None:
        """Stop serving (if running), close the socket and stop the engine. Safe to call once
        from any thread except the one running ``serve_forever``."""
        self.shutting_down.set()
        if self._serving:  # shutdown() blocks forever if serve_forever never ran
            self.shutdown()
        self.server_close()
        self.runner.stop()


def engine_info(engine: Any) -> dict[str, Any]:
    return {
        "name": engine.name,
        "clock": engine.clock["name"],
        "clock_hz": engine.clock_hz,
        "connected": engine.connected_pins,
        **engine.info,
    }


def save_upload(files: list[dict[str, Any]], root: Path) -> tuple[list[Path], Path]:
    """Validate uploaded files, write them under *root* and return (hdl files, ucf file).

    The directory name is a hash of the contents, so the same upload maps to the same paths
    (and therefore the same cached build).
    """
    if not files:
        raise ValueError("no files selected")
    if len(files) > MAX_UPLOAD_FILES:
        raise ValueError(f"too many files (max {MAX_UPLOAD_FILES})")
    seen: set[str] = set()
    cleaned: list[tuple[str, bytes]] = []
    for f in files:
        name = str(f.get("name", ""))
        if not _NAME_RE.match(name) or name in (".", "..") or name.startswith("."):
            raise ValueError(f"invalid file name: {name!r}")
        if name.lower() in seen:
            raise ValueError(f"duplicate file name: {name}")
        seen.add(name.lower())
        cleaned.append((name, str(f.get("content", "")).encode("utf-8")))

    ucfs = [n for n, _ in cleaned if Path(n).suffix.lower() in {".ucf", ".xdc"}]
    if len(ucfs) != 1:
        raise ValueError("select exactly one .ucf / .xdc file" if ucfs
                         else "no .ucf / .xdc file selected")
    hdl = [n for n, _ in cleaned if Path(n).suffix.lower() in {".v", ".sv"}]
    if not hdl:
        raise ValueError("no .v / .sv files selected")
    others = [n for n, _ in cleaned
              if n not in ucfs and Path(n).suffix.lower() not in _HDL_EXTS]
    if others:
        raise ValueError(f"unsupported file type: {', '.join(others)}")

    h = hashlib.sha256()
    for name, data in sorted(cleaned):
        h.update(name.encode() + b"\0" + data + b"\0")
    d = root / h.hexdigest()[:16]
    d.mkdir(parents=True, exist_ok=True)
    for name, data in cleaned:
        (d / name).write_bytes(data)
    return [d / n for n in sorted(hdl)], d / ucfs[0]


class _Handler(BaseHTTPRequestHandler):
    server: UIServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass  # keep the console quiet

    # ---- helpers ------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = HTTPStatus.OK, close: bool = False) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8",
                   close)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        # An error may be sent before the request body was read (bad token, too large, ...).
        # Unread bytes would be parsed as the next request on this keep-alive connection, so
        # close it instead.
        self._json({"error": message, **extra}, status, close=True)

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") in self.server.allowed_hosts():
            return True
        self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        return False

    def _token_ok(self) -> bool:
        if secrets.compare_digest(self.headers.get("X-FPGATwin-Token", ""), self.server.token):
            return True
        self._error(HTTPStatus.FORBIDDEN, "missing or invalid token")
        return False

    def _read_json(self, limit: int) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length > limit:
            raise ValueError(f"request too large (max {limit // (1024 * 1024)} MB)")
        return json.loads(self.rfile.read(length) or b"{}")

    # ---- GET ----------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_ok():
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._index()
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path == "/api/board":
            return self._json({"board": self.server.board,
                               "engine": engine_info(self.server.runner.engine),
                               "can_load": self.server.loader is not None})
        if path == "/api/events":
            return self._events()
        m = _WAVE_PATH_RE.match(path)
        if m:
            return self._get_wave(m.group(1))
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _get_wave(self, wid: str) -> None:
        """Download a finished recording. Needs the token: it holds the design's signals."""
        if not self._token_ok():
            return
        engine = self.server.runner.engine
        path = engine.wave_path(wid) if hasattr(engine, "wave_path") else None
        if path is None:
            return self._error(HTTPStatus.NOT_FOUND, "no such recording (expired or not finished)")
        top = re.sub(r"[^A-Za-z0-9_.-]", "_", str(engine.info.get("top", "design")))
        with open(path, "rb") as f:
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{top}-{wid}.vcd"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            shutil.copyfileobj(f, self.wfile, 1024 * 1024)

    def _index(self) -> None:
        html = _STATIC.joinpath("index.html").read_text(encoding="utf-8")
        html = html.replace("@TOKEN@", self.server.token)
        self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, name: str) -> None:
        if not name or "/" in name or "\\" in name or name.startswith(".") or name == "index.html":
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

        last_pins: dict[str, int] | None = None
        last_engine = None
        last_wave = None
        last_sent = 0.0
        uart_seq: dict[str, int] = {}  # bytes already streamed, per UART of the current engine
        try:
            while not self.server.shutting_down.is_set():
                runner = self.server.runner  # may be swapped by /api/load
                engine = runner.engine
                if id(engine) != last_engine:
                    uart_seq = {}  # a new design: stream its UART output from the start
                snap = engine.snapshot()
                snap["rate_hz"] = runner.rate_hz
                snap["engine_id"] = id(engine)
                uart_rx = {}
                for uid, st in snap.get("uart", {}).items():
                    if st["seq"] > uart_seq.get(uid, 0):
                        data, uart_seq[uid] = engine.uart_read(uid, uart_seq.get(uid, 0))
                        uart_rx[uid] = data.hex()
                if uart_rx:
                    snap["uart_rx"] = uart_rx
                now = time.monotonic()
                wave = snap.get("wave")
                if (snap["pins"] != last_pins or id(engine) != last_engine or uart_rx
                        or wave != last_wave or now - last_sent >= _SSE_HEARTBEAT_S):
                    self.wfile.write(f"data: {json.dumps(snap)}\n\n".encode())
                    self.wfile.flush()
                    last_pins = snap["pins"]
                    last_engine = id(engine)
                    last_wave = wave
                    last_sent = now
                time.sleep(_SSE_INTERVAL_S)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # browser closed the stream

    # ---- POST ---------------------------------------------------------------
    def do_POST(self) -> None:
        if not self._host_ok() or not self._token_ok():
            return
        if self.path == "/api/pin":
            return self._post_pin()
        if self.path == "/api/load":
            return self._post_load()
        if self.path == "/api/demo":
            return self._post_demo()
        if self.path == "/api/uart":
            return self._post_uart()
        if self.path == "/api/wave":
            return self._post_wave()
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _post_wave(self) -> None:
        """{"action": "start", "ms": 10} or {"action": "stop"}"""
        try:
            req = self._read_json(64 * 1024)
            action = str(req["action"])
            ms = float(req.get("ms", 0)) if action == "start" else 0.0
            if action not in ("start", "stop"):
                raise ValueError("action must be start or stop")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, f"bad waveform request: {exc}")
        engine = self.server.runner.engine
        if not hasattr(engine, "wave_start"):
            return self._error(HTTPStatus.BAD_REQUEST, "the current engine cannot record waveforms")
        try:
            wave = engine.wave_start(ms) if action == "start" else engine.wave_stop()
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except RuntimeError as exc:
            return self._error(HTTPStatus.CONFLICT, str(exc))
        self._json({"ok": True, "wave": wave})

    def _post_uart(self) -> None:
        """{"id": "uart0", "data"?: "text", "hex"?: "0d0a", "baud"?: 115200}"""
        try:
            req = self._read_json(256 * 1024)
            uid = str(req["id"])
            payload = b""
            if "data" in req:
                payload += str(req["data"]).encode("utf-8")
            if "hex" in req:
                payload += bytes.fromhex(str(req["hex"]))
            baud = req.get("baud")
            if baud is not None:
                baud = int(baud)
                if not 300 <= baud <= 4_000_000:
                    raise ValueError("baud must be 300 .. 4000000")
            if len(payload) > MAX_UART_SEND:
                raise ValueError(f"at most {MAX_UART_SEND} bytes per request")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, f"bad UART request: {exc}")
        engine = self.server.runner.engine
        if not hasattr(engine, "uart_send"):
            return self._error(HTTPStatus.BAD_REQUEST, "the current engine has no UART")
        try:
            if baud is not None:
                engine.uart_set_baud(uid, baud)
            if payload:
                engine.uart_send(uid, payload)
        except KeyError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc.args[0]))
        self._json({"ok": True, "sent": len(payload)})

    def _post_pin(self) -> None:
        try:
            data = self._read_json(64 * 1024)
            pin, value = str(data["pin"]), int(data["value"])
        except (ValueError, KeyError, TypeError):
            return self._error(HTTPStatus.BAD_REQUEST, "expected JSON {pin, value}")
        try:
            self.server.runner.engine.set_pin(pin, value)
        except KeyError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc.args[0]))
        self._json({"ok": True})

    def _post_load(self) -> None:
        if self.server.loader is None:
            return self._error(HTTPStatus.NOT_IMPLEMENTED, "loading designs is not available")
        try:
            data = self._read_json(MAX_UPLOAD_BYTES)
            files = data.get("files") or []
            top = (str(data.get("top") or "").strip()) or None
            if top and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
                raise ValueError(f"invalid top module name: {top!r}")
        except (ValueError, TypeError, AttributeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        if not self.server.build_lock.acquire(blocking=False):
            return self._error(HTTPStatus.CONFLICT, "another build is in progress")
        try:
            try:
                from fpgatwin.rtl.build import cache_root

                hdl, ucf = save_upload(files, cache_root() / "uploads")
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            try:
                engine = self.server.loader(hdl, ucf, top)
            except Exception as exc:  # noqa: BLE001 - BuildError, ToolchainError, ... all go to the UI
                # show "blinky.v:1:36" instead of the full path of the upload directory
                # (match on the unique hash dir name: packaged apps may see a redirected path)
                prefix = re.compile(r"[^\s'\"]*[\\/]" + re.escape(ucf.parent.name) + r"[\\/]")
                log = prefix.sub("", str(getattr(exc, "log", "")))
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, prefix.sub("", str(exc)),
                                   log=log)
            self.server.set_engine(engine)
            report: dict[str, Any] = {}
            result = getattr(engine, "result", None)
            if result is not None:
                report = {"notes": result.mapping.notes, "warnings": result.mapping.warnings,
                          "cached": result.cached}
            self._json({"ok": True, "engine": engine_info(engine), **report})
        finally:
            self.server.build_lock.release()

    def _post_demo(self) -> None:
        engine = DemoCounterEngine(self.server.board)
        self.server.set_engine(engine)
        self._json({"ok": True, "engine": engine_info(engine)})


def default_loader(board: dict[str, Any]) -> Callable[[list[Path], Path, str | None], Any]:
    """Loader that builds with Verilator and returns a running VerilatorEngine."""

    def load(hdl: list[Path], ucf: Path, top: str | None) -> Any:
        from fpgatwin.rtl.build import build
        from fpgatwin.rtl.engine import VerilatorEngine

        result = build(board, hdl, ucf, top=top)
        engine = VerilatorEngine(board, result)
        if engine.status != "running":
            engine.close()
            raise RuntimeError(engine.message)
        return engine

    return load


def create_server(
    board: dict[str, Any],
    port: int = 8600,
    speed: float = 1.0,
    clock: str | None = None,
    engine: Any = None,
    loader: Callable[[list[Path], Path, str | None], Any] | None = None,
) -> UIServer:
    """Build a server and start its engine (the demo engine unless *engine* is given).

    *loader* enables ``POST /api/load``; pass :func:`default_loader` for the real build.
    Call ``serve_forever()`` then ``close()``.
    """
    runner = EngineRunner(engine or DemoCounterEngine(board, clock=clock), speed=speed)
    server = UIServer(board, runner, port=port, loader=loader)
    runner.start()
    return server
