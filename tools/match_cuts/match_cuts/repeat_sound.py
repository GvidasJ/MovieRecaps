"""repeat_sound.py: a "repeat" is only a repeat when its sound repeats too (run 18, task 3: cartoons).

Cartoons reuse their drawings (a held reaction, a mouth, a background): the picture search can put a NEW moment of the
competitor on a RAW moment another piece already plays -- the same image, so it scores as well there. The repeat
removal (repeats.py) then cuts it out of the edit: a part the competitor shows is missing (Family Guy S05E16, "The
Funniest Phone Call": S16-S18, 1.1 s of new words over the held phone-call drawing, all put on RAW 128.73 s, which S15
already plays; 1.0 s of them cut -- the competitor plays that scene at 103 %).

In the Premiere plan (premiere_plan, before join_takes; nothing of the cached analysis changes), for every piece whose
RAW moment another piece plays too:

1. **The sound's line there:** the competitor's confident sound windows around the piece (FIT_S either side, at
   least MIN_WINDOWS) fitted with one straight line RAW = a + b * competitor time (any speed: the competitor may play a
   scene faster), only when they all lie on it (within FIT_RES). The picture sits a few frames off the sound (an A/V
   offset): measured on the pieces near it that follow the sound (within AV_MAX).
2. **The sound repeats too** (the piece's picture is where its sound is, within SAME_S): a real repeat -- left to the
   repeat removal, as before.
3. **Else the piece is put where its sound is** -- right after the piece before it (or just before the one after it)
   when that is where the sound is (within CONTINUE_S), else the RAW frame near the sound's moment (LINE_SEARCH_S)
   where its picture fits best -- only when the picture fits there as well as at its own frames (verify's masked ZNCC
   on SAMPLES frames: at most DELTA below its own, at least MIN_SCORE -- a held drawing scores the same) and the new
   RAW moment is no other piece's. A move that still ends up on another piece's RAW is undone.
4. Otherwise nothing changes (the repeat removal decides, as before).

So a piece only moves when the sound places it elsewhere AND its picture is the same there; the competitor's whole
picture stays, A1 plays the words the competitor plays, and nothing plays twice. Switch: MATCH_CUTS_REPEAT_SOUND=0 off.
New code lives here, not in model.py / common.py (their edit would re-run every cached stage of every video).
"""
from __future__ import annotations

import copy
import math
import os
from fractions import Fraction
from typing import Any

import numpy as np

from .common import log

MIN_WINDOWS = 3               # confident sound windows needed around a piece
FIT_S = 1.5                   # ... within this many competitor seconds of it
FIT_RES = 0.06                # every window within this of the one line (s): one take of the RAW, no cut in the sound
SAME_S = 0.12                 # the piece's picture this close to its sound's moment: the sound repeats too
AV_MAX = 0.25                 # a neighbour whose picture is this close to its sound follows the sound (A/V offset)
AV_RANGE_S = 3.0              # neighbours this far (competitor s) measure the A/V offset
CONTINUE_S = 0.15             # right after the piece before it, when that is this close to the sound's moment
LINE_SEARCH_S = 0.25          # else the RAW frames this far around the sound's moment are tried
DELTA = 0.02                  # the new RAW moment may score this much below the piece's own frames (the same drawing)
MIN_SCORE = 0.6               # ... and must score at least this
SAMPLES = 5
SPEED_TOL = 0.05


def enabled() -> bool:
    return os.environ.get("MATCH_CUTS_REPEAT_SOUND", "").strip() != "0"


def _plain(s: Any) -> bool:
    return (s.type == "raw" and s.raw_in_seconds is not None and not s.time_remap_keys
            and abs(float(s.speed) - 1.0) <= SPEED_TOL)


def _raw_range(s: Any, cf: float) -> tuple[float, float]:
    """The RAW seconds the piece plays."""
    a = float(s.raw_in_seconds)
    return a, a + float(s.speed) * (int(s.comp_out) - int(s.comp_in)) / cf


def repeated(segs: list, cf: float, rf: float) -> list:
    """The plain pieces whose RAW moment (at least one RAW frame of it) another plain piece plays too."""
    plain = [s for s in segs if _plain(s)]
    out = []
    for s in plain:
        a0, a1 = _raw_range(s, cf)
        for t in plain:
            if t is s:
                continue
            b0, b1 = _raw_range(t, cf)
            if min(a1, b1) - max(a0, b0) >= 1.0 / rf - 1e-9:
                out.append(s)
                break
    return out


def sound_line(comp_t: Any, raw_t: Any, ok: Any, t0: float, t1: float) -> tuple[float, float] | None:
    """(a, b): the sound's line RAW = a + b * t around competitor seconds [t0, t1] -- the confident windows within
    FIT_S, all on one straight line (FIT_RES); None when there are too few or they are not one take."""
    ct, rt = np.asarray(comp_t, np.float64), np.asarray(raw_t, np.float64)
    m = np.asarray(ok, bool) & np.isfinite(rt) & (ct >= t0 - FIT_S) & (ct <= t1 + FIT_S)
    if int(m.sum()) < MIN_WINDOWS:
        return None
    x, y = ct[m], rt[m]
    if float(x.max() - x.min()) < 0.4:
        return None
    b, a = np.polyfit(x, y, 1)
    if not 0.5 <= b <= 2.0 or float(np.abs(y - (a + b * x)).max()) > FIT_RES:
        return None
    return float(a), float(b)


def apply(ctx: Any, cl: Any, scores: Any = None) -> tuple[Any, list[dict]]:
    """The cut list with the look-alike repeats put where their sound is (module docstring) and what was done:
    [{'segment', 'from', 'to', 'own', 'line', 'av_offset'}]. Never raises: anything wrong leaves the cut list as it
    was."""
    try:
        if not enabled():
            return cl, []
        return _apply(ctx, cl, scores)
    except Exception as e:  # noqa: BLE001 - the plan stays as it was
        log.warning("repeats by sound: not used (%s: %s)", type(e).__name__, e)
        return cl, []


def _apply(ctx: Any, cl: Any, scores: Any = None) -> tuple[Any, list[dict]]:
    from .sound_takes import _sample, _Scores
    from .verify import seg_raw_frame, seg_sim
    h = getattr(ctx, "hints", None)
    if h is None or not len(getattr(h, "comp_t", ())):
        return cl, []
    cf, rf = float(Fraction(cl.comp_fps)), float(Fraction(cl.raw_fps))
    segs = sorted((copy.deepcopy(s) for s in cl.segments), key=lambda s: (int(s.comp_in), int(s.id)))
    rep = repeated(segs, cf, rf)
    if not rep:
        return cl, []
    ok = h.confident(ctx.cfg.audio_min_conf)
    raw_wh = (float(cl.raw["width"]), float(cl.raw["height"]))
    n_raw = int(getattr(getattr(ctx, "raw_info", None), "nb_frames", 0) or 0) or None
    sc = [None]

    def pic(s: Any, t: float) -> float:
        """The RAW second the piece's picture shows at competitor second t."""
        return float(s.raw_in_seconds) + float(s.speed) * (t - int(s.comp_in) / cf)

    def mid(s: Any) -> float:
        return (int(s.comp_in) + int(s.comp_out)) / 2.0 / cf

    def own_cand(s: Any, k: int):
        j = seg_raw_frame(s, k, Fraction(cl.comp_fps), Fraction(cl.raw_fps), n_raw)
        return (j, seg_sim(s, k, *raw_wh), bool(s.flip_h)) if j is not None else None

    def at_cand(r0: float, k: int, s: Any):
        j = int(math.floor((r0 + (k - int(s.comp_in)) / cf) * rf + 1e-6))
        if j < 0 or (n_raw is not None and j >= n_raw):
            return None
        return (j, seg_sim(s, k, *raw_wh), bool(s.flip_h))

    def medians(s: Any, r0: float) -> tuple[float, float]:
        if sc[0] is None:
            sc[0] = scores if callable(scores) else _Scores(ctx, cl, scores)
        own, lin = [], []
        for k in _sample(s, SAMPLES):
            oc, lc = own_cand(s, k), at_cand(r0, k, s)
            if oc is None or lc is None:
                return float("nan"), float("nan")
            v = sc[0](k, [oc, lc])
            own.append(v[0])
            lin.append(v[1])
        return (float(np.nanmedian(own)) if np.isfinite(own).any() else float("nan"),
                float(np.nanmedian(lin)) if np.isfinite(lin).any() else float("nan"))

    def span(s: Any, r0: float | None = None) -> tuple[float, float]:
        if r0 is None:
            return _raw_range(s, cf)
        return r0, r0 + (int(s.comp_out) - int(s.comp_in)) / cf

    def clash(s: Any, r0: float | None, skip: set[int]) -> bool:
        a0, a1 = span(s, r0)
        for t in segs:
            if t is s or not _plain(t) or int(t.id) in skip:
                continue
            b0, b1 = span(t)
            if min(a1, b1) - max(a0, b0) >= 1.0 / rf - 1e-9:
                return True
        return False

    # 1-2. the pieces whose sound is elsewhere
    lines: dict[int, tuple[float, float]] = {}
    for s in segs:
        if _plain(s):
            ln = sound_line(h.comp_t, h.raw_t, ok, int(s.comp_in) / cf, int(s.comp_out) / cf)
            if ln is not None:
                lines[int(s.id)] = ln
    def judge(s: Any) -> tuple[str, float, float]:
        """('real' -- its picture is where its sound is | 'move' -- elsewhere | '' -- no sound line, the RAW second its
        sound says it starts at, the A/V offset)."""
        ln = lines.get(int(s.id))
        if ln is None:
            return "", 0.0, 0.0
        near = [t for t in segs if t is not s and _plain(t) and int(t.id) in lines
                and abs(mid(t) - mid(s)) <= AV_RANGE_S]
        offs = [pic(t, mid(t)) - (lines[int(t.id)][0] + lines[int(t.id)][1] * mid(t)) for t in near]
        offs = [o for o in offs if abs(o) <= AV_MAX]
        av = float(np.median(offs)) if offs else 0.0
        want = ln[0] + ln[1] * int(s.comp_in) / cf + av
        return ("real" if abs(pic(s, mid(s)) - (ln[0] + ln[1] * mid(s) + av)) <= SAME_S else "move"), want, av

    cands, real = [], []
    for s in rep:
        how, want, av = judge(s)
        if how == "real":
            real.append(int(s.id))
        elif how == "move":
            cands.append((s, want, av))                     # where its sound says it starts
    # a piece standing where a moving piece's sound is, itself off its own sound, moves too (a run of look-alikes)
    for _ in range(5):
        ids = {int(s.id) for s, _w, _a in cands} | set(real)
        spans_to = [(w, w + (int(s.comp_out) - int(s.comp_in)) / cf) for s, w, _a in cands]
        more = []
        for t in segs:
            if not _plain(t) or int(t.id) in ids:
                continue
            b0, b1 = _raw_range(t, cf)
            if not any(min(a1, b1) - max(a0, b0) >= 1.0 / rf - 1e-9 for a0, a1 in spans_to):
                continue
            how, want, av = judge(t)
            if how == "move":
                more.append((t, want, av))
        if not more:
            break
        cands += more
    if not cands:
        if real:
            log.info("repeats by sound: the sound repeats too (a real repeat): %s",
                     ", ".join(f"S{i:02d}" for i in real[:20]))
        return cl, []
    moving = {int(s.id) for s, _w, _a in cands}
    ends = {int(t.comp_out): t for t in segs if _plain(t)}
    starts = {int(t.comp_in): t for t in segs if _plain(t)}
    done: list[dict] = []
    kept: dict[int, str] = {}
    old: dict[int, Any] = {}
    # 3. each where its sound is, in the competitor's order (the piece before it may just have moved)
    for s, want, av in sorted(cands, key=lambda c: int(c[0].comp_in)):
        n_s = (int(s.comp_out) - int(s.comp_in)) / cf
        tries = []
        prev, nxt = ends.get(int(s.comp_in)), starts.get(int(s.comp_out))
        if prev is not None and prev is not s and abs(span(prev)[1] - want) <= CONTINUE_S:
            tries.append((0, span(prev)[1]))
        if nxt is not None and nxt is not s and int(nxt.id) not in moving and \
                abs(float(nxt.raw_in_seconds) - n_s - want) <= CONTINUE_S:
            tries.append((0, float(nxt.raw_in_seconds) - n_s))
        tries += [(1, want + i / rf) for i in range(-int(LINE_SEARCH_S * rf), int(LINE_SEARCH_S * rf) + 1)]
        moved_ids = {int(d["segment"]) for d in done}
        best, seen = None, []
        for rank, r0 in tries:
            if r0 < 0 or clash(s, r0, moving - moved_ids):
                continue
            o, li = medians(s, r0)
            seen.append((li, o, r0))
            if not (math.isfinite(li) and li >= MIN_SCORE and (not math.isfinite(o) or li >= o - DELTA)):
                continue
            key = (-rank, round(li, 3), -abs(r0 - want))
            if best is None or key > best[0]:
                best = (key, r0, o, li)
            if rank == 0:
                break                                       # right after the piece before it: one take
        if best is None:
            top = max(seen, key=lambda x: x[0] if math.isfinite(x[0]) else -9.0) if seen else None
            kept[int(s.id)] = (f"S{int(s.id):02d}: its picture differs at its sound's moment (RAW {want:.2f} s" +
                               (f"; best there {top[0]:.3f} against its own {top[1]:.3f})" if top else
                                "; that moment is another piece's)"))
            continue
        _key, r0, o, li = best
        old[int(s.id)] = copy.deepcopy(s)
        before = float(s.raw_in_seconds)
        s.raw_in_seconds = round(r0, 9)
        s.raw_in_frame = int(math.floor(r0 * rf + 1e-6))
        s.raw_out_frame = int(math.floor((r0 + (int(s.comp_out) - int(s.comp_in) - 1) / cf) * rf + 1e-6))
        s.raw_in_interval = s.raw_in_interval_both = None
        s.speed, s.speed_measured = 1.0, 1.0
        au = dict(s.audio or {})
        if au.get("line"):
            au["line"] = None
        s.audio = au
        s.evidence = []
        s.notes = (s.notes + "; " if s.notes else "") + (f"repeat by sound: the competitor's sound places it at RAW "
                                                         f"{r0:.3f} s, not {before:.3f} s (the same picture there)")
        done.append({"segment": int(s.id), "from": round(before, 4), "to": round(r0, 4),
                     "own": None if not math.isfinite(o) else round(o, 4), "line": round(li, 4),
                     "av_offset": round(av, 3)})
    # a move that still lands on another piece's RAW (one that could not move) is undone
    for _ in range(len(done) + 1):
        bad = [d for d in done if clash(next(t for t in segs if int(t.id) == d["segment"]), None, set())]
        if not bad:
            break
        d = bad[-1]
        i = next(i for i, t in enumerate(segs) if int(t.id) == d["segment"])
        segs[i] = old[d["segment"]]
        done.remove(d)
        kept[d["segment"]] = f"S{d['segment']:02d}: its sound's moment is another piece's"
    if kept:
        log.info("repeats by sound: left to the repeat removal: %s", "; ".join(list(kept.values())[:20]))
    if real:
        log.info("repeats by sound: the sound repeats too (a real repeat): %s",
                 ", ".join(f"S{i:02d}" for i in real[:20]))
    if not done:
        return cl, []
    cl2 = copy.copy(cl)
    cl2.segments = segs
    return cl2, done


def sound_elsewhere(ctx: Any, cl: Any) -> list[tuple[int, int]]:
    """The silence removal checked the same way (run 18, task 3): the competitor frames [comp_in, comp_out) of every
    plain piece whose picture is NOT where the competitor's sound is (the sound's line there, its A/V offset measured
    on the pieces near it: more than SAME_S off) -- A1 plays that piece's own RAW sound, which is not what the
    competitor plays there, so a quiet stretch of A1 there is no pause of the competitor's: it is not cut. [] when the
    sound says nothing (no confident windows, not one take) or the switch is off. Never raises. OFF in the pipeline
    (MATCH_CUTS_PAUSE_SOUND_CHECK=1 on): in the check-all it kept a pause of The Dictator's re-edited interview
    (video37) that you cut -- one caption and one cut fewer reproduced."""
    try:
        if not enabled():
            return []
        h = getattr(ctx, "hints", None)
        if h is None or not len(getattr(h, "comp_t", ())):
            return []
        cf = float(Fraction(cl.comp_fps))
        ok = h.confident(ctx.cfg.audio_min_conf)
        segs = [s for s in sorted(cl.segments, key=lambda s: int(s.comp_in)) if _plain(s)]

        def mid(s: Any) -> float:
            return (int(s.comp_in) + int(s.comp_out)) / 2.0 / cf

        def off(s: Any, ln: tuple[float, float]) -> float:
            return float(s.raw_in_seconds) + float(s.speed) * (mid(s) - int(s.comp_in) / cf) - (ln[0] + ln[1] * mid(s))
        lines = {}
        for s in segs:
            ln = sound_line(h.comp_t, h.raw_t, ok, int(s.comp_in) / cf, int(s.comp_out) / cf)
            if ln is not None:
                lines[int(s.id)] = ln
        out = []
        for s in segs:
            ln = lines.get(int(s.id))
            if ln is None:
                continue
            offs = [off(t, lines[int(t.id)]) for t in segs if t is not s and int(t.id) in lines
                    and abs(mid(t) - mid(s)) <= AV_RANGE_S]
            offs = [o for o in offs if abs(o) <= AV_MAX]
            av = float(np.median(offs)) if offs else 0.0
            if abs(off(s, ln) - av) > SAME_S:
                out.append((int(s.comp_in), int(s.comp_out)))
        return out
    except Exception as e:  # noqa: BLE001 - the silences are planned without it
        log.warning("repeats by sound: the silence check not used (%s: %s)", type(e).__name__, e)
        return []


__all__ = ["enabled", "repeated", "sound_line", "apply", "sound_elsewhere"]
