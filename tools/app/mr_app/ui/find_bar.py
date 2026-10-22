"""Ctrl+F on every page (night 15, task 11): a search box over the page you are on. It finds the words you type in
everything the page shows (the queue's rows, the projects, the board's cards, a report ...), outlines the one it is
at, scrolls to it and says "3 of 12"; Enter / F3 the next one, Shift+Enter the one before. Esc clears it and closes
it. No page needs anything for it: it reads the labels, buttons and boxes the page shows (core.find decides)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea,
                               QWidget)

from ..core import find as F
from . import theme
from . import widgets as W


def texts_of(page: QWidget) -> list[tuple[QWidget, str]]:
    """Every widget of the page that shows words (visible ones, in reading order), with its words."""
    out: list[tuple[QWidget, str]] = []
    for w in page.findChildren(QWidget):
        if not w.isVisibleTo(page):
            continue
        if isinstance(w, QLabel):
            t = F.plain_text(w.text())
        elif isinstance(w, QAbstractButton):
            t = w.text()
        elif isinstance(w, QLineEdit):
            t = w.text()
        elif isinstance(w, QPlainTextEdit):
            t = w.toPlainText()
        else:
            continue
        if t and t.strip():
            out.append((w, " ".join(t.split())))
    out.sort(key=lambda x: (x[0].mapTo(page, x[0].rect().topLeft()).y(), x[0].mapTo(page, x[0].rect().topLeft()).x()))
    return out


def find_in(page: QWidget, query: str) -> list[QWidget]:
    """The page's widgets whose words answer the search (core.find.matches), top to bottom."""
    q = (query or "").strip()
    if not q:
        return []
    return [w for w, t in texts_of(page) if F.matches(q, t)]


class _Outline(QWidget):
    """The coral outline around the match the bar is at (drawn over the page, lets the mouse through)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.CORAL), 2))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)
        p.end()


class FindBar(QWidget):
    """The search box. ``page_of()`` gives the page shown now."""

    def __init__(self, page_of, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.page_of = page_of
        self.hits: list[QWidget] = []
        self.at = -1
        self._page: QWidget | None = None              # the page the hits are of
        self._outline: _Outline | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(8)
        self.box = QLineEdit()
        self.box.setPlaceholderText("Find on this page -- words, or an episode: S09E05, 9x05, ep 5  (Esc clears)")
        self.box.setClearButtonEnabled(True)
        self.box.textChanged.connect(self._changed)
        self.box.installEventFilter(self)
        lay.addWidget(self.box, 1)
        self.count = W.mono_label("", theme.MUTED)
        lay.addWidget(self.count)
        self.prev_b = W.Button("↑", "Small")
        self.prev_b.setToolTip("The one before (Shift+Enter)")
        self.prev_b.clicked.connect(lambda: self.step(-1))
        self.next_b = W.Button("↓", "Small")
        self.next_b.setToolTip("The next one (Enter, F3)")
        self.next_b.clicked.connect(lambda: self.step(1))
        close = W.Button("Close", "Ghost")
        close.clicked.connect(self.close_bar)
        for b in (self.prev_b, self.next_b, close):
            lay.addWidget(b)
        self.hide()

    # -- open / close --------------------------------------------------------------------------------------------------
    def open_bar(self) -> None:
        self.show()
        self.box.setFocus()
        self.box.selectAll()
        if self.box.text():
            self._changed(self.box.text())

    def close_bar(self) -> None:
        self.box.clear()
        self._clear()
        self.hide()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.box and e.type() == QEvent.Type.KeyPress:
            k = e.key()
            if k == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F3):
                self.step(-1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
        return super().eventFilter(obj, e)

    # -- searching -----------------------------------------------------------------------------------------------------
    def _clear(self) -> None:
        self.hits, self.at = [], -1
        self.count.setText("")
        if self._outline is not None:
            self._outline.hide()

    def refresh(self) -> None:
        """Another page shown (or the page changed): its own hits."""
        if self.isVisible() and self.box.text().strip():
            self._changed(self.box.text())

    def _changed(self, text: str, keep: bool = False) -> None:
        page = self.page_of()
        self._page = page
        old = self.hits[self.at] if keep and 0 <= self.at < len(self.hits) else None
        self._clear()
        if page is None or not text.strip():
            return
        self.hits = find_in(page, text)
        if not self.hits:
            self.count.setText("none")
            return
        self.at = self.hits.index(old) if old in self.hits else 0
        self._show()

    def step(self, d: int) -> None:
        if not self.hits or self.page_of() is not self._page:
            self._changed(self.box.text())
            return
        self.at = (self.at + d) % len(self.hits)
        self._show()

    def _show(self) -> None:
        w = self.hits[self.at]
        self.count.setText(f"{self.at + 1} of {len(self.hits)}")
        sa = w.parent()
        while sa is not None and not isinstance(sa, QScrollArea):
            sa = sa.parent()
        if isinstance(sa, QScrollArea):
            sa.ensureWidgetVisible(w, 40, 80)
        host = sa.viewport() if isinstance(sa, QScrollArea) else self.page_of()
        if host is None:
            return
        if self._outline is None or self._outline.parent() is not host:
            self._outline = _Outline(host)
        tl = w.mapTo(host, w.rect().topLeft())
        self._outline.setGeometry(QRect(tl, w.size()).adjusted(-4, -3, 4, 3))
        self._outline.show()
        self._outline.raise_()
