import pytest

from fpgatwin.board import load_board
from fpgatwin.constraints.ucf import parse_ucf_text
from fpgatwin.rtl.mapping import build_mapping
from fpgatwin.rtl.ports import Port, parse_model_header

HEADER = """
class alignas(VL_CACHE_LINE_BYTES) Vfpgatwin VL_NOT_FINAL : public VerilatedModel {
    VL_IN8(&SYSCLK_P,0,0);
    VL_IN8(&SYSCLK_N,0,0);
    VL_IN8(&RST,0,0);
    VL_IN8(&SW,4,1);
    VL_OUT8(&LED,3,0);
    VL_OUT(&COUNT,31,0);
    VL_INW(&BIG,127,0,4);
    VL_INOUT8(&SDA,0,0);
"""


def test_parse_model_header():
    ports = parse_model_header(HEADER)
    assert [p.name for p in ports] == ["SYSCLK_P", "SYSCLK_N", "RST", "SW", "LED", "COUNT", "BIG",
                                       "SDA"]
    sw = ports[3]
    assert (sw.direction, sw.msb, sw.lsb, sw.width) == ("in", 4, 1, 4)
    assert sw.bit_offset(1) == 0 and sw.bit_offset(4) == 3 and sw.bit_offset(5) is None
    assert ports[6].wide and ports[7].direction == "inout"


@pytest.fixture
def board():
    return load_board("xilinx_sp601")


def _ports():
    return [p for p in parse_model_header(HEADER) if p.name not in ("BIG", "SDA")]


UCF = """
NET "SYSCLK_P" LOC = "K15";
NET "SYSCLK_N" LOC = "K16";
NET "RST"      LOC = "N4";
NET "SW<1>"    LOC = "D14";
NET "SW<2>"    LOC = "E12";
NET "SW<3>"    LOC = "A3";   # not modelled on the board
NET "LED<0>"   LOC = "E13";
NET "LED<1>"   LOC = "C14";
NET "LED<2>"   LOC = "C4";
NET "LED<3>"   LOC = "A4";
NET "EXTRA"    LOC = "P4";
"""


def test_mapping(board):
    mp = build_mapping(board, _ports(), parse_ucf_text(UCF))
    assert mp.errors == []
    clocks = {(c.port, c.clock, c.freq_hz, c.inverted) for c in mp.clocks}
    assert clocks == {(0, "SYSCLK", 200_000_000, False), (1, "SYSCLK", 200_000_000, True)}
    inputs = {(b.port, b.bit, b.pin) for b in mp.inputs}
    assert inputs == {(2, 0, "N4"), (3, 0, "D14"), (3, 1, "E12")}
    outputs = [(b.port, b.bit, b.pin) for b in mp.outputs]
    assert outputs == [(4, 0, "E13"), (4, 1, "C14"), (4, 2, "C4"), (4, 3, "A4")]
    text = "\n".join(mp.warnings)
    assert "SW[3] -> A3: pin is not modelled" in text
    assert "SW[4]: no LOC in UCF, input tied to 0" in text
    assert "COUNT: no LOC" in text or "COUNT[0]: no LOC" in text
    assert "UCF net 'EXTRA' (P4) has no matching port" in text
    assert set(mp.connected_pins) == {"N4", "D14", "E12", "E13", "C14", "C4", "A4"}


def test_unsupported_ports_are_errors(board):
    mp = build_mapping(board, parse_model_header(HEADER), parse_ucf_text(UCF))
    text = "\n".join(mp.errors)
    assert "'BIG'" in text and "'SDA'" in text


def test_output_on_input_pin_is_warning(board):
    ports = [Port("X", "out", 0, 0, False)]
    mp = build_mapping(board, ports, parse_ucf_text('NET "X" LOC = "D14";'))
    assert mp.outputs == []
    assert any("not simulated" in w for w in mp.warnings)
