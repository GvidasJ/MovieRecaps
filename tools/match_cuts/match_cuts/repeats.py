"""repeats.py: no footage or audio of the RAW plays twice in my edit (the Premiere export).

* At a cut: when the end of one clip and the start of the next show the same RAW frames or play the same RAW audio (a
  stutter, a repeated syllable) -- up to REPEAT_S --, the repeat is trimmed so nothing plays twice: from the start of
  the next clip when it is there, else from the end of the clip before. Always.
* Anywhere: when the same RAW moment of more than REPEAT_S plays twice, one copy goes -- the one out of chronological
  order compared with the rest of the edit (a hook at the start), else the later one. ``--allow-repeats`` keeps them.

Each piece of V1 / A1 is a range of the sequence and the RAW range it plays (frames at the sequence rate; a reversed
clip plays its range backwards). The removed ranges join the silences' (silence.Ripple): the sequence closes up, A1
fades over the cut (no click), markers and captions move with it. ``check`` finds what is left in the final XML.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Sequence

REPEAT_S = 0.5            # a repeat longer than this anywhere goes (--allow-repeats keeps it); up to it, only at a cut
MAX_ROUNDS = 500


@dataclass
class Span:
    """One piece of a track: sequence frames [r0, r1) playing the RAW from position p0 at speed v (frames at the
    sequence rate): the RAW frames played over [t0, t1) are [min(p(t0), p(t1)), max(...)), p(t) = p0 + (t - r0) v
    (a reversed clip: p0 = its first frame + 1)."""
    track: str
    label: str
    r0: int
    r1: int
    p0: float
    v: float
    dissolve_in: bool = False         # joined to the piece before it by a cross dissolve (both show: not a repeat)
    raw_fps: float = 0.0              # V1: the RAW's own frame rate (a stutter is a RAW frame shown again); 0: unknown

    def raw_frame(self, t: int) -> int:
        """The RAW frame V1 shows at sequence frame t (the RAW's own frame grid)."""
        return int(math.floor(self.p(t) * self.raw_fps / self.seq_fps + 1e-6))

    seq_fps: float = 60.0

    def p(self, t: float) -> float:
        return self.p0 + (t - self.r0) * self.v

    def raw(self) -> tuple[float, float]:
        a, b = self.p(self.r0), self.p(self.r1)
        return min(a, b), max(a, b)

    def rec(self, lo: float, hi: float) -> tuple[int, int]:
        """The sequence frames of this piece that play RAW [lo, hi)."""
        ta, tb = self.r0 + (lo - self.p0) / self.v, self.r0 + (hi - self.p0) / self.v
        t0, t1 = min(ta, tb), max(ta, tb)
        return max(self.r0, int(round(t0))), min(self.r1, int(round(t1)))

    def cut(self, a: int, b: int) -> Span:
        return dataclasses.replace(self, r0=a, r1=b, p0=self.p(a), dissolve_in=self.dissolve_in and a == self.r0)


def spans_of_plan(clips: Sequence[Any], audio: Sequence[dict], comp_fps: Fraction, raw_fps: float = 0.0,
                  seq_fps: float = 60.0) -> list[Span]:
    """The V1 clips (export_xml_edl.PremiereClip) and A1 items of the plan as spans; a freeze (placed at 100 % with a
    RETIME marker, to redo by hand) is left out: what it will show is not what the XML says."""
    from .export_xml_edl import seg_speed
    out = []
    for cl in clips:
        if any(e.seg is not None and abs(seg_speed(e.seg, comp_fps)) < 1e-9 for e in (cl.events or [cl.ev])):
            continue
        p0 = cl.src_in + (1 if cl.speed < 0 else 0)
        out.append(Span("V1", cl.label, cl.rec_start, cl.rec_end, float(p0), float(cl.speed), cl.start == -1,
                        float(raw_fps), seq_fps=float(seq_fps)))
    for it in audio:
        if abs(float(it["speed"])) < 1e-9:
            continue
        p0 = it["in"] + (1 if it["speed"] < 0 else 0)
        out.append(Span("A1", _label(it), int(it["start"]), int(it["end"]), float(p0), float(it["speed"])))
    return out


def _label(it: dict) -> str:
    seg = it.get("seg")
    return f"S{int(seg.id):02d}" if seg is not None else str(it.get("label") or "?")


def _keep(a: int, b: int, cuts: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out = [(a, b)] if b > a else []
    for c0, c1 in cuts:
        out = [q for x0, x1 in out for q in ((x0, min(x1, c0)), (max(x0, c1), x1)) if q[1] > q[0]]
    return out


def _pieces(spans: Sequence[Span], cuts: Sequence[tuple[int, int]]) -> dict[str, list[Span]]:
    out: dict[str, list[Span]] = {}
    for s in spans:
        for a, b in _keep(s.r0, s.r1, cuts):
            out.setdefault(s.track, []).append(s.cut(a, b))
    for v in out.values():
        v.sort(key=lambda s: s.r0)
    return out


def _in_order(pieces: Sequence[Span]) -> set[int]:
    """Indices of the pieces in chronological order with the rest of the edit: the heaviest chain (by length) whose
    RAW positions never go back; the others (a hook from later in the RAW, shown first) are out of order."""
    n = len(pieces)
    if not n:
        return set()
    lo = [p.raw()[0] for p in pieces]
    w = [p.r1 - p.r0 for p in pieces]
    best, prev = list(w), [-1] * n
    for i in range(n):
        for j in range(i):
            if lo[j] <= lo[i] and best[j] + w[i] > best[i]:
                best[i], prev[i] = best[j] + w[i], j
    i = max(range(n), key=lambda k: best[k])
    out = set()
    while i >= 0:
        out.add(i)
        i = prev[i]
    return out


def _adjacent(p: Span, q: Span, cuts: Sequence[tuple[int, int]]) -> bool:
    """q follows p with nothing kept in between: at a cut (after the removed ranges close up)."""
    return p.r1 <= q.r0 and not _keep(p.r1, q.r0, cuts)


def find(spans: Sequence[Span], cuts: Sequence[tuple[int, int]], fps: Fraction, allow: bool = False,
         protect: Sequence[tuple[int, int]] = ()) -> dict | None:
    """The first repeat to remove from ``spans`` with ``cuts`` already removed (sequence frames), or None: a stutter at
    a cut first, then (unless ``allow``) the same RAW moment over REPEAT_S twice. {kind, track, remove (a, b), copy:
    (a, b) of the copy kept, labels, raw (lo, hi) frames, why}."""
    f = float(fps)
    tracks = _pieces(spans, cuts)
    found = []
    for track, ps in tracks.items():
        order = _in_order(ps)
        for i, p in enumerate(ps):
            for j in range(i + 1, len(ps)):
                q = ps[j]
                (a0, a1), (b0, b1) = p.raw(), q.raw()
                lo, hi = max(a0, b0), min(a1, b1)
                if hi - lo < 0.5:
                    continue
                dp, dq = p.rec(lo, hi), q.rec(lo, hi)
                if dp[1] <= dp[0] or dq[1] <= dq[0]:
                    continue
                n_s = (hi - lo) / f
                at_cut = j == i + 1 and _adjacent(p, q, cuts) and not q.dissolve_in
                if (at_cut and track == "V1" and p.raw_fps and q.raw_fps and p.v > 0 and q.v > 0
                        and q.raw_frame(q.r0) >= p.raw_frame(p.r1 - 1)):
                    continue                 # the same RAW frame held a moment longer (60 fps over a 25 fps RAW)
                if at_cut and n_s <= REPEAT_S + 1e-9 and (dq[0] <= q.r0 or dp[1] >= p.r1):
                    if dq[0] <= q.r0:
                        found.append((0, dq[0], dict(kind="stutter", track=track, remove=dq, copy=dp, removed=q.label,
                                                     kept=p.label, raw=(lo, hi), why="the start of the next clip")))
                    else:
                        found.append((0, dp[0], dict(kind="stutter", track=track, remove=dp, copy=dq, removed=p.label,
                                                     kept=q.label, raw=(lo, hi), why="the end of the clip before")))
                elif n_s > REPEAT_S + 1e-9 and not allow:
                    if i not in order and j in order:
                        rm, keep, labels, why = dp, dq, (p.label, q.label), "out of chronological order"
                    else:
                        rm, keep, labels, why = dq, dp, (q.label, p.label), "the later copy"
                    found.append((1, rm[0], dict(kind="repeat", track=track, remove=rm, copy=keep, removed=labels[0],
                                                 kept=labels[1], raw=(lo, hi), why=why)))
    if not found:
        return None
    found.sort(key=lambda x: (x[0], x[1]))
    for _, _, d in found:
        a, b = d["remove"]
        for p0, p1 in protect:                                           # never inside a cross dissolve
            if a < p1 and p0 < b:
                a, b = (a, min(b, p0)) if a < p0 else (max(a, p1), b)
        if b > a:
            d["remove"] = (a, b)
            return d
    return found[0][2] | {"remove": None}


def plan(spans: Sequence[Span], cuts: Sequence[tuple[int, int]], fps: Fraction, allow: bool = False,
         protect: Sequence[tuple[int, int]] = ()) -> tuple[list[dict], list[dict]]:
    """(the repeats removed, the repeats that could not be): find() until nothing is left, each removal added to
    ``cuts`` (sequence frames of the edit before any removal)."""
    removed: list[dict] = []
    left: list[dict] = []
    cur = sorted((int(a), int(b)) for a, b in cuts)
    for _ in range(MAX_ROUNDS):
        d = find(spans, cur, fps, allow, protect)
        if d is None:
            break
        if d["remove"] is None:                                          # inside a cross dissolve: listed, kept
            left.append(d)
            break
        removed.append(d)
        cur = _merge(cur + [d["remove"]])
    return removed, left


MIN_PIECE = 3               # sequence frames: a piece of a clip a removal would leave shorter than this goes too


def absorb_slivers(cuts: Sequence[tuple[int, int]], clips: Sequence[Any], n: int = MIN_PIECE) -> list[tuple[int, int]]:
    """The removals (sequence frames) widened over every piece of a clip they would leave shorter than ``n`` frames
    next to them: such a piece is a flash frame -- and in Premiere a source range that rounds to nothing (an item
    whose in is its out). A clip that is that short of its own is left alone."""
    cur = _merge(cuts)
    for _ in range(8):
        add = []
        for cl in clips:
            lo, hi = int(cl.rec_start), int(cl.rec_end)
            parts = [(lo, hi)]
            for a, b in cur:
                parts = [q for x0, x1 in parts for q in ((x0, min(x1, a)), (max(x0, b), x1)) if q[1] > q[0]]
            add += [(a, b) for a, b in parts if b - a < n and (a > lo or b < hi)]
        if not add:
            break
        cur = _merge(cur + add)
    return cur


def _merge(cuts: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(cuts):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def add_to_plan(sil: dict, cutlist: Any, cfg: Any = None) -> dict:
    """The silence plan (silence.plan_premiere / summarize) with the repeats removed too: the repeat ranges join its
    ripple's removals (one Ripple for the XML, A1, the markers and the captions; frames of the edit after the
    speech-safe cuts, its ``before``); ``sil['repeats']`` = {rows, left, removed_s, allow}. The silence rows' 'cut in
    the new edit' times are those of the final edit."""
    from .export_xml_edl import premiere_audio, premiere_clips, premiere_factor, premiere_settings
    from .silence import Cut, Ripple, apply_premiere
    fps = premiere_settings(cfg)["fps"]
    fac = premiere_factor(cutlist.comp_fps, fps)
    n_frames = int(cutlist.competitor["frames"]) * fac
    clips, _, _ = premiere_clips(cutlist, cfg)
    audio = premiere_audio(cutlist, clips, cfg) if bool(cutlist.raw.get("has_audio", True)) else []
    old = sil.get("ripple")
    snap = old.before if old is not None else None
    if snap is not None:
        if snap.active:
            clips, audio, _ = apply_premiere(clips, audio, [], snap)
        n_frames = snap.new_frames
    protect = [(cl.rec_start, cl.rec_start + int(cl.ev.dissolve_in) * fac) for cl in clips if cl.start == -1]
    sil_cuts = list(old.cuts) if old is not None else []
    allow = bool(getattr(cfg, "allow_repeats", False))
    done, left = plan(spans_of_plan(clips, audio, cutlist.comp_fps, float(cutlist.raw_fps), float(fps)),
                      [(c.a, c.b) for c in sil_cuts], fps, allow, protect)
    out = dict(sil)
    f = float(fps)
    merged = absorb_slivers([(c.a, c.b) for c in sil_cuts] + [d["remove"] for d in done], clips)
    if done or merged != [(c.a, c.b) for c in sil_cuts]:
        by_a = {(c.a, c.b): c for c in sil_cuts}
        rp = Ripple([by_a.get((a, b)) or Cut(a, b, a / f, b / f) for a, b in merged], n_frames, before=snap)
        out["ripple"] = rp
        out["cuts"] = [(c.a, c.b) for c in rp.cuts]
        out["final_s"] = round(rp.new_frames / f, 3)
        for r in out.get("rows") or []:
            r["new_at_s"] = round(rp.map1(int(r["a"])) / f, 3)
    else:
        rp = old
    rows = []
    for d in done:
        a, b = d["remove"]
        rows.append({"kind": d["kind"], "track": d["track"], "removed": d["removed"], "kept": d["kept"],
                     "a": a, "b": b, "copy": list(d["copy"]), "len_s": round((b - a) / f, 3),
                     "raw_s": [round(d["raw"][0] / f, 3), round(d["raw"][1] / f, 3)], "why": d["why"],
                     "new_at": rp.map1(a) if rp is not None else a})
    out["repeats"] = {"rows": rows, "left": [dict(d, remove=None) for d in left], "allow": allow,
                      "removed_s": round(sum(r["b"] - r["a"] for r in rows) / f, 3), "fps": str(fps)}
    return out


def check(spans: Sequence[Span], fps: Fraction, allow: bool = False) -> list[dict]:
    """The repeats left in a final edit (its own spans, nothing removed): every one find() would still remove."""
    left, cur = [], []
    for _ in range(MAX_ROUNDS):
        d = find(spans, cur, fps, allow)
        if d is None:
            break
        left.append(d)
        if d["remove"] is None:
            break
        cur = _merge(cur + [d["remove"]])            # look past it for the next one
    return left
