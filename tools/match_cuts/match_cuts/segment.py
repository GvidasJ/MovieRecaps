"""Segmentation, transitions and per-segment framing (prompt Stage 5.4-5.5; DESIGN.md §5 segment.py).

Input: the per-frame map m(k) (``FrameMap``) from refine.py. Output: ``list[Segment]`` in competitor
order (ids 1..N), placeholders included, with speed / speed_range / speed_measured / unsnapped, flip,
transform (+ keys), transitions, remap keys, confidence, ambiguous / timing-tie / low-margin frames and
notes filled in. raw_in_seconds & co. are filled with the same phase solve the pipeline runs afterwards
(``segment_constraints`` gives the exact constraints to use).

Algorithm overview
  1. Runs. The timeline is split into runs of MATCH / NONE / UNIFORM frames; MATCH runs are split further
     at flip changes and at transform steps (punch-ins: consecutive-frame steps, or a fast change over at
     most 2*framing_sample_step frames localised by scoring every frame under both transforms).
  2. Cuts = DP over candidate cut positions of each MATCH sub-run (never greedy prefixes). Candidates:
     boundaries of greedy free-speed runs (+-1), phase breaks at the dominant speed / 1.0 inside runs
     within 15 % of that speed and at the nearest snap speed, RAW increment anomalies, track changes, audio
     lag steps; where soft ranges are wider than refine's measured (argmax) range, also the phase breaks and
     free runs of the MEASURED frames. cost(segment) = 0 if the dominant (or an audio-confirmed) speed is
     feasible on the soft ranges, lambda_one for 1.0 when it is not dominant, lambda_nondominant for any
     other snap value (incl. freeze 0 and reverse -1), lambda_unsnapped for the unsnapped robust slope of
     the measured frames (tried when nothing snapped is feasible or every snapped explanation costs more),
     inf when infeasible -- after tolerating isolated interior single-frame violations whose model frame
     scores within 5*delta_k of their best and that no competing hypothesis explains (same track, or a near
     miss of <= 2 frames), + lambda_drop each, + lambda_tie when only a timing tie makes it feasible
     + the DATA TERM: every frame the line shows inside its soft range but outside refine's measured range
     costs lambda_data * clip(deficit / delta_k, 0.25, 1) at the best phase (soft ranges are tolerances,
     not free choices: a 2-frame jump cut on slow footage is a cut, a 1.03x shot is not snapped to 1.0);
     + lambda_cut per cut; equal costs -> fewer segments. A 1-frame-skip jump cut therefore stays a cut
     between two 1.0 segments instead of becoming a fake 1.02-1.05x segment.
  3. Clean-up: 1-2 frame segments explained by a neighbour's model are merged (matching errors), genuine
     ones are kept (verified flash cuts); adjacent compatible segments are merged when one model is
     cheaper; chains of >= 2 one-frame skips that one line (any speed) reproduces at least as well are one
     retimed segment; adjacent segments at the same speed are merged when one line explains both with runs
     of <= 2 low-margin near misses (a sub-frame phase cut bought only by the data term needs
     lambda_phase_cut more evidence); short NONE runs inside one continuous model are absorbed when the
     model frame (or a blend of two neighbouring RAW frames) matches them; phase_solve.snap_speed re-snaps
     non-dominant speeds with the other segments' speeds as preferred values, only inside the measured
     frames' exact range or within speed_snap_tol of their robust slope (prompt 5.4). Runs never cross a
     layout period boundary (D1: fullscreen / split / PiP periods; Segment.box / region set per period).
  4. Speed-only cuts (no RAW discontinuity): cut moved to the intersection of both lines,
     cut_ambiguity=[a, b] = all positions both models explain.
  5. Transitions: fit_blend over A in {Â-1..Â+1} x B in {B̂-1..B̂+1} (vectorised covariance form, equal to
     scoring.fit_blend) around every cut / short gap; blend frames by the relative test; alpha_B(k) from the
     gain-independent fit (scoring.blend_alpha_cov) = (k-O)/D least squares -> (O, D); B.comp_in = O,
     A.comp_out = O + D (DESIGN §3); the chosen A/B frames
     become phase constraints. Dips (fades to/from a UNIFORM run), flashes (UNIFORM runs of 1-2 frames);
     NONE -> NOT-IN-RAW placeholders.
  6. Criterion-2 check on every hard cut (A's last frame scores higher under A's model than under B's
     and vice versa); the cut is moved otherwise (never at a layout period boundary, never while both
     models show the same frame on both sides). Afterwards neighbours whose models agree at the boundary
     (same speed, RAW frame and framing on both sides: no discontinuity of m(k)) are merged -- no phantom
     cuts.
  7. Retiming (frame blending), freeze / reverse / ramps (remap keys), framing (constant or RDP keys,
     rotation only above rotation_min_deg, full-affine check when the similarity fits poorly), phase solve
     (the phase that reproduces the most measured frames inside the soft intersection, not its centre;
     raw_in_interval / ae_margin_ms refer to that sub-interval; speed_range = the speeds reproducing what
     the segment claims), FrameMap write-back (m(k) := model frame where consistent, BLEND, tie,
     low_margin, soft ranges), Segment.box / region from the layout period.
  8. PySceneDetect cross-check (every scene change must coincide with a cut / transition, disagreements
     explained), debug/mapping.png and debug/scores.png.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from . import phase_solve as ps
from .common import DecisionLog, log, null_dlog, seed_everything, timecode
from .geometry import Sim, interpolate_keys, rdp
from .model import FrameMap, Segment, Status

__all__ = ["build_segments", "segment_constraints", "scenedetect_changes", "plot_mapping", "plot_scores"]

_TIE = ps.TIE_SLACK
_TAU = ps.TAU
_AUTO = object()     # sentinel: "compute it here"
_PRUNED = object()   # fit(): feasible, but no explanation can cost <= bound


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    return getattr(cfg, name, default)


# =================================================================================================
# per-frame arrays
# =================================================================================================

class _Frames:
    """Working copy of the FrameMap columns segment.py needs (the FrameMap itself is only written at
    the end, see ``_Builder.write_back``)."""

    def __init__(self, fm: FrameMap, cfg: Any):
        n = fm.n
        self.n = n
        st = np.asarray(fm.status).astype(np.int8).copy()
        raw = np.asarray(fm.raw).astype(np.int64).copy()
        st[st == Status.UNKNOWN] = Status.NONE
        # a previous build_segments run may have marked transition frames BLEND: treat as matched
        st[(st == Status.BLEND) & (raw >= 0)] = Status.MATCH
        st[(st == Status.BLEND) & (raw < 0)] = Status.NONE
        st[(st == Status.MATCH) & (raw < 0)] = Status.NONE
        self.status = st
        self.raw = raw
        rlo = np.asarray(fm.raw_lo).astype(np.int64)
        rhi = np.asarray(fm.raw_hi).astype(np.int64)
        rlo = np.where(rlo >= 0, rlo, raw)
        rhi = np.where(rhi >= 0, rhi, raw)
        rlo = np.minimum(rlo, raw)
        rhi = np.maximum(rhi, raw)
        slo = np.asarray(fm.soft_lo).astype(np.int64)
        shi = np.asarray(fm.soft_hi).astype(np.int64)
        self.lo = np.minimum(np.where(slo >= 0, slo, rlo), rlo)
        self.hi = np.maximum(np.where(shi >= 0, shi, rhi), rhi)
        self.raw_lo, self.raw_hi = rlo, rhi
        self.flip = np.asarray(fm.flip).astype(bool).copy()
        self.track = np.asarray(fm.track).astype(np.int64).copy()
        self.score = np.asarray(fm.score).astype(np.float64)
        self.margin = np.asarray(fm.margin).astype(np.float64)
        self.conf = np.asarray(fm.conf).astype(np.float64)
        self.mean = np.asarray(fm.mean).astype(np.float64)
        self.std = np.asarray(fm.std).astype(np.float64)
        self.s = np.asarray(fm.s).astype(np.float64)
        self.theta = np.asarray(fm.theta).astype(np.float64)
        self.tx = np.asarray(fm.tx).astype(np.float64)
        self.ty = np.asarray(fm.ty).astype(np.float64)
        self.low_margin = np.asarray(fm.low_margin).astype(bool).copy()
        self.cand = np.asarray(fm.cand).astype(np.float64)
        self.cand_j0 = np.asarray(fm.cand_j0).astype(np.int64)
        if self.cand.ndim != 2 or self.cand.shape[0] != n:
            self.cand = np.full((n, 1), np.nan)
        fin = np.isfinite(self.cand)
        # best candidate score per frame (NaN without a candidate vector): vectorised deficits for the DP data term
        self.rowmax = np.where(fin.any(axis=1), np.where(fin, self.cand, -np.inf).max(axis=1), np.nan) \
            if self.cand.shape[1] else np.full(n, np.nan)
        self.touched: dict[int, str] = {}           # k -> why its constraint was changed
        # delta_k per track (DESIGN §3): score noise = scoring.noise_delta of the track's best scores
        from .scoring import noise_delta
        dmin = float(_cfg(cfg, "soft_delta_min", 0.001))
        dmax = float(_cfg(cfg, "soft_delta_max", 0.01))
        self.delta = np.full(n, dmin)
        s_ok = (st == Status.MATCH) & np.isfinite(self.score)
        for tr in sorted(set(self.track[st == Status.MATCH].tolist())):
            sel = s_ok & (self.track == tr)
            if sel.sum() >= 3:
                self.delta[(self.track == tr)] = noise_delta(self.score[sel], dmin, dmax)
        # intrinsic (relaxed) droppability: the frame's best is within 5 delta of something else
        with np.errstate(invalid="ignore"):
            rel = np.isfinite(self.margin) & (self.margin <= 5.0 * self.delta)
        nan_m = ~np.isfinite(self.margin)
        if nan_m.any():
            for k in np.nonzero(nan_m & (st == Status.MATCH))[0]:
                rel[k] = self._cand_any_close(int(k))
        self.relaxed = rel & (st == Status.MATCH)

    # -- candidate score vector helpers ------------------------------------------------------------
    def cand_score(self, k: int, j: int) -> float:
        j0 = int(self.cand_j0[k])
        if j0 < 0:
            return float("nan")
        i = int(j) - j0
        if 0 <= i < self.cand.shape[1]:
            return float(self.cand[k, i])
        return float("nan")

    def deficit(self, k: int, j: int) -> float:
        """max S_k - S_k(j) (NaN when j was not evaluated)."""
        s = self.cand_score(k, j)
        if not math.isfinite(s):
            return float("nan")
        row = self.cand[k]
        if not np.isfinite(row).any():
            return float("nan")
        return float(np.nanmax(row) - s)

    def deficits(self, ks: np.ndarray, js: np.ndarray) -> np.ndarray:
        """Vectorised ``deficit``: max S_k - S_k(j) per (k, j) pair (NaN where j was not evaluated)."""
        ks = np.asarray(ks, dtype=np.int64)
        js = np.asarray(js, dtype=np.int64)
        out = np.full(ks.size, np.nan)
        if ks.size == 0:
            return out
        j0 = self.cand_j0[ks]
        i = js - j0
        ok = (j0 >= 0) & (i >= 0) & (i < self.cand.shape[1])
        if ok.any():
            out[ok] = self.rowmax[ks[ok]] - self.cand[ks[ok], i[ok]]
        return out

    def pristine(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Preferred (measured) RAW range per constraint frame: refine's argmax range [raw_lo, raw_hi]
        intersected with the constraint [lo, hi]; the constraint itself where they do not intersect (e.g.
        the A/B frames chosen on crossfade blend frames, or a criterion-2 move)."""
        rlo, rhi = self.raw_lo[ks], self.raw_hi[ks]
        plo, phi = np.maximum(lo, rlo), np.minimum(hi, rhi)
        bad = plo > phi
        return np.where(bad, lo, plo).astype(np.int64), np.where(bad, hi, phi).astype(np.int64)

    def _cand_any_close(self, k: int) -> bool:
        row = self.cand[k]
        if int(self.cand_j0[k]) < 0 or not np.isfinite(row).any():
            return False
        best = np.nanmax(row)
        js = int(self.cand_j0[k]) + np.arange(row.size)
        outside = (js < self.lo[k]) | (js > self.hi[k])
        close = np.isfinite(row) & (row >= best - 5.0 * self.delta[k])
        return bool((outside & close).any())

    def sim(self, k: int) -> Sim | None:
        if not (0 <= k < self.n) or not math.isfinite(self.s[k]) or self.s[k] <= 0:
            return None
        th = self.theta[k] if math.isfinite(self.theta[k]) else 0.0
        return Sim(float(self.s[k]), float(th), float(self.tx[k]), float(self.ty[k]))


# =================================================================================================
# phase model of one segment
# =================================================================================================

@dataclass
class _Model:
    comp_in: int
    v: float
    kind: str                   # dominant | audio | one | snap | unsnapped | fixed
    cost: float
    unsnapped: bool
    vrange: tuple[float, float] | None
    v_ols: float
    drops: list[int]
    _sol: dict | None           # phase solution (``sol``; computed lazily when ``lazy`` is set)
    track: int
    flip: bool
    n_frames: int
    data: float = 0.0           # DP data term: frames shown off refine's measured argmax range (weighted)
    lazy: Any = None            # () -> sol: the DP evaluates thousands of ranges, only the chosen ones need it

    @property
    def sol(self) -> dict:
        if self._sol is None and self.lazy is not None:
            self._sol = self.lazy()
            self.lazy = None
        return self._sol if self._sol is not None else {}

    @sol.setter
    def sol(self, value: dict) -> None:
        self._sol = value
        self.lazy = None

    @property
    def raw_in(self) -> float:
        return float(self.sol["raw_in"])

    def pos(self, k: float) -> float:
        """Continuous RAW frame position at comp frame k (frames)."""
        return float(self.sol["raw_in_frames"]) + float(self.sol["u"]) * (k - self.comp_in)


class _Solver:
    """Cost model of the DP (DESIGN §5 segment.py) on top of phase_solve."""

    def __init__(self, F: _Frames, cfg: Any, comp_fps: Fraction, raw_fps: Fraction, hints: Any):
        self.F = F
        self.cfg = cfg
        self.cf = Fraction(comp_fps)
        self.rf = Fraction(raw_fps)
        self.ratio = float(self.rf / self.cf)
        self.hints = hints
        self.dominant = 1.0
        self.l_cut = float(_cfg(cfg, "lambda_cut", 1.0))
        self.l_uns = float(_cfg(cfg, "lambda_unsnapped", 3.0))
        self.l_nd = float(_cfg(cfg, "lambda_nondominant", 1.5))
        self.l_one = float(_cfg(cfg, "lambda_one", 0.25))
        self.l_drop = float(_cfg(cfg, "lambda_drop", 0.4))
        self.l_tie = float(_cfg(cfg, "lambda_tie", 0.05))
        # data term (time-math F2): a frame whose model frame lies inside its soft range but outside refine's
        # measured argmax range costs l_data * clip(deficit / delta_k, w_min, 1) (l_data without a candidate
        # vector) -- soft ranges are tolerances, not free choices, so a constant-speed line that contradicts a
        # run of measured frames loses to a cut / another speed
        self.l_data = float(_cfg(cfg, "lambda_data", 0.5))
        self.w_min = float(_cfg(cfg, "data_weight_min", 0.25))
        self.l_phase = float(_cfg(cfg, "lambda_phase_cut", 1.0))
        self.snaps = [float(s) for s in _cfg(cfg, "speed_snap_values", (1.0,))]
        self._cache: dict[tuple, Any] = {}
        self._relax_cache: dict[tuple, bool] = {}
        self._cand_cache: dict[tuple, list] = {}

    # -- audio speed evidence -------------------------------------------------------------------
    def audio_speed(self, a: int, b: int) -> float | None:
        h = self.hints
        if h is None or len(getattr(h, "comp_t", [])) == 0:
            return None
        conf = h.confident(float(_cfg(self.cfg, "audio_min_conf", 1.3)))
        w = float(getattr(h, "window", 1.0))
        t0 = a / float(self.cf) + w / 2
        t1 = b / float(self.cf) - w / 2
        sel = conf & (h.comp_t >= t0) & (h.comp_t <= t1) & np.isfinite(h.speed)
        if sel.sum() < 2:
            return None
        snapped = [self._nearest_snap(float(s)) for s in h.speed[sel]]
        vals = [s for s in snapped if s is not None]
        if len(vals) < 2:
            return None
        best = max(set(vals), key=lambda v: (vals.count(v), -abs(v - 1.0)))
        if vals.count(best) >= 0.75 * len(snapped):
            return best
        return None

    def _nearest_snap(self, s: float) -> float | None:
        c = min(self.snaps, key=lambda v: abs(v - s))
        return c if abs(c - s) <= 0.01 * abs(c) else None

    def class_cost(self, kind: str) -> float:
        return {"dominant": 0.0, "audio": 0.0, "fixed": 0.0, "one": self.l_one, "snap": self.l_nd,
                "unsnapped": self.l_uns}.get(kind, 0.0)

    # -- speed candidates -------------------------------------------------------------------------
    def speed_cands(self, a: int, b: int) -> list[tuple[float, float, str, int]]:
        """(speed, base cost, kind, rank) sorted by (cost, rank): dominant, audio-confirmed, 1.0, other
        snap values, freeze (0) and reverse (-1)."""
        key = (self.dominant, self.audio_speed(a, b))
        if key in self._cand_cache:
            return self._cand_cache[key]
        out: list[tuple[float, float, str, int]] = [(self.dominant, 0.0, "dominant", 0)]

        def add(v: float, c: float, kind: str, rank: int) -> None:
            if not any(abs(v - o[0]) <= 1e-9 * max(1.0, abs(v)) for o in out):
                out.append((v, c, kind, rank))

        if key[1] is not None:
            add(key[1], 0.0, "audio", 1)
        add(1.0, self.l_one, "one", 2)
        for s in self.snaps + [0.0, -1.0]:
            add(s, self.l_nd, "snap", 3)
        out.sort(key=lambda c: (c[1], c[3]))
        self._cand_cache[key] = out
        return out

    # -- 1-D feasibility at a fixed speed, with isolated-violation tolerance ------------------------
    def penalties(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(i, j, w) data-term pairs of a constraint set (phase_solve.prefer_penalties with deficit weights),
        or None when every frame's soft range equals its measured range (the data term is then 0)."""
        F = self.F
        if ks.size == 0 or self.l_data <= 0:
            return None
        plo, phi = F.pristine(ks, lo, hi)
        if not np.any((lo < plo) | (hi > phi)):
            return None

        def weight(i: np.ndarray, j: np.ndarray) -> np.ndarray:
            k = ks[i]
            dfc = F.deficits(k, j)
            with np.errstate(invalid="ignore", divide="ignore"):
                w = np.clip(dfc / np.maximum(F.delta[k], 1e-12), self.w_min, 1.0)
            return self.l_data * np.where(np.isfinite(w), w, 1.0)

        pen = ps.prefer_penalties(ks, lo, hi, plo, phi, weight)
        return pen if pen[0].size else None

    @staticmethod
    def _pen_subset(pen, keep: np.ndarray):
        """Penalty pairs restricted to the kept constraint frames (indices re-numbered)."""
        if pen is None:
            return None
        i, j, w = pen
        sel = keep[i]
        if not sel.any():
            return None
        new_idx = np.cumsum(keep) - 1
        return new_idx[i[sel]], j[sel], w[sel]

    def data_cost(self, pen, u: float, d: np.ndarray, base: int, xl: float, xh: float,
                  keep: np.ndarray | None = None) -> float:
        """Minimal data term over the feasible phase interval [xl, xh] (local units) at speed u."""
        if pen is None:
            return 0.0
        i, j, w = pen
        if keep is not None:
            sel = keep[i]
            i, j, w = i[sel], j[sel], w[sel]
        if i.size == 0:
            return 0.0
        st = (j - base).astype(np.float64) - u * d[i]
        return ps.min_penalty(st, w, xl, xh)

    def try_u(self, u: float, ks: np.ndarray, d: np.ndarray, lo_r: np.ndarray, hi_r: np.ndarray, base: int,
              droppable: np.ndarray, track: int | None = None, max_run: int = 1, with_interval: bool = False
              ) -> tuple | None:
        """Returns (dropped comp frames, slack t*, x*) -- plus the feasible phase interval (xl, xh) of the
        kept frames when with_interval -- or None. Local units (see phase_solve).

        A violating frame may be dropped only if it is isolated (no adjacent violation), interior (the
        caller's mask), its score for the model frame is within 5 delta_k of its best, and no competing
        hypothesis explains it: it lies on the segment's track, or its own best frame is a near miss
        (within 2 frames of the model frame -- a 1-frame excursion of +-1..2 frames is not an edit)."""
        L = lo_r - u * d
        U = hi_r + 1.0 - u * d
        lmax, umin = float(L.max()), float(U.min())
        if lmax - _TAU <= umin + _TAU:
            t = min((umin - lmax) / 2.0, 0.5)
            if with_interval:
                return [], t, (lmax + umin) / 2.0, lmax, umin
            return [], t, (lmax + umin) / 2.0
        if not droppable.any():
            return None
        hard = ~droppable
        if not hard.any():
            return None
        lh = float(L[hard].max()) - _TAU
        uh = float(U[hard].min()) + _TAU
        if lh > uh:
            return None
        di = np.nonzero(droppable)[0]
        Ld, Ud = L[di], U[di]
        cx = np.concatenate([[(lh + uh) / 2.0], np.clip((Ld + Ud) / 2.0, lh, uh), np.clip(Ld, lh, uh),
                             np.clip(Ud, lh, uh)])
        sat = (Ld[None, :] - _TAU <= cx[:, None]) & (cx[:, None] <= Ud[None, :] + _TAU)
        cnt = sat.sum(axis=1)
        best = np.nonzero(cnt == cnt.max())[0]
        centre = (lh + uh) / 2.0
        bi = int(best[np.argmin(np.abs(cx[best] - centre))])
        viol = di[~sat[bi]]
        if viol.size:
            sv = np.sort(viol)
            brk = np.nonzero(np.diff(sv) > 1)[0]
            run_len = np.diff(np.r_[-1, brk, sv.size - 1]).max()
            if run_len > max_run:
                return None   # adjacent violations: not isolated -> a real model break
        keep = np.ones(L.size, bool)
        keep[viol] = False
        lmax2, umin2 = float(L[keep].max()), float(U[keep].min())
        if lmax2 - _TAU > umin2 + _TAU:
            return None
        x = (lmax2 + umin2) / 2.0
        t = min((umin2 - lmax2) / 2.0, 0.5)
        F = self.F
        for i in viol:
            k = int(ks[i])
            j = int(math.floor(base + x + u * d[i] + 1e-9))
            if not (track is None or F.track[k] == track or abs(int(F.raw[k]) - j) <= 2):
                return None
            dfc = F.deficit(k, j)
            if math.isfinite(dfc):
                if dfc > 5.0 * F.delta[k]:
                    return None
            elif not (math.isfinite(F.margin[k]) and F.margin[k] <= 5.0 * F.delta[k]
                      and abs(int(F.raw[k]) - j) <= 2):
                # no score for the model frame: only a near miss of a low-margin frame is tolerated
                # (a far-away flash cut on the same track must stay a cut)
                return None
        if with_interval:
            return [int(ks[i]) for i in viol], t, x, lmax2, umin2
        return [int(ks[i]) for i in viol], t, x

    # -- full cost evaluation -----------------------------------------------------------------------
    def fit(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray, comp_in: int, droppable: np.ndarray,
            span: tuple[int, int], track: int, flip: bool, fixed_v: float | None = None,
            max_run: int = 1, pen: Any = _AUTO, bound: float = math.inf) -> Any:
        """Cheapest explanation of the constraint frames ks by one linear time map (or None when no line
        explains them). ``pen``: the data-term penalty pairs when the caller already has them (default:
        computed here). ``bound``: the DP only needs explanations costing <= bound; when the frames are
        feasible but nothing can, ``_PRUNED`` is returned (the data term is never computed for them)."""
        n = int(ks.size)
        if n == 0:
            v = self.dominant if fixed_v is None else fixed_v
            sol = {"raw_in": float("nan"), "raw_in_frames": float("nan"), "u": v * self.ratio, "ok": False,
                   "slack": float("nan"), "tie_frames": [], "interval_floor": None, "interval_both": None,
                   "margin_ms": float("nan"), "frame_slack": np.zeros(0)}
            return _Model(comp_in, v, "fixed", 0.0, False, None, float("nan"), [], sol, track, flip, 0)
        base = int(lo.min())
        d = (ks - comp_in).astype(np.float64)
        lo_r = (lo - base).astype(np.float64)
        hi_r = (hi - base).astype(np.float64)
        pen = self.penalties(ks, lo, hi) if pen is _AUTO else pen
        plo, phi = self.F.pristine(ks, lo, hi)
        vo_cache: list[float] = []

        def v_ols_() -> float:      # robust slope of the MEASURED frames, only computed when a decision needs it
            if not vo_cache:
                vo_cache.append(ps.estimate_speed(ks, plo, phi, self.cf, self.rf) if n >= 2 else float("nan"))
            return vo_cache[0]

        # O(1) necessary condition from the first and last frames when both are hard constraints (never
        # droppable): u within their pairwise bounds (the exact LP projection of that pair)
        u_lo, u_hi = -math.inf, math.inf
        if n >= 2 and not droppable[0] and not droppable[-1] and d[-1] > d[0]:
            D = float(d[-1] - d[0])
            u_lo = (lo_r[-1] - hi_r[0] - 1.0 - 4 * _TAU) / D
            u_hi = (hi_r[-1] + 1.0 - lo_r[0] + 4 * _TAU) / D

        def evaluate(v: float, bc: float, dmask: np.ndarray, mr: int, bound: float = math.inf):
            """(total, data, drops) of speed v, or None when infeasible; the data term is skipped (returned as
            inf) when the candidate cannot reach ``bound`` even with a zero data term."""
            u = v * self.ratio
            if dmask is droppable and not (u_lo <= u <= u_hi):
                return None
            r = self.try_u(u, ks, d, lo_r, hi_r, base, dmask, track, mr, with_interval=True)
            if r is None:
                return None
            drops, t, _x, xl, xh = r
            base_total = bc + self.l_drop * len(drops) + (self.l_tie if t < _TIE else 0.0)
            if base_total > bound + 1e-9:
                return math.inf, math.inf, drops
            keep = None
            if drops:
                keep = ~np.isin(ks, np.asarray(drops, dtype=np.int64))
            data = self.data_cost(pen, v * self.ratio, d, base, xl, xh, keep)
            return round(base_total + data, 9), data, drops

        if fixed_v is not None:
            cands = [(float(fixed_v), 0.0, "fixed", 0)]
        else:
            cands = self.speed_cands(*span)
        feas: list[tuple] = []      # (total, data, rank, v, kind, drops)
        bt = math.inf
        feasible = False
        for v, bc, kind, rank in cands:
            lim = min(bt, bound)
            if bc > lim + 1e-9 and feasible:
                break
            ev = evaluate(v, bc, droppable, max_run, lim)
            if ev is None:
                continue
            feasible = True
            if not math.isfinite(ev[0]):
                continue
            feas.append((ev[0], ev[1], rank, v, kind, ev[2]))
            bt = min(bt, ev[0])
        best = None      # (key, v, kind, drops, unsnapped, data); key = (total, data, rank, |v - v_ols|, v)
        if feas:
            k0 = min(f[:3] for f in feas)
            ties = [f for f in feas if f[:3] == k0]
            if len(ties) > 1:       # equal cost and rank (snap values): the one closest to the measured slope
                vo = v_ols_()
                ties.sort(key=lambda f: (abs(f[3] - vo) if math.isfinite(vo) else 0.0, f[3]))
            f = ties[0]
            best = ((f[0], f[1], f[2], 0.0, f[3]), f[3], f[4], f[5], False, f[1])
        # unsnapped speed (prompt 5.4: snap only if the residuals don't get worse): the robust slope of the
        # measured frames, clipped into the speeds that reproduce them (else into the soft range). Tried when
        # nothing snapped is feasible or when every snapped explanation costs more than lambda_unsnapped
        # (i.e. it contradicts the measured frames).
        if fixed_v is None and n >= 2 and (best is None or best[0][0] > self.l_uns + 1e-9) and \
                not (feasible and self.l_uns > bound + 1e-9) and \
                not (best is not None and self._within_snap_tol(best[1], ks, plo, phi)):
            un = self._unsnapped(ks, lo, hi, plo, phi, comp_in, droppable, v_ols_, evaluate, bound)
            if un is not None:
                feasible = True
                v, (total, data, drops) = un
                key = (total, data, 9, 0.0, v)
                if math.isfinite(total) and (best is None or key < best[0]):
                    best = (key, v, "unsnapped", drops, True, data)
        if best is None:
            return _PRUNED if feasible else None
        key, v, kind, drops, uns, data = best
        use = np.ones(n, bool)
        if drops:
            use &= ~np.isin(ks, np.asarray(drops, dtype=np.int64))
        cf, rf, psub = self.cf, self.rf, self._pen_subset(pen, use)

        def solve() -> dict:
            return ps.solve_raw_in(ks[use], lo[use], hi[use], comp_in, v, cf, rf, penalties=psub)

        vo = vo_cache[0] if vo_cache else float("nan")     # final segments recompute it (to_segment)
        return _Model(comp_in, float(v), kind, float(key[0]), bool(uns), None, float(vo), list(drops), None,
                      track, flip, n, float(data), lazy=solve)

    def _within_snap_tol(self, v: float, ks: np.ndarray, plo: np.ndarray, phi: np.ndarray) -> bool:
        """The measured frames' least-squares slope is within speed_snap_tol of v: the snap passes the prompt's
        test and an unsnapped line at (almost) the same slope cannot explain the frames better (O(n) gate for
        the O(n^2) unsnapped search -- dense argmax noise raises the data term of EVERY line)."""
        x = ks.astype(np.float64)
        x = x - x.mean()
        sxx = float((x * x).sum())
        if sxx <= 0 or v == 0:
            return False
        y = (plo + phi + 1).astype(np.float64) / 2.0
        vl = float((x * (y - y.mean())).sum()) / sxx / self.ratio
        return abs(vl - v) <= float(_cfg(self.cfg, "speed_snap_tol", 0.003)) * abs(v)

    def _unsnapped(self, ks, lo, hi, plo, phi, comp_in, droppable, v_ols_, evaluate, bound=math.inf):
        """(v, evaluate(v)) of the unsnapped explanation (total inf: feasible but above bound), or None."""
        vr = ps.feasible_speed_range(ks, lo, hi, comp_in, self.cf, self.rf)
        if vr is None and droppable.any():
            keep = ~droppable
            if keep.sum() >= 1:
                vr = ps.feasible_speed_range(ks[keep], lo[keep], hi[keep], comp_in, self.cf, self.rf)
        if vr is None:
            return None
        v_ols = v_ols_()
        vo = v_ols if math.isfinite(v_ols) else (vr[0] + vr[1]) / 2.0
        tries = []
        ex = ps.feasible_speed_range(ks, plo, phi, comp_in, self.cf, self.rf) \
            if bool(np.any((plo > lo) | (phi < hi))) else vr
        if ex is not None and ex[0] <= vr[1] and ex[1] >= vr[0]:
            e0, e1 = max(ex[0], vr[0]), min(ex[1], vr[1])
            tries.append(float(min(max(vo, e0), e1)))
            tries.append((e0 + e1) / 2.0)
        tries.append(float(min(max(vo, vr[0]), vr[1])))
        tries.append((vr[0] + vr[1]) / 2.0)   # clipped v on the edge of a relaxed range: the centre
        best = None
        seen: list[float] = []
        for v in tries:
            if any(abs(v - s) <= 1e-12 for s in seen):
                continue
            seen.append(v)
            ev = evaluate(v, self.l_uns, droppable, 1, bound)
            if ev is not None and (best is None or ev[:2] < best[1][:2]):
                best = (v, ev)
                if ev[1] <= 1e-12:
                    break
        return best

    def vrange(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray, comp_in: int) -> tuple[float, float] | None:
        if ks.size == 0:
            return None
        return ps.feasible_speed_range(ks, lo, hi, comp_in, self.cf, self.rf)

    def relaxed_infeasible(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray, comp_in: int,
                           relaxed: np.ndarray) -> bool:
        """Sound break test for the DP: infeasible at every speed even with every relaxed-droppable
        frame removed (so every superset segment is infeasible too)."""
        keep = ~relaxed
        if keep.sum() <= 1:
            return False
        return ps.feasible_speed_range(ks[keep], lo[keep], hi[keep], comp_in, self.cf, self.rf) is None


# =================================================================================================
# pixel scoring (optional: needs proxies + layout)
# =================================================================================================

def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    import cv2
    img = img.astype(np.float32, copy=False)
    return cv2.GaussianBlur(img, (0, 0), sigma) if sigma and sigma > 0 else img


def _box_coverage(box_p: tuple[float, float, float, float, float], size: tuple[int, int],
                  roi: tuple[int, int, int, int], ss: int = 4) -> np.ndarray:
    """Coverage of a rounded rectangle (proxy coords, CORNER convention) over the ROI pixels."""
    bx, by, bw, bh, r = box_p
    x0, y0, w, h = roi
    r = max(0.0, min(r, bw / 2.0, bh / 2.0))
    offs = (np.arange(ss) + 0.5) / ss
    xs = ((x0 + np.arange(w))[:, None] + offs[None, :]).reshape(-1) - bx
    ys = ((y0 + np.arange(h))[:, None] + offs[None, :]).reshape(-1) - by
    inx = (xs >= 0) & (xs <= bw)
    iny = (ys >= 0) & (ys <= bh)
    cx = np.clip(xs, r, bw - r)
    cy = np.clip(ys, r, bh - r)
    dx = (xs - cx)[None, :]
    dy = (ys - cy)[:, None]
    inside = (inx[None, :] & iny[:, None])
    if r > 0:
        inside &= (dx * dx + dy * dy) <= r * r
    return inside.reshape(h, ss, w, ss).mean(axis=(1, 3)).astype(np.float32)


class _Scorer:
    """Masked ZNCC in competitor space (same conventions as scoring.py) with warp caching."""

    def __init__(self, comp: Any, raw: Any, layout: Any, overlays: Any, cfg: Any):
        self.cfg = cfg
        self.comp, self.raw, self.layout, self.overlays = comp, raw, layout, overlays
        self.ok = bool(comp is not None and raw is not None and getattr(comp, "frames", None) is not None
                       and getattr(raw, "frames", None) is not None)
        self.blur = float(_cfg(cfg, "score_blur", 1.0))
        self.gw = float(_cfg(cfg, "grad_weight", 0.0))
        self._warps: OrderedDict = OrderedDict()
        self._regions: OrderedDict = OrderedDict()
        self._layout_allowed = None
        if not self.ok:
            return
        h, w = int(comp.frames.shape[1]), int(comp.frames.shape[2])
        self.psize = (w, h)
        rx, ry = comp.ratio
        box = getattr(layout, "box", None) if layout is not None else None
        if box is not None:
            bp = box.scaled(rx, ry)
            x0, y0 = max(0, int(math.floor(bp.x))), max(0, int(math.floor(bp.y)))
            x1, y1 = min(w, int(math.ceil(bp.x + bp.w))), min(h, int(math.ceil(bp.y + bp.h)))
            self.roi = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
            cov = _box_coverage((bp.x, bp.y, bp.w, bp.h, bp.corner_radius), (w, h), self.roi)
        else:
            self.roi = (0, 0, w, h)
            cov = np.ones((h, w), np.float32)
        base = np.zeros((h, w), bool)
        x0, y0, rw, rh = self.roi
        base[y0:y0 + rh, x0:x0 + rw] = cov >= 0.99
        sm = getattr(layout, "static_mask_file", "") if layout is not None else ""
        if sm and Path(sm).exists():
            try:
                st = np.load(sm).astype(bool)
                if st.shape == base.shape:
                    base &= ~st
            except Exception as e:  # pragma: no cover - corrupt file
                log.warning("segment: cannot load static mask %s: %s", sm, e)
        self.base_allowed = base
        self.raw_w = float(raw.full_size[0])

    # -- masks & regions ------------------------------------------------------------------------------
    def allowed(self, k: int) -> np.ndarray:
        if self.overlays is not None:
            if self._layout_allowed is None:
                try:
                    from .layout import allowed_mask  # noqa: WPS433 (other agent's module, optional)
                    self._layout_allowed = allowed_mask
                except Exception:
                    self._layout_allowed = False
            if self._layout_allowed:
                try:
                    return np.asarray(self._layout_allowed(self.layout, self.overlays, k, self.comp), bool)
                except Exception as e:  # fall back to the local mask
                    log.debug("segment: layout.allowed_mask failed (%s); using local mask", e)
            m = self.base_allowed
            try:
                ov = self.overlays.get(k)
            except Exception:
                ov = None
            if ov is not None and np.asarray(ov).shape == m.shape:
                import cv2
                dp = int(_cfg(self.cfg, "overlay_dilate_px", 3))
                ovd = np.asarray(ov).astype(np.uint8)
                if dp > 0:
                    ovd = cv2.dilate(ovd, np.ones((2 * dp + 1, 2 * dp + 1), np.uint8))
                return m & ~(ovd > 0)
            return m
        return self.base_allowed

    def region(self, k: int):
        from .scoring import prepare_comp
        if k in self._regions:
            self._regions.move_to_end(k)
            return self._regions[k]
        if not (0 <= k < self.comp.n):
            return None
        try:
            img = np.asarray(self.comp.get(k))
        except (KeyError, IndexError):
            return None
        reg = prepare_comp(img, self.roi, self.allowed(k), blur=self.blur, with_grad=False)
        self._regions[k] = reg
        if len(self._regions) > 48:
            self._regions.popitem(last=False)
        return reg

    def warp(self, j: int, sim: Sim, flip: bool):
        from .scoring import warp_to_roi
        key = (int(j), round(sim.s, 9), round(sim.theta_deg, 7), round(sim.tx, 5), round(sim.ty, 5), bool(flip))
        if key in self._warps:
            self._warps.move_to_end(key)
            return self._warps[key]
        if not self.raw.has(int(j)):
            return None
        img = np.asarray(self.raw.get(int(j)))
        w, v = warp_to_roi(img, sim, flip, self.raw_w, self.raw.ratio, self.comp.ratio, self.roi)
        out = (_blur(w, self.blur), v)
        self._warps[key] = out
        if len(self._warps) > 160:
            self._warps.popitem(last=False)
        return out

    def _stack(self, k: int, items: Sequence[tuple[int, Sim, bool]]):
        reg = self.region(k)
        if reg is None or not items:
            return None
        ws = []
        mask = reg.mask.copy()
        for j, sim, flip in items:
            r = self.warp(j, sim, flip)
            if r is None:
                return None
            ws.append(r[0])
            mask &= r[1]
        idx = np.flatnonzero(mask)
        if idx.size < 256:
            return None
        cap = int(_cfg(self.cfg, "segment_score_max_pixels", 60000))
        if idx.size > cap:          # regular subsample: correlations need far fewer pixels than the ROI has
            idx = idx[:: int(math.ceil(idx.size / cap))]
        y = reg.img.ravel()[idx].astype(np.float64)
        M = np.stack([w_.ravel()[idx] for w_ in ws]).astype(np.float64)
        return reg, mask, y, M, ws

    def zncc_set(self, k: int, items: Sequence[tuple[int, Sim, bool]]) -> np.ndarray | None:
        """Masked ZNCC of comp frame k against each (RAW frame, Sim, flip) on their COMMON valid mask."""
        from .scoring import zncc_rows, _gradmag  # noqa: PLC2701 (foundation helper)
        st = self._stack(k, items)
        if st is None:
            return None
        reg, mask, y, M, ws = st
        s = zncc_rows(y, M)
        if self.gw > 0:
            idx = np.flatnonzero(mask)
            g = _gradmag(reg.img).ravel()[idx]
            gm = np.stack([_gradmag(w_).ravel()[idx] for w_ in ws])
            s = (1 - self.gw) * s + self.gw * zncc_rows(g, gm)
        return s

    def blend_fit(self, k: int, a_items: Sequence[tuple[int, Sim, bool]],
                  b_items: Sequence[tuple[int, Sim, bool]]) -> dict | None:
        """All A x B two-source fits y ≈ alpha*A + (1-alpha)*B + c (scoring.fit_blend, vectorised via the
        covariance matrix) + single-source ZNCC and gains. alpha = weight of A (outgoing).

        The best pair (highest zfit) also gets the gain-independent weight 'alpha_a_free' (scoring.
        blend_alpha_cov: y ≈ beta_A*A + beta_B*B + c, beta_A / (beta_A + beta_B); NaN when undefined) and
        'gain_free' (beta_A + beta_B): a contrast change of the repost biases the constrained alpha, not
        this one (review R2-1). The crossfade fit uses alpha_a_free; zfit stays the constrained fit's."""
        items = list(a_items) + list(b_items)
        st = self._stack(k, items)
        if st is None:
            return None
        _reg, _mask, y, M, _ws = st
        X = np.vstack([y[None, :], M])
        X -= X.mean(axis=1, keepdims=True)
        C = (X @ X.T) / X.shape[1]
        vy = C[0, 0]
        na = len(a_items)
        single = np.array([C[0, i + 1] / math.sqrt(max(vy * C[i + 1, i + 1], 1e-30)) for i in range(len(items))])
        gains = np.array([C[0, i + 1] / max(C[i + 1, i + 1], 1e-30) for i in range(len(items))])
        best = None
        for ia in range(na):
            for ib in range(len(b_items)):
                A, B = 1 + ia, 1 + na + ib
                ve = C[A, A] + C[B, B] - 2 * C[A, B]
                if ve <= 1e-12:
                    continue
                cez = C[A, 0] - C[A, B] - C[B, 0] + C[B, B]
                al = cez / ve
                cyw = al * C[0, A] + (1 - al) * C[0, B]
                vw = al * al * C[A, A] + (1 - al) ** 2 * C[B, B] + 2 * al * (1 - al) * C[A, B]
                z = cyw / math.sqrt(max(vy * vw, 1e-30))
                if best is None or z > best[0]:
                    best = (z, al, ia, ib)
        out = {"single": single, "gains": gains, "na": na}
        if best is not None:
            from .scoring import blend_alpha_cov
            A, B = 1 + best[2], 1 + na + best[3]
            af, g = blend_alpha_cov(float(C[A, A]), float(C[B, B]), float(C[A, B]), float(C[A, 0]), float(C[B, 0]))
            out.update({"zfit": float(best[0]), "alpha_a": float(best[1]), "ia": best[2], "ib": best[3],
                        "alpha_a_free": af, "gain_free": g})
        return out


# =================================================================================================
# working segment
# =================================================================================================

@dataclass
class _Seg:
    kind: str                                   # raw | not_in_raw | flash | dip
    a: int
    b: int
    model: _Model | None = None
    flip: bool = False
    track: int = -1
    trans_in: dict | None = None
    trans_out: dict | None = None
    extra: dict = field(default_factory=dict)   # k -> (lo, hi) extra constraints (blend frames)
    notes: list = field(default_factory=list)
    cut_ambiguity: list | None = None
    color: str | None = None
    retime: str = "none"
    uncertain: bool = False
    framing: dict | None = None
    blend_frames: list = field(default_factory=list)
    ramp: list = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.b - self.a


# =================================================================================================
# the builder
# =================================================================================================

class _Builder:
    def __init__(self, fm: FrameMap, comp: Any, raw: Any, layout: Any, overlays: Any, cfg: Any,
                 dlog: DecisionLog | None, debug_dir: Any, hints: Any, src: FrameMap | None = None):
        self.fm = fm                      # write target
        src = fm if src is None else src  # refine's columns (read)
        self.cfg = cfg
        self.dlog = dlog or null_dlog()
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.comp, self.raw, self.layout = comp, raw, layout
        self.hints = hints
        if comp is None or raw is None:
            raise ValueError("build_segments needs the competitor and RAW proxies (for fps and geometry)")
        self.cf = Fraction(comp.fps)
        self.rf = Fraction(raw.fps)
        self.F = _Frames(src, cfg)
        self.unreliable_sim: set[int] = set()   # frames inside a localised framing step (interpolated Sims)
        self.S = _Solver(self.F, cfg, self.cf, self.rf, hints)
        self.P = _Scorer(comp, raw, layout, overlays, cfg)
        self.n = fm.n
        cw, ch = (comp.full_size if getattr(comp, "full_size", None) else
                  (getattr(layout, "comp_w", 0), getattr(layout, "comp_h", 0)))
        box = getattr(layout, "box", None) if layout is not None else None
        if box is not None:
            self.center = (box.x + box.w / 2.0, box.y + box.h / 2.0)
            self.box_r = math.hypot(box.w, box.h) / 2.0
        else:
            self.center = (cw / 2.0, ch / 2.0)
            self.box_r = math.hypot(cw, ch) / 2.0
        self.ts = int(_cfg(cfg, "transition_search", 20))
        self._gpen: Any = None          # data-term pairs over the whole timeline (per DP pass)
        self._init_periods(layout, cw, ch)

    # ---------------------------------------------------------------------------------------------
    # layout periods (D1: fullscreen vs boxed shots, split / PiP)
    # ---------------------------------------------------------------------------------------------
    def _init_periods(self, layout: Any, cw: Any, ch: Any) -> None:
        """Per-frame layout class from layout.periods: (box dict | None, region, mode, period). The dominant
        layout -> (None, 0); a 'fullscreen' period inside another dominant layout -> the whole canvas, region
        1; 'split' / 'pip' (unsupported: extra regions are not recreated) -> region 2 / 3, box None.
        ``self.plabel`` increments at every change, so segments never straddle a period boundary."""
        n = self.n
        self.pinfos: list[tuple] = [(None, 0, None, None)]      # class 0 = dominant layout
        self.pcls = np.zeros(n, np.int64)
        self.plabel = np.zeros(n, np.int64)
        periods = list(getattr(layout, "periods", None) or []) if layout is not None else []
        if not periods or n == 0:
            return
        W = float(getattr(layout, "comp_w", 0) or (cw or 0))
        H = float(getattr(layout, "comp_h", 0) or (ch or 0))
        dom = str(getattr(layout, "mode", "boxed") or "boxed")
        for p in sorted(periods, key=lambda p: (int(p.comp_in), int(p.comp_out))):
            a, b = max(0, int(p.comp_in)), min(n, int(p.comp_out))
            mode = str(p.mode)
            if b <= a or mode == dom:
                continue
            if mode == "fullscreen":
                info = ({"x": 0.0, "y": 0.0, "w": W, "h": H, "corner_radius": 0.0}, 1, mode, (a, b))
            elif mode == "pip":
                info = (None, 3, mode, (a, b))
            else:                       # split (or any other unsupported multi-region layout)
                info = (None, 2, mode, (a, b))
            self.pinfos.append(info)
            self.pcls[a:b] = len(self.pinfos) - 1
        if len(self.pinfos) == 1:
            return
        self.plabel = np.concatenate([[0], np.cumsum(self.pcls[1:] != self.pcls[:-1])]).astype(np.int64)
        self.log("layout_periods", evidence={"dominant": dom, "periods": [
            {"comp_in": i[3][0], "comp_out": i[3][1], "mode": i[2], "region": i[1], "box": i[0]}
            for i in self.pinfos[1:]]})

    def _period_break(self, k: int) -> bool:
        return 0 < k < self.n and self.plabel[k] != self.plabel[k - 1]

    def _same_period(self, a: int, b: int) -> bool:
        """[a, b) lies inside one layout period."""
        a, b = max(0, int(a)), min(self.n, int(b))
        return b <= a or self.plabel[a] == self.plabel[b - 1]

    def _period_info(self, a: int, b: int) -> tuple:
        """(box, region, mode, period) of the layout class covering most of [a, b)."""
        a, b = max(0, int(a)), min(self.n, int(b))
        if len(self.pinfos) == 1 or b <= a:
            return self.pinfos[0]
        cls, cnt = np.unique(self.pcls[a:b], return_counts=True)
        return self.pinfos[int(cls[np.argmax(cnt)])]

    def assign_regions(self, segs: list[Segment]) -> None:
        """Segment.box / Segment.region from the layout period (D1)."""
        if len(self.pinfos) == 1:
            return
        for s in segs:
            box, region, mode, per = self._period_info(s.comp_in, s.comp_out)
            s.box = dict(box) if box is not None else None
            s.region = int(region)
            if not self._same_period(s.comp_in, s.comp_out):
                self.log("segment_straddles_period", comp_range=[s.comp_in, s.comp_out], evidence={
                    "reason": "transition overlap across a layout period boundary", "assigned_mode": mode})
            if mode is None:
                continue
            if mode == "fullscreen":
                note = f"fullscreen layout period (frames {per[0]}-{per[1] - 1}): shown on the whole canvas"
            else:
                note = (f"'{mode}' layout period (frames {per[0]}-{per[1] - 1}): extra video region(s) are not "
                        "recreated (only the dominant box is rebuilt)")
            s.notes = (s.notes + "; " if s.notes else "") + note
            self.log("segment_layout_period", comp_range=[s.comp_in, s.comp_out], evidence={
                "mode": mode, "region": s.region, "box": s.box, "period": list(per)})

    # ---------------------------------------------------------------------------------------------
    def log(self, decision: str, **kw: Any) -> None:
        self.dlog.record("segment", decision, **kw)

    def pred(self, seg: _Seg, k: int | np.ndarray):
        if seg.model is None or not math.isfinite(seg.model.sol.get("raw_in", float("nan"))):
            return -1 if np.isscalar(k) else np.full(np.shape(k), -1, np.int64)
        return ps.ae_frame(seg.model.raw_in, seg.model.v, k, seg.model.comp_in, self.cf, self.rf)

    # ---------------------------------------------------------------------------------------------
    # runs and hard boundaries
    # ---------------------------------------------------------------------------------------------
    def status_runs(self) -> list[tuple[int, int, int]]:
        """Runs of equal status, split at layout period boundaries (D1)."""
        st = self.F.status
        out = []
        a = 0
        for k in range(1, self.n + 1):
            if k == self.n or st[k] != st[a] or self._period_break(k):
                out.append((a, k, int(st[a])))
                a = k
        return out

    def _sim_arrays(self, idx: np.ndarray):
        F = self.F
        s = F.s[idx]
        th = np.where(np.isfinite(F.theta[idx]), F.theta[idx], 0.0)
        return s, th, F.tx[idx], F.ty[idx]

    def _centre_shift(self, s0, th0, tx0, ty0, s1, th1, tx1, ty1) -> np.ndarray:
        """|sim1(sim0^-1(c)) - c| for the box centre c (vectorised)."""
        cx, cy = self.center
        t0 = np.radians(th0)
        t1 = np.radians(th1)
        qx, qy = cx - tx0, cy - ty0
        px = (np.cos(t0) * qx + np.sin(t0) * qy) / s0
        py = (-np.sin(t0) * qx + np.cos(t0) * qy) / s0
        mx = s1 * (np.cos(t1) * px - np.sin(t1) * py) + tx1
        my = s1 * (np.sin(t1) * px + np.cos(t1) * py) + ty1
        return np.hypot(mx - cx, my - cy)

    def _change(self, i0: np.ndarray, i1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        s0, th0, x0, y0 = self._sim_arrays(i0)
        s1, th1, x1, y1 = self._sim_arrays(i1)
        ds = np.abs(s1 / s0 - 1.0)
        dp = self._centre_shift(s0, th0, x0, y0, s1, th1, x1, y1)
        dp = dp + np.abs(np.radians(th1 - th0)) * self.box_r   # rotation as displacement at the box edge
        return ds, dp

    def transform_steps(self, r0: int, r1: int) -> list[int]:
        """Comp frames k (cut before k) where the framing steps inside a MATCH run (punch-in)."""
        F, cfg = self.F, self.cfg
        idx = np.arange(r0, r1)
        good = np.isfinite(F.s[idx]) & (F.s[idx] > 0)
        idx = idx[good]
        if idx.size < 2:
            return []
        thr_s = float(_cfg(cfg, "punch_scale_step", 0.01))
        thr_p = float(_cfg(cfg, "punch_pos_step", 4.0))
        W = 2 * int(_cfg(cfg, "framing_sample_step", 3))
        ds, dp = self._change(idx[:-1], idx[1:])
        span = np.maximum(idx[1:] - idx[:-1], 1)
        fast = (ds / span > thr_s / W) | (dp / span > thr_p / W)
        steps: list[int] = []
        i = 0
        m = fast.size
        while i < m:
            if not fast[i]:
                i += 1
                continue
            j = i
            while j + 1 < m and fast[j + 1]:
                j += 1
            ka, kb = int(idx[i]), int(idx[j + 1])      # Sims before / after the fast stretch
            tds, tdp = self._change(np.array([ka]), np.array([kb]))
            if (tds[0] > thr_s or tdp[0] > thr_p) and (kb - ka) <= W:
                cut = self._localise_step(ka, kb, float(tds[0]), float(tdp[0]))
                steps.append(cut)
            i = j + 1
        return steps

    def _localise_step(self, ka: int, kb: int, ds: float, dp: float) -> int:
        """Cut position between frames ka (old framing) and kb (new framing): the first frame scored
        better under the new transform by > 3 delta_k (DESIGN), else the Sim midpoint."""
        F = self.F
        if kb == ka + 1:
            cut = kb
            how = "consecutive_step"
        else:
            cut = None
            how = "sim_midpoint"
            simA, simB = F.sim(ka), F.sim(kb)
            if self.P.ok and simA is not None and simB is not None:
                for k in range(ka, kb + 1):
                    j = int(F.raw[k])
                    if j < 0:
                        continue
                    sc = self.P.zncc_set(k, [(j, simA, bool(F.flip[k])), (j, simB, bool(F.flip[k]))])
                    if sc is None or not np.all(np.isfinite(sc)):
                        continue
                    if sc[1] > sc[0] + 3.0 * F.delta[k]:
                        cut, how = k, "scored_both_transforms"
                        break
            if cut is None:
                thr_s = float(_cfg(self.cfg, "punch_scale_step", 0.01))
                thr_p = float(_cfg(self.cfg, "punch_pos_step", 4.0))
                ks_ = np.arange(ka, kb + 1)
                ds_, dp_ = self._change(np.full(ks_.size, ka), ks_)
                prog = ds_ / thr_s + dp_ / thr_p
                tot = max(float(prog[-1]), 1e-12)
                cut = next((int(k) for k, p_ in zip(ks_, prog) if p_ / tot >= 0.5), kb)
                cut = max(ka + 1, min(kb, cut))
        self.unreliable_sim.update(range(ka + 1, kb))
        self.log("transform_step", comp_frame=int(cut), evidence={"from": ka, "to": kb, "d_scale": ds,
                                                                   "d_pos_px": dp, "method": how})
        return int(cut)

    def match_subruns(self, r0: int, r1: int) -> list[tuple[int, int]]:
        F = self.F
        cuts = {r0, r1}
        for k in range(r0 + 1, r1):
            if F.flip[k] != F.flip[k - 1]:
                cuts.add(k)
        pts = sorted(cuts)
        out = []
        for a, b in zip(pts[:-1], pts[1:]):
            inner = sorted(set([a, b] + [c for c in self.transform_steps(a, b) if a < c < b]))
            out.extend(zip(inner[:-1], inner[1:]))
        return out

    # ---------------------------------------------------------------------------------------------
    # DP
    # ---------------------------------------------------------------------------------------------
    def _phase_breaks(self, r0: int, r1: int, u: float, lo: np.ndarray | None = None,
                      hi: np.ndarray | None = None) -> set[int]:
        F = self.F
        lo = F.lo if lo is None else lo
        hi = F.hi if hi is None else hi
        out: set[int] = set()
        # forward
        start, lr, ur = r0, -math.inf, math.inf
        for k in range(r0, r1):
            dd = k - start
            lr = max(lr, lo[k] - u * dd)
            ur = min(ur, hi[k] + 1 - u * dd)
            if lr > ur + 2 * _TAU:
                out.add(k)
                start, lr, ur = k, float(lo[k]), float(hi[k] + 1)
        # backward
        start, lr, ur = r1 - 1, -math.inf, math.inf
        for k in range(r1 - 1, r0 - 1, -1):
            dd = k - start
            lr = max(lr, lo[k] - u * dd)
            ur = min(ur, hi[k] + 1 - u * dd)
            if lr > ur + 2 * _TAU:
                out.add(k + 1)
                start, lr, ur = k, float(lo[k]), float(hi[k] + 1)
        return out

    def _measured_steps(self, r0: int, r1: int, u: float, w: int = 5) -> set[int]:
        """Candidate cuts where the MEASURED (argmax-range) frames step away from a line of speed u: the
        residual l_k = centre(raw_lo, raw_hi)_k - u k changes level between the medians of the w frames
        before and from k (jump cuts, 1-frame skips / repeats, the regular steps of a 1.02-1.05x retime).
        Isolated argmax errors inside wide soft ranges do not move a median, so noisy slow footage adds few
        candidates (the DP is O(candidates^2))."""
        F = self.F
        n = r1 - r0
        if n < 2 * w:
            return set()
        ks = np.arange(r0, r1)
        lv = (F.raw_lo[r0:r1] + F.raw_hi[r0:r1]).astype(np.float64) / 2.0 - u * ks
        win = np.lib.stride_tricks.sliding_window_view(lv, w)
        med = np.median(win, axis=1)                   # med[i] = median of lv[i:i+w]
        left, right = med[:-w], med[w:]                # windows [i, i+w) and [i+w, i+2w) -> step at i+w
        idx = np.nonzero(np.abs(right - left) >= 0.5)[0] + w
        return {int(r0 + i) for i in idx}

    def _free_runs(self, r0: int, r1: int, forward: bool = True, lo_a: np.ndarray | None = None,
                   hi_a: np.ndarray | None = None) -> list[tuple[int, int, float, float]]:
        """Greedy maximal runs explained by ONE line at ANY speed (exact pairwise u bounds, incremental).
        Returns [(a, b, umin, umax)] tiling [r0, r1) (u in RAW frames per comp frame). Used only to place
        candidate cuts: its boundaries are real model breaks at some speed. Soft ranges by default."""
        F = self.F
        lo = (F.lo if lo_a is None else lo_a).astype(np.float64)
        hi = (F.hi if hi_a is None else hi_a).astype(np.float64)
        t2 = 2 * _TAU
        runs = []
        if forward:
            a, umin, umax = r0, -math.inf, math.inf
            for k in range(r0 + 1, r1):
                d = k - np.arange(a, k, dtype=np.float64)
                nu = float(((hi[k] + 1.0 - lo[a:k] + t2) / d).min())
                nl = float(((lo[k] - hi[a:k] - 1.0 - t2) / d).max())
                umin2, umax2 = max(umin, nl), min(umax, nu)
                if umin2 > umax2 + 1e-12:
                    runs.append((a, k, umin, umax))
                    a, umin, umax = k, -math.inf, math.inf
                else:
                    umin, umax = umin2, umax2
            runs.append((a, r1, umin, umax))
        else:
            b, umin, umax = r1, -math.inf, math.inf
            for k in range(r1 - 2, r0 - 1, -1):
                d = np.arange(k + 1, b, dtype=np.float64) - k
                nu = float(((hi[k + 1:b] + 1.0 - lo[k] + t2) / d).min())
                nl = float(((lo[k + 1:b] - hi[k] - 1.0 - t2) / d).max())
                umin2, umax2 = max(umin, nl), min(umax, nu)
                if umin2 > umax2 + 1e-12:
                    runs.append((k + 1, b, umin, umax))
                    b, umin, umax = k + 1, -math.inf, math.inf
                else:
                    umin, umax = umin2, umax2
            runs.append((r0, b, umin, umax))
            runs.reverse()
        return runs

    def candidates(self, r0: int, r1: int) -> list[int]:
        """Candidate cut positions of a MATCH run (DESIGN: frames where the increment deviates from the
        floor pattern, track / transform changes, audio lag steps).

        * boundaries of the greedy free-speed runs (forward and backward) +-1: every real model break;
        * phase breaks at the dominant speed and at 1.0 (+-1) and increment anomalies -- only inside free
          runs whose speed range lies within 15 % of that speed, where a jump cut could masquerade as a
          slightly different speed (a 1-frame skip at 1.0 looks like 1.02-1.05x); far from it (e.g. a
          1.337x run) they would put a candidate on every frame and explain nothing;
        * phase breaks at the snap speed nearest to a free run's speed range (within 3 %);
        * track changes and audio lag steps.
        The consecutive forward free-run boundaries always form a feasible segmentation."""
        F = self.F
        cands: set[int] = {r0, r1}
        fwd = self._free_runs(r0, r1, True)
        bwd = self._free_runs(r0, r1, False)
        for a, b, _u0, _u1 in fwd + bwd:
            cands.update((a - 1, a, a + 1, b - 1, b, b + 1))
        ratio = self.S.ratio

        def near(run, u: float, tol: float) -> bool:
            _a, _b, u0, u1 = run
            return u0 <= u * (1 + tol) and u1 >= u * (1 - tol) if u >= 0 else u0 <= u * (1 - tol) and u1 >= u * (1 + tol)

        def run_at(runs, k: int):
            for r in runs:
                if r[0] <= k < r[1]:
                    return r
            return runs[-1]

        speeds = {self.S.dominant: 0.15, 1.0: 0.15}
        for run in fwd:
            u0, u1 = run[2], run[3]
            if math.isfinite(u0) and math.isfinite(u1) and run[1] - run[0] >= 3:
                uc = (u0 + u1) / 2.0
                vs = min(self.S.snaps, key=lambda v: abs(v * ratio - uc))
                if abs(vs * ratio - uc) <= 0.03 * abs(vs * ratio) and vs not in speeds:
                    speeds[vs] = 0.03
        inc = np.diff(F.raw[r0:r1])
        for v, tol in sorted(speeds.items()):
            u = v * ratio
            for b in self._phase_breaks(r0, r1, u):
                if near(run_at(fwd, min(b, r1 - 1)), u, tol) or near(run_at(bwd, min(b, r1 - 1)), u, tol) or \
                        near(run_at(fwd, max(b - 1, r0)), u, tol):
                    cands.update((b - 1, b, b + 1))
            lo_i, hi_i = math.floor(u), math.ceil(u)
            for i in np.nonzero((inc < lo_i) | (inc > hi_i))[0]:
                k = r0 + int(i) + 1
                if near(run_at(fwd, k), u, tol) or near(run_at(fwd, k - 1), u, tol):
                    cands.add(k)
        # soft ranges wider than the measured argmax range (slow footage): the soft phase breaks above do not
        # see a 1-2 frame jump cut there; the MEASURED frames' phase breaks (and free runs) do (data term)
        wide = (F.lo[r0:r1] < F.raw_lo[r0:r1]) | (F.hi[r0:r1] > F.raw_hi[r0:r1])
        if wide.any():
            for v in sorted({self.S.dominant, 1.0}):
                cands.update(self._measured_steps(r0, r1, v * ratio))
        for k in range(r0 + 1, r1):
            if F.track[k] != F.track[k - 1]:
                cands.add(k)
        cands.update(self._audio_steps(r0, r1))
        return sorted(c for c in cands if r0 <= c <= r1)

    def _audio_steps(self, r0: int, r1: int) -> set[int]:
        h = self.hints
        out: set[int] = set()
        if h is None or len(getattr(h, "comp_t", [])) < 2:
            return out
        conf = h.confident(float(_cfg(self.cfg, "audio_min_conf", 1.3)))
        idx = np.nonzero(conf)[0]
        cf, rf = float(self.cf), float(self.rf)
        for i0, i1 in zip(idx[:-1], idx[1:]):
            dt = h.comp_t[i1] - h.comp_t[i0]
            if dt > 2 * getattr(h, "hop", 0.25) + 1e-6:
                continue
            sp = h.speed[i0] if np.isfinite(h.speed[i0]) else 1.0
            jump = (h.raw_t[i1] - h.raw_t[i0]) - sp * dt
            if abs(jump) * rf > 0.5:
                k = int(round((h.comp_t[i0] + h.comp_t[i1]) / 2 * cf))
                if r0 < k < r1:
                    out.add(k)
        return out

    def _range_arrays(self, q: int, p: int):
        F = self.F
        return np.arange(q, p), F.lo[q:p], F.hi[q:p]

    def _majority_track(self, ks: np.ndarray) -> int:
        if ks.size == 0:
            return -1
        vals, cnt = np.unique(self.F.track[ks], return_counts=True)
        return int(vals[np.argmax(cnt)])

    def eval_range(self, q: int, p: int, bound: float = math.inf) -> Any:
        """Cheapest model of [q, p) (None: infeasible; _PRUNED: feasible but costlier than bound)."""
        key = ("r", q, p, self.S.dominant)
        if key in self.S._cache:
            return self.S._cache[key]
        ks, lo, hi = self._range_arrays(q, p)
        tr = self._majority_track(ks)
        dm = self.F.relaxed[ks].copy()
        if dm.size:
            dm[0] = False
            dm[-1] = False
        m = self.S.fit(ks, lo, hi, q, dm, (q, p), tr, bool(self.F.flip[q]), pen=self._range_penalties(q, p),
                       bound=bound)
        if m is not _PRUNED:
            self.S._cache[key] = m
        return m

    def _range_penalties(self, q: int, p: int):
        """Data-term pairs of the DP range [q, p) (soft ranges F.lo/F.hi, unchanged during a DP pass), sliced
        from one precomputation over the whole timeline."""
        if self._gpen is None:
            F = self.F
            allk = np.arange(self.n, dtype=np.int64)
            gp = self.S.penalties(allk, F.lo.astype(np.int64), F.hi.astype(np.int64))
            self._gpen = gp if gp is not None else False
        if self._gpen is False:
            return None
        i, j, w = self._gpen
        a, b = np.searchsorted(i, q, side="left"), np.searchsorted(i, p, side="left")
        if b <= a:
            return None
        return i[a:b] - q, j[a:b], w[a:b]

    def _relaxed_break(self, q: int, p: int) -> bool:
        key = (q, p)
        if key in self.S._relax_cache:
            return self.S._relax_cache[key]
        ks, lo, hi = self._range_arrays(q, p)
        rel = self.F.relaxed[ks].copy()
        if rel.size:
            rel[-1] = False
        r = self.S.relaxed_infeasible(ks, lo, hi, q, rel)
        self.S._relax_cache[key] = r
        return r

    def dp_run(self, r0: int, r1: int) -> list[_Seg]:
        P = self.candidates(r0, r1)
        lam = self.S.l_cut
        max_fail = int(_cfg(self.cfg, "dp_max_consecutive_fail", 6))
        # best[p] = (cost, q, model, number of segments); equal costs (1e-9) -> fewer segments (a constant
        # non-snap speed whose regular 1-frame steps could also be read as equally many 1-frame jump cuts)
        best: dict[int, tuple[float, int | None, _Model | None, int]] = {r0: (0.0, None, None, 0)}
        for pi in range(1, len(P)):
            p = P[pi]
            bc, bn, arg = math.inf, 0, None
            fails = 0
            for qi in range(pi - 1, -1, -1):
                q = P[qi]
                prev, _pq, _pm, pn = best.get(q, (math.inf, None, None, 0))
                lamq = lam if q > r0 else 0.0
                bound = (bc - prev - lamq + 2e-9) if math.isfinite(prev) else -math.inf
                if bound < 0.0 and ("r", q, p, self.S.dominant) not in self.S._cache:
                    # nothing in [q, p) can improve p: skip the fit (counted as feasible, so the streak of
                    # infeasible ranges -- an optimisation only -- restarts)
                    fails = 0
                    continue
                m = self.eval_range(q, p, bound)
                if m is None:
                    # Sound break: infeasible even with every relaxed-droppable frame removed. Practical
                    # break: only the first frame of [q, p) can turn droppable when q moves left, so a
                    # long streak of infeasible ranges means a real model break was crossed.
                    fails += 1
                    if fails >= max_fail or self._relaxed_break(q, p):
                        break
                    continue
                fails = 0
                if m is _PRUNED or prev == math.inf:
                    continue
                c = prev + m.cost + lamq
                if c < bc - 1e-9 or (c <= bc + 1e-9 and pn + 1 < bn):
                    bc, bn, arg = c, pn + 1, (q, m)
            best[p] = (bc, arg[0] if arg else None, arg[1] if arg else None, bn)
        if best[r1][0] == math.inf:
            # cannot happen when the dominant-speed forward breaks are candidates; be safe anyway
            log.warning("segment: DP found no segmentation of [%d, %d); using the free-speed runs", r0, r1)
            pts = [r0] + [r[1] for r in self._free_runs(r0, r1, True)][:-1] + [r1]
            segs = []
            for a, b in zip(pts[:-1], pts[1:]):
                m = self.eval_range(a, b) or self._fixed_model(a, b, self.S.dominant)
                segs.append(self._raw_seg(a, b, m))
            return segs
        out = []
        p = r1
        while p != r0:
            _c, q, m, _n = best[p]
            out.append(self._raw_seg(q, p, m))
            p = q
        out.reverse()
        self.log("dp_run", comp_range=[r0, r1], evidence={
            "candidates": len(P), "cost": best[r1][0],
            "segments": [{"comp_in": s.a, "comp_out": s.b, "speed": s.model.v, "kind": s.model.kind,
                          "drops": s.model.drops, "data": round(float(s.model.data), 6)} for s in out]})
        return out

    def _fixed_model(self, a: int, b: int, v: float) -> _Model:
        ks, lo, hi = self._range_arrays(a, b)
        tr = self._majority_track(ks)
        m = self.S.fit(ks, lo, hi, a, np.zeros(ks.size, bool), (a, b), tr, bool(self.F.flip[a]), fixed_v=v)
        if m is None:
            sol = ps.solve_raw_in(ks, lo, hi, a, v, self.cf, self.rf) if ks.size else {}
            m = _Model(a, v, "fixed", self.S.l_uns, True, None, float("nan"), [], sol, tr, bool(self.F.flip[a]),
                       int(ks.size))
        return m

    def _raw_seg(self, a: int, b: int, m: _Model) -> _Seg:
        return _Seg("raw", a, b, model=m, flip=bool(self.F.flip[a]), track=m.track)

    # ---------------------------------------------------------------------------------------------
    # constraints & re-solving of a working segment
    # ---------------------------------------------------------------------------------------------
    def constraints(self, seg: _Seg) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        F = self.F
        ks = [k for k in range(seg.a, seg.b) if F.status[k] == Status.MATCH and bool(F.flip[k]) == seg.flip]
        cons = {k: (int(F.lo[k]), int(F.hi[k])) for k in ks}
        for k, (lo, hi) in seg.extra.items():
            if seg.a <= k < seg.b:
                cons[k] = (int(lo), int(hi))
        kk = np.array(sorted(cons), dtype=np.int64)
        lo = np.array([cons[k][0] for k in kk], dtype=np.int64)
        hi = np.array([cons[k][1] for k in kk], dtype=np.int64)
        return kk, lo, hi

    def refit(self, seg: _Seg, keep_v: bool = True) -> bool:
        """Re-solve a working segment after its range / constraints changed. Returns feasibility."""
        ks, lo, hi = self.constraints(seg)
        tr = self._majority_track(ks) if ks.size else seg.track
        dm = self.F.relaxed[ks].copy() if ks.size else np.zeros(0, bool)
        if dm.size:
            dm[0] = dm[-1] = False
        m = None
        if keep_v and seg.model is not None:
            m = self.S.fit(ks, lo, hi, seg.a, dm, (seg.a, seg.b), tr, seg.flip, fixed_v=seg.model.v)
            if m is not None:
                m.kind, m.unsnapped = seg.model.kind, seg.model.unsnapped
                m.cost += self.S.class_cost(seg.model.kind)
                m.v_ols = self.measured_speed(ks, lo, hi)
        if m is None:
            m = self.S.fit(ks, lo, hi, seg.a, dm, (seg.a, seg.b), tr, seg.flip)
        if m is None:
            v = seg.model.v if seg.model is not None else self.S.dominant
            sol = ps.solve_raw_in(ks, lo, hi, seg.a, v, self.cf, self.rf) if ks.size else {}
            m = _Model(seg.a, v, "fixed", self.S.l_uns, True, None, float("nan"), [], sol, tr, seg.flip,
                       int(ks.size))
            seg.model = m
            return False
        seg.model = m
        seg.track = tr
        return True

    def seg_cost(self, seg: _Seg) -> float:
        return seg.model.cost if seg.model is not None else 0.0

    def measured_speed(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
        """v_ols: robust slope of refine's MEASURED (argmax) frames, not of the soft-range midpoints."""
        if ks.size < 2:
            return float("nan")
        plo, phi = self.F.pristine(ks, lo, hi)
        return ps.estimate_speed(ks, plo, phi, self.cf, self.rf)

    def claimed_range(self, S: _Seg, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[float, float] | None:
        """Segment.speed_range: the speeds at which one line reproduces what the segment CLAIMS -- refine's
        measured range where the model shows it, the model frame where the segment re-assigns a frame --
        (the soft-range feasible range when that is empty, e.g. timing ties)."""
        if ks.size == 0:
            return None
        plo, phi = self.F.pristine(ks, lo, hi)
        if S.model is not None and math.isfinite(S.model.sol.get("raw_in", float("nan"))):
            j = np.asarray(self.pred(S, ks), dtype=np.int64)
            inside = (plo <= j) & (j <= phi)
            clo, chi = np.where(inside, plo, j), np.where(inside, phi, j)
            vr = ps.feasible_speed_range(ks, clo, chi, S.a, self.cf, self.rf)
            if vr is not None:
                return vr
        return self.S.vrange(ks, lo, hi, S.a)

    def exact_range(self, ks: np.ndarray, lo: np.ndarray, hi: np.ndarray, comp_in: int,
                    drops: Iterable[int] = ()) -> tuple[float, float] | None:
        """Speeds at which one line reproduces every measured (argmax-range) frame -- dropped frames
        excluded -- i.e. the residuals do not get worse (prompt 5.4); None when none does."""
        if ks.size == 0:
            return None
        keep = ~np.isin(ks, np.asarray(list(drops), dtype=np.int64)) if drops else np.ones(ks.size, bool)
        if keep.sum() == 0:
            return None
        plo, phi = self.F.pristine(ks[keep], lo[keep], hi[keep])
        return ps.feasible_speed_range(ks[keep], plo, phi, comp_in, self.cf, self.rf)

    # ---------------------------------------------------------------------------------------------
    # framing
    # ---------------------------------------------------------------------------------------------
    def framing(self, seg: _Seg, force: bool = False) -> dict:
        if seg.framing is not None and not force and seg.framing.get("_range") == (seg.a, seg.b):
            return seg.framing
        F, cfg = self.F, self.cfg
        ks = [k for k in range(seg.a, seg.b) if F.status[k] == Status.MATCH and bool(F.flip[k]) == seg.flip
              and F.sim(k) is not None]
        if any(k not in self.unreliable_sim for k in ks):
            ks = [k for k in ks if k not in self.unreliable_sim]
        if not ks:
            # nearest frame with a transform (placeholder-adjacent tiny segments)
            near = [k for k in range(max(0, seg.a - 30), min(self.n, seg.b + 30))
                    if F.sim(k) is not None and bool(F.flip[k]) == seg.flip]
            ks = sorted(near, key=lambda k: abs(k - seg.a))[:1]
        if not ks:
            fr = {"transform": Sim.identity().to_dict(), "keys": [], "easing": "linear", "animated": False,
                  "_range": (seg.a, seg.b), "notes": ["no transform measured"]}
            seg.framing = fr
            return fr
        step = max(1, int(_cfg(cfg, "framing_sample_step", 3)))
        samp = ks[::step]
        if samp[-1] != ks[-1]:
            samp.append(ks[-1])
        idx = np.array(samp)
        s, th, tx, ty = self._sim_arrays(idx)
        sim0 = Sim(float(np.median(s)), float(np.median(th)), float(np.median(tx)), float(np.median(ty)))
        # visible reference point: the RAW point at the box centre under the median Sim
        pref = sim0.inverse().apply([self.center])[0]
        c, sn = np.cos(np.radians(th)), np.sin(np.radians(th))
        Px = s * (c * pref[0] - sn * pref[1]) + tx
        Py = s * (sn * pref[0] + c * pref[1]) + ty
        s_spread = float((s.max() - s.min()) / np.median(s))
        p_spread = float(max(np.ptp(Px), np.ptp(Py)) + np.radians(np.ptp(th)) * self.box_r)
        rot_min = float(_cfg(cfg, "rotation_min_deg", 0.2))
        stable = (s_spread < float(_cfg(cfg, "framing_scale_spread", 0.003))
                  and p_spread < float(_cfg(cfg, "framing_pos_spread", 1.5)))
        notes = []
        if stable or idx.size < 2:
            t = self._derotate(sim0, pref, rot_min)
            fr = {"transform": t.to_dict(), "keys": [], "easing": "linear", "animated": False}
        else:
            # light smoothing: each interior sample -> least-squares line through it and its two neighbours
            # evaluated at its time (exact for linear motion whatever the sample spacing), then RDP
            tt = idx.astype(np.float64)

            def sm(a: np.ndarray) -> np.ndarray:
                if a.size < 3:
                    return a.copy()
                o = a.copy()
                t3 = np.stack([tt[:-2], tt[1:-1], tt[2:]], axis=1)
                y3 = np.stack([a[:-2], a[1:-1], a[2:]], axis=1)
                tm, ym = t3.mean(axis=1, keepdims=True), y3.mean(axis=1, keepdims=True)
                sl = ((t3 - tm) * (y3 - ym)).sum(axis=1) / np.maximum(((t3 - tm) ** 2).sum(axis=1), 1e-12)
                o[1:-1] = ym[:, 0] + sl * (tt[1:-1] - tm[:, 0])
                return o
            s2, th2, Px2, Py2 = sm(s), sm(th), sm(Px), sm(Py)
            pts = np.stack([idx.astype(np.float64), s2 / float(np.median(s)), Px2, Py2, th2], axis=1)
            tol = [float(_cfg(cfg, "rdp_scale_tol", 0.001)), float(_cfg(cfg, "rdp_pos_tol", 0.5)),
                   float(_cfg(cfg, "rdp_pos_tol", 0.5)), 0.05]
            keep = rdp(pts, tol)
            keys = []
            use_rot = bool(np.max(np.abs(th2)) > rot_min)
            for i in keep:
                thi = float(th2[i]) if use_rot else 0.0
                ci, si = math.cos(math.radians(thi)), math.sin(math.radians(thi))
                sc = float(s2[i])
                txi = float(Px2[i]) - sc * (ci * pref[0] - si * pref[1])
                tyi = float(Py2[i]) - sc * (si * pref[0] + ci * pref[1])
                keys.append({"comp_frame": int(idx[i]), "scale": float(sc), "rotation_deg": float(thi),
                             "tx": float(txi), "ty": float(tyi)})
            easing = _easing(idx, s2, Px2, Py2)
            fr = {"transform": {k: keys[0][k] for k in ("scale", "rotation_deg", "tx", "ty")}, "keys": keys,
                  "easing": easing, "animated": True}
            notes.append(f"animated framing: {len(keys)} keys, scale {s.min():.4f}->{s.max():.4f}, easing {easing}")
        fr["_range"] = (seg.a, seg.b)
        fr["notes"] = notes
        fr["spread"] = {"scale": s_spread, "pos_px": p_spread}
        seg.framing = fr
        return fr

    @staticmethod
    def _derotate(sim: Sim, pref: np.ndarray, rot_min: float) -> Sim:
        """Zero a rotation below rot_min while keeping the box-centre pre-image fixed."""
        if abs(sim.theta_deg) > rot_min:
            return sim
        p = sim.apply([pref])[0]
        return Sim(sim.s, 0.0, float(p[0] - sim.s * pref[0]), float(p[1] - sim.s * pref[1]))

    def sim_at(self, seg: _Seg, k: int) -> Sim:
        fr = self.framing(seg)
        if fr["keys"]:
            raw_w, raw_h = self.raw.full_size
            return interpolate_keys(fr["keys"], k, raw_w, raw_h)
        return Sim.from_dict(fr["transform"])

    # ---------------------------------------------------------------------------------------------
    # clean-up passes
    # ---------------------------------------------------------------------------------------------
    def _explains(self, seg: _Seg, k: int) -> tuple[bool, dict]:
        """Does seg's model explain comp frame k (a frame currently outside / at the edge of seg)?"""
        F = self.F
        j = int(self.pred(seg, k))
        ev: dict = {"k": k, "pred": j, "soft": [int(F.lo[k]), int(F.hi[k])]}
        if F.status[k] != Status.MATCH or bool(F.flip[k]) != seg.flip:
            return False, ev
        if F.lo[k] <= j <= F.hi[k]:
            return True, ev
        dfc = F.deficit(k, j)
        ev["deficit"] = dfc
        same_track = F.track[k] == seg.track
        if math.isfinite(dfc) and same_track:
            return dfc <= 5.0 * F.delta[k], ev
        if self.P.ok:
            own = F.sim(k)
            if own is not None and F.raw[k] >= 0:
                sc = self.P.zncc_set(k, [(j, self.sim_at(seg, k), seg.flip), (int(F.raw[k]), own, bool(F.flip[k]))])
                if sc is not None and np.all(np.isfinite(sc)):
                    ev["score_model"], ev["score_own"] = float(sc[0]), float(sc[1])
                    return bool(sc[0] >= sc[1] - 3.0 * F.delta[k]), ev
        return False, ev

    def _widen(self, k: int, j: int, why: str) -> None:
        F = self.F
        F.lo[k] = min(F.lo[k], j)
        F.hi[k] = max(F.hi[k], j)
        F.touched[k] = why

    def merge_tiny(self, segs: list[_Seg]) -> list[_Seg]:
        """1-2 frame raw segments: merge into a neighbour whose model explains them (matching error),
        otherwise keep them as verified flash cuts."""
        changed = True
        while changed:
            changed = False
            for i, t in enumerate(segs):
                if t.kind != "raw" or t.length > 2:
                    continue
                sides = (-1, 1)
                if len(self.pinfos) > 1:     # layout periods: a neighbour with the same framing first (D1)
                    sides = tuple(sorted(sides, key=lambda sd: not (
                        0 <= i + sd < len(segs) and segs[i + sd].kind == "raw" and self._framing_close(t, segs[i + sd]))))
                for side in sides:
                    ni = i + side
                    if not (0 <= ni < len(segs)):
                        continue
                    nb = segs[ni]
                    if nb.kind != "raw" or nb.flip != t.flip or nb.length <= t.length:
                        continue
                    if (side < 0 and nb.b != t.a) or (side > 0 and nb.a != t.b):
                        continue
                    if not self._same_period(min(nb.a, t.a), max(nb.b, t.b)) and \
                            not self._framing_close(t, nb):
                        # a 1-2 frame sliver across a layout period boundary is merged only into a neighbour with
                        # the same framing (the detected boundary is off by a frame or two); otherwise the
                        # period split stands (D1)
                        continue
                    res = [self._explains(nb, k) for k in range(t.a, t.b)]
                    if all(r[0] for r in res):
                        trial = _Seg("raw", min(nb.a, t.a), max(nb.b, t.b), model=nb.model, flip=nb.flip,
                                     track=nb.track, extra=dict(nb.extra))
                        saved = {k: (int(self.F.lo[k]), int(self.F.hi[k])) for k in range(t.a, t.b)}
                        for k in range(t.a, t.b):
                            self._widen(k, int(self.pred(nb, k)), "tiny_segment_merged")
                        if self.refit(trial, keep_v=True):
                            self.log("merge_tiny_segment", comp_range=[t.a, t.b], evidence={
                                "into": [nb.a, nb.b], "checks": [r[1] for r in res]})
                            segs[min(i, ni)] = trial
                            del segs[max(i, ni)]
                            changed = True
                            break
                        for k, (lo, hi) in saved.items():
                            self.F.lo[k], self.F.hi[k] = lo, hi
                            self.F.touched.pop(k, None)
                if changed:
                    break
        for t in segs:
            if t.kind == "raw" and t.length <= 2:
                self.log("flash_cut_verified", comp_range=[t.a, t.b], evidence={
                    "speed": t.model.v, "raw": [int(self.F.raw[k]) for k in range(t.a, t.b)],
                    "score": [float(self.F.score[k]) for k in range(t.a, t.b)]})
        return segs

    def _framing_close(self, t: _Seg, nb: _Seg) -> bool:
        """The measured framing of tiny segment t matches its neighbour nb at their common boundary."""
        it = [k for k in range(t.a, t.b) if self.F.sim(k) is not None]
        side = range(nb.b - 1, nb.a - 1, -1) if nb.b <= t.a else range(nb.a, nb.b)
        ib = [k for k in side if self.F.sim(k) is not None][:1]
        if not it or not ib or t.flip != nb.flip:
            return False
        ds, dp = self._change(np.array(it[:1]), np.array(ib))
        return bool(ds[0] <= float(_cfg(self.cfg, "punch_scale_step", 0.01))
                    and dp[0] <= float(_cfg(self.cfg, "punch_pos_step", 4.0)))

    def _compatible(self, A: _Seg, B: _Seg) -> bool:
        if A.kind != "raw" or B.kind != "raw" or A.flip != B.flip:
            return False
        if not self._same_period(min(A.a, B.a), max(A.b, B.b)):
            return False            # D1: never merge across a layout period boundary
        ka, kb = A.b - 1, B.a
        ia = [k for k in range(A.b - 1, A.a - 1, -1) if self.F.sim(k) is not None][:1]
        ib = [k for k in range(B.a, B.b) if self.F.sim(k) is not None][:1]
        if not ia or not ib:
            return True
        ds, dp = self._change(np.array(ia), np.array(ib))
        del ka, kb
        return bool(ds[0] <= float(_cfg(self.cfg, "punch_scale_step", 0.01))
                    and dp[0] <= float(_cfg(self.cfg, "punch_pos_step", 4.0)))

    def merge_adjacent(self, segs: list[_Seg]) -> list[_Seg]:
        """Merge adjacent compatible raw segments when one model explains both more cheaply."""
        i = 0
        while i + 1 < len(segs):
            A, B = segs[i], segs[i + 1]
            if A.b == B.a and self._compatible(A, B):
                trial = _Seg("raw", A.a, B.b, model=A.model, flip=A.flip, track=A.track,
                             extra={**A.extra, **B.extra})
                if self.refit(trial, keep_v=False) and \
                        trial.model.cost <= self.seg_cost(A) + self.seg_cost(B) + self.S.l_cut - 1e-9:
                    self.log("merge_adjacent", comp_range=[A.a, B.b], evidence={
                        "costs": [self.seg_cost(A), self.seg_cost(B), trial.model.cost], "speed": trial.model.v})
                    segs[i:i + 2] = [trial]
                    continue
            i += 1
        return segs

    def merge_continuous(self, segs: list[_Seg]) -> list[_Seg]:
        """Two adjacent segments at the same speed on (nearly) the same line, same flip and framing, are not
        separated by an edit when ONE line explains both allowing runs of at most 2 consecutive near-miss
        low-margin frames (the DP only tolerates isolated ones). A genuine 1-frame-skip jump cut is untouched:
        every frame after the skip violates the union's line, which is not a short run."""
        i = 0
        while i + 1 < len(segs):
            A, B = segs[i], segs[i + 1]
            if not (A.kind == "raw" and B.kind == "raw" and A.b == B.a and A.model is not None and
                    B.model is not None and abs(A.model.v - B.model.v) <= 1e-12 and self._compatible(A, B)
                    and abs(A.model.pos(B.a) - B.model.pos(B.a)) < 1.5):
                i += 1
                continue
            trial = _Seg("raw", A.a, B.b, model=A.model, flip=A.flip, track=A.track, extra={**A.extra, **B.extra})
            ks, lo, hi = self.constraints(trial)
            dm = self.F.relaxed[ks].copy()
            if dm.size:
                dm[0] = dm[-1] = False
            m = self.S.fit(ks, lo, hi, trial.a, dm, (trial.a, trial.b), A.track, A.flip, fixed_v=A.model.v,
                           max_run=2)
            if m is not None:
                m.kind, m.unsnapped = A.model.kind, A.model.unsnapped
                m.cost += self.S.class_cost(A.model.kind)
            # a cut that ONE line at the same speed also fits (inside the soft ranges) is justified by the data
            # term alone. Random +-1 argmax noise inside wide soft ranges can mimic a 1-frame skip around a
            # boundary: such a cut changes few frames, or changes more than it fixes. It needs lambda_phase_cut
            # more evidence; a genuine skip / retime step moves every frame on one side to the measured frame.
            extra = 0.0
            if m is not None:
                changed, fixed = self._cut_evidence(A, B, m)
                if changed < int(_cfg(self.cfg, "phase_cut_min_frames", 6)) or fixed < 0.75 * changed:
                    extra = min(self.S.l_phase, max(0.0, m.data - A.model.data - B.model.data))
            if m is not None and m.cost <= self.seg_cost(A) + self.seg_cost(B) + self.S.l_cut + extra - 1e-9:
                trial.model = m
                for k in m.drops:   # later refits use the isolated-only rule: fold the runs into the ranges
                    self._widen(k, int(self.pred(trial, k)), "continuous_merge")
                self.refit(trial, keep_v=True)
                self.log("merge_continuous", comp_range=[A.a, B.b], evidence={
                    "cut_removed": B.a, "speed": m.v, "drops": m.drops,
                    "costs": [self.seg_cost(A), self.seg_cost(B), m.cost], "data": [A.model.data, B.model.data, m.data]})
                segs[i:i + 2] = [trial]
                continue
            i += 1
        return segs

    def _cut_evidence(self, A: _Seg, B: _Seg, m: _Model) -> tuple[int, int]:
        """(changed, fixed): the comp frames whose RAW frame differs between the segment models of A and B and
        the single model m of their union, and how many more of them the two models show inside refine's
        measured range than m does (what the cut A|B changes in the output, and whether it agrees with the
        measurement)."""
        F = self.F
        U = _Seg("raw", A.a, B.b, model=m, flip=A.flip, track=A.track)
        ks = np.arange(A.a, B.b)
        split = np.concatenate([np.asarray(self.pred(A, ks[ks < B.a])), np.asarray(self.pred(B, ks[ks >= B.a]))])
        union = np.asarray(self.pred(U, ks))
        ch = split != union
        rlo, rhi = F.raw_lo[ks], F.raw_hi[ks]
        ok_s = (rlo <= split) & (split <= rhi)
        ok_u = (rlo <= union) & (union <= rhi)
        return int(ch.sum()), int(ok_s[ch].sum()) - int(ok_u[ch].sum())

    def _skip_link(self, A: _Seg, B: _Seg) -> bool:
        """A hard cut between two pieces at the same speed whose lines differ by one RAW frame (0.5..1.5: a
        1-frame skip / repeat -- not a sub-frame phase jitter), same flip, framing and layout period."""
        return bool(A.kind == "raw" and B.kind == "raw" and A.b == B.a and B.trans_in is None and A.trans_out is None
                    and B.cut_ambiguity is None and A.model is not None and B.model is not None
                    and abs(A.model.v - B.model.v) <= 1e-12 and self._compatible(A, B)
                    and 0.5 <= abs(A.model.pos(B.a) - B.model.pos(B.a)) < 1.5)

    def merge_retime_chains(self, segs: list[_Seg]) -> list[_Seg]:
        """time-math F2: >= 2 one-frame skips / repeats in a row that ONE line (any speed) reproduces at least as
        well as the pieces do are a constant retime (1.02-1.05x: its floor pattern steps by 2 every 1/(u-1)
        frames), not a run of equally many 1-frame jump cuts that happen to fall exactly where that line
        steps. A single 1-frame skip stays a cut (DESIGN: never a fake 1.02-1.05x speed)."""
        i = 0
        while i < len(segs):
            j = i
            while j + 1 < len(segs) and self._skip_link(segs[j], segs[j + 1]):
                j += 1
            if j - i < 2:
                i = j + 1
                continue
            merged = False
            for L in range(j - i + 1, 2, -1):           # longest sub-chain first, leftmost first
                for a in range(i, j - L + 2):
                    chain = segs[a:a + L]
                    trial = _Seg("raw", chain[0].a, chain[-1].b, model=chain[0].model, flip=chain[0].flip,
                                 track=chain[0].track, extra={k: v for c in chain for k, v in c.extra.items()},
                                 notes=[n for c in chain for n in c.notes])
                    if not self.refit(trial, keep_v=False):
                        continue
                    d_union = trial.model.data + self.S.l_drop * len(trial.model.drops)
                    d_parts = sum(c.model.data + self.S.l_drop * len(c.model.drops) for c in chain)
                    if abs(trial.model.v - chain[0].model.v) <= 1e-12 or d_union > d_parts + 1e-9:
                        continue
                    # a retime accounts for its steps: all in one direction, and the union line drifts away from
                    # the pieces' speed by as many RAW frames as there are steps
                    steps = [B.model.pos(B.a) - A.model.pos(B.a) for A, B in zip(chain[:-1], chain[1:])]
                    drift = (trial.model.v - chain[0].model.v) * self.S.ratio * (trial.b - trial.a)
                    if not (all(x > 0 for x in steps) or all(x < 0 for x in steps)) or \
                            abs(drift - sum(round(x) for x in steps)) > 1.0:
                        continue
                    self.log("merge_retime_chain", comp_range=[trial.a, trial.b], evidence={
                        "pieces": [[c.a, c.b] for c in chain], "piece_speed": chain[0].model.v,
                        "speed": trial.model.v, "kind": trial.model.kind, "data": [d_parts, d_union]})
                    segs[a:a + L] = [trial]
                    merged = True
                    break
                if merged:
                    break
            if not merged:
                i = j + 1
        return segs

    def absorb_none(self, segs: list[_Seg]) -> list[_Seg]:
        """Short NONE runs between two parts of one continuous model: absorb them when the model frame
        (or a blend of two neighbouring RAW frames: frame-blend retiming) matches (matching failures)."""
        if not self.P.ok:
            return segs
        F, cfg = self.F, self.cfg
        max_len = max(2, int(_cfg(cfg, "transition_search", 20)) // 2)
        thr = float(_cfg(cfg, "match_thresh", 0.9)) - float(_cfg(cfg, "anchor_zncc_slack", 0.05))
        i = 0
        while i + 2 < len(segs):
            A, N, B = segs[i], segs[i + 1], segs[i + 2]
            if not (N.kind == "none" and A.kind == "raw" and B.kind == "raw" and A.b == N.a and N.b == B.a
                    and N.length <= max_len and self._compatible(A, B)):
                i += 1
                continue
            trial = _Seg("raw", A.a, B.b, model=A.model, flip=A.flip, track=A.track, extra={**A.extra, **B.extra})
            if not self.refit(trial, keep_v=False) or \
                    trial.model.cost > self.seg_cost(A) + self.seg_cost(B) + 2 * self.S.l_cut:
                i += 1
                continue
            acc = []
            for k in range(N.a, N.b):
                j = int(self.pred(trial, k))
                sim = self.sim_at(trial, k)
                sc = self.P.zncc_set(k, [(j, sim, trial.flip)])
                if sc is not None and math.isfinite(sc[0]) and sc[0] >= thr:
                    acc.append((k, j, j, "single", float(sc[0]), j))
                    continue
                ok = False
                for j0 in (j - 1, j):
                    bf = self.P.blend_fit(k, [(j0, sim, trial.flip)], [(j0 + 1, sim, trial.flip)])
                    if bf and "zfit" in bf and bf["zfit"] >= float(_cfg(cfg, "match_thresh", 0.9)) \
                            and 0.1 < bf["alpha_a"] < 0.9:
                        heavier = j0 if bf["alpha_a"] >= 0.5 else j0 + 1
                        acc.append((k, j0, j0 + 1, "blend", float(bf["zfit"]), heavier))
                        ok = True
                        break
                if not ok:
                    break
            if len(acc) != N.length:
                i += 1
                continue
            for k, lo, hi, how, sc, jr in acc:
                F.status[k] = Status.MATCH
                F.raw[k] = jr
                F.lo[k], F.hi[k] = lo, hi
                F.raw_lo[k], F.raw_hi[k] = F.raw[k], F.raw[k]
                F.flip[k] = trial.flip
                F.track[k] = trial.track
                F.low_margin[k] = True
                F.touched[k] = f"none_absorbed_{how}"
                if how == "blend":
                    trial.blend_frames.append(k)
            self.refit(trial, keep_v=True)
            trial.notes.append(f"frames {N.a}-{N.b - 1} unmatched by refine, absorbed "
                               f"({', '.join(a[3] for a in acc)})")
            self.log("absorb_none", comp_range=[N.a, N.b], evidence={"frames": [
                {"k": a[0], "raw": [a[1], a[2]], "how": a[3], "score": a[4], "m": a[5]} for a in acc]})
            segs[i:i + 3] = [trial]
        return segs

    # ---------------------------------------------------------------------------------------------
    # speed-only cuts
    # ---------------------------------------------------------------------------------------------
    def speed_only_cuts(self, segs: list[_Seg]) -> None:
        F = self.F
        for A, B in zip(segs[:-1], segs[1:]):
            if not (A.kind == "raw" and B.kind == "raw" and A.b == B.a and A.flip == B.flip):
                continue
            if abs(A.model.v - B.model.v) <= 1e-9 or not self._compatible(A, B):
                continue
            c = B.a
            if abs(A.model.pos(c) - B.model.pos(c)) >= 1.5:
                continue
            # valid cut positions: A's model explains [c, c') / B's explains [c', c)
            cmax = c
            while cmax < B.b - 1 and F.status[cmax] == Status.MATCH and \
                    F.lo[cmax] <= self.pred(A, cmax) <= F.hi[cmax]:
                cmax += 1
            cmin = c
            while cmin > A.a + 1 and F.status[cmin - 1] == Status.MATCH and \
                    F.lo[cmin - 1] <= self.pred(B, cmin - 1) <= F.hi[cmin - 1]:
                cmin -= 1
            if cmin == c and cmax == c:
                continue     # neither model explains the other's frames: an ordinary (hard) cut
            du = A.model.sol["u"] - B.model.sol["u"]
            kstar = c + (B.model.pos(c) - A.model.pos(c)) / du if abs(du) > 1e-12 else c
            cnew = int(min(max(round(kstar), cmin), cmax))
            if cnew != c:
                oldA, oldB = (A.a, A.b, A.model), (B.a, B.b, B.model)
                A.b, B.a = cnew, cnew
                if not (self.refit(A, keep_v=True) and self.refit(B, keep_v=True)):
                    A.a, A.b, A.model = oldA
                    B.a, B.b, B.model = oldB
                    cnew = c
            B.cut_ambiguity = [int(cmin), int(cmax)]
            B.notes.append(f"speed-only cut (no RAW jump): cut may lie anywhere in [{cmin}, {cmax}]")
            self.log("speed_only_cut", comp_frame=int(B.a), evidence={
                "speeds": [A.model.v, B.model.v], "intersection": kstar, "window": [cmin, cmax], "dp_cut": c})

    # ---------------------------------------------------------------------------------------------
    # transitions
    # ---------------------------------------------------------------------------------------------
    def transitions(self, segs: list[_Seg]) -> list[_Seg]:
        if not self.P.ok:
            return segs
        i = 0
        while i < len(segs):
            A = segs[i]
            if A.kind != "raw" or A.length < 2:
                i += 1
                continue
            # next major raw segment within transition_search, with only NONE / tiny raw between
            j = i + 1
            gap_ok = True
            while j < len(segs) and segs[j].a - A.b <= self.ts:
                if segs[j].kind == "raw" and segs[j].length > 2:
                    break
                if segs[j].kind not in ("none", "raw"):
                    gap_ok = False
                    break
                j += 1
            if not gap_ok or j >= len(segs) or segs[j].kind != "raw" or segs[j].a - A.b > self.ts:
                i += 1
                continue
            B = segs[j]
            res = self._fit_crossfade(A, B)
            if res is None:
                i += 1
                continue
            O, D, info = res
            inner = segs[i + 1:j]
            if any(s.kind == "raw" and not (O <= s.a and s.b <= O + D) for s in inner) or \
                    any(s.kind == "none" and not (O <= s.a and s.b <= O + D + 1) for s in inner) or \
                    not (A.a < O and O + D < B.b) or not (O <= B.a and A.b <= O + D):
                self.log("crossfade_rejected", comp_range=[A.b, B.a], evidence={"O": O, "D": D, **info})
                i += 1
                continue
            self._apply_crossfade(A, B, O, D, info)
            segs[i + 1:j] = []
            i += 1
        return segs

    def _fit_crossfade(self, A: _Seg, B: _Seg):
        F, cfg = self.F, self.cfg
        rel = float(_cfg(cfg, "blend_rel", 0.5))
        k0 = max(A.a + 1, A.b - self.ts)
        k1 = min(B.b - 1, B.a + self.ts)
        stop_after = int(_cfg(cfg, "transition_stop_after", 3))

        def row(k: int) -> dict | None:
            ja, jb = int(self.pred(A, k)), int(self.pred(B, k))
            sa, sb = self.sim_at(A, k), self.sim_at(B, k)
            a_items = [(ja + d, sa, A.flip) for d in (-1, 0, 1) if self.raw.has(ja + d)]
            b_items = [(jb + d, sb, B.flip) for d in (-1, 0, 1) if self.raw.has(jb + d)]
            if not a_items or not b_items:
                return None
            r = self.P.blend_fit(k, a_items, b_items)
            if r is None or "zfit" not in r:
                return None
            best_single = float(np.nanmax(r["single"]))
            # alpha from the gain-independent fit (a graded / softer repost biases the constrained one and
            # tilts the ramp by a frame or two, review R2-1); the relative blend test keeps the constrained
            # fit's zfit
            af = r.get("alpha_a_free", float("nan"))
            free_ok = af is not None and math.isfinite(af)
            alpha_b = 1.0 - (af if free_ok else r["alpha_a"])
            is_blend = free_ok and (0.02 < alpha_b < 0.98) and (1.0 - r["zfit"]) <= rel * (1.0 - best_single)
            return {"k": k, "alpha_b": alpha_b, "zfit": r["zfit"], "single": best_single, "blend": is_blend,
                    "a_j": a_items[r["ia"]][0], "b_j": b_items[r["ib"]][0]}

        # scan the gap (and the frames at the DP cut) first, then outwards until `stop_after` consecutive
        # non-blend frames on each side (a crossfade is contiguous and contains the cut)
        rows_d: dict[int, dict] = {}
        core = list(range(max(k0, A.b - 1), min(k1, B.a) + 1))
        for k in core:
            r = row(k)
            if r is not None:
                rows_d[k] = r
        for direction, start, end in ((-1, (core[0] if core else A.b) - 1, k0 - 1),
                                      (1, (core[-1] if core else B.a) + 1, k1 + 1)):
            miss = 0
            for k in range(start, end, direction):
                r = row(k)
                if r is not None:
                    rows_d[k] = r
                if r is None or not r["blend"]:
                    miss += 1
                    if miss >= stop_after:
                        break
                else:
                    miss = 0
        rows = [rows_d[k] for k in sorted(rows_d)]
        bl = [r for r in rows if r["blend"]]
        if not bl:
            return None
        # largest cluster of blend frames (gaps <= 1 frame), nearest the cut
        clusters: list[list[dict]] = [[bl[0]]]
        for r in bl[1:]:
            if r["k"] - clusters[-1][-1]["k"] <= 2:
                clusters[-1].append(r)
            else:
                clusters.append([r])
        mid = (A.b + B.a) / 2.0
        cl = max(clusters, key=lambda c: (len(c), -abs(np.mean([r["k"] for r in c]) - mid)))
        ks = np.array([r["k"] for r in cl], dtype=np.float64)
        al = np.array([r["alpha_b"] for r in cl], dtype=np.float64)
        if ks.size >= 2:
            slope, icpt = np.polyfit(ks, al, 1)
            if slope <= 1e-6:
                return None
            O = int(round(-icpt / slope))
            D = int(round(1.0 / slope))
        else:
            k1f, a1 = float(ks[0]), float(al[0])
            if cl[0]["single"] >= float(_cfg(cfg, "match_thresh", 0.9)):
                # one isolated "blend" frame that still matches a single source well: not evidence enough
                # for a transition (typical of a jump cut inside one shot, where A and B look alike)
                return None
            if abs(a1 - 0.5) < 0.2:
                D = 2
                O = int(k1f) - 1
            elif a1 < 0.5:
                D = max(2, int(round(1.0 / a1)))
                O = int(k1f) - 1
            else:
                D = max(2, int(round(1.0 / (1.0 - a1))))
                O = int(k1f) - (D - 1)
        if D < 2:
            return None
        lin = (ks - O) / D
        resid = float(np.max(np.abs(lin - al)))
        info = {"blend_frames": [int(r["k"]) for r in cl], "alpha_measured": [round(float(a), 4) for a in al],
                "resid_max": resid, "rows": [{k_: (round(v_, 5) if isinstance(v_, float) else v_)
                                              for k_, v_ in r.items()} for r in rows]}
        if resid > 0.25:
            self.log("crossfade_rejected_fit", comp_range=[A.b, B.a], evidence={"O": O, "D": D, **info})
            return None
        info["rows_by_k"] = {r["k"]: r for r in rows}
        return O, D, info

    def _apply_crossfade(self, A: _Seg, B: _Seg, O: int, D: int, info: dict) -> None:
        F = self.F
        rows = info.pop("rows_by_k")
        resid = info["resid_max"]
        meas = dict(zip(info["blend_frames"], info["alpha_measured"]))
        alpha = [(k - O) / D for k in range(O, O + D)]
        if resid > 0.05:   # not linear: keep the measured curve (linear fill where unmeasured)
            alpha = [float(meas.get(k, (k - O) / D)) for k in range(O, O + D)]
            alpha[0] = 0.0
        tr = {"type": "crossfade", "duration_frames": int(D), "alpha": [round(float(a), 6) for a in alpha],
              "color": None, "notes": f"O={O} D={D}; measured alpha_B {info['alpha_measured']} at frames "
                                      f"{info['blend_frames']} (max dev from linear {resid:.3f})"}
        old = (A.b, B.a)
        A.b, B.a = O + D, O
        for k in range(O, O + D):
            F.status[k] = Status.BLEND
            aB = (k - O) / D
            r = rows.get(k)
            if r is not None and 1 - aB >= 0.3:
                A.extra[k] = (int(r["a_j"]), int(r["a_j"]))
            if r is not None and aB >= 0.3:
                B.extra[k] = (int(r["b_j"]), int(r["b_j"]))
        okA, okB = self.refit(A), self.refit(B)
        if not (okA and okB):
            A.extra = {k: v for k, v in A.extra.items() if not (O <= k < O + D)}
            B.extra = {k: v for k, v in B.extra.items() if not (O <= k < O + D)}
            self.refit(A)
            self.refit(B)
            tr["notes"] += "; blend-frame constraints conflicted and were dropped"
        A.trans_out = dict(tr)
        B.trans_in = dict(tr)
        B.notes.append(f"crossfade in: O={O}, D={D} frames (B's frame at {O} inferred, invisible)")
        self.log("crossfade", comp_frame=int(O), evidence={"O": O, "D": D, "dp_cut": list(old), **info})

    # ---------------------------------------------------------------------------------------------
    # uniform runs: flash / dip
    # ---------------------------------------------------------------------------------------------
    def _fade(self, S: _Seg, ks: Iterable[int], ref_ks: Iterable[int]) -> dict[int, float]:
        """Solid opacity 1 - g_k/g_ref per frame k from single-source gains under S's model."""
        if not self.P.ok:
            return {}
        gains = {}
        for k in list(ks) + list(ref_ks):
            j = int(self.pred(S, k))
            if not self.raw.has(j):
                continue
            r = self.P.blend_fit(k, [(j, self.sim_at(S, k), S.flip)], [])
            if r is not None:
                gains[k] = float(r["gains"][0])
        ref = [gains[k] for k in ref_ks if k in gains]
        if not ref:
            return {}
        g0 = float(np.median(ref))
        if abs(g0) < 1e-6:
            return {}
        return {k: 1.0 - gains[k] / g0 for k in ks if k in gains}

    @staticmethod
    def _fade_ok(fr: list[int], a_arr: np.ndarray, O: int, D: int) -> bool:
        """A measured fade is a linear ramp alpha(k) = (k - O)/D whose visible frames are exactly the
        faded ones (D - 1 of them, +-1) and which reaches a clearly visible opacity."""
        if D < 2 or abs(D - (len(fr) + 1)) > 1:
            return False
        lin = (np.asarray(fr, float) - O) / D
        return bool(np.max(np.abs(lin - a_arr)) <= 0.15 and np.max(np.minimum(a_arr, 1 - a_arr)) >= 0.2)

    def uniform_runs(self, segs: list[_Seg]) -> list[_Seg]:
        F = self.F
        for i, U in enumerate(segs):
            if U.kind != "uniform":
                continue
            lum = float(np.nanmedian(F.mean[U.a:U.b])) if np.isfinite(F.mean[U.a:U.b]).any() else 0.0
            g = int(min(255, max(0, round(lum))))
            U.color = f"#{g:02x}{g:02x}{g:02x}"
            A = segs[i - 1] if i > 0 and segs[i - 1].kind == "raw" and segs[i - 1].b == U.a else None
            B = segs[i + 1] if i + 1 < len(segs) and segs[i + 1].kind == "raw" and segs[i + 1].a == U.b else None
            fo = fi = None
            if A is not None and A.length > 4:
                ks = list(range(max(A.a + 3, U.a - self.ts), U.a))
                ref = list(range(max(A.a, U.a - self.ts - 6), max(A.a + 1, U.a - self.ts)))
                al = self._fade(A, ks, ref or [A.a])
                fr = sorted(k for k, a in al.items() if a > 0.03)
                fr = [k for k in fr if all(kk in fr for kk in range(k, U.a))]  # contiguous up to the dip
                if fr:
                    a_arr = np.array([al[k] for k in fr])
                    slope = float(np.polyfit(np.array(fr, float), a_arr, 1)[0]) if len(fr) >= 2 else a_arr[0]
                    D = int(max(1, round(1.0 / slope))) if slope > 1e-6 else len(fr) + 1
                    O = U.a - D
                    if self._fade_ok(fr, a_arr, O, D):
                        fo = (O, D, {k: round(al[k], 4) for k in fr})
            if B is not None and B.length > 4:
                ks = list(range(U.b, min(B.b - 3, U.b + self.ts)))
                ref = list(range(min(B.b - 1, U.b + self.ts), min(B.b, U.b + self.ts + 6)))
                al = self._fade(B, ks, ref or [B.b - 1])
                fr = sorted(k for k, a in al.items() if a > 0.03)
                fr = [k for k in fr if all(kk in fr for kk in range(U.b, k + 1))]
                if fr:
                    a_arr = np.array([1.0 - al[k] for k in fr])   # incoming opacity of B
                    slope = float(np.polyfit(np.array(fr, float), a_arr, 1)[0]) if len(fr) >= 2 else a_arr[0]
                    D = int(max(1, round(1.0 / slope))) if slope > 1e-6 else len(fr) + 1
                    O = U.b - 1
                    if self._fade_ok(fr, a_arr, O, D):
                        fi = (O, D, {k: round(1.0 - al[k], 4) for k in fr})
            ttype = "dip_black" if g < 32 else ("dip_white" if g > 223 else "dip_color")
            if fo is None and fi is None:
                U.kind = "flash" if U.length <= 2 else "dip"
                if U.kind == "dip":
                    U.notes.append("uniform colour hold with hard cuts")
                self.log("uniform_run", comp_range=[U.a, U.b], evidence={"kind": U.kind, "color": U.color})
                continue
            U.kind = "dip"
            if fo is not None:
                O, D, meas = fo
                t = {"type": ttype, "duration_frames": D, "alpha": [round((k - O) / D, 6) for k in range(O, O + D)],
                     "color": U.color, "notes": f"fade out of S at O={O}, D={D}; measured {meas}"}
                U.a = O
                U.trans_in = t
                A.trans_out = dict(t)
            if fi is not None:
                O, D, meas = fi
                t = {"type": ttype, "duration_frames": D, "alpha": [round((k - O) / D, 6) for k in range(O, O + D)],
                     "color": U.color, "notes": f"fade in at O={O}, D={D}; measured {meas}"}
                U.b = O + D
                U.trans_out = t
                B.trans_in = dict(t)
                B.a = O
                self.refit(B)
            self.log("dip", comp_range=[U.a, U.b], evidence={"color": U.color, "fade_out": fo and fo[:2],
                                                             "fade_in": fi and fi[:2]})
        return segs

    # ---------------------------------------------------------------------------------------------
    # criterion 2
    # ---------------------------------------------------------------------------------------------
    def _side_scores(self, A: _Seg, B: _Seg, k: int) -> tuple[float, float] | None:
        """(score of frame k under A's model, under B's model)."""
        F = self.F
        ja, jb = int(self.pred(A, k)), int(self.pred(B, k))
        if self.P.ok:
            sc = self.P.zncc_set(k, [(ja, self.sim_at(A, k), A.flip), (jb, self.sim_at(B, k), B.flip)])
            if sc is not None and np.all(np.isfinite(sc)):
                return float(sc[0]), float(sc[1])
        # pixel-free fallback: candidate vector (only meaningful under the same transform)
        if A.flip == B.flip and F.sim(k) is not None:
            sa, sb = F.cand_score(k, ja), F.cand_score(k, jb)
            if math.isfinite(sa) and math.isfinite(sb):
                sA, sB = self.sim_at(A, k), self.sim_at(B, k)
                if abs(sA.s / sB.s - 1) < 1e-3 and math.hypot(sA.tx - sB.tx, sA.ty - sB.ty) < 1.0:
                    return sa, sb
        return None

    def _same_view(self, A: _Seg, B: _Seg, k: int) -> bool:
        """Both segment models show the same RAW frame with the same flip and framing at comp frame k."""
        if A.flip != B.flip or A.model is None or B.model is None:
            return False
        if int(self.pred(A, k)) != int(self.pred(B, k)) or int(self.pred(A, k)) < 0:
            return False
        sa, sb = self.sim_at(A, k), self.sim_at(B, k)
        return bool(abs(sa.s / sb.s - 1.0) <= 1e-3 and math.hypot(sa.tx - sb.tx, sa.ty - sb.ty) <= 1.0
                    and abs(sa.theta_deg - sb.theta_deg) <= 0.05)

    def _phantom(self, A: _Seg, B: _Seg) -> bool:
        """A hard cut with no discontinuity in m(k): same speed, and both models show the same RAW frame and
        framing on both sides of the boundary (only speed-only cuts, which carry cut_ambiguity, may agree)."""
        return bool(A.kind == "raw" and B.kind == "raw" and A.b == B.a and B.trans_in is None
                    and B.cut_ambiguity is None and A.model is not None and B.model is not None
                    and abs(A.model.v - B.model.v) <= 1e-12 and self._compatible(A, B)
                    and self._same_view(A, B, B.a - 1) and self._same_view(A, B, B.a))

    def merge_phantom_cuts(self, segs: list[_Seg]) -> list[_Seg]:
        """verification-honesty F3: after criterion-2 moves, merge neighbours whose models agree at the
        boundary (a cut must be a discontinuity of m(k)); the union keeps A's incoming and B's outgoing
        transition. Refit at the common speed with the isolated-frame rule, then with runs of 2."""
        i = 0
        while i + 1 < len(segs):
            A, B = segs[i], segs[i + 1]
            if not self._phantom(A, B):
                i += 1
                continue
            trial = _Seg("raw", A.a, B.b, model=A.model, flip=A.flip, track=A.track, extra={**A.extra, **B.extra},
                         trans_in=A.trans_in, trans_out=B.trans_out, notes=A.notes + B.notes,
                         cut_ambiguity=A.cut_ambiguity, uncertain=A.uncertain or B.uncertain,
                         blend_frames=sorted(set(A.blend_frames) | set(B.blend_frames)))
            ok = self.refit(trial, keep_v=True)
            if not ok:
                ks, lo, hi = self.constraints(trial)
                dm = self.F.relaxed[ks].copy()
                if dm.size:
                    dm[0] = dm[-1] = False
                m = self.S.fit(ks, lo, hi, trial.a, dm, (trial.a, trial.b), A.track, A.flip, fixed_v=A.model.v,
                               max_run=2)
                if m is not None:
                    m.kind, m.unsnapped = A.model.kind, A.model.unsnapped
                    m.cost += self.S.class_cost(A.model.kind)
                    trial.model = m
                    for k in m.drops:
                        self._widen(k, int(self.pred(trial, k)), "phantom_cut_merge")
                    ok = self.refit(trial, keep_v=True)
            if ok and trial.model.cost <= self.seg_cost(A) + self.seg_cost(B) + self.S.l_cut + 1e-9:
                self.log("phantom_cut_merged", comp_frame=int(B.a), evidence={
                    "segments": [[A.a, A.b], [B.a, B.b]], "speed": trial.model.v,
                    "pred": [int(self.pred(trial, B.a - 1)), int(self.pred(trial, B.a))],
                    "costs": [self.seg_cost(A), self.seg_cost(B), trial.model.cost]})
                segs[i:i + 2] = [trial]
                continue
            self.log("phantom_cut_kept", comp_frame=int(B.a), evidence={
                "segments": [[A.a, A.b], [B.a, B.b]], "union_feasible": bool(ok)})
            B.notes.append(f"cut at {B.a} shows no RAW / framing discontinuity but one model cannot explain "
                           "both sides")
            i += 1
        return segs

    def check_cuts(self, segs: list[_Seg]) -> None:
        for i in range(len(segs) - 1):
            A, B = segs[i], segs[i + 1]
            if not (A.kind == "raw" and B.kind == "raw" and A.b == B.a) or B.trans_in is not None \
                    or B.cut_ambiguity is not None:
                continue
            if self._period_break(B.a):
                self.log("criterion2_layout_boundary", comp_frame=int(B.a), evidence={
                    "reason": "cut at a layout period boundary (not moved)"})
                continue
            for it in range(3):
                c = B.a
                if self._same_view(A, B, c - 1) and self._same_view(A, B, c):
                    # both models show the same frame on both sides: moving the cut cannot help (it would only
                    # oscillate); merge_phantom_cuts removes it
                    self.log("criterion2_indistinguishable", comp_frame=int(c), evidence={"cut": c, "iteration": it})
                    break
                s1 = self._side_scores(A, B, c - 1)
                s2 = self._side_scores(A, B, c)
                ev = {"cut": c, "last_A": s1, "first_B": s2, "iteration": it}
                if s1 is None or s2 is None:
                    self.log("criterion2_unchecked", comp_frame=int(c), evidence=ev)
                    break
                okA, okB = s1[0] > s1[1], s2[1] > s2[0]
                if okA and okB:
                    self.log("criterion2_pass", comp_frame=int(c), evidence=ev)
                    break
                move = None
                if not okA and A.length > 1:
                    move = c - 1
                elif not okB and B.length > 1:
                    move = c + 1
                if move is None:
                    self.log("criterion2_fail", comp_frame=int(c), evidence=ev)
                    B.notes.append(f"criterion 2 not satisfied at cut {c} (scores {s1}, {s2})")
                    break
                old = (A.b, B.a, A.model, B.model, dict(A.extra), dict(B.extra))
                k_move = c - 1 if move < c else c
                owner = B if move < c else A
                A.b = B.a = move
                owner.extra[k_move] = (int(self.pred(owner, k_move)),) * 2
                if self.refit(A) and self.refit(B):
                    self.F.touched[k_move] = "criterion2_moved"
                    self.log("criterion2_move", comp_frame=int(move), evidence=ev)
                else:
                    A.b, B.a, A.model, B.model, A.extra, B.extra = old
                    self.log("criterion2_fail", comp_frame=int(c), evidence={**ev, "move_infeasible": move})
                    B.notes.append(f"criterion 2 not satisfied at cut {c}; moving it is infeasible")
                    break

    # ---------------------------------------------------------------------------------------------
    # retiming, remap, ramps
    # ---------------------------------------------------------------------------------------------
    def final_speeds(self, segs: list[_Seg]) -> None:
        """phase_solve.snap_speed over the final constraints of every segment whose speed is neither the
        dominant one nor 1.0, with the speeds of the other segments as preferred values (DESIGN: 'candidates
        = snap values ∪ speeds of already-solved segments'), so e.g. a repeated odd speed is reused."""
        weights: dict[float, float] = {}
        for S in segs:
            if S.kind == "raw" and S.model is not None:
                weights[S.model.v] = weights.get(S.model.v, 0.0) + S.length
        for S in segs:
            if S.kind != "raw" or S.model is None or S.model.kind not in ("snap", "unsnapped"):
                continue
            ks, lo, hi = self.constraints(S)
            if ks.size < 2:
                continue
            vr = self.S.vrange(ks, lo, hi, S.a)
            ex = self.exact_range(ks, lo, hi, S.a, S.model.drops)
            S.model.v_ols = self.measured_speed(ks, lo, hi)
            others = dict(weights)
            others[S.model.v] = others.get(S.model.v, 0.0) - S.length
            pref = {v: w for v, w in others.items() if w > 0}
            dom = self.S.dominant
            pref[dom] = pref.get(dom, 0.0) + 1e9           # the edit's dominant speed stays dominant
            # snap test (prompt 5.4): inside the measured frames' exact range, or within speed_snap_tol of the
            # robust slope of the measured frames; a soft range alone never licenses a snap
            v2, uns = ps.snap_speed(S.model.v_ols, vr, self.cfg, preferred=pref,
                                    exact_range=ex if ex is not None else (S.model.v_ols, S.model.v_ols))
            if uns or abs(v2 - S.model.v) <= 1e-12:
                continue
            old = S.model
            S.model = _Model(old.comp_in, v2, "snap", old.cost, False, vr, old.v_ols, [], old.sol, old.track,
                             old.flip, old.n_frames, old.data)
            if self.refit(S, keep_v=True) and S.model.data <= old.data + 1e-9:
                self.log("speed_resnapped", comp_range=[S.a, S.b], evidence={
                    "from": old.v, "to": v2, "range": vr, "exact_range": ex, "v_ols": old.v_ols,
                    "was_unsnapped": old.unsnapped})
            else:
                S.model = old

    def retime(self, segs: list[_Seg]) -> None:
        if not self.P.ok:
            return
        F, cfg = self.F, self.cfg
        mt = float(_cfg(cfg, "match_thresh", 0.9))
        for S in segs:
            if S.kind != "raw" or abs(S.model.v - 1.0) <= 0.005 or S.model.v <= 0:
                continue
            frames = [k for k in range(S.a, S.b) if F.status[k] == Status.MATCH]
            if not frames:
                continue
            blends = set(S.blend_frames)
            pairs: dict[int, int] = {}
            for k in frames:
                if k in blends or not (math.isfinite(F.score[k]) and F.score[k] < mt):
                    continue
                j = int(self.pred(S, k))
                sim = self.sim_at(S, k)
                for j0 in (j - 1, j):
                    bf = self.P.blend_fit(k, [(j0, sim, S.flip)], [(j0 + 1, sim, S.flip)])
                    if bf and "zfit" in bf and bf["zfit"] >= mt and 0.1 < bf["alpha_a"] < 0.9:
                        pairs[k] = j0
                        break
            if len(blends) + len(pairs) >= 0.2 * len(frames):
                for k, j0 in pairs.items():      # a blend of j0 and j0+1 is consistent with showing either
                    self._widen(k, j0, "frame_blend")
                    self._widen(k, j0 + 1, "frame_blend")
                blends |= set(pairs)
                S.retime = "frame_blend"
                S.blend_frames = sorted(blends)
                ok = self.refit(S)
                S.notes.append(f"frame-blend retiming: {len(blends)}/{len(frames)} frames are blends of adjacent "
                               f"RAW frames (AE Frame Blending approximates it)")
                if not ok:
                    S.uncertain = True
                    S.notes.append("phase solve infeasible even without the blended frames: timing uncertain")
                self.log("retime_frame_blend", comp_range=[S.a, S.b], evidence={
                    "blend_frames": S.blend_frames, "n_frames": len(frames), "feasible": ok})

    def ramps(self, segs: list[_Seg]) -> list[_Seg]:
        """Chains of >= 3 raw pieces joined by speed-only cuts with monotone speeds -> one remap segment."""
        out: list[_Seg] = []
        i = 0
        while i < len(segs):
            j = i
            while j + 1 < len(segs) and segs[j + 1].cut_ambiguity is not None and segs[j].kind == "raw" \
                    and segs[j + 1].kind == "raw" and segs[j].b == segs[j + 1].a:
                j += 1
            chain = segs[i:j + 1]
            vs = [s.model.v for s in chain] if len(chain) >= 3 else []
            dv = np.diff(vs)
            if len(chain) >= 3 and (np.all(dv > 0) or np.all(dv < 0)):
                R = _Seg("raw", chain[0].a, chain[-1].b, model=chain[0].model, flip=chain[0].flip,
                         track=chain[0].track, trans_in=chain[0].trans_in, trans_out=chain[-1].trans_out)
                R.ramp = chain
                R.notes.append("speed ramp: " + " -> ".join(f"{v:.4g}" for v in vs))
                self.log("speed_ramp", comp_range=[R.a, R.b], evidence={"speeds": vs,
                                                                          "pieces": [[s.a, s.b] for s in chain]})
                out.append(R)
            else:
                out.extend(chain)
            i = j + 1
        return out

    # ---------------------------------------------------------------------------------------------
    # placeholders & finalisation
    # ---------------------------------------------------------------------------------------------
    def to_segment(self, S: _Seg) -> Segment:
        F, cf = self.F, self.cf
        if S.kind == "none":
            sc = F.score[S.a:S.b]
            conf = 0.9 if not np.isfinite(sc).any() else float(np.clip(1.0 - np.nanmax(sc), 0.0, 1.0))
            label = f"MISSING - not in RAW ({timecode(S.a, cf)}-{timecode(S.b, cf)})"
            seg = Segment(0, "not_in_raw", S.a, S.b, speed=1.0, confidence=round(conf, 4), label=label,
                          notes="; ".join(S.notes + ["no RAW match (NOT-IN-RAW placeholder)"]))
            seg.audio["exception"] = "not_in_raw"
            return seg
        if S.kind in ("flash", "dip"):
            seg = Segment(0, S.kind, S.a, S.b, speed=1.0, color=S.color, transition_in=S.trans_in,
                          transition_out=S.trans_out, confidence=0.9,
                          label=f"{S.kind.upper()} {S.color}", notes="; ".join(S.notes))
            return seg
        # raw
        if S.ramp:
            return self._ramp_segment(S)
        m = S.model
        for k in m.drops:   # tolerated isolated frames: their soft range now includes the model frame
            self._widen(k, int(self.pred(S, k)), "drop")
        ks, lo, hi = self.constraints(S)
        if ks.size:
            # the phase follows the measured frames (data term); tolerated drops keep their widened soft range
            # but carry no preference
            pen = self.S.penalties(ks, lo, hi)
            if pen is not None and m.drops:
                sel = ~np.isin(ks[pen[0]], np.asarray(m.drops, dtype=np.int64))
                pen = (pen[0][sel], pen[1][sel], pen[2][sel]) if sel.any() else None
            sol = ps.solve_raw_in(ks, lo, hi, S.a, m.v, cf, self.rf, penalties=pen)
        else:
            sol = m.sol
        m.sol = sol
        m.data = float(sol.get("data_cost", 0.0)) if ks.size else m.data
        vr = self.claimed_range(S, ks, lo, hi)
        m.v_ols = self.measured_speed(ks, lo, hi)
        notes = list(S.notes)
        uncertain = S.uncertain
        if ks.size and not sol.get("ok", False):
            uncertain = True
            notes.append("phase solve infeasible at the chosen speed (slack %.3g frames)" % sol.get("slack", 0))
        fr = self.framing(S)
        notes += fr.get("notes", [])
        if m.unsnapped:
            notes.append(f"speed {m.v:.5f} could not be snapped (feasible range {vr})")
        tie = [int(k) for k in sol.get("tie_frames", [])]
        seg = Segment(0, "raw", S.a, S.b)
        seg.speed = float(m.v)
        seg.speed_measured = float(m.v_ols) if math.isfinite(m.v_ols) else None
        seg.speed_range = [float(vr[0]), float(vr[1])] if vr is not None else None
        seg.unsnapped = bool(m.unsnapped)
        seg.flip_h = bool(S.flip)
        seg.transform = dict(fr["transform"])
        seg.transform_keys = [dict(k) for k in fr["keys"]]
        seg.easing = fr.get("easing", "linear")
        seg.transition_in, seg.transition_out = S.trans_in, S.trans_out
        seg.retime = S.retime
        seg.cut_ambiguity = S.cut_ambiguity
        if ks.size:
            seg.raw_in_seconds = float(sol["raw_in"])
            seg.raw_in_interval = list(sol["interval_floor"])
            seg.raw_in_interval_both = list(sol["interval_both"]) if sol["interval_both"] else None
            seg.ae_margin_ms = float(sol["margin_ms"])
            seg.raw_in_frame = int(self.pred(S, S.a))
            seg.raw_out_frame = int(self.pred(S, S.b - 1))
        seg.tie_frames = tie
        in_seg = [k for k in range(S.a, S.b) if F.status[k] == Status.MATCH]
        seg.ambiguous_frames = [k for k in in_seg if F.raw_hi[k] > F.raw_lo[k]]
        if m.v == 0.0 and ks.size:
            j = int(self.pred(S, S.a))
            val = (j + 0.25) / float(self.rf)
            seg.time_mode = "remap"
            seg.time_remap_keys = [{"comp_frame": S.a, "raw_seconds": val}, {"comp_frame": S.b, "raw_seconds": val}]
            notes.append(f"freeze frame on RAW {j}")
        elif m.v < 0 and ks.size:
            seg.time_mode = "remap"
            seg.time_remap_keys = [
                {"comp_frame": S.a, "raw_seconds": float(sol["raw_in"])},
                {"comp_frame": S.b, "raw_seconds": float(sol["raw_in"]) + m.v * (S.b - S.a) / float(cf)}]
            notes.append("reverse playback")
        conf = self._confidence(S, in_seg, uncertain)
        seg.confidence = conf
        seg.uncertain = bool(uncertain)
        seg.notes = "; ".join(notes)
        # blend-frame constraints of crossfade segments (not representable in the FrameMap)
        seg.__dict__["_phase_extra"] = {int(k): (int(v[0]), int(v[1])) for k, v in S.extra.items()
                                        if self.F.status[k] != Status.MATCH}
        return seg

    def _ramp_segment(self, S: _Seg) -> Segment:
        cf = self.cf
        # keys at the first AND last frame of every piece: every comp frame then lies exactly on its own
        # piece's line (no comp frame falls between the last key of a piece and the first of the next)
        keys = []
        for p in S.ramp:
            ks, lo, hi = self.constraints(p)
            if ks.size:
                p.model.sol = ps.solve_raw_in(ks, lo, hi, p.a, p.model.v, cf, self.rf)
            keys.append({"comp_frame": p.a, "raw_seconds": float(p.model.raw_in)})
            if p.b - 1 > p.a:
                keys.append({"comp_frame": p.b - 1, "raw_seconds": float(p.model.raw_in) + p.model.v *
                             (p.b - 1 - p.a) / float(cf)})
        last = S.ramp[-1]
        first = S.ramp[0]
        seg = Segment(0, "raw", S.a, S.b)
        seg.time_mode = "remap"
        seg.time_remap_keys = keys
        span = (keys[-1]["comp_frame"] - keys[0]["comp_frame"]) / float(cf)
        seg.speed = float((keys[-1]["raw_seconds"] - keys[0]["raw_seconds"]) / span) if span > 0 else first.model.v
        seg.speed_range = [min(p.model.v for p in S.ramp), max(p.model.v for p in S.ramp)]
        seg.speed_measured = seg.speed
        seg.flip_h = S.flip
        fr = self.framing(S)
        seg.transform, seg.transform_keys, seg.easing = dict(fr["transform"]), fr["keys"], fr.get("easing", "linear")
        seg.transition_in, seg.transition_out = S.trans_in, S.trans_out
        seg.raw_in_seconds = float(first.model.raw_in)
        seg.raw_in_frame = int(self.pred(first, S.a))
        seg.raw_out_frame = int(self.pred(last, S.b - 1))
        seg.tie_frames = sorted(set(k for p in S.ramp for k in p.model.sol.get("tie_frames", [])))
        in_seg = [k for k in range(S.a, S.b) if self.F.status[k] == Status.MATCH]
        seg.ambiguous_frames = [k for k in in_seg if self.F.raw_hi[k] > self.F.raw_lo[k]]
        seg.confidence = self._confidence(S, in_seg, False)
        seg.notes = "; ".join(S.notes)
        return seg

    def _confidence(self, S: _Seg, in_seg: list[int], uncertain: bool) -> float:
        F = self.F
        if not in_seg:
            return 0.5
        c = F.conf[in_seg]
        if np.isfinite(c).any() and np.nanmax(c) > 0:
            base = float(np.nanmedian(c))
        else:
            sc = F.score[in_seg]
            nt = float(_cfg(self.cfg, "none_thresh", 0.6))
            base = float(np.clip((np.nanmedian(sc) - nt) / max(1e-6, 1 - nt), 0, 1)) if np.isfinite(sc).any() else 0.5
        if S.model is not None and S.model.unsnapped:
            base *= 0.8
        if uncertain:
            base *= 0.6
        return round(float(np.clip(base, 0.0, 1.0)), 4)

    # ---------------------------------------------------------------------------------------------
    # write-back to the FrameMap
    # ---------------------------------------------------------------------------------------------
    def write_back(self, work: list[_Seg], segs: list[Segment]) -> None:
        fm, F = self.fm, self.F
        status = np.asarray(fm.status).copy()
        raw = np.asarray(fm.raw).copy()
        rlo, rhi = np.asarray(fm.raw_lo).copy(), np.asarray(fm.raw_hi).copy()
        slo, shi = np.asarray(fm.soft_lo).copy(), np.asarray(fm.soft_hi).copy()
        low = np.asarray(fm.low_margin).copy()
        tie = np.zeros(fm.n, bool)
        flip = np.asarray(fm.flip).copy()
        track = np.asarray(fm.track).copy()
        changed = []
        for S, seg in zip(work, segs):
            if seg.type != "raw":
                continue
            pieces = S.ramp if S.ramp else [S]
            for p in pieces:
                if not p.model.sol or "raw_in" not in p.model.sol or not math.isfinite(p.model.raw_in):
                    continue
                for k in range(p.a, p.b):
                    if F.status[k] != Status.MATCH or bool(F.flip[k]) != p.flip:
                        continue
                    j = int(self.pred(p, k))
                    if status[k] != Status.MATCH:   # absorbed NONE frames
                        status[k] = Status.MATCH
                        flip[k], track[k] = p.flip, p.track
                    lo_k, hi_k = p.extra.get(k, (int(F.lo[k]), int(F.hi[k])))
                    if not (lo_k <= j <= hi_k):
                        if k not in p.model.drops:
                            continue          # e.g. a timing-tie frame: keep refine's frame (it is listed)
                        lo_k, hi_k = min(lo_k, j), max(hi_k, j)   # tolerated isolated low-margin frame
                    if not (int(rlo[k]) <= j <= int(rhi[k])):
                        changed.append({"k": k, "old": int(raw[k]), "new": j,
                                        "why": F.touched.get(k, "drop" if k in p.model.drops else "model")})
                        raw[k] = j
                        rlo[k] = rhi[k] = j
                        low[k] = True
                    slo[k], shi[k] = lo_k, hi_k
            for k in seg.tie_frames:
                tie[k] = True
        for S in work:
            if S.kind == "raw" and S.trans_in and S.trans_in.get("type") == "crossfade":
                D = int(S.trans_in["duration_frames"])
                status[S.a:S.a + D] = Status.BLEND
        fm.status, fm.raw, fm.raw_lo, fm.raw_hi = status, raw, rlo, rhi
        fm.soft_lo, fm.soft_hi, fm.low_margin, fm.tie = slo, shi, low, tie
        fm.flip, fm.track = flip, track
        if changed:
            self.log("frame_map_corrected", evidence={"frames": changed[:500], "count": len(changed)})

    # ---------------------------------------------------------------------------------------------
    def run(self) -> list[Segment]:
        seed_everything(int(_cfg(self.cfg, "seed", 12345)))
        if self.n == 0:
            return []
        self.S.dominant = self._dominant_from_hints()
        work = self._segment_pass()
        dom = self._dominant_from_segments(work)
        if abs(dom - self.S.dominant) > 1e-9:
            self.log("dominant_speed_changed", evidence={"from": self.S.dominant, "to": dom})
            self.S.dominant = dom
            self.S._cache.clear()
            self.S._relax_cache.clear()
            work = self._segment_pass()
        work = self.merge_tiny(work)
        work = self.merge_adjacent(work)
        work = self.merge_retime_chains(work)
        work = self.merge_continuous(work)
        work = self.absorb_none(work)
        self.final_speeds(work)
        self.speed_only_cuts(work)
        work = self.transitions(work)
        work = self.uniform_runs(work)
        self.check_cuts(work)
        work = self.merge_phantom_cuts(work)
        self.retime(work)
        work = self.ramps(work)
        for S in work:
            if S.kind == "raw":
                self.framing(S, force=True)
                self.full_affine_check(S)
        work.sort(key=lambda s: (s.a, s.b))
        segs = [self.to_segment(S) for S in work]
        order = sorted(range(len(segs)), key=lambda i: (segs[i].comp_in, segs[i].comp_out))
        work = [work[i] for i in order]
        segs = [segs[i] for i in order]
        for i, s in enumerate(segs, start=1):
            s.id = i
        self.assign_regions(segs)
        self.write_back(work, segs)
        self._coverage_check(segs)
        pre_raw = np.asarray(self.fm.d[_PRE + "raw"]) if (_PRE + "raw") in self.fm.d else None
        for s in segs:
            s.low_margin_frames = [k for k in range(s.comp_in, s.comp_out)
                                   if bool(self.fm.low_margin[k]) and self.fm.status[k] == Status.MATCH]
            if s.type == "raw" and s.low_margin_frames:
                n_in = max(1, sum(1 for k in range(s.comp_in, s.comp_out) if self.fm.status[k] == Status.MATCH))
                s.confidence = round(float(s.confidence * max(0.5, 1.0 - len(s.low_margin_frames) / n_in)), 4)
                if pre_raw is not None:
                    fixed = [k for k in s.low_margin_frames if int(pre_raw[k]) != int(self.fm.raw[k])]
                    if fixed:
                        s.notes = (s.notes + "; " if s.notes else "") + \
                            f"low-margin frames re-assigned to the segment model's RAW frame: {fixed}"
        self.log("segments", evidence={"count": len(segs), "segments": [
            {"id": s.id, "type": s.type, "comp": [s.comp_in, s.comp_out], "speed": s.speed,
             "raw_in_frame": s.raw_in_frame, "flip": s.flip_h} for s in segs]})
        return segs

    def _segment_pass(self) -> list[_Seg]:
        self._gpen = None
        work: list[_Seg] = []
        for a, b, st in self.status_runs():
            if st == Status.MATCH:
                for r0, r1 in self.match_subruns(a, b):
                    work.extend(self.dp_run(r0, r1))
            elif st == Status.UNIFORM:
                work.append(_Seg("uniform", a, b))
            else:
                work.append(_Seg("none", a, b))
        return work

    def _dominant_from_hints(self) -> float:
        h = self.hints
        if h is None or len(getattr(h, "comp_t", [])) == 0:
            return 1.0
        conf = h.confident(float(_cfg(self.cfg, "audio_min_conf", 1.3)))
        vals = [self.S._nearest_snap(float(s)) for s in h.speed[conf] if np.isfinite(s)]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 4:
            best = max(set(vals), key=lambda v: (vals.count(v), -abs(v - 1.0)))
            if vals.count(best) >= 0.5 * len(vals):
                if abs(best - 1.0) > 1e-9:
                    self.log("dominant_speed_audio", evidence={"speed": best, "votes": vals.count(best),
                                                               "windows": len(vals)})
                return best
        return 1.0

    def _dominant_from_segments(self, work: list[_Seg]) -> float:
        w: dict[float, int] = {}
        for s in work:
            if s.kind == "raw" and s.model is not None and not s.model.unsnapped:
                w[s.model.v] = w.get(s.model.v, 0) + s.length
        if not w:
            return self.S.dominant
        best = ps.dominant_speed(w)
        tot = sum(w.values())
        return best if best is not None and w.get(best, 0) >= 0.5 * tot else self.S.dominant

    def _coverage_check(self, segs: list[Segment]) -> None:
        cover = np.zeros(self.n, np.int32)
        for s in segs:
            cover[s.comp_in:s.comp_out] += 1
        gaps = np.nonzero(cover == 0)[0]
        if gaps.size:
            log.warning("segment: %d competitor frames not covered (first %s)", gaps.size, gaps[:10].tolist())
            self.log("coverage_gap", evidence={"frames": gaps[:200].tolist(), "count": int(gaps.size)})

    # ---------------------------------------------------------------------------------------------
    def full_affine_check(self, S: _Seg) -> None:
        """Prompt 5.5: try a full affine only when the similarity fit is clearly poor. AE keeps the
        similarity; the result is reported in the notes."""
        if not self.P.ok:
            return
        F = self.F
        mt = float(_cfg(self.cfg, "match_thresh", 0.9))
        ks = [k for k in range(S.a, S.b) if F.status[k] == Status.MATCH and math.isfinite(F.score[k])]
        if len(ks) < 3 or float(np.median(F.score[ks])) >= mt + 0.02:
            return
        k = ks[len(ks) // 2]
        try:
            import cv2
            from .geometry import from_cv_matrix, h3, to_cv_matrix, translate3
            reg = self.P.region(k)
            j = int(self.pred(S, k))
            if reg is None or not self.raw.has(j):
                return
            sim = self.sim_at(S, k)
            x0, y0, _w, _h = self.P.roi
            M = translate3(-x0, -y0) @ h3(to_cv_matrix(sim, S.flip, self.P.raw_w, self.raw.ratio, self.comp.ratio))
            init = np.linalg.inv(M)[:2].astype(np.float32)
            raw_img = _blur(np.asarray(self.raw.get(j)), self.P.blur)
            crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, int(_cfg(self.cfg, "ecc_iterations", 60)),
                    float(_cfg(self.cfg, "ecc_eps", 1e-5)))
            _cc, Wm = cv2.findTransformECC(reg.img.astype(np.float32), raw_img.astype(np.float32), init,
                                           cv2.MOTION_AFFINE, crit, reg.mask.astype(np.uint8), 5)
            full = from_cv_matrix((translate3(x0, y0) @ np.linalg.inv(h3(Wm)))[:2], S.flip, self.P.raw_w,
                                  self.raw.ratio, self.comp.ratio, project=False)
            A2 = np.asarray(full)[:2, :2]
            warped = cv2.warpAffine(raw_img, Wm, (reg.img.shape[1], reg.img.shape[0]),
                                    flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            from .scoring import zncc
            z_aff = zncc(reg.img, warped, reg.mask)
            z_sim = self.P.zncc_set(k, [(j, sim, S.flip)])
            z_sim = float(z_sim[0]) if z_sim is not None else float("nan")
            sx, sy = float(np.hypot(*A2[:, 0])), float(np.hypot(*A2[:, 1]))
            if math.isfinite(z_aff) and math.isfinite(z_sim) and z_aff > z_sim + 0.01:
                S.notes.append(f"full affine fits clearly better (ZNCC {z_aff:.4f} vs {z_sim:.4f}; sx/sy = "
                               f"{sx / sy:.4f}); AE uses the similarity transform")
            self.log("full_affine_check", comp_frame=int(k), evidence={"zncc_affine": z_aff, "zncc_sim": z_sim,
                                                                        "sx": sx, "sy": sy})
        except Exception as e:  # ECC does not converge: keep the similarity (DESIGN §2.2)
            self.log("full_affine_check_failed", comp_frame=int(k), evidence={"error": str(e)[:200]})


def _easing(idx: np.ndarray, s: np.ndarray, px: np.ndarray, py: np.ndarray) -> str:
    """Informational easing label from the progress curve of the dominant animated quantity."""
    cands = [s / max(np.median(s), 1e-9) * 1000.0, px, py]
    q = max(cands, key=lambda a: float(np.ptp(a)))
    if np.ptp(q) <= 1e-9 or idx.size < 5:
        return "linear"
    t = (idx - idx[0]) / max(1, idx[-1] - idx[0])
    p = (q - q[0]) / (q[-1] - q[0]) if abs(q[-1] - q[0]) > 1e-9 else np.zeros_like(q)
    dev = p - t
    first, last = dev[t <= 0.4], dev[t >= 0.6]
    slow_start = first.size and float(np.mean(first)) < -0.05
    slow_end = last.size and float(np.mean(last)) > 0.05
    if slow_start and slow_end:
        return "ease_in_out"
    if slow_start:
        return "ease_in"
    if slow_end:
        return "ease_out"
    return "linear"


# =================================================================================================
# PySceneDetect cross-check
# =================================================================================================

def scenedetect_changes(path: str, cfg: Any) -> list[int]:
    """Competitor frame indices where PySceneDetect (OpenCV backend; AdaptiveDetector + ContentDetector at
    low thresholds) starts a new scene. Cached in WORK_DIR by file hash + thresholds."""
    from .common import Cache, file_hash, stage_key

    ad = float(_cfg(cfg, "scenedetect_adaptive", 2.0))
    ct = float(_cfg(cfg, "scenedetect_content", 15.0))
    ml = int(_cfg(cfg, "scenedetect_min_len", 2))

    def compute() -> dict:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import AdaptiveDetector, ContentDetector
        video = open_video(str(path), backend="opencv")
        sm = SceneManager()
        sm.add_detector(AdaptiveDetector(adaptive_threshold=ad, min_scene_len=ml))
        sm.add_detector(ContentDetector(threshold=ct, min_scene_len=ml))
        sm.detect_scenes(video)
        scenes = sm.get_scene_list()
        return {"changes": sorted({int(s[0].frame_num) for s in scenes[1:]})}

    work = _cfg(cfg, "work_dir", None)
    if work:
        try:
            cache = Cache(work)
            key = stage_key("scenedetect", file_hash(path), ad, ct, ml)
            return list(cache.json("scenedetect", key, compute)["changes"])
        except OSError:
            pass
    return compute()["changes"]


def _crosscheck(b: _Builder, segs: list[Segment]) -> dict:
    if not bool(_cfg(b.cfg, "scenedetect", True)):
        b.log("scenedetect_skipped", evidence={"reason": "disabled by cfg.scenedetect"})
        return {"status": "skipped"}
    path = getattr(b.comp, "path", "")
    if not path or not Path(path).exists():
        b.log("scenedetect_skipped", evidence={"reason": "competitor file not available", "path": str(path)})
        return {"status": "skipped"}
    try:
        changes = scenedetect_changes(path, b.cfg)
    except Exception as e:  # scenedetect missing / decode failure: never fatal
        log.warning("segment: PySceneDetect cross-check failed: %s", e)
        b.log("scenedetect_failed", evidence={"error": str(e)[:300]})
        return {"status": "failed", "error": str(e)}
    cuts: dict[int, Segment] = {}
    windows: list[tuple[int, int, str]] = []
    for s in segs:
        if s.comp_in > 0:
            cuts[s.comp_in] = s
        for t in (s.transition_in, s.transition_out):
            if t:
                if t is s.transition_in:
                    windows.append((s.comp_in, s.comp_in + int(t["duration_frames"]), t["type"]))
                else:
                    windows.append((s.comp_out - int(t["duration_frames"]), s.comp_out, t["type"]))
        if s.type in ("flash", "dip", "not_in_raw"):
            windows.append((s.comp_in, s.comp_out, s.type))
    agree, unexplained, missed = [], [], []
    for f in changes:
        near_cut = [c for c in cuts if abs(c - f) <= 1]
        near_win = [w for w in windows if w[0] - 1 <= f <= w[1] + 1]
        if near_cut or near_win:
            agree.append(f)
            continue
        seg = next((s for s in segs if s.comp_in <= f < s.comp_out), None)
        why = "inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, " \
              "caption or overlay change -- not a cut"
        ev = {"frame": f, "segment": seg.id if seg else None}
        if seg is not None and seg.type == "raw":
            sc = b.F.score[max(0, f - 1):f + 1]
            ev["scores"] = [float(x) for x in sc]
            if np.isfinite(sc).all() and float(np.min(sc)) < float(_cfg(b.cfg, "match_thresh", 0.9)):
                why = "inside a segment but the match score dips there (check overlays / a missed flash cut)"
            seg.notes = (seg.notes + "; " if seg.notes else "") + f"PySceneDetect change at {f}: {why}"
        unexplained.append({**ev, "explanation": why})
    changes_set = set(changes)
    for c, s in sorted(cuts.items()):
        if any(abs(c - f) <= 1 for f in changes_set):
            continue
        prev = next((p for p in segs if p.comp_out == c and p.type == "raw"), None)
        if s.type == "raw" and prev is not None and s.raw_in_frame is not None and prev.raw_out_frame is not None:
            jump = s.raw_in_frame - prev.raw_out_frame
            why = (f"same-shot jump cut (RAW jump {jump} frames, same framing)" if s.flip_h == prev.flip_h
                   and s.transform and prev.transform and
                   abs(s.transform["scale"] / max(prev.transform["scale"], 1e-9) - 1) < 0.01
                   else f"low-contrast cut (RAW jump {jump} frames)")
        else:
            why = "cut next to a transition / placeholder (content change is gradual or uniform)"
        missed.append({"cut": c, "explanation": why})
    b.log("scenedetect_crosscheck", evidence={"detected": changes, "agree": agree, "unexplained": unexplained,
                                              "cuts_not_detected": missed})
    return {"status": "ok", "detected": changes, "agree": agree, "unexplained": unexplained,
            "cuts_not_detected": missed}


# =================================================================================================
# debug plots
# =================================================================================================

_INK = "#0b0b0b"
_INK2 = "#52514e"
_SURF = "#fcfcfb"
_BLUE, _ORANGE, _AQUA, _RED, _VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#e34948", "#4a3aa7"


def _style_axes(ax) -> None:
    ax.set_facecolor(_SURF)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#c9c8c3")
    ax.tick_params(colors=_INK2, labelsize=8)
    ax.grid(True, color="#e6e5e0", linewidth=0.6)
    ax.set_axisbelow(True)


def plot_mapping(segs: Sequence[Segment], fm: FrameMap, comp_fps: Any, raw_fps: Any, path: str | Path) -> None:
    """debug/mapping.png: competitor time (x) vs RAW time (y); one line per segment, cuts as jumps,
    NOT-IN-RAW shaded, crossfades / dips / flashes marked."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cf, rf = float(Fraction(comp_fps)), float(Fraction(raw_fps))
    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=110)
    fig.patch.set_facecolor(_SURF)
    _style_axes(ax)
    st = np.asarray(fm.status)
    raw = np.asarray(fm.raw)
    m = (st == Status.MATCH) & (raw >= 0)
    ks = np.nonzero(m)[0]
    if ks.size:
        ax.scatter(ks / cf, raw[ks] / rf, s=4, color=_INK2, alpha=0.35, linewidths=0, label="m(k) (RAW frame shown)",
                   zorder=2)
    labelled = set()
    for s in segs:
        t0, t1 = s.comp_in / cf, s.comp_out / cf
        if s.type == "raw" and s.raw_in_seconds is not None:
            if s.time_remap_keys:
                xs = [kk["comp_frame"] / cf for kk in s.time_remap_keys]
                ys = [kk["raw_seconds"] for kk in s.time_remap_keys]
            else:
                xs = [t0, t1]
                ys = [s.raw_in_seconds, s.raw_in_seconds + s.speed * (t1 - t0)]
            col = _VIOLET if s.flip_h else _BLUE
            lab = "segment (flipped)" if s.flip_h else "segment"
            ax.plot(xs, ys, color=col, linewidth=2, solid_capstyle="round", zorder=3,
                    label=lab if lab not in labelled else None)
            labelled.add(lab)
            ax.annotate(f"{s.id}" + (f" x{s.speed:.2f}" if abs(s.speed - 1) > 0.004 else ""),
                        (xs[0], ys[0]), textcoords="offset points", xytext=(2, 4), fontsize=7, color=_INK)
        elif s.type == "not_in_raw":
            ax.axvspan(t0, t1, color=_RED, alpha=0.15, linewidth=0, zorder=1,
                       label="NOT-IN-RAW" if "nir" not in labelled else None)
            labelled.add("nir")
        elif s.type in ("flash", "dip"):
            ax.axvspan(t0, t1, color=_INK2, alpha=0.18, linewidth=0, zorder=1,
                       label="flash / dip" if "fd" not in labelled else None)
            labelled.add("fd")
        if s.transition_in and s.transition_in.get("type") == "crossfade":
            d = int(s.transition_in["duration_frames"])
            ax.axvspan(t0, (s.comp_in + d) / cf, color=_AQUA, alpha=0.25, linewidth=0, zorder=1,
                       label="crossfade" if "xf" not in labelled else None)
            labelled.add("xf")
        if s.comp_in > 0:
            ax.axvline(t0, color="#c9c8c3", linewidth=0.8, linestyle="--", zorder=0)
    ax.set_xlabel("competitor time (s)", color=_INK, fontsize=9)
    ax.set_ylabel("RAW time (s)", color=_INK, fontsize=9)
    ax.set_title("Competitor -> RAW mapping (every segment a line, every cut a jump)", color=_INK, fontsize=11,
                 loc="left")
    ax.set_xlim(0, fm.n / cf)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=6, fontsize=8, frameon=False,
              labelcolor=_INK2)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=_SURF)
    plt.close(fig)


def plot_scores(segs: Sequence[Segment], fm: FrameMap, cfg: Any, path: str | Path) -> None:
    """debug/scores.png: per-frame match score and margin (two panels, one scale each), thresholds, cuts."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    k = np.arange(fm.n)
    sc = np.asarray(fm.score, dtype=np.float64)
    mg = np.asarray(fm.margin, dtype=np.float64)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 6.5), dpi=110, sharex=True,
                                 gridspec_kw={"height_ratios": [3, 2]})
    fig.patch.set_facecolor(_SURF)
    for ax in (a1, a2):
        _style_axes(ax)
        for s in segs:
            if s.comp_in > 0:
                ax.axvline(s.comp_in, color="#c9c8c3", linewidth=0.8, linestyle="--", zorder=0)
            if s.type == "not_in_raw":
                ax.axvspan(s.comp_in, s.comp_out, color=_RED, alpha=0.12, linewidth=0)
            if s.transition_in and s.transition_in.get("type") == "crossfade":
                ax.axvspan(s.comp_in, s.comp_in + int(s.transition_in["duration_frames"]), color=_AQUA,
                           alpha=0.25, linewidth=0)
    a1.plot(k, sc, color=_BLUE, linewidth=1.2, label="best score (masked ZNCC)")
    a1.axhline(float(_cfg(cfg, "match_thresh", 0.9)), color=_INK2, linewidth=1, linestyle=":",
               label="match_thresh")
    a1.axhline(float(_cfg(cfg, "none_thresh", 0.6)), color=_RED, linewidth=1, linestyle=":", label="none_thresh")
    lo = np.nanmin(sc) if np.isfinite(sc).any() else 0.0
    a1.set_ylim(max(-0.05, min(0.5, lo - 0.02)), 1.005)
    a1.set_ylabel("score", color=_INK, fontsize=9)
    a1.legend(loc="lower left", fontsize=8, frameon=False, labelcolor=_INK2, ncol=3)
    a1.set_title("Per-frame match score and margin (dashed: cuts)", color=_INK, fontsize=11, loc="left")
    a2.plot(k, mg, color=_ORANGE, linewidth=1.2, label="margin (best - second best)")
    a2.axhline(float(_cfg(cfg, "low_margin_eps", 0.001)), color=_INK2, linewidth=1, linestyle=":",
               label="low_margin_eps")
    lm = np.asarray(fm.low_margin, bool)
    if lm.any():
        a2.scatter(k[lm], np.where(np.isfinite(mg[lm]), mg[lm], 0.0), s=10, color=_RED, zorder=3,
                   label="low-margin frame")
    if np.isfinite(mg).any() and np.nanmax(mg) > 0:
        a2.set_yscale("symlog", linthresh=1e-3)
    a2.set_ylabel("margin", color=_INK, fontsize=9)
    a2.set_xlabel("competitor frame", color=_INK, fontsize=9)
    a2.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=_INK2, ncol=3)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=_SURF)
    plt.close(fig)


# =================================================================================================
# public API
# =================================================================================================

_WRITTEN = ("status", "raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi", "low_margin", "flip", "track", "tie")
_PRE = "pre_segment_"


def _pristine(fm: FrameMap) -> FrameMap:
    """The FrameMap as refine produced it. build_segments writes its corrections into fm but keeps the
    original columns in the column store (keys 'pre_segment_<name>', saved/loaded with the FrameMap), so
    re-running it on its own output reads exactly the same input (determinism, Stage 9.7)."""
    d = fm.__dict__["d"]
    if all(_PRE + k in d for k in _WRITTEN):
        src = fm.copy()
        for k in _WRITTEN:
            setattr(src, k, d[_PRE + k])
        return src
    for k in _WRITTEN:
        d[_PRE + k] = np.asarray(d[k]).copy()
    return fm.copy()


def build_segments(fm: FrameMap, comp: Any, raw: Any, layout: Any, overlays: Any, cfg: Any,
                   dlog: DecisionLog | None, debug_dir: Any, hints: Any = None) -> list[Segment]:
    """Stage 5.4-5.5: cut the competitor timeline into segments (DESIGN §5 segment.py).

    fm        FrameMap from refine (mutated in place: m(k) corrected to the segment model where the soft
              range allows it (low_margin flagged), crossfade overlap frames -> Status.BLEND, tie flags,
              soft ranges widened for tolerated isolated frames; refine's columns are kept as
              'pre_segment_*' entries of the column store, so re-running on the output is exact).
    comp, raw Proxy objects (fps, sizes, ratios; pixels used when ``frames`` is not None).
    layout    Layout (box / static mask) or None; overlays: layout.OverlayMasks or None.
    hints     AudioHints (speed evidence for the DP, lag steps as cut candidates) or None.
    Returns segments with ids 1..N in competitor order; NOT-IN-RAW placeholders, flashes and dips are
    segments too. Writes debug/mapping.png and debug/scores.png when debug_dir is given.
    """
    b = _Builder(fm, comp, raw, layout, overlays, cfg, dlog, debug_dir, hints, src=_pristine(fm))
    segs = b.run()
    b.crosscheck = _crosscheck(b, segs)
    if b.debug_dir is not None:
        try:
            plot_mapping(segs, fm, b.cf, b.rf, b.debug_dir / "mapping.png")
            plot_scores(segs, fm, cfg, b.debug_dir / "scores.png")
        except Exception as e:  # plotting must never break the analysis
            log.warning("segment: debug plots failed: %s", e)
    return segs


def segment_constraints(seg: Segment, fm: FrameMap) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ks, lo, hi) the phase solve of a raw segment uses: MATCH frames of the segment's flip inside
    [comp_in, comp_out) with their soft ranges [soft_lo, soft_hi] (after build_segments' write-back),
    plus -- for segments returned by build_segments in this process -- the RAW frames chosen for the
    visible blend frames of a crossfade (DESIGN: 'add the chosen A/B frames as constraints').
    Crossfade overlap frames are Status.BLEND, so without that extra they only loosen the solve."""
    st = np.asarray(fm.status)
    ks = np.arange(seg.comp_in, seg.comp_out)
    sel = (st[ks] == Status.MATCH) & (np.asarray(fm.flip)[ks] == bool(seg.flip_h))
    ks = ks[sel]
    lo = np.asarray(fm.soft_lo)[ks].astype(np.int64)
    hi = np.asarray(fm.soft_hi)[ks].astype(np.int64)
    raw = np.asarray(fm.raw)[ks].astype(np.int64)
    lo = np.where(lo >= 0, np.minimum(lo, raw), raw)
    hi = np.where(hi >= 0, np.maximum(hi, raw), raw)
    extra = seg.__dict__.get("_phase_extra") or {}
    if extra:
        cons = {int(k): (int(a), int(b)) for k, a, b in zip(ks, lo, hi)}
        for k, (a, b) in extra.items():
            if seg.comp_in <= int(k) < seg.comp_out:
                cons[int(k)] = (int(a), int(b))
        kk = np.array(sorted(cons), dtype=np.int64)
        return kk, np.array([cons[k][0] for k in kk], np.int64), np.array([cons[k][1] for k in kk], np.int64)
    return ks.astype(np.int64), lo, hi
