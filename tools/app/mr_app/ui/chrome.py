"""The window's frame pieces (night 11): the intro, the timecode clock and the small text buttons of the top strip,
and the quote line under the top bar.

* Intro (1.3 s at the start, replayed from Settings): the viewfinder corners draw, the two halves of the triangle
  slide together, the name fades in -- all in the middle of the window -- then it fades away.
* The clock shows the time of day as a timecode, HH:MM:SS:FF at 24 frames a second (the frames in red).
* The quote line: one quote per part of the day (quotes.py); a click shows the next one; a change fades.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import motion as M
from . import theme
from . import widgets as W

INTRO_MS = 1300


def _phase(t_ms: float, start: float, dur: float, curve=None) -> float:
    x = max(0.0, min(1.0, (t_ms - start) / dur))
    return curve.valueForProgress(x) if curve is not None else x


class Intro(QWidget):
    """The intro over the whole window; ``play()``. Under 1.3 s, all centred."""
    done = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.t = INTRO_MS
        self._a = QVariantAnimation(self)
        self._a.setStartValue(0.0)
        self._a.setEndValue(float(INTRO_MS))
        self._a.setDuration(INTRO_MS)
        self._a.valueChanged.connect(self._step)
        self._a.finished.connect(self._end)
        self.hide()

    def play(self) -> None:
        if not M.enabled():
            return
        par = self.parentWidget()
        if par is not None:
            self.setGeometry(par.rect())
        self.t = 0.0
        self.show()
        self.raise_()
        self._a.stop()
        self._a.start()

    def playing(self) -> bool:
        return self.isVisible()

    def _step(self, v) -> None:
        self.t = float(v)
        self.update()

    def _end(self) -> None:
        self.hide()
        self.done.emit()

    def mousePressEvent(self, _e) -> None:                # noqa: N802 - a click skips it
        self._a.stop()
        self._end()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        t = self.t
        fade_out = 1.0 - _phase(t, 950, 350, M.SOFT)
        p.setOpacity(fade_out)
        p.fillRect(self.rect(), QColor(theme.BG))
        size = 96.0
        word_h = 30.0
        cx, cy = self.width() / 2.0, self.height() / 2.0
        top = cy - (size + 14 + word_h) / 2.0
        logo = QRectF(cx - size / 2, top, size, size)
        halves = _phase(t, 200, 550, M.GLIDE)
        W.draw_logo(p, logo, corners_share=_phase(t, 100, 450, M.SOFT), left=halves, right=halves,
                    slide=1.0 - halves, stroke=1.6)
        word = _phase(t, 550, 400)
        if word > 0:
            p.setOpacity(fade_out * word)
            f = W.font(15, 600)
            gap = 15 * 0.3                                  # letter-spacing .3em, as the reference
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, gap)
            p.setFont(f)
            fm = QFontMetrics(f)
            a, b = "MOVIE", "RECAPS"
            wa, wb = fm.horizontalAdvance(a), fm.horizontalAdvance(b)
            x = cx - (wa + wb - gap) / 2                    # (the spacing after the last letter is not text)
            y = top + size + 14
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(x, y, wa + 2, word_h), Qt.AlignmentFlag.AlignVCenter, a)
            p.setPen(QColor(theme.RED))
            p.drawText(QRectF(x + wa, y, wb + 2, word_h), Qt.AlignmentFlag.AlignVCenter, b)
        p.end()


class Timecode(QWidget):
    """HH:MM:SS:FF of the time of day (24 frames a second; the frames in red)."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._t = QTimer(self)
        self._t.timeout.connect(self.update)
        self.restart()

    def restart(self) -> None:
        self._t.start(42 if M.enabled() else 1000)

    def sizeHint(self) -> QSize:                          # noqa: N802
        fm = QFontMetrics(W.mono(11))
        return QSize(fm.horizontalAdvance("00:00:00:00") + 4, fm.height() + 2)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        now = time.time()
        lt = time.localtime(now)
        frames = int((now % 1.0) * 24) if M.enabled() else 0
        main, ff = f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}", f":{frames:02d}"
        p = QPainter(self)
        f = W.mono(11)
        p.setFont(f)
        fm = QFontMetrics(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter, main)
        p.setPen(QColor(theme.RED))
        p.drawText(QRectF(fm.horizontalAdvance(main), 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter,
                   ff)
        p.end()


class LinkButton(QWidget):
    """A small mono text button (the reference's "replay intro"): dim, fading to coral under the mouse."""
    clicked = Signal()

    def __init__(self, text: str):
        super().__init__()
        self.text = text
        self.hover = M.Fader(self, self.update, M.FADE_IN_MS, 400)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:                          # noqa: N802
        fm = QFontMetrics(W.mono(11))
        return QSize(fm.horizontalAdvance(self.text) + 4, fm.height() + 2)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:               # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setFont(W.mono(11))
        p.setPen(M.mix(theme.DIM, theme.CORAL, self.hover.value))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignVCenter, self.text)
        p.end()


class QuoteLine(QWidget):
    """'[MORNING]  “I just never thought I couldn't.”  — MCQUEEN', centred; a click asks for the next quote
    (``clicked``); ``show_quote`` fades the old one out and the new one in."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click for the next one")
        self.tag, self.text, self.who = "", "", ""
        self._next: tuple[str, str, str] | None = None
        self.op = M.Fader(self, self._faded, 600, 600)
        self.op.set(1.0)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def show_quote(self, tag: str, text: str, who: str = "") -> None:
        new = (tag, text, who)
        if new == (self.tag, self.text, self.who):
            return
        if not self.text or not M.enabled():
            self.tag, self.text, self.who = new
            self.op.set(1.0)
            self.update()
            return
        self._next = new
        self.op.to(0.0)

    def _faded(self) -> None:
        if self._next is not None and self.op.value <= 0.0:
            self.tag, self.text, self.who = self._next
            self._next = None
            self.op.to(1.0)
        self.update()

    def mouseReleaseEvent(self, e) -> None:               # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        if not self.text:
            return
        p = QPainter(self)
        ft, fq, fw = W.mono(9.5, 600, 14), W.font(15, 600), W.mono(11, 400, 6)
        fq.setItalic(True)
        q = f"“{self.text}”"
        w = f"— {self.who.upper()}" if self.who else ""
        mt, mq, mw = QFontMetrics(ft), QFontMetrics(fq), QFontMetrics(fw)
        tag = self.tag.upper()
        tw = mt.horizontalAdvance(tag) + 14 if tag else 0
        qw = min(mq.horizontalAdvance(q), self.width() - tw - 40 - (mw.horizontalAdvance(w) if w else 0))
        ww = mw.horizontalAdvance(w) if w else 0
        gap = 10
        total = tw + (gap if tw else 0) + qw + (gap + ww if w else 0)
        x = (self.width() - total) / 2
        cy = self.height() / 2 + 6
        p.setOpacity(1.0)
        if tag:
            r = QRectF(x, cy - 9, tw, 18)
            W.box(p, r, None, theme.LINE2)
            p.setFont(ft)
            p.setPen(QColor(theme.DIM))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tag)
            x += tw + gap
        p.setOpacity(self.op.value)
        p.setFont(fq)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(x, cy - 12, qw, 24), Qt.AlignmentFlag.AlignVCenter,
                   mq.elidedText(q, Qt.TextElideMode.ElideRight, int(qw)))
        x += qw + gap
        if w:
            p.setFont(fw)
            p.setPen(QColor(theme.CORAL))
            p.drawText(QRectF(x, cy - 10, ww + 2, 22), Qt.AlignmentFlag.AlignVCenter, w)
        p.end()
