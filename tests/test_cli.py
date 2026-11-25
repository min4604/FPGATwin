import subprocess
import sys

import pytest

from fpgatwin import __version__
from fpgatwin.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_boards_lists_sp601(capsys):
    assert main(["boards"]) == 0
    assert "xilinx_sp601" in capsys.readouterr().out


def test_board_show(capsys):
    assert main(["board", "xilinx_sp601"]) == 0
    out = capsys.readouterr().out
    assert "XC6SLX16-2CSG324" in out
    assert "SYSCLK" in out


def test_unknown_board_returns_error(capsys):
    assert main(["board", "no_such_board"]) == 2
    assert "unknown board" in capsys.readouterr().err


def test_python_m_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "fpgatwin", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout
