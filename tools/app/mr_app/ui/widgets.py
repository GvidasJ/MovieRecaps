"""The app's building blocks in the reference's style: the grid background, cards, small tags, the coloured state
square, section headers and the drop zones."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme


def font(size: float, weight: int = 400, spacing: float = 0.0, family: str | None = None) -> QFont:
    f = QFont(family or theme.FONT)
    f.setPixelSize(int(round(size)))
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0 + spacing)
    return f


class GridBackground(QWidget):
    """The page behind everything: near-black with a thin grid that fades out downwards (the reference's hero)."""

    def paintEvent(self, _e) -> None:                     # noqa: N802 - Qt's name
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        h, w, step = self.height(), self.width(), theme.GRID_STEP
        fade = max(1.0, min(h, 900.0))
        x = (w % step) // 2
        while x < w:
            g = QLinearGradient(QPointF(x, 0), QPointF(x, fade))
            g.setColorAt(0.0, QColor(255, 255, 255, theme.GRID_ALPHA))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(QPen(g, 1))
            p.drawLine(QPointF(x + 0.5, 0), QPointF(x + 0.5, fade))
            x += step
        y = 0
        while y < fade:
            a = int(theme.GRID_ALPHA * (1.0 - y / fade))
            if a > 0:
                p.setPen(QPen(QColor(255, 255, 255, a), 1))
                p.drawLine(QPointF(0, y + 0.5), QPointF(w, y + 0.5))
            y += step
        p.end()


def label(text: str = "", name: str | None = None, wrap: bool = False, select: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    if select:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def tag(text: str, red: bool = False) -> QLabel:
    """A small outlined uppercase tag ('FAST', 'INSTANT DELIVERY' in the reference)."""
    lb = QLabel(text.upper())
    lb.setObjectName("TagRed" if red else "Tag")
    lb.setFont(font(11, 600, 6))
    lb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lb


class Square(QWidget):
    """The small coloured square of a state."""

    def __init__(self, colour: str = theme.MUTED, size: int = 7):
        super().__init__()
        self._c = QColor(colour)
        self.setFixedSize(size + 2, size + 2)

    def set_colour(self, colour: str) -> None:
        self._c = QColor(colour)
        self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.fillRect(QRectF(1, 1, self.width() - 2, self.height() - 2), self._c)
        p.end()


class StateLabel(QWidget):
    """A coloured square and a state word ('PASS', 'running') with a muted line after it."""

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        self.sq = Square()
        self.word = label("", "State")
        self.note = label("", "Muted", wrap=True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.sq, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.word, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.note, 1, Qt.AlignmentFlag.AlignVCenter)

    def set(self, word: str, note: str = "", colour: str | None = None) -> None:
        c = colour or theme.STATE_COLOURS.get(word, theme.MUTED)
        self.sq.set_colour(c)
        self.word.setText(word.upper() if word in theme.STATE_COLOURS and word.islower() else word)
        self.word.setStyleSheet(f"color: {c};")
        self.note.setText(note)


class Card(QFrame):
    """A flat card: thin light border, square corners, lighter under the mouse."""

    def __init__(self, hover: bool = True):
        super().__init__()
        self.setObjectName("Card")
        self._hover = hover
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 18)
        self.lay.setSpacing(10)

    def enterEvent(self, e) -> None:                      # noqa: N802
        if self._hover:
            self.setProperty("hot", True)
            self.style().unpolish(self)
            self.style().polish(self)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        if self._hover:
            self.setProperty("hot", False)
            self.style().unpolish(self)
            self.style().polish(self)
        super().leaveEvent(e)


class SectionHeader(QWidget):
    """'■ Queue' on the left (the reference's '◆ Accounts'), anything on the right."""

    def __init__(self, title: str, right: Sequence[QWidget] = ()):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 4)
        lay.setSpacing(10)
        lay.addWidget(Square(theme.RED, 7), 0, Qt.AlignmentFlag.AlignVCenter)
        t = label(title, "Section")
        t.setFont(font(17, 700, -1))
        lay.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        for w in right:
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)


class Segmented(QWidget):
    """Two or three small toggles, one on ('FAST | THOROUGH')."""
    changed = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]], value: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._b: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text.upper())
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(font(11, 600, 6))
            b.clicked.connect(lambda _=False, k=key: self.set(k, emit=True))
            lay.addWidget(b)
            self._b[key] = b
        self.set(value)

    def set(self, value: str, emit: bool = False) -> None:
        for k, b in self._b.items():
            b.setChecked(k == value)
        self.value = value
        if emit:
            self.changed.emit(value)

    def setEnabled(self, on: bool) -> None:               # noqa: N802
        for b in self._b.values():
            b.setEnabled(on)


class DropZone(QFrame):
    """A big area that takes dropped files and folders (Windows Explorer): ``dropped(list of Path)``."""
    dropped = Signal(list)

    def __init__(self, title: str, lines: Sequence[str], buttons: Sequence[tuple[str, Callable[[], None]]] = (),
                 big: bool = True):
        super().__init__()
        self.setObjectName("Drop")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 30 if big else 20, 28, 30 if big else 20)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = label(title)
        t.setFont(font(30 if big else 20, 800, -2))
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        for ln in lines:
            m = label(ln, "Muted", wrap=True)
            m.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(m)
        if buttons:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            for text, fn in buttons:
                b = QPushButton(text)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(fn)
                row.addWidget(b)
            row.addStretch(1)
            lay.addSpacing(6)
            lay.addLayout(row)

    def _hot(self, on: bool) -> None:
        self.setProperty("hot", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e) -> None:                  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hot(True)

    def dragLeaveEvent(self, e) -> None:                  # noqa: N802
        self._hot(False)

    def dropEvent(self, e) -> None:                       # noqa: N802
        self._hot(False)
        got = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if got:
            e.acceptProposedAction()
            self.dropped.emit(got)


def hbox(*widgets: QWidget, spacing: int = 8, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for i, w in enumerate(widgets):
        if stretch_at is not None and i == stretch_at:
            lay.addStretch(1)
        lay.addWidget(w)
    if stretch_at is not None and stretch_at >= len(widgets):
        lay.addStretch(1)
    return lay


def dark_title_bar(w: QWidget) -> None:
    """Windows 10 / 11: the window's title bar dark too (DWMWA_USE_IMMERSIVE_DARK_MODE); nothing elsewhere."""
    try:
        import ctypes
        hwnd = int(w.winId())
        on = ctypes.c_int(1)
        for attr in (20, 19):                      # 20: Windows 11 / 10 20H1+, 19: older Windows 10
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:  # noqa: BLE001 - not Windows
        pass
