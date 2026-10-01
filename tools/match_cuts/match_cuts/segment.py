"""Segmentation, transitions and per-segment framing (prompt Stage 5.4-5.5; DESIGN.md §5 segment.py).

Input: the per-frame map m(k) (``FrameMap``) from refine.py. Output: ``list[Segment]`` in competitor
order (ids 1..N), placeholders included, with speed / speed_range / speed_measured / unsnapped, flip,
transform (+ keys), transitions, remap keys, confidence, ambiguous / timing-tie / low-margin frames and
notes filled in. raw_in_seconds & co. are filled with the same phase solve the pipeline runs afterwards
(``segment_constraints`` gives the exact constraints to use).

Algorithm overview
  1. Runs. The timeline is split into runs of MATCH / NONE / UNIFORM frames; MATCH runs are split further
     at flip changes and at framing steps the pixels CONFIRM (FX-06: detrended per-frame increments -- found at
     any pan speed --, both sides' trends extrapolated across the step, the old framing winning before and the
     new one after on the frames around it); unconfirmed steps are only DP candidates.
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
  3. Clean-up (FX-04: neighbours' framing EXTRAPOLATED / re-measured, never held): 1-2 frame segments explained
     by a neighbour's model are merged (matching errors, re-checked after the refit); the others are verified
     flash cuts only when their own frames beat the neighbours by more than the noise (else uncertain);
     adjacent compatible segments are merged when one model is
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
     cuts. Then the UNION TEST (FX-04, FX-07): a triggered cut (criterion-2 oscillation, competitor repeat
     pair, confounded frames, short tracks, time lines that meet) must beat the continuous hypothesis on the
     pixels; inside the noise the repeat pair / audio decide, else it is kept and reported uncertain. A NONE
     frame repeating a matched frame takes its RAW frame when it scores alike (comp-duplicate invariant).
  7. Retiming (frame blending), freeze / reverse / ramps (remap keys), framing (FX-06: consistent (RAW frame,
     Sim) samples on every frame -- re-assigned frames measured again --, least-squares AE-linear keys at the
     measured noise, rotation by a pixel test, full-affine check when the similarity fits poorly), time ties
     (segments on one time line share one phase solve), phase solve
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


def _rng(frames: Iterable[int], cap: int = 12) -> str:
    """'3-7, 12, 20-21' for sorted runs of frame numbers (at most ``cap`` runs)."""
    fr = sorted(set(int(f) for f in frames))
    runs: list[list[int]] = []
    for f in fr:
        if runs and f == runs[-1][1] + 1:
            runs[-1][1] = f
        else:
            runs.append([f, f])
    txt = ", ".join(f"{a}-{b}" if b > a else f"{a}" for a, b in runs[:cap])
    return txt + (f" (+{len(runs) - cap} more)" if len(runs) > cap else "")


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
        # FX-03 refine: the Sim columns are the run's smooth path, sim_meas the per-frame ECC measurement of RAW
        # m(k); 'confounded' frames tie with m+-1 under its own path; pair_label = comp-only temporal labels
        self.sim_meas = np.asarray(fm.sim_meas).astype(np.float64)
        self.sim_meas_score = np.asarray(fm.sim_meas_score).astype(np.float64)
        self.confounded = np.asarray(fm.confounded).astype(bool)
        self.pair_label = np.asarray(fm.pair_label).astype(np.int64)
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

    def measured(self, k: int) -> Sim | None:
        """refine's per-frame framing measurement of RAW m(k) (sim_meas), when it scores at least like the path
        value (a failed ECC that fell back below the path is not a measurement); None otherwise."""
        if not (0 <= k < self.n):
            return None
        v = self.sim_meas[k]
        if not (np.all(np.isfinite(v)) and v[0] > 0):
            return None
        z, zp = self.sim_meas_score[k], self.score[k]
        if math.isfinite(zp) and not (math.isfinite(z) and z >= zp - self.delta[k]):
            return None
        return Sim(float(v[0]), float(v[1]), float(v[2]), float(v[3]))

    def repeat_pair(self, c: int) -> bool:
        """Comp frames (c-1, c) are a competitor REPEAT pair (temporal.py label of pair c-1)."""
        return 0 < c < self.n and int(self.pair_label[c - 1]) == 1


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
    c2_oscillation: dict | None = None          # criterion-2 verdict of the cut at a (positions, scores, repeat pair)

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
        self.unreliable_sim: set[int] = set()   # transition frames before a localised framing step (no measurement)
        self.step_after: dict[int, list[Sim]] = {}   # frames after a localised step inside its stretch -> ECC inits
        self.soft_steps: set[int] = set()       # framing-step candidates the pixels did not confirm (DP candidates)
        self.hard_steps: set[int] = set()       # framing steps the pixels confirmed (cuts no merge may remove)
        self.union_cuts: set[int] = set()       # extra union-test triggers from the caller (e.g. large J/L, FX-09)
        self.time_conflicts: set[int] = set()   # re-assigned frames whose own RAW frame still wins (framing samples)
        self.scene_changes: list[int] = []      # PySceneDetect changes (independent evidence of the union test)
        self._ecc: dict[tuple, tuple[Sim, float] | None] = {}   # (k, j, flip) -> re-measured framing (or None)
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

    # ---------------------------------------------------------------------------------------------
    # framing steps (FX-06): detrended increments, trends extrapolated across, confirmed by the pixels
    # ---------------------------------------------------------------------------------------------
    def _framing_vec(self, sims_or_idx: Any, pref: np.ndarray | None = None, log_scale: bool = True
                     ) -> tuple[np.ndarray, np.ndarray]:
        """(V, pref): per frame [Px, Py, log s (or s), theta deg], Px / Py = the image of the RAW point ``pref``
        (default: the box-centre pre-image of the median Sim) -- decorrelates scale and translation. Input: FrameMap
        frame indices or a list of Sims."""
        if isinstance(sims_or_idx, np.ndarray):
            s, th, tx, ty = self._sim_arrays(sims_or_idx)
        else:
            s = np.array([x.s for x in sims_or_idx], np.float64)
            th = np.array([x.theta_deg for x in sims_or_idx], np.float64)
            tx = np.array([x.tx for x in sims_or_idx], np.float64)
            ty = np.array([x.ty for x in sims_or_idx], np.float64)
        if pref is None:
            med = Sim(float(np.median(s)), float(np.median(th)), float(np.median(tx)), float(np.median(ty)))
            pref = med.inverse().apply([self.center])[0]
        c, sn = np.cos(np.radians(th)), np.sin(np.radians(th))
        px = s * (c * pref[0] - sn * pref[1]) + tx
        py = s * (sn * pref[0] + c * pref[1]) + ty
        return np.c_[px, py, np.log(s) if log_scale else s, th], pref

    @staticmethod
    def _vec_sim(v: np.ndarray, pref: np.ndarray, log_scale: bool = True) -> Sim:
        """Inverse of ``_framing_vec`` for one row."""
        s = float(math.exp(v[2])) if log_scale else float(v[2])
        th = float(v[3])
        c, sn = math.cos(math.radians(th)), math.sin(math.radians(th))
        return Sim(s, th, float(v[0] - s * (c * pref[0] - sn * pref[1])), float(v[1] - s * (sn * pref[0] + c * pref[1])))

    @staticmethod
    def _line(ks: np.ndarray, V: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Least-squares line V(t) = a + b t through the rows (one row: constant)."""
        if len(ks) < 2:
            return V[0].astype(np.float64).copy(), np.zeros(V.shape[1])
        A = np.c_[np.ones(len(ks)), np.asarray(ks, np.float64)]
        coef, *_ = np.linalg.lstsq(A, V, rcond=None)
        return coef[0], coef[1]

    def transform_steps(self, r0: int, r1: int) -> list[int]:
        """Comp frames k (cut before k) where the framing STEPS inside a MATCH run (punch-in / reframe; FX-06 4).

        The per-frame increments of the framing (box-centre pre-image position, log scale, rotation) minus the
        median of the 2 x 2 framing_sample_step increments around them -- a pan's own velocity and a velocity knot
        cancel, so the speed of a pan never hides a step (the S60 1-frame snap-back inside a 0.92 px/frame pan
        did) -- are 'fast' above
        punch_*_step / (2 framing_sample_step) per frame. For every fast stretch [ka, kb] of at most
        2 framing_sample_step frames the linear trends of up to framing_sample_step + 1 frames on each side,
        EXTRAPOLATED across it (never held), must differ by more than punch_pos_step px or punch_scale_step
        everywhere in between (trends that meet inside the stretch are a velocity change). The step is then
        localised and confirmed by the pixels (``_localise_step``): confirmed steps are returned (cuts),
        unconfirmed ones are only DP candidates (``soft_steps``)."""
        F, cfg = self.F, self.cfg
        idx = np.arange(r0, r1)
        good = np.isfinite(F.s[idx]) & (F.s[idx] > 0)
        idx = idx[good]
        if idx.size < 2:
            return []
        thr_s = float(_cfg(cfg, "punch_scale_step", 0.01))
        thr_p = float(_cfg(cfg, "punch_pos_step", 4.0))
        fs = max(1, int(_cfg(cfg, "framing_sample_step", 3)))
        W, L = 2 * fs, fs + 1
        V, pref = self._framing_vec(idx)
        span = np.maximum(np.diff(idx), 1).astype(np.float64)
        dV = np.diff(V, axis=0) / span[:, None]
        m = dV.shape[0]
        med = np.empty_like(dV)
        for i in range(m):        # +-W increments: a step spread over up to W frames stays a minority
            med[i] = np.median(dV[max(0, i - W):min(m, i + W + 1)], axis=0)
        e = dV - med
        exc_p = np.hypot(e[:, 0], e[:, 1]) + np.abs(np.radians(e[:, 3])) * self.box_r
        exc_s = np.abs(np.expm1(e[:, 2]))
        fast = (exc_s > thr_s / W) | (exc_p > thr_p / W)
        steps: list[int] = []
        i = 0
        while i < m:
            if not fast[i]:
                i += 1
                continue
            j = i
            while j + 1 < m and fast[j + 1]:
                j += 1
            ia, ib = i, j + 1                            # index of the last frame before / first after the stretch
            ka, kb = int(idx[ia]), int(idx[ib])
            i = j + 1
            if kb - ka > W:
                continue                                 # a slow change is animation, not a step
            left, right = slice(max(0, ia - L + 1), ia + 1), slice(ib, ib + L)
            lineA = self._line(idx[left], V[left])
            lineB = self._line(idx[right], V[right])
            # how well each side follows its own trend: a jump must stand out of that noise (3x) -- two wrong,
            # alternating tracks (the confound) never make a step
            res_p, res_s = 0.0, 0.0
            for sl, (la, lb) in ((left, lineA), (right, lineB)):
                R_ = V[sl] - (la + np.outer(idx[sl], lb))
                res_p = max(res_p, float(np.max(np.hypot(R_[:, 0], R_[:, 1]) + np.abs(np.radians(R_[:, 3])) * self.box_r)))
                res_s = max(res_s, float(np.max(np.abs(np.expm1(R_[:, 2])))))
            ts = np.linspace(ka, kb, 2 * (kb - ka) + 1)
            D = (lineA[0] + np.outer(ts, lineA[1])) - (lineB[0] + np.outer(ts, lineB[1]))
            dpos = np.hypot(D[:, 0], D[:, 1]) + np.abs(np.radians(D[:, 3])) * self.box_r
            dsc = np.abs(np.expm1(D[:, 2]))
            jump = np.maximum(dpos / max(thr_p, 3.0 * res_p), dsc / max(thr_s, 3.0 * res_s))
            t = int(np.argmin(jump))
            if jump[t] <= 1.0:
                continue                                 # the trends meet inside the stretch: a velocity change
            cut, ok = self._localise_step(ka, kb, lineA, lineB, pref, float(dsc[t]), float(dpos[t]), r0, r1)
            if ok:
                steps.append(cut)
                self.hard_steps.add(cut)
            else:
                self.soft_steps.add(cut)
        return steps

    def _localise_step(self, ka: int, kb: int, lineA: tuple, lineB: tuple, pref: np.ndarray, ds: float, dp: float,
                       r0: int, r1: int) -> tuple[int, bool]:
        """(cut, confirmed) of a framing step between frames ka (old framing) and kb (new framing). Both sides'
        trends are EXTRAPOLATED over the frames around the stretch (simA / simB). With pixels every MATCH frame in
        [ka - n + 1, kb + n) (n = step_confirm_frames) is scored under both on its own RAW frame: the cut is the
        first frame after ka where the new framing wins by > 3 delta_k, CONFIRMED only when the old framing wins by
        > 3 delta_k on the (up to n) scored frames before it and the new one on the (up to n) frames from it --
        consecutive steps are validated too (FX-06 4). Without pixels the FrameMap decides (consecutive frames: the
        cut is kb; a stretch: where half of the change is reached). Only the transition frames (ka, cut) lose their
        Sims (``unreliable_sim``); frames [cut, kb) are measured again (``step_after``: their ECC inits)."""
        F = self.F
        nb = max(1, int(_cfg(self.cfg, "step_confirm_frames", 2)))
        flip = bool(F.flip[ka])

        def simA(f: float) -> Sim:
            return self._vec_sim(lineA[0] + lineA[1] * f, pref)

        def simB(f: float) -> Sim:
            return self._vec_sim(lineB[0] + lineB[1] * f, pref)
        ev: dict = {"from": ka, "to": kb, "d_scale": ds, "d_pos_px": dp}
        if not self.P.ok:
            confirmed = True
            if kb == ka + 1:
                cut, how = kb, "consecutive_step"
            else:
                how = "sim_midpoint"
                thr_s = float(_cfg(self.cfg, "punch_scale_step", 0.01))
                thr_p = float(_cfg(self.cfg, "punch_pos_step", 4.0))
                ks_ = np.arange(ka, kb + 1)
                ks_ = ks_[np.isfinite(F.s[ks_]) & (F.s[ks_] > 0)]
                ds_, dp_ = self._change(np.full(ks_.size, ka), ks_)
                prog = ds_ / thr_s + dp_ / thr_p
                tot = max(float(prog[-1]), 1e-12)
                cut = next((int(k) for k, p_ in zip(ks_, prog) if p_ / tot >= 0.5), kb)
                cut = max(ka + 1, min(kb, cut))
            how += " (no pixels: FrameMap only)"
        else:
            rows: dict[int, tuple[float, float]] = {}
            for f in range(max(r0, ka - nb + 1), min(r1, kb + nb)):
                if F.status[f] != Status.MATCH or F.raw[f] < 0 or bool(F.flip[f]) != flip:
                    continue
                sc = self.P.zncc_set(f, [(int(F.raw[f]), simA(f), flip), (int(F.raw[f]), simB(f), flip)])
                if sc is None or not np.all(np.isfinite(sc)):
                    continue
                rows[f] = (float(sc[0]), float(sc[1]))
            ev["scores"] = {str(f): [round(a_, 6), round(b_, 6)] for f, (a_, b_) in sorted(rows.items())}
            cut = next((f for f in sorted(rows) if ka < f <= kb and rows[f][1] - rows[f][0] > 3.0 * F.delta[f]), None)
            if cut is None:
                confirmed, how = False, "not_confirmed: the new framing never wins by > 3 delta"
                cut = kb
            else:
                before = [f for f in sorted(rows) if f < cut][-nb:]
                after = [f for f in sorted(rows) if f >= cut][:nb]
                confirmed = bool(before) and bool(after) and \
                    all(rows[f][0] - rows[f][1] > 3.0 * F.delta[f] for f in before) and \
                    all(rows[f][1] - rows[f][0] > 3.0 * F.delta[f] for f in after)
                how = "scored_both_transforms" if confirmed else \
                    "not_confirmed: the old framing does not win before the step or the new one after it"
        ev["method"] = how
        if confirmed:
            self.unreliable_sim.update(range(ka + 1, cut))
            for f in range(cut, kb):
                self.step_after[f] = [simB(f), simA(f)]
            self.log("transform_step", comp_frame=int(cut), evidence=ev)
        else:
            self.log("transform_step_unconfirmed", comp_frame=int(cut), evidence=ev)
        return int(cut), bool(confirmed)

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
        # framing steps the pixels did not confirm are candidates only (FX-04 1): a cut there must be bought by
        # the time evidence like any other
        cands.update(c for c in self.soft_steps if r0 < c < r1)
        cands.update(c for c in self.scene_changes if r0 < c < r1)     # PySceneDetect changes (FX-06 6)
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

    def cut_cost(self, c: int) -> float:
        """lambda_cut, + lambda_repeat_cut for a time cut between the two frames of a competitor REPEAT pair (FX-07:
        both frames show the same image; soft evidence -- a cut there must be bought by more time evidence)."""
        return self.S.l_cut + (float(_cfg(self.cfg, "lambda_repeat_cut", 1.0)) if self.F.repeat_pair(c) else 0.0)

    def dp_run(self, r0: int, r1: int) -> list[_Seg]:
        P = self.candidates(r0, r1)
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
                lamq = self.cut_cost(q) if q > r0 else 0.0
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
    # framing (FX-06): consistent (RAW frame, Sim) samples -> constant or measured AE-linear keys
    # ---------------------------------------------------------------------------------------------
    def _model_key(self, seg: _Seg) -> tuple | None:
        m = seg.model
        if m is None:
            return None
        ri = m.sol.get("raw_in", float("nan")) if m.sol else float("nan")
        return (round(float(ri), 9) if math.isfinite(ri) else None, float(m.v), int(m.comp_in))

    def _remeasure(self, k: int, j: int, flip: bool, inits: Sequence[Sim | None]) -> tuple[Sim, float] | None:
        """Framing of RAW j on comp frame k measured by ECC (refine.ecc_measure: coarse-to-fine, every init a start):
        (Sim, masked ZNCC on this module's scorer) of a converged result, None without pixels / convergence."""
        inits = [s for s in inits if s is not None and math.isfinite(s.s) and s.s > 0]
        if not self.P.ok or not inits or not (0 <= k < self.n) or getattr(self.comp, "frames", None) is None \
                or getattr(self.raw, "frames", None) is None or not self.raw.has(int(j)):
            return None
        key = (int(k), int(j), bool(flip), tuple((round(s.s, 6), round(s.theta_deg, 4), round(s.tx, 2), round(s.ty, 2))
                                                 for s in inits))
        if key in self._ecc:
            return self._ecc[key]
        from .refine import ecc_measure
        out = None
        try:
            r = ecc_measure(np.asarray(self.comp.get(int(k))), np.asarray(self.raw.get(int(j))), inits[0], bool(flip),
                            self.P.raw_w, tuple(self.raw.ratio), tuple(self.comp.ratio), self.P.allowed(int(k)), self.cfg,
                            roi=self.P.roi, starts=inits[1:])
            # the best-scoring of the ECC optimum and the inits (an exact init is not moved by ECC: 'not converged');
            # accepted when ECC converged or the framing matches the frame (>= match_thresh - anchor_zncc_slack)
            cands = [r.sim] + inits
            z = self.P.zncc_set(int(k), [(int(j), s, bool(flip)) for s in cands])
            if z is not None and np.isfinite(z).any():
                i = int(np.nanargmax(z))
                thr = float(_cfg(self.cfg, "match_thresh", 0.9)) - float(_cfg(self.cfg, "anchor_zncc_slack", 0.05))
                if (r.converged and i == 0) or float(z[i]) >= thr:
                    out = (cands[i], float(z[i]))
        except Exception as e:  # noqa: BLE001 - a failed measurement is 'no measurement', never fatal
            log.debug("segment: ECC re-measurement of k=%d j=%d failed: %s", k, j, e)
        self._ecc[key] = out
        return out

    def _framing_samples(self, seg: _Seg) -> tuple[list[tuple[int, Sim, int]], dict]:
        """(k, Sim, RAW frame shown) framing samples of a segment and {'remeasured', 'dropped', 'conflict'} frames.

        Where the segment shows the RAW frame refine measured (or one visually identical to it) the sample is refine's
        per-frame measurement (sim_meas; the path value when the ECC fell back). A frame the segment RE-ASSIGNS to
        another RAW frame carries a Sim fitted to that other frame (FX-06 1: the merge at 1444 imported 2066's Sim onto
        2064, ZNCC 0.685): it is measured again by ECC on the shown frame (inits: the path, the trend of the samples
        so far), else left out -- and when its own frame still wins by > 3 delta_k it is a 'conflict' (evidence that
        the segment's time model is wrong there). Frames after a localised framing step inside its stretch are
        measured again the same way; transition frames before it are left out."""
        F = self.F
        has = seg.model is not None and math.isfinite(seg.model.sol.get("raw_in", float("nan"))
                                                       if seg.model.sol else float("nan"))
        out: list[tuple[int, Sim, int]] = []
        info: dict[str, list] = {"remeasured": [], "dropped": [], "conflict": []}
        for k in range(seg.a, seg.b):
            if F.status[k] != Status.MATCH or bool(F.flip[k]) != seg.flip or k in self.unreliable_sim:
                continue
            j = int(self.pred(seg, k)) if has else int(F.raw[k])
            own = bool(F.raw_lo[k] <= j <= F.raw_hi[k])
            meas = F.measured(k) if own else None
            if own and k not in self.step_after:
                s = meas or F.sim(k)
                if s is not None:
                    out.append((k, s, j))
                continue
            if meas is not None:                       # after a step: refine's own per-frame measurement
                out.append((k, meas, j))
                continue
            inits: list[Sim | None] = list(self.step_after.get(k, []))
            if len(out) >= 2 and out[-1][0] - out[-2][0] > 0:      # previous sample + its velocity
                (k1, s1, _), (k0, s0, _) = out[-1], out[-2]
                u = (k - k1) / float(k1 - k0)
                inits.append(Sim(s1.s + u * (s1.s - s0.s), s1.theta_deg + u * (s1.theta_deg - s0.theta_deg),
                                 s1.tx + u * (s1.tx - s0.tx), s1.ty + u * (s1.ty - s0.ty)))
            elif out:
                inits.append(out[-1][1])
            inits.append(F.sim(k))
            r = self._remeasure(k, j, seg.flip, inits)
            if r is None:
                info["dropped"].append(k)
                continue
            out.append((k, r[0], j))
            info["remeasured"].append(k)
            if not own and F.sim(k) is not None and F.raw[k] >= 0:
                z = self.P.zncc_set(k, [(int(F.raw[k]), F.measured(k) or F.sim(k), seg.flip)])
                if z is not None and math.isfinite(float(z[0])) and float(z[0]) > r[1] + 3.0 * F.delta[k]:
                    info["conflict"].append(k)
        return out, info

    @staticmethod
    def _lsq_keys(t: np.ndarray, V: np.ndarray, knots: Sequence[int]) -> np.ndarray:
        """Least-squares values at the knot samples of the piecewise-linear (AE-linear) curve through V(t)."""
        T = t[list(knots)]
        n, m = len(t), len(T)
        A = np.zeros((n, m))
        i = np.clip(np.searchsorted(T, t, side="right") - 1, 0, m - 2)
        u = (t - T[i]) / (T[i + 1] - T[i])
        A[np.arange(n), i] = 1.0 - u
        A[np.arange(n), i + 1] += u
        coef, *_ = np.linalg.lstsq(A, V, rcond=None)
        return coef

    def _rotation_test(self, tk: np.ndarray, jk: Sequence[int], knot_t: np.ndarray, keysV: np.ndarray,
                       pref: np.ndarray, flip: bool) -> tuple[bool, str]:
        """Rotation by a pixel test, not a vote (FX-06 5): up to 8 sample frames scored under the fitted keys and
        under the same keys with theta = 0 (derotated about the box-centre pre-image); the rotation is kept only
        when it wins by > 3 soft_delta_max on most of them."""
        dmax = float(_cfg(self.cfg, "soft_delta_max", 0.01))
        k0V = keysV.copy()
        k0V[:, 3] = 0.0
        pick = np.unique(np.linspace(0, len(tk) - 1, min(8, len(tk))).round().astype(int))
        wins = scored = 0
        for i in pick:
            k = int(tk[i])
            vr = np.array([np.interp(k, knot_t, keysV[:, c]) for c in range(4)])
            v0 = np.array([np.interp(k, knot_t, k0V[:, c]) for c in range(4)])
            sc = self.P.zncc_set(k, [(int(jk[i]), self._vec_sim(vr, pref, False), flip),
                                     (int(jk[i]), self._vec_sim(v0, pref, False), flip)])
            if sc is None or not np.all(np.isfinite(sc)):
                continue
            scored += 1
            wins += int(float(sc[0]) - float(sc[1]) > 3.0 * dmax)
        keep = scored > 0 and wins > scored / 2.0
        return keep, f"rotation pixel test: theta wins on {wins}/{scored} sampled frames"

    def _fit_framing(self, ks: np.ndarray, sims: list[Sim], js: Sequence[int], flip: bool) -> dict:
        """Constant transform or AE-linear keys from per-frame samples (FX-06 2): samples off the local trend of their
        neighbours removed (refine's outlier rule: a wrong-frame measurement), median-3 smoothed; knots by RDP at the
        measured noise (>= rdp_pos_tol px / rdp_scale_tol / 0.05 deg), key values by least squares over the samples
        (a step stays a step -- segmentation cuts there -- and a velocity knot stays a knot), then max-error
        refinement: the worst sample further than the tolerance from the keys becomes a key until none is. The first
        and last keys sit on the first and last sample. Rotation only when the pixels prefer it (``_rotation_test``)."""
        from .refine import _local_line_dev, _running_median
        cfg = self.cfg
        V, pref = self._framing_vec(list(sims), log_scale=False)          # [Px, Py, s, theta]
        t = np.asarray(ks, np.float64)
        n = len(t)
        s_med = float(np.median(V[:, 2]))
        keep = np.ones(n, bool)
        if n >= 4:
            w = int(_cfg(cfg, "path_median", 5))
            for c, floor in ((0, 1.0), (1, 1.0), (2, 0.002 * s_med), (3, 0.1)):
                dev = _local_line_dev(t, V[:, c], w)
                sig = 1.4826 * float(np.median(np.abs(dev)))
                keep &= np.abs(dev) <= max(3.5 * sig, floor)
            if keep.sum() < 2:
                keep[:] = True
        out_k = [int(k) for k in np.asarray(ks)[~keep]]
        tk, Vk = t[keep], V[keep]
        jk = [j for j, kp in zip(js, keep) if kp]
        Vs = np.stack([_running_median(Vk[:, c], 3) for c in range(4)], axis=1)
        # reference curve: the local least-squares line through each sample and its 2 + 2 neighbours (measurement
        # noise -- correlated over the frames that show one RAW frame -- averages out, a velocity knot only rounds)
        R = Vs.copy()
        if len(tk) >= 5:
            for i in range(len(tk)):
                a = min(max(0, i - 2), len(tk) - 5)
                a_, b_ = self._line(tk[a:a + 5], Vk[a:a + 5])
                R[i] = a_ + b_ * tk[i]
        # measurement noise: robust spread of the samples around the reference (white noise: residual std ~ 0.89
        # sigma) and around the chord of their two neighbours (catches an alternation; ~ 1.22 sigma)
        sig = np.zeros(4)
        if len(tk) >= 3:
            u = (tk[1:-1] - tk[:-2]) / (tk[2:] - tk[:-2])
            r = Vk[1:-1] - (Vk[:-2] + u[:, None] * (Vk[2:] - Vk[:-2]))
            sig = 1.4826 * np.median(np.abs(r), axis=0) / 1.2247
        if len(tk) >= 5:
            sig = np.maximum(sig, 1.4826 * np.median(np.abs(Vk - R), axis=0) / 0.8944)
        s_spread = float(np.ptp(R[:, 2]) / s_med)          # spreads of the reference: measurement noise averaged
        p_spread = float(max(np.ptp(R[:, 0]), np.ptp(R[:, 1])) + np.radians(np.ptp(R[:, 3])) * self.box_r)
        rot_min = float(_cfg(cfg, "rotation_min_deg", 0.2))
        stable = (s_spread < float(_cfg(cfg, "framing_scale_spread", 0.003))
                  and p_spread < float(_cfg(cfg, "framing_pos_spread", 1.5)))
        notes: list[str] = []
        if out_k:
            notes.append(f"framing: {len(out_k)} sample(s) off the local trend of their neighbours left out "
                         f"({_rng(out_k)})")
        if stable or len(tk) < 2:
            keysV = np.median(Vk, axis=0)[None, :]
            knot_t = tk[:1]
        else:
            tol = np.array([max(float(_cfg(cfg, "rdp_pos_tol", 0.5)), 3.5 * sig[0]),
                            max(float(_cfg(cfg, "rdp_pos_tol", 0.5)), 3.5 * sig[1]),
                            max(float(_cfg(cfg, "rdp_scale_tol", 0.001)) * s_med, 3.5 * sig[2]),
                            max(0.05, 3.5 * sig[3])])
            knots = sorted(rdp(np.c_[tk, R], tol))

            def max_err(kn: list[int]) -> tuple[float, int, np.ndarray]:
                kv = self._lsq_keys(tk, Vk, kn)
                model = np.stack([np.interp(tk, tk[kn], kv[:, c]) for c in range(4)], axis=1)
                err = np.max(np.abs(model - Vk) / tol, axis=1)
                w_ = int(np.argmax(err))
                return float(err[w_]), w_, kv
            e_, wst, keysV = max_err(knots)
            while e_ > 1.0 + 1e-9 and wst not in knots:
                knots = sorted(set(knots) | {wst})
                e_, wst, keysV = max_err(knots)
            # knots the least-squares values made redundant (the reference rounds a velocity knot over a few
            # frames): dropped while every sample stays within the tolerance
            while len(knots) > 2:
                trials = [(max_err(knots[:q] + knots[q + 1:]), q) for q in range(1, len(knots) - 1)]
                (e2, _w2, kv2), q = min(trials, key=lambda t_: (t_[0][0], t_[1]))
                if e2 > max(1.0, e_) + 1e-9:
                    break
                knots = knots[:q] + knots[q + 1:]
                keysV = kv2
            knot_t = tk[knots]
        use_rot = bool(np.max(np.abs(Vs[:, 3])) > rot_min)
        if use_rot and self.P.ok:
            use_rot, how = self._rotation_test(tk, jk, knot_t, keysV, pref, flip)
            notes.append(how + ("" if use_rot else ": rotation set to 0"))
        if not use_rot:
            keysV = keysV.copy()
            keysV[:, 3] = 0.0
        spread = {"scale": s_spread, "pos_px": p_spread}
        if len(keysV) == 1:
            t0 = self._vec_sim(keysV[0], pref, False)
            return {"transform": t0.to_dict(), "keys": [], "easing": "linear", "animated": False, "notes": notes,
                    "spread": spread}
        keys = []
        for tt, v in zip(knot_t, keysV):
            sk = self._vec_sim(v, pref, False)
            keys.append({"comp_frame": int(tt), "scale": float(sk.s), "rotation_deg": float(sk.theta_deg),
                         "tx": float(sk.tx), "ty": float(sk.ty)})
        easing = _easing(tk, Vs[:, 2], Vs[:, 0], Vs[:, 1])
        notes.append(f"animated framing: {len(keys)} keys, scale {Vs[:, 2].min():.4f}->{Vs[:, 2].max():.4f}, "
                     f"easing {easing}")
        return {"transform": {k: keys[0][k] for k in ("scale", "rotation_deg", "tx", "ty")}, "keys": keys,
                "easing": easing, "animated": True, "notes": notes, "spread": spread}

    def framing(self, seg: _Seg, force: bool = False) -> dict:
        """Framing of a raw segment (prompt 5.5, FX-06): samples from ``_framing_samples`` (consistent (RAW frame,
        Sim) pairs), summarised by ``_fit_framing``. Every key is a measurement-backed value; frames the samples do
        not reach at the segment's edges are reported (AE holds the edge key there), never extrapolated."""
        key = (seg.a, seg.b, self._model_key(seg), bool(seg.flip))
        if seg.framing is not None and not force and seg.framing.get("_key") == key:
            return seg.framing
        F = self.F
        samples, info = self._framing_samples(seg)
        notes: list[str] = []
        if not samples:
            # nearest frame with a transform (placeholder-adjacent tiny segments)
            near = [k for k in range(max(0, seg.a - 30), min(self.n, seg.b + 30))
                    if F.sim(k) is not None and bool(F.flip[k]) == seg.flip]
            near = sorted(near, key=lambda k: abs(k - seg.a))[:1]
            if near:
                samples = [(near[0], F.measured(near[0]) or F.sim(near[0]), int(F.raw[near[0]]))]
                notes.append(f"no measured framing inside the segment: frame {near[0]}'s used")
        if not samples:
            fr = {"transform": Sim.identity().to_dict(), "keys": [], "easing": "linear", "animated": False,
                  "_range": (seg.a, seg.b), "_key": key, "notes": ["no transform measured"], "_samples": {}}
            seg.framing = fr
            return fr
        ks = np.array([k for k, _, _ in samples], np.int64)
        fr = self._fit_framing(ks, [s for _, s, _ in samples], [j for _, _, j in samples], seg.flip)
        notes = notes + fr.pop("notes")
        if info["remeasured"]:
            notes.append(f"framing measured again on frames {_rng(info['remeasured'])} (RAW frame differs from "
                         "refine's, or after a framing step)")
        if info["dropped"]:
            notes.append(f"framing not measurable on frames {_rng(info['dropped'])} (left out)")
        if info["conflict"]:
            notes.append(f"frames {_rng(info['conflict'])}: refine's own RAW frame still matches better than the "
                         "segment's (re-measured) -- time model questionable there")
            self.time_conflicts.update(info["conflict"])
            self.log("framing_time_conflict", comp_range=[seg.a, seg.b], evidence={"frames": info["conflict"]})
        match = [k for k in range(seg.a, seg.b) if F.status[k] == Status.MATCH and bool(F.flip[k]) == seg.flip]
        if match and samples and (int(ks[0]) > match[0] or int(ks[-1]) < match[-1]):
            held = [k for k in match if k < int(ks[0]) or k > int(ks[-1])]
            notes.append(f"edge framing not measured on frames {_rng(held)} (the edge key is held there)")
        fr["_range"] = (seg.a, seg.b)
        fr["_key"] = key
        fr["_ks"] = [int(k) for k in ks]
        fr["notes"] = notes
        fr["_samples"] = {int(k): (int(j), s) for k, s, j in samples if k in set(info["remeasured"])}
        seg.framing = fr
        return fr

    def sim_at(self, seg: _Seg, k: int) -> Sim:
        """The segment's framing at comp frame k: AE-linear between its keys; beyond the first / last key the edge
        segment is EXTRAPOLATED for at most framing_sample_step frames (then held) -- a pan's framing held at its
        last key lags v px per frame and makes every neighbour test (criterion 2, merges) unfair (FX-04 4)."""
        fr = self.framing(seg)
        if fr["keys"]:
            from .refine import _sim_at
            cap = max(1, int(_cfg(self.cfg, "framing_sample_step", 3)))
            return _sim_at(fr["keys"], k, (float(self.raw.full_size[0]), float(self.raw.full_size[1])), cap)
        return Sim.from_dict(fr["transform"])

    # ---------------------------------------------------------------------------------------------
    # clean-up passes
    # ---------------------------------------------------------------------------------------------
    def _explains(self, seg: _Seg, k: int) -> tuple[bool, dict]:
        """Does seg's model explain comp frame k (a frame currently outside / at the edge of seg)? Its time line
        extended and its framing EXTRAPOLATED (``sim_at``) and, with pixels, measured again on frame k by ECC from
        there (FX-04 5: the held key of a pan lags v px per frame and always lost). Explained when the model's
        frame scores within 3 delta_k of the frame's own (RAW frame, Sim); ev['own_wins'] = the own pair beats it by
        more than 3 delta_k (positive evidence of a genuine flash cut), ev['score_model'] / ev['score_own']."""
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
            ev["own_wins"] = bool(dfc > 5.0 * F.delta[k])
            return dfc <= 5.0 * F.delta[k], ev
        if self.P.ok:
            own = F.measured(k) or F.sim(k)
            if own is not None and F.raw[k] >= 0:
                sm = self.sim_at(seg, k)
                items = [(j, sm, seg.flip), (int(F.raw[k]), own, bool(F.flip[k]))]
                r = self._remeasure(k, j, seg.flip, [sm, own])
                if r is not None:
                    items.append((j, r[0], seg.flip))
                sc = self.P.zncc_set(k, items)
                if sc is not None and np.all(np.isfinite(sc[:2])):
                    zm = float(np.nanmax([sc[0]] + ([sc[2]] if r is not None else [])))
                    ev["score_model"], ev["score_own"] = zm, float(sc[1])
                    ev["framing"] = "re-measured" if r is not None and float(sc[2]) >= float(sc[0]) else "extrapolated"
                    ev["own_wins"] = bool(sc[1] > zm + 3.0 * F.delta[k])
                    return bool(zm >= sc[1] - 3.0 * F.delta[k]), ev
        return False, ev

    def _widen(self, k: int, j: int, why: str) -> None:
        F = self.F
        F.lo[k] = min(F.lo[k], j)
        F.hi[k] = max(F.hi[k], j)
        F.touched[k] = why

    def _score_model(self, S: _Seg, k: int) -> float:
        """Score of comp frame k under S's model: its RAW frame with the framing extrapolated / interpolated by the
        keys and, with pixels, measured again by ECC from there (the better of the two)."""
        j = int(self.pred(S, k))
        sm = self.sim_at(S, k)
        z = self._frame_score(S, k)
        r = self._remeasure(k, j, S.flip, [sm])
        if r is not None and (not math.isfinite(z) or r[1] > z):
            return r[1]
        return z

    def merge_tiny(self, segs: list[_Seg]) -> list[_Seg]:
        """1-2 frame raw segments: merged into a neighbour whose model explains them (``_explains``: the neighbour's
        time line with its framing extrapolated / re-measured), re-checked AFTER the merged segment's refit (a frame
        that now scores more than 3 delta_k below the check reverts the merge, FX-06 1). The others are logged
        'flash_cut_verified' only when every frame's own (RAW frame, Sim) beats each adjacent neighbour's model by
        more than 3 delta_k; else 'flash_cut_unverified' (segment uncertain, noted). Islands next to a NONE /
        uniform run are reported for the not-in-RAW resolver (FX-08)."""
        changed = True
        checks: dict[int, list] = {}        # id(tiny) -> the explains evidence of its neighbours
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
                    if (t.a if side < 0 else t.b) in self.hard_steps:
                        continue            # a confirmed framing step is a cut (FX-06): never merged across
                    if not self._same_period(min(nb.a, t.a), max(nb.b, t.b)) and \
                            not self._framing_close(t, nb):
                        # a 1-2 frame sliver across a layout period boundary is merged only into a neighbour with
                        # the same framing (the detected boundary is off by a frame or two); otherwise the
                        # period split stands (D1)
                        continue
                    res = [self._explains(nb, k) for k in range(t.a, t.b)]
                    checks.setdefault(id(t), []).append({"neighbour": [nb.a, nb.b], "checks": [r[1] for r in res]})
                    if all(r[0] for r in res):
                        trial = _Seg("raw", min(nb.a, t.a), max(nb.b, t.b), model=nb.model, flip=nb.flip,
                                     track=nb.track, extra=dict(nb.extra))
                        saved = {k: (int(self.F.lo[k]), int(self.F.hi[k])) for k in range(t.a, t.b)}
                        for k in range(t.a, t.b):
                            self._widen(k, int(self.pred(nb, k)), "tiny_segment_merged")
                        ok = self.refit(trial, keep_v=True)
                        drop = []
                        if ok and self.P.ok:
                            # the refit (new phase, new framing fit) must keep explaining the merged frames
                            for k, r in zip(range(t.a, t.b), res):
                                z0 = r[1].get("score_model")
                                if z0 is not None and math.isfinite(z0):
                                    z1 = self._score_model(trial, k)
                                    if not math.isfinite(z1) or z1 < z0 - 3.0 * self.F.delta[k]:
                                        drop.append({"k": k, "checked": z0, "after_refit": z1})
                        if ok and not drop:
                            self.log("merge_tiny_segment", comp_range=[t.a, t.b], evidence={
                                "into": [nb.a, nb.b], "checks": [r[1] for r in res]})
                            segs[min(i, ni)] = trial
                            del segs[max(i, ni)]
                            changed = True
                            break
                        if drop:
                            self.log("merge_tiny_reverted", comp_range=[t.a, t.b], evidence={
                                "into": [nb.a, nb.b], "frames": drop})
                        for k, (lo, hi) in saved.items():
                            self.F.lo[k], self.F.hi[k] = lo, hi
                            self.F.touched.pop(k, None)
                if changed:
                    break
        for i, t in enumerate(segs):
            if t.kind != "raw" or t.length > 2:
                continue
            ev = {"speed": t.model.v, "raw": [int(self.F.raw[k]) for k in range(t.a, t.b)],
                  "score": [float(self.F.score[k]) for k in range(t.a, t.b)], "neighbours": checks.get(id(t), [])}
            nbs = [segs[i + d] for d in (-1, 1) if 0 <= i + d < len(segs)
                   and (segs[i + d].b == t.a if d < 0 else segs[i + d].a == t.b)]
            if any(nb.kind in ("none", "uniform") for nb in nbs):
                self.log("tiny_island_next_to_none", comp_range=[t.a, t.b], evidence=ev)
                t.notes.append(f"{t.length}-frame island next to an unmatched run: left for the not-in-RAW resolver")
            raw_nbs = [c for c in ev["neighbours"]]
            verified = bool(raw_nbs) and all(all(c.get("own_wins") for c in nbc["checks"]) for nbc in raw_nbs)
            if verified:
                self.log("flash_cut_verified", comp_range=[t.a, t.b], evidence=ev)
            else:
                self.log("flash_cut_unverified", comp_range=[t.a, t.b], evidence=ev)
                if raw_nbs:
                    t.uncertain = True
                    t.notes.append(f"{t.length}-frame segment kept but not verified as a flash cut: its own RAW frame "
                                   "does not beat the neighbours' time line by more than the noise")
        return segs

    def _sim_change(self, a: Sim, b: Sim) -> tuple[float, float]:
        """(|scale ratio - 1|, box-centre displacement px incl. rotation at the box edge) between two framings."""
        dp = float(self._centre_shift(a.s, a.theta_deg, a.tx, a.ty, b.s, b.theta_deg, b.tx, b.ty))
        dp += abs(math.radians(b.theta_deg - a.theta_deg)) * self.box_r
        return abs(b.s / a.s - 1.0), dp

    def _framing_jump(self, A: _Seg, B: _Seg) -> tuple[float, float] | None:
        """(scale, position px) jump of the framing at the boundary A|B: the longer side's framing EXTRAPOLATED
        across the boundary (FX-04 4: never held) against the other side's framing at its measured sample nearest
        to the boundary (a short side's constant framing is only known there). None when a side has no measured
        framing."""
        fa, fb = self.framing(A), self.framing(B)
        if not fa.get("_ks") or not fb.get("_ks"):
            return None
        if A.length >= B.length:
            k = min(fb["_ks"])
            return self._sim_change(self.sim_at(A, k), self.sim_at(B, k))
        k = max(fa["_ks"])
        return self._sim_change(self.sim_at(B, k), self.sim_at(A, k))

    def _framing_close(self, t: _Seg, nb: _Seg) -> bool:
        """The framing of tiny segment t matches its neighbour nb's (extrapolated) at their common boundary."""
        if t.flip != nb.flip or t.kind != "raw" or nb.kind != "raw":
            return False
        A, B = (nb, t) if nb.b <= t.a else (t, nb)
        j = self._framing_jump(A, B)
        return bool(j is not None and j[0] <= float(_cfg(self.cfg, "punch_scale_step", 0.01))
                    and j[1] <= float(_cfg(self.cfg, "punch_pos_step", 4.0)))

    def _compatible(self, A: _Seg, B: _Seg) -> bool:
        """Same flip, same layout period and no framing step at the boundary (``_framing_jump`` within
        punch_scale_step / punch_pos_step)."""
        if A.kind != "raw" or B.kind != "raw" or A.flip != B.flip:
            return False
        if not self._same_period(min(A.a, B.a), max(A.b, B.b)):
            return False            # D1: never merge across a layout period boundary
        if A.b == B.a and B.a in self.hard_steps:
            return False            # a framing step the pixels confirmed (FX-06)
        j = self._framing_jump(A, B)
        if j is None:
            return True
        return bool(j[0] <= float(_cfg(self.cfg, "punch_scale_step", 0.01))
                    and j[1] <= float(_cfg(self.cfg, "punch_pos_step", 4.0)))

    def merge_adjacent(self, segs: list[_Seg]) -> list[_Seg]:
        """Merge adjacent compatible raw segments when one model explains both more cheaply."""
        i = 0
        while i + 1 < len(segs):
            A, B = segs[i], segs[i + 1]
            if A.b == B.a and self._compatible(A, B):
                trial = _Seg("raw", A.a, B.b, model=A.model, flip=A.flip, track=A.track,
                             extra={**A.extra, **B.extra})
                if self.refit(trial, keep_v=False) and \
                        trial.model.cost <= self.seg_cost(A) + self.seg_cost(B) + self.cut_cost(B.a) - 1e-9:
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
            if m is not None and m.cost <= self.seg_cost(A) + self.seg_cost(B) + self.cut_cost(B.a) + extra - 1e-9:
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
            sims: dict[int, Sim] = {}
            for k in range(N.a, N.b):
                j = int(self.pred(trial, k))
                sim = self.sim_at(trial, k)
                sc = self.P.zncc_set(k, [(j, sim, trial.flip)])
                if sc is not None and math.isfinite(sc[0]) and sc[0] >= thr:
                    acc.append((k, j, j, "single", float(sc[0]), j))
                    sims[k] = sim
                    continue
                # the neighbours' framing measured again on this frame (FX-04 7), from the interpolated one
                r = self._remeasure(k, j, trial.flip, [sim])
                if r is not None and r[1] >= thr:
                    acc.append((k, j, j, "single_remeasured", float(r[1]), j))
                    sims[k] = r[0]
                    continue
                ok = False
                for j0 in (j - 1, j):
                    bf = self.P.blend_fit(k, [(j0, sim, trial.flip)], [(j0 + 1, sim, trial.flip)])
                    if bf and "zfit" in bf and bf["zfit"] >= float(_cfg(cfg, "match_thresh", 0.9)) \
                            and 0.1 < bf["alpha_a"] < 0.9:
                        heavier = j0 if bf["alpha_a"] >= 0.5 else j0 + 1
                        acc.append((k, j0, j0 + 1, "blend", float(bf["zfit"]), heavier))
                        sims[k] = sim
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
                F.s[k], F.theta[k], F.tx[k], F.ty[k] = sims[k].s, sims[k].theta_deg, sims[k].tx, sims[k].ty
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
            if ok and trial.model.cost <= self.seg_cost(A) + self.seg_cost(B) + self.cut_cost(B.a) + 1e-9:
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

    C2_MAX_MOVES = 3

    def check_cuts(self, segs: list[_Seg]) -> None:
        """Criterion 2 per hard cut: A's last frame must score higher under A's model than B's and B's first
        frame the reverse; otherwise move the cut one frame towards the failing side and refit. A visited set
        guards the mover (it used to oscillate between two positions with identical evidence and stop wherever
        its last iteration ended): on a revisit, or when C2_MAX_MOVES moves did not satisfy criterion 2, every
        visited position is re-evaluated (``_criterion2_verdict``), the one with the best summed score of A's
        frames under A and B's frames under B is kept, and the cut is reported (criterion2_fail with the
        oscillation evidence, a segment note). Cuts that pass are untouched."""
        for i in range(len(segs) - 1):
            A, B = segs[i], segs[i + 1]
            if not (A.kind == "raw" and B.kind == "raw" and A.b == B.a) or B.trans_in is not None \
                    or B.cut_ambiguity is not None:
                continue
            if self._period_break(B.a):
                self.log("criterion2_layout_boundary", comp_frame=int(B.a), evidence={
                    "reason": "cut at a layout period boundary (not moved)"})
                continue
            visited: dict[int, tuple] = {}         # failing cut position -> state at that position
            order: list[int] = []
            history: list[dict] = []
            moves = 0
            while True:
                c = B.a
                if self._same_view(A, B, c - 1) and self._same_view(A, B, c):
                    # both models show the same frame on both sides: moving the cut cannot help (it would only
                    # oscillate); merge_phantom_cuts removes it
                    self.log("criterion2_indistinguishable", comp_frame=int(c), evidence={"cut": c, "iteration": moves})
                    break
                s1 = self._side_scores(A, B, c - 1)
                s2 = self._side_scores(A, B, c)
                ev = {"cut": c, "last_A": s1, "first_B": s2, "iteration": moves}
                if s1 is None or s2 is None:
                    self.log("criterion2_unchecked", comp_frame=int(c), evidence=ev)
                    break
                okA, okB = s1[0] > s1[1], s2[1] > s2[0]
                if okA and okB:
                    self.log("criterion2_pass", comp_frame=int(c), evidence=ev)
                    break
                if c in visited or moves >= self.C2_MAX_MOVES:
                    reason = "oscillation" if c in visited else "moves_exhausted"
                    if c not in visited:
                        visited[c] = self._c2_state(A, B)
                        order.append(c)
                    history.append({"cut": c, "last_A": s1, "first_B": s2})
                    self._criterion2_verdict(A, B, visited, order, history, reason)
                    break
                visited[c] = self._c2_state(A, B)
                order.append(c)
                history.append({"cut": c, "last_A": s1, "first_B": s2})
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
                    moves += 1
                else:
                    A.b, B.a, A.model, B.model, A.extra, B.extra = old
                    self.log("criterion2_fail", comp_frame=int(c), evidence={**ev, "move_infeasible": move})
                    B.notes.append(f"criterion 2 not satisfied at cut {c}; moving it is infeasible")
                    break

    def _c2_state(self, A: _Seg, B: _Seg) -> tuple:
        """Everything a criterion-2 move changes (restored by ``_criterion2_verdict``)."""
        lo, hi = A.a, B.b
        touched = {k: v for k, v in self.F.touched.items() if lo <= k < hi}
        return (A.b, B.a, A.model, B.model, dict(A.extra), dict(B.extra), touched, A.track, B.track)

    def _c2_restore(self, A: _Seg, B: _Seg, st: tuple) -> None:
        A.b, B.a, A.model, B.model = st[0], st[1], st[2], st[3]
        A.extra, B.extra = dict(st[4]), dict(st[5])
        A.track, B.track = st[7], st[8]
        for k in [k for k in self.F.touched if A.a <= k < B.b]:
            self.F.touched.pop(k, None)
        self.F.touched.update(st[6])

    def _frame_score(self, S: _Seg, k: int) -> float:
        """Score of comp frame k under segment S's model (pixels; the candidate vector without them)."""
        j = int(self.pred(S, k))
        if self.P.ok:
            sc = self.P.zncc_set(k, [(j, self.sim_at(S, k), S.flip)])
            if sc is not None and math.isfinite(float(sc[0])):
                return float(sc[0])
        v = self.F.cand_score(k, j)
        return float(v) if math.isfinite(v) else float("nan")

    def _criterion2_verdict(self, A: _Seg, B: _Seg, visited: dict[int, tuple], order: list[int], history: list[dict],
                            reason: str) -> None:
        """Honest verdict of a cut criterion 2 cannot place: re-evaluate every visited position (each with the
        models refitted for it) by the summed score of A's frames under A plus B's frames under B over the frames
        around the visited positions, keep the best, log criterion2_fail with the oscillation evidence (and
        whether a visited position splits a competitor REPEAT pair -- two frames showing the same image, where no
        cut can exist), add a segment note and mark the boundary for the continuous-shot (union) test."""
        pos = sorted(visited)
        w0 = max(A.a, pos[0] - 10)
        w1 = min(B.b, pos[-1] + 10)
        sums: dict[int, float] = {}
        for p in order:
            if p in sums:
                continue
            self._c2_restore(A, B, visited[p])
            tot = 0.0
            for k in range(w0, w1):
                v = self._frame_score(A if k < p else B, k)
                tot += v if math.isfinite(v) else 0.0
            sums[p] = tot
        best = max(order, key=lambda p: (round(sums[p], 9), -order.index(p)))
        self._c2_restore(A, B, visited[best])
        rp = self._repeat_pair_cuts(pos)
        ev = {"cut": best, "reason": reason, "oscillation": order, "scores": {str(p): round(v, 6) for p, v in sums.items()},
              "window": [w0, w1], "repeat_pair": bool(rp), "repeat_pair_cuts": rp, "history": history}
        self.log("criterion2_fail", comp_frame=int(best), evidence=ev)
        what = "oscillated between" if reason == "oscillation" else "was moved over"
        B.notes.append(f"criterion 2 not satisfied: the cut {what} frames {sorted(set(order))}; kept {best} (best summed "
                       f"score of both sides)" + (f"; {', '.join(f'{p - 1}|{p}' for p in rp)} split(s) a competitor repeat "
                                                  "pair (the same image: no cut can exist there)" if rp else "")
                       + "; boundary left for the continuous-shot test")
        B.c2_oscillation = ev

    def _repeat_pair_cuts(self, positions: Sequence[int]) -> list[int]:
        """Visited cut positions p whose frames (p-1, p) are a competitor REPEAT pair (temporal.py, comp-only
        labels: refine's FrameMap pair_label where measured, else measured here around the positions)."""
        F = self.F
        known = [int(p) for p in positions if 0 < int(p) < self.n and int(F.pair_label[int(p) - 1]) >= 0]
        out = [p for p in known if F.repeat_pair(p)]
        positions = [int(p) for p in positions if int(p) not in known]
        if not self.P.ok or not positions:
            return out
        from . import temporal
        max_side = int(_cfg(self.cfg, "temporal_max_side", 200))
        x, y, w, h = self.P.roi
        blur = self.P.blur * temporal.scale_of((h, w), max_side)

        def get(k: int):
            if not (0 <= k < self.comp.n):
                return None
            img = np.asarray(self.comp.get(int(k)))[y:y + h, x:x + w]
            return temporal.prepare(img, np.asarray(self.P.allowed(int(k)))[y:y + h, x:x + w], max_side, blur)
        try:
            lab = temporal.local_labels(get, max(0, min(positions) - 8), min(self.n - 1, max(positions) + 8), self.cfg)
        except Exception as e:  # noqa: BLE001 - evidence only: never break segmentation
            log.debug("segment: temporal labels around %s failed: %s", positions, e)
            return out
        return sorted(out + [int(p) for p in positions if lab.get(int(p) - 1) == temporal.REPEAT])

    # ---------------------------------------------------------------------------------------------
    # union test (FX-04 3/6): a cut must beat the continuous hypothesis
    # ---------------------------------------------------------------------------------------------
    def _hard_pair(self, A: _Seg, B: _Seg) -> bool:
        return bool(A.kind == "raw" and B.kind == "raw" and A.b == B.a and B.trans_in is None and A.trans_out is None
                    and B.cut_ambiguity is None and A.model is not None and B.model is not None and not A.ramp
                    and not B.ramp and self._same_period(A.a, B.b))

    def _lines_meet(self, A: _Seg, B: _Seg) -> bool:
        """B's time line meets A's extended one at the cut: within one competitor frame's worth of RAW time (a
        time-continuous cut: a reframe on one line, or no edit at all; FX-09's 'large J/L where two lines meet')."""
        c = B.a
        try:
            return abs(A.model.pos(c) - B.model.pos(c)) <= self.S.ratio + 1e-9
        except (KeyError, TypeError, ValueError):
            return False

    def _union_triggers(self, A: _Seg, B: _Seg) -> list[str]:
        """Why the cut A|B must face the continuous hypothesis (FX-04 6): criterion 2 could not place it (FX-05), it
        splits a competitor REPEAT pair (FX-07), confounded frames or a chain of >= 3 short refine tracks around it,
        refine's own frame beats a re-assigned one near it, the two time lines meet at it, or the caller asks."""
        F, c = self.F, B.a
        out = []
        osc = B.c2_oscillation or {}
        if osc:
            out.append("criterion2_oscillation")
        # the cut, or a position criterion 2 moved it over (the DP's own cut among them), splits a REPEAT pair
        if F.repeat_pair(c) or any(F.repeat_pair(int(p)) for p in osc.get("oscillation", [])) or \
                osc.get("repeat_pair_cuts"):
            out.append("repeat_pair")
        if F.confounded[max(0, c - 2):min(self.n, c + 2)].any():
            out.append("confounded")
        w = int(_cfg(self.cfg, "union_track_window", 6))
        sl = slice(max(A.a, c - w), min(B.b, c + w))
        tr = F.track[sl][F.status[sl] == Status.MATCH]
        if len(set(int(x) for x in tr) - {-1}) >= 3:
            out.append("short_tracks")
        if any(abs(k - c) <= 2 for k in self.time_conflicts):
            out.append("framing_time_conflict")
        if self._lines_meet(A, B):
            out.append("lines_meet")
        if c in self.union_cuts:
            out.append("caller")
        return out

    def _audio_continuous(self, A: _Seg, B: _Seg) -> bool | None:
        """The coarse audio line (AudioHints, confident windows within 1 s of the cut on both sides) runs on across
        the cut without a lag step (raw_t - speed * comp_t agrees within half a RAW frame): True / False, None when
        the audio does not cover both sides."""
        h = self.hints
        if h is None or len(getattr(h, "comp_t", [])) < 2:
            return None
        conf = h.confident(float(_cfg(self.cfg, "audio_min_conf", 1.3)))
        t = B.a / float(self.cf)
        sp = np.where(np.isfinite(h.speed), h.speed, 1.0)
        off = h.raw_t - sp * h.comp_t
        left = conf & (h.comp_t < t) & (h.comp_t >= t - 1.0)
        right = conf & (h.comp_t > t) & (h.comp_t <= t + 1.0)
        if not left.any() or not right.any():
            return None
        return bool(abs(float(np.median(off[left])) - float(np.median(off[right]))) * float(self.rf) <= 0.5)

    def _union_eval(self, A: _Seg, B: _Seg, line: _Seg) -> dict:
        """Score the union hypothesis 'line's time model over A and B' against the split, frame by frame, with the
        framing of the split side extrapolated / interpolated and, with pixels, re-measured by ECC on the frame
        (both hypotheses get the same treatment). Rows only for frames whose RAW frame differs."""
        F = self.F
        rows, same = [], 0
        # nearest the cut first; the first frame the union explains clearly worse ends the test (a real cut is
        # rejected after a few ECC measurements, a spurious one is scored on every frame it changes)
        for k in sorted(range(A.a, B.b), key=lambda k_: (abs(k_ - B.a + 0.5), k_)):
            if rows and rows[-1].get("z_line") is not None and rows[-1]["z_line"] < rows[-1]["z_split"] - rows[-1]["d3"]:
                break
            if F.status[k] != Status.MATCH:
                continue
            S = A if k < B.a else B
            if bool(F.flip[k]) != S.flip:
                continue
            ju, js = int(self.pred(line, k)), int(self.pred(S, k))
            if ju == js:
                same += 1
                continue
            sm = self.sim_at(S, k)
            items = [(ju, sm, S.flip), (js, sm, S.flip)]
            ru = self._remeasure(k, ju, S.flip, [sm])
            rs = self._remeasure(k, js, S.flip, [sm])
            sc = self.P.zncc_set(k, items) if self.P.ok else None
            if sc is None or not np.all(np.isfinite(sc)):
                du, ds = F.deficit(k, ju), F.deficit(k, js)     # pixel-free: refine's candidate vector
                if not (math.isfinite(du) and math.isfinite(ds)):
                    rows.append({"k": k, "line": ju, "split": js, "z_line": None, "z_split": None})
                    continue
                zu, zs = -du, -ds
            else:
                zu = max(float(sc[0]), ru[1] if ru is not None else -math.inf)
                zs = max(float(sc[1]), rs[1] if rs is not None else -math.inf)
            rows.append({"k": k, "line": ju, "split": js, "z_line": round(zu, 6), "z_split": round(zs, 6),
                         "d3": round(3.0 * float(F.delta[k]), 6)})
        scored = [r for r in rows if r["z_line"] is not None]
        worse = [r["k"] for r in scored if r["z_line"] < r["z_split"] - r["d3"]]
        better = [r["k"] for r in scored if r["z_line"] > r["z_split"] + r["d3"]]
        gain = float(sum(r["z_line"] - r["z_split"] for r in scored))
        return {"rows": rows, "same": same, "worse": worse, "better": better, "gain": round(gain, 6),
                "unscored": len(rows) - len(scored)}

    def union_test(self, segs: list[_Seg]) -> list[_Seg]:
        """FX-04 3/6, FX-07 (a): every triggered hard cut A|B faces the continuous hypothesis -- A's time line
        extended over B, or B's over A, each frame scored against the split with re-measured framing
        (``_union_eval``). No union where the framing steps at the cut (a reframe on one line is a cut; the
        segments share their phase, ``time_ties``). A union no frame of which is worse than the split by more than
        3 delta_k is merged when it is better somewhere by more than 3 delta_k or changes no frame; inside the noise
        the independent evidence decides -- a competitor REPEAT pair at the cut, or audio running on without a lag
        step with no scene change detected -- and otherwise the cut is kept and reported uncertain (never silently
        merged or kept). The merged segment's soft ranges admit the union's frames on the changed frames only."""
        i = 0
        while i + 1 < len(segs):
            A, B = segs[i], segs[i + 1]
            if not self._hard_pair(A, B) or A.flip != B.flip:
                i += 1
                continue
            trig = self._union_triggers(A, B)
            if not trig:
                i += 1
                continue
            c = B.a
            ev: dict[str, Any] = {"cut": c, "triggers": trig, "segments": [[A.a, A.b], [B.a, B.b]]}
            jump = self._framing_jump(A, B)
            if not self._compatible(A, B):
                ev.update(result="framing_step", framing_jump=None if jump is None else [round(jump[0], 5),
                                                                                       round(jump[1], 3)])
                self.log("union_test", comp_frame=c, evidence=ev)
                i += 1
                continue
            best = None
            for name, line in (("A_line", A), ("B_line", B)):
                r = self._union_eval(A, B, line)
                ev[name] = {k: v for k, v in r.items() if k != "rows"}
                ev[name]["rows"] = r["rows"][:40]
                if r["worse"] or r["unscored"]:
                    continue
                key = (bool(r["better"]) or not r["rows"], r["gain"], name == "A_line")
                if best is None or key > best[0]:
                    best = (key, name, line, r)
            if best is None:
                ev["result"] = "cut_verified"
                self.log("union_test", comp_frame=c, evidence=ev)
                i += 1
                continue
            _key, name, line, r = best
            decided = bool(r["better"]) or not r["rows"]
            why = "the union explains frames better" if r["better"] else "no frame changes"
            if not decided:
                aud = self._audio_continuous(A, B)
                scene = any(abs(f - c) <= 1 for f in self.scene_changes)
                ev.update(audio_continuous=aud, scene_change=scene)
                if "repeat_pair" in trig:
                    decided, why = True, "competitor repeat pair at the cut (the same image on both frames)"
                elif aud and not scene:
                    decided, why = True, "audio runs on without a lag step and no scene change is detected"
            if not decided:
                ev["result"] = "undecided"
                self.log("union_test", comp_frame=c, evidence=ev)
                B.uncertain = True
                B.notes.append(f"cut at {c} not decidable: {name.replace('_', ' ')} explains both sides within the "
                               f"score noise and no independent evidence decides ({', '.join(trig)})")
                i += 1
                continue
            # B's notes about the removed cut (criterion 2's verdict on it) go with it
            notes = A.notes + [nt for nt in B.notes if not nt.startswith("criterion 2 not satisfied")]
            U = _Seg("raw", A.a, B.b, model=line.model, flip=A.flip, track=line.track, extra={**A.extra, **B.extra},
                     trans_in=A.trans_in, trans_out=B.trans_out, notes=notes,
                     uncertain=A.uncertain or B.uncertain, blend_frames=sorted(set(A.blend_frames) | set(B.blend_frames)))
            saved = {k: (int(self.F.lo[k]), int(self.F.hi[k])) for k in range(A.a, B.b)}
            for row in r["rows"]:
                self._widen(int(row["k"]), int(row["line"]), "union_merged")
            if not self.refit(U, keep_v=True):
                for k, (lo, hi) in saved.items():
                    self.F.lo[k], self.F.hi[k] = lo, hi
                    self.F.touched.pop(k, None)
                ev["result"] = "union_infeasible"
                self.log("union_test", comp_frame=c, evidence=ev)
                B.notes.append(f"cut at {c}: the continuous hypothesis explains both sides but one time line cannot "
                               "hold all frames")
                i += 1
                continue
            ev.update(result="merged", line=name, why=why)
            self.log("union_test", comp_frame=c, evidence=ev)
            U.notes.append(f"cut at {c} removed: {why} ({', '.join(trig)})")
            segs[i:i + 2] = [U]
            i = max(0, i - 1)
        return segs

    # ---------------------------------------------------------------------------------------------
    # competitor repeat pairs (FX-07): no time cut inside, same RAW content on both frames
    # ---------------------------------------------------------------------------------------------
    def repeat_pairs(self, segs: list[_Seg]) -> list[_Seg]:
        """FX-07 (c) comp-duplicate invariant, NONE side: a competitor REPEAT pair (k, k+1) whose one frame is matched
        by an adjacent raw segment and the other is NONE. The NONE frame takes its partner's RAW frame when it scores
        under the partner's (RAW frame, framing) within 3 delta_k of the partner and above match_thresh -
        anchor_zncc_slack (identical frames score alike: a measurement, not a guess) and the segment's time line
        can hold it; otherwise the pair stays split and the invariant check reports it."""
        if not self.P.ok:
            return segs
        F, cfg = self.F, self.cfg
        thr = float(_cfg(cfg, "match_thresh", 0.9)) - float(_cfg(cfg, "anchor_zncc_slack", 0.05))
        changed = True
        while changed:
            changed = False
            for i in range(len(segs) - 1):
                X, Y = segs[i], segs[i + 1]
                if X.b != Y.a or not F.repeat_pair(Y.a):
                    continue
                if X.kind == "none" and Y.kind == "raw":
                    N, R, k, kp = X, Y, Y.a - 1, Y.a
                elif X.kind == "raw" and Y.kind == "none":
                    N, R, k, kp = Y, X, X.b, X.b - 1
                else:
                    continue
                if F.status[kp] != Status.MATCH or bool(F.flip[kp]) != R.flip or R.model is None:
                    continue
                jp, sp = int(self.pred(R, kp)), self.sim_at(R, kp)
                zk = self.P.zncc_set(k, [(jp, sp, R.flip)])
                zp = self.P.zncc_set(kp, [(jp, sp, R.flip)])
                ev = {"pair": [min(k, kp), max(k, kp)], "raw": jp}
                if zk is None or zp is None or not (math.isfinite(float(zk[0])) and math.isfinite(float(zp[0]))):
                    continue
                ev.update(score=round(float(zk[0]), 6), partner_score=round(float(zp[0]), 6))
                if not (float(zk[0]) >= thr and float(zk[0]) >= float(zp[0]) - 3.0 * F.delta[kp]):
                    self.log("repeat_pair_not_absorbed", comp_frame=int(k), evidence=ev)
                    continue
                old = {a: getattr(F, a)[k] for a in ("status", "raw", "lo", "hi", "raw_lo", "raw_hi", "flip", "track",
                                                     "low_margin", "s", "theta", "tx", "ty")}
                F.status[k], F.raw[k] = Status.MATCH, jp
                F.lo[k] = F.hi[k] = F.raw_lo[k] = F.raw_hi[k] = jp
                F.flip[k], F.track[k], F.low_margin[k] = R.flip, R.track, True
                F.s[k], F.theta[k], F.tx[k], F.ty[k] = sp.s, sp.theta_deg, sp.tx, sp.ty
                ra, rb = R.a, R.b
                if k < R.a:
                    R.a = k
                else:
                    R.b = k + 1
                if not self.refit(R, keep_v=True):
                    R.a, R.b = ra, rb
                    for a, v in old.items():
                        getattr(F, a)[k] = v
                    self.refit(R, keep_v=True)
                    self.log("repeat_pair_not_absorbed", comp_frame=int(k), evidence={**ev, "reason": "time line"})
                    continue
                F.touched[k] = "repeat_pair_absorbed"
                if k < kp:
                    N.b = k
                else:
                    N.a = k + 1
                if N.length <= 0:
                    segs.remove(N)
                R.notes.append(f"frame {k} (NONE) repeats frame {kp}: shows the same RAW frame {jp}")
                self.log("repeat_pair_absorbed", comp_frame=int(k), evidence=ev)
                changed = True
                break
        return segs

    def repeat_invariant(self, segs: list[_Seg]) -> None:
        """FX-07 (c) check on the final segments: both frames of every competitor REPEAT pair must get the same
        status and the same RAW content (the same frame, or visually identical ones: RAW's own duplicates are
        fine). A time cut inside a pair is allowed only where the framing steps on one time line. Conflicts are
        logged (comp_duplicate_conflict) and noted on the segment."""
        F = self.F
        pos = {}
        for S in segs:
            for k in range(S.a, S.b):
                pos.setdefault(k, S)
        bad = []
        for k in np.flatnonzero(F.pair_label == 1):
            k = int(k)
            if k + 1 >= self.n:
                continue
            X, Y = pos.get(k), pos.get(k + 1)
            if X is None or Y is None:
                continue
            why = None
            if X.kind != Y.kind:
                why = f"{X.kind} vs {Y.kind}"
            elif X.kind == "raw":
                jx, jy = int(self.pred(X, k)), int(self.pred(Y, k + 1))
                same = jx == jy or (F.raw_lo[k] <= jy <= F.raw_hi[k] and F.raw_lo[k + 1] <= jx <= F.raw_hi[k + 1])
                if X is not Y and not (self._lines_meet(X, Y) and not self._compatible(X, Y)):
                    why = f"time cut inside the pair (RAW {jx} | {jy})"
                elif not same and X is Y:
                    why = f"RAW {jx} vs {jy} on one time line (phase contradicts the repeat cadence)"
            if why:
                bad.append({"pair": [k, k + 1], "why": why})
                Y.notes.append(f"competitor frames {k}/{k + 1} are identical but {why}")
        if bad:
            self.log("comp_duplicate_conflict", evidence={"count": len(bad), "pairs": bad[:200]})

    # ---------------------------------------------------------------------------------------------
    # time ties (FX-04 2): segments on one time line share one phase solve
    # ---------------------------------------------------------------------------------------------
    def time_ties(self, segs: list[_Seg]) -> None:
        """Adjacent stretch segments at the same speed with a hard cut between them whose MEASURED frames (refine's
        argmax ranges, tolerated drops left out) fit ONE line -- a reframe at a RAW-native shot change, a framing
        step on a continuous clip -- are phase-solved together (phase_solve.solve_shared_raw_in): every member gets
        the shared line's raw_in at its comp_in and the shared interval, so the layers keep one time line."""
        groups: list[list[_Seg]] = []
        cur: list[_Seg] = []
        for S in segs:
            if not (S.kind == "raw" and S.model is not None and not S.ramp and S.model.v > 0):
                if len(cur) > 1:
                    groups.append(cur)
                cur = []
                continue
            if cur:
                A = cur[-1]
                if A.b == S.a and abs(A.model.v - S.model.v) <= 1e-12 and S.trans_in is None and A.trans_out is None \
                        and S.cut_ambiguity is None and self._tied(cur + [S]):
                    cur.append(S)
                    continue
                if len(cur) > 1:
                    groups.append(cur)
            cur = [S]
        if len(cur) > 1:
            groups.append(cur)
        for g in groups:
            if len(g) < 2:
                continue
            parts, pens = [], []
            for S in g:
                ks, lo, hi = self.constraints(S)
                keep = ~np.isin(ks, np.asarray(S.model.drops, dtype=np.int64)) if S.model.drops else np.ones(ks.size, bool)
                parts.append((ks[keep], lo[keep], hi[keep], S.a))
            kk = np.concatenate([p[0] for p in parts])
            ll = np.concatenate([p[1] for p in parts])
            hh = np.concatenate([p[2] for p in parts])
            pen = self.S.penalties(kk, ll, hh)
            sols = ps.solve_shared_raw_in(parts, g[0].model.v, self.cf, self.rf, penalties=pen)
            if not sols or not all(s.get("ok") for s in sols):
                continue
            for S, sol in zip(g, sols):
                S.model.sol = sol
                S.notes.append(f"time line shared with segment(s) {', '.join(f'[{o.a},{o.b})' for o in g if o is not S)}"
                               " (one phase solve)")
            self.log("time_tie", comp_range=[g[0].a, g[-1].b], evidence={
                "segments": [[S.a, S.b] for S in g], "speed": g[0].model.v,
                "raw_in": [round(float(s["raw_in"]), 9) for s in sols], "margin_ms": round(float(sols[0]["margin_ms"]), 4)})

    def _tied(self, group: list[_Seg]) -> bool:
        """What the segments of ``group`` (same speed) CLAIM fits one line: refine's measured range where a segment's
        model shows it, the model's frame where the segment re-assigns a frame (tolerated drops left out) -- the
        Chebyshev LP over all of them is feasible. A 1-frame skip between two exactly measured sides never is."""
        v = group[0].model.v
        kk, plo, phi = [], [], []
        for S in group:
            ks, lo, hi = self.constraints(S)
            if S.model.drops:
                keep = ~np.isin(ks, np.asarray(S.model.drops, dtype=np.int64))
                ks, lo, hi = ks[keep], lo[keep], hi[keep]
            a, b = self.F.pristine(ks, lo, hi)
            j = np.asarray(self.pred(S, ks), dtype=np.int64)
            inside = (a <= j) & (j <= b)
            kk.append(ks)
            plo.append(np.where(inside, a, j))
            phi.append(np.where(inside, b, j))
        ks = np.concatenate(kk)
        if ks.size == 0:
            return False
        return bool(ps.is_feasible(ks, np.concatenate(plo), np.concatenate(phi), int(group[0].a), self.cf, self.rf, v=v))

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
        if ks.size and m.sol.get("shared"):
            sol = m.sol             # one phase solve with the segments on its time line (time_ties)
        elif ks.size:
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
        sims = np.stack([np.asarray(fm.s), np.asarray(fm.theta), np.asarray(fm.tx), np.asarray(fm.ty)], axis=1).copy()
        meas = np.asarray(fm.sim_meas).copy()
        changed = []
        for S, seg in zip(work, segs):
            if seg.type != "raw":
                continue
            pieces = S.ramp if S.ramp else [S]
            remeasured = (S.framing or {}).get("_samples") or {}
            for p in pieces:
                if not p.model.sol or "raw_in" not in p.model.sol or not math.isfinite(p.model.raw_in):
                    continue
                for k in range(p.a, p.b):
                    if F.status[k] != Status.MATCH or bool(F.flip[k]) != p.flip:
                        continue
                    j = int(self.pred(p, k))
                    if status[k] != Status.MATCH:   # absorbed NONE frames (with the framing they were scored under)
                        status[k] = Status.MATCH
                        flip[k], track[k] = p.flip, p.track
                        sims[k] = (F.s[k], F.theta[k], F.tx[k], F.ty[k])
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
                        rm = remeasured.get(k)
                        if rm is not None and int(rm[0]) == j:
                            # FX-06 1: the framing measured on the frame now shown (a consistent (RAW frame, Sim) pair)
                            sims[k] = meas[k] = (rm[1].s, rm[1].theta_deg, rm[1].tx, rm[1].ty)
                            changed[-1]["sim"] = "re-measured"
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
        fm.s, fm.theta, fm.tx, fm.ty = sims[:, 0], sims[:, 1], sims[:, 2], sims[:, 3]
        fm.sim_meas = meas
        if changed:
            self.log("frame_map_corrected", evidence={"frames": changed[:500], "count": len(changed)})

    # ---------------------------------------------------------------------------------------------
    def confounded_notes(self, segs: list[_Seg]) -> None:
        """DESIGN refine step 7: frames refine found time / translation confounded (m+-1 with its own framing path
        scores within noise of m) are named in their segment's notes."""
        F = self.F
        for S in segs:
            if S.kind != "raw":
                continue
            ks = [k for k in range(S.a, S.b) if F.status[k] == Status.MATCH and bool(F.confounded[k])]
            if ks:
                S.notes.append(f"time/translation confounded frames {_rng(ks)} (RAW m+-1 with its own framing scores "
                               "within noise: soft range m+-1)")

    def _scene_changes(self) -> list[int]:
        """PySceneDetect changes of the competitor (cached), the union test's independent evidence; [] without."""
        path = getattr(self.comp, "path", "")
        if not bool(_cfg(self.cfg, "scenedetect", True)) or not path or not Path(path).exists():
            return []
        try:
            return [int(f) for f in scenedetect_changes(path, self.cfg)]
        except Exception as e:  # scenedetect missing / decode failure: evidence only, never fatal
            log.debug("segment: scene changes unavailable for the union test: %s", e)
            return []

    def run(self) -> list[Segment]:
        seed_everything(int(_cfg(self.cfg, "seed", 12345)))
        if self.n == 0:
            return []
        self.scene_changes = self._scene_changes()
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
        work = self.repeat_pairs(work)
        self.final_speeds(work)
        self.speed_only_cuts(work)
        work = self.transitions(work)
        work = self.uniform_runs(work)
        self.check_cuts(work)
        work = self.merge_phantom_cuts(work)
        work = self.union_test(work)
        self.retime(work)
        work = self.ramps(work)
        for S in work:
            if S.kind == "raw":
                self.framing(S, force=True)
                self.full_affine_check(S)
        work.sort(key=lambda s: (s.a, s.b))
        self.time_ties(work)
        self.repeat_invariant(work)
        self.confounded_notes(work)
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


def _model_sim(b: _Builder, s: Segment, k: int) -> Sim | None:
    """A final segment's framing at comp frame k (AE rule: keys interpolated, held outside)."""
    if s.transform_keys:
        return interpolate_keys(s.transform_keys, k, float(b.raw.full_size[0]), float(b.raw.full_size[1]))
    return Sim.from_dict(s.transform) if s.transform else None


def _change_framing(b: _Builder, seg: Segment, f: int) -> dict | None:
    """Framing evidence at a scene change f inside a raw segment (FX-06 6): the measured framing jump (refine's
    per-frame measurement of f-2, f-1 extrapolated to f against f's) and the segment model's deviation from the
    measurement on f-1 / f (box-centre px, relative scale)."""
    F = b.F

    def meas(k: int) -> Sim | None:
        if not (0 <= k < b.n) or F.status[k] != Status.MATCH:
            return None
        return F.measured(k) or F.sim(k)
    m2, m1, m0 = meas(f - 2), meas(f - 1), meas(f)
    if m1 is None or m0 is None:
        return None
    pred = m1 if m2 is None else Sim(2 * m1.s - m2.s, 2 * m1.theta_deg - m2.theta_deg, 2 * m1.tx - m2.tx,
                                     2 * m1.ty - m2.ty)
    js, jp = b._sim_change(pred, m0)
    dev_s = dev_p = 0.0
    for k, mk in ((f - 1, m1), (f, m0)):
        md = _model_sim(b, seg, k) if seg.comp_in <= k < seg.comp_out else None
        if md is not None:
            ds_, dp_ = b._sim_change(md, mk)
            dev_s, dev_p = max(dev_s, ds_), max(dev_p, dp_)
    return {"jump_px": round(jp, 3), "jump_scale": round(js, 5), "model_dev_px": round(dev_p, 3),
            "model_dev_scale": round(dev_s, 5)}


def _crosscheck(b: _Builder, segs: list[Segment]) -> dict:
    """PySceneDetect cross-check (DESIGN §5 segment.py): every detected scene change must coincide (+-1) with a
    cut / transition; an unexplained change inside a raw segment is checked against the framing (FX-06 6: a measured
    framing jump or a model off the measurement above punch_pos_step / punch_scale_step is reported as 'framing step
    not represented', never asserted away), a caption event of the layout or a score dip; a cut PySceneDetect did
    not see is described by its RAW jump and its framing change (position, scale and rotation)."""
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
    thr_s = float(_cfg(b.cfg, "punch_scale_step", 0.01))
    thr_p = float(_cfg(b.cfg, "punch_pos_step", 4.0))
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
    caps = [c for c in (getattr(b.layout, "captions", None) or []) if isinstance(c, dict)]
    agree, unexplained, missed, steps = [], [], [], []
    for f in changes:
        near_cut = [c for c in cuts if abs(c - f) <= 1]
        near_win = [w for w in windows if w[0] - 1 <= f <= w[1] + 1]
        if near_cut or near_win:
            agree.append(f)
            continue
        seg = next((s for s in segs if s.comp_in <= f < s.comp_out), None)
        why = "inside a continuous mapping (same RAW line; measured framing continuous, model on the measurement): " \
              "motion, lighting or overlay change -- not a cut"
        ev: dict[str, Any] = {"frame": f, "segment": seg.id if seg else None}
        if seg is not None and seg.type == "raw":
            fr = _change_framing(b, seg, f)
            ev["framing"] = fr
            sc = b.F.score[max(0, f - 1):f + 1]
            ev["scores"] = [float(x) for x in sc]
            cap = [c for c in caps if min(abs(int(c.get("comp_in", -9)) - f), abs(int(c.get("comp_out", -9)) - f)) <= 1]
            if fr is not None and (fr["jump_px"] > thr_p or fr["jump_scale"] > thr_s or fr["model_dev_px"] > thr_p
                                   or fr["model_dev_scale"] > thr_s):
                why = (f"framing step not represented: measured framing jump {fr['jump_px']:.1f} px / scale "
                       f"{100 * fr['jump_scale']:.2f} %, segment model off the measurement by {fr['model_dev_px']:.1f} px")
                steps.append(f)
            elif fr is None:
                why = "inside a segment where the framing is not measured on both sides (not checked)"
            elif np.isfinite(sc).all() and float(np.min(sc)) < float(_cfg(b.cfg, "match_thresh", 0.9)):
                why = "inside a segment but the match score dips there (check overlays / a missed flash cut)"
            elif cap:
                why = f"caption change (layout caption event {int(cap[0].get('comp_in'))}-{int(cap[0].get('comp_out'))})"
            seg.notes = (seg.notes + "; " if seg.notes else "") + f"PySceneDetect change at {f}: {why}"
        unexplained.append({**ev, "explanation": why})
    changes_set = set(changes)
    for c, s in sorted(cuts.items()):
        if any(abs(c - f) <= 1 for f in changes_set):
            continue
        prev = next((p for p in segs if p.comp_out == c and p.type == "raw"), None)
        if s.type == "raw" and prev is not None and s.raw_in_frame is not None and prev.raw_out_frame is not None:
            jump = s.raw_in_frame - prev.raw_out_frame
            a_, b_ = _model_sim(b, prev, c - 1), _model_sim(b, s, c)
            same = bool(s.flip_h == prev.flip_h and a_ is not None and b_ is not None and
                        (lambda d: d[0] <= thr_s and d[1] <= thr_p)(b._sim_change(a_, b_)))
            if jump in (0, 1):
                why = (f"no RAW skip (RAW jump {jump}: one time line) and " +
                       ("the same framing" if same else "a framing change (reframe on the time line)"))
            elif jump == -1:
                why = "1-frame RAW repeat back (RAW jump -1)" + ("" if same else " with a framing change")
            else:
                why = (f"same-shot jump cut (RAW jump {jump} frames, same framing)" if same
                       else f"low-contrast cut (RAW jump {jump} frames)")
        else:
            why = "cut next to a transition / placeholder (content change is gradual or uniform)"
        missed.append({"cut": c, "explanation": why})
    b.log("scenedetect_crosscheck", evidence={"detected": changes, "agree": agree, "unexplained": unexplained,
                                              "cuts_not_detected": missed, "framing_steps_not_represented": steps})
    return {"status": "ok", "detected": changes, "agree": agree, "unexplained": unexplained,
            "cuts_not_detected": missed, "framing_steps_not_represented": steps}


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

_WRITTEN = ("status", "raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi", "low_margin", "flip", "track", "tie", "s",
            "theta", "tx", "ty", "sim_meas")
_PRE = "pre_segment_"


def _pristine(fm: FrameMap) -> FrameMap:
    """The FrameMap as refine produced it. build_segments writes its corrections into fm but keeps the
    original columns in the column store (keys 'pre_segment_<name>', saved/loaded with the FrameMap), so
    re-running it on its own output reads exactly the same input (determinism, Stage 9.7)."""
    d = fm.__dict__["d"]
    for k in _WRITTEN:          # per column: a FrameMap written before a column joined _WRITTEN keeps the others
        if _PRE + k not in d:
            d[_PRE + k] = np.asarray(d[k]).copy()
    src = fm.copy()
    for k in _WRITTEN:
        setattr(src, k, d[_PRE + k])
    return src


def build_segments(fm: FrameMap, comp: Any, raw: Any, layout: Any, overlays: Any, cfg: Any,
                   dlog: DecisionLog | None, debug_dir: Any, hints: Any = None,
                   union_cuts: Iterable[int] = ()) -> list[Segment]:
    """Stage 5.4-5.5: cut the competitor timeline into segments (DESIGN §5 segment.py).

    fm        FrameMap from refine (mutated in place: m(k) corrected to the segment model where the soft
              range allows it (low_margin flagged) -- with the framing measured on the frame now shown --,
              crossfade overlap frames -> Status.BLEND, tie flags, soft ranges widened for tolerated isolated
              frames; refine's columns are kept as 'pre_segment_*' entries of the column store, so re-running
              on the output is exact).
    comp, raw Proxy objects (fps, sizes, ratios; pixels used when ``frames`` is not None).
    layout    Layout (box / static mask) or None; overlays: layout.OverlayMasks or None.
    hints     AudioHints (speed evidence for the DP, lag steps as cut candidates) or None.
    union_cuts comp frames whose cuts must face the continuous hypothesis in any case (union test; e.g. the cuts
              of a previous pass where audio_align logged 'jl_not_exported': a large J/L where B's time line meets
              A's, FX-09) -- the union test also finds such cuts itself (time lines that meet).
    Returns segments with ids 1..N in competitor order; NOT-IN-RAW placeholders, flashes and dips are
    segments too. Writes debug/mapping.png and debug/scores.png when debug_dir is given.
    """
    b = _Builder(fm, comp, raw, layout, overlays, cfg, dlog, debug_dir, hints, src=_pristine(fm))
    b.union_cuts = {int(c) for c in union_cuts}
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
