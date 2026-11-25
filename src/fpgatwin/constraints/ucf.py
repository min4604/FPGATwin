"""Xilinx ISE ``.ucf`` constraint parser (not implemented yet).

Planned scope (MVP):
  * ``NET "name" LOC = "PIN";`` including bus notation ``NET "led<0>"``
  * ``IOSTANDARD``, ``PULLUP`` / ``PULLDOWN`` attributes
  * comments (``#``), multiple attributes separated by ``|``
"""

from __future__ import annotations

from pathlib import Path


def parse_ucf(path: str | Path) -> dict[str, str]:
    """Parse *path* and return ``{port_name: physical_pin}``."""
    raise NotImplementedError("UCF parser is not implemented yet")
