"""The look (night 11, from docs\\ui_preview.html): a dark background with a thin fading grid, flat panels with thin
borders and square corners, red #e5484d with a coral #ff7a59 accent, the channels' own colours, Open Sans for text,
Bebas Neue for section titles and JetBrains Mono for times, dates and slates. Every colour of the reference's
``:root`` is here under its own name; the widgets (widgets.py) paint themselves with them, so a hover can fade.
"""
from __future__ import annotations

from ..core import paths

# the reference's :root
BG = "#0e0e0e"                  # --bg: the page
PANEL = "#141414"               # --panel: a card, a column
PANEL2 = "#1a1a1a"              # --panel2: a button, a board card
LINE = "#262626"                # --line: borders
LINE2 = "#333333"               # --line2: stronger borders
TEXT = "#f2f2f2"                # --fg
MUTED = "#9a9a9a"               # --muted
DIM = "#6b6b6b"                 # --dim
RED = "#e5484d"                 # --red: the accent
RED_HI = "#ff6a6e"              # the primary button's hover fill
RED_SOFT = "#2a1416"            # --redsoft: a button's hover fill
WINE = "#3a1418"                # --wine
WINE_LINE = "#4a2a2c"           # a hovered row's / card's border
CORAL = "#ff7a59"               # --coral: labels, the slate's numbers, today's square
GREEN = "#3fb950"               # --ok: PASS
GREEN_SOFT = "#12251a"
GREEN_LINE = "#24502c"
AMBER = "#d29922"               # --warn: OK with a note, a waiting note
AMBER_SOFT = "#2a2110"
AMBER_LINE = "#5a4513"
BLUE = "#5b9cf5"                # --info: running
BLUE_SOFT = "#121c2c"
BLUE_LINE = "#22406b"
RED_LINE = "#5a2326"
LOU = "#f0a84a"                 # the channels' dots
MIKE = "#e5484d"
FLICK = "#a78bfa"
SCENE = "#1d9bf0"               # SceneProof (night 15): its verified badge's blue

# kept for the older names the tools still use
SURFACE = PANEL
SURFACE_HI = "#171717"
FILL = "#1c1c1c"
LINE_HI = LINE2
RED_SOFT_A = "rgba(229,72,77,28)"

FONT = "Open Sans"              # set by load_fonts()
DISPLAY = "Bebas Neue"
MONO = "JetBrains Mono"
GRID_STEP = 48                  # px between the grid's lines (the reference: 48)
GRID_COLOUR = "#1b1b1b"

CHANNEL_COLOURS = {"LouLaughs": LOU, "MikeMemesYou": MIKE, "Flick707": FLICK, "SceneProof": SCENE}

# a result word -> its pill: (text colour, background, border)
PILLS = {
    "waiting": (MUTED, PANEL2, LINE2), "ready": (MUTED, PANEL2, LINE2), "queued": (MUTED, PANEL2, LINE2),
    "running": (BLUE, BLUE_SOFT, BLUE_LINE),
    "PASS": (GREEN, GREEN_SOFT, GREEN_LINE), "ready to edit": (GREEN, GREEN_SOFT, GREEN_LINE),
    "OK with a note": (AMBER, AMBER_SOFT, AMBER_LINE), "note": (AMBER, AMBER_SOFT, AMBER_LINE),
    "FAIL": (RED, RED_SOFT, RED_LINE), "PICTURE NOT FOUND": (RED, RED_SOFT, RED_LINE),
    "NOT IN THIS RAW": (RED, RED_SOFT, RED_LINE), "WRONG PAIR": (RED, RED_SOFT, RED_LINE),
    "STOPPED": (DIM, PANEL2, LINE2),
}
STATE_COLOURS = {k: v[0] for k, v in PILLS.items()}


# ---------------------------------------------------------------------------------------------------------------------
# run 18 (task 8): the "Mono + red" look -- the default; Settings -> "Classic look" keeps the look above
# ---------------------------------------------------------------------------------------------------------------------

LOOK = "classic"                # apply_look() sets it: "mono" or "classic"
MONO_LOOK = False               # the widgets that draw themselves otherwise in the Mono + red look ask this


def apply_look(look: str | None) -> str:
    """Set the look before any widget is made (app.py, at the start): "mono" (the default) -- near-black, one
    monospace font, uppercase labels, red only for failures, counts and the one main button, green for PASS / done,
    amber for running, thin lines instead of boxes, no glows or gradients -- or "classic" (as before: nothing
    changes). Returns the look set."""
    global LOOK, MONO_LOOK, BG, PANEL, PANEL2, LINE, LINE2, TEXT, MUTED, DIM, RED, RED_HI, RED_SOFT, WINE
    global WINE_LINE, CORAL, GREEN, GREEN_SOFT, GREEN_LINE, AMBER, AMBER_SOFT, AMBER_LINE, BLUE, BLUE_SOFT, BLUE_LINE
    global RED_LINE, SURFACE, SURFACE_HI, FILL, LINE_HI, GRID_COLOUR, PILLS, STATE_COLOURS
    look = "classic" if str(look or "").strip().lower() == "classic" else "mono"
    LOOK = look
    MONO_LOOK = look == "mono"
    if not MONO_LOOK:
        return look
    BG = "#0a0a0a"
    PANEL = "#0d0d0d"
    PANEL2 = "#121212"
    LINE = "#1a1a1a"
    LINE2 = "#333333"
    TEXT = "#dcdcdc"
    MUTED = "#9a9a9a"
    DIM = "#6b6b6b"
    RED = "#e5484d"
    RED_HI = "#ef5a5f"
    RED_SOFT = "#1d0f10"
    WINE = "#1d0f10"
    WINE_LINE = "#333333"
    CORAL = DIM                 # (no second accent: labels are dim)
    GREEN = "#58c470"
    GREEN_SOFT = "#0f1a12"
    GREEN_LINE = "#24502c"
    AMBER = "#d6a12a"
    AMBER_SOFT = "#1d170b"
    AMBER_LINE = "#5a4513"
    BLUE, BLUE_SOFT, BLUE_LINE = AMBER, AMBER_SOFT, AMBER_LINE      # running is amber
    RED_LINE = "#5a2326"
    SURFACE, SURFACE_HI, FILL, LINE_HI = PANEL, "#111111", "#141414", LINE2
    GRID_COLOUR = BG            # no grid
    PILLS = {
        "waiting": (DIM, PANEL2, LINE2), "ready": (DIM, PANEL2, LINE2), "queued": (DIM, PANEL2, LINE2),
        "running": (AMBER, AMBER_SOFT, AMBER_LINE),
        "PASS": (GREEN, GREEN_SOFT, GREEN_LINE), "ready to edit": (GREEN, GREEN_SOFT, GREEN_LINE),
        "OK with a note": (AMBER, AMBER_SOFT, AMBER_LINE), "note": (AMBER, AMBER_SOFT, AMBER_LINE),
        "FAIL": (RED, RED_SOFT, RED_LINE), "PICTURE NOT FOUND": (RED, RED_SOFT, RED_LINE),
        "NOT IN THIS RAW": (RED, RED_SOFT, RED_LINE), "WRONG PAIR": (RED, RED_SOFT, RED_LINE),
        "STOPPED": (DIM, PANEL2, LINE2),
    }
    STATE_COLOURS = {k: v[0] for k, v in PILLS.items()}
    return look


def mono_fonts() -> None:
    """The Mono + red look: one monospace font for the whole app (the bundled JetBrains Mono; Cascadia Mono or
    Consolas when it can't load) -- called after load_fonts()."""
    global FONT, DISPLAY
    if not MONO_LOOK:
        return
    from PySide6.QtGui import QFontDatabase
    fams = set(QFontDatabase.families())
    for f in (MONO, "JetBrains Mono", "Cascadia Mono", "Consolas"):
        if f in fams:
            FONT = DISPLAY = f
            return


def channel_colour(name: str | None) -> str:
    """A channel's dot colour (red for one the app does not know)."""
    if not name:
        return DIM
    for k, c in CHANNEL_COLOURS.items():
        if k.lower() == str(name).lower():
            return c
    return RED


def pill_colours(word: str) -> tuple[str, str, str]:
    return PILLS.get(word) or PILLS.get(word.upper()) or PILLS.get(word.lower()) or (MUTED, PANEL2, LINE2)


def load_fonts() -> str:
    """Register the bundled fonts (Open Sans, Bebas Neue, JetBrains Mono; all SIL OFL) -- the text family's name; the
    system's Segoe UI / Impact / Consolas stand in when one cannot load."""
    global DISPLAY, MONO
    from PySide6.QtGui import QFontDatabase
    fam = None
    for f in sorted(paths.FONTS.glob("*.ttf")):
        i = QFontDatabase.addApplicationFont(str(f))
        fams = QFontDatabase.applicationFontFamilies(i) if i >= 0 else []
        if fams and f.name.startswith("OpenSans") and fam is None:
            fam = fams[0]
        elif fams and f.name.startswith("BebasNeue"):
            DISPLAY = fams[0]
        elif fams and f.name.startswith("JetBrainsMono"):
            MONO = fams[0]
    if DISPLAY not in QFontDatabase.families():
        DISPLAY = "Impact"
    if MONO not in QFontDatabase.families():
        MONO = "Consolas"
    return fam or "Segoe UI"


WARM_TEXT = "\U0001F62D\U0001F36B ★ ▲ → ✓ ·"     # an emoji and the symbols the app draws


def warm_fonts(f) -> None:
    """Windows looks up its fallback fonts (emoji, symbols) the first time such a character is drawn: about half a
    second, on the window's thread -- a visible stall on the first page that shows a title with an emoji. A background
    thread cannot do it for the window (the lookup holds it up all the same), so app.py does it once here, before the
    window shows."""
    from PySide6.QtGui import QFontMetrics
    QFontMetrics(f).horizontalAdvance(WARM_TEXT)


def qss(font: str) -> str:
    """Only what stays still: inputs, scroll bars, tooltips, plain dialogs. Nothing here changes on hover (a hover
    must fade, and a style sheet cannot fade) -- the buttons, cards and rows paint themselves (widgets.py)."""
    # (no font-family here: a style sheet's family beats every setFont -- the app's font is set in app.py, and the
    #  section titles, slates and times set their own Bebas Neue / JetBrains Mono)
    del font
    return f"""
* {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Page, QWidget#Transparent {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,34); min-height: 40px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: rgba(255,255,255,34); min-width: 40px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0;
    width: 0; }}
QToolTip {{ background: #000000; color: {TEXT}; border: 1px solid {LINE2}; padding: 4px 7px; font-family: '{MONO}';
           font-size: 11px; }}
QLabel#Title {{ color: {TEXT}; }}
QLabel#Lead {{ color: {MUTED}; font-size: 14px; }}
QLabel#Section {{ color: {TEXT}; }}
QLabel#Muted {{ color: {MUTED}; font-size: 13px; }}
QLabel#Dim {{ color: {DIM}; font-size: 12px; }}
QLabel#Big {{ font-size: 24px; font-weight: 800; }}
QLabel#Name {{ font-size: 15px; font-weight: 700; }}
QLabel#Small {{ color: {MUTED}; font-size: 12px; }}
QLabel#State {{ font-size: 12px; font-weight: 700; }}
QLabel#Mono {{ color: {DIM}; font-family: '{MONO}'; font-size: 11px; }}
QLabel#Tag {{ color: {MUTED}; border: 1px solid {LINE2}; padding: 2px 7px; }}
QLabel#TagRed {{ color: {RED}; border: 1px solid {RED}; padding: 2px 7px; }}
QPushButton {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {LINE2}; padding: 7px 14px; }}
QPushButton:pressed {{ background: {RED_SOFT}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {LINE}; }}
QLineEdit, QPlainTextEdit, QTextBrowser, QSpinBox {{ background: #111111; border: 1px solid {LINE2}; padding: 6px;
    selection-background-color: {RED}; font-size: 13px; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border-color: {RED}; }}
QCheckBox {{ color: {MUTED}; font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {LINE2}; background: #111111; }}
QCheckBox::indicator:checked {{ background: {RED}; border-color: {RED}; }}
QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ font-size: 13px; }}
QMenu {{ background: #151515; border: 1px solid {LINE2}; padding: 5px; }}
QMenu::item {{ padding: 7px 18px 7px 10px; color: {TEXT}; }}
QMenu::item:selected {{ background: {RED_SOFT}; }}
"""
