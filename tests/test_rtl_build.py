"""Integration tests: build examples/sp601_blinky with Verilator and run it.

Skipped automatically when Verilator or a C++ compiler is not available.
"""

import shutil
from pathlib import Path

import pytest

from fpgatwin.board import load_board
from fpgatwin.rtl.toolchain import ToolchainError, find_toolchain

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sp601_blinky"
LEDS = ["E13", "C14", "C4", "A4"]
F_USER_CLOCK = 27_000_000
PS = 10**12

try:
    TOOLCHAIN = find_toolchain()
except ToolchainError as exc:  # pragma: no cover - depends on the machine
    TOOLCHAIN = None
    REASON = str(exc)
else:
    REASON = ""

pytestmark = pytest.mark.skipif(TOOLCHAIN is None, reason=f"toolchain missing: {REASON}")


def led_value(engine):
    pins = engine.snapshot()["pins"]
    return sum(pins[p] << i for i, p in enumerate(LEDS))


def ps_for_rising_edges(n):
    """Sim time of the n-th rising edge of USER_CLOCK.

    The clock starts low; edge k is at floor(k * 1e12 / (2 f)) ps and odd edges rise, so the
    n-th rising edge is edge 2n-1.
    """
    return ((2 * n - 1) * PS) // (2 * F_USER_CLOCK)


@pytest.fixture(scope="module")
def built():
    from fpgatwin.rtl.build import build

    return build(load_board("xilinx_sp601"), [EXAMPLE / "blinky.v"], EXAMPLE / "blinky.ucf",
                 toolchain=TOOLCHAIN)


@pytest.fixture
def engine(built):
    from fpgatwin.rtl.engine import VerilatorEngine

    eng = VerilatorEngine(load_board("xilinx_sp601"), built, echo_console=False)
    assert eng.status == "running", eng.message
    yield eng
    eng.close()


def test_mapping(built):
    assert built.top == "blinky"
    assert [c.clock for c in built.mapping.clocks] == ["USER_CLOCK"]
    assert sorted(built.mapping.connected_pins) == sorted(LEDS + ["N4", "D14", "E12", "F12", "V13"])


def test_counts_exactly(engine):
    # LED = counter[25:22]; after 5 * 2^22 rising edges the LEDs read 5
    engine.advance_ps(ps_for_rising_edges(5 << 22) - 1)
    assert led_value(engine) == 4  # one edge short
    engine.advance_ps(1)
    assert led_value(engine) == 5


def test_pause_and_reset(engine):
    engine.advance_ps(ps_for_rising_edges(3 << 22))
    assert led_value(engine) == 3
    engine.set_pin("D14", 1)  # SW8.1 pauses
    engine.advance_ps(PS // 2)
    assert led_value(engine) == 3
    engine.set_pin("D14", 0)
    engine.set_pin("N4", 1)  # hold CPU_RESET
    engine.advance_ps(PS // 1000)
    assert led_value(engine) == 0
    engine.set_pin("N4", 0)
    t0 = engine.snapshot()["sim_time_ps"]
    engine.advance_ps(ps_for_rising_edges(2 << 22))
    assert led_value(engine) in (1, 2)  # reset released mid-period
    assert engine.snapshot()["sim_time_ps"] > t0


def test_unconnected_board_input_is_accepted(engine):
    engine.set_pin("P4", 1)  # GPIO_BUTTON_0 is not used by blinky


@pytest.mark.parametrize("dirname", ["my design", "我的 設計"])
def test_path_with_spaces_and_cjk(tmp_path, dirname):
    from fpgatwin.rtl.build import build

    d = tmp_path / dirname
    d.mkdir()
    shutil.copy(EXAMPLE / "blinky.v", d / "blinky.v")
    shutil.copy(EXAMPLE / "blinky.ucf", d / "腳位 設定.ucf")
    result = build(load_board("xilinx_sp601"), [d / "blinky.v"], d / "腳位 設定.ucf",
                   toolchain=TOOLCHAIN)
    assert result.exe.is_file()


def test_non_ascii_dir_without_short_names_is_staged(tmp_path, monkeypatch):
    """Simulate a volume with 8.3 names disabled: sources get copied into the build dir."""
    import fpgatwin.rtl.build as b

    real = b.native_path
    monkeypatch.setattr(b, "native_path", lambda p: real(p) if str(p).isascii() else None)
    monkeypatch.setenv("FPGATWIN_CACHE", str(tmp_path / "cache"))
    d = tmp_path / "設計"
    d.mkdir()
    shutil.copy(EXAMPLE / "blinky.v", d / "blinky.v")
    shutil.copy(EXAMPLE / "blinky.ucf", d / "blinky.ucf")
    result = b.build(load_board("xilinx_sp601"), [d / "blinky.v"], d / "blinky.ucf",
                     toolchain=TOOLCHAIN)
    assert result.exe.is_file()
    assert (result.build_dir / "stage" / "d0" / "blinky.v").is_file()


def test_differential_clock_through_ibufgds(tmp_path):
    """SYSCLK_P/N (200 MHz) -> IBUFGDS primitive -> counter on the LEDs."""
    from fpgatwin.rtl.build import build
    from fpgatwin.rtl.engine import VerilatorEngine

    v = tmp_path / "diff.v"
    v.write_text(
        "module diff(input wire SYSCLK_P, input wire SYSCLK_N, output wire [3:0] LED);\n"
        "  wire clk;\n"
        "  IBUFGDS u_buf (.I(SYSCLK_P), .IB(SYSCLK_N), .O(clk));\n"
        "  reg [3:0] n = 0;\n"
        "  always @(posedge clk) n <= n + 1;\n"
        "  assign LED = n;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    ucf = tmp_path / "diff.ucf"
    ucf.write_text(
        'NET "SYSCLK_P" LOC = "K15";\nNET "SYSCLK_N" LOC = "K16";\n'
        + "".join(f'NET "LED<{i}>" LOC = "{p}";\n' for i, p in enumerate(LEDS)),
        encoding="utf-8",
    )
    board = load_board("xilinx_sp601")
    result = build(board, [v], ucf, toolchain=TOOLCHAIN)
    assert sorted((c.clock, c.inverted) for c in result.mapping.clocks) == [
        ("SYSCLK", False), ("SYSCLK", True)]
    eng = VerilatorEngine(board, result, echo_console=False)
    try:
        # 200 MHz: rising edge n at (2n-1) * 2500 ps
        eng.advance_ps(9 * 2500 - 1)  # just before the 5th rising edge
        assert led_value(eng) == 4
        eng.advance_ps(1)
        assert led_value(eng) == 5
    finally:
        eng.close()


def test_display_and_finish(tmp_path):
    """$display output is captured as console text; $finish stops the engine cleanly."""
    from fpgatwin.rtl.build import build
    from fpgatwin.rtl.engine import VerilatorEngine

    v = tmp_path / "fin.v"
    v.write_text(
        "module fin(input wire clk, output reg led = 0);\n"
        "  integer n = 0;\n"
        "  always @(posedge clk) begin\n"
        "    n <= n + 1; led <= 1;\n"
        "    if (n == 2) $write(\"no newline \");\n"
        "    if (n == 3) $display(\"hello from rtl\");\n"
        "    if (n == 9) $finish;\n"
        "  end\n"
        "endmodule\n",
        encoding="utf-8",
    )
    ucf = tmp_path / "fin.ucf"
    ucf.write_text('NET "clk" LOC = "V10";\nNET "led" LOC = "E13";\n', encoding="utf-8")
    board = load_board("xilinx_sp601")
    eng = VerilatorEngine(board, build(board, [v], ucf, toolchain=TOOLCHAIN), echo_console=False)
    try:
        eng.advance_ps(PS // 1000)  # 1 us = 27 cycles, past the $finish
        snap = eng.snapshot()
        assert snap["status"] == "stopped"
        assert "$finish" in snap["message"]
        assert any("hello from rtl" in line for line in eng.console)
        assert any("no newline" in line for line in eng.console)
        eng.advance_ps(PS)  # no-op after stop, must not hang
        eng.set_pin("D14", 1)
    finally:
        eng.close()


def test_cache_tracks_unlisted_submodules(tmp_path):
    """Editing a submodule that Verilator finds via -I must invalidate the cached build."""
    from fpgatwin.rtl.build import build

    (tmp_path / "top.v").write_text(
        "module top(output wire [3:0] LED); sub u(.LED(LED)); endmodule\n", encoding="utf-8")
    sub = tmp_path / "sub.v"
    sub.write_text("module sub(output wire [3:0] LED); assign LED = 4'd1; endmodule\n",
                   encoding="utf-8")
    ucf = tmp_path / "t.ucf"
    ucf.write_text('NET "LED<0>" LOC = "E13";\n', encoding="utf-8")
    board = load_board("xilinx_sp601")
    first = build(board, [tmp_path / "top.v"], ucf, toolchain=TOOLCHAIN)
    again = build(board, [tmp_path / "top.v"], ucf, toolchain=TOOLCHAIN)
    assert again.cached and again.exe == first.exe
    sub.write_text("module sub(output wire [3:0] LED); assign LED = 4'd2; endmodule\n",
                   encoding="utf-8")
    changed = build(board, [tmp_path / "top.v"], ucf, toolchain=TOOLCHAIN)
    assert not changed.cached and changed.exe != first.exe


def test_server_close_stops_sim_process(built):
    from fpgatwin.rtl.engine import VerilatorEngine
    from fpgatwin.ui.server import create_server

    board = load_board("xilinx_sp601")
    eng = VerilatorEngine(board, built, echo_console=False)
    srv = create_server(board, port=0, engine=eng)
    proc = eng._proc
    assert proc.poll() is None
    srv.close()
    assert proc.wait(timeout=5) is not None


def test_build_error_is_reported(tmp_path):
    from fpgatwin.rtl.build import BuildError, build

    bad = tmp_path / "bad.v"
    bad.write_text("module bad(input a; endmodule\n", encoding="utf-8")
    ucf = tmp_path / "bad.ucf"
    ucf.write_text('NET "a" LOC = "D14";\n', encoding="utf-8")
    with pytest.raises(BuildError) as exc:
        build(load_board("xilinx_sp601"), [bad], ucf, toolchain=TOOLCHAIN)
    assert "Verilator failed" in str(exc.value)
    assert "%Error" in exc.value.log


def test_ui_upload_builds_and_runs(tmp_path, monkeypatch):
    """POST /api/load with the blinky sources builds them and swaps in a running Verilator engine."""
    import http.client
    import json
    import threading

    from fpgatwin.ui.server import create_server, default_loader

    monkeypatch.setenv("FPGATWIN_CACHE", str(tmp_path / "cache"))
    board = load_board("xilinx_sp601")
    srv = create_server(board, port=0, loader=default_loader(board))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        files = [{"name": n, "content": (EXAMPLE / n).read_text(encoding="utf-8")}
                 for n in ("blinky.v", "blinky.ucf")]
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=300)
        conn.request("POST", "/api/load", body=json.dumps({"files": files}),
                     headers={"Content-Type": "application/json", "X-FPGATwin-Token": srv.token})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        assert resp.status == 200, data
        assert data["engine"]["name"] == "verilator" and data["engine"]["top"] == "blinky"
        assert any("USER_CLOCK" in n for n in data["notes"])
        eng = srv.runner.engine
        assert eng.name == "verilator" and eng.status == "running"
        proc = eng._proc
    finally:
        srv.close()
        thread.join(timeout=2)
    assert proc.wait(timeout=5) is not None


def test_top_name_is_detected_and_xdc_works(tmp_path):
    """The reported top is the module Verilator picked, not the first file's name."""
    from fpgatwin.rtl.build import build

    (tmp_path / "a_child.v").write_text(
        "module a_child(input wire clk, output reg q = 0); always @(posedge clk) q <= ~q; endmodule\n",
        encoding="utf-8")
    (tmp_path / "z_top.v").write_text(
        "module z_top(input wire clk, output wire led); a_child u(.clk(clk), .q(led)); endmodule\n",
        encoding="utf-8")
    xdc = tmp_path / "pins.ucf"  # XDC syntax in a .ucf file
    xdc.write_text("set_property PACKAGE_PIN V10 [get_ports clk]\r\n"
                   "#NET led LOC = E13;\r\n"
                   "set_property PACKAGE_PIN E13 [get_ports led]\r\n", encoding="utf-8")
    result = build(load_board("xilinx_sp601"), [tmp_path / "a_child.v", tmp_path / "z_top.v"], xdc,
                   toolchain=TOOLCHAIN)
    assert result.top == "z_top"
    assert result.mapping.warnings == []
    assert sorted(result.mapping.connected_pins) == ["E13"]
