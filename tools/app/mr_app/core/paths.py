"""Where things are: the project folder (this file is tools/app/mr_app/core/paths.py), its venv's Python, and the
folders the tool uses (output, work, finished)."""
from __future__ import annotations

import sys
from pathlib import Path

APP_PKG = Path(__file__).resolve().parents[1]          # tools/app/mr_app
APP_DIR = APP_PKG.parent                               # tools/app
REPO = APP_DIR.parents[1]                              # the project folder (MovieRecaps)
ASSETS = APP_PKG / "assets"
ICON_ICO = ASSETS / "icon.ico"
ICON_PNG = ASSETS / "icon.png"
FONTS = ASSETS / "fonts"
LAUNCHER = APP_DIR / "MovieRecaps.pyw"


def repo() -> Path:
    return REPO


def output_dir() -> Path:
    return REPO / "output"


def work_dir() -> Path:
    return REPO / "work"


def finished_dir() -> Path:
    return REPO / "finished"


def python_exe() -> Path:
    """The Python that runs the tool: the project's venv (console python.exe, never pythonw.exe -- the app reads
    its output), else the one running the app."""
    venv = REPO / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        return venv
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return exe.parent / "python.exe"
    return exe


def pythonw_exe() -> Path:
    """The Python without a console window that starts the app (the desktop shortcut)."""
    venv = REPO / ".venv" / "Scripts" / "pythonw.exe"
    if venv.is_file():
        return venv
    exe = Path(sys.executable)
    w = exe.parent / "pythonw.exe"
    return w if w.is_file() else exe
