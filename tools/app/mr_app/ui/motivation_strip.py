"""The motivation strip under the quote on the Match cuts tab (night 11):

* Today: a ring split into the daily goal's pieces (6 by default); every Short uploaded today fills one piece in its
  channel's colour; "4 / 6", "2 more to hit today's goal", "2 Lou · 1 Mike · 1 Flick707". The goal hit: a green
  border, one green sweep (and the window's toast).
* This month: "87 / 150 Shorts", a small square per day (a stronger red = more Shorts, today outlined in coral,
  future days dashed; the mouse over one: "Oct 12 · 5 Shorts") and "▲ 21 ahead of September" (amber when behind).

The counts come from the board's Uploaded dates (core.motivation)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolTip, QWidget

from . import motion as M
from . import theme
from . import widgets as W


class _Box(QWidget):
    """A panel whose border fades to --line2 under the mouse; a green border + one sweep when ``goal``."""

    def __init__(self):
        super().__init__()
        self.hover = M.Fader(self, self.update, M.FADE_IN_MS, 450)
        self.goal = M.Fader(self, self.update, 600, 600)
        self.wipe = M.Once(self, self.update, 1200)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def set_goal(self, hit: bool, sweep: bool = False) -> None:
        self.goal.to(1.0 if hit else 0.0)
        if hit and sweep:
            self.wipe.play()

    def paint_frame(self, p: QPainter) -> None:
        r = QRectF(self.rect())
        line = M.mix(M.mix(theme.LINE, theme.LINE2, self.hover.value), QColor(63, 185, 80, 140), self.goal.value)
        W.box(p, r, theme.PANEL, line)
        if self.wipe.active:
            x = -r.width() + 2 * r.width() * self.wipe.value
            from PySide6.QtGui import QBrush, QLinearGradient
            g = QLinearGradient(QPointF(x, 0), QPointF(x + r.width(), r.height() * 0.2))
            g.setColorAt(0.3, QColor(63, 185, 80, 0))
            g.setColorAt(0.5, QColor(63, 185, 80, 36))
            g.setColorAt(0.7, QColor(63, 185, 80, 0))
            p.fillRect(r.adjusted(1, 1, -1, -1), QBrush(g))


def _k(p: QPainter, x: float, y: float, text: str) -> None:
    p.setFont(W.mono(10.5, 600, 14))
    p.setPen(QColor(theme.DIM))
    p.drawText(QPointF(x, y), text.upper())


def _big(p: QPainter, x: float, base: float, num: str, rest: str) -> None:
    f = W.display(40, 2)
    p.setFont(f)
    p.setPen(QColor(theme.TEXT))
    p.drawText(QPointF(x, base), num)
    w = QFontMetrics(f).horizontalAdvance(num)
    p.setFont(W.font(13))
    p.setPen(QColor(theme.MUTED))
    p.drawText(QPointF(x + w + 6, base), rest)


class TodayBox(_Box):
    def __init__(self):
        super().__init__()
        self.count, self.goal_n, self.segments = 0, 6, []
        self.text, self.split = "", []
        self.grow = M.Once(self, self.update, 700, M.GLIDE)
        self._new = -1
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_today(self, count: int, goal: int, segments: list[str], text: str, split: list[tuple[str, int]],
                  hit: bool, animate: bool = True) -> None:
        grew = len(segments) > len(self.segments) and animate
        was_hit = self.count >= self.goal_n
        self.count, self.goal_n = count, max(1, goal)
        self.segments, self.text, self.split = list(segments)[: self.goal_n], text, split
        if grew:
            self._new = len(self.segments) - 1
            self.grow.play()
        self.set_goal(hit, sweep=hit and not was_hit and animate)
        self.update()

    def sizeHint(self) -> QSize:                          # noqa: N802
        return QSize(300, 128)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_frame(p)
        ring = QRectF(16, (self.height() - 96) / 2, 96, 96)
        n = self.goal_n
        gap_deg = 360.0 * 6.0 / (2 * math.pi * 50.0)     # the reference's 6 units of gap on r = 50
        seg = 360.0 / n - gap_deg
        r = ring.adjusted(4.5 * 96 / 120, 4.5 * 96 / 120, -4.5 * 96 / 120, -4.5 * 96 / 120)
        width = 9.0 * 96 / 120
        for i in range(n):
            ch = self.segments[i] if i < len(self.segments) else None
            start = 90.0 - i * (seg + gap_deg)            # clockwise from the top
            span = -seg
            if ch is not None and i == self._new and self.grow.active:
                span = -seg * self.grow.value
            col = QColor(theme.channel_colour(ch)) if ch else QColor("#222222")
            if ch:
                glow = QColor(col)
                glow.setAlphaF(0.22)
                p.setPen(QPen(glow, width + 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawArc(r, int(start * 16), int(span * 16))
            p.setPen(QPen(col, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(r, int(start * 16), int(span * 16))
        x = ring.right() + 16
        _k(p, x, 34, "Today")
        _big(p, x, 76, str(self.count), f"/ {self.goal_n}")
        p.setFont(W.font(11.5))
        p.setPen(QColor(theme.DIM))
        p.drawText(QPointF(x, 96), self.text)
        p.setFont(W.mono(11))
        sx = x
        for name, k in self.split:
            p.fillRect(QRectF(sx, 106, 7, 7), QColor(theme.channel_colour(name)))
            t = f"{k} {_short(name)}"
            p.setPen(QColor(theme.MUTED))
            p.drawText(QPointF(sx + 12, 114), t)
            sx += 12 + QFontMetrics(W.mono(11)).horizontalAdvance(t) + 12
        p.end()


def _short(name: str) -> str:
    return {"LouLaughs": "Lou", "MikeMemesYou": "Mike"}.get(name, name)


class MonthBox(_Box):
    def __init__(self):
        super().__init__()
        self.label, self.count, self.goal_n = "", 0, 150
        self.days: list = []                              # [(day, count, kind, tip, level)]
        self.pace, self.ahead = "", True
        self.setMouseTracking(True)
        self.setMinimumHeight(128)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_month(self, label: str, count: int, goal: int, days: list, pace: str, ahead: bool) -> None:
        self.label, self.count, self.goal_n, self.days, self.pace, self.ahead = label, count, goal, days, pace, ahead
        self.update()

    def _cells(self) -> list[QRectF]:
        n = max(28, len(self.days) or 31)
        left, right = 16.0, self.width() - 16.0
        gap = 3.0
        w = (right - left - gap * (n - 1)) / n
        w = min(w, 18.0)
        top = 74.0
        return [QRectF(left + i * (w + gap), top, w, w) for i in range(len(self.days))]

    def mouseMoveEvent(self, e) -> None:                  # noqa: N802
        pos = e.position()
        for cell, d in zip(self._cells(), self.days):
            if cell.adjusted(-1.5, -1.5, 1.5, 1.5).contains(pos):
                QToolTip.showText(e.globalPosition().toPoint(), d[3], self)
                return
        QToolTip.hideText()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        self.paint_frame(p)
        _k(p, 16, 30, self.label or "This month")
        _big(p, 16, 64, str(self.count), f"/ {self.goal_n} Shorts")
        p.setFont(W.mono(11))
        p.setPen(QColor(theme.GREEN if self.ahead else theme.AMBER))
        fm = QFontMetrics(W.mono(11))
        p.drawText(QPointF(self.width() - 16 - fm.horizontalAdvance(self.pace), 30), self.pace)
        for cell, (_day, n, kind, _tip, level) in zip(self._cells(), self.days):
            if kind == "future":
                pen = QPen(QColor("#262626"), 1, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(cell.adjusted(0.5, 0.5, -0.5, -0.5))
                continue
            col = QColor(229, 72, 77) if n else QColor("#1c1c1c")
            if n:
                col.setAlphaF(max(0.15, min(1.0, float(level))))
            p.fillRect(cell, col)
            if kind == "today":
                p.setPen(QPen(QColor(theme.CORAL), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(cell.adjusted(-1.5, -1.5, 1.5, 1.5))
        # the key: less ▫▫▫▫ more
        p.setFont(W.mono(10))
        y = self.height() - 14
        x = self.width() - 16
        t2 = "more"
        x -= QFontMetrics(W.mono(10)).horizontalAdvance(t2)
        p.setPen(QColor(theme.DIM))
        p.drawText(QPointF(x, y + 8), t2)
        for o in (1.0, 0.6, 0.35, 0.12):
            x -= 13
            c = QColor(229, 72, 77)
            c.setAlphaF(o)
            p.fillRect(QRectF(x, y, 9, 9), c)
        t1 = "less"
        x -= QFontMetrics(W.mono(10)).horizontalAdvance(t1) + 6
        p.drawText(QPointF(x, y + 8), t1)
        p.end()


class MotivationStrip(QWidget):
    """Today | This month, side by side (300 px + the rest). ``refresh(uploads, goals)`` with (time, channel)
    uploads; ``goal_hit`` fires once when today's goal is reached."""
    goal_hit = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.today = TodayBox()
        self.today.setFixedWidth(300)
        self.month = MonthBox()
        lay.addWidget(self.today)
        lay.addWidget(self.month, 1)
        self._hit_day = None
        self.hit = False

    def refresh(self, uploads, daily_goal: int = 6, monthly_goal: int = 150, now=None) -> None:
        from datetime import datetime
        now = now or datetime.now()
        try:
            from ..core import motivation as MV
            td = MV.today(uploads, now, daily_goal)
            mo = MV.month(uploads, now, monthly_goal)
        except Exception:  # noqa: BLE001 - nothing to show yet
            return
        split = list(getattr(td, "split_counts", None) or [])
        if not split:
            per = getattr(td, "per_channel", None) or {}
            split = [(k, v) for k, v in per.items() if v]
        live = getattr(self, "_seen_once", False)          # the first fill at the start: no sweep, no toast
        self.today.set_today(int(td.count), int(td.goal), list(td.segments), str(td.text), split, bool(td.hit),
                             animate=live)
        days = []
        for d in getattr(mo, "days", []) or []:
            days.append((int(getattr(d, "day", 0)), int(getattr(d, "count", 0)), str(getattr(d, "kind", "past")),
                         str(getattr(d, "tip", "")), float(getattr(d, "level", 0.0) or 0.0)))
        self.month.set_month(str(getattr(mo, "label", "")), int(getattr(mo, "count", 0)), int(getattr(mo, "goal", 150)),
                             days, str(getattr(mo, "pace", "")), bool(getattr(mo, "ahead", True)))
        was, self.hit = self.hit, bool(td.hit)
        if self.hit and not was and live and self._hit_day != now.date():
            self.goal_hit.emit(int(td.count))
        if self.hit:
            self._hit_day = now.date()
        self._seen_once = True
