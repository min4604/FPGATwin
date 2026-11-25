"""UART model: mapping (no toolchain) and the harness bridge (Verilator integration)."""

from pathlib import Path

import pytest

from fpgatwin.board import load_board
from fpgatwin.constraints.ucf import parse_ucf_text
from fpgatwin.rtl.mapping import build_mapping
from fpgatwin.rtl.ports import Port
from fpgatwin.rtl.toolchain import ToolchainError, find_toolchain

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sp601_uart_echo"
PS = 10**12
GREETING = b"Hello from FPGATwin!\r\n"


def test_mapping_finds_uart_pins():
    ports = [Port("clk", "in", 0, 0, False), Port("rx", "in", 0, 0, False),
             Port("tx", "out", 0, 0, False), Port("cts", "in", 0, 0, False),
             Port("rts", "out", 0, 0, False)]
    ucf = parse_ucf_text('NET clk LOC=V10; NET rx LOC=K14; NET tx LOC=L12; NET cts LOC=T5;'
                         ' NET rts LOC=U10;')
    mp = build_mapping(load_board("xilinx_sp601"), ports, ucf)
    assert mp.warnings == [] and mp.errors == []
    (u,) = mp.uarts
    assert (u.id, u.tx, u.rx) == ("uart0", (2, 0), (1, 0))
    assert {"K14", "L12", "T5", "U10"} <= set(mp.connected_pins)


def test_mapping_wrong_direction_warns():
    ports = [Port("tx", "in", 0, 0, False)]  # an input on the FPGA's TX pin
    mp = build_mapping(load_board("xilinx_sp601"), ports, parse_ucf_text("NET tx LOC=L12;"))
    assert mp.uarts == []
    assert any("not simulated" in w for w in mp.warnings)


# ---- integration --------------------------------------------------------------------

try:
    TOOLCHAIN = find_toolchain()
except ToolchainError as exc:  # pragma: no cover - depends on the machine
    TOOLCHAIN, REASON = None, str(exc)
else:
    REASON = ""

needs_toolchain = pytest.mark.skipif(TOOLCHAIN is None, reason=f"toolchain missing: {REASON}")


@pytest.fixture(scope="module")
def built():
    from fpgatwin.rtl.build import build

    return build(load_board("xilinx_sp601"), sorted(EXAMPLE.glob("*.v")),
                 EXAMPLE / "uart_echo.ucf", toolchain=TOOLCHAIN)


@pytest.fixture
def engine(built):
    from fpgatwin.rtl.engine import VerilatorEngine

    eng = VerilatorEngine(load_board("xilinx_sp601"), built, echo_console=False)
    assert eng.status == "running", eng.message
    yield eng
    eng.close()


def ms(n):
    return n * PS // 1000


@needs_toolchain
def test_greeting_then_echo(engine):
    assert engine.info["uarts"] == [{"id": "uart0", "label": "CP2103GM", "tx": True, "rx": True,
                                     "baud": 115200}]
    engine.advance_ps(ms(3))  # 22 bytes * 86.8 us
    data, seq = engine.uart_read("uart0", 0)
    assert data == GREETING and seq == len(GREETING)

    msg = "abc 你好\r\n".encode()
    engine.uart_send("uart0", msg)
    engine.advance_ps(ms(2))
    data, seq2 = engine.uart_read("uart0", seq)
    assert data == msg and seq2 == seq + len(msg)
    assert engine.snapshot()["uart"]["uart0"] == {"seq": seq2, "errors": 0, "baud": 115200}
    leds = [engine.snapshot()["pins"][p] for p in ("E13", "C14", "C4", "A4")]
    assert leds == [0, 1, 0, 1]  # last byte '\n' = 0x0a


@needs_toolchain
def test_bytes_split_across_runs(engine):
    """A frame that straddles many short run commands still decodes."""
    for _ in range(3000):
        engine.advance_ps(PS // 1_000_000)  # 1 us steps, 3 ms total
    assert engine.uart_read("uart0", 0)[0] == GREETING


@needs_toolchain
def test_wrong_baud_gives_framing_errors(engine):
    engine.uart_set_baud("uart0", 57600)  # design sends at 115200
    engine.advance_ps(ms(3))
    data, _ = engine.uart_read("uart0", 0)
    snap = engine.snapshot()["uart"]["uart0"]
    assert data != GREETING
    assert snap["errors"] > 0 and snap["baud"] == 57600


@needs_toolchain
def test_reset_resends_greeting(engine):
    engine.advance_ps(ms(3))
    engine.set_pin("N4", 1)
    engine.advance_ps(ms(1) // 10)
    engine.set_pin("N4", 0)
    engine.advance_ps(ms(3))
    assert engine.uart_read("uart0", 0)[0] == GREETING * 2


@needs_toolchain
def test_uart_errors_for_unknown_id(engine):
    with pytest.raises(KeyError):
        engine.uart_send("uart9", b"x")


@needs_toolchain
def test_ui_uart_api(built, tmp_path):
    """POST /api/uart reaches the design and the event stream carries its reply."""
    import http.client
    import json
    import threading
    import time

    from fpgatwin.rtl.engine import VerilatorEngine
    from fpgatwin.ui.server import create_server

    board = load_board("xilinx_sp601")
    srv = create_server(board, port=0, engine=VerilatorEngine(board, built, echo_console=False))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        def post(body):
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            conn.request("POST", "/api/uart", body=json.dumps(body),
                         headers={"Content-Type": "application/json",
                                  "X-FPGATwin-Token": srv.token})
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return resp.status

        assert post({"id": "uart0", "baud": 1}) == 400
        assert post({"id": "nope", "data": "x"}) == 400
        deadline = time.monotonic() + 30
        while srv.runner.engine.snapshot()["uart"]["uart0"]["seq"] < len(GREETING):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert post({"id": "uart0", "data": "ping"}) == 200

        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        got = b""
        while b"ping" not in got:
            assert time.monotonic() < deadline
            line = resp.fp.readline()
            if line.startswith(b"data: "):
                snap = json.loads(line[6:])
                got += bytes.fromhex(snap.get("uart_rx", {}).get("uart0", ""))
        conn.close()
        assert got == GREETING + b"ping"  # a new stream first replays the buffered output
    finally:
        srv.close()
        thread.join(timeout=2)
