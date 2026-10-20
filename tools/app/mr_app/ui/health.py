"""Day 13: this computer's health in the window.

* ``SpaceStrip`` -- the free space in the top strip: "Free C: 64.2 GB · D: 2.6 TB" (C: and the drives of this
  computer's projects and of "Save projects to" when they are other ones). A drive under 30 GB turns amber, under 10 GB
  red -- the colour FADES (0.5 s); its width never changes (nothing moves). Read in the background (core.space: a
  network folder that does not answer gives up after 8 s and shows "?"). A click opens the Board's Clean up.
* ``VersionLine`` -- ONE amber line under the top strip when this computer's programs are not what the project
  expects (core.versions): what is wrong in plain words and **Fix now** (setup_app.py in the background, its output in
  a log) -> "Fixing ..." -> "Fixed ✓" (green, then the line glides closed) or the plain reason with **Open log**. The
  line glides open and closed (its height, 0.45 s) while its content fades; it never blocks the window.
* ``offer_cleanup`` -- the question a page asks when it refuses to start for lack of room: the numbers, and
  **Clean up** (the Board's own Clean up: it looks first and asks before it removes anything)."""
from __future__ import annotations

import os
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMessageBox, QSizePolicy, QWidget

from ..core import space as S
from . import motion as M
from . import theme
from . import widgets as W

LEVEL_COLOURS = {"ok": theme.DIM, "low": theme.AMBER, "very low": theme.RED, "unknown": theme.DIM}


def short_name(name: str) -> str:
    """How the strip names a drive: 'C:', 'D:', a network share by its share name ('Shared')."""
    if name.startswith(("\\\\", "//")):
        bits = [b for b in name.replace("/", "\\").split("\\") if b]
        return bits[-1] if bits else name
    return name


class StripLabel(QLabel):
    """A top-strip item (GPU, Premiere, Save to): mono text that ends in "…" when the window is narrow -- it never
    pushes the strip wider than the window (day 13: room for the free space). Its colour comes from its style sheet,
    as before."""

    def minimumSizeHint(self) -> QSize:                   # noqa: N802
        fm = QFontMetrics(self.font())
        return QSize(min(fm.horizontalAdvance(self.text()) + 2, fm.horizontalAdvance("M") * 10),
                     fm.height() + 2)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        fm = QFontMetrics(self.font())
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()))
        p.end()


def strip_label(text: str = "", colour: str = theme.DIM, size: float = 11) -> StripLabel:
    lb = StripLabel(text)
    lb.setFont(W.mono(size))
    lb.setStyleSheet(f"color: {colour};")
    lb.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return lb


class SpaceStrip(QWidget):
    """The top strip's free space (module docstring). ``set_folders([(name, folder)])`` when the save folder
    changes; ``set_drives([DriveFree])`` with what the background read found."""
    clicked = Signal()

    def __init__(self, folders: Sequence[tuple[str, str]] = ()):
        super().__init__()
        self.hover = M.Fader(self, self.update, M.FADE_IN_MS, 400)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.drives: list[S.DriveFree] = []
        self.folders: list[tuple[str, str]] = []
        self._fade: dict[str, tuple[QColor, QColor, M.Fader]] = {}
        self.set_folders(folders)

    # -- what it shows --
    def set_folders(self, folders: Sequence[tuple[str, str]]) -> None:
        self.folders = list(folders)
        old = {d.name: d for d in self.drives}
        self.drives = [old.get(n) or S.DriveFree(n, None) for n, _f in self.folders]
        # room for the longest numbers: the numbers never move it; only a narrow window drops the word "Free"
        self.setMinimumSize(self.minimumSizeHint())
        self.setMaximumSize(self.sizeHint())
        self.updateGeometry()
        self._tip()
        self.update()

    def set_drives(self, drives: Sequence[S.DriveFree]) -> None:
        got = {d.name: d for d in drives}
        self.drives = [got.get(d.name, d) for d in self.drives]
        for d in self.drives:
            self._colour_to(d.name, LEVEL_COLOURS[d.level])
        self._tip()
        self.update()

    def level(self) -> str:
        """The worst drive's level ('ok', 'low', 'very low', 'unknown')."""
        order = ("very low", "low", "ok", "unknown")
        levels = [d.level for d in self.drives]
        return next((lv for lv in order if lv in levels), "unknown")

    def text(self) -> str:
        return "Free " + " · ".join(f"{short_name(d.name)} {S.gb(d.free) if d.free is not None else '?'}"
                                    for d in self.drives)

    def _colour_to(self, name: str, colour: str) -> None:
        cur = self._fade.get(name)
        new = QColor(colour)
        if cur is None:
            self._fade[name] = (new, new, M.Fader(self, self.update, 500, 500))
            self._fade[name][2].set(1.0)
            return
        a, b, f = cur
        if b == new:
            return
        now = M.mix(a, b, f.value)
        self._fade[name] = (now, new, f)
        f.set(0.0)
        f.to(1.0)

    def colour(self, name: str) -> QColor:
        cur = self._fade.get(name)
        if cur is None:
            return QColor(theme.DIM)
        a, b, f = cur
        return M.mix(a, b, f.value)

    def _tip(self) -> None:
        where = dict(self.folders)
        lines = []
        for d in self.drives:
            f = where.get(d.name, "")
            role = "Windows, this project and the tool's work files (work\\cache)" if d.name.upper() == \
                (os.environ.get("SystemDrive", "C:") or "C:").upper() else f"{f}"
            if d.free is None:
                lines.append(f"{d.name}: can't be read now ({role})")
            else:
                tot = f" of {S.gb(d.total)}" if d.total else ""
                warn = {"low": " -- under 30 GB: getting full", "very low": " -- under 10 GB: almost full"}.get(
                    d.level, "")
                lines.append(f"{d.name}: {S.gb(d.free)} free{tot}{warn} ({role})")
        lines.append("Click: Clean up on the Board (it looks first and asks before it removes anything)")
        self.setToolTip("\n".join(lines))

    # -- size, mouse, painting --
    def _font(self):
        return W.mono(11)

    def _numbers_w(self) -> int:
        fm = QFontMetrics(self._font())
        w = sum(fm.horizontalAdvance(f"{short_name(d.name)} 999.9 GB") for d in self.drives)
        return w + fm.horizontalAdvance(" · ") * max(0, len(self.drives) - 1) + 4

    def sizeHint(self) -> QSize:                          # noqa: N802 - room for the longest numbers
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance("Free ") + self._numbers_w(), fm.height() + 2)

    def minimumSizeHint(self) -> QSize:                   # noqa: N802 - a narrow window: without "Free"
        return QSize(self._numbers_w(), QFontMetrics(self._font()).height() + 2)

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
        f = self._font()
        p.setFont(f)
        fm = QFontMetrics(f)
        h = self.hover.value
        x = 0.0
        r = QRectF(self.rect())

        def draw(text: str, colour: QColor) -> None:
            nonlocal x
            p.setPen(colour)
            p.drawText(QRectF(x, 0, fm.horizontalAdvance(text) + 2, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += fm.horizontalAdvance(text)
        if self.width() >= self.sizeHint().width():
            draw("Free ", M.mix(theme.DIM, theme.CORAL, h))
        for i, d in enumerate(self.drives):
            if i:
                draw(" · ", QColor(theme.DIM))
            c = self.colour(d.name)
            if d.level in ("ok", "unknown"):
                c = M.mix(c, theme.CORAL, h)                # the link's hover: dim -> coral (a fade, nothing moves)
            draw(f"{short_name(d.name)} {S.gb(d.free) if d.free is not None else '?'}", c)
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# the version line
# ---------------------------------------------------------------------------------------------------------------------

HIDDEN, PROBLEM, FIXING, FIXED, FAILED = "hidden", "problem", "fixing", "fixed", "failed"
LINE_COLOURS = {PROBLEM: (theme.AMBER, theme.AMBER_SOFT, theme.AMBER_LINE),
                FIXING: (theme.BLUE, theme.BLUE_SOFT, theme.BLUE_LINE),
                FIXED: (theme.GREEN, theme.GREEN_SOFT, theme.GREEN_LINE),
                FAILED: (theme.AMBER, theme.AMBER_SOFT, theme.AMBER_LINE)}
OPEN_H = 34


class VersionLine(QWidget):
    """ONE line under the top strip (module docstring): ``show_problems``, ``show_fixing``, ``show_fixed``,
    ``show_failed``, ``close_line``. Signals: ``fix`` (Fix now), ``open_log``."""
    fix = Signal()
    open_log = Signal()

    def __init__(self):
        super().__init__()
        self.state = HIDDEN
        self.problems: list = []
        self._from = self._to = LINE_COLOURS[PROBLEM]
        self.tint = M.Fader(self, self.update, 500, 500)
        self.tint.set(1.0)
        self.op = M.Fader(self, self._faded, M.FADE_IN_MS, 450)
        self._h = QVariantAnimation(self)
        self._h.setEasingCurve(M.GLIDE)
        self._h.valueChanged.connect(lambda v: self.setFixedHeight(int(v)))
        self.setFixedHeight(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)
        self.mark = W.Square(theme.AMBER)
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel("")
        self.label.setFont(W.font(12, 600))
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.b_fix = W.Button("Fix now", "Small")
        self.b_fix.clicked.connect(self.fix.emit)
        lay.addWidget(self.b_fix, 0, Qt.AlignmentFlag.AlignVCenter)
        from .chrome import LinkButton
        self.b_log = LinkButton("open log")
        self.b_log.clicked.connect(self.open_log.emit)
        lay.addWidget(self.b_log, 0, Qt.AlignmentFlag.AlignVCenter)
        self._full = ""
        self._show_children(False)

    # -- states --
    def show_problems(self, problems: Sequence, can_fix: bool | None = None) -> None:
        from ..core import versions as V
        self.problems = list(problems)
        if not self.problems:
            self.close_line()
            return
        fixable = [p for p in self.problems if getattr(p, "fix", "") in (V.PIP, V.ARIA2C, V.DENO)
                   and not getattr(p, "closed", False)]
        can = bool(fixable) if can_fix is None else can_fix
        hand = [p for p in self.problems if p not in fixable]
        text = "This computer differs from what the project expects: " + V.summary(self.problems)
        if not can and hand:
            first = hand[0]
            how = first.hand or ("close MovieRecaps, then run: .venv\\Scripts\\python tools\\app\\setup_app.py"
                                 if getattr(first, "closed", False) else "")
            text += f" -- {how}" if how else ""
        self._set(PROBLEM, text, fix=can, log=False)

    def show_fixing(self, text: str = "") -> None:
        self._set(FIXING, "Fixing ..." + (f" {text}" if text else ""), fix=False, log=True)

    def show_fixed(self, text: str = "Fixed ✓ -- this computer now has what the project expects") -> None:
        self._set(FIXED, text, fix=False, log=True)

    def show_failed(self, text: str) -> None:
        self._set(FAILED, text, fix=True, log=True)

    def close_line(self) -> None:
        if self.state == HIDDEN:
            return
        self.state = HIDDEN
        self.op.to(0.0)
        self._glide(0)

    def is_open(self) -> bool:
        return self.state != HIDDEN

    def text(self) -> str:
        return self._full

    # -- inside --
    def _set(self, state: str, text: str, fix: bool, log: bool) -> None:
        was = self.state
        self.state = state
        self._full = text
        self.label.setText(text)
        self.label.setToolTip("\n".join(f"- {getattr(p, 'text', p)}" for p in self.problems) or text)
        self.b_fix.setText("Try again" if state == FAILED else "Fix now")
        self.b_fix.setVisible(fix)
        self.b_log.setVisible(log)
        new = LINE_COLOURS[state]
        if new != self._to:
            self._from = tuple(self._now())
            self._to = new
            self.tint.set(0.0)
            self.tint.to(1.0)
        self.mark.set_colour(new[0])
        self.label.setStyleSheet(f"color: {theme.TEXT};")
        self._show_children(True)
        if was == HIDDEN:
            self.op.set(0.0)
            self.op.to(1.0)
            self._glide(OPEN_H)
        self.update()

    def _now(self) -> list[QColor]:
        return [M.mix(a, b, self.tint.value) for a, b in zip(self._from, self._to)]

    def _glide(self, h: int) -> None:
        self._h.stop()
        if not M.enabled():
            self.setFixedHeight(h)
            self._faded()
            return
        self._h.setStartValue(float(self.height()))
        self._h.setEndValue(float(h))
        self._h.setDuration(M.GLIDE_MS)
        self._h.start()

    def _faded(self) -> None:
        """The whole line's opacity while it fades in or out (an opacity effect only then: none when it is fully
        shown)."""
        v = max(0.0, min(1.0, self.op.value))
        eff = self.graphicsEffect()
        if v >= 1.0:
            if eff is not None:
                self.setGraphicsEffect(None)
        else:
            if not isinstance(eff, QGraphicsOpacityEffect):
                eff = QGraphicsOpacityEffect(self)
                self.setGraphicsEffect(eff)
            eff.setOpacity(v)
        if self.state == HIDDEN and v <= 0.0:
            self._show_children(False)
        self.update()

    def _show_children(self, on: bool) -> None:
        for w in (self.mark, self.label):
            w.setVisible(on)
        if not on:
            self.b_fix.setVisible(False)
            self.b_log.setVisible(False)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        if self.height() <= 0:
            return
        p = QPainter(self)
        _fg, bg, line = self._now()
        r = QRectF(self.rect())
        p.fillRect(r, bg)
        p.fillRect(QRectF(0, r.height() - 1, r.width(), 1), line)
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# "not enough free space -- Clean up?"
# ---------------------------------------------------------------------------------------------------------------------

def ask_cleanup(parent: QWidget | None, text: str, title: str = "Not enough free space") -> bool:
    """The question (a test replaces it): True = Clean up."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText("Not enough free space -- nothing was started.")
    box.setInformativeText(text + "\n\nClean up looks for finished leftovers (temp files, old copies) and asks "
                                  "before it removes anything. Your projects and Premiere files are never touched.")
    clean = box.addButton("Clean up", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is clean


def offer_cleanup(ctx, parent: QWidget | None, text: str) -> None:
    """A page refused to start for lack of room: the window asks (``ctx.offer_cleanup``) -- or, without a window
    (tests), nothing."""
    fn: Callable | None = getattr(ctx, "offer_cleanup", None)
    if fn is not None:
        try:
            fn(text)
        except Exception:  # noqa: BLE001 - a question is never a crash
            pass


def space_changed(ctx) -> None:
    """A run, a download or a conversion ended: the top strip reads the free space again (soon, in the
    background)."""
    fn: Callable | None = getattr(ctx, "space_changed", None)
    if fn is not None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
