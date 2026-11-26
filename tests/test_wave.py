"""Waveform capture: VCD recording in the harness, the engine API and the HTTP endpoints."""

import re
from pathlib import Path

import pytest

from fpgatwin.board import load_board
from fpgatwin.rtl.toolchain import ToolchainError, find_toolchain

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sp601_uart_echo"
PS = 10**12
MS = PS // 1000

try:
    TOOLCHAIN = find_toolchain()
except ToolchainError as exc:  # pragma: no cover - depends on the machine
    TOOLCHAIN, REASON = None, str(exc)
else:
    REASON = ""

pytestmark = pytest.mark.skipif(TOOLCHAIN is None, reason=f"toolchain missing: {REASON}")


@pytest.fixture(scope="module")
def built():
    from fpgatwin.rtl.build import build

    return build(load_board("xilinx_sp601"), sorted(EXAMPLE.glob("*.v")),
                 EXAMPLE / "uart_echo.ucf", toolchain=TOOLCHAIN)


@pytest.fixture
def engine(built, tmp_path, monkeypatch):
    from fpgatwin.rtl.engine import VerilatorEngine

    eng = VerilatorEngine(load_board("xilinx_sp601"), built, echo_console=False)
    assert eng.status == "running", eng.message
    monkeypatch.setenv("FPGATWIN_CACHE", str(tmp_path))  # recordings go here (build is done)
    yield eng
    eng.close()


def parse_vcd(path):
    text = path.read_text(encoding="ascii")
    head, body = text.split("$enddefinitions $end", 1)
    names = re.findall(r"\$var \w+ \d+ \S+ (\S+)", head)
    times = [int(line[1:]) for line in body.splitlines() if line.startswith("#")]
    return head, names, times


def test_recording_has_all_signals_and_exact_span(engine):
    engine.advance_ps(MS)
    w = engine.wave_start(2)
    assert w["state"] == "recording" and w["start_ps"] == MS and w["length_ps"] == 2 * MS
    engine.advance_ps(MS)
    assert engine.snapshot()["wave"]["state"] == "recording"
    engine.advance_ps(2 * MS)  # passes the end: the harness closes the file itself
    w = engine.snapshot()["wave"]
    assert w["state"] == "done" and (w["start_ps"], w["end_ps"]) == (MS, 3 * MS)

    path = engine.wave_path(w["id"])
    assert path is not None and w["size"] == path.stat().st_size > 0
    head, names, times = parse_vcd(path)
    assert "$timescale 1ps $end" in head
    assert {"USER_CLOCK", "UART_TX", "GPIO_LED", "tx_busy", "shreg"} <= set(names)  # internals too
    assert times[0] == MS and times[-1] == 3 * MS
    assert len(times) == 2 * 54_000 + 1  # every edge of the 27 MHz clock, both ends included


def test_stop_early_and_stop_when_idle(engine):
    assert engine.wave_stop() == {"state": "idle"}
    w = engine.wave_start(50)
    engine.advance_ps(MS)
    done = engine.wave_stop()
    assert done["state"] == "done" and done["id"] == w["id"] and done["end_ps"] == MS
    assert engine.wave_stop() == done  # nothing recording any more

    second = engine.wave_start(1)  # a model can be traced again into a new file
    engine.advance_ps(2 * MS)
    assert engine.snapshot()["wave"]["state"] == "done"
    assert engine.wave_path(second["id"]) != engine.wave_path(w["id"])
    assert engine.uart_read("uart0", 0)[0] == b"Hello from FPGATwin!\r\n"  # still simulating


@pytest.mark.parametrize("ms", [0, -1, 101])
def test_bad_length_rejected(engine, ms):
    with pytest.raises(ValueError):
        engine.wave_start(ms)


def test_old_recordings_are_pruned(engine, tmp_path):
    from fpgatwin.rtl.engine import WAVE_KEEP

    ids = []
    for _ in range(WAVE_KEEP + 2):
        ids.append(engine.wave_start(0.01)["id"])
        engine.advance_ps(MS // 10)
    assert len(list((tmp_path / "waves").glob("wave-*.vcd"))) == WAVE_KEEP
    assert engine.wave_path(ids[0]) is None and engine.wave_path(ids[-1]) is not None


def test_ui_wave_api(built, tmp_path, monkeypatch):
    import http.client
    import json
    import threading
    import time

    from fpgatwin.rtl.engine import VerilatorEngine
    from fpgatwin.ui.server import create_server

    monkeypatch.setenv("FPGATWIN_CACHE", str(tmp_path))
    board = load_board("xilinx_sp601")
    srv = create_server(board, port=0, engine=VerilatorEngine(board, built, echo_console=False))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def call(method, path, body=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
        headers = {"X-FPGATwin-Token": srv.token} if token else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    try:
        assert call("POST", "/api/wave", {"action": "start", "ms": 500})[0].status == 400
        assert call("POST", "/api/wave", {"action": "jump"})[0].status == 400
        resp, data = call("POST", "/api/wave", {"action": "start", "ms": 1})
        assert resp.status == 200
        wid = json.loads(data)["wave"]["id"]

        deadline = time.monotonic() + 30
        while srv.runner.engine.snapshot()["wave"]["state"] != "done":
            assert time.monotonic() < deadline
            time.sleep(0.05)

        assert call("GET", f"/api/wave/{wid}.vcd", token=False)[0].status == 403
        assert call("GET", "/api/wave/0-0.vcd")[0].status == 404
        assert call("GET", "/api/wave/..%2fx.vcd")[0].status == 404
        resp, data = call("GET", f"/api/wave/{wid}.vcd")
        assert resp.status == 200
        assert "uart_echo" in resp.getheader("Content-Disposition")
        assert data.startswith(b"$version") and b"$enddefinitions" in data
        assert int(resp.getheader("Content-Length")) == len(data)
    finally:
        srv.close()
        thread.join(timeout=2)


def test_demo_engine_cannot_record():
    import http.client
    import json
    import threading

    from fpgatwin.ui.server import create_server

    srv = create_server(load_board("xilinx_sp601"), port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        conn.request("POST", "/api/wave", body=json.dumps({"action": "start", "ms": 1}),
                     headers={"Content-Type": "application/json", "X-FPGATwin-Token": srv.token})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 400
    finally:
        srv.close()
        thread.join(timeout=2)
