"""The Mono + red look's own pieces (run 18, task 8 -- you chose them from the mockups):

* ``PipelineDots``  -- a video's steps as dots and lines: ``● DOWNLOAD ─── ● CONVERT ─── ◉ EDIT ─── ○ DONE`` (● done
  green, ◉ running amber, ○ waiting dim, ✕ red where it failed)
* ``BlockBar``      -- a progress bar of blocks: ``■■■■■■■□□□ 68%  ~10 MIN`` (amber while running, green when done,
  red when it failed)
* ``CoverageStrip`` -- a finished or failed edit along the competitor's length: green where it was found in the RAW,
  red "not in RAW", amber unsure; 0:00 and the end time under it (``coverage_of(run_dir)`` reads the run's cut list)
* ``CommandBox``    -- Ctrl+K: type a few letters to jump to any page or action, each with its shortcut shown

Plain painting: no glow, no gradient. Run 19 (task 3), the running video moves -- calm, no sound, no glow, driven by
the run's real numbers (core.live), and Settings' "Animations" off makes every change happen at once:

* the dots pop (a short scale tick) when a step finishes; the block bar fills smoothly and never goes back;
* the coverage strip fills live: dark grey "not searched yet", each found piece fading in where it is;
* ``WaveScan``      -- the competitor's sound as a small wave, a thin red line sweeping across it with the search,
  the wave green where it was found, red where it is not in the RAW;
* ``RollCounters``  -- SHOTS FOUND 23 / 41, CAPTIONS, FRAME 00:00:09:12 (big, rolling to each new value);
* ``Typer``         -- the status line types itself in when the step changes;
* ``Receipt``       -- the finish: a dashed box (PASS, clips, captions, flashes, how much was found, the time) and
  "ready for premiere ›"."""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import motion as Mo
from . import theme
from . import widgets as W

DONE, RUN, WAIT, FAIL = "done", "running", "waiting", "failed"
POP_MS = 260                    # run 19: a dot's tick when its step finishes
FILL_MS = 650                   # the block bar's smooth fill
FADE_MS = 420                   # a found piece fading in on the coverage strip
ROLL_MS = 520                   # a counter rolling to its new value
TYPE_MS = 16                    # the status typing in: one letter this often (at most TYPE_MAX_MS in all)
TYPE_MAX_MS = 700


def _state_colour(state: str) -> str:
    return {DONE: theme.GREEN, RUN: theme.AMBER, FAIL: theme.RED}.get(state, theme.DIM)


class PipelineDots(QWidget):
    """``set([(label, state), ...])`` -- the states DONE / RUN / WAIT / FAIL."""

    def __init__(self, steps: Sequence[tuple[str, str]] = ()):
        super().__init__()
        self.steps: list[tuple[str, str]] = list(steps)
        self.pops: dict[int, Mo.Once] = {}          # run 19: the dots whose step just finished (a short tick)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)

    def set(self, steps: Sequence[tuple[str, str]]) -> None:
        steps = [(str(a), str(b)) for a, b in steps]
        if steps != self.steps:
            before = dict(self.steps)
            self.steps = steps
            for i, (a, s) in enumerate(steps):
                if s == DONE and before.get(a, DONE) != DONE:
                    self._pop(i)
            self.update()

    def _pop(self, i: int) -> None:
        if not Mo.enabled():
            return
        o = self.pops.get(i) or Mo.Once(self, self.update, POP_MS, Mo.LINEAR)
        self.pops[i] = o
        o.play()

    def scale(self, i: int) -> float:
        """The dot's size now (1: still; up to 1.6 in the middle of its tick)."""
        o = self.pops.get(i)
        return 1.0 + 0.6 * math.sin(math.pi * o.value) if o is not None and o.active else 1.0

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
            k = self.scale(i)
            if k != 1.0:                                 # run 19: the tick -- the dot a little bigger, then back
                w0 = fm.horizontalAdvance(m)
                p.save()
                p.translate(x + w0 / 2.0, cy)
                p.scale(k, k)
                p.drawText(QRectF(-w0, -self.height() / 2.0, 2 * w0, self.height()), Qt.AlignmentFlag.AlignCenter, m)
                p.restore()
            else:
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
        self.shown = 0.0                         # run 19: what is drawn -- it fills smoothly to ``share``
        self._fill = QVariantAnimation(self)
        self._fill.setEasingCurve(Mo.SOFT)
        self._fill.valueChanged.connect(self._step)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)

    def set(self, share: float, state: str = RUN, eta: str = "") -> None:
        share = max(0.0, min(1.0, float(share or 0.0)))
        if state == RUN and self.state == RUN:
            share = max(share, self.share)        # (a running bar never goes back)
        if (share, state, eta) != (self.share, self.state, self.eta):
            grow = state == RUN and self.state in (RUN, WAIT) and share > self.shown
            self.share, self.state, self.eta = share, state, eta
            self._fill.stop()
            if grow and Mo.enabled():
                self._fill.setStartValue(float(self.shown))
                self._fill.setEndValue(share)
                self._fill.setDuration(FILL_MS)
                self._fill.start()
            else:
                self.shown = share
            self.update()

    def _step(self, v) -> None:
        self.shown = float(v)
        self.update()

    def text(self) -> str:
        full = int(round(self.share * self.N))
        out = "■" * full + "□" * (self.N - full) + f" {int(round(100 * self.share))}%"
        return out + (f"  ~{self.eta.upper()}" if self.eta else "")

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        col = QColor(_state_colour(self.state if self.state != WAIT else RUN))
        filled = max(0.0, min(1.0, self.shown)) * self.N          # run 19: the last block filled part-way
        x, s = 0.0, 9.0
        top = (self.height() - s) / 2.0
        for i in range(self.N):
            r = QRectF(x, top, s, s)
            part = max(0.0, min(1.0, filled - i))
            if part >= 1.0:
                p.fillRect(r, col)
            else:
                p.setPen(QColor(theme.LINE2))
                p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
                if part > 0.0:
                    p.fillRect(QRectF(x, top, s * part, s), col)
            x += s + 3
        f = W.mono(10, 600, 4)
        p.setFont(f)
        p.setPen(col)
        t = f"{int(round(100 * self.shown))}%"
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
        self.live = False                         # run 19: while the run searches (dark grey = not searched yet)
        self.new: set[tuple] = set()              # the pieces that came with the last set(): they fade in
        self.fade = Mo.Once(self, self.update, FADE_MS, Mo.SOFT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)
        self.setToolTip("Where the competitor was found in the RAW: green found, red not in the RAW, amber unsure "
                        "(dark grey: not searched yet)")

    def set(self, duration: float, spans: Sequence[tuple[float, float, str]], live: bool = False) -> None:
        spans = [tuple(s) for s in spans]
        new = {s for s in spans if s not in set(self.spans)}
        if (max(0.0, float(duration or 0.0)), spans, bool(live)) == (self.duration, self.spans, self.live):
            return
        self.duration, self.spans, self.live = max(0.0, float(duration or 0.0)), spans, bool(live)
        self.new = new if self.spans and new != set(spans) or live else set()
        if self.new:
            self.fade.play()
        self.setVisible(self.duration > 0 and (bool(self.spans) or self.live))
        self.update()

    def alpha_of(self, span: tuple) -> float:
        """How far a piece has faded in (1: there)."""
        return self.fade.value if span in self.new and self.fade.active else 1.0

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
        for sp in self.spans:
            t0, t1, kind = sp
            a, b = w * max(0.0, t0) / self.duration, w * min(self.duration, t1) / self.duration
            if b > a:                              # run 19: a new piece fades in from the grey
                p.fillRect(QRectF(a, 2, max(1.0, b - a), 4),
                           Mo.mix(theme.LINE, cols.get(kind, theme.DIM), self.alpha_of(sp)))
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


_KIND_COL = {"raw": "GREEN", "not": "RED", "unsure": "AMBER"}


def _kind_at(spans: Sequence[tuple[float, float, str]], t: float) -> str | None:
    for a, b, k in spans:
        if a <= t < b:
            return k
    return None


class WaveScan(QWidget):
    """Run 19: the competitor's sound as a small wave (``set(wave, duration, scan, spans)``): a thin red line sweeps
    across it while the search runs (``scan`` 0..1, None: no line), each bar green where that moment was found in the
    RAW, amber unsure, red not in the RAW; dim where it was searched and nothing is known yet, darker ahead."""

    def __init__(self):
        super().__init__()
        self.wave: list[float] = []
        self.duration, self.spans, self.scan = 0.0, [], None
        self.x = 0.0                                   # the line where it is drawn (it glides to ``scan``)
        self._glide = QVariantAnimation(self)
        self._glide.setEasingCurve(Mo.LINEAR)
        self._glide.valueChanged.connect(self._at)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(30)
        self.setToolTip("The competitor's sound: the red line is the search, green found in the RAW, red not in it")
        self.hide()

    def set(self, wave: Sequence[float], duration: float, scan: float | None,
            spans: Sequence[tuple[float, float, str]]) -> None:
        self.wave, self.duration, self.spans = list(wave or []), float(duration or 0.0), list(spans)
        if scan is not None and (self.scan is None or scan < self.scan - 0.5):
            self.x = float(scan)                      # (a new pass starts from the left)
        self.scan = None if scan is None else max(0.0, min(1.0, float(scan)))
        self._glide.stop()
        if self.scan is not None and Mo.enabled() and self.scan > self.x:
            self._glide.setStartValue(float(self.x))
            self._glide.setEndValue(self.scan)
            self._glide.setDuration(1800)             # (the next counter comes in a few seconds: it never stops)
            self._glide.start()
        elif self.scan is not None:
            self.x = self.scan
        self.setVisible(bool(self.wave))
        self.update()

    def _at(self, v) -> None:
        self.x = float(v)
        self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        if not self.wave:
            return
        p = QPainter(self)
        w, h = float(self.width()), float(self.height())
        n = len(self.wave)
        bw = w / n
        mid = h / 2.0
        for i, v in enumerate(self.wave):
            t = (i + 0.5) / n
            k = _kind_at(self.spans, t * self.duration) if self.duration > 0 else None
            if k is not None:
                col = QColor(getattr(theme, _KIND_COL[k]))
            elif self.scan is not None and t <= self.x:
                col = QColor(theme.DIM)
            else:
                col = QColor(theme.LINE2)
            hh = max(1.0, v * (h - 4))
            p.fillRect(QRectF(i * bw, mid - hh / 2.0, max(1.0, bw - 1.0), hh), col)
        if self.scan is not None:
            p.fillRect(QRectF(self.x * w - 0.5, 0, 1.0, h), QColor(theme.RED))
        p.end()


class _Roll:
    """One number rolling to its new value (the counters)."""

    def __init__(self, owner: QWidget):
        self.value: float = 0.0
        self.target: float | None = None
        self._a = QVariantAnimation(owner)
        self._a.setEasingCurve(Mo.SOFT)
        self._a.valueChanged.connect(lambda v: (setattr(self, "value", float(v)), owner.update()))

    def to(self, v: float | None) -> None:
        if v is None:
            self.target = None
            return
        v = float(v)
        if v == self.target:
            return
        first = self.target is None
        self.target = v
        self._a.stop()
        if first or not Mo.enabled():
            self.value = v
            return
        self._a.setStartValue(float(self.value))
        self._a.setEndValue(v)
        self._a.setDuration(ROLL_MS)
        self._a.start()


class RollCounters(QWidget):
    """Run 19: the running video's big counters -- ``set(shots, captions, frame, frames)``: SHOTS FOUND ``found /
    all`` once the edit is made (before that, while it searches: FRAMES FOUND ``frames`` = (found, searched or
    None)), CAPTIONS ``n``, FRAME ``(frame, fps)`` as a timecode. A value not known yet is a dash; each rolls to its
    new value."""

    def __init__(self):
        super().__init__()
        self.shots_a, self.shots_b, self.caps, self.frame = _Roll(self), _Roll(self), _Roll(self), _Roll(self)
        self.fps = 0.0
        self.first = "SHOTS FOUND"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(44)
        self.hide()

    def set(self, shots: tuple | None, captions: int | None, frame: tuple | None, frames: tuple | None = None) -> None:
        if not shots and frames:                       # (the shots are known once the edit is made)
            shots, self.first = frames, "FRAMES FOUND"
        elif shots:
            if self.first != "SHOTS FOUND":            # (another kind of number: no roll from the frames)
                self.shots_a.target = self.shots_b.target = None
            self.first = "SHOTS FOUND"
        self.shots_a.to(shots[0] if shots else None)
        self.shots_b.to(shots[1] if shots and shots[1] is not None else None)
        self.caps.to(captions)
        self.frame.to(frame[0] if frame else None)
        self.fps = float(frame[1]) if frame and frame[1] else self.fps
        self.update()

    def cells(self) -> list[tuple[str, str]]:
        """[(label, value)] as drawn now (the tests read it)."""
        def num(r: _Roll) -> str:
            return "-" if r.target is None else f"{int(round(r.value))}"
        shots = num(self.shots_a) + (f" / {num(self.shots_b)}" if self.shots_b.target is not None else "")
        from ..core.live import timecode
        fr = "--:--:--:--" if self.frame.target is None else timecode(int(round(self.frame.value)), self.fps)
        return [(self.first, shots), ("CAPTIONS", num(self.caps)), ("FRAME", fr)]

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        small, big = W.mono(8.5, 600, 6), W.mono(17, 700, 1)
        x = 0.0
        for label, value in self.cells():
            p.setFont(small)
            p.setPen(QColor(theme.DIM))
            p.drawText(QRectF(x, 0, 260, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            p.setFont(big)
            p.setPen(QColor(theme.TEXT if value[:1] not in ("-",) else theme.DIM))
            p.drawText(QRectF(x, 14, 300, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
            x += max(QFontMetrics(big).horizontalAdvance(value), QFontMetrics(small).horizontalAdvance(label)) + 36
        p.end()


class DoneCount(QWidget):
    """Run 19: "DONE n" in the Videos header -- ``set(n)``; it ticks up (rolls) when a video finishes."""

    def __init__(self):
        super().__init__()
        self.n = _Roll(self)
        self.setFixedHeight(18)
        self.setMinimumWidth(80)
        self.hide()

    def set(self, n: int) -> None:
        self.n.to(int(n))
        self.setVisible(int(n) > 0)
        self.update()

    def text(self) -> str:
        return f"DONE {int(round(self.n.value))}"

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        f = W.mono(10, 700, 6)
        p.setFont(f)
        p.setPen(QColor(theme.DIM))
        p.drawText(QRectF(0, 0, 60, self.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "DONE")
        p.setPen(QColor(theme.GREEN))
        x = QFontMetrics(f).horizontalAdvance("DONE ") + 2
        p.drawText(QRectF(x, 0, 60, self.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{int(round(self.n.value))}")
        p.end()


class Typer:
    """Run 19: a status label's text types itself in when its step changes -- ``set(head, tail)``: ``head`` (the
    step in plain words) is typed letter by letter when it is new, ``tail`` (rich text: the %, the time left, the
    S-number) is shown as it is. Animations off: at once."""

    def __init__(self, label: QLabel):
        self.label = label
        self.head, self.tail, self.n = "", "", 0
        self._t = QTimer(label)
        self._t.timeout.connect(self._tick)
        self.step = 1

    def set(self, head: str, tail: str = "") -> None:
        if head != self.head:
            self.head, self.n = head, 0
            self.step = max(1, math.ceil(len(head) * TYPE_MS / TYPE_MAX_MS))
            if Mo.enabled() and head:
                self._t.start(TYPE_MS)
            else:
                self.n = len(head)
        self.tail = tail
        self._show()

    def stop(self) -> None:
        """The label is someone else's again (the row is not running): no typing goes on into it."""
        self._t.stop()
        self.head, self.tail, self.n = "", "", 0

    def _tick(self) -> None:
        self.n = min(len(self.head), self.n + self.step)
        if self.n >= len(self.head):
            self._t.stop()
        self._show()

    def typing(self) -> bool:
        return self.n < len(self.head)

    def _show(self) -> None:
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setText(html.escape(self.head[:self.n]) + (self.tail if not self.typing() else ""))


class Receipt(QWidget):
    """Run 19: the finish -- a small dashed box: ✓ PASS, the clips, captions and flashes, how much of the competitor
    was found in the RAW, the time taken, and "ready for premiere ›" (``premiere`` when it is clicked)."""

    premiere = Signal()

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self.word, self.good = "", True
        self.appear = Mo.Once(self, self.update, 360, Mo.SOFT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(62)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def set(self, final: dict | None, shown_before: bool = True) -> None:
        f = dict(final or {})
        label = str(f.get("label") or "")
        if not label:
            self.hide()
            return
        self.good = label in ("PASS", "OK with a note")
        self.word = ("✓ " if self.good else "✕ ") + label
        bits = []
        for key, one, many in (("clips", "clip", "clips"), ("captions", "caption", "captions"),
                               ("flashes", "flash", "flashes")):
            if f.get(key) is not None:
                bits.append(f"{f[key]} {one if f[key] == 1 else many}")
        if f.get("raw_pct") is not None:
            bits.append(f"{f['raw_pct']} % found in the RAW")
        if f.get("seconds"):
            s = int(round(float(f["seconds"])))
            bits.append(f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s")
        self.lines = bits
        was = self.isVisible()
        self.show()
        if not was and not shown_before:
            self.appear.play()
        self.update()

    def text(self) -> str:
        return " · ".join([self.word] + self.lines) + ("  ready for premiere ›" if self.good else "")

    def mousePressEvent(self, e) -> None:                 # noqa: N802
        if self.good and e.button() == Qt.MouseButton.LeftButton:
            self.premiere.emit()
        super().mousePressEvent(e)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        a = self.appear.value if self.appear.active else 1.0
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(Mo.alpha(theme.LINE2 if self.good else theme.RED_LINE, a))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(r)
        p.setFont(W.mono(13, 700, 4))
        p.setPen(Mo.alpha(theme.GREEN if self.good else theme.RED, a))
        p.drawText(QRectF(12, 6, r.width() - 24, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.word)
        p.setFont(W.mono(10, 500, 2))
        p.setPen(Mo.alpha(theme.MUTED, a))
        p.drawText(QRectF(12, 32, r.width() - 200, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "  ·  ".join(self.lines))
        if self.good:
            p.setFont(W.mono(10, 700, 4))
            p.setPen(Mo.alpha(theme.RED, a))
            p.drawText(QRectF(r.width() - 200, 32, 188, 22),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "READY FOR PREMIERE ›")
        p.end()


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
