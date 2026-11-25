import http.client
import json
import threading

import pytest

from fpgatwin.board import load_board
from fpgatwin.ui.server import create_server


@pytest.fixture
def server():
    srv = create_server(load_board("xilinx_sp601"), port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.close()
    thread.join(timeout=2)


def request(srv, method, path, body=None, token=True, headers=None):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=60)
    hdrs = {"Content-Type": "application/json"} if body is not None else {}
    if token and method == "POST":
        hdrs["X-FPGATwin-Token"] = srv.token
    hdrs.update(headers or {})
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_index_and_static(server):
    resp, data = request(server, "GET", "/")
    assert resp.status == 200
    assert b"/static/app.js" in data
    for name, ctype in [("app.js", "javascript"), ("style.css", "text/css")]:
        resp, _ = request(server, "GET", f"/static/{name}")
        assert resp.status == 200
        assert ctype in resp.getheader("Content-Type")


def test_static_rejects_traversal(server):
    resp, _ = request(server, "GET", "/static/../server.py")
    assert resp.status == 404


def test_board_api(server):
    resp, data = request(server, "GET", "/api/board")
    assert resp.status == 200
    payload = json.loads(data)
    assert payload["board"]["board"]["id"] == "xilinx_sp601"
    assert payload["engine"]["name"] == "demo"


def test_set_pin(server):
    resp, _ = request(server, "POST", "/api/pin", {"pin": "D14", "value": 1})
    assert resp.status == 200
    assert server.runner.engine.snapshot()["pins"]["D14"] == 1


def test_set_pin_rejects_bad_input(server):
    resp, _ = request(server, "POST", "/api/pin", {"pin": "E13", "value": 1})
    assert resp.status == 400
    resp, _ = request(server, "POST", "/api/pin", {"oops": 1})
    assert resp.status == 400


def test_event_stream_sends_snapshot(server):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", "/api/events")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith("text/event-stream")
    line = resp.fp.readline().decode()
    assert line.startswith("data: ")
    snap = json.loads(line[len("data: ") :])
    assert snap["engine"] == "demo"
    assert "E13" in snap["pins"]
    conn.close()


# ---- security ---------------------------------------------------------------


def test_index_embeds_token(server):
    _, data = request(server, "GET", "/")
    assert server.token.encode() in data
    assert b"@TOKEN@" not in data


def test_post_requires_token(server):
    resp, _ = request(server, "POST", "/api/pin", {"pin": "D14", "value": 1}, token=False)
    assert resp.status == 403
    resp, _ = request(server, "POST", "/api/pin", {"pin": "D14", "value": 1}, token=False,
                      headers={"X-FPGATwin-Token": "wrong"})
    assert resp.status == 403
    assert server.runner.engine.snapshot()["pins"]["D14"] == 0


def test_rejected_post_does_not_poison_keepalive(server):
    """An error sent before the body is read must close the connection; otherwise the unread
    body is parsed as the start of the next request (this broke the next UI click)."""
    import socket

    host, port = server.server_address[:2]
    body = b'{"pin": "D14", "value": 1}'
    req = (b"POST /api/pin HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: application/json\r\n"
           b"Content-Length: %d\r\n\r\n" % (host.encode(), port, len(body))) + body
    with socket.create_connection((host, port), timeout=10) as s:
        s.sendall(req)  # no token -> 403 without reading the body
        s.sendall(req.replace(b"Content-Type", b"X-FPGATwin-Token: %s\r\nContent-Type"
                              % server.token.encode()))
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    head = data.split(b"\r\n\r\n", 1)[0]
    assert head.startswith(b"HTTP/1.1 403")
    assert b"Connection: close" in head
    assert data.count(b"HTTP/1.") == 1  # the leftover body was never parsed as a request


def test_bad_host_rejected(server):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends Host: evil.example."""
    for method in ("GET", "POST"):
        resp, _ = request(server, method, "/api/board" if method == "GET" else "/api/pin",
                          {"pin": "D14", "value": 1} if method == "POST" else None,
                          headers={"Host": f"evil.example:{server.port}"})
        assert resp.status == 403


def test_localhost_host_allowed(server):
    resp, _ = request(server, "GET", "/api/board", headers={"Host": f"localhost:{server.port}"})
    assert resp.status == 200


# ---- loading designs ------------------------------------------------------------


def test_load_disabled_without_loader(server):
    _, data = request(server, "GET", "/api/board")
    assert json.loads(data)["can_load"] is False
    resp, _ = request(server, "POST", "/api/load", {"files": []})
    assert resp.status == 501


class _FakeEngine:
    """Stands in for VerilatorEngine so the upload path is tested without a toolchain."""

    name = "fake"
    clock_hz = 27_000_000

    def __init__(self):
        self.clock = {"name": "USER_CLOCK", "freq_hz": 27_000_000}
        self.connected_pins = ["E13"]
        self.info = {"top": "blinky"}
        self.closed = False

    def set_pin(self, pin, value):
        pass

    def advance_ps(self, ps):
        pass

    def snapshot(self):
        return {"engine": "fake", "status": "running", "message": "", "pins": {"E13": 1},
                "cycles": 0, "sim_time_ps": 0}

    def close(self):
        self.closed = True


@pytest.fixture
def loading_server(monkeypatch, tmp_path):
    monkeypatch.setenv("FPGATWIN_CACHE", str(tmp_path / "cache"))
    calls = []

    def loader(hdl, ucf, top):
        calls.append((hdl, ucf, top))
        if top == "boom":
            raise RuntimeError("build exploded")
        return _FakeEngine()

    srv = create_server(load_board("xilinx_sp601"), port=0, loader=loader)
    srv.calls = calls
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.close()
    thread.join(timeout=2)


FILES = [{"name": "blinky.v", "content": "module blinky; endmodule\n"},
         {"name": "inc.vh", "content": "`define X 1\n"},
         {"name": "pins.ucf", "content": 'NET "LED<0>" LOC = "E13";\n'}]


def test_load_swaps_engine(loading_server):
    srv = loading_server
    old = srv.runner
    resp, data = request(srv, "POST", "/api/load", {"files": FILES, "top": "blinky"})
    assert resp.status == 200, data
    assert json.loads(data)["engine"]["name"] == "fake"
    assert srv.runner is not old and srv.runner.engine.name == "fake"
    assert old._thread is None or not old._thread.is_alive()
    hdl, ucf, top = srv.calls[0]
    assert [p.name for p in hdl] == ["blinky.v"] and ucf.name == "pins.ucf" and top == "blinky"
    assert (hdl[0].parent / "inc.vh").read_text() == "`define X 1\n"
    _, data = request(srv, "GET", "/api/board")
    assert json.loads(data)["engine"]["name"] == "fake"

    resp, data = request(srv, "POST", "/api/demo", {})
    assert resp.status == 200
    assert srv.runner.engine.name == "demo"


@pytest.mark.parametrize("files, message", [
    ([], "no files"),
    ([FILES[0]], "no .ucf"),
    ([FILES[0], FILES[2], {"name": "b.ucf", "content": ""}], "exactly one"),
    ([FILES[2]], "no .v"),
    ([FILES[0], FILES[2], {"name": "../evil.v", "content": ""}], "invalid file name"),
    ([FILES[0], FILES[2], {"name": r"sub\evil.v", "content": ""}], "invalid file name"),
    ([FILES[0], FILES[2], {"name": "run.bat", "content": ""}], "unsupported"),
    ([FILES[0], FILES[2], {"name": "BLINKY.v", "content": ""}], "duplicate"),
])
def test_load_rejects_bad_uploads(loading_server, files, message):
    resp, data = request(loading_server, "POST", "/api/load", {"files": files})
    assert resp.status == 400
    assert message in json.loads(data)["error"]
    assert loading_server.calls == []
    assert loading_server.runner.engine.name == "demo"


def test_load_rejects_bad_top(loading_server):
    resp, _ = request(loading_server, "POST", "/api/load", {"files": FILES, "top": "a b"})
    assert resp.status == 400


def test_load_build_failure_keeps_engine(loading_server):
    resp, data = request(loading_server, "POST", "/api/load", {"files": FILES, "top": "boom"})
    assert resp.status == 422
    assert "build exploded" in json.loads(data)["error"]
    assert loading_server.runner.engine.name == "demo"
