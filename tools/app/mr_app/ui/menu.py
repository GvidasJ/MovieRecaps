"""A small right-click menu whose items fade under the mouse (a style sheet's menu highlight would jump)."""
from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from . import motion as M
from . import theme
from . import widgets as W


class _Item(QWidget):
    def __init__(self, text: str, fn: Callable[[], None], danger: bool, menu: "FadeMenu", enabled: bool = True):
        super().__init__()
        self.text, self.fn, self.danger, self.menu = text, fn, danger, menu
        self.on = enabled
        self.hover = M.Fader(self, self.update)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

    def sizeHint(self) -> QSize:                          # noqa: N802
        fm = QFontMetrics(W.font(13))
        return QSize(fm.horizontalAdvance(self.text) + 40, fm.height() + 14)

    def enterEvent(self, e) -> None:                      # noqa: N802
        if self.on:
            self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:               # noqa: N802
        if self.on and e.button() == Qt.MouseButton.LeftButton:
            self.menu.close()
            self.fn()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        if self.hover.value > 0:
            p.fillRect(self.rect(), M.alpha(theme.RED_SOFT, self.hover.value))
        p.setFont(W.font(13))
        p.setPen(QColor(theme.DIM if not self.on else ("#ff8a8d" if self.danger else theme.TEXT)))
        p.drawText(QRectF(self.rect()).adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, self.text)
        p.end()


class _Line(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(9)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.fillRect(QRectF(0, 4, self.width(), 1), QColor(theme.LINE))
        p.end()


class FadeMenu(QFrame):
    """``FadeMenu(parent, [("Open folder", fn), None (a line), ("Remove", fn, True (danger))])``; ``popup(global
    point)``."""

    def __init__(self, parent: QWidget, items: Sequence):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("FadeMenu")
        self.setStyleSheet(f"QFrame#FadeMenu {{ background: #151515; border: 1px solid {theme.LINE2}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(0)
        for it in items:
            if it is None:
                lay.addWidget(_Line())
                continue
            text, fn = it[0], it[1]
            danger = bool(it[2]) if len(it) > 2 else False
            enabled = bool(it[3]) if len(it) > 3 else True
            lay.addWidget(_Item(text, fn, danger, self, enabled))
        self.setMinimumWidth(190)

    def popup(self, at: QPoint) -> None:
        self.adjustSize()
        scr = self.screen().availableGeometry() if self.screen() else None
        x, y = at.x(), at.y()
        if scr is not None:
            x = min(x, scr.right() - self.width())
            y = min(y, scr.bottom() - self.height())
        self.move(x, y)
        self.show()
