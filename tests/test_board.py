"""SP601 board.yaml 腳位回歸測試（依 UG518 v1.4 核對過的值）。"""

import pytest

from fpgatwin.board import list_boards, load_board, pin_map


@pytest.fixture(scope="module")
def sp601():
    return load_board("xilinx_sp601")


def _items(board, per_id):
    per = next(p for p in board["peripherals"] if p["id"] == per_id)
    return per


def test_sp601_is_listed():
    assert "xilinx_sp601" in list_boards()


def test_fpga_part(sp601):
    fpga = sp601["board"]["fpga"]
    assert (fpga["vendor"], fpga["family"], fpga["part"]) == (
        "xilinx",
        "spartan6",
        "XC6SLX16-2CSG324",
    )


def test_clocks(sp601):
    clocks = {c["name"]: c for c in sp601["clocks"]}
    assert clocks["SYSCLK"]["pins"] == {"p": "K15", "n": "K16"}
    assert clocks["SYSCLK"]["freq_hz"] == 200_000_000
    assert clocks["USER_CLOCK"]["pins"] == {"p": "V10"}
    assert clocks["USER_CLOCK"]["freq_hz"] == 27_000_000
    assert clocks["USER_SMA_CLOCK"]["pins"] == {"p": "H17", "n": "H18"}


@pytest.mark.parametrize(
    ("per_id", "expected"),
    [
        ("user_leds", ["E13", "C14", "C4", "A4"]),
        ("user_switches", ["D14", "E12", "F12", "V13"]),
        ("user_buttons", ["P4", "F6", "E4", "F5", "N4"]),
    ],
)
def test_user_io_pins(sp601, per_id, expected):
    assert [i["pin"] for i in _items(sp601, per_id)["items"]] == expected


def test_uart_pins(sp601):
    assert _items(sp601, "uart0")["pins"] == {
        "tx_from_fpga": "L12",
        "rx_to_fpga": "K14",
        "rts_from_fpga": "U10",
        "cts_to_fpga": "T5",
    }


def test_no_duplicate_pins(sp601):
    pins = pin_map(sp601)  # raises on duplicates
    assert len(pins) == 2 + 1 + 2 + 4 + 4 + 5 + 4


def test_unknown_board():
    with pytest.raises(KeyError):
        load_board("no_such_board")
