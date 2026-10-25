"""The app's building blocks in the look of docs\\ui_preview.html. They paint themselves, so a hover can FADE (the
user's rule: nothing moves, grows or jumps): buttons with a red fill sweeping in from the left, cards whose border
and background fade, viewfinder corners that fade in, a faint warm light crossing a box, tinted status pills with a
pulsing dot, progress bars that glide every frame with a soft shimmer, the drop zone (clean: no glow, no sweep),
section titles in Bebas Neue, the "match cut" logo, the toast. Timing and curves: motion.py."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import motion as M
from . import theme


def font(size: float, weight: int = 400, spacing: float = 0.0, family: str | None = None) -> QFont:
    """A font in pixels; ``spacing`` is extra letter spacing in percent (6 = +6 %)."""
    f = QFont(family or theme.FONT)
    if theme.MONO_LOOK:                                    # run 18: a monospace font has no emoji -- Windows' own
        f.setFamilies([family or theme.FONT, "Segoe UI Emoji", "Segoe UI Symbol", "Segoe UI"])   # come after it
    f.setPixelSize(max(1, int(round(size))))
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0 + spacing)
    return f


def mono(size: float = 11, weight: int = 400, spacing: float = 0.0) -> QFont:
    return font(size, weight, spacing, family=theme.MONO)


def display(size: float = 26, spacing: float = 6) -> QFont:
    return font(size, 400, spacing, family=theme.DISPLAY)


# ---------------------------------------------------------------------------------------------------------------------
# painting helpers
# ---------------------------------------------------------------------------------------------------------------------

def sweep(p: QPainter, r: QRectF, s: float, colour: QColor | None = None, peak: float = 0.035) -> None:
    """The faint warm light crossing a box: a band (transparent 35 % -> rgba(255,120,110, peak) 50 % -> transparent
    65 % of the box's width) moved from -110 % (s = 0) to +110 % (s = 1) of the width."""
    if s <= 0.0 or s >= 1.0:
        return
    w = r.width()
    tx = (-1.1 + 2.2 * s) * w
    g = QLinearGradient(QPointF(r.left() + tx, r.top()), QPointF(r.right() + tx, r.top() + r.height() * 0.18))
    c = QColor(colour or QColor(255, 120, 110))
    c0 = QColor(c)
    c0.setAlphaF(0.0)
    c.setAlphaF(peak)
    g.setColorAt(0.35, c0)
    g.setColorAt(0.50, c)
    g.setColorAt(0.65, c0)
    p.fillRect(r, QBrush(g))


def soft_shadow(p: QPainter, r: QRectF, colour: str | QColor, strength: float, blur: float = 26.0,
                dy: float = 8.0) -> None:
    """A box-shadow (0 dy blur colour) painted as rings of falling alpha around ``r`` -- behind the box."""
    if strength <= 0.0:
        return
    base = QColor(colour)
    steps = 9
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(steps, 0, -1):
        grow = blur * i / steps
        a = strength * (1.0 - i / (steps + 1)) ** 2 / steps * 2.2
        c = QColor(base)
        c.setAlphaF(max(0.0, min(1.0, a)))
        p.setBrush(c)
        p.drawRect(r.adjusted(-grow * 0.6, -grow * 0.6 + dy * 0.6, grow * 0.6, grow * 0.6 + dy))
    p.restore()


def corners(p: QPainter, r: QRectF, a: float, colour: str = theme.RED, size: float = 12.0, width: float = 2.0) -> None:
    """The viewfinder brackets (top-left and bottom-right, as the reference's .vf) at opacity ``a``."""
    if a <= 0.0:
        return
    pen = QPen(M.alpha(colour, a), width)
    pen.setCapStyle(Qt.PenCapStyle.SquareCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.save()
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    h = width / 2.0
    x0, y0, x1, y1 = r.left() - 1 + h, r.top() - 1 + h, r.right() + 1 - h, r.bottom() + 1 - h
    p.drawPolyline(QPolygonF([QPointF(x0, y0 + size - h), QPointF(x0, y0), QPointF(x0 + size - h, y0)]))
    p.drawPolyline(QPolygonF([QPointF(x1, y1 - size + h), QPointF(x1, y1), QPointF(x1 - size + h, y1)]))
    p.restore()


def box(p: QPainter, r: QRectF, fill: QColor | str | None, border: QColor | str | None, width: float = 1.0) -> None:
    """A flat box with a hairline border inside ``r`` (square corners)."""
    p.save()
    if fill is not None:
        p.fillRect(r, QColor(fill))
    if border is not None:
        pen = QPen(QColor(border), width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(width / 2, width / 2, -width / 2, -width / 2))
    p.restore()


# ---------------------------------------------------------------------------------------------------------------------
# the logo: a play triangle split diagonally, the left half white, the right half red and a little offset, inside red
# viewfinder corners (viewBox 28 x 28, as in the reference's SVG)
# ---------------------------------------------------------------------------------------------------------------------

LOGO_CORNERS = (((2, 8), (2, 2), (8, 2)), ((20, 2), (26, 2), (26, 8)), ((26, 20), (26, 26), (20, 26)),
                ((8, 26), (2, 26), (2, 20)))
_TRI = ((9, 7), (21, 14), (9, 21))
_CLIP_L = ((0, 0), (15.5, 0), (11.5, 28), (0, 28))
_CLIP_R = ((17, 0), (28, 0), (28, 28), (13, 28))
RIGHT_SHIFT = 1.2


def _poly(points) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    return path


def logo_halves() -> tuple[QPainterPath, QPainterPath]:
    """(the left half, the right half -- already shifted) of the triangle, in logo units."""
    tri = _poly(_TRI)
    return tri.intersected(_poly(_CLIP_L)), tri.intersected(_poly(_CLIP_R)).translated(RIGHT_SHIFT, 0)


def _partial(points, share: float) -> list[QPointF]:
    """The first ``share`` (0..1) of a polyline, as points."""
    segs = list(zip(points, points[1:]))
    total = sum(math.dist(a, b) for a, b in segs)
    left = total * max(0.0, min(1.0, share))
    out = [QPointF(*points[0])]
    for a, b in segs:
        d = math.dist(a, b)
        if left >= d:
            out.append(QPointF(*b))
            left -= d
            continue
        if left > 0:
            t = left / d
            out.append(QPointF(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        break
    return out


def draw_logo(p: QPainter, r: QRectF, corners_share: float = 1.0, left: float = 1.0, right: float = 1.0,
              slide: float = 0.0, stroke: float = 2.0, fg: str = theme.TEXT) -> None:
    """The logo in ``r`` (square). The intro animates it: ``corners_share`` draws the brackets, ``left`` / ``right``
    are the halves' opacity, ``slide`` (0..1 of 14 units) how far apart they still are."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = min(r.width(), r.height()) / 28.0
    p.translate(r.left() + (r.width() - 28 * s) / 2, r.top() + (r.height() - 28 * s) / 2)
    p.scale(s, s)
    if corners_share > 0:
        pen = QPen(QColor(theme.RED), stroke)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pts in LOGO_CORNERS:
            part = _partial(pts, corners_share)
            if len(part) >= 2:
                p.drawPolyline(QPolygonF(part))
    p.setPen(Qt.PenStyle.NoPen)
    lh, rh = logo_halves()
    if left > 0:
        p.save()
        p.translate(-14.0 * slide, 0)
        p.setOpacity(p.opacity() * left)
        p.fillPath(lh, QColor(fg))
        p.restore()
    if right > 0:
        p.save()
        p.translate(14.0 * slide, 0)
        p.setOpacity(p.opacity() * right)
        p.fillPath(rh, QColor(theme.RED))
        p.restore()
    p.restore()


def logo_pixmap(size: int, background: str | None = None, pad: float = 0.0) -> QPixmap:
    """The logo as a picture (the window and taskbar icon: on the dark background)."""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0) if background is None else QColor(background))
    p = QPainter(pm)
    draw_logo(p, QRectF(pad, pad, size - 2 * pad, size - 2 * pad))
    p.end()
    return pm


class Logo(QWidget):
    def __init__(self, size: int = 28):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:                     # noqa: N802 - Qt's name
        p = QPainter(self)
        draw_logo(p, QRectF(self.rect()))
        p.end()


def brand(size: int = 15) -> QWidget:
    """The logo and the name: Movie (white) Recaps (red)."""
    w = QWidget()
    w.setObjectName("Transparent")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    if theme.MONO_LOOK:                                    # run 18: MOVIE + red RECAPS, no logo
        name = QLabel(f"MOVIE<span style='color:{theme.RED}'>RECAPS</span>")
        name.setFont(mono(size, 800, 10))
        lay.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        return w
    lay.addWidget(Logo(28), 0, Qt.AlignmentFlag.AlignVCenter)
    name = QLabel(f"Movie<span style='color:{theme.RED}'>Recaps</span>")
    name.setFont(font(size + 1, 700, 1))
    lay.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


# ---------------------------------------------------------------------------------------------------------------------
# the background: a thin grid fading out downwards, a very soft vignette
# ---------------------------------------------------------------------------------------------------------------------

class GridBackground(QWidget):
    """The window behind everything: --bg, 1 px lines every 48 px (#1b1b1b) fading out by 85 % of the height (the
    reference's .win), and a very soft vignette at the edges (painted behind the pages, so it never catches the
    mouse or darkens a card)."""

    def __init__(self):
        super().__init__()
        self._cache: QPixmap | None = None

    def resizeEvent(self, e) -> None:                     # noqa: N802
        self._cache = None
        super().resizeEvent(e)

    def _paint(self) -> QPixmap:
        w, h = max(1, self.width()), max(1, self.height())
        pm = QPixmap(w, h)
        pm.fill(QColor(theme.BG))
        if theme.MONO_LOOK:                                # run 18: plain near-black, no grid, no vignette
            return pm
        p = QPainter(pm)
        step = theme.GRID_STEP
        p.setPen(QPen(QColor(theme.GRID_COLOUR), 1))
        x = step - 1
        while x < w:
            p.drawLine(QPointF(x + 0.5, 0), QPointF(x + 0.5, h))
            x += step
        y = step - 1
        while y < h:
            p.drawLine(QPointF(0, y + 0.5), QPointF(w, y + 0.5))
            y += step
        fade = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        fade.setColorAt(0.0, M.alpha(theme.BG, 0.0))
        fade.setColorAt(0.85, M.alpha(theme.BG, 1.0))
        fade.setColorAt(1.0, M.alpha(theme.BG, 1.0))
        p.fillRect(QRectF(0, 0, w, h), QBrush(fade))
        edge = 140.0
        for (x0, y0, x1, y1) in ((0, 0, edge, 0), (w, 0, w - edge, 0), (0, 0, 0, edge), (0, h, 0, h - edge)):
            g = QLinearGradient(QPointF(x0, y0), QPointF(x1, y1))
            g.setColorAt(0.0, QColor(0, 0, 0, 70))
            g.setColorAt(0.45, QColor(0, 0, 0, 22))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(0, 0, w, h), QBrush(g))
        p.end()
        return pm

    def paintEvent(self, e) -> None:                      # noqa: N802
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._paint()
        p = QPainter(self)
        p.drawPixmap(e.rect(), self._cache, e.rect())
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------------------------------------------------

def label(text: str = "", name: str | None = None, wrap: bool = False, select: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    if select:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def mono_label(text: str = "", colour: str = theme.DIM, size: float = 11) -> QLabel:
    lb = QLabel(text)
    lb.setFont(mono(size))
    lb.setStyleSheet(f"color: {colour};")
    return lb


def tag(text: str, red: bool = False) -> QLabel:
    """A small outlined uppercase tag (still)."""
    lb = QLabel(text.upper())
    lb.setObjectName("TagRed" if red else "Tag")
    lb.setFont(font(10, 600, 8))
    lb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lb


class Kbd(QLabel):
    """A key hint: Space, Del, Ctrl+V."""

    def __init__(self, key: str, on_primary: bool = False):
        super().__init__(key)
        self.setFont(mono(10, 500))
        c, b, bg = ("#ffffff", "rgba(255,255,255,90)", "transparent") if on_primary else \
            (theme.MUTED, theme.LINE2, "#111111")
        self.setStyleSheet(f"color: {c}; border: 1px solid {b}; background: {bg}; padding: 0px 5px;")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class Square(QWidget):
    """A small coloured square (a state, a channel)."""

    def __init__(self, colour: str = theme.MUTED, size: int = 7, round_: bool = False):
        super().__init__()
        self._c = QColor(colour)
        self._round = round_
        self.setFixedSize(size + 2, size + 2)

    def set_colour(self, colour: str) -> None:
        self._c = QColor(colour)
        self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.drawEllipse(r) if self._round else p.drawRect(r)
        p.end()


def channel_chip(name: str, size: int = 12, colour: str = theme.MUTED) -> QWidget:
    """'● LouLaughs' in the channel's colour dot."""
    w = QWidget()
    w.setObjectName("Transparent")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(Square(theme.channel_colour(name), 7, round_=True), 0, Qt.AlignmentFlag.AlignVCenter)
    t = QLabel(name or "no channel")
    t.setFont(font(size, 600))
    t.setStyleSheet(f"color: {colour};")
    lay.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


class SectionHeader(QWidget):
    """A section title in Bebas Neue with the red-to-coral square before it (the reference's h2), anything on the
    right."""

    def __init__(self, title: str, right: Sequence[QWidget] = ()):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 2)
        lay.setSpacing(10)
        lay.addWidget(_H2Mark(), 0, Qt.AlignmentFlag.AlignVCenter)
        self.title = QLabel(title.upper())
        self.title.setObjectName("Section")
        self.title.setFont(mono(13, 700, 14) if theme.MONO_LOOK else display(26, 6))   # run 18: "■ VIDEOS"
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        for w in right:
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)


class _H2Mark(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(7, 7)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        if theme.MONO_LOOK:                                # a plain small red square
            p.fillRect(self.rect(), QColor(theme.RED))
            p.end()
            return
        g = QLinearGradient(QPointF(0, 0), QPointF(7, 7))
        g.setColorAt(0, QColor(theme.RED))
        g.setColorAt(1, QColor(theme.CORAL))
        p.fillRect(self.rect(), QBrush(g))
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# buttons
# ---------------------------------------------------------------------------------------------------------------------

class Button(QPushButton):
    """A button that paints itself. Its kind is its object name (as before, so the tools keep their calls):

    * "" (normal): --panel2 with a red fill sweeping in from the left under the mouse, the border turning red;
    * "Primary": red -> coral, a lighter red sweeping in;
    * "Small": a smaller normal one;  "Ghost": text only, a faint fill fading in;
    * "Seg" / "Mini": the small outlined toggle (checked = on);  "Copy": the title's small Copy button;
    * "Danger": a normal one with red text.

    The faint warm light crosses it under the mouse (not on Ghost / Seg). ``kbd`` adds a key hint (Space)."""

    PAD = {"": (16, 8), "Primary": (16, 8), "Small": (10, 4), "Ghost": (8, 4), "Seg": (10, 4), "Mini": (10, 4),
           "Copy": (9, 3), "Danger": (12, 6)}

    def __init__(self, text: str = "", kind: str = "", kbd: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        if kind:
            self.setObjectName(kind)
        self._kbd = kbd
        self.hover = M.Fader(self, self.update)
        self.light = M.Fader(self, self.update, M.SWEEP_IN_MS, M.SWEEP_OUT_MS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.toggled.connect(lambda _on: self._aim())
        self._down = False

    def kind(self) -> str:
        return self.objectName() if self.objectName() in self.PAD else ""

    def _font(self) -> QFont:
        k = self.kind()
        if theme.MONO_LOOK:                                # run 18: uppercase mono, a little letter spacing
            return mono(10 if k in ("Seg", "Mini", "Copy", "Small", "Ghost") else 11, 600, 6)
        if k in ("Seg", "Mini", "Copy"):
            return font(11, 600, 4 if k == "Seg" else 0)
        if k in ("Small", "Ghost"):
            return font(12, 600)
        return font(13, 600)

    def _shown_text(self) -> str:
        return self.text().upper() if theme.MONO_LOOK else self.text()

    def sizeHint(self) -> QSize:                          # noqa: N802
        fm = QFontMetrics(self._font())
        px, py = self.PAD[self.kind()]
        w = fm.horizontalAdvance(self._shown_text()) + 2 * px + 2
        if self._kbd:
            w += QFontMetrics(mono(10, 500)).horizontalAdvance(self._kbd) + 10 + 6
        return QSize(w, fm.height() + 2 * py + 2)

    def minimumSizeHint(self) -> QSize:                   # noqa: N802
        return self.sizeHint()

    def _aim(self) -> None:
        on = self.underMouse() and self.isEnabled()
        self.hover.to(1.0 if (on or (self.isCheckable() and self.isChecked())) else 0.0)
        self.light.to(1.0 if on else 0.0)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self._aim()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        super().leaveEvent(e)
        QTimer.singleShot(0, self._aim)

    def changeEvent(self, e) -> None:                     # noqa: N802
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self._aim()

    def setChecked(self, on: bool) -> None:               # noqa: N802
        super().setChecked(on)
        self._aim()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        r = QRectF(self.rect())
        k, h, on = self.kind(), self.hover.value, self.isEnabled()
        down = self.isDown()
        if theme.MONO_LOOK:                                # run 18: square; the main action red filled, the rest
            text = self._paint_mono(p, r, k, h, on)        # outlined grey; no glow, no gradient, no sweep
        elif k == "Primary":
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, QColor(theme.RED) if on else QColor("#3a1d1e"))
            g.setColorAt(1, QColor(theme.CORAL) if on else QColor("#3a2420"))
            p.fillRect(r, QBrush(g))
            if on and h > 0:
                p.fillRect(QRectF(r.left(), r.top(), r.width() * h, r.height()), QColor(theme.RED_HI))
            box(p, r, None, theme.RED if on else "#3a1d1e")
            text = QColor("#ffffff") if on else QColor("#8a6466")
        elif k == "Ghost":
            if on and h > 0:
                p.fillRect(r, M.alpha(theme.PANEL2, h))
            text = M.mix(theme.MUTED, theme.TEXT, h) if on else QColor(theme.DIM)
        elif k in ("Seg", "Mini", "Copy"):
            if on:
                p.fillRect(r, M.alpha(theme.RED_SOFT, h))
                box(p, r, None, M.mix(theme.LINE2, theme.RED, h))
                text = M.mix(theme.MUTED, theme.TEXT, h)
            else:
                checked = self.isCheckable() and self.isChecked()
                box(p, r, "#262626" if checked else None, theme.LINE)
                text = QColor(theme.DIM)
        else:
            p.fillRect(r, QColor(theme.PANEL2))
            if on and h > 0:
                p.fillRect(QRectF(r.left(), r.top(), r.width() * h, r.height()), QColor(theme.RED_SOFT))
            box(p, r, None, M.mix(theme.LINE2, theme.RED, h) if on else theme.LINE)
            text = QColor(("#ff8a8d" if k == "Danger" else theme.TEXT) if on else theme.DIM)
        if on and k not in ("Ghost", "Seg", "Mini") and self.light.value > 0 and not theme.MONO_LOOK:
            sweep(p, r, self.light.value)           # in: it crosses to the right; out: it goes back
        if down and on:
            p.fillRect(r, QColor(255, 255, 255, 18))
        p.setFont(self._font())
        p.setPen(text)
        fm = QFontMetrics(self._font())
        tw = fm.horizontalAdvance(self._shown_text())
        kw = (QFontMetrics(mono(10, 500)).horizontalAdvance(self._kbd) + 10 + 6) if self._kbd else 0
        x = r.left() + (r.width() - tw - kw) / 2
        p.drawText(QRectF(x, r.top(), tw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._shown_text())
        if self._kbd:
            kf = mono(10, 500)
            p.setFont(kf)
            kfm = QFontMetrics(kf)
            kr = QRectF(x + tw + 6, r.center().y() - kfm.height() / 2 - 1, kw - 6, kfm.height() + 2)
            prim = k == "Primary"
            box(p, kr, None if prim else "#111111", QColor(255, 255, 255, 90) if prim else QColor(theme.LINE2))
            p.setPen(QColor("#ffffff") if prim else QColor(theme.MUTED))
            p.drawText(kr, Qt.AlignmentFlag.AlignCenter, self._kbd)
        p.end()


    def _paint_mono(self, p: QPainter, r: QRectF, k: str, h: float, on: bool) -> QColor:
        """The Mono + red button (its text's colour): Primary -- the one main action of an area -- red filled (a
        little lighter under the mouse); the others outlined grey, the outline and the text brightening under the
        mouse (red text for Danger); a toggle that is on: a white outline."""
        if k == "Primary":
            p.fillRect(r, M.mix(theme.RED, theme.RED_HI, h) if on else QColor("#2a1416"))
            return QColor("#ffffff") if on else QColor("#8a6466")
        if k == "Ghost":
            if on and h > 0:
                p.fillRect(r, M.alpha("#161616", h))
            return M.mix(theme.DIM, theme.TEXT, h) if on else QColor(theme.DIM)
        checked = self.isCheckable() and self.isChecked()
        if on and h > 0:
            p.fillRect(r, M.alpha("#141414", h))
        line = theme.TEXT if checked else (M.mix(theme.LINE2, "#7a7a7a", h) if on else QColor(theme.LINE))
        box(p, r, None, line)
        if k == "Danger":
            return QColor(theme.RED) if on else QColor(theme.DIM)
        if not on:
            return QColor(theme.DIM)
        return QColor(theme.TEXT) if checked else M.mix(theme.MUTED, theme.TEXT, h)


class Segmented(QWidget):
    """Two or three small toggles, one on ('FAST | THOROUGH')."""
    changed = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]], value: str):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._b: dict[str, Button] = {}
        for key, text in options:
            b = Button(text.upper(), "Seg")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set(k, emit=True))
            lay.addWidget(b)
            self._b[key] = b
        self.value = value
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


# ---------------------------------------------------------------------------------------------------------------------
# status: pills, progress bars
# ---------------------------------------------------------------------------------------------------------------------

class Pill(QWidget):
    """A tinted status pill: grey waiting, blue running (its dot pulses), green PASS, amber a note, red FAIL /
    PICTURE NOT FOUND / WRONG PAIR. A new state's colours fade in (0.5 s)."""

    def __init__(self, word: str = "waiting"):
        super().__init__()
        self._word = ""
        self._from = self._to = theme.pill_colours("waiting")
        self.fade = M.Fader(self, self.update, 500, 500)
        self.pulse = M.Loop(self, self.update, 1400, M.IN_OUT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set(word)

    def word(self) -> str:
        return self._word

    def text(self) -> str:
        """The word as shown (a lowercase state in capitals, as the older label did)."""
        return self._word.upper() if self._word.islower() else self._word

    def set(self, word: str, colours: tuple[str, str, str] | None = None) -> None:
        word = str(word or "")
        new = colours or theme.pill_colours(word)
        if word == self._word and new == self._to:
            return
        self._from = tuple(self._now())
        self._to = new
        self._word = word
        self.fade.set(0.0)
        self.fade.to(1.0)
        if word.lower() == "running":
            self.pulse.start()
        else:
            self.pulse.stop()
        self.updateGeometry()
        self.update()

    def _now(self) -> list[QColor]:
        t = self.fade.value
        return [M.mix(a, b, t) for a, b in zip(self._from, self._to)]

    def _font(self) -> QFont:
        return mono(10, 700, 8) if theme.MONO_LOOK else font(10, 600, 8)

    def sizeHint(self) -> QSize:                          # noqa: N802
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._word.upper()) + 14 + 12 + 2, fm.height() + 6)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        fg, bg, line = self._now()
        r = QRectF(self.rect())
        if not theme.MONO_LOOK:                            # run 18, Mono + red: the coloured word only
            box(p, r, bg, line)
        dot = QColor(fg)
        a = 0.9
        if self.pulse.running():
            a = 0.25 + 0.65 * (0.5 + 0.5 * math.cos(2 * math.pi * self.pulse.value))
        dot.setAlphaF(a)
        fm = QFontMetrics(self._font())
        cy = r.center().y()
        p.fillRect(QRectF(7, cy - 3, 6, 6), dot)
        p.setFont(self._font())
        p.setPen(fg)
        p.drawText(QRectF(19, r.top(), r.width() - 19, r.height()), Qt.AlignmentFlag.AlignVCenter, self._word.upper())
        p.end()


class StateLabel(QWidget):
    """A status pill and a muted line after it (kept for the tools: ``set(word, note, colour)``)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        self.pill = Pill("ready")
        self.word = self.pill                      # (older name)
        self.note = label("", "Muted", wrap=True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.note, 1, Qt.AlignmentFlag.AlignVCenter)

    def set(self, word: str, note: str = "", colour: str | None = None) -> None:
        cols = None
        if colour is not None and word not in theme.PILLS:
            cols = _tint(colour)
        elif colour is not None and colour != theme.pill_colours(word)[0]:
            cols = _tint(colour)
        self.pill.set(word, cols)
        self.note.setText(note)


def _tint(colour: str) -> tuple[str, str, str]:
    """A pill's (text, background, border) from one colour."""
    for v in theme.PILLS.values():
        if QColor(v[0]) == QColor(colour):
            return v
    c = QColor(colour)
    return (colour, M.mix(theme.BG, c, 0.14).name(), M.mix(theme.BG, c, 0.38).name())


class GlideBar(QWidget):
    """A progress bar that glides toward the real value every frame and never jumps -- the jump to 100 % at the end
    glides too -- with a soft shimmer while it runs. ``set(share)``, ``state(word)`` (running blue, PASS green, a note
    amber, a failure red, otherwise red)."""

    TAU_MS = 270.0               # the glide: 6 % of the way per 60 Hz frame (the reference)

    def __init__(self, height: int = 3):
        super().__init__()
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.target = 0.0
        self.shown = 0.0
        self._col_from = self._col_to = QColor(theme.RED)
        self.colour = M.Fader(self, self.update, 500, 500)
        self.shine = M.Loop(self, self.update, 1600, M.IN_OUT)
        self._tick = QTimer(self)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self._step)
        self._clock = QElapsedTimer()

    def set(self, share: float) -> None:
        self.target = max(0.0, min(1.0, float(share)))
        if not M.enabled():
            self.shown = self.target
            self.update()
            return
        if not self._tick.isActive():
            self._clock.start()
            self._tick.start()

    def jump(self, share: float) -> None:
        """No glide (a row shown again)."""
        self.target = self.shown = max(0.0, min(1.0, float(share)))
        self.update()

    def state(self, word: str) -> None:
        w = (word or "").lower()
        c = {"running": theme.BLUE, "pass": theme.GREEN, "ok with a note": theme.AMBER, "note": theme.AMBER,
             "waiting": theme.DIM}.get(w, theme.RED)
        new = QColor(c)
        if new != self._col_to:
            self._col_from = M.mix(self._col_from, self._col_to, self.colour.value)
            self._col_to = new
            self.colour.set(0.0)
            self.colour.to(1.0)
        if w == "running":
            self.shine.start()
        else:
            self.shine.stop()
        self.update()

    def _step(self) -> None:
        dt = max(1.0, float(self._clock.restart()))
        k = 1.0 - math.exp(-dt / self.TAU_MS)
        self.shown += (self.target - self.shown) * k
        if abs(self.target - self.shown) < 0.0008 or (self.target >= 1.0 and self.shown > 0.996):
            self.shown = self.target
            self._tick.stop()
        self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, QColor("#1f1f1f"))
        w = r.width() * self.shown
        if w > 0.5:
            fill = QRectF(r.left(), r.top(), w, r.height())
            p.fillRect(fill, M.mix(self._col_from, self._col_to, self.colour.value))
            if self.shine.running():
                x = fill.left() - fill.width() + 2 * fill.width() * self.shine.value
                g = QLinearGradient(QPointF(x, 0), QPointF(x + fill.width(), 0))
                g.setColorAt(0.0, QColor(255, 255, 255, 0))
                g.setColorAt(0.5, QColor(255, 255, 255, 56))
                g.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.save()
                p.setClipRect(fill)
                p.fillRect(fill, QBrush(g))
                p.restore()
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# boxes
# ---------------------------------------------------------------------------------------------------------------------

class Card(QFrame):
    """A flat panel (square corners). Under the mouse its border and background fade (0.3 s in, 0.48 s out) and the
    faint light crosses it; ``corners=True`` adds the viewfinder brackets fading in (the board's cards)."""

    def __init__(self, hover: bool = True, corners: bool = False, fill: str = theme.PANEL,
                 fill_hot: str = "#171717", line: str = theme.LINE, line_hot: str = theme.LINE2,
                 margins: tuple[int, int, int, int] = (20, 18, 20, 18), spacing: int = 10):
        super().__init__()
        self.setObjectName("CardSelf")
        self._hover_on, self._corners = hover, corners
        self._fill, self._fill_hot, self._line, self._line_hot = fill, fill_hot, line, line_hot
        self.hover = M.Fader(self, self.update)
        self.light = M.Fader(self, self.update, 1000, 1000)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)

    def enterEvent(self, e) -> None:                      # noqa: N802
        if self._hover_on:
            self.hover.to(1.0)
            self.light.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        if self._hover_on:
            self.hover.to(0.0)
            self.light.to(0.0)
        super().leaveEvent(e)

    def paint_box(self, p: QPainter, r: QRectF) -> None:
        h = self.hover.value
        if theme.MONO_LOOK:                                # run 18: thin lines, no box -- a row of a list (its
            if h > 0:                                      # line under it) or a plain thin frame; a faint fill
                p.fillRect(r, M.alpha("#111111", h))      # under the mouse
            if getattr(self, "list_row", False):
                p.fillRect(QRectF(r.left(), r.bottom() - 1, r.width(), 1), QColor(theme.LINE2 if h > 0 else
                                                                                   theme.LINE))
            else:
                box(p, r, None, M.mix(theme.LINE, theme.LINE2, h))
            if self._corners:
                corners(p, r, h)
            return
        box(p, r, M.mix(self._fill, self._fill_hot, h), M.mix(self._line, self._line_hot, h))
        if self._hover_on:
            sweep(p, r.adjusted(1, 1, -1, -1), self.light.value, peak=0.03)
        if self._corners:
            corners(p, r, h)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        self.paint_box(p, QRectF(self.rect()))
        p.end()


class DropZone(QFrame):
    """The drop area: a dashed border fading to red and a slightly warmer background under the mouse or a drag --
    clean: no glow, no sweep; the viewfinder corners fade in. ``dropped(list of Path)``."""
    dropped = Signal(list)

    def __init__(self, title: str, lines: Sequence[str], buttons: Sequence[tuple[str, Callable[[], None]]] = (),
                 big: bool = True):
        super().__init__()
        self.setObjectName("DropSelf")
        self.setAcceptDrops(True)
        self.hover = M.Fader(self, self.update)
        self._drag = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 26 if big else 18, 16, 26 if big else 18)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = label(title)
        t.setFont(font(18 if big else 15, 700))
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        self.title = t
        for ln in lines:
            m = label(ln, "Muted", wrap=True)
            m.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(m)
        if buttons:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            for text, fn in buttons:
                b = Button(text)
                b.clicked.connect(fn)
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)

    def _aim(self) -> None:
        self.hover.to(1.0 if (self._drag or self.underMouse()) else 0.0)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self._aim()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        super().leaveEvent(e)
        QTimer.singleShot(0, self._aim)

    def dragEnterEvent(self, e) -> None:                  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag = True
            self._aim()

    def dragLeaveEvent(self, e) -> None:                  # noqa: N802
        self._drag = False
        self._aim()

    def dropEvent(self, e) -> None:                       # noqa: N802
        self._drag = False
        self._aim()
        got = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if got:
            e.acceptProposedAction()
            self.dropped.emit(got)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        h = self.hover.value
        p.fillRect(r, M.mix(theme.PANEL, "#1a1314", h))
        pen = QPen(M.mix(theme.LINE2, theme.RED, h), 1, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        corners(p, r, h)
        p.end()


class TitleBox(QFrame):
    """The video's title with 'TITLE' in coral and a Copy button (the reference's .job .title): its border lights up
    with its row and under the mouse. ``label="YouTube"`` + ``editable`` make the second line: the title the Short is
    uploaded with, with an Edit button (``edit_clicked``)."""
    copied = Signal(str)
    edit_clicked = Signal()

    def __init__(self, text: str = "", label: str = "Title", editable: bool = False, empty: str = "no title yet"):
        super().__init__()
        self.setObjectName("TitleSelf")
        self.hover = M.Fader(self, self.update)
        self.row_hot = M.Fader(self, self.update)
        self._empty = empty
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(10)
        small = QLabel(label.upper())
        small.setFont(font(10, 600, 8))
        small.setStyleSheet(f"color: {M.alpha(theme.CORAL, 0.8).name(QColor.NameFormat.HexArgb)};")
        lay.addWidget(small, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setFont(font(13, 400))
        lay.addWidget(self.text, 1)
        self.edit = None
        if editable:
            self.edit = Button("Edit", "Copy")
            self.edit.clicked.connect(self.edit_clicked.emit)
            lay.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignVCenter)
        self.copy = Button("Copy", "Copy")
        self.copy.clicked.connect(self._copy)
        lay.addWidget(self.copy, 0, Qt.AlignmentFlag.AlignVCenter)
        self.set(text)

    def set(self, text: str) -> None:
        self._raw = text or ""
        shown = " / ".join(x.strip() for x in self._raw.splitlines() if x.strip())
        self.text.setText(shown or self._empty)
        self.text.setStyleSheet(f"color: {theme.TEXT if shown else theme.DIM};")
        self.copy.setEnabled(bool(shown))

    def value(self) -> str:
        return self._raw

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        t = " ".join(x.strip() for x in self._raw.splitlines() if x.strip())
        QApplication.clipboard().setText(t)
        self.copied.emit(t)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        h, rh = self.hover.value, self.row_hot.value
        line = M.mix(M.mix(theme.LINE, "#3a2526", rh), theme.WINE_LINE, h)
        box(p, QRectF(self.rect()), M.mix("#121212", "#171314", h), line)
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# the toast
# ---------------------------------------------------------------------------------------------------------------------

class Toast(QWidget):
    """A short message at the bottom middle of the window ('Title copied ✓', "That's a wrap -- 3 videos"): fades in
    and rises 16 px, stays, fades out. Colour: green, amber for a note, red for a failure. Never takes the mouse."""

    SHOW_MS = 2200

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text, self._edge = "", QColor(theme.GREEN)
        self.op = M.Fader(self, self._place, 350, 450)
        self.rise = M.Fader(self, self._place, 500, 450)
        self._queue: list[tuple[str, str]] = []
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self._out)
        self.hide()

    def show_text(self, text: str, kind: str = "ok") -> None:
        if self.isVisible() and self.op.target > 0:
            self._queue.append((text, kind))
            return
        self._text = text
        self._edge = QColor({"note": theme.AMBER, "fail": theme.RED, "info": theme.BLUE}.get(kind, theme.GREEN))
        self.show()
        self.raise_()
        self.op.set(0.0)
        self.rise.set(0.0)
        self.op.to(1.0)
        self.rise.to(1.0)
        self._hide.start(self.SHOW_MS)

    def _out(self) -> None:
        self.op.to(0.0)
        self.rise.to(0.0)
        QTimer.singleShot(480 if M.enabled() else 0, self._done)

    def _done(self) -> None:
        if self.op.target > 0:
            return
        self.hide()
        if self._queue:
            self.show_text(*self._queue.pop(0))

    def _font(self) -> QFont:
        return font(13, 600)

    def _place(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        fm = QFontMetrics(self._font())
        w, h = fm.horizontalAdvance(self._text) + 32 + 3, fm.height() + 20
        y = par.height() - 28 - h + int(round(16 * (1.0 - self.rise.value)))
        self.setGeometry((par.width() - w) // 2, y, w, h)
        self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setOpacity(self.op.value)
        r = QRectF(self.rect())
        box(p, r, theme.PANEL2, theme.LINE2)
        p.fillRect(QRectF(0, 0, 3, r.height()), self._edge)
        p.setFont(self._font())
        p.setPen(QColor(theme.TEXT))
        p.drawText(r.adjusted(3 + 16, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------

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
