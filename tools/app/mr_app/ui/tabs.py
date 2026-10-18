"""The tabs (night 11): Match cuts · Board · Progress · History, and the small sub-tabs inside a page.

* A tab under the mouse fades (0.45 s): its text brightens and a dark fill comes in; the active tab has a red
  underline that grows from its middle (no sliding line between tabs).
* A new page glides in from the side of the tab clicked (a tab to the right: from the right), 0.45 s, no blur: the
  page is drawn into a picture once and that picture moves 24 px and fades in, then the real page takes its place.
"""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import (QGraphicsOpacityEffect, QHBoxLayout, QSizePolicy, QStackedWidget, QWidget)

from . import motion as M
from . import theme
from . import widgets as W


class Tab(QWidget):
    clicked = Signal()

    def __init__(self, text: str, small: bool = False):
        super().__init__()
        self.text = text
        self.small = small
        self.badge = ""
        self.selected = False
        self.hover = M.Fader(self, self.update, M.TAB_MS, M.TAB_MS)      # "tab hover is about 0.45 s"
        self.sel = M.Fader(self, self.update, M.TAB_MS, M.TAB_MS)
        self.light = M.Fader(self, self.update, 900, 900)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _font(self):
        return W.font(11, 600, 8) if self.small else W.font(13, 600)

    def sizeHint(self) -> QSize:                          # noqa: N802
        fm = QFontMetrics(self._font())
        t = self.text.upper() if self.small else self.text
        extra = (QFontMetrics(W.mono(10, 500)).horizontalAdvance(self.badge) + 10) if self.badge else 0
        return QSize(fm.horizontalAdvance(t) + (20 if self.small else 24) + extra, fm.height() + (10 if self.small else 14))

    def set_selected(self, on: bool, animate: bool = True) -> None:
        self.selected = on
        self.sel.to(1.0 if on else 0.0) if animate else self.sel.set(1.0 if on else 0.0)

    def set_badge(self, text: str) -> None:
        self.badge = text
        self.updateGeometry()
        self.update()

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        self.light.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        self.light.to(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:               # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        h, s = self.hover.value, self.sel.value
        if h > 0:
            p.fillRect(r, M.alpha(theme.PANEL2, h))
            W.sweep(p, r, self.light.value, peak=0.03)
        p.setFont(self._font())
        p.setPen(M.mix(theme.MUTED, theme.TEXT, max(h, s)))
        t = self.text.upper() if self.small else self.text
        fm = QFontMetrics(self._font())
        tw = fm.horizontalAdvance(t)
        bw = (QFontMetrics(W.mono(10, 500)).horizontalAdvance(self.badge) + 10) if self.badge else 0
        x = (r.width() - tw - bw) / 2
        p.drawText(QRectF(x, 0, tw + 2, r.height() - 1), Qt.AlignmentFlag.AlignVCenter, t)
        if self.badge:
            br = QRectF(x + tw + 6, r.center().y() - 7, bw - 4, 14)
            p.fillRect(br, QColor(theme.RED))
            p.setFont(W.mono(10, 500))
            p.setPen(QColor("#ffffff"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, self.badge)
        # the underline: active 100 % wide, hovered 60 % at 35 %, else 30 % at 0 -- growing from the middle
        op = s + (1.0 - s) * 0.35 * h
        scale = s + (1.0 - s) * (0.3 + 0.3 * h)
        if op > 0.002:
            inset = 8.0 if self.small else 12.0
            full = r.width() - 2 * inset
            w = full * scale
            p.fillRect(QRectF(inset + (full - w) / 2, r.height() - 2, w, 2), M.alpha(theme.RED, op))
        p.end()


class TabBar(QWidget):
    """A row of tabs; ``changed(index, from_index)``."""
    changed = Signal(int, int)

    def __init__(self, titles: Sequence[str], small: bool = False, spacing: int = 4):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        self.tabs: list[Tab] = []
        for i, t in enumerate(titles):
            tab = Tab(t, small)
            tab.clicked.connect(lambda i=i: self.select(i, emit=True))
            lay.addWidget(tab)
            self.tabs.append(tab)
        self.index = 0
        if self.tabs:
            self.tabs[0].set_selected(True, animate=False)

    def select(self, i: int, emit: bool = False) -> None:
        if not (0 <= i < len(self.tabs)):
            return
        old = self.index
        if i == old:
            return
        self.index = i
        for k, t in enumerate(self.tabs):
            t.set_selected(k == i)
        if emit:
            self.changed.emit(i, old)


class PageStack(QStackedWidget):
    """A stack of pages where a new page glides in from the side of the tab clicked (``glide_to(index, from_right)``).
    During the glide the real page is hidden behind a picture of itself (one grab), so nothing heavy is drawn twice."""

    SHIFT = 24.0

    def __init__(self):
        super().__init__()
        self._pm: QPixmap | None = None
        self._dir = 1.0
        self._page: QWidget | None = None
        self.glide = M.Fader(self, self._step, M.GLIDE_MS, M.GLIDE_MS, M.GLIDE)
        self._overlay = _Overlay(self)
        self._overlay.hide()

    def glide_to(self, index: int, from_right: bool = True) -> None:
        if index == self.currentIndex() or not (0 <= index < self.count()):
            self.setCurrentIndex(index)
            return
        self._finish()
        self.setCurrentIndex(index)
        page = self.currentWidget()
        if not M.enabled() or page is None:
            return
        page.resize(self.size())
        self._pm = page.grab()
        self._page = page
        eff = QGraphicsOpacityEffect(page)
        eff.setOpacity(0.0)
        page.setGraphicsEffect(eff)
        self._dir = 1.0 if from_right else -1.0
        self._overlay.setGeometry(self.rect())
        self._overlay.show()
        self._overlay.raise_()
        self.glide.set(0.0)
        self.glide.to(1.0)

    def _step(self) -> None:
        if self._pm is None:
            return
        self._overlay.update()
        if self.glide.value >= 1.0:
            QTimer.singleShot(0, self._finish)

    def _finish(self) -> None:
        if self._page is not None:
            self._page.setGraphicsEffect(None)
        self._page, self._pm = None, None
        self._overlay.hide()

    def resizeEvent(self, e) -> None:                     # noqa: N802
        super().resizeEvent(e)
        self._overlay.setGeometry(self.rect())


class _Overlay(QWidget):
    def __init__(self, stack: PageStack):
        super().__init__(stack)
        self.stack = stack
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        st = self.stack
        if st._pm is None:                                # noqa: SLF001 - the stack's own picture
            return
        t = st.glide.value
        p = QPainter(self)
        p.setOpacity(t)
        p.drawPixmap(QPointF(st._dir * st.SHIFT * (1.0 - t), 0), st._pm)   # noqa: SLF001
        p.end()
