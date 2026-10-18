"""Motion (night 11): the app's own animations -- Qt's QVariantAnimation, never Windows' "Animation effects", so they
run the same when that is switched off -- and the user's rules for them:

* a hover only FADES: nothing moves, grows or jumps; in about 0.3 s, out about 0.45-0.5 s, on the soft curve
  cubic-bezier(.4, 0, .2, 1) (``SOFT``);
* a page glides in from the side of the tab clicked, 0.45 s on cubic-bezier(.22, 1, .36, 1) (``GLIDE``);
* everything can be switched off in Settings ("Animations"): then every change happens at once.

``Fader`` is the one hover value (0..1) every self-painted widget uses; ``Loop`` a 0..1 that repeats (the pulsing dot,
the shimmer, the living border); ``Once`` a 0..1 played once (a sweep).
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QVariantAnimation
from PySide6.QtGui import QColor

FADE_IN_MS = 300
FADE_OUT_MS = 480
TAB_MS = 450
GLIDE_MS = 450
SWEEP_IN_MS = 800               # the faint light that crosses a box under the mouse
SWEEP_OUT_MS = 1000

_on = True


def set_enabled(on: bool) -> None:
    """Settings' "Animations on/off"."""
    global _on
    _on = bool(on)


def enabled() -> bool:
    return _on


def bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    """CSS's cubic-bezier(x1, y1, x2, y2) as a Qt curve."""
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return c


SOFT = bezier(0.4, 0.0, 0.2, 1.0)
GLIDE = bezier(0.22, 1.0, 0.36, 1.0)
LINEAR = QEasingCurve(QEasingCurve.Type.Linear)
IN_OUT = QEasingCurve(QEasingCurve.Type.InOutSine)


def mix(a: str | QColor, b: str | QColor, t: float) -> QColor:
    """The colour ``t`` of the way from ``a`` to ``b`` (alpha too)."""
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, float(t)))
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t), round(ca.alpha() + (cb.alpha() - ca.alpha()) * t))


def alpha(c: str | QColor, a: float) -> QColor:
    """``c`` with alpha ``a`` (0..1)."""
    q = QColor(c)
    q.setAlphaF(max(0.0, min(1.0, float(a))))
    return q


class Fader(QObject):
    """A value 0..1 that fades: ``to(1)`` in FADE_IN_MS, ``to(0)`` in FADE_OUT_MS (shorter when it is part-way),
    on the soft curve; ``on_change`` (usually the widget's update) on every step."""

    def __init__(self, parent: QObject, on_change: Callable[[], None], in_ms: int = FADE_IN_MS,
                 out_ms: int = FADE_OUT_MS, curve: QEasingCurve = SOFT):
        super().__init__(parent)
        self.value = 0.0
        self.target = 0.0
        self.in_ms, self.out_ms = in_ms, out_ms
        self._cb = on_change
        self._a = QVariantAnimation(self)
        self._a.setEasingCurve(curve)
        self._a.valueChanged.connect(self._step)

    def to(self, v: float) -> None:
        v = max(0.0, min(1.0, float(v)))
        if v == self.target and (self._a.state() == QVariantAnimation.State.Running or v == self.value):
            return
        self.target = v
        self._a.stop()
        span = abs(v - self.value)
        ms = int((self.in_ms if v > self.value else self.out_ms) * span)
        if not _on or ms <= 0:
            self.value = v
            self._cb()
            return
        self._a.setStartValue(float(self.value))
        self._a.setEndValue(v)
        self._a.setDuration(max(1, ms))
        self._a.start()

    def set(self, v: float) -> None:
        """Jump (no animation)."""
        self._a.stop()
        self.value = self.target = max(0.0, min(1.0, float(v)))
        self._cb()

    def _step(self, v) -> None:
        self.value = float(v)
        self._cb()


class Loop(QObject):
    """0..1 again and again (``period_ms`` a round): the pulsing dot, the shimmer, the living border. Stopped, its
    value stays where it is; with animations off it does not run."""

    def __init__(self, parent: QObject, on_change: Callable[[], None], period_ms: int,
                 curve: QEasingCurve = LINEAR):
        super().__init__(parent)
        self.value = 0.0
        self._cb = on_change
        self._a = QVariantAnimation(self)
        self._a.setStartValue(0.0)
        self._a.setEndValue(1.0)
        self._a.setDuration(period_ms)
        self._a.setLoopCount(-1)
        self._a.setEasingCurve(curve)
        self._a.valueChanged.connect(self._step)

    def start(self) -> None:
        if _on and self._a.state() != QVariantAnimation.State.Running:
            self._a.start()

    def stop(self) -> None:
        self._a.stop()

    def running(self) -> bool:
        return self._a.state() == QVariantAnimation.State.Running

    def _step(self, v) -> None:
        if not _on:                                 # switched off in Settings while it ran
            self._a.stop()
            self.value = 0.0
        else:
            self.value = float(v)
        self._cb()


class Once(QObject):
    """0..1 played once (``play()``), then back to "not playing" (``active`` False): a sweep across a box."""

    def __init__(self, parent: QObject, on_change: Callable[[], None], ms: int, curve: QEasingCurve = SOFT):
        super().__init__(parent)
        self.value = 0.0
        self.active = False
        self._cb = on_change
        self._a = QVariantAnimation(self)
        self._a.setStartValue(0.0)
        self._a.setEndValue(1.0)
        self._a.setDuration(ms)
        self._a.setEasingCurve(curve)
        self._a.valueChanged.connect(self._step)
        self._a.finished.connect(self._end)

    def play(self) -> None:
        if not _on:
            return
        self._a.stop()
        self.active = True
        self._a.start()

    def _step(self, v) -> None:
        self.value = float(v)
        self._cb()

    def _end(self) -> None:
        self.active = False
        self._cb()
