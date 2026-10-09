"""--keep-speed: the Premiere edit plays every RAW clip at 100 % instead of the competitor's speed change -- the same
moments of the RAW (each clip starts on the frame the competitor's starts on and covers the same stretch of it) in the
same order, so a clip the competitor sped up to 125 % lasts 1.25 x as long on the timeline.

``keep_speed(cutlist)`` lays the cut list out again on that longer timeline (still at the competitor's frame rate):
every competitor frame k moves to ``KeepMap.map(k)``, a piecewise-linear stretch that lengthens the frames a RAW clip
plays by its |speed| (frames of a placeholder, a dip, a freeze, another video: unchanged). Each RAW segment then
plays at +-100 % (a reverse stays a reverse, a ramp becomes 100 %; a freeze stays a freeze), its framing keys, audio
offsets and dissolves moved with it. The Premiere plan (speech-safe cuts, silences, captions transcribed from the cut
edit) is made on this cut list as on any other; the competitor's burned-in captions move by ``KeepMap.map``.
The preview, compare.mp4 and the checks against the competitor keep the competitor's own timing.

Where a dissolve joins two clips of different speeds, the overlap is stretched by the outgoing clip's speed: the
incoming clip still starts on its moment, its end may land a few RAW frames off (the overlap x the speed difference).
"""
from __future__ import annotations

import bisect
import copy
import dataclasses
from dataclasses import dataclass, field
from typing import Any

from .model import Cutlist, Segment


@dataclass
class KeepMap:
    """Competitor frame -> frame of the --keep-speed timeline: breakpoints ``knots`` [(competitor frame, new frame)],
    linear between them (float frames; ``map`` rounds)."""
    knots: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 0.0)])

    def at(self, k: float) -> float:
        xs = [a for a, _ in self.knots]
        i = bisect.bisect_right(xs, float(k)) - 1
        if i < 0:
            return float(k) - self.knots[0][0] + self.knots[0][1]
        if i >= len(self.knots) - 1:
            a, b = self.knots[-1]
            return b + (float(k) - a)
        (a0, b0), (a1, b1) = self.knots[i], self.knots[i + 1]
        return b0 + (b1 - b0) * (float(k) - a0) / (a1 - a0)

    def map(self, k: float) -> int:
        return int(round(self.at(k)))

    @property
    def stretched(self) -> bool:
        return any(abs((b1 - b0) - (a1 - a0)) > 1e-9 for (a0, b0), (a1, b1) in zip(self.knots, self.knots[1:]))


def _rate(seg: Segment, comp_fps: Any) -> float:
    """How much longer a frame of ``seg`` plays at 100 %: |speed| of a RAW clip (a ramp: its average), else 1."""
    if seg.type != "raw":
        return 1.0
    if seg.time_remap_keys:
        from .export_xml_edl import seg_speed
        v = abs(float(seg_speed(seg, comp_fps)))
        return v if v > 1e-6 else 1.0                       # a freeze stays one frame held as long
    v = abs(float(seg.speed))
    return v if v > 1e-6 else 1.0


def keep_map(cutlist: Cutlist) -> KeepMap:
    """The stretch of the competitor's timeline (module docstring): each frame takes the rate of the earliest-starting
    RAW clip that covers it (the outgoing clip inside a dissolve)."""
    n = int(cutlist.competitor["frames"])
    comp_fps = cutlist.comp_fps
    bounds = sorted({0, n} | {int(s.comp_in) for s in cutlist.segments} | {int(s.comp_out) for s in cutlist.segments})
    bounds = [b for b in bounds if 0 <= b <= n]
    knots, y = [(float(bounds[0]), float(bounds[0]))], float(bounds[0])
    for a, b in zip(bounds, bounds[1:]):
        cover = [s for s in cutlist.segments if int(s.comp_in) <= a and b <= int(s.comp_out)]
        raw = [s for s in cover if s.type == "raw"]
        r = _rate(min(raw, key=lambda s: (int(s.comp_in), int(s.id))), comp_fps) if raw else 1.0
        y += r * (b - a)
        knots.append((float(b), y))
    return KeepMap(knots)


def keep_speed(cutlist: Cutlist) -> tuple[Cutlist, KeepMap]:
    """(the cut list laid out at 100 % on the longer timeline, its KeepMap); unchanged (and an identity map) when no
    clip changes speed."""
    km = keep_map(cutlist)
    if not km.stretched:
        return cutlist, km
    comp_fps = cutlist.comp_fps
    segs = []
    for s in cutlist.segments:
        t = copy.deepcopy(s)
        a, b = km.map(s.comp_in), km.map(s.comp_out)
        t.comp_in, t.comp_out = a, max(a + 1, b)
        if t.transform_keys:
            t.transform_keys = [dict(k, comp_frame=km.at(float(k["comp_frame"]))) for k in t.transform_keys]
        if s.type == "raw" and s.time_remap_keys and _is_freeze(s, comp_fps):
            t.time_remap_keys = [dict(k, comp_frame=km.at(float(k["comp_frame"]))) for k in s.time_remap_keys]
        elif s.type == "raw":
            backwards = float(s.speed) < 0 or (bool(s.time_remap_keys) and _remap_backwards(s))
            if s.time_remap_keys:                                   # a ramp: from its first moment at 100 %
                t.raw_in_seconds = float(s.time_remap_keys[0]["raw_seconds"])
                t.raw_in_frame = None
                t.time_remap_keys, t.time_mode, t.retime = [], "stretch", "none"
            t.speed = -1.0 if backwards else 1.0
            t.speed_measured, t.speed_range = None, None
        au = dict(t.audio or {})
        for key, edge in (("in_offset_frames", s.comp_in), ("out_offset_frames", s.comp_out)):
            off = int(au.get(key) or 0)
            if off:
                au[key] = km.map(edge + off) - km.map(edge)
        if isinstance(au.get("line"), dict) and float(au["line"].get("speed") or 1.0) != 0.0:
            ln = dict(au["line"])
            ln["speed"] = -1.0 if float(ln.get("speed") or 1.0) < 0 else 1.0
            au["line"] = ln
        t.audio = au
        for name in ("transition_in", "transition_out"):
            tr = getattr(s, name)
            if tr and int(tr.get("duration_frames") or 0) > 0:
                lo = s.comp_in if name == "transition_in" else s.comp_out - int(tr["duration_frames"])
                d = max(1, km.map(lo + int(tr["duration_frames"])) - km.map(lo))
                al = list(tr.get("alpha") or [])
                tr = dict(tr, duration_frames=d,
                          alpha=[al[min(len(al) - 1, int(i * len(al) / d))] for i in range(d)] if al else [])
                setattr(t, name, tr)
        t.cut_ambiguity = None
        t.tie_frames, t.low_margin_frames, t.ambiguous_frames = [], [], []
        segs.append(t)
    out = dataclasses.replace(cutlist, segments=segs,
                              competitor=dict(cutlist.competitor, frames=km.map(int(cutlist.competitor["frames"]))))
    return out, km


def _is_freeze(seg: Segment, comp_fps: Any) -> bool:
    from .export_xml_edl import seg_speed
    return abs(float(seg_speed(seg, comp_fps))) < 1e-6


def _remap_backwards(seg: Segment) -> bool:
    k = seg.time_remap_keys
    return len(k) >= 2 and float(k[-1]["raw_seconds"]) < float(k[0]["raw_seconds"])
