"""Pin-constraint parser for Xilinx ISE ``.ucf`` and Vivado ``.xdc`` (the subset needed for pin
mapping). Both syntaxes may appear in one file: a Vivado ``set_property`` file saved as ``.ucf``
is common in course material, often with the old UCF lines commented out.

UCF:
  * ``NET "name" LOC = "PIN" | IOSTANDARD = LVCMOS25 | PULLUP;`` (quotes optional)
  * bus bits ``NET "led<3>"`` (also ``led[3]``), attributes split over several NET lines
  * statements spanning multiple lines (terminated by ``;``); a missing ``;`` is reported and
    the statement ends at the next line that starts a new one
  * ignored with no warning: ``TIMESPEC``, ``TIMEGRP``, ``INST``, ``CONFIG``, ``PIN``,
    ``AREA_GROUP``

XDC:
  * ``set_property PACKAGE_PIN N15 [get_ports {PB[0]}]`` (also ``LOC``, ``IOSTANDARD``,
    ``PULLUP true``, ``PULLDOWN true``, ``PULLTYPE PULLUP``)
  * ``set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS18} [get_ports PB[0]]``
  * several ports in one ``get_ports {a b}``; lines continued with a trailing ``\\``
  * other commands (``create_clock``, ``set_input_delay``, ...) are ignored with no warning

``#`` starts a comment in both. Anything else is reported as a warning with its line number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_UCF_KEYWORDS = {"NET", "TIMESPEC", "TIMEGRP", "INST", "CONFIG", "PIN", "AREA_GROUP"}
_IGNORED_KEYWORDS = _UCF_KEYWORDS - {"NET"}
_NET_RE = re.compile(r'^NET\s+(?:"([^"]+)"|(\S+))\s*(.*)$', re.IGNORECASE | re.DOTALL)
_BIT_RE = re.compile(r"^(.*?)[<\[](\d+)[>\]]$")
# Tcl commands as they appear at the start of an XDC line
_XDC_CMD_RE = re.compile(r"^(set_|create_|get_|current_|reset_|group_|startgroup|endgroup)\w*")
_XDC_SET_RE = re.compile(
    r"^set_property\s+(?:-dict\s+(?P<dict>\{[^}]*\}|\"[^\"]*\")"
    r"|(?P<name>\S+)\s+(?P<value>\{[^}]*\}|\"[^\"]*\"|\S+))"
    r"\s+\[\s*get_ports\s+(?P<ports>.*)\]\s*;?\s*$",
    re.DOTALL,
)


@dataclass
class NetConstraint:
    net: str  # as written, e.g. "GPIO_LED<0>"
    port: str  # "GPIO_LED"
    index: int | None  # 0, or None for a scalar net
    loc: str | None = None
    iostandard: str | None = None
    pull: str | None = None  # "up" | "down" | "keeper"
    line: int = 0  # first line that mentioned the net


@dataclass
class UcfResult:
    nets: dict[str, NetConstraint] = field(default_factory=dict)  # keyed by net name
    warnings: list[str] = field(default_factory=list)

    def by_port_bit(self) -> dict[tuple[str, int | None], NetConstraint]:
        return {(n.port, n.index): n for n in self.nets.values()}


def _split_net(net: str) -> tuple[str, int | None]:
    m = _BIT_RE.match(net)
    if m:
        return m.group(1), int(m.group(2))
    return net, None


def _is_xdc(line: str) -> bool:
    return bool(_XDC_CMD_RE.match(line))


def _starts_statement(line: str) -> bool:
    word = line.split(None, 1)[0].upper() if line.split() else ""
    return word in _UCF_KEYWORDS or _is_xdc(line)


def _statements(text: str):
    """Yield ``(line_number, statement, problem)`` with comments stripped.

    UCF statements end at ``;``; XDC commands end at the end of the line. *problem* is a
    warning message for a UCF statement that was cut short by a missing ``;``, else ``None``.
    """
    buf: list[str] = []
    start = 0
    lines = iter(enumerate(text.splitlines(), start=1))
    for lineno, raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if _starts_statement(line) and buf:  # previous UCF statement never got its ';'
            yield start, " ".join(buf), "missing ';' at the end of this statement"
            buf = []
        if not buf and _is_xdc(line):
            while line.endswith("\\"):  # Tcl line continuation
                nxt = next(lines, None)
                if nxt is None:
                    break
                line = line[:-1] + " " + nxt[1].split("#", 1)[0].strip()
            yield lineno, line, None
            continue
        while line:
            if not buf:
                start = lineno
            head, sep, rest = line.partition(";")
            if head.strip():
                buf.append(head.strip())
            if sep:
                if buf:
                    yield start, " ".join(buf), None
                buf = []
            line = rest.strip()
    if buf:
        yield start, " ".join(buf), None


def _unbrace(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and (s[0], s[-1]) in (("{", "}"), ('"', '"')):
        return s[1:-1].strip()
    return s


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.result = UcfResult()

    def warn(self, lineno: int, msg: str) -> None:
        self.result.warnings.append(f"{self.source}:{lineno}: {msg}")

    def net(self, net: str, lineno: int) -> NetConstraint | None:
        if "*" in net or "?" in net:
            self.warn(lineno, f"wildcard nets are not supported: {net}")
            return None
        nc = self.result.nets.get(net)
        if nc is None:
            port, index = _split_net(net)
            nc = self.result.nets[net] = NetConstraint(net=net, port=port, index=index, line=lineno)
        return nc

    def set_loc(self, nc: NetConstraint, value: str, lineno: int) -> None:
        value = value.upper()
        if nc.loc and nc.loc != value:
            self.warn(lineno, f"{nc.net} LOC redefined ({nc.loc} -> {value})")
        nc.loc = value

    # ---- UCF --------------------------------------------------------------------
    def ucf(self, lineno: int, stmt: str) -> None:
        m = _NET_RE.match(stmt)
        if not m:
            self.warn(lineno, f"unsupported statement: {stmt[:60]}")
            return
        nc = self.net(m.group(1) or m.group(2), lineno)
        if nc is None:
            return
        for attr in filter(None, (a.strip() for a in m.group(3).split("|"))):
            name, _, value = attr.partition("=")
            name = name.strip().upper()
            value = value.strip().strip('"').strip()
            if name == "LOC":
                self.set_loc(nc, value, lineno)
            elif name == "IOSTANDARD":
                nc.iostandard = value.upper()
            elif name in ("PULLUP", "PULLDOWN", "KEEPER"):
                nc.pull = {"PULLUP": "up", "PULLDOWN": "down", "KEEPER": "keeper"}[name]
            # other attributes (DRIVE, SLEW, TNM_NET, PERIOD, ...) don't affect simulation

    # ---- XDC --------------------------------------------------------------------
    def xdc(self, lineno: int, cmd: str) -> None:
        if not cmd.startswith("set_property"):
            return  # create_clock, set_input_delay, ...: timing only
        m = _XDC_SET_RE.match(cmd)
        if not m:
            if "get_ports" in cmd:
                self.warn(lineno, f"unsupported set_property form: {cmd[:60]}")
            return  # properties on cells, nets, the design (CFGBVS, ...) don't matter here
        if m.group("dict") is not None:
            words = _unbrace(m.group("dict")).split()
            props = list(zip(words[0::2], words[1::2], strict=False))
        else:
            props = [(m.group("name"), _unbrace(m.group("value")))]
        ports_arg = m.group("ports").strip()
        if ports_arg.startswith("-"):
            self.warn(lineno, f"unsupported get_ports option: {ports_arg[:40]}")
            return
        names = [_unbrace(p) for p in _unbrace(ports_arg).split()]
        for name in filter(None, names):
            nc = self.net(name, lineno)
            if nc is None:
                continue
            for prop, value in props:
                prop = prop.upper()
                if prop in ("PACKAGE_PIN", "LOC"):
                    self.set_loc(nc, value, lineno)
                elif prop == "IOSTANDARD":
                    nc.iostandard = value.upper()
                elif prop in ("PULLUP", "PULLDOWN", "KEEPER"):
                    if value.lower() in ("true", "1", "yes"):
                        nc.pull = {"PULLUP": "up", "PULLDOWN": "down", "KEEPER": "keeper"}[prop]
                elif prop == "PULLTYPE":
                    nc.pull = {"PULLUP": "up", "PULLDOWN": "down", "KEEPER": "keeper"}.get(
                        value.upper(), nc.pull)

    def run(self, text: str) -> UcfResult:
        for lineno, stmt, problem in _statements(text):
            if problem:
                self.warn(lineno, problem)
            if _is_xdc(stmt):
                self.xdc(lineno, stmt)
                continue
            keyword = stmt.split(None, 1)[0].upper()
            if keyword in _IGNORED_KEYWORDS:
                continue
            if keyword != "NET":
                self.warn(lineno, f"unsupported statement: {stmt[:60]}")
                continue
            self.ucf(lineno, stmt)

        # LOC collisions
        seen: dict[str, str] = {}
        for nc in self.result.nets.values():
            if nc.loc:
                if nc.loc in seen:
                    self.warn(nc.line, f"pin {nc.loc} assigned to both {seen[nc.loc]} and {nc.net}")
                else:
                    seen[nc.loc] = nc.net
        return self.result


def parse_ucf_text(text: str, source: str = "<ucf>") -> UcfResult:
    """Parse UCF and/or XDC pin constraints."""
    return _Parser(source).run(text)


def parse_ucf(path: str | Path) -> UcfResult:
    """Parse a ``.ucf`` or ``.xdc`` file."""
    p = Path(path)
    return parse_ucf_text(p.read_text(encoding="utf-8", errors="replace"), source=p.name)
