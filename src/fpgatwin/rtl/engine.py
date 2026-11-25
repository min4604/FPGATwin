"""Engine that runs a Verilator-built design in a child process (see harness.cpp.in)."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections import deque
from typing import Any

from fpgatwin.rtl.build import BuildResult

_SENTINEL = b"\x1e"
UART_LOG_BYTES = 256 * 1024  # received bytes kept per UART for late-joining viewers
DEFAULT_BAUD = 115200


class VerilatorEngine:
    name = "verilator"

    def __init__(self, board: dict[str, Any], result: BuildResult, echo_console: bool = True):
        self.board = board
        self.result = result
        mp = result.mapping
        self.echo_console = echo_console

        # reference clock for rate display: first driven clock, else the board's first fixed one
        fixed = [c for c in board.get("clocks", []) if c.get("freq_hz")]
        ref_name = mp.clocks[0].clock if mp.clocks else (fixed[0]["name"] if fixed else None)
        self.clock = next((c for c in fixed if c["name"] == ref_name), {"name": "-", "freq_hz": 1})
        self.clock_hz = int(self.clock["freq_hz"])

        self.ports = mp.ports
        clock_ports = {c.port for c in mp.clocks}
        self._in_ports = [i for i, p in enumerate(self.ports)
                          if p.direction == "in" and i not in clock_ports and not p.wide]
        self._out_ports = [i for i, p in enumerate(self.ports)
                           if p.direction == "out" and not p.wide]
        self._inputs = {b.pin: b for b in mp.inputs}
        self._outputs = list(mp.outputs)
        self.input_pins = set(self._inputs)
        self.connected_pins = mp.connected_pins

        self._port_values = {i: 0 for i in self._in_ports}
        for port, bit in mp.tie_high:
            if port in self._port_values:
                self._port_values[port] |= 1 << bit
        self._pin_state = {pin: 0 for pin in self._inputs}
        for b in mp.inputs:  # buttons/switches at rest: logical 0 -> electrical level
            if not b.active_high:
                self._port_values[b.port] |= 1 << b.bit
        self._out_values = [0] * len(self._out_ports)
        self._sim_ps = 0

        self.status = "running"
        self.message = ""
        self.console: deque[str] = deque(maxlen=200)
        # UARTs, in harness order; received bytes are kept with a running offset (seq)
        self._uart_ids = [u.id for u in mp.uarts]
        self._uart_log = [bytearray() for _ in mp.uarts]
        self._uart_base = [0] * len(mp.uarts)  # seq of _uart_log[i][0]
        self._uart_errors = [0] * len(mp.uarts)
        self._uart_baud = [DEFAULT_BAUD] * len(mp.uarts)
        self.info = {
            "top": result.top,
            "connected": self.connected_pins,
            "uarts": [{"id": u.id, "label": u.label, "tx": u.tx is not None,
                       "rx": u.rx is not None, "baud": DEFAULT_BAUD} for u in mp.uarts],
        }

        self._io_lock = threading.Lock()  # serialises the pipe
        self._state_lock = threading.Lock()  # guards cached state for snapshot()
        self._stderr = open(result.build_dir / "sim_stderr.log", "wb")  # noqa: SIM115
        self._proc = subprocess.Popen(
            [str(result.exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._stderr, cwd=result.build_dir, bufsize=0,
        )
        reply = self._read_reply()
        if reply is None or not reply.startswith(b"R"):
            self._fail("simulation process did not start")
            return
        with self._io_lock:
            for port, value in self._port_values.items():
                self._send(f"i {port} {value:x}\n")

    # ---- process I/O ----------------------------------------------------------
    def _send(self, line: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()

    def _read_reply(self) -> bytes | None:
        """Read stdout until a protocol line; other lines are the design's own output."""
        assert self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if not line:
                return None
            # $write without a newline leaves design text in front of the protocol marker
            before, sep, after = line.partition(_SENTINEL)
            text = before.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                self.console.append(text)
                if self.echo_console:
                    print(f"[rtl] {text}", file=sys.stderr, flush=True)
            if sep:
                return after.rstrip(b"\r\n")

    def _fail(self, message: str) -> None:
        with self._state_lock:
            self.status = "stopped"
            self.message = message

    # ---- engine API -------------------------------------------------------------
    def set_pin(self, pin: str, value: int) -> None:
        if pin not in self.input_pins:
            if pin in self._pin_state or self._is_board_input(pin):
                return  # board input not connected to the design: accept and ignore
            raise KeyError(f"pin {pin} is not an input on this board")
        b = self._inputs[pin]
        level = (1 if value else 0) ^ (0 if b.active_high else 1)
        # compute and send under the pipe lock so concurrent presses on one port stay ordered
        with self._io_lock:
            with self._state_lock:
                self._pin_state[pin] = 1 if value else 0
                cur = self._port_values[b.port]
                new = (cur | (1 << b.bit)) if level else (cur & ~(1 << b.bit))
                self._port_values[b.port] = new
            if self.status != "running":
                return
            try:
                self._send(f"i {b.port} {new:x}\n")
            except OSError:
                self._fail("simulation process exited")

    def _is_board_input(self, pin: str) -> bool:
        kinds = {"dip_switch", "pushbutton_array"}
        return any(item["pin"] == pin for per in self.board.get("peripherals", [])
                   if per["type"] in kinds for item in per.get("items", []))

    def advance_ps(self, ps: int) -> None:
        if self.status != "running" or ps <= 0:
            return
        with self._io_lock:
            try:
                self._send(f"r {ps}\n")
                reply = self._read_reply()
                while reply is not None and reply.startswith(b"U"):
                    self._on_uart(reply)
                    reply = self._read_reply()
            except OSError:
                reply = None
        if reply is None:
            code = self._proc.poll()
            self._fail(f"simulation process exited (code {code})")
            return
        parts = reply.split()
        if parts[0] == b"F":
            with self._state_lock:
                self._sim_ps = int(parts[1])
            self._fail("design called $finish")
            return
        if parts[0] == b"O":
            values = [int(v, 16) for v in parts[2:]]
            with self._state_lock:
                self._sim_ps = int(parts[1])
                self._out_values = values

    # ---- UART -------------------------------------------------------------------
    def _on_uart(self, reply: bytes) -> None:
        parts = reply.split()
        try:
            idx, errors = int(parts[1]), int(parts[2])
            data = b"" if parts[3] == b"-" else bytes.fromhex(parts[3].decode())
        except (IndexError, ValueError):
            return
        if not 0 <= idx < len(self._uart_log):
            return
        with self._state_lock:
            log = self._uart_log[idx]
            log += data
            if len(log) > UART_LOG_BYTES:
                drop = len(log) - UART_LOG_BYTES
                del log[:drop]
                self._uart_base[idx] += drop
            self._uart_errors[idx] = errors

    def _uart_index(self, uart_id: str) -> int:
        try:
            return self._uart_ids.index(uart_id)
        except ValueError:
            raise KeyError(f"no UART '{uart_id}' in this design") from None

    def uart_read(self, uart_id: str, since: int) -> tuple[bytes, int]:
        """Bytes the design transmitted after offset *since*, and the new offset."""
        i = self._uart_index(uart_id)
        with self._state_lock:
            base, log = self._uart_base[i], self._uart_log[i]
            start = max(since - base, 0)
            return bytes(log[start:]), base + len(log)

    def uart_send(self, uart_id: str, data: bytes) -> None:
        """Queue bytes for the design to receive on its RX pin."""
        i = self._uart_index(uart_id)
        if not self.info["uarts"][i]["rx"]:
            raise KeyError(f"the design has no RX pin on '{uart_id}'")
        if self.status != "running" or not data:
            return
        with self._io_lock:
            try:
                for k in range(0, len(data), 4096):
                    self._send(f"u {i} {data[k:k + 4096].hex()}\n")
            except OSError:
                self._fail("simulation process exited")

    def uart_set_baud(self, uart_id: str, baud: int) -> None:
        i = self._uart_index(uart_id)
        with self._state_lock:
            self._uart_baud[i] = baud
            self.info["uarts"][i]["baud"] = baud
        if self.status != "running":
            return
        with self._io_lock:
            try:
                self._send(f"b {i} {baud}\n")
            except OSError:
                self._fail("simulation process exited")

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            pins = dict(self._pin_state)
            by_port = dict(zip(self._out_ports, self._out_values, strict=False))
            for b in self._outputs:
                level = (by_port.get(b.port, 0) >> b.bit) & 1
                pins[b.pin] = level if b.active_high else level ^ 1
            return {
                "engine": self.name,
                "status": self.status,
                "message": self.message,
                "pins": pins,
                "cycles": self._sim_ps * self.clock_hz // 1_000_000_000_000,
                "sim_time_ps": self._sim_ps,
                "uart": {uid: {"seq": self._uart_base[i] + len(self._uart_log[i]),
                               "errors": self._uart_errors[i], "baud": self._uart_baud[i]}
                         for i, uid in enumerate(self._uart_ids)},
            }

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is None:
            try:
                with self._io_lock:
                    self._send("q\n")
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        for f in (proc.stdin, proc.stdout, self._stderr):
            if f:
                f.close()
