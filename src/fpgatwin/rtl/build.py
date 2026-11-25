"""Build a user design into a simulation executable.

Steps:
  1. ``verilator --cc`` the RTL (plus FPGATwin's primitive library) into C++
  2. read the generated header for the top-level ports, map them via UCF + board.yaml
  3. generate the harness and compile everything with the C++ compiler directly
     (Verilator's own Makefile flow needs a POSIX shell, which plain Windows lacks)

Builds are cached by content hash, under the user cache directory.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from fpgatwin import __version__
from fpgatwin.constraints.ucf import UcfResult, parse_ucf
from fpgatwin.rtl.mapping import Mapping, build_mapping
from fpgatwin.rtl.ports import parse_model_header
from fpgatwin.rtl.toolchain import IS_WINDOWS, Toolchain, find_toolchain, native_path

PREFIX = "Vfpgatwin"
_DEFINES = [
    "-DVERILATOR=1", "-DVM_COVERAGE=0", "-DVM_SC=0", "-DVM_TIMING=0", "-DVM_TRACE=0",
    "-DVM_TRACE_FST=0", "-DVM_TRACE_VCD=0", "-DVM_TRACE_SAIF=0", "-DVM_VPI=0",
]


class BuildError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


@dataclass
class BuildResult:
    exe: Path
    mapping: Mapping
    top: str
    build_dir: Path
    cached: bool


def cache_root() -> Path:
    override = os.environ.get("FPGATWIN_CACHE")
    if override:
        return Path(override)
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "fpgatwin" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fpgatwin"


def _primitive_dir(board: dict[str, Any]) -> Path | None:
    fpga = board["board"]["fpga"]
    res = resources.files("fpgatwin").joinpath("primitives", f"{fpga['vendor']}_{fpga['family']}")
    path = Path(str(res))
    return path if path.is_dir() else None


_HDL_EXTS = {".v", ".sv", ".vh", ".svh", ".vinc", ".inc", ".mem", ".hex", ".dat", ".coe"}


def _tool_path(path: Path, what: str) -> str:
    p = native_path(path)
    if p is None:
        raise BuildError(
            f"{what} path contains characters the toolchain cannot open: {path}\n"
            "Move it to a path with only ASCII characters (or set FPGATWIN_CACHE)."
        )
    return p


def _tool_sources(sources: list[Path], bdir: Path) -> tuple[list[str], list[str]]:
    """Return (source args, include dirs) usable on the tool command line.

    If a source directory has no ASCII form (non-ASCII path, no 8.3 names), its HDL files are
    copied into the build directory so Verilator can read them.
    """
    dirs = sorted({s.parent for s in sources})
    dir_map: dict[Path, str] = {}
    for i, d in enumerate(dirs):
        nd = native_path(d)
        if nd is None:
            stage = bdir / "stage" / f"d{i}"
            stage.mkdir(parents=True, exist_ok=True)
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in _HDL_EXTS and f.name.isascii():
                    shutil.copy2(f, stage / f.name)
            nd = _tool_path(stage, "build")
        dir_map[d] = nd
    srcs = []
    for s in sources:
        if not s.name.isascii() and native_path(s) is None:
            raise BuildError(f"RTL file name must be ASCII on this system: {s.name}")
        base = dir_map[s.parent]
        srcs.append(str(Path(base) / s.name) if s.name.isascii() else native_path(s))
    return srcs, [dir_map[d] for d in dirs]


def _run(cmd: list[str], tc: Toolchain, cwd: Path, what: str) -> str:
    proc = subprocess.run(cmd, cwd=cwd, env=tc.env, capture_output=True, check=False)
    log = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise BuildError(f"{what} failed (exit {proc.returncode})", log)
    return log


def _hash(*parts: bytes | str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode() if isinstance(p, str) else p)
        h.update(b"\0")
    return h.hexdigest()[:16]


def _verilated_top(obj: Path) -> str | None:
    """Name of the top module Verilator chose (it picks the one nobody instantiates)."""
    for f in obj.glob(f"{PREFIX}__Syms*.cpp"):
        m = re.search(r'catName\(vlSymsp->name\(\),\s*"([^"]+)"\)',
                      f.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return None


def _classes(mk_text: str) -> dict[str, list[str]]:
    """Parse ``VM_X += \\ a \\ b`` lists from the generated *_classes.mk."""
    out: dict[str, list[str]] = {}
    current = None
    for line in mk_text.splitlines():
        m = re.match(r"^(VM_\w+)\s*\+=", line)
        if m:
            current = out.setdefault(m.group(1), [])
            continue
        if current is not None and line.startswith((" ", "\t")):
            name = line.strip().rstrip("\\").strip()
            if name:
                current.append(name)
        else:
            current = None
    return out


def _runtime_objects(tc: Toolchain, names: list[str]) -> list[Path]:
    """Compile Verilator's runtime sources once per toolchain and reuse them."""
    key = _hash(str(tc.root), tc.version, tc.cxx, *tc.cxx_flags(), *_DEFINES)
    rdir = cache_root() / f"runtime-{key}"
    rdir.mkdir(parents=True, exist_ok=True)
    rdir = Path(_tool_path(rdir, "build cache"))
    inc = Path(_tool_path(tc.include, "Verilator include"))
    objs = []
    for name in names:
        obj = rdir / f"{name}.o"
        if not obj.is_file():
            src = inc / f"{name}.cpp"
            tmp = rdir / f"{name}.o.tmp"
            _run([tc.cxx, *tc.cxx_flags(), "-Os", *_DEFINES, f"-I{inc}",
                  f"-I{inc / 'vltstd'}", "-c", str(src), "-o", str(tmp)],
                 tc, rdir, f"compiling Verilator runtime ({name})")
            tmp.replace(obj)
        objs.append(obj)
    return objs


def _gen_harness(mapping: Mapping, header: str) -> str:
    ports = mapping.ports
    clock_cases = "\n".join(
        f"        case {i}: top->{ports[c.port].name} = value; break;"
        for i, c in enumerate(mapping.clocks)
    )
    clock_init = "\n".join(
        f"    clocks[{i}] = Clock{{{c.freq_hz}ULL, {'true' if c.inverted else 'false'}, 0, 0, 0}};"
        f" clocks[{i}].next_ps = edge_time(clocks[{i}], 1);"
        for i, c in enumerate(mapping.clocks)
    )
    clock_ports = {c.port for c in mapping.clocks}
    input_cases = "\n".join(
        f"        case {i}: top->{p.name} = v; break;"
        for i, p in enumerate(ports)
        if p.direction == "in" and i not in clock_ports and not p.wide
    )
    output_prints = "\n".join(
        f'    std::printf(" %llx", (unsigned long long)top->{p.name});'
        for p in ports
        if p.direction == "out" and not p.wide
    )
    output_reads = "\n".join(
        f"        case {i}: return (int)(((uint64_t)top->{p.name} >> bit) & 1);"
        for i, p in enumerate(ports)
        if p.direction == "out" and not p.wide
    )
    uart_init = "\n".join(
        f"    uarts[{i}].tx_port = {u.tx[0] if u.tx else -1}; uarts[{i}].tx_bit = {u.tx[1] if u.tx else 0};"
        f" uarts[{i}].rx_port = {u.rx[0] if u.rx else -1}; uarts[{i}].rx_bit = {u.rx[1] if u.rx else 0};"
        for i, u in enumerate(mapping.uarts)
    )
    tpl = resources.files("fpgatwin.rtl").joinpath("harness.cpp.in").read_text(encoding="utf-8")
    return (
        tpl.replace("@HEADER@", header)
        .replace("@CLASS@", PREFIX)
        .replace("@NCLOCKS@", str(len(mapping.clocks)))
        .replace("@CLOCK_CASES@", clock_cases)
        .replace("@CLOCK_INIT@", clock_init)
        .replace("@INPUT_CASES@", input_cases)
        .replace("@OUTPUT_PRINTS@", output_prints)
        .replace("@NPORTS@", str(len(ports)))
        .replace("@OUTPUT_READS@", output_reads)
        .replace("@NUARTS@", str(len(mapping.uarts)))
        .replace("@UART_INIT@", uart_init)
    )


def build(
    board: dict[str, Any],
    rtl: list[str | Path],
    ucf_path: str | Path,
    top: str | None = None,
    toolchain: Toolchain | None = None,
    ucf: UcfResult | None = None,
) -> BuildResult:
    tc = toolchain or find_toolchain()
    sources = [Path(p).resolve() for p in rtl]
    for s in sources:
        if not s.is_file():
            raise BuildError(f"RTL file not found: {s}")
    ucf_file = Path(ucf_path).resolve()
    if ucf is None:
        if not ucf_file.is_file():
            raise BuildError(f"UCF file not found: {ucf_file}")
        ucf = parse_ucf(ucf_file)

    prim = _primitive_dir(board)
    harness_tpl = resources.files("fpgatwin.rtl").joinpath("harness.cpp.in").read_bytes()
    prim_bytes = b"".join(p.read_bytes() for p in sorted(prim.glob("*.v"))) if prim else b""
    # Verilator also reads submodules / `include files from the source folders (-I), so every
    # HDL file there is part of the key, not just the ones listed on the command line.
    dep_files = sorted(
        {f for d in {s.parent for s in sources} for f in d.iterdir()
         if f.is_file() and f.suffix.lower() in _HDL_EXTS} | set(sources)
    )
    key = _hash(
        __version__, tc.version, tc.cxx, *tc.cxx_flags(), *tc.link_flags(), str(top),
        board["board"]["id"], harness_tpl, prim_bytes, ucf_file.read_bytes(), str(board),
        *[str(s) for s in sources],
        *[part for f in dep_files for part in (str(f), f.read_bytes())],
    )
    name = top or sources[0].stem
    bdir = cache_root() / "build" / f"{re.sub(r'[^A-Za-z0-9_]', '_', name)}-{key}"
    bdir.mkdir(parents=True, exist_ok=True)
    bdir = Path(_tool_path(bdir, "build cache"))
    exe = bdir / (f"{PREFIX}.exe" if IS_WINDOWS else PREFIX)
    obj = bdir / "obj"

    # 1. verilate (always: it is fast and yields the port list for the mapping)
    if not (obj / f"{PREFIX}.h").is_file():
        obj.mkdir(parents=True, exist_ok=True)
        src_args, inc_dirs = _tool_sources(sources, bdir)
        cmd = [*(tc.verilator_build or tc.verilator), "--cc", "-Wno-fatal",
               "--prefix", PREFIX, "--Mdir", str(obj)]
        if top:
            cmd += ["--top-module", top]
        cmd += [f"-I{d}" for d in inc_dirs]
        if prim:
            cmd += ["-y", _tool_path(prim, "primitive library"), "+libext+.v"]
        cmd += src_args
        try:
            log = _run(cmd, tc, bdir, "Verilator")
        except BuildError:
            for f in obj.glob("*"):
                f.unlink()
            raise
        (bdir / "verilator.log").write_text(log, encoding="utf-8")

    name = top or _verilated_top(obj) or name
    header_text = (obj / f"{PREFIX}.h").read_text(encoding="utf-8", errors="replace")
    mapping = build_mapping(board, parse_model_header(header_text), ucf)
    if mapping.errors:
        raise BuildError("cannot map the design to the board:\n  " + "\n  ".join(mapping.errors))

    if exe.is_file():
        return BuildResult(exe, mapping, name, bdir, cached=True)

    # 3. harness + compile
    (bdir / "harness.cpp").write_text(_gen_harness(mapping, f"{PREFIX}.h"), encoding="utf-8")
    classes = _classes((obj / f"{PREFIX}_classes.mk").read_text(encoding="utf-8"))
    model_srcs = [
        obj / f"{c}.cpp"
        for k in ("VM_CLASSES_FAST", "VM_CLASSES_SLOW", "VM_SUPPORT_FAST", "VM_SUPPORT_SLOW")
        for c in classes.get(k, [])
    ]
    runtime = _runtime_objects(
        tc, classes.get("VM_GLOBAL_FAST", []) + classes.get("VM_GLOBAL_SLOW", [])
    )
    tmp_exe = exe.with_name(exe.name + ".tmp" + (".exe" if IS_WINDOWS else ""))
    inc = _tool_path(tc.include, "Verilator include")
    _run(
        [tc.cxx, *tc.cxx_flags(), "-O2", *_DEFINES, f"-DVM_PREFIX_INCLUDE=<{PREFIX}.h>",
         f"-I{obj}", f"-I{inc}", f"-I{Path(inc) / 'vltstd'}",
         str(bdir / "harness.cpp"), *[str(s) for s in model_srcs], *[str(o) for o in runtime],
         "-o", str(tmp_exe), *tc.link_flags()],
        tc, bdir, "C++ compile",
    )
    tmp_exe.replace(exe)
    return BuildResult(exe, mapping, name, bdir, cached=False)
