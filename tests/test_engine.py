import pytest

from fpgatwin.board import load_board
from fpgatwin.engine import DemoCounterEngine

LEDS = ["E13", "C14", "C4", "A4"]
SW0, RESET = "D14", "N4"


@pytest.fixture
def engine():
    # cnt_bits=8 -> LED = counter[7:4], one LED step every 16 cycles
    return DemoCounterEngine(load_board("xilinx_sp601"), cnt_bits=8)


def led_value(engine):
    pins = engine.snapshot()["pins"]
    return sum(pins[p] << i for i, p in enumerate(LEDS))


def test_pins_come_from_board(engine):
    assert engine.led_pins == LEDS
    assert engine.reset_pin == RESET
    assert engine.switch_pins[0] == SW0
    assert engine.clock["name"] == "USER_CLOCK"  # single-ended preferred, like blinky


def test_explicit_clock():
    board = load_board("xilinx_sp601")
    assert DemoCounterEngine(board, clock="SYSCLK").clock_hz == 200_000_000
    with pytest.raises(KeyError):
        DemoCounterEngine(board, clock="USER_SMA_CLOCK")  # external, no fixed freq


def test_counts(engine):
    engine.step(16 * 5)
    assert led_value(engine) == 5


def test_switch_pauses(engine):
    engine.step(16 * 3)
    engine.set_pin(SW0, 1)
    engine.step(1000)
    assert led_value(engine) == 3
    engine.set_pin(SW0, 0)
    engine.step(16)
    assert led_value(engine) == 4


def test_reset_clears_while_held(engine):
    engine.step(16 * 7)
    engine.set_pin(RESET, 1)
    engine.step(50)
    assert led_value(engine) == 0
    engine.set_pin(RESET, 0)
    engine.step(16 * 2)
    assert led_value(engine) == 2


def test_wraps(engine):
    engine.step(256 + 16)
    assert led_value(engine) == 1


def test_sim_time_and_inputs_in_snapshot(engine):
    engine.step(27)  # 27 cycles @ 27 MHz = 1 us
    snap = engine.snapshot()
    assert snap["sim_time_ps"] == 1_000_000
    assert snap["pins"][SW0] == 0


def test_rejects_non_input_pin(engine):
    with pytest.raises(KeyError):
        engine.set_pin("E13", 1)  # LED is an output
