"""Command-line entry point: ``fpgatwin``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fpgatwin import __version__
from fpgatwin.board import list_boards, load_board


def _cmd_boards(_args: argparse.Namespace) -> int:
    for board_id in list_boards():
        board = load_board(board_id)
        print(f"{board_id:20s} {board['board']['name']}")
    return 0


def _cmd_board_show(args: argparse.Namespace) -> int:
    board = load_board(args.board_id)
    info = board["board"]
    fpga = info["fpga"]
    print(f"{info['name']} ({info['id']})")
    print(f"  FPGA: {fpga['vendor']} {fpga['family']} {fpga['part']}")
    for clk in board.get("clocks", []):
        freq = clk.get("freq_hz")
        freq_str = f"{freq / 1e6:g} MHz" if freq else "external"
        print(f"  clock  {clk['name']:16s} {freq_str:>10s}  pins={clk['pins']}")
    for per in board.get("peripherals", []):
        count = len(per.get("items", [])) or 1
        print(f"  {per['type']:16s} {per['id']:16s} x{count}")
    return 0


def _expand_rtl(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(f for ext in ("*.v", "*.sv") for f in p.glob(ext)))
        else:
            files.append(p)
    return files


def _build_or_report(board: dict, args: argparse.Namespace):
    """Build the design, print the mapping report; return BuildResult or None on failure."""
    from fpgatwin.rtl.build import BuildError, build
    from fpgatwin.rtl.toolchain import ToolchainError

    rtl = _expand_rtl(args.rtl)
    if not rtl:
        print("error: no .v/.sv files found in --rtl", file=sys.stderr)
        return None
    print(f"Building {', '.join(f.name for f in rtl)} with {Path(args.ucf).name} ...", flush=True)
    try:
        result = build(board, rtl, args.ucf, top=args.top)
    except ToolchainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.log:
            print(exc.log.rstrip(), file=sys.stderr)
        return None

    mp = result.mapping
    print(f"Top module: {result.top}{'  (cached build)' if result.cached else ''}")
    print("Pin mapping:")
    for line in mp.notes:
        print(f"  {line}")
    for w in mp.warnings:
        print(f"  warning: {w}")
    log = result.build_dir / "verilator.log"
    if log.is_file():
        n = log.read_text(encoding="utf-8", errors="replace").count("%Warning")
        if n:
            print(f"  Verilator reported {n} warning(s); see {log}")
    return result


def _cmd_build(args: argparse.Namespace) -> int:
    board = load_board(args.board)
    return 0 if _build_or_report(board, args) else 1


def _cmd_ui(args: argparse.Namespace) -> int:
    import webbrowser

    from fpgatwin.ui.server import create_server, default_loader

    board = load_board(args.board)
    engine = None
    if args.rtl or args.ucf:
        if not (args.rtl and args.ucf):
            print("error: --rtl and --ucf must be given together", file=sys.stderr)
            return 2
        result = _build_or_report(board, args)
        if result is None:
            return 1
        from fpgatwin.rtl.engine import VerilatorEngine

        engine = VerilatorEngine(board, result)
        if engine.status != "running":
            print(f"error: {engine.message}", file=sys.stderr)
            engine.close()
            return 1
    try:
        server = create_server(
            board, port=args.port, speed=args.speed, clock=args.clock, engine=engine,
            loader=default_loader(board),
        )
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    except OSError as exc:
        if engine:
            engine.close()
        print(f"error: cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    label = f"RTL: {engine.result.top}" if engine else "demo engine"
    print(f"FPGATwin UI ({board['board']['name']}, {label}): {server.url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


def _add_design_args(p: argparse.ArgumentParser, required: bool) -> None:
    p.add_argument("--rtl", nargs="+", required=required, metavar="PATH",
                   help="Verilog/SystemVerilog files or folders")
    p.add_argument("--ucf", required=required, help="pin constraints: ISE .ucf or Vivado .xdc (set_property PACKAGE_PIN)")
    p.add_argument("--top", help="top module name (default: detected by Verilator)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpgatwin",
        description="A digital twin for FPGA development boards.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_boards = sub.add_parser("boards", help="list supported boards")
    p_boards.set_defaults(func=_cmd_boards)

    p_show = sub.add_parser("board", help="show a board's hardware mapping")
    p_show.add_argument("board_id")
    p_show.set_defaults(func=_cmd_board_show)

    p_ui = sub.add_parser("ui", help="open the virtual board in a local web UI")
    p_ui.add_argument("--board", default="xilinx_sp601", help="board id (default: xilinx_sp601)")
    p_ui.add_argument("--port", type=int, default=8600, help="local port (default: 8600)")
    p_ui.add_argument(
        "--speed", type=float, default=1.0, help="simulation speed vs. real time (default: 1.0)"
    )
    _add_design_args(p_ui, required=False)
    p_ui.add_argument("--clock", help="clock driving the demo engine (default: auto)")
    p_ui.add_argument("--no-browser", action="store_true", help="don't open a browser")
    p_ui.set_defaults(func=_cmd_ui)

    p_build = sub.add_parser("build", help="build a design and show its pin mapping")
    p_build.add_argument("--board", default="xilinx_sp601", help="board id (default: xilinx_sp601)")
    _add_design_args(p_build, required=True)
    p_build.set_defaults(func=_cmd_build)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
