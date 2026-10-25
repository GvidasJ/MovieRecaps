"""The Mono + red look's own pieces (run 18, task 8 -- you chose them from the mockups):

* ``PipelineDots``  -- a video's steps as dots and lines: ``● DOWNLOAD ─── ● CONVERT ─── ◉ EDIT ─── ○ DONE`` (● done
  green, ◉ running amber, ○ waiting dim, ✕ red where it failed)
* ``BlockBar``      -- a progress bar of blocks: ``■■■■■■■□□□ 68%  ~10 MIN`` (amber while running, green when done,
  red when it failed)
* ``CoverageStrip`` -- a finished or failed edit along the competitor's length: green where it was found in the RAW,
  red "not in RAW", amber unsure; 0:00 and the end time under it (``coverage_of(run_dir)`` reads the run's cut list)
* ``CommandBox``    -- Ctrl+K: type a few letters to jump to any page or action, each with its shortcut shown

Plain painting: no glow, no gradient, no animation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import theme
from . import widgets as W

DONE, RUN, WAIT, FAIL = "done", "running", "waiting", "failed"


def _state_colour(state: str) -> str:
    return {DONE: theme.GREEN, RUN: theme.AMBER, FAIL: theme.RED}.get(state, theme.DIM)


class PipelineDots(QWidget):
    """``set([(label, state), ...])`` -- the states DONE / RUN / WAIT / FAIL."""

    def __init__(self, steps: Sequence[tuple[str, str]] = ()):
        super().__init__()
        self.steps: list[tuple[str, str]] = list(steps)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)

    def set(self, steps: Sequence[tuple[str, str]]) -> None:
        steps = [(str(a), str(b)) for a, b in steps]
        if steps != self.steps:
            self.steps = steps
            self.update()

    def text(self) -> str:
        """The line as text (the tests read it): '● DOWNLOAD ─── ◉ EDIT ─── ○ DONE'."""
        mark = {DONE: "●", RUN: "◉", FAIL: "✕"}
        return " ─── ".join(f"{mark.get(s, '○')} {a.upper()}" for a, s in self.steps)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        f = W.mono(10, 600, 6)
        p.setFont(f)
        fm = QFontMetrics(f)
        x, cy = 0.0, self.height() / 2.0
        mark = {DONE: "●", RUN: "◉", FAIL: "✕"}
        for i, (label, state) in enumerate(self.steps):
            if i:
                p.fillRect(QRectF(x + 6, cy, 30, 1), QColor(theme.LINE2))
                x += 42
            col = QColor(_state_colour(state))
            p.setPen(col)
            m = mark.get(state, "○")
            p.drawText(QRectF(x, 0, 14, self.height()), Qt.AlignmentFlag.AlignVCenter, m)
            x += fm.horizontalAdvance(m) + 6
            p.setPen(QColor(theme.TEXT if state in (RUN, FAIL) else theme.DIM if state == WAIT else theme.MUTED))
            t = label.upper()
            p.drawText(QRectF(x, 0, fm.horizontalAdvance(t) + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, t)
            x += fm.horizontalAdvance(t)
        p.end()


class BlockBar(QWidget):
    """``set(share, state, eta='')`` -- ten blocks, the percent and the time left: ``■■■■■■■□□□ 68%  ~10 MIN``."""

    N = 10

    def __init__(self):
        super().__init__()
        self.share, self.state, self.eta = 0.0, WAIT, ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)

    def set(self, share: float, state: str = RUN, eta: str = "") -> None:
        share = max(0.0, min(1.0, float(share or 0.0)))
        if (share, state, eta) != (self.share, self.state, self.eta):
            self.share, self.state, self.eta = share, state, eta
            self.update()

    def text(self) -> str:
        full = int(round(self.share * self.N))
        out = "■" * full + "□" * (self.N - full) + f" {int(round(100 * self.share))}%"
        return out + (f"  ~{self.eta.upper()}" if self.eta else "")

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        col = QColor(_state_colour(self.state if self.state != WAIT else RUN))
        full = int(round(self.share * self.N))
        x, s = 0.0, 9.0
        top = (self.height() - s) / 2.0
        for i in range(self.N):
            r = QRectF(x, top, s, s)
            if i < full:
                p.fillRect(r, col)
            else:
                p.setPen(QColor(theme.LINE2))
                p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
            x += s + 3
        f = W.mono(10, 600, 4)
        p.setFont(f)
        p.setPen(col)
        t = f"{int(round(100 * self.share))}%"
        p.drawText(QRectF(x + 6, 0, 60, self.height()), Qt.AlignmentFlag.AlignVCenter, t)
        if self.eta:
            p.setPen(QColor(theme.DIM))
            p.drawText(QRectF(x + 6 + QFontMetrics(f).horizontalAdvance(t) + 14, 0, 200, self.height()),
                       Qt.AlignmentFlag.AlignVCenter, f"~{self.eta.upper()}")
        p.end()


_ETA = re.compile(r"about ([0-9]+(?:[.,][0-9]+)?\s*(?:min|h|s|sec|hours?|minutes?)[^,;)]*)", re.I)


def eta_of(progress: str) -> str:
    """'Placing every frame exactly -- 68 %, about 10 min left' -> '10 min'."""
    m = _ETA.search(progress or "")
    if not m:
        return ""
    return re.sub(r"\s*left\s*$", "", m.group(1).strip())


class CoverageStrip(QWidget):
    """``set(duration_s, [(t0, t1, kind)])`` -- kind 'raw' green, 'not' red (not in the RAW), 'unsure' amber; the
    times under it."""

    def __init__(self):
        super().__init__()
        self.duration, self.spans = 0.0, []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)
        self.setToolTip("Where the competitor was found in the RAW: green found, red not in the RAW, amber unsure")

    def set(self, duration: float, spans: Sequence[tuple[float, float, str]]) -> None:
        self.duration, self.spans = max(0.0, float(duration or 0.0)), list(spans)
        self.setVisible(self.duration > 0 and bool(self.spans))
        self.update()

    @staticmethod
    def _clock(s: float) -> str:
        s = int(round(s))
        return f"{s // 60}:{s % 60:02d}"

    def paintEvent(self, _e) -> None:                     # noqa: N802
        if self.duration <= 0:
            return
        p = QPainter(self)
        w = float(self.width())
        bar = QRectF(0, 2, w, 4)
        p.fillRect(bar, QColor(theme.LINE))
        cols = {"raw": theme.GREEN, "not": theme.RED, "unsure": theme.AMBER}
        for t0, t1, kind in self.spans:
            a, b = w * max(0.0, t0) / self.duration, w * min(self.duration, t1) / self.duration
            if b > a:
                p.fillRect(QRectF(a, 2, max(1.0, b - a), 4), QColor(cols.get(kind, theme.DIM)))
        p.setFont(W.mono(9, 500))
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(0, 8, 60, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "0:00")
        p.drawText(QRectF(w - 60, 8, 60, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._clock(self.duration))
        p.end()


def coverage_of(run_dir: str | Path | None) -> tuple[float, list[tuple[float, float, str]]]:
    """(the competitor's length, s; [(t0, t1, 'raw' | 'not' | 'unsure')]) from the run's extras\\cutlist.json --
    (0, []) when it can't be read. Read it off the window's thread."""
    try:
        d = json.loads((Path(run_dir) / "extras" / "cutlist.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 0.0, []
    try:
        comp = d.get("competitor") or {}
        fps = comp.get("fps") or "30"
        num, _, den = str(fps).partition("/")
        f = float(num) / float(den or 1)
        n = float(comp.get("frames") or 0)
        out = []
        for s in d.get("segments") or []:
            a, b = int(s.get("comp_in") or 0) / f, int(s.get("comp_out") or 0) / f
            typ = str(s.get("type") or "")
            kind = ("not" if typ in ("not_in_raw", "other", "other_video", "black") else
                    "unsure" if typ == "uncertain" or s.get("uncertain") else "raw")
            out.append((a, b, kind))
        return (n / f if n else max((b for _a, b, _k in out), default=0.0)), out
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0, []


class CommandBox(QDialog):
    """Ctrl+K: a box to type in and the pages / actions matching what you typed, each with its shortcut; Enter (or a
    click) runs the first / the chosen one, Esc closes. ``actions``: [(name, shortcut text, callable)]."""

    ran = Signal(str)

    def __init__(self, parent: QWidget, actions: Sequence[tuple[str, str, Callable[[], None]]]):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.actions = list(actions)
        self.setObjectName("CommandBox")
        self.setStyleSheet(f"QDialog#CommandBox {{ background: {theme.BG}; border: 1px solid {theme.LINE2}; }}"
                           f"QListWidget {{ background: {theme.BG}; border: none; color: {theme.TEXT}; }}"
                           f"QListWidget::item {{ padding: 6px 8px; }}"
                           f"QListWidget::item:selected {{ background: #161616; color: {theme.TEXT}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        head = QLabel("■ GO TO")
        head.setFont(W.mono(10, 700, 12))
        head.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(head)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("type a few letters: new, learn, board ...")
        self.edit.setFont(W.mono(13))
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._run_current)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setFont(W.mono(12))
        self.list.itemActivated.connect(lambda it: self._run(it.data(Qt.ItemDataRole.UserRole)))
        self.list.itemClicked.connect(lambda it: self._run(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.list)
        self.resize(560, 420)
        self._filter("")

    @staticmethod
    def matches(name: str, typed: str) -> bool:
        """'nv' matches 'New video' (the letters in order), 'learn' matches 'Learn'."""
        t = re.sub(r"\s+", "", typed.lower())
        if not t:
            return True
        it = iter(name.lower())
        return all(ch in it for ch in t)

    def _filter(self, typed: str) -> None:
        self.list.clear()
        for i, (name, keys, _fn) in enumerate(self.actions):
            if self.matches(name, typed):
                it = QListWidgetItem(f"{name.upper():<34}{keys}")
                it.setData(Qt.ItemDataRole.UserRole, i)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def shown(self) -> list[str]:
        return [self.actions[self.list.item(i).data(Qt.ItemDataRole.UserRole)][0] for i in range(self.list.count())]

    def _run_current(self) -> None:
        it = self.list.currentItem()
        if it is not None:
            self._run(it.data(Qt.ItemDataRole.UserRole))

    def _run(self, i) -> None:
        if i is None:
            return
        name, _keys, fn = self.actions[int(i)]
        self.close()
        try:
            fn()
        finally:
            self.ran.emit(name)

    def sizeHint(self) -> QSize:                          # noqa: N802
        return QSize(560, 420)


def install_command_box(win: QWidget, actions_fn: Callable[[], Sequence[tuple[str, str, Callable[[], None]]]]
                        ) -> QShortcut:
    """Ctrl+K on the window opens the command box (its actions read when it opens)."""
    sc = QShortcut(QKeySequence("Ctrl+K"), win)

    def open_box() -> None:
        box = CommandBox(win, actions_fn())
        g = win.geometry()
        box.move(g.x() + (g.width() - box.width()) // 2, g.y() + 120)
        win._command_box = box                           # noqa: SLF001 - kept while it is open (the tests read it)
        box.show()
        box.edit.setFocus()
    sc.activated.connect(open_box)
    return sc
