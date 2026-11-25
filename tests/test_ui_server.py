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


def request(srv, method, path, body=None):
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
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
