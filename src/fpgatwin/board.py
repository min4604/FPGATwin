"""Board definition loader (``boards/<id>/board.yaml``)."""

from __future__ import annotations

from importlib import resources
from typing import Any

import yaml

_BOARDS_PKG = "fpgatwin.boards"


def list_boards() -> list[str]:
    """Return the ids of all bundled boards, sorted."""
    root = resources.files(_BOARDS_PKG)
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.joinpath("board.yaml").is_file()
    )


def load_board(board_id: str) -> dict[str, Any]:
    """Load and return the parsed ``board.yaml`` for *board_id*.

    Raises ``KeyError`` if the board is not bundled.
    """
    path = resources.files(_BOARDS_PKG).joinpath(board_id, "board.yaml")
    if not path.is_file():
        raise KeyError(f"unknown board '{board_id}' (available: {', '.join(list_boards())})")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("board", {}).get("id") != board_id:
        raise ValueError(f"{board_id}/board.yaml: board.id does not match directory name")
    return data


def pin_map(board: dict[str, Any]) -> dict[str, str]:
    """Return a flat ``{physical_pin: label}`` map of every pin the board defines."""
    pins: dict[str, str] = {}

    def add(pin: str, label: str) -> None:
        if pin in pins:
            raise ValueError(f"pin {pin} defined twice: '{pins[pin]}' and '{label}'")
        pins[pin] = label

    for clk in board.get("clocks", []):
        for role, pin in clk["pins"].items():
            add(pin, f"{clk['name']}.{role}")
    for per in board.get("peripherals", []):
        for item in per.get("items", []):
            add(item["pin"], item["label"])
        for role, pin in per.get("pins", {}).items():
            add(pin, f"{per['id']}.{role}")
    return pins
