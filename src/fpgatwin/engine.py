"""Simulation engines.

An engine exposes the board's I/O **by physical pin** (e.g. ``"E13"``) — the pin is the
join between the user's constraint file and ``board.yaml``, so a Verilator-backed engine
can replace the demo engine without touching the UI.
"""

from __future__ import annotations

import threading
import time
from typing import Any


def _items(board: dict[str, Any], per_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for per in board.get("peripherals", []):
        if per["type"] == per_type:
            out.extend(per.get("items", []))
    return out


class DemoCounterEngine:
    """Built-in demo that behaves like ``examples/sp601_blinky`` — **not** the user's RTL.

    * a free-running counter clocked by *clock* (default: the first fixed-frequency
      single-ended clock, like blinky's ``USER_CLOCK``; else the first fixed clock)
    * the LEDs show the counter's top bits (LED0 = bit ``cnt_bits - 4``)
    * the first DIP switch pauses counting
    * holding a button whose label contains ``RESET`` clears the counter

    All pin I/O is derived from ``board.yaml``; nothing here is board-specific.
    """

    name = "demo"

    def __init__(
        self, board: dict[str, Any], cnt_bits: int = 26, clock: str | None = None
    ) -> None:
        self.board = board
        self.cnt_bits = cnt_bits
        clocks = [c for c in board.get("clocks", []) if c.get("freq_hz")]
        if not clocks:
            raise ValueError("demo engine needs at least one fixed-frequency clock")
        if clock is not None:
            match = [c for c in clocks if c["name"] == clock]
            if not match:
                names = ", ".join(c["name"] for c in clocks)
                raise KeyError(f"unknown or external clock '{clock}' (fixed clocks: {names})")
            self.clock = match[0]
        else:
            single = [c for c in clocks if c.get("type") == "single_ended"]
            self.clock = (single or clocks)[0]
        self.clock_hz: int = int(self.clock["freq_hz"])

        self.led_pins = [i["pin"] for i in _items(board, "led_array")]
        self.switch_pins = [i["pin"] for i in _items(board, "dip_switch")]
        buttons = _items(board, "pushbutton_array")
        self.button_pins = [i["pin"] for i in buttons]
        self.reset_pin = next((i["pin"] for i in buttons if "RESET" in i["label"].upper()), None)
        self.input_pins = set(self.switch_pins) | set(self.button_pins)

        self._lock = threading.Lock()
        self._inputs: dict[str, int] = {p: 0 for p in self.input_pins}
        self._counter = 0
        self._cycles = 0

    # ---- inputs -------------------------------------------------------------
    def set_pin(self, pin: str, value: int) -> None:
        if pin not in self.input_pins:
            raise KeyError(f"pin {pin} is not an input on this board")
        with self._lock:
            self._inputs[pin] = 1 if value else 0

    # ---- time ---------------------------------------------------------------
    def step(self, cycles: int) -> None:
        """Advance the simulation by *cycles* clock cycles."""
        if cycles <= 0:
            return
        mask = (1 << self.cnt_bits) - 1
        with self._lock:
            self._cycles += cycles
            if self.reset_pin and self._inputs[self.reset_pin]:
                self._counter = 0
            elif not (self.switch_pins and self._inputs[self.switch_pins[0]]):
                self._counter = (self._counter + cycles) & mask

    # ---- outputs ------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            led_value = self._counter >> (self.cnt_bits - len(self.led_pins))
            pins = dict(self._inputs)
            for bit, pin in enumerate(self.led_pins):
                pins[pin] = (led_value >> bit) & 1
            return {
                "engine": self.name,
                "pins": pins,
                "cycles": self._cycles,
                "sim_time_ps": self._cycles * 1_000_000_000_000 // self.clock_hz,
            }


class EngineRunner:
    """Paces an engine against wall-clock time in a background thread."""

    def __init__(self, engine: DemoCounterEngine, speed: float = 1.0, period_s: float = 0.01):
        self.engine = engine
        self.speed = speed
        self.period_s = period_s
        self.rate_hz = 0.0  # measured simulated cycles per wall-clock second
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fpgatwin-engine", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        last = time.perf_counter()
        carry = 0.0
        while not self._stop.wait(self.period_s):
            now = time.perf_counter()
            dt = now - last
            last = now
            want = self.engine.clock_hz * self.speed * dt + carry
            cycles = int(want)
            carry = want - cycles
            self.engine.step(cycles)
            if dt > 0:
                self.rate_hz = 0.8 * self.rate_hz + 0.2 * (cycles / dt)
