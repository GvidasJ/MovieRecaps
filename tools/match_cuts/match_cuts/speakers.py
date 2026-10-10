"""speakers.py: the person speaking is always in the picture (--premiere framing, task 2).

The check, for one clip's fixed framing (a Sim: RAW px -> sequence px, rotation 0) and the RAW it plays:

* while someone speaks (the RAW's speech map: speech.SpeechMap sounds that are speech), the speaking person
  (people.People.speaker: the clip's dominant speaker by Light-ASD, else the biggest face) must be in the template
  window -- their face box over the speech frames (the 10th-90th percentile of its edges: a head turning for a
  frame does not count) inside x 42-1039, y 555-1591, its centre in and no more than SHOWN_FRAC of it cut off at an
  edge (``shown``: a close-up the competitor crops at the forehead shows its person);
* when nobody speaks, at least one person must be shown so;
* a clip with nobody in the picture (B-roll, a hand, an object) cannot be checked: listed, not failed.

The fix (``reframe``): keep the zoom, move the picture sideways so the person is centred in the window -- as far as
the picture still covers the window; up or down only when the face is cut off at the top or bottom. It beats
--min-move -- and, since night 3, the competitor's framing only where that shows nobody at all (``shows_anyone``):
where it shows a person, even not the one the speech detection picked, the competitor chose whom to show (021: you
framed 3 of the 5 re-framed clips back onto the competitor's subject -- the "speaker" was a face at the edge of a wide
shot; laptop004, a cartoon, was re-framed onto the wrong panda). The hard check then lists such a clip ("shows
another person") instead of failing the run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

INSIDE_TOL_PX = 0.5          # rounding of the written values
# How much of a face box may lie past a window edge while the framing still shows that person (of its width / height,
# per side). output/020's close-ups (faces ~700 px tall in the 1037 px window) are cropped at the forehead by the
# competitor itself: a fully-inside rule called that "someone else" and re-centred the picture onto the other person.
SHOWN_FRAC = 0.15


@dataclass
class Faces:
    """What the framing of a clip has to show: the person's face box (x0, y0, x1, y1) in RAW px (unflipped), why
    ('speaker' / 'biggest face' / 'a person' -- any of ``boxes`` -- / 'nobody'), whether someone speaks."""
    how: str
    speaking: bool
    box: tuple[float, float, float, float] | None = None              # the one that must be inside ('speaker' ...)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)   # 'a person': any one of these
    track: int | None = None
    share: float = 0.0
    others: list[tuple[float, float, float, float]] = field(default_factory=list)  # every face present over the clip


@dataclass
class Context:
    """What the framing decisions need: the RAW's people, its speech (RAW s) and shot changes (RAW s)."""
    people: Any
    speech: list[tuple[float, float]]
    shots: list[float] | None
    raw_wh: tuple[float, float]

    def faces(self, t0: float, t1: float) -> Faces | None:
        """The face(s) RAW [t0, t1) has to show, or None when that stretch was not analysed."""
        p = self.people
        if p is None or not p.covered(t0, t1):
            return None
        v = p.speaker(t0, t1, self.speech)
        ks = v["ks"]
        if v["track"] is None:
            return Faces("nobody", bool(v["speaking"]))
        everyone = [b for b in (extent(t, ks) for t in p.present(ks)) if b is not None]
        if v["how"] == "a person":
            return Faces("a person", False, None, everyone, others=everyone)
        b = extent(v["track"], ks)
        return Faces(v["how"], True, b, [b] if b else [], int(v["track"].id), float(v["share"]), everyone) if b else \
            Faces("nobody", True)

    def same_shot(self, t_a: float, t_b: float) -> bool:
        """RAW times t_a and t_b in one shot (no shot change between them)."""
        if not self.shots:
            return True
        lo, hi = sorted((t_a, t_b))
        return not any(lo < c <= hi + 1e-9 for c in self.shots)


def extent(t: Any, ks: np.ndarray) -> tuple[float, float, float, float] | None:
    """The face box of track t over the frames ks (where it is there): 10th percentile of its left / top edges, 90th
    of its right / bottom ones."""
    idx = [t.at(int(k)) for k in ks]
    idx = [i for i in idx if i is not None]
    if not idx:
        return None
    b = t.box[idx]
    return (float(np.percentile(b[:, 0], 10)), float(np.percentile(b[:, 1], 10)),
            float(np.percentile(b[:, 2], 90)), float(np.percentile(b[:, 3], 90)))


def on_screen(sim: Any, box: tuple[float, float, float, float], raw_w: float, flip: bool
              ) -> tuple[float, float, float, float]:
    """A RAW box in sequence px under a rotation-0 framing (flipped RAW: x mirrored first)."""
    x0, y0, x1, y1 = box
    if flip:
        x0, x1 = raw_w - x1, raw_w - x0
    return (sim.s * x0 + sim.tx, sim.s * y0 + sim.ty, sim.s * x1 + sim.tx, sim.s * y1 + sim.ty)


def inside(r: tuple[float, float, float, float], win: tuple[float, float, float, float]) -> bool:
    x, y, w, h = win
    t = INSIDE_TOL_PX
    return r[0] >= x - t and r[1] >= y - t and r[2] <= x + w + t and r[3] <= y + h + t


def shown(r: tuple[float, float, float, float], win: tuple[float, float, float, float]) -> bool:
    """A face box on screen ``r`` is shown by the window: its centre inside, at most SHOWN_FRAC of its width / height
    past any edge."""
    x, y, w, h = win
    tx, ty = INSIDE_TOL_PX + SHOWN_FRAC * (r[2] - r[0]), INSIDE_TOL_PX + SHOWN_FRAC * (r[3] - r[1])
    cx, cy = (r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0
    return (x <= cx <= x + w and y <= cy <= y + h and r[0] >= x - tx and r[1] >= y - ty and r[2] <= x + w + tx and
            r[3] <= y + h + ty)


def passes(sim: Any, f: Faces | None, raw_w: float, flip: bool, win: tuple[float, float, float, float]) -> bool:
    """The framing shows what it must (module docstring); True when there is nothing to check."""
    if f is None or f.how == "nobody":
        return True
    if f.how == "a person":
        return any(shown(on_screen(sim, b, raw_w, flip), win) for b in f.boxes) if f.boxes else True
    return shown(on_screen(sim, f.box, raw_w, flip), win)


def shows_anyone(sim: Any, f: Faces | None, raw_w: float, flip: bool, win: tuple[float, float, float, float]) -> bool:
    """The framing shows a person of the clip -- the one speaking or anyone else (night 3: the competitor's choice of
    whom to show wins; you framed 021's re-framed clips back onto the competitor's subject 3 times of 5, and
    laptop004's re-frame onto "the speaker" showed the wrong panda)."""
    if f is None or f.how == "nobody":
        return True
    return any(shown(on_screen(sim, b, raw_w, flip), win) for b in ([f.box] if f.box else []) + list(f.boxes)
               + list(f.others) if b)


def x_range(sim: Any, box: tuple[float, float, float, float], raw_wh: tuple[float, float], flip: bool,
            win: tuple[float, float, float, float]) -> tuple[float, float] | None:
    """The tx values (sequence px; the zoom and ty kept) where the box is inside the window and the picture still
    covers it, or None."""
    x, y, w, h = win
    W, H = raw_wh
    b = on_screen(type(sim)(sim.s, 0.0, 0.0, sim.ty), box, W, flip)
    lo = max(x - b[0], x + w - sim.s * W)                       # the face's left edge in / the picture's right edge
    hi = min(x + w - b[2], x)                                   # the face's right edge in / the picture's left edge
    return (lo, hi) if hi >= lo - 1e-9 else None


def y_range(sim: Any, box: tuple[float, float, float, float], raw_wh: tuple[float, float],
            win: tuple[float, float, float, float]) -> tuple[float, float] | None:
    x, y, w, h = win
    W, H = raw_wh
    lo = max(y - sim.s * box[1], y + h - sim.s * H)
    hi = min(y + h - sim.s * box[3], y)
    return (lo, hi) if hi >= lo - 1e-9 else None


def centred(sim: Any, box: tuple[float, float, float, float], raw_wh: tuple[float, float], flip: bool,
            win: tuple[float, float, float, float]) -> Any | None:
    """The framing with the same zoom moved sideways to centre the box in the window (as far as the picture still
    covers it), and up / down only as much as needed when the box is cut off there; None when no move fits."""
    from .geometry import Sim
    xr = x_range(sim, box, raw_wh, flip, win)
    if xr is None:
        return None
    W = raw_wh[0]
    x0, _, x1, _ = box
    cx = ((W - x1) + (W - x0)) / 2.0 if flip else (x0 + x1) / 2.0
    want = win[0] + win[2] / 2.0 - sim.s * cx
    tx = min(max(want, xr[0]), xr[1])
    ty = sim.ty
    if not (sim.s * box[1] + ty >= win[1] - INSIDE_TOL_PX and sim.s * box[3] + ty <= win[1] + win[3] + INSIDE_TOL_PX):
        yr = y_range(sim, box, raw_wh, win)
        if yr is None:
            return None
        ty = min(max(ty, yr[0]), yr[1])
    return Sim(sim.s, 0.0, tx, ty)


def target(sim: Any, f: Faces | None, raw_w: float, flip: bool, win: tuple[float, float, float, float]
           ) -> tuple[float, float, float, float] | None:
    """The box a framing has to show for ``f``: the speaker's (or the biggest face's); for 'a person' the one that
    needs the smallest move from ``sim`` (one already inside when there is one); None when nothing is to be shown."""
    if f is None or f.how == "nobody":
        return None
    if f.how != "a person":
        return f.box
    if not f.boxes:
        return None
    wx = win[0] + win[2] / 2.0

    def cost(b: tuple[float, float, float, float]) -> float:
        r = on_screen(sim, b, raw_w, flip)
        return 0.0 if shown(r, win) else abs((r[0] + r[2]) / 2.0 - wx)
    return min(f.boxes, key=cost)


def frame_run(sim: Any, items: Sequence[tuple[Any, Faces | None, bool]], raw_wh: tuple[float, float],
              win: tuple[float, float, float, float]) -> list[Any]:
    """The framings of a stretch of clips that show one framing ``sim`` (zoom kept): [(clip) -> Sim or None (no
    change)] in the order of ``items`` = [(clip, Faces, flip)]. One framing for the whole stretch when one sideways
    position shows every clip's person -- the speakers centred as well as that allows --, else each clip that does
    not show its person on its own: that person centred (``centred``)."""
    from .geometry import Sim
    W = raw_wh[0]
    boxes = [(c, target(sim, f, W, fl, win), fl) for c, f, fl in items]
    need = [(c, b, fl) for c, b, fl in boxes if b is not None]
    if not need:
        return [None] * len(items)
    lo, hi = -math.inf, math.inf
    ty = sim.ty
    for c, b, fl in need:                          # up / down only when a face is cut off there
        if not (sim.s * b[1] + ty >= win[1] - INSIDE_TOL_PX and sim.s * b[3] + ty <= win[1] + win[3] + INSIDE_TOL_PX):
            yr = y_range(Sim(sim.s, 0.0, sim.tx, ty), b, raw_wh, win)
            if yr is not None:
                ty = min(max(ty, yr[0]), yr[1])
    common = Sim(sim.s, 0.0, sim.tx, ty)
    for c, b, fl in need:
        xr = x_range(common, b, raw_wh, fl, win)
        if xr is None:
            lo, hi = 1.0, 0.0
            break
        lo, hi = max(lo, xr[0]), min(hi, xr[1])
    if lo <= hi + 1e-9 and all(sim.s * b[1] + ty >= win[1] - INSIDE_TOL_PX and
                               sim.s * b[3] + ty <= win[1] + win[3] + INSIDE_TOL_PX for _, b, _ in need):
        centres = [((W - b[2]) + (W - b[0])) / 2.0 if fl else (b[0] + b[2]) / 2.0 for _, b, fl in need]
        want = win[0] + win[2] / 2.0 - sim.s * float(np.median(centres))
        one = Sim(sim.s, 0.0, min(max(want, lo), hi), ty)
        return [one] * len(items)
    out = []
    for c, b, fl in boxes:
        if b is None or shown(on_screen(sim, b, W, fl), win):
            out.append(None)
        else:
            out.append(centred(sim, b, raw_wh, fl, win))
    return out
