"""sound_takes.py: look-alike scenes -- the sound decides (day 17, task 7).

The Dictator's interview reel (one scene, a static background): the picture search jumped between look-alike RAW
frames -- 62 cuts, repeats of the same RAW moment, 6-8 frame flashes, 34 of 62 cuts failed -- while the competitor's
sound sits at ONE RAW offset from 17 s to its end (77 sound windows, +99.79 s) and plays the scene straight through.

In the Premiere plan (premiere_plan, before join_takes; nothing of the cached analysis changes):

1. **The sound's takes:** the confident sound windows at the normal speed, grouped by RAW offset
   (placed_by_sound.runs); a run of at least MIN_WINDOWS windows says "here the competitor plays the RAW straight
   through at this offset".
2. **The take's line:** the picture of the pieces that already follow the sound there (anchors: plain RAW pieces
   within ANCHOR_TOL_S of the sound's offset) gives the exact line -- the sound can sit a few frames off the picture
   (an A/V offset). Without an anchor the line is the sound's offset +- LINE_SEARCH_S, the one the picture fits best.
3. **The sound decides between look-alikes:** each piece of the run that is NOT on the line (another RAW moment, a
   repeat, a flash, an uncertain stretch) is scored on a few of its frames against its own RAW frames and the line's
   (verify.ProxyScorer: the same masked ZNCC, each with the piece's own framing). When the line scores almost the same
   (its median at most DELTA below, and at least MIN_SCORE) the piece is put on the line: one continuous take, never
   the same RAW moment twice. A piece the line clearly doesn't fit (a real cut to another shot) stays.
4. **Straight through:** the take then reaches on past the run's ends, piece by piece, as long as the picture says
   the line fits as well there (the sound may be music or silence there).

5. **One competitor shot, one take:** inside each of the competitor's own shots (PySceneDetect's changes), pieces
   the cut list doubts are put on ONE line -- a piece's own RAW moment played on at 100 % or at 80 % (a 23.976 fps RAW
   shown frame for frame at 29.97) -- when every piece's picture fits it as well as its own frames; the best picture
   wins, the line nearest the sound breaks a tie.

join_takes then plays the pieces on one line as one take (one clip per framing). Switch: MATCH_CUTS_SOUND_TAKES=1 on,
=0 off (DEFAULT_ON below: ON only when the check-all says nothing got worse).

Measured on the Dictator reel (day 17): the sound's take is found (+99.97 s, 79 windows), but the competitor is a
re-edit there -- Larry King's close-up over the dialogue the film plays on its wide shot -- so the film's picture at
the sound's moment is another shot (ZNCC ~0.1 against the pieces' ~0.85): steps 1-4 rightly change nothing. Step 5
made one of its 18 shots one take (27 -> 26 clips); in the others no single line fits every piece (cuts between
near-identical angles that PySceneDetect does not see).
"""
from __future__ import annotations

import copy
import math
import os
from fractions import Fraction
from typing import Any

import numpy as np

from .common import log

DEFAULT_ON = False
MIN_WINDOWS = 8               # a run of at least this many confident sound windows (2 s at the 0.25 s hop)
ANCHOR_TOL_S = 0.5            # a piece this close to the sound's offset already follows the sound's take
LINE_SEARCH_S = 0.25          # no anchor: the line searched this far around the sound's offset (RAW frames)
ON_LINE_FRAMES = 1.5          # a piece within this many RAW frames of the line is on it already
DELTA = 0.03                  # the line may score this much below the piece's own frames (look-alikes: within noise)
MIN_SCORE = 0.5               # ... and must score at least this (the line's picture is really the scene)
SAMPLES = 5                   # frames scored per piece
SPEED_TOL = 0.05
LOOKALIKE_TAKES = True        # step 5 (one shot, one take) -- part of the switch
SLIVER = 30                   # a piece this short (competitor frames) ...
DOUBT_CONF = 0.5              # ... or less sure than this, or uncertain: one the cut list doubts
TAKE_SPEEDS = (1.0, 0.8)      # step 5's take speeds: the RAW's own, and 23.976 shown frame for frame at 29.97
MAX_GROUP = 40                # a competitor shot of more pieces than this is left as it is (no shot changes found?)


def shot_changes(ctx: Any) -> list[int]:
    """The competitor's own shot changes (competitor frames: segment.scenedetect_changes, cached); ``ctx.comp_shots``
    when given (the tests); [] when they can't be found (then the whole competitor is one 'shot' -- nothing joined
    across a cut the picture search found, as every piece must fit the line)."""
    given = getattr(ctx, "comp_shots", None)
    if given is not None:
        return [int(c) for c in given]
    try:
        from . import segment
        path = getattr(getattr(ctx, "comp_proxy", None), "path", "") or getattr(getattr(ctx, "comp_info", None),
                                                                                 "path", "")
        return [int(c) for c in segment.scenedetect_changes(str(path), ctx.cfg)] if path else []
    except Exception as e:  # noqa: BLE001
        log.warning("sound takes: the competitor's shot changes not found (%s)", e)
        return []


def enabled(cfg: Any = None) -> bool:
    v = os.environ.get("MATCH_CUTS_SOUND_TAKES", "").strip()
    if v in ("0", "1"):
        return v == "1"
    return bool(getattr(cfg, "sound_takes", DEFAULT_ON))


def _plain(s: Any) -> bool:
    return (s.type == "raw" and s.raw_in_seconds is not None and not s.time_remap_keys
            and abs(float(s.speed) - 1.0) <= SPEED_TOL)


def _doubted(s: Any) -> bool:
    return (s.type == "uncertain" or bool(getattr(s, "uncertain", False)) or int(s.length) <= SLIVER
            or float(s.confidence or 0.0) < DOUBT_CONF)


def _offset(s: Any, cf: float) -> float:
    return float(s.raw_in_seconds) - int(s.comp_in) / cf


def _sample(s: Any, n: int = SAMPLES) -> list[int]:
    a, b = int(s.comp_in), int(s.comp_out)
    if b - a <= n:
        return list(range(a, b))
    return sorted({int(round(a + (b - 1 - a) * i / (n - 1))) for i in range(n)})


class _Scores:
    """ZNCC of competitor frames against (RAW frame, framing) candidates -- verify's ProxyScorer on the run's own
    proxies. ``fake``: the tests' scorer (k, [cands]) -> scores."""

    def __init__(self, ctx: Any, cl: Any, fake: Any = None):
        self.fake = fake
        self.scorer = None
        if fake is None:
            from . import verify
            segs = list(cl.segments)
            n = int(ctx.comp_proxy.n)
            box_fn = verify.frame_box_fn(segs, cl.layout, verify._box(ctx), verify._full_canvas(cl), n)
            allowed = verify._allowed_fn(ctx, box_fn)
            self.scorer = verify.ProxyScorer(ctx.comp_proxy, ctx.raw_proxy, verify._box(ctx), allowed,
                                             float(cl.raw["width"]), ctx.cfg, box_fn=box_fn)

    def __call__(self, k: int, cands: list) -> np.ndarray:
        if self.fake is not None:
            return np.asarray(self.fake(k, cands), dtype=np.float64)
        return np.asarray(self.scorer.score(int(k), cands), dtype=np.float64)


def apply(ctx: Any, cl: Any, scores: Any = None) -> tuple[Any, list[dict]]:
    """The cut list with the sound's takes (module docstring) and what was done: [{'segment', 'take', 'how', 'own',
    'line'}]. Never raises: anything wrong leaves the cut list as it was."""
    try:
        if not enabled(getattr(ctx, "cfg", None)):
            return cl, []
        return _apply(ctx, cl, scores)
    except Exception as e:  # noqa: BLE001 - the plan stays as it was
        log.warning("sound takes: not used (%s: %s)", type(e).__name__, e)
        return cl, []


def _apply(ctx: Any, cl: Any, scores: Any = None) -> tuple[Any, list[dict]]:
    from . import placed_by_sound as P
    from .verify import seg_raw_frame, seg_sim
    h = getattr(ctx, "hints", None)
    if h is None or not len(getattr(h, "comp_t", ())):
        return cl, []
    cfg = ctx.cfg
    cf, rf = float(Fraction(cl.comp_fps)), float(Fraction(cl.raw_fps))
    raw_wh = (float(cl.raw["width"]), float(cl.raw["height"]))
    n_raw = int(getattr(getattr(ctx, "raw_info", None), "nb_frames", 0) or 0) or None
    runs = [r for r in P.runs(h.comp_t, h.raw_t, h.confident(cfg.audio_min_conf), h.speed, float(h.window))
            if int(r["windows"]) >= MIN_WINDOWS]
    if not runs:
        return cl, []
    sc = scores if callable(scores) else _Scores(ctx, cl, scores)
    segs = sorted((copy.deepcopy(s) for s in cl.segments), key=lambda s: (int(s.comp_in), int(s.id)))
    done: list[dict] = []
    taken: set[int] = set()

    def own_cand(s: Any, k: int, frame_from: Any = None):
        j = seg_raw_frame(s, k, Fraction(cl.comp_fps), Fraction(cl.raw_fps), n_raw)
        return (j, seg_sim(frame_from or s, k, *raw_wh), bool((frame_from or s).flip_h)) if j is not None else None

    V = [1.0]                                    # the take's speed (step 5 tries 100 % and the competitor's 80 %)

    def line_cand(d: float, k: int, framing: Any):
        j = int(math.floor((V[0] * k / cf + d) * rf + 1e-6))
        if j < 0 or (n_raw is not None and j >= n_raw):
            return None
        return (j, seg_sim(framing, k, *raw_wh), bool(framing.flip_h))

    def framed_near(s: Any, pool: list) -> Any:
        """The piece whose framing ``s`` gets: its own, else the nearest framed piece of ``pool``."""
        if s.type == "raw" and (s.transform or s.transform_keys):
            return s
        return min(pool, key=lambda x: min(abs(int(x.comp_in) - int(s.comp_out)),
                                           abs(int(s.comp_in) - int(x.comp_out))))

    def fits(s: Any, d: float, src: Any) -> tuple[bool, float, float]:
        """(the line d fits piece s as well as its own frames, own median, line median); ``src``: the framing of an
        uncertain piece."""
        fr = s if s.type == "raw" and (s.transform or s.transform_keys) else src
        own, lin = [], []
        for k in _sample(s):
            oc = own_cand(s, k) if s.type == "raw" else None
            lc = line_cand(d, k, fr)
            if lc is None:
                return False, float("nan"), float("nan")
            if oc is None:                       # an uncertain piece: its best evidence at that frame
                ev = next((e for e in (s.evidence or []) if int(e.get("comp_frame", -1)) == k and
                           int(e.get("raw", -1)) >= 0), None)
                if ev is not None and ev.get("sim"):
                    from .geometry import Sim
                    oc = (int(ev["raw"]), Sim.from_dict(ev["sim"]), bool(ev.get("flip")))
            v = sc(k, [oc, lc]) if oc is not None else np.array([np.nan, sc(k, [lc])[0]])
            own.append(v[0])
            lin.append(v[1])
        o = float(np.nanmedian(own)) if np.isfinite(own).any() else float("nan")
        li = float(np.nanmedian(lin)) if np.isfinite(lin).any() else float("nan")
        if not math.isfinite(li) or li < MIN_SCORE:
            return False, o, li
        return (not math.isfinite(o)) or li >= o - DELTA, o, li

    def on_line(s: Any, d: float) -> bool:
        return (s.type == "raw" and s.raw_in_seconds is not None and not s.time_remap_keys
                and abs(float(s.speed) - V[0]) <= SPEED_TOL
                and abs((float(s.raw_in_seconds) - V[0] * int(s.comp_in) / cf - d) * rf) <= ON_LINE_FRAMES)

    def put(s: Any, d: float, src: Any, o: float, li: float, how: str) -> None:
        fr = s if s.type == "raw" and (s.transform or s.transform_keys) else src
        t = s
        t.type, t.uncertain, t.label = "raw", False, ""
        t.raw_in_seconds = round(d + V[0] * int(t.comp_in) / cf, 9)
        t.raw_in_frame = int(math.floor(float(t.raw_in_seconds) * rf + 1e-6))
        t.raw_out_frame = int(math.floor((d + V[0] * (int(t.comp_out) - 1) / cf) * rf + 1e-6))
        t.raw_in_interval = t.raw_in_interval_both = None
        t.speed, t.speed_measured = V[0], V[0]
        t.time_remap_keys, t.time_mode, t.retime = [], "stretch", "none"
        if fr is not t:
            last = sorted(fr.transform_keys or [], key=lambda x: float(x["comp_frame"]))
            t.transform = ({k: v for k, v in last[-1].items() if k != "comp_frame"} if last else
                           copy.deepcopy(fr.transform))
            t.transform_keys, t.box, t.flip_h = [], copy.deepcopy(fr.box), fr.flip_h
        au = dict(t.audio or {})
        if au.get("line"):
            au["line"] = None
        t.audio = au
        t.evidence = []
        t.notes = (t.notes + "; " if t.notes else "") + f"sound take: put on the take's line ({how})"
        taken.add(int(t.id))
        done.append({"segment": int(t.id), "take": round(d, 4), "speed": V[0], "how": how,
                     "own": None if not math.isfinite(o) else round(o, 4), "line": round(li, 4)})

    def grow(i0: int, d: float, src: Any, how: str, only_doubted: bool) -> None:
        """The take on line d reaches from segs[i0] both ways, piece by piece, while the line's picture fits as well
        (``only_doubted``: and the piece is one the cut list doubts -- a sliver, low confidence, uncertain)."""
        for step in (-1, 1):
            i = i0 + step
            while 0 <= i < len(segs):
                s = segs[i]
                if s.type not in ("raw", "uncertain") or int(s.id) in taken:
                    break
                if not on_line(s, d):
                    if only_doubted and not _doubted(s):
                        break
                    ok, o, li = fits(s, d, src)
                    if not ok:
                        break
                    put(s, d, src, o, li, how)
                i += step

    # 1-3. the sound's takes
    for r in runs:
        a = max(0, int(math.floor(float(r["from"]) * cf)))
        b = int(math.ceil(float(r["to"]) * cf))
        inside = [s for s in segs if int(s.comp_in) >= a - 1 and int(s.comp_out) <= b + 1
                  and s.type in ("raw", "uncertain")]
        if not inside:
            continue
        anchors = [s for s in inside if _plain(s) and abs(_offset(s, cf) - float(r["offset"])) <= ANCHOR_TOL_S
                   and (s.transform or s.transform_keys)]
        framed = anchors or [s for s in inside if s.type == "raw" and (s.transform or s.transform_keys)]
        if not framed:
            continue
        if anchors:                                  # the picture's own line of the sound's take (longest pieces)
            w = np.array([s.length for s in anchors], np.float64)
            offs = np.array([_offset(s, cf) for s in anchors])
            order = np.argsort(offs)
            cum = np.cumsum(w[order])
            d = float(offs[order][int(np.searchsorted(cum, cum[-1] / 2.0))])
            how_line = f"the picture of {len(anchors)} piece(s) on the sound's take"
        else:                                        # the sound's offset, the picture's best phase around it
            best = max(framed, key=lambda s: s.length)
            ks = _sample(best, 9)
            cands = [float(r["offset"]) + i / rf for i in range(-int(LINE_SEARCH_S * rf), int(LINE_SEARCH_S * rf) + 1)]
            tot = []
            for d0 in cands:
                v = [sc(k, [line_cand(d0, k, best)])[0] for k in ks]
                tot.append(np.nanmedian(v) if np.isfinite(v).any() else -np.inf)
            d = cands[int(np.argmax(tot))]
            how_line = "the sound's offset, the picture's best phase"
        kept = []
        for s in list(inside):
            if on_line(s, d) or int(s.id) in taken:
                continue
            ok, o, li = fits(s, d, framed_near(s, framed))
            if ok:
                put(s, d, framed_near(s, framed), o, li, "the sound decides")
            else:
                kept.append(f"S{int(s.id):02d} own {o:.3f} line {li:.3f}")
        if kept:
            log.info("sound takes: kept off the sound's take (the line's picture fits worse -- the competitor shows "
                     "another shot there): %s", "; ".join(kept[:30]))
        on = [i for i, s in enumerate(segs) if s in inside and on_line(s, d)]
        if on:                                       # 4. straight through, past the sound's ends
            grow(min(on), d, framed_near(segs[min(on)], framed), "straight through", False)
            grow(max(on), d, framed_near(segs[max(on)], framed), "straight through", False)
        log.info("sound takes: the sound's take from %.2f to %.2f s (%d windows; line %s, RAW %+.3f s)",
                 float(r["from"]), float(r["to"]), int(r["windows"]), how_line, d)
    # 5. one shot, one take: pieces the cut list doubts that jump between look-alike RAW moments around a piece it
    #    is sure of go on that piece's line when the line's picture fits them as well (a still interview shot)
    if LOOKALIKE_TAKES:
        n = max([int(s.comp_out) for s in segs] or [0])
        cuts = shot_changes(ctx)
        bounds = [0] + sorted(c for c in cuts if 0 < c < n) + [n]
        for c0, c1 in zip(bounds, bounds[1:]):
            group = [s for s in segs if int(s.comp_in) >= c0 - 1 and int(s.comp_out) <= c1 + 1
                     and s.type in ("raw", "uncertain") and int(s.id) not in taken]
            framed = [s for s in group if s.type == "raw" and s.raw_in_seconds is not None
                      and (s.transform or s.transform_keys)]
            if not 2 <= len(group) <= MAX_GROUP or not framed or 2 * sum(_doubted(s) for s in group) < len(group):
                continue
            # the candidate lines: each piece's own RAW moment played on at 100 % or at the competitor's 80 % (a
            # 23.976 fps RAW shown frame for frame at 29.97), the sound's nearest breaking ties
            cand = {(v, round((float(s.raw_in_seconds) - v * int(s.comp_in) / cf) * rf) / rf): s
                    for v in TAKE_SPEEDS for s in sorted(framed, key=lambda s: s.length)}
            sound = [float(r["offset"]) for r in runs if float(r["from"]) * cf <= c1 and float(r["to"]) * cf >= c0]
            best, why = None, []
            for (v, d), src in cand.items():
                V[0] = v
                res = [fits(s, d, framed_near(s, framed)) for s in group]
                if not all(ok for ok, _o, _li in res):
                    bad = [(s, o, li) for s, (ok, o, li) in zip(group, res) if not ok]
                    why.append(f"RAW {d:+.2f} s: {len(bad)} misfit(s), e.g. S{int(bad[0][0].id):02d} own "
                               f"{bad[0][1]:.3f} line {bad[0][2]:.3f}")
                    continue
                score = float(np.median([li for _ok, _o, li in res]))
                near = min((abs(d - x) for x in sound), default=0.0)
                key = (round(score, 3), -near, int(src.length))     # the best picture; then nearest the sound
                if best is None or key > best[0]:
                    best = (key, d, res, v)
            V[0] = 1.0
            if best is None:
                log.info("sound takes: competitor shot %d-%d (%d pieces): no one take fits it -- %s", c0, c1,
                         len(group), "; ".join(why[:4]))
                continue
            _key, d, res, V[0] = best
            for s, (_ok, o, li) in zip(group, res):
                if not on_line(s, d):
                    put(s, d, framed_near(s, framed), o, li, "one shot, one take")
            log.info("sound takes: competitor shot %d-%d: %d piece(s) made one take (RAW %+.3f s at %.0f %%, line "
                     "%.3f)", c0, c1, len(group), d, 100 * V[0], float(np.median([li for _ok, _o, li in res])))
        V[0] = 1.0
    if not done:
        return cl, []
    cl2 = copy.copy(cl)
    cl2.segments = segs
    return cl2, done


__all__ = ["DEFAULT_ON", "enabled", "apply"]
