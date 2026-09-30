"""Stage 5.3 -- frame-exact refinement: m(k) for every competitor frame (DESIGN.md §5 refine.py).

Algorithm (all scores are ``scoring`` masked ZNCC in competitor space):

1. Anchors (visual_match) are linked into TRACKS: same flip, |Δs|/s <= link_scale_tol and
   |Δpos| <= link_pos_tol after the track's linear trend, raw-vs-k slope consistent (0 and negative
   slopes allowed for the first link).
2. Every track has ONE transform model (constant, or linear keys simplified with RDP when animated),
   never a free per-frame ECC: ECC runs on sampled frames against the MODEL frame m(k), an update is
   kept only if it raises masked ZNCC over its init, and the model is refitted robustly over the track.
   Transform fit and frame assignment alternate until m(k) stops changing (<= 3 iterations).
   Consecutive tracks whose ECC samples lie on ONE linear trend with continuous time (an animated
   zoom/pan faster than the link tolerance, split at the first link) are chained into one animated
   track; a punch-in (a framing STEP) is never chained. Constant tracks where RAW frame m±1 with an
   ECC-refitted transform scores within noise are flagged ``time_translation_confounded`` (dlog + the
   extra FrameMap column ``confounded``).
3. Every frame k is scored under every track active near k: RAW frames ĵ-R..ĵ+R around the track's
   prediction ĵ; if the argmax is on the window edge the window is extended in that direction up to
   cfg.track_search_radius (then the frame is re-searched with visual_match.search_frame). Tracks grow
   frame-wise beyond their anchors while they keep winning; duplicate tracks (same argmax, same
   scores) are merged.
4. Overlay pass 2: residual masks (layout.masks_from_residuals, or the local equivalent), re-score.
5. Rescue: frames scoring < match_thresh, or < rolling track median(±5) - max(rel_drop_min, 4·MAD), or
   whose argmax stayed on the window edge -> visual_match.search_frame -> new tracks -> re-score (catches
   1-2 frame flash cuts and jump cuts inside a track). Remaining frames: UNIFORM (region std <
   uniform_std) or NONE.
6. raw_lo/raw_hi = RAW frames visually identical to m(k) (RAW-vs-RAW, warped & masked); low_margin;
   soft ranges; candidate score vectors (FrameMap.cand, CAND_W wide, centred on m); confidence.
   NONE/UNIFORM frames have raw = raw_lo = raw_hi = soft_* = -1 but keep the best hypothesis' score,
   track, flip, Sim and candidate vector (diagnostics; consumers must check ``status``).
7. debug/low_confidence/k#####.png for conf < cfg.low_conf_thresh (competitor | best | 2nd best), max 200.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from . import scoring
from .common import Cache, DecisionLog, log, null_dlog, params_hash, stage_key
from .geometry import Sim, from_cv_matrix, h3, interpolate_keys, rdp, to_cv_matrix, translate3
from .model import CAND_W, AudioHints, FrameMap, Layout, Proxy, Status
from .visual_match import (AllowedMasks, Anchor, RawIndex, _nanargmax, _Scorer, box_roi, hints_id, layout_id,
                           mask_bbox, overlays_id, parallel_map, proxy_id, run_searches)

__all__ = ["build_frame_map", "refine_transform"]

_MAX_ITER = 3               # transform fit <-> frame assignment alternations
_CONST_SAMPLES = 24         # ECC samples for a track whose model is constant
_MAX_DEBUG_PNG = 200
_IDENTICAL_MAX = 120        # max RAW frames scanned each way for visually identical neighbours
_SOFT_CAP_MAX = 0.01        # cap on the candidate-window extension margin (2 x provisional soft delta)


# ---------------------------------------------------------------------------------------------
# ECC transform refinement
# ---------------------------------------------------------------------------------------------

def refine_transform(comp_img: np.ndarray, raw_img: np.ndarray, sim0: Sim, flip: bool, raw_w: float,
                     raw_ratio: tuple[float, float], comp_ratio: tuple[float, float], allowed: np.ndarray | None,
                     cfg, roi: tuple[int, int, int, int] | None = None) -> tuple[Sim, float]:
    """ECC-refine the canonical Sim of ``raw_img`` (unflipped RAW proxy frame) -> ``comp_img`` (comp
    proxy frame). Returns (sim, masked ZNCC); the update is kept ONLY if it raises masked ZNCC over
    ``sim0`` (else (sim0, zncc(sim0))).

    DESIGN §2.2 recipe: ``M = translate3(-x0,-y0) @ h3(to_cv_matrix(sim0, flip, ...))``, init = inv(M),
    ECC(template = blurred comp ROI, input = blurred RAW proxy, MOTION_AFFINE), result
    ``from_cv_matrix((translate3(x0,y0) @ inv(h3(W)))[:2], flip, ...)``. The comp mask (box & ~static &
    ~overlay) is passed as the TEMPLATE mask via ``cv2.findTransformECCWithMask`` (OpenCV >= 4.12/5;
    ``findTransformECC``'s ``inputMask`` applies to the RAW image, so the plain call is used without a
    mask on older builds). ``cv2.error`` ("Iterations do not converge") falls back to sim0.
    """
    import cv2

    if roi is None:
        roi = mask_bbox(allowed, comp_img.shape)
    region = scoring.prepare_comp(comp_img, roi, allowed, cfg.score_blur, with_grad=cfg.grad_weight > 0)

    def score(s: Sim) -> float:
        return float(scoring.score_candidates(region, [raw_img], s, flip, raw_w, raw_ratio, comp_ratio,
                                              blur=cfg.score_blur, grad_weight=cfg.grad_weight)[0])

    z0 = score(sim0)
    x0, y0, w, h = roi
    try:
        M = translate3(-x0, -y0) @ h3(to_cv_matrix(sim0, flip, raw_w, raw_ratio, comp_ratio))
        init = np.linalg.inv(M)[:2].astype(np.float32)
        tmpl = region.img.astype(np.float32)
        sig = float(cfg.score_blur)
        inp = raw_img.astype(np.float32)
        if sig > 0:
            inp = cv2.GaussianBlur(inp, (0, 0), sig)
        crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, int(cfg.ecc_iterations), float(cfg.ecc_eps))
        if hasattr(cv2, "findTransformECCWithMask"):
            tmask = region.mask.astype(np.uint8) * 255
            imask = np.full(inp.shape[:2], 255, np.uint8)
            _, Wm = cv2.findTransformECCWithMask(tmpl, inp, tmask, imask, init, cv2.MOTION_AFFINE, crit, 5)
        else:  # pragma: no cover - older OpenCV
            _, Wm = cv2.findTransformECC(tmpl, inp, init, cv2.MOTION_AFFINE, crit, None, 5)
        full = translate3(x0, y0) @ np.linalg.inv(h3(Wm))
        sim1 = from_cv_matrix(full[:2], flip, raw_w, raw_ratio, comp_ratio)
    except (cv2.error, ValueError, np.linalg.LinAlgError):
        return sim0, z0
    if not (0.5 * sim0.s < sim1.s < 2.0 * sim0.s) or not all(map(math.isfinite, (sim1.tx, sim1.ty, sim1.theta_deg))):
        return sim0, z0
    z1 = score(sim1)
    if math.isfinite(z1) and (not math.isfinite(z0) or z1 > z0):
        return sim1, z1
    return sim0, z0


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def _key(k: int, sim: Sim) -> dict:
    return {"comp_frame": int(k), "scale": float(sim.s), "rotation_deg": float(sim.theta_deg),
            "tx": float(sim.tx), "ty": float(sim.ty)}


def _sim_at(keys: list[dict], k: float, raw_wh: tuple[float, float]) -> Sim:
    return interpolate_keys(keys, k, raw_wh[0], raw_wh[1])


def _sim_delta(a: Sim, b: Sim, center: np.ndarray) -> tuple[float, float]:
    """(|Δs|/s, |Δpos| in comp full-res px of the RAW point that ``b`` maps to ``center``)."""
    p = b.inverse().apply(center)[0]
    d = a.apply(p)[0] - center
    return abs(a.s / b.s - 1.0), float(math.hypot(d[0], d[1]))


def _zero_rotation(sim: Sim, center: np.ndarray) -> Sim:
    """Same scale, theta = 0, keeping the RAW point at ``center`` in place (rotation rule §2.2)."""
    p = sim.inverse().apply(center)[0]
    return Sim(sim.s, 0.0, float(center[0] - sim.s * p[0]), float(center[1] - sim.s * p[1]))


def _robust_line_inliers(x: np.ndarray, y: np.ndarray, floor: float) -> np.ndarray:
    """Inlier mask of y ~ a + b x (least squares, 2 trimming rounds, threshold max(3.5 σ_MAD, floor))."""
    keep = np.isfinite(y)
    for _ in range(2):
        if keep.sum() < 3:
            return keep
        A = np.c_[np.ones(keep.sum()), x[keep]]
        coef, *_ = np.linalg.lstsq(A, y[keep], rcond=None)
        r = np.abs(y - (coef[0] + coef[1] * x))
        mad = float(np.median(r[keep])) * 1.4826
        new = np.isfinite(y) & (r <= max(3.5 * mad, floor))
        if np.array_equal(new, keep):
            break
        keep = new
    return keep


def fit_track_model(samples: Sequence[tuple[int, Sim]], center: np.ndarray, cfg) -> list[dict]:
    """Robust transform model of one track from (k, Sim) samples -> AE-linear keys.

    Constant (scale spread < framing_scale_spread, position spread < framing_pos_spread, rotation
    spread < rotation_min_deg) -> one key (medians); else running-median-3 smoothing + RDP
    (rdp_pos_tol px, rdp_scale_tol relative, 0.05°). Positions are those of the RAW point that the
    median Sim maps to ``center`` (box centre), which decorrelates scale and translation. If every key
    has |θ| <= rotation_min_deg, θ is set to 0 keeping that point fixed.
    """
    ss = sorted(((int(k), s) for k, s in samples), key=lambda t: t[0])
    if not ss:
        raise ValueError("fit_track_model: no samples")
    ks = np.array([k for k, _ in ss], np.float64)
    s = np.array([x.s for _, x in ss])
    th = np.array([x.theta_deg for _, x in ss])
    tx = np.array([x.tx for _, x in ss])
    ty = np.array([x.ty for _, x in ss])
    med = Sim(float(np.median(s)), float(np.median(th)), float(np.median(tx)), float(np.median(ty)))
    p_ref = med.inverse().apply(center)[0]
    cxy = np.array([x.apply(p_ref)[0] for _, x in ss])
    cx, cy = cxy[:, 0], cxy[:, 1]

    def mk(k: float, s_: float, th_: float, cx_: float, cy_: float) -> dict:
        lin = Sim(s_, th_, 0.0, 0.0).apply(p_ref)[0]
        return _key(int(k), Sim(float(s_), float(th_), float(cx_ - lin[0]), float(cy_ - lin[1])))

    keep = np.ones(len(ks), bool)
    if len(ks) >= 4:
        keep &= _robust_line_inliers(ks, s / med.s, 0.002)
        keep &= _robust_line_inliers(ks, cx, 1.0)
        keep &= _robust_line_inliers(ks, cy, 1.0)
        if keep.sum() < 2:
            keep[:] = True
    ks, s, th, cx, cy = ks[keep], s[keep], th[keep], cx[keep], cy[keep]
    s_med = float(np.median(s))
    const = (len(ks) == 1 or
             ((s.max() - s.min()) / s_med < cfg.framing_scale_spread and
              max(np.ptp(cx), np.ptp(cy)) < cfg.framing_pos_spread and np.ptp(th) < cfg.rotation_min_deg))
    if const:
        keys = [mk(ks[0], s_med, float(np.median(th)), float(np.median(cx)), float(np.median(cy)))]
    else:
        def med3(v: np.ndarray) -> np.ndarray:
            if len(v) < 3:
                return v.copy()
            out = v.copy()
            out[1:-1] = np.median(np.stack([v[:-2], v[1:-1], v[2:]]), axis=0)
            return out
        s2, th2, cx2, cy2 = med3(s), med3(th), med3(cx), med3(cy)
        pts = np.c_[ks, s2, cx2, cy2, th2]
        idx = rdp(pts, np.array([cfg.rdp_scale_tol * s_med, cfg.rdp_pos_tol, cfg.rdp_pos_tol, 0.05]))
        keys = [mk(ks[i], s2[i], th2[i], cx2[i], cy2[i]) for i in idx]
    if all(abs(kk["rotation_deg"]) <= cfg.rotation_min_deg for kk in keys):
        keys = [_key(kk["comp_frame"], _zero_rotation(Sim.from_dict(kk), center)) for kk in keys]
    return keys


# ---------------------------------------------------------------------------------------------
# Tracks and hypotheses
# ---------------------------------------------------------------------------------------------

@dataclass
class _Hyp:
    """Scores of frame k under one track: scores[i] = S_k(lo + i) (NaN = not evaluated)."""
    lo: int
    scores: np.ndarray
    jb: int
    sb: float
    edge: bool
    widened: bool

    def get(self, j: int) -> float:
        i = j - self.lo
        return float(self.scores[i]) if 0 <= i < len(self.scores) else float("nan")


@dataclass
class _Track:
    id: int
    flip: bool
    anchors: list[Anchor]
    keys: list[dict] = field(default_factory=list)
    span: list[int] = field(default_factory=lambda: [0, 0])
    sup_k: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    sup_j: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float64))
    samples: list[tuple[int, Sim]] = field(default_factory=list)   # ECC samples of the last refit
    keep_support: bool = False     # keep sup_k/sup_j once (set by a chain merge)
    confounded: bool = False

    def is_constant(self) -> bool:
        return len(self.keys) <= 1

    def predict(self, k: int, u1: float) -> float:
        """Predicted RAW frame at comp frame k: piecewise-linear through the support points; ends are
        extrapolated with the median local slope (clamped to +-4x the 1.0-speed slope u1)."""
        ks, js = self.sup_k, self.sup_j
        if len(ks) == 0:
            a = self.anchors[0]
            return a.raw + u1 * (k - a.k)
        if len(ks) == 1:
            return float(js[0] + u1 * (k - ks[0]))
        if ks[0] <= k <= ks[-1]:
            return float(np.interp(k, ks, js))

        def slope(kk: np.ndarray, jj: np.ndarray) -> float:
            d = np.diff(jj) / np.maximum(np.diff(kk), 1)
            return float(np.clip(np.median(d), -4 * u1, 4 * u1)) if len(d) else u1
        if k > ks[-1]:
            return float(js[-1] + slope(ks[-6:], js[-6:]) * (k - ks[-1]))
        return float(js[0] + slope(ks[:6], js[:6]) * (k - ks[0]))


# ---------------------------------------------------------------------------------------------
# Worker functions (run in fork pools; state = dict of shared read-only objects)
# ---------------------------------------------------------------------------------------------

def _w_eval(state: dict, task: tuple) -> tuple:
    """Score frame k under one track model: window jhat±R, widened on the edge up to Rmax."""
    k, keys, flip, jhat, R, Rmax = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    n = raw.n
    sim = _sim_at(keys, k, state["raw_wh"])
    sc = _Scorer(np.asarray(comp.get(k)), state["roi"], state["allowed"](k), raw, comp.ratio, cfg)
    S: dict[int, float] = {}

    def ev(a: int, b: int) -> None:
        js = [j for j in range(max(0, a), min(n - 1, b) + 1) if j not in S]
        if js:
            for j, v in zip(js, sc.scores(js, sim, flip)):
                S[j] = float(v)

    jhat = int(min(max(jhat, 0), n - 1))
    lo, hi = max(0, jhat - R), min(n - 1, jhat + R)
    ev(lo, hi)
    widened = False

    def best() -> int:
        js = sorted(j for j in S if lo <= j <= hi)
        vals = np.array([S[j] for j in js])
        i = _nanargmax(vals)
        return js[i] if i >= 0 else -1

    jb = best()
    while jb >= 0:
        if jb == lo and lo > 0 and jhat - lo < Rmax:
            nlo = max(0, lo - R, jhat - Rmax)
            ev(nlo, lo - 1)
            lo = nlo
        elif jb == hi and hi < n - 1 and hi - jhat < Rmax:
            nhi = min(n - 1, hi + R, jhat + Rmax)
            ev(hi + 1, nhi)
            hi = nhi
        else:
            break
        widened = True
        jb = best()
    edge = jb >= 0 and ((jb == lo and lo > 0) or (jb == hi and hi < n - 1))
    if jb >= 0:
        ev(jb - R, jb + R)
    lo_all, hi_all = min(S), max(S)
    arr = np.full(hi_all - lo_all + 1, np.nan, np.float32)
    for j, v in S.items():
        arr[j - lo_all] = v
    sb = S.get(jb, float("nan")) if jb >= 0 else float("nan")
    return int(lo_all), arr, int(jb), float(sb), bool(edge), bool(widened)


def _w_ecc(state: dict, task: tuple) -> tuple[dict, float]:
    k, j, flip, sim_d = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    sim, z = refine_transform(np.asarray(comp.get(k)), np.asarray(raw.get(j)), Sim.from_dict(sim_d), flip,
                              float(raw.full_size[0]), tuple(raw.ratio), tuple(comp.ratio), state["allowed"](k),
                              cfg, roi=state["roi"])
    return sim.to_dict(), float(z)


def _w_final(state: dict, task: tuple) -> tuple:
    """Candidate vector around m (>= m±R, extended up to ±CAND_W//2 while the edge score is within ``cap``
    of the peak, so the soft range is never cut by the window) and the visually-identical RAW range
    [raw_lo, raw_hi] (RAW-vs-RAW, warped & masked)."""
    k, keys, flip, jb, lo, scores, R, cap = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    n = raw.n
    sim = _sim_at(keys, k, state["raw_wh"])
    allowed = state["allowed"](k)
    sc = _Scorer(np.asarray(comp.get(k)), state["roi"], allowed, raw, comp.ratio, cfg)
    S = {lo + i: float(v) for i, v in enumerate(scores) if np.isfinite(v)}
    half = CAND_W // 2

    def ev(js: list[int]) -> None:
        js = [j for j in js if 0 <= j < n and j not in S and raw.has(j)]
        if js:
            for j, v in zip(js, sc.scores(js, sim, flip)):
                S[j] = float(v)

    ev(list(range(jb - R, jb + R + 1)))
    peak = S.get(jb, float("nan"))
    for direction in (-1, 1):
        d = R
        while d < half:
            edge_v = S.get(jb + direction * d, float("nan"))
            if not (np.isfinite(edge_v) and np.isfinite(peak) and edge_v >= peak - cap):
                break
            d += 1
            ev([jb + direction * d])
    # visually identical neighbours
    W = float(raw.full_size[0])
    rr, cr = tuple(raw.ratio), tuple(comp.ratio)
    roi = state["roi"]
    wb, vb = scoring.warp_to_roi(np.asarray(raw.get(jb)), sim, flip, W, rr, cr, roi)
    x0, y0, w, h = roi
    am = allowed[y0:y0 + h, x0:x0 + w] & vb
    raw_lo = raw_hi = jb
    for direction in (-1, 1):
        for d in range(1, _IDENTICAL_MAX + 1):
            j = jb + direction * d
            if not raw.has(j):
                break
            wj, vj = scoring.warp_to_roi(np.asarray(raw.get(j)), sim, flip, W, rr, cr, roi)
            m = am & vj
            if m.sum() < 64:
                break
            mad = float(np.mean(np.abs(wb[m] - wj[m])))
            z = scoring.zncc(wb, wj, m)
            if mad <= cfg.identical_mad or (np.isfinite(z) and z >= cfg.identical_thresh):
                if direction < 0:
                    raw_lo = j
                else:
                    raw_hi = j
            else:
                break
    lo2, hi2 = min(S), max(S)
    arr = np.full(hi2 - lo2 + 1, np.nan, np.float32)
    for j, v in S.items():
        arr[j - lo2] = v
    return int(lo2), arr, int(raw_lo), int(raw_hi)


def _w_resid(state: dict, task: tuple) -> np.ndarray:
    """|comp - (a·warped RAW + b)| (8-bit, blurred, gain/offset fitted) over the valid box ROI, 0 elsewhere.
    Returns the box-ROI crop only (h x w of state['roi']; the caller pads it to the full frame)."""
    import cv2
    k, keys, flip, jb = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    sim = _sim_at(keys, k, state["raw_wh"])
    roi = state["roi"]
    x0, y0, w, h = roi
    img = np.asarray(comp.get(k))
    out = np.zeros((h, w), np.uint8)
    wr, vr = scoring.warp_to_roi(np.asarray(raw.get(jb)), sim, flip, float(raw.full_size[0]), tuple(raw.ratio),
                                 tuple(comp.ratio), roi)
    sig = float(cfg.score_blur)
    c = img[y0:y0 + h, x0:x0 + w].astype(np.float32)
    if sig > 0:
        c = cv2.GaussianBlur(c, (0, 0), sig)
        wr = cv2.GaussianBlur(wr, (0, 0), sig)
    # exclude pixels whose blurred values mix in the black outside the box or the zero border outside
    # the warped RAW frame (both would read as residual 'overlays' along the edges)
    er = int(math.ceil(2.0 * sig)) + 1
    ker = np.ones((2 * er + 1, 2 * er + 1), np.uint8)
    base = state["base_allowed"][y0:y0 + h, x0:x0 + w].astype(np.uint8) & vr.astype(np.uint8)
    base = cv2.erode(base, ker, borderType=cv2.BORDER_CONSTANT, borderValue=0) > 0
    if base.sum() < 64:
        return out
    xv = wr[base].astype(np.float64)
    yv = c[base].astype(np.float64)
    xm, ym = xv.mean(), yv.mean()
    vx = float(((xv - xm) ** 2).sum())
    gain = float(((xv - xm) * (yv - ym)).sum()) / vx if vx > 1e-9 else 0.0
    res = np.abs(c - (gain * wr + (ym - gain * xm)))       # least-squares gain/offset fit
    res[~base] = 0
    return np.clip(res, 0, 255).astype(np.uint8)


def _w_confound(state: dict, task: tuple) -> tuple[float, float]:
    """(score of m under the model, best score of m±1 after an ECC refit from the model)."""
    k, j, flip, sim_d = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    img = np.asarray(comp.get(k))
    allowed = state["allowed"](k)
    sim = Sim.from_dict(sim_d)
    sc = _Scorer(img, state["roi"], allowed, raw, comp.ratio, cfg)
    z0 = float(sc.scores([j], sim, flip)[0])
    best = -np.inf
    for jj in (j - 1, j + 1):
        if raw.has(jj):
            _, z = refine_transform(img, np.asarray(raw.get(jj)), sim, flip, float(raw.full_size[0]), tuple(raw.ratio),
                                    tuple(comp.ratio), allowed, cfg, roi=state["roi"])
            if np.isfinite(z):
                best = max(best, z)
    return z0, float(best)


def _masks_from_residuals_local(residuals: dict[int, np.ndarray], base_allowed: np.ndarray,
                                cfg) -> dict[int, np.ndarray]:
    """Local equivalent of layout.masks_from_residuals: pixels whose residual >= overlay_resid_thresh in
    >= overlay_min_frames of the frames k-3..k+3 (among the given frames), dilated by overlay_dilate_px;
    frames whose mask would cover > 35 % of the allowed region are skipped (a mismatch, not an overlay)."""
    import cv2
    ks = sorted(residuals)
    if not ks:
        return {}
    ys, xs = np.nonzero(base_allowed)
    if len(ys) == 0:
        return {}
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1       # work inside the allowed bbox
    hi = np.stack([np.asarray(residuals[k])[y0:y1, x0:x1] >= cfg.overlay_resid_thresh for k in ks])
    flat = hi.reshape(len(ks), -1)
    kk = np.array(ks)
    lo_i = np.searchsorted(kk, kk - 3, side="left")
    hi_i = np.searchsorted(kk, kk + 3, side="right")
    d = int(cfg.overlay_dilate_px)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1)) if d > 0 else None
    base = base_allowed[y0:y1, x0:x1]
    area = max(1, int(base_allowed.sum()))
    out: dict[int, np.ndarray] = {}
    for i, k in enumerate(ks):
        idx = np.flatnonzero(flat[i])
        if idx.size == 0:
            continue
        cnt = flat[lo_i[i]:hi_i[i], idx].sum(axis=0)          # temporal support of the flagged pixels
        keep = idx[cnt >= min(int(cfg.overlay_min_frames), int(hi_i[i] - lo_i[i]))]
        if keep.size == 0:
            continue
        m = np.zeros(flat.shape[1], bool)
        m[keep] = True
        m = m.reshape(hi.shape[1:])
        m8 = m.astype(np.uint8)
        if ker is not None:
            m8 = cv2.dilate(m8, ker)
        m = (m8 > 0) & base
        if m.sum() > 0.35 * area:
            continue
        full = np.zeros(base_allowed.shape, bool)
        full[y0:y1, x0:x1] = m
        out[k] = full
    return out


# ---------------------------------------------------------------------------------------------
# The refiner
# ---------------------------------------------------------------------------------------------

class _Refiner:
    def __init__(self, comp: Proxy, raw: Proxy, layout: Layout | None, overlays: Any, anchors: list[Anchor],
                 hints: AudioHints | None, index: RawIndex | None, cfg, dlog: DecisionLog,
                 allowed: AllowedMasks, residual_fn: Callable | None):
        self.comp, self.raw, self.layout, self.overlays = comp, raw, layout, overlays
        self.anchors = [a for a in anchors if 0 <= a.k < comp.n and 0 <= a.raw < raw.n]
        self.hints, self.index, self.cfg, self.dlog = hints, index, cfg, dlog
        self.allowed = allowed
        self.residual_fn = residual_fn
        self.N = int(comp.n)
        self.roi = box_roi(layout, comp)
        self.raw_wh = (float(raw.full_size[0]), float(raw.full_size[1]))
        self.u1 = float(raw.fps / comp.fps)
        if layout is not None and layout.box is not None:
            b = layout.box
            self.center = np.array([b.x + b.w / 2.0, b.y + b.h / 2.0])
        else:
            self.center = np.array([comp.full_size[0] / 2.0, comp.full_size[1] / 2.0])
        self.stride = max(1, int(cfg.comp_search_stride))
        self.ext = 2 * self.stride + 1
        self.max_gap = 3 * self.stride + 1
        self.scale_tol = float(cfg.link_scale_tol)
        self.pos_tol = max(float(cfg.link_pos_tol), 1.0 / float(comp.ratio[0]))
        self.workers = cfg.resolved_workers()
        self.tracks: dict[int, _Track] = {}
        self._next_id = 0
        self.hyp: list[dict[int, _Hyp]] = [dict() for _ in range(self.N)]
        self.win_tid = np.full(self.N, -1, np.int64)
        self.win_j = np.full(self.N, -1, np.int64)
        self.win_s = np.full(self.N, np.nan)
        self.uniform = np.zeros(self.N, bool)
        self.searched: set[int] = set(a.k for a in self.anchors)
        self.pass2_done: set[int] = set()
        self.stats: dict[str, Any] = {"eval_tasks": 0, "ecc_tasks": 0, "rescue_searches": 0}

    # -- plumbing -------------------------------------------------------------------------------
    def _state(self) -> dict:
        return {"comp": self.comp, "raw": self.raw, "cfg": self.cfg, "allowed": self.allowed, "roi": self.roi,
                "raw_wh": self.raw_wh, "base_allowed": self.allowed.base}

    def _map(self, fn, tasks: list) -> list:
        return parallel_map(fn, tasks, self.workers, self._state(), self.cfg.seed)

    def _new_track(self, flip: bool, anchors: list[Anchor]) -> _Track:
        t = _Track(self._next_id, bool(flip), sorted(anchors, key=lambda a: a.k))
        self._next_id += 1
        self.tracks[t.id] = t
        return t

    # -- 1. linking -----------------------------------------------------------------------------
    def _trend(self, anchors: list[Anchor], k: int) -> tuple[Sim, float | None]:
        """Transform predicted at k from the last <= 4 anchors (linear trend) + RAW frame prediction."""
        rec = anchors[-4:]
        if len(rec) == 1:
            return rec[0].sim, None
        ks = np.array([a.k for a in rec], np.float64)
        A = np.c_[np.ones(len(ks)), ks]

        def lin(v: np.ndarray) -> float:
            coef, *_ = np.linalg.lstsq(A, v, rcond=None)
            return float(coef[0] + coef[1] * k)
        p_ref = rec[-1].sim.inverse().apply(self.center)[0]
        c = np.array([a.sim.apply(p_ref)[0] for a in rec])
        s = lin(np.array([a.sim.s for a in rec]))
        th = lin(np.array([a.sim.theta_deg for a in rec]))
        cx, cy = lin(c[:, 0]), lin(c[:, 1])
        base = Sim(s, th, 0.0, 0.0).apply(p_ref)[0]
        pred = Sim(s, th, cx - base[0], cy - base[1])
        d = np.diff(np.array([a.raw for a in rec], np.float64)) / np.maximum(np.diff(ks), 1)
        slope = float(np.median(d))
        return pred, rec[-1].raw + slope * (k - rec[-1].k)

    def _link_cost(self, t: _Track, a: Anchor) -> float | None:
        if t.flip != a.flip:
            return None
        last = t.anchors[-1]
        dk = a.k - last.k
        if dk <= 0 or dk > self.max_gap:
            return None
        pred, jpred = self._trend(t.anchors, a.k)
        ds, dpos = _sim_delta(a.sim, pred, self.center)
        if ds > self.scale_tol or dpos > self.pos_tol or abs(a.sim.theta_deg - pred.theta_deg) > 0.5:
            return None
        if jpred is None:
            slope = (a.raw - last.raw) / dk
            if abs(slope) > 3.0 * self.u1:
                return None
            dj = 0.0
        else:
            tol_j = 2.0 + 0.1 * dk
            dj = abs(a.raw - jpred) / tol_j
            if dj > 1.0:
                return None
        return ds / self.scale_tol + dpos / self.pos_tol + dj

    def _link(self, anchors: Iterable[Anchor]) -> list[_Track]:
        made: list[_Track] = []
        for a in sorted(anchors, key=lambda a: (a.k, -a.zncc, a.raw, a.flip)):
            best, cost = None, math.inf
            for t in made:
                c = self._link_cost(t, a)
                if c is not None and c < cost:
                    best, cost = t, c
            if best is not None:
                best.anchors.append(a)
            else:
                made.append(self._new_track(a.flip, [a]))
        for t in made:
            t.keys = fit_track_model([(a.k, a.sim) for a in t.anchors], self.center, self.cfg)
            ks = [a.k for a in t.anchors]
            t.span = [max(0, min(ks) - self.ext), min(self.N - 1, max(ks) + self.ext)]
            self._support_from_anchors(t)
            self.dlog.record("refine", "track", track=t.id, flip=t.flip, anchors=[(a.k, a.raw) for a in t.anchors],
                             span=list(t.span), keys=t.keys)
        return made

    def _support_from_anchors(self, t: _Track) -> None:
        d: dict[int, int] = {}
        for a in t.anchors:
            d.setdefault(a.k, a.raw)
        ks = sorted(d)
        t.sup_k = np.array(ks, np.int64)
        t.sup_j = np.array([d[k] for k in ks], np.float64)

    def _update_support(self, t: _Track) -> None:
        if t.keep_support:
            t.keep_support = False
            return
        sel = np.flatnonzero((self.win_tid == t.id) & (self.win_s >= self.cfg.match_thresh))
        if len(sel) >= 2:
            t.sup_k = sel.astype(np.int64)
            t.sup_j = self.win_j[sel].astype(np.float64)
        else:
            self._support_from_anchors(t)

    # -- 3. evaluation / assignment -------------------------------------------------------------
    def _evaluate(self, pairs: list[tuple[int, _Track]]) -> None:
        if not pairs:
            return
        R, Rmax = int(self.cfg.refine_radius), int(max(self.cfg.track_search_radius, self.cfg.refine_radius))
        tasks, keys = [], []
        for k, t in sorted(pairs, key=lambda p: (p[0], p[1].id)):
            jhat = int(round(t.predict(k, self.u1)))
            tasks.append((int(k), t.keys, t.flip, jhat, R, Rmax))
            keys.append((int(k), t.id))
        self.stats["eval_tasks"] += len(tasks)
        for (k, tid), (lo, arr, jb, sb, edge, wid) in zip(keys, self._map(_w_eval, tasks)):
            self.hyp[k][tid] = _Hyp(lo, arr, jb, sb, edge, wid)

    def _assign(self, frames: Iterable[int] | None = None) -> None:
        rng = range(self.N) if frames is None else sorted(set(frames))
        for k in rng:
            best_tid, best_s, best_j = -1, -np.inf, -1
            for tid in sorted(self.hyp[k]):
                h = self.hyp[k][tid]
                if h.jb >= 0 and np.isfinite(h.sb) and h.sb > best_s:
                    best_tid, best_s, best_j = tid, h.sb, h.jb
            self.win_tid[k] = best_tid
            self.win_j[k] = best_j
            self.win_s[k] = best_s if best_tid >= 0 else np.nan

    def _span_pairs(self, tracks: Iterable[_Track]) -> list[tuple[int, _Track]]:
        return [(k, t) for t in tracks for k in range(t.span[0], t.span[1] + 1)]

    def _grow(self) -> None:
        thr = self.cfg.match_thresh
        for _ in range(10 * self.N):
            pairs: list[tuple[int, _Track]] = []
            for t in sorted(self.tracks.values(), key=lambda t: t.id):
                lo, hi = t.span
                if hi < self.N - 1 and self.win_tid[hi] == t.id and self.win_s[hi] >= thr:
                    nh = min(self.N - 1, hi + self.ext)
                    pairs += [(k, t) for k in range(hi + 1, nh + 1) if t.id not in self.hyp[k]]
                    t.span[1] = nh
                if lo > 0 and self.win_tid[lo] == t.id and self.win_s[lo] >= thr:
                    nl = max(0, lo - self.ext)
                    pairs += [(k, t) for k in range(nl, lo) if t.id not in self.hyp[k]]
                    t.span[0] = nl
            if not pairs:
                return
            for t in {p[1].id: p[1] for p in pairs}.values():
                self._update_support(t)
            self._evaluate(pairs)
            self._assign({k for k, _ in pairs})

    def _merge_duplicates(self) -> set[int]:
        """Merge tracks that explain the same frames identically (same argmax, |Δscore| <= 2e-3).
        Returns the ids of the surviving merged tracks (their models must be refitted)."""
        merged: set[int] = set()
        changed = True
        while changed:
            changed = False
            ids = sorted(self.tracks)
            for i, a_id in enumerate(ids):
                for b_id in ids[i + 1:]:
                    A, B = self.tracks.get(a_id), self.tracks.get(b_id)
                    if A is None or B is None or A.flip != B.flip:
                        continue
                    lo, hi = max(A.span[0], B.span[0]), min(A.span[1], B.span[1])
                    if hi < lo:
                        continue
                    common = agree = 0
                    for k in range(lo, hi + 1):
                        ha, hb = self.hyp[k].get(a_id), self.hyp[k].get(b_id)
                        if ha is None or hb is None or not (np.isfinite(ha.sb) and np.isfinite(hb.sb)):
                            continue
                        if max(ha.sb, hb.sb) < self.cfg.match_thresh:
                            continue
                        common += 1
                        if ha.jb == hb.jb and abs(ha.sb - hb.sb) <= 2e-3:
                            agree += 1
                    if agree >= min(3, common) and agree >= 0.8 * common and common >= 1:
                        self._merge(A, B)
                        merged.add(A.id)
                        changed = True
                        break
                if changed:
                    break
        return merged

    def _merge(self, A: _Track, B: _Track, evaluate: bool = True) -> None:
        self.dlog.record("refine", "merge_tracks", keep=A.id, drop=B.id, spans=[list(A.span), list(B.span)])
        A.anchors = sorted(A.anchors + B.anchors, key=lambda a: (a.k, -a.zncc))
        A.span = [min(A.span[0], B.span[0]), max(A.span[1], B.span[1])]
        del self.tracks[B.id]
        for k in range(B.span[0], B.span[1] + 1):
            self.hyp[k].pop(B.id, None)
        self._assign(range(B.span[0], B.span[1] + 1))
        if not evaluate:
            return
        self._update_support(A)
        need = [(k, A) for k in range(A.span[0], A.span[1] + 1) if A.id not in self.hyp[k]]
        self._evaluate(need)
        self._assign({k for k, _ in need})

    # -- 2. transform refit ---------------------------------------------------------------------
    def _refit(self, tracks: list[_Track]) -> list[_Track]:
        thr = self.cfg.match_thresh
        tasks, owners = [], []
        per: dict[int, list[int]] = {}
        for t in tracks:
            frames = np.flatnonzero((self.win_tid == t.id) & (self.win_s >= thr))
            if len(frames) == 0:
                continue
            step = max(1, int(self.cfg.framing_sample_step))
            smp = list(frames[::step])
            if smp[-1] != frames[-1]:
                smp.append(int(frames[-1]))
            if t.is_constant() and len(smp) > _CONST_SAMPLES:
                smp = [smp[int(i)] for i in np.linspace(0, len(smp) - 1, _CONST_SAMPLES).round().astype(int)]
            per[t.id] = [int(k) for k in smp]
            for k in per[t.id]:
                tasks.append((int(k), int(self.win_j[k]), t.flip, _sim_at(t.keys, k, self.raw_wh).to_dict()))
                owners.append((t.id, int(k)))
        if not tasks:
            return []
        self.stats["ecc_tasks"] += len(tasks)
        res = self._map(_w_ecc, tasks)
        samples: dict[int, list[tuple[int, Sim]]] = {}
        for (tid, k), (sd, _) in zip(owners, res):
            samples.setdefault(tid, []).append((k, Sim.from_dict(sd)))
        changed = []
        for tid, smp in sorted(samples.items()):
            t = self.tracks[tid]
            t.samples = sorted(smp, key=lambda x: x[0])
            new_keys = fit_track_model(smp, self.center, self.cfg)
            frames = np.flatnonzero(self.win_tid == tid)
            probe = frames[:: max(1, len(frames) // 16)] if len(frames) else np.array([t.span[0]])
            dmax_s = dmax_p = 0.0
            for k in probe:
                ds, dp = _sim_delta(_sim_at(new_keys, k, self.raw_wh), _sim_at(t.keys, k, self.raw_wh), self.center)
                dmax_s, dmax_p = max(dmax_s, ds), max(dmax_p, dp)
            t.keys = new_keys
            if dmax_s > 5e-4 or dmax_p > 0.25:
                changed.append(t)
            self.dlog.record("refine", "track_model", track=tid, samples=len(smp), keys=new_keys,
                             change={"scale_rel": round(dmax_s, 6), "pos_px": round(dmax_p, 4)})
        return changed

    def _chain_ok(self, A: _Track, B: _Track) -> dict | None:
        """B continues A (animated framing split at the first link): B's ECC samples start after A's,
        within max_gap of A's last sample, and go on beyond it; B's first support point continues A's time
        line (+-2 frames); the last/first <= 4 ECC samples of A/B lie on ONE linear trend (scale <= 0.15 %,
        position <= pos_tol/2, rotation <= 0.1 deg). A punch-in (a scale / position STEP) leaves larger
        residuals and is never chained. Uses samples + support points only (not the current frame
        assignment, which is stale between a merge and the next evaluation)."""
        if not A.samples or not B.samples or len(A.sup_k) == 0 or len(B.sup_k) == 0:
            return None
        a0, a1 = A.samples[0][0], A.samples[-1][0]
        b0, b1 = B.samples[0][0], B.samples[-1][0]
        if not (a0 < b0 and b0 <= a1 + self.max_gap and b1 > a1):
            return None
        kb, jb = int(B.sup_k[0]), float(B.sup_j[0])
        ka = int(A.sup_k[-1])
        if abs(jb - A.predict(kb, self.u1)) > 2.0 + 0.1 * max(0, kb - ka):
            return None
        sa, sb = A.samples[-4:], B.samples[:4]
        if len(sa) < 2 or len(sb) < 2:
            return None
        smp = sorted(sa + sb, key=lambda x: x[0])
        ks = np.array([k for k, _ in smp], np.float64)
        p_ref = sa[-1][1].inverse().apply(self.center)[0]
        sc = np.array([x.s for _, x in smp])
        th = np.array([x.theta_deg for _, x in smp])
        c = np.array([x.apply(p_ref)[0] for _, x in smp])
        M = np.c_[np.ones(len(ks)), ks]

        def resid(v: np.ndarray) -> float:
            coef, *_ = np.linalg.lstsq(M, v, rcond=None)
            return float(np.max(np.abs(v - M @ coef)))
        r = {"scale": resid(sc / sc.mean()), "pos": max(resid(c[:, 0]), resid(c[:, 1])), "rot": resid(th)}
        if r["scale"] <= 0.0015 and r["pos"] <= 0.5 * self.pos_tol and r["rot"] <= 0.1:
            return r
        return None

    def _merge_chains(self) -> set[int]:
        """Merge consecutive tracks that are one animated framing (see _chain_ok); refit their model
        from the union of their ECC samples. Returns the surviving merged track ids."""
        merged: set[int] = set()
        changed = True
        while changed:
            changed = False
            for a_id in sorted(self.tracks):
                A = self.tracks[a_id]
                for b_id in sorted(self.tracks):
                    B = self.tracks[b_id]
                    if b_id == a_id or A.flip != B.flip:
                        continue
                    r = self._chain_ok(A, B)
                    if r is None:
                        continue
                    self.dlog.record("refine", "chain_tracks", keep=A.id, drop=B.id, residuals=r)
                    A.samples = sorted(A.samples + B.samples, key=lambda x: x[0])
                    A.keys = fit_track_model(A.samples, self.center, self.cfg)
                    sup = dict(zip(A.sup_k.tolist(), A.sup_j.tolist()))
                    for k, j in zip(B.sup_k.tolist(), B.sup_j.tolist()):
                        sup.setdefault(int(k), float(j))
                    ks = sorted(sup)
                    A.sup_k = np.array(ks, np.int64)
                    A.sup_j = np.array([sup[k] for k in ks], np.float64)
                    A.keep_support = True          # re-evaluated (dirty) in the next iteration
                    self._merge(A, B, evaluate=False)
                    merged.add(A.id)
                    changed = True
                    break
                if changed:
                    break
        return {m for m in merged if m in self.tracks}

    def _converge(self, dirty: list[_Track]) -> None:
        """Alternate frame assignment (evaluate + grow + merge) and per-track transform refits until the
        models stop changing; at most _MAX_ITER evaluation passes (the last pass is not refitted, so the
        stored scores were computed under the final model)."""
        dirty = [t for t in dirty if t.id in self.tracks]
        for it in range(_MAX_ITER):
            if not dirty:
                break
            before = self.win_j.copy()
            for t in dirty:
                self._update_support(t)
            pairs = self._span_pairs(dirty)
            self._evaluate(pairs)
            self._assign({k for k, _ in pairs})
            self._grow()
            merged = self._merge_duplicates()
            self._drop_empty()
            moved = int(np.sum(before != self.win_j))
            if it == _MAX_ITER - 1:
                self.dlog.record("refine", "iteration", iteration=it, tracks=[t.id for t in dirty],
                                 frames_changed=moved, models_changed=[])
                break
            todo = {t.id: t for t in dirty if t.id in self.tracks}
            todo.update({tid: self.tracks[tid] for tid in merged if tid in self.tracks})
            changed = self._refit([todo[i] for i in sorted(todo)])
            chained = self._merge_chains()
            dirty_ids = {t.id for t in changed if t.id in self.tracks} | chained
            self.dlog.record("refine", "iteration", iteration=it, tracks=sorted(todo), frames_changed=moved,
                             models_changed=[t.id for t in changed], chained=sorted(chained))
            dirty = [self.tracks[i] for i in sorted(dirty_ids)]

    def _drop_empty(self) -> None:
        for tid in sorted(self.tracks):
            t = self.tracks[tid]
            if not np.any(self.win_tid == tid):
                self.dlog.record("refine", "drop_track", track=tid, reason="wins no frame",
                                 anchors=[(a.k, a.raw) for a in t.anchors])
                for k in range(t.span[0], t.span[1] + 1):
                    self.hyp[k].pop(tid, None)
                del self.tracks[tid]

    # -- 4. overlay pass 2 ------------------------------------------------------------------------
    def _overlay_pass2(self) -> list[int]:
        """Residual overlay masks for frames whose best hypothesis scores >= none_thresh and that were
        not processed yet (called again after rescue for new tracks); re-scores the masked frames."""
        fn = self.residual_fn
        if fn is None:
            return []
        eligible = [k for k in range(self.N) if self.win_tid[k] >= 0 and np.isfinite(self.win_s[k])
                    and self.win_s[k] >= self.cfg.none_thresh]
        core_all = [k for k in eligible if k not in self.pass2_done]
        if not core_all:
            return []
        self.pass2_done.update(core_all)
        changed: list[int] = []
        chunk, ctx = 96, 3
        elig = np.array(eligible)
        for c0 in range(0, len(core_all), chunk):
            core = core_all[c0:c0 + chunk]
            sel = [int(k) for k in elig[(elig >= core[0] - ctx) & (elig <= core[-1] + ctx)]]
            tasks = []
            for k in sel:
                t = self.tracks[int(self.win_tid[k])]
                tasks.append((k, t.keys, t.flip, int(self.win_j[k])))
            res = self._map(_w_resid, tasks)
            x0, y0, w, h = self.roi
            residuals = {}
            for k, r in zip(sel, res):
                full = np.zeros(self.allowed.base.shape, np.uint8)
                full[y0:y0 + h, x0:x0 + w] = r
                residuals[k] = full
            new = fn(residuals, self.allowed.base, self.cfg) or {}
            for k in core:
                m = new.get(k)
                if m is None:
                    continue
                m = np.asarray(m, bool)
                if m.shape != self.allowed.base.shape:
                    log.warning("overlay pass 2: mask shape %s != proxy %s - ignored", m.shape, self.allowed.base.shape)
                    continue
                if int((m & self.allowed(k)).sum()) < 16:
                    continue
                self.allowed.add_extra(k, m)
                if self.overlays is not None and hasattr(self.overlays, "union"):
                    self.overlays.union(k, m)
                changed.append(k)
        if changed:
            # re-score the winner and every hypothesis within 0.05 of it (masking an overlay moves
            # scores by far less; hypotheses further below cannot win and do not set the margin)
            pairs = [(k, self.tracks[tid]) for k in changed for tid in sorted(self.hyp[k])
                     if tid in self.tracks and np.isfinite(self.hyp[k][tid].sb)
                     and self.hyp[k][tid].sb >= self.win_s[k] - 0.05]
            self._evaluate(pairs)
            self._assign(changed)
        self.dlog.record("refine", "overlay_pass2", frames=len(core_all), masked_frames=changed[:500],
                         n_masked=len(changed))
        return changed

    # -- 5. rescue --------------------------------------------------------------------------------
    def _rescue_triggers(self) -> list[int]:
        thr = self.cfg.match_thresh
        trig = []
        for k in range(self.N):
            if self.uniform[k]:
                continue
            tid, s = int(self.win_tid[k]), float(self.win_s[k])
            if tid < 0 or not np.isfinite(s) or s < thr:
                trig.append(k)
                continue
            if self.hyp[k][tid].edge:
                trig.append(k)
                continue
            nb = [self.win_s[i] for i in range(max(0, k - 5), min(self.N, k + 6))
                  if i != k and self.win_tid[i] == tid and np.isfinite(self.win_s[i])]
            if len(nb) >= 3:
                nb = np.array(nb)
                med = float(np.median(nb))
                mad = float(np.median(np.abs(nb - med)))
                if s < med - max(self.cfg.rel_drop_min, 4.0 * mad):
                    trig.append(k)
        return trig

    def _rescue(self) -> bool:
        if self.index is None:
            return False
        trig = [k for k in self._rescue_triggers() if k not in self.searched]
        if not trig:
            return False
        todo: list[int] = []
        run: list[int] = []
        for k in trig + [None]:
            if run and (k is None or k != run[-1] + 1):
                if len(run) > 2 * self.stride:
                    sel = run[::self.stride]
                    if sel[-1] != run[-1]:
                        sel.append(run[-1])
                    todo += sel
                else:
                    todo += run
                run = []
            if k is not None:
                run.append(k)
        self.searched.update(todo)
        self.stats["rescue_searches"] += len(todo)
        found = run_searches(self.comp, self.raw, self.index, self.allowed, self.roi, self.hints, todo, self.cfg,
                             source="rescue")
        new: list[Anchor] = []
        for k, anchors, rep in found:
            confirms = []
            for a in anchors:
                a.source = "rescue"
                tid = int(self.win_tid[k])
                if tid >= 0 and self.tracks[tid].flip == a.flip and abs(int(self.win_j[k]) - a.raw) <= 1:
                    ds, dp = _sim_delta(a.sim, _sim_at(self.tracks[tid].keys, k, self.raw_wh), self.center)
                    if ds <= self.scale_tol and dp <= self.pos_tol and self.win_s[k] >= self.cfg.match_thresh:
                        confirms.append(a)
                        continue
                new.append(a)
            self.dlog.record("refine", "rescue_search", comp_frame=k,
                             current={"track": int(self.win_tid[k]), "raw": int(self.win_j[k]),
                                      "score": (None if not np.isfinite(self.win_s[k])
                                                else round(float(self.win_s[k]), 4))},
                             found=[{"raw": a.raw, "flip": a.flip, "zncc": round(a.zncc, 4), "inliers": a.inliers}
                                    for a in anchors], confirmed=len(confirms), rejected=rep[:6])
        self.anchors.extend(new)
        if not new:
            return False
        made = self._link(new)
        self._converge(made)
        return True

    # -- time/translation confound check ---------------------------------------------------------
    def _confound_check(self, fm: FrameMap, delta: dict[int, float]) -> None:
        tasks, owners = [], []
        for tid, t in sorted(self.tracks.items()):
            if not t.is_constant():
                continue
            frames = np.flatnonzero((fm.track == tid) & (fm.status == Status.MATCH))
            if len(frames) < 3:
                continue
            for k in frames[np.unique(np.linspace(0, len(frames) - 1, min(5, len(frames))).round().astype(int))]:
                tasks.append((int(k), int(fm.raw[k]), t.flip, fm.sim(int(k)).to_dict()))
                owners.append(tid)
        if not tasks:
            return
        res = self._map(_w_confound, tasks)
        per: dict[int, list[tuple[float, float]]] = {}
        for tid, r in zip(owners, res):
            per.setdefault(tid, []).append(r)
        conf_col = np.zeros(self.N, bool)
        for tid, rs in per.items():
            d = delta.get(tid, self.cfg.soft_delta_min)
            if all(np.isfinite(z0) and np.isfinite(za) and za >= z0 - d for z0, za in rs):
                self.tracks[tid].confounded = True
                conf_col |= (fm.track == tid) & (fm.status == Status.MATCH)
                self.dlog.record("refine", "time_translation_confounded", track=tid,
                                 evidence=[{"model": round(z0, 5), "refit_pm1": round(za, 5)} for z0, za in rs],
                                 delta=d)
        fm.d["confounded"] = conf_col

    # -- 6. finalize ------------------------------------------------------------------------------
    def _finalize(self) -> FrameMap:
        cfg = self.cfg
        fm = FrameMap(self.N)
        thr = cfg.match_thresh
        R = int(cfg.refine_radius)
        half = CAND_W // 2
        match = [k for k in range(self.N) if self.win_tid[k] >= 0 and self.win_s[k] >= thr]
        # provisional soft delta per track (margin of the argmax within its own window) -> how far the
        # candidate window must extend so that the soft range {S >= max - delta} is never truncated
        prov: dict[int, list[float]] = {}
        for k in match:
            h = self.hyp[k][int(self.win_tid[k])]
            others = np.delete(h.scores, h.jb - h.lo)
            if np.any(np.isfinite(others)):
                prov.setdefault(int(self.win_tid[k]), []).append(h.sb - float(np.nanmax(others)))
        cap: dict[int, float] = {}
        for tid, mg in prov.items():
            mg = np.array(mg)
            mad = float(np.median(np.abs(mg - np.median(mg))))
            cap[tid] = min(_SOFT_CAP_MAX, 2.0 * max(float(cfg.soft_delta_min), 3.0 * mad))
        tasks = []
        for k in match:
            t = self.tracks[int(self.win_tid[k])]
            h = self.hyp[k][t.id]
            tasks.append((k, t.keys, t.flip, int(self.win_j[k]), h.lo, h.scores, R,
                          cap.get(t.id, 2.0 * float(cfg.soft_delta_min))))
        res = dict(zip(match, self._map(_w_final, tasks)))
        status = np.full(self.N, Status.NONE, np.int8)
        raw_a = np.full(self.N, -1, np.int32)
        lo_a = np.full(self.N, -1, np.int32)
        hi_a = np.full(self.N, -1, np.int32)
        score = np.full(self.N, np.nan, np.float32)
        second = np.full(self.N, np.nan, np.float32)
        flip = np.zeros(self.N, bool)
        sims = np.full((self.N, 4), np.nan)
        track = np.full(self.N, -1, np.int32)
        widened = np.zeros(self.N, bool)
        cand = np.full((self.N, CAND_W), np.nan, np.float32)
        cand_j0 = np.full(self.N, -1, np.int32)
        vecs: dict[int, tuple[int, np.ndarray]] = {}
        for k in range(self.N):
            tid = int(self.win_tid[k])
            if tid < 0:
                continue
            t = self.tracks[tid]
            h = self.hyp[k][tid]
            track[k] = tid
            flip[k] = t.flip
            sm = _sim_at(t.keys, k, self.raw_wh)
            sims[k] = (sm.s, sm.theta_deg, sm.tx, sm.ty)
            widened[k] = h.widened
            score[k] = self.win_s[k]
            if k in res:
                lo2, arr, rlo, rhi = res[k]
            else:
                lo2, arr, rlo, rhi = h.lo, h.scores, int(self.win_j[k]), int(self.win_j[k])
            vecs[k] = (lo2, arr)
            jb = int(self.win_j[k])
            j0 = jb - half
            for i in range(CAND_W):
                p = j0 + i - lo2
                if 0 <= p < len(arr):
                    cand[k, i] = arr[p]
            cand_j0[k] = j0
            # second best: outside [rlo, rhi] under this hypothesis, and other hypotheses
            js = np.arange(lo2, lo2 + len(arr))
            outside = (js < rlo) | (js > rhi)
            vals = arr[outside]
            sec = float(np.nanmax(vals)) if np.any(np.isfinite(vals)) else -np.inf
            for otid, oh in self.hyp[k].items():
                if otid == tid or otid not in self.tracks or not np.isfinite(oh.sb):
                    continue
                if self.tracks[otid].flip == t.flip and rlo <= oh.jb <= rhi:
                    continue
                sec = max(sec, oh.sb)
            second[k] = sec if np.isfinite(sec) else np.nan
            if k in res:
                status[k] = Status.MATCH
                raw_a[k], lo_a[k], hi_a[k] = jb, rlo, rhi
        fm.status = status
        fm.raw, fm.raw_lo, fm.raw_hi = raw_a, lo_a, hi_a
        fm.score, fm.second = score, second
        fm.margin = np.where(np.isfinite(second), score - second, np.nan).astype(np.float32)
        fm.flip, fm.track, fm.widened = flip, track, widened
        fm.s, fm.theta, fm.tx, fm.ty = sims[:, 0], sims[:, 1], sims[:, 2], sims[:, 3]
        fm.cand, fm.cand_j0 = cand, cand_j0
        fm.mean, fm.std = self._mean, self._std
        # UNIFORM / NONE
        for k in range(self.N):
            if status[k] != Status.MATCH and self.uniform[k]:
                status[k] = Status.UNIFORM
        fm.status = status
        # per-track delta, soft ranges, low_margin, confidence
        delta: dict[int, float] = {}
        for tid in sorted(self.tracks):
            m = fm.margin[(fm.track == tid) & (fm.status == Status.MATCH)]
            m = m[np.isfinite(m)]
            mad = float(np.median(np.abs(m - np.median(m)))) if len(m) else 0.0
            delta[tid] = max(float(cfg.soft_delta_min), 3.0 * mad)
        soft_lo = np.full(self.N, -1, np.int32)
        soft_hi = np.full(self.N, -1, np.int32)
        low = np.zeros(self.N, bool)
        conf = np.zeros(self.N, np.float32)
        for k in range(self.N):
            st = int(status[k])
            if st == Status.MATCH:
                lo2, arr = vecs[k]
                d = delta.get(int(track[k]), cfg.soft_delta_min)
                peak = float(score[k])
                js = np.flatnonzero(np.isfinite(arr) & (arr >= peak - d)) + lo2
                a = int(min(js.min(), lo_a[k])) if len(js) else int(lo_a[k])
                b = int(max(js.max(), hi_a[k])) if len(js) else int(hi_a[k])
                soft_lo[k], soft_hi[k] = a, b
                mg = float(fm.margin[k])
                low[k] = bool(np.isfinite(mg) and mg <= cfg.low_margin_eps)
                base = min(1.0, max(0.0, (peak - cfg.none_thresh) / (1.0 - cfg.none_thresh)))
                mf = 1.0 if not np.isfinite(mg) else min(1.0, max(0.5, 0.5 + mg / 0.01))
                conf[k] = base * mf
            elif st == Status.UNIFORM:
                sd = float(self._std[k]) if np.isfinite(self._std[k]) else 0.0
                conf[k] = min(1.0, max(0.0, 1.0 - sd / max(cfg.uniform_std, 1e-6)))
            else:
                sc = float(score[k])
                span = max(cfg.match_thresh - cfg.none_thresh, 1e-6)
                conf[k] = 1.0 if not np.isfinite(sc) else min(1.0, max(0.0, (cfg.match_thresh - sc) / span))
        fm.soft_lo, fm.soft_hi, fm.low_margin, fm.conf = soft_lo, soft_hi, low, conf
        # anchor inliers on frames where a keypoint search ran and agrees with the result
        inl = np.full(self.N, -1, np.int32)
        for a in self.anchors:
            if 0 <= a.k < self.N and status[a.k] == Status.MATCH and fm.flip[a.k] == a.flip \
                    and abs(int(fm.raw[a.k]) - a.raw) <= 1:
                inl[a.k] = max(inl[a.k], a.inliers)
        fm.inliers = inl
        fm.d["confounded"] = np.zeros(self.N, bool)
        self._delta = delta
        return fm

    # -- 7. debug images --------------------------------------------------------------------------
    def _debug_pngs(self, fm: FrameMap, debug_dir: str | Path | None) -> int:
        if debug_dir is None:
            return 0
        import cv2
        out = Path(debug_dir) / "low_confidence"
        low = [k for k in range(self.N) if fm.conf[k] < self.cfg.low_conf_thresh and fm.status[k] != Status.UNIFORM]
        if out.is_dir():
            for p in out.glob("k*.png"):
                p.unlink()
        if not low:
            return 0
        out.mkdir(parents=True, exist_ok=True)
        low = sorted(sorted(low, key=lambda k: (float(fm.conf[k]), k))[:_MAX_DEBUG_PNG])
        x0, y0, w, h = self.roi
        W = float(self.raw.full_size[0])
        for k in low:
            img = np.asarray(self.comp.get(k))
            panels = [img[y0:y0 + h, x0:x0 + w].astype(np.uint8)]
            labels = [f"comp k={k} conf={fm.conf[k]:.2f}"]
            tid = int(fm.track[k])
            if tid >= 0 and np.isfinite(fm.s[k]):
                sim, fl = fm.sim(k), bool(fm.flip[k])
                j0, vec = fm.cand_scores(k)
                order = [int(i) for i in np.argsort(-np.nan_to_num(vec.astype(np.float64), nan=-np.inf))
                         if np.isfinite(vec[i])][:2]
                for rank, i in enumerate(order):
                    j = j0 + i
                    if not self.raw.has(j):
                        continue
                    wr, _ = scoring.warp_to_roi(np.asarray(self.raw.get(j)), sim, fl, W, tuple(self.raw.ratio),
                                                tuple(self.comp.ratio), self.roi)
                    panels.append(np.clip(wr, 0, 255).astype(np.uint8))
                    labels.append(f"{'best' if rank == 0 else '2nd'} raw={j} s={vec[i]:.4f}")
            tiles = []
            for p, lab in zip(panels, labels):
                t = cv2.cvtColor(p, cv2.COLOR_GRAY2BGR)
                cv2.putText(t, lab, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1, cv2.LINE_AA)
                tiles.append(t)
            cv2.imwrite(str(out / f"k{k:05d}.png"), np.hstack(tiles))
        return len(low)

    # -- main -------------------------------------------------------------------------------------
    def run(self, debug_dir: str | Path | None) -> FrameMap:
        cfg = self.cfg
        self._mean = np.full(self.N, np.nan, np.float32)
        self._std = np.full(self.N, np.nan, np.float32)
        for k in range(self.N):
            mu, sd = scoring.region_stats(np.asarray(self.comp.get(k)), self.roi, self.allowed(k))
            self._mean[k], self._std[k] = mu, sd
        self.uniform = ~(self._std >= cfg.uniform_std)          # NaN (empty region) counts as uniform
        tm = self.stats.setdefault("seconds", {})
        t0 = time.perf_counter()

        def lap(name: str) -> None:
            nonlocal t0
            t1 = time.perf_counter()
            tm[name] = round(tm.get(name, 0.0) + t1 - t0, 2)
            t0 = t1
        tracks = self._link(self.anchors)
        self._converge(tracks)
        lap("tracks")
        self._overlay_pass2()
        lap("overlay_pass2")
        for _ in range(2):
            if not self._rescue():
                break
            lap("rescue")
            self._overlay_pass2()
            lap("overlay_pass2")
        lap("rescue")
        fm = self._finalize()
        lap("finalize")
        self._confound_check(fm, self._delta)
        lap("confound")
        n_png = self._debug_pngs(fm, debug_dir)
        lap("debug_png")
        counts = {name: int(np.sum(fm.status == v)) for name, v in
                  (("match", Status.MATCH), ("none", Status.NONE), ("uniform", Status.UNIFORM))}
        self.dlog.record("refine", "frame_map", counts=counts, tracks=len(self.tracks),
                         low_margin=int(fm.low_margin.sum()),
                         widened=[int(k) for k in np.flatnonzero(fm.widened)][:500],
                         ambiguous=[int(k) for k in np.flatnonzero(fm.raw_hi > fm.raw_lo)][:500],
                         low_conf_png=n_png, stats=self.stats,
                         confounded_tracks=[t.id for t in self.tracks.values() if t.confounded])
        log.info("frame map: %s, %d tracks, %d eval / %d ECC tasks, %d rescue searches", counts, len(self.tracks),
                 self.stats["eval_tasks"], self.stats["ecc_tasks"], self.stats["rescue_searches"])
        return fm


def _default_residual_fn() -> Callable:
    try:
        from .layout import masks_from_residuals  # lazy: written by another agent
        return masks_from_residuals
    except ImportError:
        log.warning("layout.masks_from_residuals unavailable - using the local residual-mask rule")
        return _masks_from_residuals_local


def build_frame_map(comp: Proxy, raw: Proxy, layout: Layout | None, overlays: Any, anchors: list[Anchor],
                    hints: AudioHints | None, index: RawIndex | None, cfg, cache: Cache | None,
                    dlog: DecisionLog | None, debug_dir: str | Path | None,
                    allowed_fn: Callable[[int], np.ndarray] | None = None,
                    residual_fn: Callable | None | bool = None) -> FrameMap:
    """m(k) for every competitor frame (DESIGN §5 refine.py; algorithm in the module docstring).

    ``anchors`` from visual_match.sparse_search; ``index`` is needed for the rescue search (None
    disables it). ``allowed_fn(k)`` overrides the default :class:`AllowedMasks` (box & ~static &
    ~overlay). ``residual_fn(residuals, base_allowed, cfg) -> {k: mask}`` overrides
    ``layout.masks_from_residuals`` for overlay pass 2 (``False`` disables the pass). Masks found by
    pass 2 are merged into ``overlays`` in place (``overlays.union``) so later stages use them.
    Cached (stage 'frame_map') when ``cache`` is given; the pipeline saves the returned FrameMap.
    """
    dlog = dlog or null_dlog()
    base_fn = allowed_fn
    allowed = AllowedMasks(layout, overlays, comp, cfg, base_fn=base_fn)
    if residual_fn is False:
        rfn = None
    elif residual_fn is None or residual_fn is True:
        rfn = _default_residual_fn()
    else:
        rfn = residual_fn
    key = None
    if cache is not None:
        key = stage_key("frame_map", proxy_id(comp), proxy_id(raw), layout_id(layout), overlays_id(overlays),
                        params_hash([a.to_dict() for a in sorted(anchors, key=lambda a: (a.k, a.raw, a.flip))]),
                        hints_id(hints), index.key if index is not None else "", cfg.analysis_params(),
                        "custom_allowed" if allowed_fn is not None else "",
                        getattr(rfn, "__qualname__", repr(rfn)) if rfn is not None else "no_pass2")
        p = cache.path("frame_map", key, ".npz")
        pm = cache.path("frame_map", key, ".masks.npz")
        if p.exists():
            fm = FrameMap.load(p)
            if pm.exists() and overlays is not None and hasattr(overlays, "union"):
                with np.load(pm) as z:
                    shape = tuple(int(v) for v in z["shape"])
                    for name in z.files:
                        if name.startswith("k"):
                            m = np.unpackbits(z[name])[: shape[0] * shape[1]].reshape(shape).astype(bool)
                            overlays.union(int(name[1:]), m)
            dlog.record("refine", "cache_hit", key=key)
            log.info("frame map: cache hit %s", key)
            return fm
    ref = _Refiner(comp, raw, layout, overlays, anchors, hints, index, cfg, dlog, allowed, rfn)
    fm = ref.run(debug_dir)
    if cache is not None and key is not None:
        p = cache.path("frame_map", key, ".npz")
        tmp = p.with_name(p.stem + ".tmp.npz")
        fm.save(tmp)
        tmp.replace(p)
        shape = allowed.base.shape
        packed = {f"k{k}": np.packbits(m.astype(bool)) for k, m in sorted(allowed.extra.items())}
        pm = cache.path("frame_map", key, ".masks.npz")
        tmpm = pm.with_name(pm.stem + ".tmp.npz")
        np.savez_compressed(tmpm, shape=np.array(shape), **packed)
        tmpm.replace(pm)
    return fm
