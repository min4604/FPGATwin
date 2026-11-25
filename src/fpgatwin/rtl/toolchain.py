"""Locate Verilator and a C++ compiler, and build the environment to run them in.

Lookup order for each tool:
  1. environment variable (``FPGATWIN_VERILATOR`` / ``FPGATWIN_CXX``)
  2. the active Python environment (``sys.prefix``), so a conda env works without activation
  3. ``PATH``
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = os.name == "nt"


class ToolchainError(RuntimeError):
    pass


@dataclass
class Toolchain:
    verilator: list[str]  # command prefix, e.g. ["perl", ".../verilator"] or ["verilator"]
    cxx: str
    root: Path  # VERILATOR_ROOT (contains include/)
    env: dict[str, str] = field(default_factory=dict)
    version: str = ""
    # Command used for builds. On Windows the perl wrapper re-joins arguments without
    # quoting (breaking paths with spaces), so verilator_bin.exe is called directly.
    verilator_build: list[str] = field(default_factory=list)

    @property
    def include(self) -> Path:
        return self.root / "include"

    def cxx_flags(self) -> list[str]:
        return ["-std=gnu++17"]

    def link_flags(self) -> list[str]:
        # conda-forge MinGW: dynamically linked libstdc++ crashes at runtime -> link statically
        return ["-static"] if IS_WINDOWS else ["-pthread"]


def _prefix_dirs() -> list[Path]:
    prefix = Path(sys.prefix)
    dirs = [prefix / "Library" / "bin", prefix / "bin", prefix / "Scripts"] if IS_WINDOWS \
        else [prefix / "bin"]
    return [d for d in dirs if d.is_dir()]


def _run_env() -> dict[str, str]:
    env = dict(os.environ)
    extra = os.pathsep.join(str(d) for d in _prefix_dirs())
    env["PATH"] = extra + os.pathsep + env.get("PATH", "") if extra else env.get("PATH", "")
    return env


def _which(name: str, env: dict[str, str]) -> str | None:
    return shutil.which(name, path=env["PATH"])


def _find_verilator(env: dict[str, str]) -> list[str]:
    override = os.environ.get("FPGATWIN_VERILATOR")
    if override:
        return [override]
    for d in _prefix_dirs():
        script = d / "verilator"
        if script.is_file() and (d / ("verilator_bin.exe" if IS_WINDOWS else "verilator_bin")).exists():
            if IS_WINDOWS:  # the Windows package ships a perl script; run it with its perl
                local = d / "perl.exe"
                perl = str(local) if local.is_file() else _which("perl", env)
                if perl:
                    return [perl, str(script)]
            else:
                return [str(script)]
    found = _which("verilator", env)
    if found:
        return [found]
    raise ToolchainError(
        "Verilator not found. Install it into this environment "
        "(conda install -c conda-forge verilator) or set FPGATWIN_VERILATOR."
    )


def _find_cxx(env: dict[str, str]) -> str:
    override = os.environ.get("FPGATWIN_CXX")
    if override:
        return override
    for name in ("g++", "clang++"):
        found = _which(name, env)
        if found:
            return found
    raise ToolchainError(
        "No C++ compiler found. Install one (conda install -c conda-forge gxx) "
        "or set FPGATWIN_CXX."
    )


def _capture(cmd: list[str], env: dict[str, str]) -> str:
    out = subprocess.run(cmd, env=env, capture_output=True, check=False)
    return out.stdout.decode("utf-8", errors="replace")


def find_toolchain() -> Toolchain:
    env = _run_env()
    verilator = _find_verilator(env)
    cxx = _find_cxx(env)

    root_out = _capture([*verilator, "--getenv", "VERILATOR_ROOT"], env)
    root = None
    for line in reversed(root_out.splitlines()):  # wrappers may print noise first
        cand = Path(line.strip())
        if line.strip() and (cand / "include" / "verilated.h").is_file():
            root = cand
            break
    if root is None:
        raise ToolchainError(f"cannot determine VERILATOR_ROOT (got: {root_out.strip()!r})")

    version = _capture([*verilator, "--version"], env).strip().splitlines()
    env["VERILATOR_ROOT"] = str(root)

    build_cmd = verilator
    if IS_WINDOWS:
        for d in [Path(verilator[-1]).parent, root / "bin", *_prefix_dirs()]:
            exe = d / "verilator_bin.exe"
            if exe.is_file():
                build_cmd = [str(exe)]
                break
    return Toolchain(verilator, cxx, root, env, version[-1] if version else "", build_cmd)


def native_path(path: Path) -> str | None:
    """Return a form of *path* that narrow-argv Windows tools (Verilator, MinGW g++) can open.

    ASCII paths are returned as-is. On Windows a non-ASCII path is converted to its 8.3 short
    name when the volume provides one. Returns None when no ASCII form exists.
    """
    s = str(path)
    if s.isascii():
        return s
    if not IS_WINDOWS:
        return s  # POSIX tools take bytes; UTF-8 paths are fine
    import ctypes

    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(s, buf, len(buf))
    if 0 < n < len(buf) and buf.value.isascii():
        return buf.value
    return None
