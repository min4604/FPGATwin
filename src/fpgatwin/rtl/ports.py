"""Read the top-level port list from a Verilator-generated model header."""

from __future__ import annotations

import re
from dataclasses import dataclass

# VL_IN8(&name,msb,lsb);  VL_OUT64(&name,63,0);  VL_INW(&name,127,0,4);  VL_INOUT8(...)
_PORT_RE = re.compile(r"\bVL_(IN|OUT|INOUT)(8|16|64|W)?\(\s*&(\w+)\s*,\s*(\d+)\s*,\s*(\d+)")


@dataclass(frozen=True)
class Port:
    name: str
    direction: str  # "in" | "out" | "inout"
    msb: int
    lsb: int
    wide: bool  # VL_*W: wider than 64 bits

    @property
    def width(self) -> int:
        return abs(self.msb - self.lsb) + 1

    def bit_offset(self, index: int | None) -> int | None:
        """Offset inside the C++ value for HDL bit *index* (None for a scalar)."""
        if index is None:
            return 0 if self.width == 1 else None
        lo, hi = min(self.msb, self.lsb), max(self.msb, self.lsb)
        if not lo <= index <= hi:
            return None
        return index - lo


def parse_model_header(text: str) -> list[Port]:
    ports = []
    for m in _PORT_RE.finditer(text):
        kind, size, name, msb, lsb = m.groups()
        direction = {"IN": "in", "OUT": "out", "INOUT": "inout"}[kind]
        ports.append(Port(name, direction, int(msb), int(lsb), wide=(size == "W")))
    return ports
