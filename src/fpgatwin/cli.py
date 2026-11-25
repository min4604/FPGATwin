"""Command-line entry point: ``fpgatwin``."""

from __future__ import annotations

import argparse
import sys

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


def _cmd_ui(args: argparse.Namespace) -> int:
    import webbrowser

    from fpgatwin.ui.server import create_server

    board = load_board(args.board)
    try:
        server = create_server(board, port=args.port, speed=args.speed, clock=args.clock)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    print(f"FPGATwin UI ({board['board']['name']}, demo engine): {server.url}", flush=True)
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
    p_ui.add_argument("--clock", help="clock driving the demo engine (default: auto)")
    p_ui.add_argument("--no-browser", action="store_true", help="don't open a browser")
    p_ui.set_defaults(func=_cmd_ui)

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
