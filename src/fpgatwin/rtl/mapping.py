"""Join RTL ports, UCF constraints and ``board.yaml`` into a simulation pin map."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fpgatwin.constraints.ucf import UcfResult
from fpgatwin.rtl.ports import Port


@dataclass
class ClockDrive:
    port: int  # index into Mapping.ports
    clock: str  # board clock name
    freq_hz: int
    inverted: bool  # True for the N side of a differential clock


@dataclass
class BitMap:
    port: int  # index into Mapping.ports
    bit: int  # offset inside the port value
    pin: str
    active_high: bool = True


@dataclass
class UartMap:
    id: str  # peripheral id in board.yaml, e.g. "uart0"
    label: str  # e.g. "CP2103GM"
    tx: tuple[int, int] | None = None  # (port, bit) the design transmits on (FPGA -> PC)
    rx: tuple[int, int] | None = None  # (port, bit) the design receives on (PC -> FPGA)
    pins: list[str] = field(default_factory=list)


@dataclass
class Mapping:
    ports: list[Port]
    clocks: list[ClockDrive] = field(default_factory=list)
    inputs: list[BitMap] = field(default_factory=list)  # board input pin -> RTL input bit
    outputs: list[BitMap] = field(default_factory=list)  # RTL output bit -> board output pin
    tie_high: list[tuple[int, int]] = field(default_factory=list)  # (port, bit) pulled up
    uarts: list[UartMap] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # human-readable mapping lines

    @property
    def connected_pins(self) -> list[str]:
        return sorted({b.pin for b in self.inputs} | {b.pin for b in self.outputs}
                      | {p for u in self.uarts for p in u.pins})


def _board_pins(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """pin -> {kind, label, active_high, clock?, inverted?}"""
    pins: dict[str, dict[str, Any]] = {}
    for clk in board.get("clocks", []):
        for role, pin in clk["pins"].items():
            pins[pin] = {"kind": "clock", "label": f"{clk['name']}.{role}", "clock": clk,
                         "inverted": role == "n"}
    kinds = {"led_array": "output", "dip_switch": "input", "pushbutton_array": "input"}
    uart_roles = {"tx_from_fpga": "uart_tx", "rx_to_fpga": "uart_rx",
                  "cts_to_fpga": "uart_cts", "rts_from_fpga": "uart_rts"}
    for per in board.get("peripherals", []):
        kind = kinds.get(per["type"])
        for item in per.get("items", []):
            pins[item["pin"]] = {"kind": kind or "other", "label": item["label"],
                                 "active_high": per.get("active_high", True)}
        for role, pin in per.get("pins", {}).items():
            pkind = uart_roles.get(role, "other") if per["type"] == "uart" else "other"
            pins[pin] = {"kind": pkind, "label": f"{per['id']}.{role}", "periph": per}
    return pins


def build_mapping(board: dict[str, Any], ports: list[Port], ucf: UcfResult) -> Mapping:
    mp = Mapping(ports=list(ports))
    mp.warnings.extend(ucf.warnings)
    board_pins = _board_pins(board)
    constraints = ucf.by_port_bit()
    used_nets: set[str] = set()
    uarts: dict[str, UartMap] = {}

    def uart(info: dict[str, Any]) -> UartMap:
        per = info["periph"]
        if per["id"] not in uarts:
            uarts[per["id"]] = UartMap(per["id"], per.get("chip", per["id"]))
        return uarts[per["id"]]

    for pi, port in enumerate(ports):
        if port.direction == "inout":
            mp.errors.append(f"port '{port.name}': inout ports are not supported yet")
            continue
        if port.wide:
            mp.errors.append(f"port '{port.name}': ports wider than 64 bits are not supported")
            continue

        lo, hi = min(port.msb, port.lsb), max(port.msb, port.lsb)
        indices: list[int | None]
        if port.width > 1:
            indices = list(range(lo, hi + 1))
        elif (port.name, None) not in constraints and (port.name, lo) in constraints:
            indices = [lo]  # 1-bit vector written as NET "x<0>"
        else:
            indices = [None]

        for index in indices:
            nc = constraints.get((port.name, index))
            hdl = port.name if index is None else f"{port.name}[{index}]"
            bit = port.bit_offset(index)
            if nc is None or not nc.loc:
                if port.direction == "in":
                    mp.warnings.append(f"{hdl}: no LOC in UCF, input tied to 0")
                else:
                    mp.warnings.append(f"{hdl}: no LOC in UCF, output not shown")
                continue
            used_nets.add(nc.net)
            info = board_pins.get(nc.loc)
            if info is None:
                mp.warnings.append(f"{hdl} -> {nc.loc}: pin is not modelled on this board")
                if port.direction == "in" and nc.pull == "up" and bit is not None:
                    mp.tie_high.append((pi, bit))
                continue

            if port.direction == "in" and info["kind"] == "clock":
                clk = info["clock"]
                if not clk.get("freq_hz"):
                    mp.warnings.append(f"{hdl} -> {nc.loc} ({info['label']}): external clock "
                                       "with no fixed frequency, input tied to 0")
                    continue
                if port.width != 1:
                    mp.errors.append(f"{hdl}: clock must be a 1-bit port")
                    continue
                mp.clocks.append(ClockDrive(pi, clk["name"], int(clk["freq_hz"]), info["inverted"]))
                mp.notes.append(f"{hdl:24s} <- {nc.loc:4s} {info['label']} "
                                f"({clk['freq_hz'] / 1e6:g} MHz)")
            elif port.direction == "in" and info["kind"] == "input":
                mp.inputs.append(BitMap(pi, bit, nc.loc, info["active_high"]))
                mp.notes.append(f"{hdl:24s} <- {nc.loc:4s} {info['label']}")
            elif port.direction == "out" and info["kind"] == "output":
                mp.outputs.append(BitMap(pi, bit, nc.loc, info["active_high"]))
                mp.notes.append(f"{hdl:24s} -> {nc.loc:4s} {info['label']}")
            elif port.direction == "out" and info["kind"] == "uart_tx":
                u = uart(info)
                u.tx = (pi, bit)
                u.pins.append(nc.loc)
                mp.notes.append(f"{hdl:24s} -> {nc.loc:4s} {info['label']} (UART, FPGA -> PC)")
            elif port.direction == "in" and info["kind"] == "uart_rx":
                u = uart(info)
                u.rx = (pi, bit)
                u.pins.append(nc.loc)
                mp.notes.append(f"{hdl:24s} <- {nc.loc:4s} {info['label']} (UART, PC -> FPGA)")
            elif port.direction == "in" and info["kind"] == "uart_cts":
                uart(info).pins.append(nc.loc)  # stays 0: CTS# is active low, always clear to send
                mp.notes.append(f"{hdl:24s} <- {nc.loc:4s} {info['label']} (always 0 = clear to send)")
            elif port.direction == "out" and info["kind"] == "uart_rts":
                uart(info).pins.append(nc.loc)
                mp.notes.append(f"{hdl:24s} -> {nc.loc:4s} {info['label']} (ignored)")
            else:
                verb = "input" if port.direction == "in" else "output"
                mp.warnings.append(f"{hdl} -> {nc.loc} ({info['label']}): {verb} on this pin "
                                   "is not simulated yet")

    mp.uarts = [u for u in uarts.values() if u.tx or u.rx]
    for net, nc in ucf.nets.items():
        if net not in used_nets and nc.loc:
            mp.warnings.append(f"UCF net '{net}' ({nc.loc}) has no matching port in the RTL")
    return mp
