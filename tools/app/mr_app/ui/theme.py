"""The look: copied from the style reference (highimpression.org) -- near-black background with a thin grid, flat cards
with a thin light border and square corners, small outlined uppercase tags, Open Sans, white text with grey labels
-- with RED as the accent instead of its green. Small coloured squares give a state (as its 'in stock' / 'sold out').
"""
from __future__ import annotations

from ..core import paths

BG = "#0e0e0e"                  # the page (rgb 14,14,14)
SURFACE = "#121212"             # a card
SURFACE_HI = "#171717"          # a card under the mouse, an input
FILL = "#1c1c1c"                # the active tab, a pressed button
LINE = "rgba(255,255,255,20)"   # borders (8 %)
LINE_HI = "rgba(255,255,255,46)"
TEXT = "#ededed"
MUTED = "#9f9f9f"
DIM = "#6b6b6b"
RED = "#e5484d"                 # the accent
RED_HI = "#f05a5f"
RED_SOFT = "rgba(229,72,77,28)"
GREEN = "#3ecf8e"               # PASS
AMBER = "#f5a524"               # OK with a note, waiting
BLUE = "#7aa2f7"                # running

FONT = "Open Sans"
GRID_STEP = 44                  # px between the grid's lines
GRID_ALPHA = 9                  # its lines' alpha (0-255) at the top; it fades out downwards

STATE_COLOURS = {"PASS": GREEN, "OK with a note": AMBER, "FAIL": RED, "PICTURE NOT FOUND": RED,
                 "NOT IN THIS RAW": RED, "STOPPED": DIM, "ready": MUTED, "waiting": AMBER, "running": BLUE}


def load_fonts() -> str:
    """Register the bundled Open Sans (OFL) -- the family name to use; the system's Segoe UI when it cannot load."""
    from PySide6.QtGui import QFontDatabase
    fam = None
    for f in sorted(paths.FONTS.glob("OpenSans-*.ttf")):
        i = QFontDatabase.addApplicationFont(str(f))
        if i >= 0 and fam is None:
            fams = QFontDatabase.applicationFontFamilies(i)
            fam = fams[0] if fams else None
    return fam or "Segoe UI"


def qss(font: str) -> str:
    return f"""
* {{ font-family: '{font}'; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Page, QWidget#Transparent {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,30); min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,60); }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QToolTip {{ background: {SURFACE_HI}; color: {TEXT}; border: 1px solid {LINE_HI}; padding: 6px; }}

QFrame#Strip {{ background: {BG}; border-bottom: 1px solid {LINE}; }}
QLabel#StripText {{ color: {DIM}; font-size: 11px; }}
QFrame#Nav {{ background: rgba(14,14,14,235); border-bottom: 1px solid {LINE}; }}
QLabel#Brand {{ font-size: 15px; font-weight: 700; }}
QPushButton#NavTab {{ background: transparent; border: none; color: {MUTED}; font-size: 13px; padding: 7px 12px; }}
QPushButton#NavTab:hover {{ color: {TEXT}; }}
QPushButton#NavTab:checked {{ background: {FILL}; color: {TEXT}; font-weight: 600; }}
QPushButton#NavTab:disabled {{ color: {DIM}; }}

QLabel#Title {{ color: {TEXT}; }}
QLabel#Lead {{ color: {MUTED}; font-size: 14px; }}
QLabel#Section {{ color: {TEXT}; font-size: 17px; font-weight: 700; }}
QLabel#Muted {{ color: {MUTED}; font-size: 13px; }}
QLabel#Dim {{ color: {DIM}; font-size: 12px; }}
QLabel#Big {{ font-size: 24px; font-weight: 800; }}
QLabel#Name {{ font-size: 20px; font-weight: 800; }}
QLabel#Small {{ color: {MUTED}; font-size: 12px; }}
QLabel#State {{ font-size: 12px; font-weight: 700; }}

QFrame#Card {{ background: {SURFACE}; border: 1px solid {LINE}; }}
QFrame#Card[hot="true"] {{ background: {SURFACE_HI}; border: 1px solid {LINE_HI}; }}
QFrame#Drop {{ background: {SURFACE}; border: 1px solid {LINE_HI}; }}
QFrame#Drop[hot="true"] {{ background: {RED_SOFT}; border: 1px solid {RED}; }}
QLabel#Tag {{ color: {MUTED}; border: 1px solid {LINE_HI}; padding: 2px 7px; }}
QLabel#TagRed {{ color: {RED}; border: 1px solid {RED}; padding: 2px 7px; }}

QPushButton {{ background: transparent; color: {TEXT}; border: 1px solid {LINE_HI}; padding: 7px 14px; }}
QPushButton:hover {{ border-color: rgba(255,255,255,110); background: {SURFACE_HI}; }}
QPushButton:pressed {{ background: {FILL}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {LINE}; }}
QPushButton#Primary {{ background: {RED}; border: 1px solid {RED}; color: #ffffff; font-weight: 700;
                      padding: 9px 22px; font-size: 13px; }}
QPushButton#Primary:hover {{ background: {RED_HI}; border-color: {RED_HI}; }}
QPushButton#Primary:disabled {{ background: #3a1d1e; border-color: #3a1d1e; color: #8a6466; }}
QPushButton#Small {{ padding: 4px 10px; font-size: 12px; }}
QPushButton#Ghost {{ border: none; color: {MUTED}; padding: 4px 8px; font-size: 12px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: {FILL}; }}
QPushButton#Seg {{ border: 1px solid {LINE_HI}; color: {MUTED}; padding: 3px 10px; }}
QPushButton#Seg:checked {{ background: {TEXT}; color: {BG}; border-color: {TEXT}; }}
QPushButton#Seg:disabled {{ color: {DIM}; }}
QPushButton#Seg:checked:disabled {{ background: #5a5a5a; color: {BG}; border-color: #5a5a5a; }}

QProgressBar {{ background: {FILL}; border: none; height: 4px; max-height: 4px; }}
QProgressBar::chunk {{ background: {RED}; }}
QLineEdit, QPlainTextEdit, QTextBrowser {{ background: {BG}; border: 1px solid {LINE_HI}; padding: 6px;
                                          selection-background-color: {RED}; font-size: 13px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: rgba(255,255,255,110); }}
QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ font-size: 13px; }}
"""
