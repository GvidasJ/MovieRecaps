"""Stage 5.3 -- frame-exact refinement: m(k) for every competitor frame (DESIGN.md §5 refine.py).

Algorithm (all scores are ``scoring`` masked ZNCC in competitor space). TIME LINE FIRST (FX-03): in a moving
shot a wrong RAW frame (m +- 1) plus a compensating shift / zoom / rotation scores almost like the truth, so
RAW time is decided before framing and never by a free per-frame or per-candidate framing fit.

0. The competitor's own temporal signature (temporal.py, comp-only; measured where a line can repeat RAW frames,
   slope <= temporal_refine_max_slope): pair labels REPEAT / MOVE / UNKNOWN / CUT and each pair's editor move
   (FrameMap ``pair_label`` / ``pair_warp``, -1 / NaN where not measured). REPEAT / MOVE give a time
   line's speed and fractional phase, never its integer offset; a repeat pair's warp is the editor's own crop
   velocity (FX-07, refine side).
1. Anchors (visual_match) are grouped into RUNS by RAW time only: same flip, gaps <= max_gap, within
   +-line_time_tol frames of the run's robust snap-speed line -- never by framing, so a pan or zoom of any
   speed is one run. RANSAC near-misses only join an existing run whose line they continue.
2. Per track, ``_refit`` (time-line-first framing fit): the track's line through its anchors and won frames
   (snap speeds that explain them alike: the repeat / move labels decide);
   candidate lines = floor-phase cells x the snap speed, pruned by the repeat / move labels; the framing is
   measured by coarse-to-fine multi-start ECC (``ecc_measure``) at the central line's RAW frame on EVERY
   frame (and at +-1 every framing_sample_step frames), ONE smooth path per family (``fit_path``:
   outlier-robust, piecewise-linear RDP keys at the measured noise, no steps); every candidate line is
   scored under its family's path and the highest summed score wins (per-frame +-1 alternatives under the
   SAME path). A framing STEP in the measurements splits the track (``_steps``). Track model = the path,
   extrapolated (capped to framing_sample_step frames) beyond its keys, never held. Fit and frame assignment
   alternate (<= 3 iterations).
3. Every frame k is scored under every track active near k: RAW frames ĵ-R..ĵ+R around the track's
   prediction ĵ under the track's path; if the argmax is on the window edge the window is extended in that
   direction up to cfg.track_search_radius (then the frame is re-searched with visual_match.search_frame).
   Tracks grow frame-wise beyond their frames while they keep winning, measuring the new frames on their
   line first; duplicate tracks (same argmax, same scores) are merged; equal explanations keep the larger
   track.
4. Overlay pass 2: residual masks (layout.masks_from_residuals, or the local equivalent), re-score.
5. Rescue: frames scoring < match_thresh, or < rolling track median(±5) - max(rel_drop_min, 4·MAD), or
   whose argmax stayed on the window edge -> visual_match.search_frame -> anchors on an existing track's
   line join it, the others start runs -> re-score (catches 1-2 frame flash cuts and jump cuts inside a
   track). Remaining frames: UNIFORM (region std < uniform_std) or NONE.
6. raw_lo/raw_hi = RAW frames visually identical to m(k) (RAW-vs-RAW, warped & masked); low_margin;
   soft ranges; candidate score vectors (FrameMap.cand, CAND_W wide, centred on m); confidence. Sim columns =
   the track's path value; ``sim_meas`` / ``sim_meas_score`` = the per-frame ECC measurement of RAW m(k).
   NONE/UNIFORM frames have raw = raw_lo = raw_hi = soft_* = -1 but keep the best hypothesis' score,
   track, flip, Sim and candidate vector (diagnostics; consumers must check ``status``).
7. Confound check on every track: m +- 1 with its OWN refitted path within the track's score noise ->
   ``confounded`` + soft range widened to m +- 1; strictly better (> 3 delta) -> the frame is reassigned.
8. debug/low_confidence/k#####.png for conf < cfg.low_conf_thresh (competitor | best | 2nd best), max 200.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from . import scoring
from .scoring import noise_delta
from .common import Cache, DecisionLog, log, null_dlog, params_hash, stage_key
from .geometry import (AETransform, Sim, ae_to_matrix, from_cv_matrix, h3, interpolate_keys, rdp, sim_to_ae,
                       to_cv_matrix, translate3)
from .model import CAND_W, AudioHints, FrameMap, Layout, Proxy, Status
from .visual_match import (AllowedMasks, Anchor, RawIndex, _nanargmax, _Scorer, box_roi, hints_id, layout_id,
                           mask_bbox, overlays_id, parallel_map, proxy_id, run_searches)

__all__ = ["build_frame_map", "refine_transform"]

_MAX_ITER = 3               # transform fit <-> frame assignment alternations
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


@dataclass
class EccResult:
    """One framing measurement (:func:`ecc_measure`): ``sim`` (canonical Sim), ``z`` its masked ZNCC at proxy
    resolution (the shared scorer), ``converged`` (the finest level's optimisation ran without error and its
    result was kept), ``z0`` the ZNCC of the init."""
    sim: Sim
    z: float
    converged: bool
    z0: float


@dataclass
class _Level:
    """One pyramid level: template (blurred comp ROI) + mask, input (blurred RAW proxy), ratios and the ROI
    origin at this level (floats: the ROI crop is resized, its origin scales with it)."""
    tmpl: np.ndarray
    mask: np.ndarray
    inp: np.ndarray
    rr: tuple[float, float]
    cr: tuple[float, float]
    roi: tuple[float, float, int, int]


def _pyramid(region: "scoring.CompRegion", raw_img: np.ndarray, raw_ratio, comp_ratio, cfg,
             levels: int) -> list[_Level]:
    """Levels finest (proxy resolution) first. The comp ROI and the RAW proxy are both resized by ~2^-l
    (INTER_AREA); ratios carry the exact per-axis factors, so ``to_cv_matrix`` / ``warp_to_roi`` work at any
    level unchanged. Coarser levels stop when the template's short side would drop below
    ``ecc_pyramid_min_side``."""
    import cv2
    x0, y0, w, h = region.roi
    sig = float(cfg.score_blur)
    inp = raw_img.astype(np.float32)
    if sig > 0:
        inp = cv2.GaussianBlur(inp, (0, 0), sig)
    out = [_Level(region.img.astype(np.float32), region.mask.astype(bool), inp, tuple(raw_ratio), tuple(comp_ratio),
                  (float(x0), float(y0), int(w), int(h)))]
    min_side = int(getattr(cfg, "ecc_pyramid_min_side", 40))
    for lev in range(1, max(1, int(levels))):
        f = 0.5 ** lev
        tw, th = int(round(w * f)), int(round(h * f))
        if min(tw, th) < min_side:
            break
        rh, rw = inp.shape[:2]
        iw, ih = max(8, int(round(rw * f))), max(8, int(round(rh * f)))
        fx, fy = tw / float(w), th / float(h)
        gx, gy = iw / float(rw), ih / float(rh)
        tm = cv2.resize(region.img.astype(np.float32), (tw, th), interpolation=cv2.INTER_AREA)
        mm = cv2.resize(region.mask.astype(np.float32), (tw, th), interpolation=cv2.INTER_AREA) >= 0.999
        im = cv2.resize(inp, (iw, ih), interpolation=cv2.INTER_AREA)
        out.append(_Level(tm, mm, im, (raw_ratio[0] * gx, raw_ratio[1] * gy), (comp_ratio[0] * fx, comp_ratio[1] * fy),
                          (x0 * fx, y0 * fy, tw, th)))
    return out


def _level_zncc(L: _Level, sim: Sim, flip: bool, raw_w: float) -> float:
    w, v = scoring.warp_to_roi(L.inp, sim, flip, raw_w, L.rr, L.cr, L.roi)
    return scoring.zncc(L.tmpl, w, L.mask & v)


def _ecc_level(L: _Level, sim: Sim, flip: bool, raw_w: float, cfg) -> Sim | None:
    """OpenCV ECC (MOTION_AFFINE, template mask) at one level, projected to the closest similarity; None when
    ECC raises / leaves the physical bounds."""
    import cv2
    ox, oy, w, h = L.roi
    try:
        M = translate3(-ox, -oy) @ h3(to_cv_matrix(sim, flip, raw_w, L.rr, L.cr))
        init = np.linalg.inv(M)[:2].astype(np.float32)
        crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, int(cfg.ecc_iterations), float(cfg.ecc_eps))
        gauss = 5 if min(w, h) >= 100 else 3
        if hasattr(cv2, "findTransformECCWithMask"):
            tmask = L.mask.astype(np.uint8) * 255
            imask = np.full(L.inp.shape[:2], 255, np.uint8)
            _, Wm = cv2.findTransformECCWithMask(L.tmpl, L.inp, tmask, imask, init, cv2.MOTION_AFFINE, crit, gauss)
        else:  # pragma: no cover - older OpenCV
            _, Wm = cv2.findTransformECC(L.tmpl, L.inp, init, cv2.MOTION_AFFINE, crit, None, gauss)
        full = translate3(ox, oy) @ np.linalg.inv(h3(Wm))
        out = from_cv_matrix(full[:2], flip, raw_w, L.rr, L.cr)
    except (cv2.error, ValueError, np.linalg.LinAlgError):
        return None
    if not (0.5 * sim.s < out.s < 2.0 * sim.s) or not all(map(math.isfinite, (out.tx, out.ty, out.theta_deg))):
        return None
    return out


def _gn_level(L: _Level, sim: Sim, flip: bool, raw_w: float, cfg, lock_theta: bool) -> Sim | None:
    """Gauss-Newton fit of the canonical Sim (scale, translation; rotation unless ``lock_theta``) at one level:
    comp ~ a * warped RAW + b over the template mask & the warp's support (gain / offset solved jointly),
    Jacobian from the warped image's gradient (forward-additive Lucas-Kanade in Sim parameters). A step that
    lowers the masked ZNCC is halved (at most 4 times). None when the fit has too few pixels."""
    import cv2
    ox, oy, w, h = L.roi
    cx, cy = L.cr
    X = (np.arange(w, dtype=np.float64) + ox + 0.5)[None, :] / cx       # comp full-res CORNER coords of the
    Y = (np.arange(h, dtype=np.float64) + oy + 0.5)[:, None] / cy       # level-ROI pixel centres
    X = np.broadcast_to(X, (h, w))
    Y = np.broadcast_to(Y, (h, w))
    ker = np.ones((3, 3), np.uint8)
    cur = Sim(sim.s, 0.0 if lock_theta else sim.theta_deg, sim.tx, sim.ty)
    best_z, best = -np.inf, cur
    step = 1.0
    iters = int(cfg.ecc_iterations)
    for _ in range(iters):
        Iw, v = scoring.warp_to_roi(L.inp, cur, flip, raw_w, L.rr, L.cr, L.roi)
        m = L.mask & (cv2.erode(v.astype(np.uint8), ker) > 0)
        if int(m.sum()) < 64:
            return None if best_z == -np.inf else best
        z = scoring.zncc(L.tmpl, Iw, m)
        if not math.isfinite(z):
            return None if best_z == -np.inf else best
        if z < best_z - 1e-9:
            # the last step overshot: go back half way
            step *= 0.5
            if step < 1.0 / 16:
                break
            cur = Sim(best.s + step * (cur.s - best.s), best.theta_deg + step * (cur.theta_deg - best.theta_deg),
                      best.tx + step * (cur.tx - best.tx), best.ty + step * (cur.ty - best.ty))
            continue
        best_z, best = z, cur
        gx = cv2.Sobel(Iw, cv2.CV_32F, 1, 0, ksize=3, scale=0.125)[m].astype(np.float64)
        gy = cv2.Sobel(Iw, cv2.CV_32F, 0, 1, ksize=3, scale=0.125)[m].astype(np.float64)
        xv = Iw[m].astype(np.float64)
        yv = L.tmpl[m].astype(np.float64)
        A = np.c_[xv, np.ones_like(xv)]
        (a, b), *_ = np.linalg.lstsq(A, yv, rcond=None)
        dX, dY = X[m] - cur.tx, Y[m] - cur.ty                            # c - t = s R p
        cols = [-(gx * cx * dX + gy * cy * dY) / cur.s, -gx * cx, -gy * cy]
        if not lock_theta:
            th = math.radians(1.0)                                     # d(c)/d(theta deg) = J (c - t) * pi/180
            cols.append(-(gx * cx * (-dY) + gy * cy * dX) * th)
        J = np.c_[np.stack(cols, axis=1) * a, xv, np.ones_like(xv)]
        r = yv - (a * xv + b)
        try:
            d, *_ = np.linalg.lstsq(J, r, rcond=None)
        except np.linalg.LinAlgError:
            break
        ds, dtx, dty = float(d[0]), float(d[1]), float(d[2])
        dth = 0.0 if lock_theta else float(d[3])
        nxt = Sim(cur.s + step * ds, cur.theta_deg + step * dth, cur.tx + step * dtx, cur.ty + step * dty)
        if not (0.5 * sim.s < nxt.s < 2.0 * sim.s) or not all(map(math.isfinite, (nxt.tx, nxt.ty, nxt.theta_deg))):
            break
        cur = nxt
        # converged: the step moves the box (comp px at this level) by < 0.01 px and the scale by < 1e-5
        if abs(step * dtx * cx) < 0.01 and abs(step * dty * cy) < 0.01 and abs(step * ds) / cur.s < 1e-5 \
                and abs(step * dth) < 1e-4:
            Iw, v = scoring.warp_to_roi(L.inp, cur, flip, raw_w, L.rr, L.cr, L.roi)
            m = L.mask & (cv2.erode(v.astype(np.uint8), ker) > 0)
            z = scoring.zncc(L.tmpl, Iw, m) if int(m.sum()) >= 64 else float("nan")
            if math.isfinite(z) and z >= best_z:
                best_z, best = z, cur
            break
    return best


def ecc_measure(comp_img: np.ndarray, raw_img: np.ndarray, sim0: Sim, flip: bool, raw_w: float,
                raw_ratio: tuple[float, float], comp_ratio: tuple[float, float], allowed: np.ndarray | None,
                cfg, roi: tuple[int, int, int, int] | None = None, starts: Sequence[Sim] = (),
                lock_theta: bool = False, levels: int | None = None, phase: bool = True) -> EccResult:
    """Framing of ``raw_img`` on ``comp_img`` measured coarse-to-fine (DESIGN §5 refine.py, FX-03).

    Pyramid (``ecc_pyramid_levels``, proxy resolution down to a template short side of
    ``ecc_pyramid_min_side``); at the coarsest level every start -- ``sim0``, the extra ``starts`` and, with
    ``phase``, ``sim0`` moved by the phase-correlation translation between the comp ROI and the warped RAW --
    is optimised and the best level ZNCC continues to the finer levels. ``lock_theta``: rotation fixed at 0
    (Gauss-Newton in scale + translation, :func:`_gn_level`), else OpenCV ECC (MOTION_AFFINE projected to the
    closest similarity). The result is kept only where it raises the masked ZNCC over ``sim0`` at proxy
    resolution (else ``sim0`` is returned with converged=False); deterministic (fixed iteration counts)."""
    if roi is None:
        roi = mask_bbox(allowed, comp_img.shape)
    region = scoring.prepare_comp(comp_img, roi, allowed, cfg.score_blur, with_grad=cfg.grad_weight > 0)

    def score(s: Sim) -> float:
        return float(scoring.score_candidates(region, [raw_img], s, flip, raw_w, raw_ratio, comp_ratio,
                                              blur=cfg.score_blur, grad_weight=cfg.grad_weight)[0])
    if lock_theta:
        c = np.array([(roi[0] + roi[2] / 2.0) / comp_ratio[0], (roi[1] + roi[3] / 2.0) / comp_ratio[1]])
        sim0 = _zero_rotation(sim0, c)
        starts = [_zero_rotation(s, c) for s in starts]
    z0 = score(sim0)
    nlev = int(levels if levels is not None else getattr(cfg, "ecc_pyramid_levels", 3))
    pyr = _pyramid(region, raw_img, raw_ratio, comp_ratio, cfg, nlev)

    def opt(L: _Level, s: Sim) -> Sim | None:
        return _gn_level(L, s, flip, raw_w, cfg, True) if lock_theta else _ecc_level(L, s, flip, raw_w, cfg)

    top = pyr[-1]
    cands: list[Sim] = [sim0] + [s for s in starts if s is not None]
    if phase:
        from .temporal import _phase_shift
        wimg, wv = scoring.warp_to_roi(top.inp, sim0, flip, raw_w, top.rr, top.cr, top.roi)
        if int((top.mask & wv).sum()) >= 64:
            sx, sy = _phase_shift(top.tmpl, top.mask, wimg, wv)
            if math.hypot(sx, sy) >= 0.5:
                cands.append(sim0.translated(-sx / top.cr[0], -sy / top.cr[1]))
    best, best_z = None, -np.inf
    for s in cands:
        r = opt(top, s) if len(pyr) > 1 else s
        if r is None:
            continue
        z = _level_zncc(top, r, flip, raw_w)
        if math.isfinite(z) and z > best_z + 1e-9:
            best, best_z = r, z
    if best is None:
        return EccResult(sim0, z0, False, z0)
    cur, converged = best, False
    for L in reversed(pyr[:-1] if len(pyr) > 1 else pyr):
        r = opt(L, cur)
        if r is not None:
            cur = r
            converged = L is pyr[0]
    z1 = score(cur)
    if math.isfinite(z1) and (not math.isfinite(z0) or z1 >= z0):
        return EccResult(cur, z1, converged, z0)
    return EccResult(sim0, z0, False, z0)


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def _key(k: int, sim: Sim) -> dict:
    return {"comp_frame": int(k), "scale": float(sim.s), "rotation_deg": float(sim.theta_deg),
            "tx": float(sim.tx), "ty": float(sim.ty)}


def _lerp_keys(a: dict, b: dict, u: float, raw_wh: tuple[float, float]) -> Sim:
    """AE-linear interpolation between two keys at parameter u (geometry.interpolate_keys' rule); u outside
    [0, 1] extrapolates along the same line."""
    ra, rb = float(a.get("rotation_deg", 0.0)), float(b.get("rotation_deg", 0.0))
    if abs(ra - rb) > 1e-9:
        ea = sim_to_ae(Sim.from_dict(a), False, raw_wh[0], raw_wh[1])
        eb = sim_to_ae(Sim.from_dict(b), False, raw_wh[0], raw_wh[1])
        lerp = lambda p, q: tuple(pi + u * (qi - pi) for pi, qi in zip(p, q))  # noqa: E731
        return Sim.from_matrix(ae_to_matrix(AETransform(ea.anchor, lerp(ea.scale, eb.scale),
                                                        ea.rotation + u * (eb.rotation - ea.rotation),
                                                        lerp(ea.position, eb.position))))
    return Sim(a["scale"] + u * (b["scale"] - a["scale"]), ra + u * (rb - ra),
               a["tx"] + u * (b["tx"] - a["tx"]), a["ty"] + u * (b["ty"] - a["ty"]))


def _sim_at(keys: list[dict], k: float, raw_wh: tuple[float, float], cap: int = 0) -> Sim:
    """A track's framing model at comp frame k: AE-linear between its keys; beyond the first / last key the edge
    segment is EXTRAPOLATED for at most ``cap`` frames and held from there (a moving framing is never held at its
    last measured value -- that made every pan lag v px per frame; FX-03 step 6). cap = 0: plain hold."""
    if cap <= 0 or len(keys) < 2:
        return interpolate_keys(keys, k, raw_wh[0], raw_wh[1])
    ks = sorted(keys, key=lambda d: d["comp_frame"])
    k0, k1 = ks[0]["comp_frame"], ks[-1]["comp_frame"]
    if k > k1:
        a, b, kk = ks[-2], ks[-1], min(float(k), float(k1 + cap))
    elif k < k0:
        a, b, kk = ks[0], ks[1], max(float(k), float(k0 - cap))
    else:
        return interpolate_keys(ks, k, raw_wh[0], raw_wh[1])
    u = (kk - a["comp_frame"]) / float(b["comp_frame"] - a["comp_frame"])
    return _lerp_keys(a, b, u, raw_wh)


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


def _running_median(v: np.ndarray, w: int) -> np.ndarray:
    """Centred running median (window w) of the interior samples; the end samples are kept (a shrinking window
    would pull them toward the trend's inside). Preserves monotone sequences exactly (a pan of any
    acceleration), removes isolated outliers."""
    n = len(v)
    h = max(0, int(w) // 2)
    out = v.copy()
    if n < 3 or h == 0:
        return out
    for i in range(1, n - 1):
        r = min(h, i, n - 1 - i)
        out[i] = np.median(v[i - r:i + r + 1])
    return out


def _local_line_dev(ks: np.ndarray, v: np.ndarray, w: int) -> np.ndarray:
    """Deviation of every sample from its neighbours' local trend: the lines through any two of the w//2 + 1 (>= 3)
    nearest samples BEFORE it, or any two of those AFTER it, extrapolated to it -- the smallest deviation counts.
    ~0 on any piecewise-linear path, at its first / last sample and at a velocity knot (the knot lies on one
    side's lines), also next to a wrong sample (a clean pair remains); a frame measured on a wrong RAW frame (or
    a failed ECC) deviates from every clean line."""
    n = len(v)
    m = max(3, int(w) // 2 + 1)
    out = np.zeros(n)
    for i in range(n):
        best = None
        for idx in (list(range(i - 1, max(-1, i - 1 - m), -1)), list(range(i + 1, min(n, i + 1 + m)))):
            # every line through two of the side's points: one wrong sample among them leaves a clean pair
            for a in range(len(idx)):
                for b in range(a + 1, len(idx)):
                    ka, kb = ks[idx[a]], ks[idx[b]]
                    if ka == kb:
                        continue
                    sl = (v[idx[b]] - v[idx[a]]) / (kb - ka)
                    dev = float(v[i] - (v[idx[a]] + sl * (ks[i] - ka)))
                    if best is None or abs(dev) < abs(best):
                        best = dev
        out[i] = 0.0 if best is None else best
    return out


def fit_path(samples: Sequence[tuple[int, Sim]], center: np.ndarray, cfg) -> list[dict]:
    """ONE smooth framing path of a track from per-frame measurements (DESIGN §5 refine.py, FX-03 step 5).

    Positions are those of the RAW point the median Sim maps to ``center`` (decorrelates scale and
    translation). Samples deviating from the running median (``path_median``) by more than
    max(3.5 sigma_MAD, 1 px / 0.2 % / 0.1 deg) are outliers (a frame measured on the wrong RAW frame with a
    compensating shift, a failed ECC); the rest is median-3 smoothed and simplified by max-error RDP whose
    tolerance is the measured noise (>= rdp_pos_tol px, rdp_scale_tol, 0.05 deg): piecewise-linear keys, no
    steps (a framing step splits the track, ``_steps``), never one key per frame for noise. Constant framing
    (spreads below framing_scale_spread / framing_pos_spread / rotation_min_deg) -> one key; rotation is zeroed
    when every key is within rotation_min_deg. Fewer than 4 samples -> :func:`fit_track_model`."""
    d: dict[int, Sim] = {}
    for k, s in samples:
        d.setdefault(int(k), s)
    ss = sorted(d.items())
    if len(ss) < 4:
        return fit_track_model(ss, center, cfg)
    ks = np.array([k for k, _ in ss], np.float64)
    s = np.array([x.s for _, x in ss])
    th = np.array([x.theta_deg for _, x in ss])
    med = Sim(float(np.median(s)), float(np.median(th)), float(np.median([x.tx for _, x in ss])),
              float(np.median([x.ty for _, x in ss])))
    p_ref = med.inverse().apply(center)[0]
    cxy = np.array([x.apply(p_ref)[0] for _, x in ss])
    cx, cy = cxy[:, 0], cxy[:, 1]
    w = int(getattr(cfg, "path_median", 5))
    keep = np.ones(len(ks), bool)
    noise = {}
    for name, v, floor in (("s", s / med.s, 0.002), ("cx", cx, 1.0), ("cy", cy, 1.0), ("th", th, 0.1)):
        dev = _local_line_dev(ks, v, w)
        sig = 1.4826 * float(np.median(np.abs(dev)))
        noise[name] = sig
        keep &= np.abs(dev) <= max(3.5 * sig, floor)
    if keep.sum() < 2:
        keep[:] = True
    ks, s, th, cx, cy = ks[keep], s[keep], th[keep], cx[keep], cy[keep]

    def mk(k: float, s_: float, th_: float, cx_: float, cy_: float) -> dict:
        lin = Sim(s_, th_, 0.0, 0.0).apply(p_ref)[0]
        return _key(int(k), Sim(float(s_), float(th_), float(cx_ - lin[0]), float(cy_ - lin[1])))
    s_med = float(np.median(s))
    s2, th2, cx2, cy2 = (_running_median(v, 3) for v in (s, th, cx, cy))
    const = ((s2.max() - s2.min()) / s_med < cfg.framing_scale_spread and
             max(np.ptp(cx2), np.ptp(cy2)) < cfg.framing_pos_spread and np.ptp(th2) < cfg.rotation_min_deg)
    if const:
        keys = [mk(ks[0], s_med, float(np.median(th)), float(np.median(cx)), float(np.median(cy)))]
    else:
        tol_p = max(float(cfg.rdp_pos_tol), 3.0 * max(noise["cx"], noise["cy"]))
        tol_s = max(float(cfg.rdp_scale_tol), 3.0 * noise["s"]) * s_med
        tol_t = max(0.05, 3.0 * noise["th"])
        idx = rdp(np.c_[ks, s2, cx2, cy2, th2], np.array([tol_s, tol_p, tol_p, tol_t]))
        keys = [mk(ks[i], s2[i], th2[i], cx2[i], cy2[i]) for i in idx]
    if all(abs(kk["rotation_deg"]) <= cfg.rotation_min_deg for kk in keys):
        keys = [_key(kk["comp_frame"], _zero_rotation(Sim.from_dict(kk), center)) for kk in keys]
    return keys


def _steps(meas: Sequence[tuple[int, Sim]], center: np.ndarray, cfg, side: int = 4) -> list[int]:
    """Framing STEPS inside per-frame measurements (sorted by k): boundaries b (a cut between frame b-1 and b)
    where the linear trends of up to ``side`` measurements on each side (within 2*side frames), extrapolated to
    the boundary, differ by more than punch_pos_step px or punch_scale_step (relative) -- a punch-in / reframe,
    never a velocity change (both trends are linear). Each side must follow its own trend (residual <= 1 px /
    0.2 %), and only the largest jump of a run of flagged boundaries is returned."""
    if len(meas) < 2 * 2:
        return []
    ks = np.array([k for k, _ in meas], np.float64)
    med = Sim(float(np.median([x.s for _, x in meas])), 0.0, float(np.median([x.tx for _, x in meas])),
              float(np.median([x.ty for _, x in meas])))
    p_ref = med.inverse().apply(center)[0]
    P = np.array([x.apply(p_ref)[0] for _, x in meas])
    ls = np.log(np.array([x.s for _, x in meas]))
    V = np.c_[P, ls]

    def trend(sel: np.ndarray) -> tuple[np.ndarray, float] | None:
        if sel.sum() < 2:
            return None
        A = np.c_[np.ones(int(sel.sum())), ks[sel]]
        coef, *_ = np.linalg.lstsq(A, V[sel], rcond=None)
        res = V[sel] - A @ coef
        r = max(float(np.max(np.hypot(res[:, 0], res[:, 1]))), float(np.max(np.abs(res[:, 2]))) * 500.0)
        return coef, r

    def closest(d0: np.ndarray, d1: np.ndarray) -> float:
        """min over t in [0, 1] of |d0 + t (d1 - d0)| (two continuous trends meeting inside the gap: 0)."""
        e = d1 - d0
        ee = float(e @ e)
        t = 0.0 if ee <= 1e-12 else min(1.0, max(0.0, -float(d0 @ e) / ee))
        return float(np.linalg.norm(d0 + t * e))
    flagged: list[tuple[int, float]] = []
    for i in range(1, len(ks)):
        b = ks[i]
        left = np.zeros(len(ks), bool)
        left[max(0, i - side):i] = True
        left &= ks >= b - 2 * side
        right = np.zeros(len(ks), bool)
        right[i:i + side] = True
        right &= ks < b + 2 * side
        tl, tr = trend(left), trend(right)
        if tl is None or tr is None or tl[1] > 1.0 or tr[1] > 1.0:
            continue
        # the trends may meet anywhere between the last frame before and the first frame after the boundary
        # (a velocity change at a knot): a step is a jump at every such point
        ka = float(ks[i - 1])
        d0 = (tl[0][0] + tl[0][1] * ka) - (tr[0][0] + tr[0][1] * ka)
        d1 = (tl[0][0] + tl[0][1] * b) - (tr[0][0] + tr[0][1] * b)
        dpos = closest(d0[:2], d1[:2])
        dsc = math.expm1(closest(d0[2:], d1[2:]))
        if dpos > float(cfg.punch_pos_step) or dsc > float(cfg.punch_scale_step):
            flagged.append((int(b), dpos + 400.0 * dsc))
    out: list[int] = []
    for b, mag in flagged:
        near = [m for bb, m in flagged if abs(bb - b) < side]
        if mag >= max(near) and not any(abs(b - o) < side for o in out):
            out.append(b)
    return out


def _snap_slopes(u1: float, cfg) -> list[float]:
    """Candidate time-line slopes (RAW frames per competitor frame): the snap speeds and the freeze."""
    return sorted({round(float(u1) * float(v), 9) for v in cfg.speed_snap_values} | {0.0})


def _robust_line(ks: np.ndarray, js: np.ndarray, slopes: Sequence[float], u1: float, tol: float,
                 min_frac: float, alternatives: bool = False):
    """Time line j(k) = floor(x + u k) through hint points (k, j) (FX-03 step 3): for every snap slope u the
    offset x = median(j - u k) + 0.5 (centre of the floor cell), integer residuals r = j - floor(x + u k),
    inliers |r| <= tol. The slope with the most inliers wins; among those within max(1, 5 %) of the most, the
    one with the smallest mean |r|; the 1.0-speed slope u1 is kept unless another is better by > 0.25 frame
    (the dominant speed of an edit, as snap_speed). None when no slope explains ``min_frac`` of the points.
    Returns (u, x, inlier mask); ``alternatives``: every slope of that near-best set as [(u, x, inlier mask)],
    the chosen one first (the competitor's repeat cadence may decide between them, ``_refit``)."""
    ks = np.asarray(ks, np.float64)
    js = np.asarray(js, np.float64)
    if len(ks) == 0:
        return None
    res = []
    for u in slopes:
        x = float(np.median(js - u * ks)) + 0.5
        r = js - np.floor(x + u * ks + 1e-9)
        inl = np.abs(r) <= tol
        if inl.any():
            x2 = float(np.median(js[inl] - u * ks[inl])) + 0.5
            r = js - np.floor(x2 + u * ks + 1e-9)
            inl = np.abs(r) <= tol
            x = x2
        res.append((int(inl.sum()), float(np.mean(np.abs(r[inl]))) if inl.any() else np.inf, u, x, inl))
    nmax = max(r[0] for r in res)
    if nmax < min_frac * len(ks):
        return None
    good = [r for r in res if r[0] >= nmax - max(1, int(0.05 * len(ks)))]
    best = min(good, key=lambda r: (r[1], abs(r[2] - u1)))
    one = [r for r in good if abs(r[2] - u1) < 1e-9]
    if one and one[0][1] <= best[1] + 0.25:
        best = one[0]
    if alternatives:
        rest = sorted((r for r in good if r is not best), key=lambda r: (r[1], abs(r[2] - u1)))
        return [(r[2], r[3], r[4]) for r in [best] + rest]
    return best[2], best[3], best[4]


def _line_frames(u: float, x: float, ks: np.ndarray) -> np.ndarray:
    return np.floor(x + u * np.asarray(ks, np.float64) + 1e-9).astype(np.int64)


def _line_cells(u: float, x_c: float, ks: np.ndarray, half: float = 1.5, max_cells: int = 48) -> list[float]:
    """Representatives (cell midpoints) of the floor-phase cells of x in [x_c - half, x_c + half] for the frames
    ``ks``: x values between two consecutive breakpoints n - u k give the same floor pattern on every frame.
    Cells narrower than 1e-6 frame are merged into their neighbour. A long track at a near-rational speed has
    thousands of slivers that differ on single frames (the per-frame +-1 alternatives under the chosen path
    settle those): more than ``max_cells`` cells -> the cells containing an even grid of ``max_cells`` x values."""
    lo, hi = x_c - half, x_c + half
    br = {lo, hi}
    for k in np.asarray(ks, np.float64):
        base = u * k
        for n in range(int(math.floor(lo + base)) - 1, int(math.ceil(hi + base)) + 2):
            b = n - base
            if lo < b < hi:
                br.add(round(b, 9))
    br = sorted(br)
    cells = []
    for a, b in zip(br[:-1], br[1:]):
        if b - a > 1e-6:
            cells.append(0.5 * (a + b))
    if len(cells) > max_cells:
        edges = np.array(br)
        grid = lo + (np.arange(max_cells) + 0.5) * (2.0 * half / max_cells)
        pick = sorted({int(np.searchsorted(edges, g, side="right")) - 1 for g in grid})
        cells = [0.5 * (edges[i] + edges[i + 1]) for i in pick if 0 <= i < len(edges) - 1 and edges[i + 1] - edges[i] > 1e-6]
    return cells


def _label_disagreements(js_line: dict[int, int], labels: dict[int, int]) -> tuple[int, int]:
    """(disagreements, labelled pairs) of a time line with the competitor's own repeat / move labels (FX-07): a
    REPEAT pair (k, k+1) must show one RAW frame (j(k+1) == j(k)), a MOVE pair two. UNKNOWN / CUT pairs and
    pairs outside the line say nothing."""
    dis = n = 0
    for k, lab in labels.items():
        if lab not in (_LAB_REPEAT, _LAB_MOVE) or k not in js_line or k + 1 not in js_line:
            continue
        n += 1
        same = js_line[k + 1] == js_line[k]
        if same != (lab == _LAB_REPEAT):
            dis += 1
    return dis, n


_LAB_UNKNOWN, _LAB_REPEAT, _LAB_MOVE, _LAB_CUT = 0, 1, 2, 3


def _label_speed_choice(K: np.ndarray, alts: Sequence[tuple], labels: dict[int, int]) -> tuple[int, list[int]]:
    """(index into ``alts``, label disagreements per alternative): the snap slope whose best floor-phase cell
    over the frames K disagrees with the fewest REPEAT / MOVE labels; ties keep the earlier (residual) order.
    No REPEAT / MOVE label -> (0, [])."""
    if len(alts) < 2 or not any(v in (_LAB_REPEAT, _LAB_MOVE) for v in labels.values()):
        return 0, []
    dis = [min(_label_disagreements(dict(zip(K.tolist(), _line_frames(u, c, K).tolist())), labels)[0]
               for c in _line_cells(u, x, K)) for u, x, *_ in alts]
    return int(min(range(len(alts)), key=lambda i: (dis[i], i))), dis


def _is_near(a: Anchor) -> bool:
    """A RANSAC near-miss (visual_match.search_frame near_miss): may only join an existing track's time line."""
    return str(getattr(a, "source", "")).endswith("_near")


def _is_gray(a: Anchor) -> bool:
    """A full RANSAC match whose ZNCC lies in the gray zone (visual_match.search_frame, FX-08): seeds a WEAK track
    whose scores are UNRESOLVED evidence, never an anchor."""
    return str(getattr(a, "source", "")).endswith("_gray")


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs [a, b) of True in a bool array."""
    out, a = [], None
    for k, v in enumerate(np.asarray(mask, bool).tolist() + [False]):
        if v and a is None:
            a = k
        elif not v and a is not None:
            out.append((a, k))
            a = None
    return out


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
    line: tuple[float, float] | None = None    # (u, x): the track's time line j(k) = floor(x + u k), or None
    meas: dict[int, tuple[int, Sim, float, bool]] = field(default_factory=dict)   # k -> (j, Sim, z, converged)
    fitted: bool = False           # the time-line-first framing fit ran at least once
    weak: bool = False             # seeded by gray-zone matches only (FX-08): UNRESOLVED evidence, no pass-2 masks

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
# Worker functions (run in fork or spawn pools, visual_match.parallel_map: module-level, picklable;
# state = dict of shared read-only objects)
# ---------------------------------------------------------------------------------------------

def _w_eval(state: dict, task: tuple) -> tuple:
    """Score frame k under one track model: window jhat±R, widened on the edge up to Rmax."""
    k, keys, flip, jhats, R, Rmax = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    n = raw.n
    sim = _sim_at(keys, k, state["raw_wh"], state.get("cap", 0))
    sc = _Scorer(np.asarray(comp.get(k)), state["roi"], state["allowed"](k), raw, comp.ratio, cfg)
    S: dict[int, float] = {}

    def ev(a: int, b: int) -> None:
        js = [j for j in range(max(0, a), min(n - 1, b) + 1) if j not in S]
        if js:
            for j, v in zip(js, sc.scores(js, sim, flip)):
                S[j] = float(v)

    if isinstance(jhats, (int, np.integer)):
        jhats = (int(jhats),)
    jhats = [int(min(max(int(j), 0), n - 1)) for j in jhats]
    # the primary hint (track prediction) and the local-continuity hints: score a window around each,
    # then continue from the best of them
    for jh in jhats:
        ev(jh - R, jh + R)
    jhat = max(jhats, key=lambda jh: max(S.get(j, -np.inf) for j in range(max(0, jh - R), min(n - 1, jh + R) + 1)))
    lo, hi = max(0, jhat - R), min(n - 1, jhat + R)
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
    # FX-08: a best score below none_thresh says the window missed the frame (a jump on the line), wherever the argmax
    # lies -- the whole +-Rmax window is scored before the frame may count as NOT-IN-RAW evidence
    if not (S.get(jb, -np.inf) >= float(cfg.none_thresh)) and (jhat - lo < Rmax or hi - jhat < Rmax):
        nlo, nhi = max(0, jhat - Rmax), min(n - 1, jhat + Rmax)
        ev(nlo, nhi)
        lo, hi = min(lo, nlo), max(hi, nhi)
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


def _w_final(state: dict, task: tuple) -> tuple:
    """Candidate vector around m (>= m±R, extended up to ±CAND_W//2 while the edge score is within ``cap``
    of the peak, so the soft range is never cut by the window) and the visually-identical RAW range
    [raw_lo, raw_hi] (RAW-vs-RAW, warped & masked)."""
    k, keys, flip, jb, lo, scores, R, cap = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    n = raw.n
    sim = _sim_at(keys, k, state["raw_wh"], state.get("cap", 0))
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
    # visually identical neighbours (FX-08: contrast-relative, a max over tiles, on the layout's mask only -- never
    # on pass-2 residual masks, which hide exactly the pixels where a wrong match differs)
    W = float(raw.full_size[0])
    rr, cr = tuple(raw.ratio), tuple(comp.ratio)
    roi = state["roi"]
    wb, vb = scoring.warp_to_roi(np.asarray(raw.get(jb)), sim, flip, W, rr, cr, roi)
    x0, y0, w, h = roi
    lay = state["allowed"].layout_only(k) if hasattr(state["allowed"], "layout_only") else allowed
    am = lay[y0:y0 + h, x0:x0 + w] & vb
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
            same, _ev = scoring.identical_images(wb, wj, m, cfg.identical_mad, cfg.identical_thresh,
                                                 int(cfg.identical_tiles), float(cfg.identical_contrast_ref),
                                                 float(cfg.identical_contrast_min))
            if same:
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
    sim = _sim_at(keys, k, state["raw_wh"], state.get("cap", 0))
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


def _w_measure(state: dict, task: tuple) -> tuple[dict, float, bool]:
    """Per-frame framing measurement of RAW j on comp frame k (:func:`ecc_measure`, coarse-to-fine, extra
    starts = e.g. the nearest anchors' Sims)."""
    k, j, flip, sim_d, starts, phase = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    r = ecc_measure(np.asarray(comp.get(k)), np.asarray(raw.get(j)), Sim.from_dict(sim_d), flip,
                    float(raw.full_size[0]), tuple(raw.ratio), tuple(comp.ratio), state["allowed"](k), cfg,
                    roi=state["roi"], starts=[Sim.from_dict(d) for d in starts], phase=phase)
    return r.sim.to_dict(), float(r.z), bool(r.converged)


def _w_pairs(state: dict, task: tuple) -> list[list[float]]:
    """Scores of comp frame k against RAW frames under given Sims: items [(js, sim_dict)] -> [[S(j) for j in js]]
    (one comp ROI preparation for all of them)."""
    k, flip, items = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    sc = _Scorer(np.asarray(comp.get(k)), state["roi"], state["allowed"](k), raw, comp.ratio, cfg)
    return [[float(v) for v in sc.scores(list(js), Sim.from_dict(sd), flip)] for js, sd in items]


def _w_temporal(state: dict, task: tuple) -> list[tuple[int, int, tuple]]:
    """Competitor-only pair measurements (temporal.align_pair) for pairs (k, k+g), k in [k0, k1), g in (1, 2):
    the comp proxy's box ROI with refine's allowed mask (layout + overlays), downscaled to temporal_max_side."""
    from . import temporal
    k0, k1 = task
    comp, cfg = state["comp"], state["cfg"]
    x, y, w, h = state["roi"]
    max_side = int(getattr(cfg, "temporal_max_side", 200))
    blur = float(cfg.score_blur) * temporal.scale_of((h, w), max_side)
    n = int(comp.n)

    def get(k: int):
        if not (0 <= k < n):
            return None
        img = np.asarray(comp.get(int(k)))[y:y + h, x:x + w]
        return temporal.prepare(img, np.asarray(state["allowed"](int(k)))[y:y + h, x:x + w], max_side, blur)
    pairs = [(k, g) for k in range(k0, k1) for g in (1, 2) if k + g < n]
    sig = temporal.measure(get, [], cfg, pairs=pairs)
    out = []
    for g, dd in ((1, sig.d1), (2, sig.d2)):
        for k, pm in sorted(dd.items()):
            out.append((int(k), g, (pm.cc, pm.mad, pm.dx, pm.dy, pm.ds, pm.dtheta, pm.aligned)))
    return out


def _w_detail(state: dict, task: tuple) -> dict:
    """Detail-score promotion test of an UNRESOLVED frame (FX-08): RAW jb under the hypothesis' framing and its
    neighbours jb +- 1, +- 2 each under its OWN re-measured framing (ecc_measure from the hypothesis), scored by
    scoring.detail_score (blur-matched gradient ZNCC). Equal explanations under their own framing (a camera pan over
    a static world) leave no margin: the frame stays unresolved."""
    k, keys, flip, jb = task
    comp, raw, cfg = state["comp"], state["raw"], state["cfg"]
    sim = _sim_at(keys, k, state["raw_wh"], state.get("cap", 0))
    roi = state["roi"]
    x0, y0, w, h = roi
    img = np.asarray(comp.get(k))
    allowed = state["allowed"](k)
    c = img[y0:y0 + h, x0:x0 + w].astype(np.float32)
    W, rr, cr = float(raw.full_size[0]), tuple(raw.ratio), tuple(comp.ratio)
    kern = scoring.blur_kernels()
    out = {"k": int(k), "jb": int(jb), "scores": {}}
    for d in (0, -1, 1, -2, 2):
        j = jb + d
        if not raw.has(j):
            continue
        s = sim
        if d != 0:
            r = ecc_measure(img, np.asarray(raw.get(j)), sim, flip, W, rr, cr, allowed, cfg, roi=roi, phase=False)
            s = r.sim
        wr, vr = scoring.warp_to_roi(np.asarray(raw.get(j)), s, flip, W, rr, cr, roi)
        m = allowed[y0:y0 + h, x0:x0 + w] & vr
        det, z, kname = scoring.detail_score(c, wr, m, kern)
        out["scores"][d] = (det, z, kname)
    return out


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
        valid = [a for a in anchors if 0 <= a.k < comp.n and 0 <= a.raw < raw.n]
        self.anchors = [a for a in valid if not _is_near(a) and not _is_gray(a)]
        self.near = [a for a in valid if _is_near(a)]        # RANSAC near-misses: join-only (FX-03 step 3)
        self.gray = [a for a in valid if _is_gray(a)]        # gray-zone matches: weak tracks only (FX-08)
        self.line_done: set[tuple[int, bool]] = set()        # (k, flip) already line-searched
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
        self.searched: set[int] = set(a.k for a in valid)
        self.pass2_done: set[int] = set()
        self.stats: dict[str, Any] = {"eval_tasks": 0, "ecc_tasks": 0, "rescue_searches": 0, "score_tasks": 0}
        self.slopes = _snap_slopes(self.u1, cfg)
        self.cap = max(0, int(cfg.framing_sample_step))       # model extrapolation beyond the last key (frames)
        self.meas_cache: dict[tuple[int, int, bool], tuple[dict, float, bool]] = {}
        self.labels: dict[int, int] = {}                      # competitor pair (k, k+1) -> _LAB_*
        from .temporal import Signature
        self._sig = Signature()                               # comp-only pair measurements (lazy, _ensure_labels)
        self._sig_done: set[int] = set()
        self._lab = None
        self.repeat_max_slope = float(getattr(cfg, "temporal_refine_max_slope", 0.95))
        self.warps: dict[int, tuple[float, float, float, float]] = {}   # pair (k, k+1) -> editor move (comp full-res)

    # -- plumbing -------------------------------------------------------------------------------
    def _state(self) -> dict:
        return {"comp": self.comp, "raw": self.raw, "cfg": self.cfg, "allowed": self.allowed, "roi": self.roi,
                "raw_wh": self.raw_wh, "base_allowed": self.allowed.base, "cap": self.cap}

    def _map(self, fn, tasks: list) -> list:
        return parallel_map(fn, tasks, self.workers, self._state(), self.cfg.seed)

    def _new_track(self, flip: bool, anchors: list[Anchor]) -> _Track:
        t = _Track(self._next_id, bool(flip), sorted(anchors, key=lambda a: a.k))
        self._next_id += 1
        self.tracks[t.id] = t
        return t

    # -- 1. time-line-first linking (FX-03 step 3) -----------------------------------------------
    def _run_line(self, anchors: Sequence[Anchor]) -> tuple[float, float] | None:
        """(u, x) of anchors on >= 2 comp frames: their robust snap-slope time line (no inlier floor)."""
        ks = np.array([a.k for a in anchors], np.float64)
        if len(np.unique(ks)) < 2:
            return None
        r = _robust_line(ks, np.array([a.raw for a in anchors], np.float64), self.slopes, self.u1,
                         float(self.cfg.line_time_tol), 0.0)
        return None if r is None else (r[0], r[1])

    def _time_cost(self, anchors: list[Anchor], a: Anchor) -> float | None:
        """How well anchor a continues a run's RAW time line (|integer residual| <= line_time_tol), never its
        framing: an editor pan / zoom of any speed links (FX-03 step 3; a framing STEP splits the track after the
        per-frame measurement, ``_steps``). One comp frame so far: the best snap slope through both anchors, the
        1.0 speed preferred."""
        last = anchors[-1]
        dk = a.k - last.k
        if dk <= 0 or dk > self.max_gap:
            return None
        tol = float(self.cfg.line_time_tol)
        line = self._run_line(anchors)
        if line is None:
            best = None
            for u in self.slopes:
                r = abs(a.raw - (last.raw + u * dk))
                if r <= tol:
                    c = r + (0.0 if abs(u - self.u1) < 1e-9 else 0.5)
                    best = c if best is None else min(best, c)
            return best
        u, x = line
        r = abs(a.raw - math.floor(x + u * a.k + 1e-9))
        return float(r) if r <= tol else None

    def _repeat_velocity(self, k0: int, k1: int) -> tuple[float, float] | None:
        """Median editor framing velocity (comp full-res px / frame) over the competitor's REPEAT pairs in
        [k0, k1): at a pulldown repeat the RAW frame is the same, so the pair's warp IS the editor's crop move
        (FX-07 (d)), independent of the RAW's own motion. None without repeat pairs."""
        v = [self.warps[k][:2] for k in range(max(0, k0), min(self.N - 1, k1))
             if self.labels.get(k) == _LAB_REPEAT and k in self.warps]
        if not v:
            return None
        m = np.median(np.array(v, np.float64), axis=0)
        return float(m[0]), float(m[1])

    def _initial_keys(self, t: _Track) -> list[dict]:
        """Track model before the first per-frame measurement: robust fit over the anchors' Sims; a run with
        anchors on ONE comp frame gets the competitor's repeat-pair velocity as its slope (else constant)."""
        smp = [(a.k, a.sim) for a in t.anchors]
        if len({a.k for a in t.anchors}) == 1:
            a = t.anchors[0]
            vel = self._repeat_velocity(a.k - self.ext, a.k + self.ext + 1)
            if vel is not None and math.hypot(*vel) > 0.1:
                smp.append((a.k + self.stride, a.sim.translated(vel[0] * self.stride, vel[1] * self.stride)))
        return fit_track_model(smp, self.center, self.cfg)

    def _link(self, anchors: Iterable[Anchor]) -> list[_Track]:
        """Group anchors into runs by RAW time line first (same flip, gaps <= max_gap, one anchor per comp frame,
        lowest time residual), one track per run."""
        runs: list[list[Anchor]] = []
        for a in sorted(anchors, key=lambda a: (a.k, -a.zncc, a.raw, a.flip)):
            best, cost = None, math.inf
            for r in runs:
                if r[0].flip != a.flip or any(b.k == a.k for b in r):
                    continue
                c = self._time_cost(r, a)
                if c is not None and c < cost:
                    best, cost = r, c
            if best is not None:
                best.append(a)
            else:
                runs.append([a])
        made = [self._new_track(r[0].flip, r) for r in runs]
        if self.u1 <= self.repeat_max_slope:
            self._ensure_labels([(t.anchors[0].k - self.ext, t.anchors[0].k + self.ext) for t in made
                                 if len({a.k for a in t.anchors}) == 1])
        for t in made:
            t.line = self._run_line(t.anchors)
            t.keys = self._initial_keys(t)
            ks = [a.k for a in t.anchors]
            t.span = [max(0, min(ks) - self.ext), min(self.N - 1, max(ks) + self.ext)]
            self._support_from_anchors(t)
            self.dlog.record("refine", "track", track=t.id, flip=t.flip, anchors=[(a.k, a.raw) for a in t.anchors],
                             time_ambiguous=[a.k for a in t.anchors if getattr(a, "time_ambiguous", False)],
                             line=None if t.line is None else [round(t.line[0], 6), round(t.line[1], 4)],
                             span=list(t.span), keys=t.keys)
        return made

    def _support_from_anchors(self, t: _Track) -> None:
        d: dict[int, int] = {}
        for a in t.anchors:
            d.setdefault(a.k, a.raw)
        if not d:                       # a piece split off at a framing step without its own anchor
            d = {k: m[0] for k, m in t.meas.items()}
            if not d:
                return
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
        thr = self.cfg.match_thresh
        for k, t in sorted(pairs, key=lambda p: (p[0], p[1].id)):
            jhats = [int(round(t.predict(k, self.u1)))]
            # local continuity hints: a same-shot jump cut breaks the track's time line, the neighbours
            # already assigned to this track predict the next frame far better than the global support
            for nb, sgn in ((k - 1, 1), (k + 1, -1)):
                if 0 <= nb < self.N and int(self.win_tid[nb]) == t.id and self.win_s[nb] >= thr:
                    jh = int(round(float(self.win_j[nb]) + sgn * self.u1))
                    if jh not in jhats:
                        jhats.append(jh)
            tasks.append((int(k), t.keys, t.flip, tuple(jhats), R, Rmax))
            keys.append((int(k), t.id))
        self.stats["eval_tasks"] += len(tasks)
        for (k, tid), (lo, arr, jb, sb, edge, wid) in zip(keys, self._map(_w_eval, tasks)):
            self.hyp[k][tid] = _Hyp(lo, arr, jb, sb, edge, wid)

    def _assign(self, frames: Iterable[int] | None = None) -> None:
        """Best (track, RAW frame) per frame. Tracks that show the SAME RAW frame within the duplicate tolerance
        (2e-3, as _merge_duplicates) explain the frame equally: the one with the larger support keeps it, so a
        one-frame track whose framing is that frame's own measurement cannot take a frame from a run by noise."""
        rng = range(self.N) if frames is None else sorted(set(frames))
        for k in rng:
            best_tid, best_s, best_j = -1, -np.inf, -1
            for tid in sorted(self.hyp[k]):
                h = self.hyp[k][tid]
                if h.jb >= 0 and np.isfinite(h.sb) and h.sb > best_s:
                    best_tid, best_s, best_j = tid, h.sb, h.jb
            if best_tid >= 0:
                fl = self.tracks[best_tid].flip if best_tid in self.tracks else None

                def size(tid: int) -> int:
                    t = self.tracks.get(tid)
                    return 0 if t is None else len(t.sup_k) + len(t.anchors)
                ties = [tid for tid, h in self.hyp[k].items() if tid in self.tracks and h.jb == best_j
                        and np.isfinite(h.sb) and h.sb >= best_s - 2e-3 and self.tracks[tid].flip == fl]
                if len(ties) > 1:
                    best_tid = max(sorted(ties), key=size)
                    best_s = float(self.hyp[k][best_tid].sb)
            self.win_tid[k] = best_tid
            self.win_j[k] = best_j
            self.win_s[k] = best_s if best_tid >= 0 else np.nan

    def _span_pairs(self, tracks: Iterable[_Track]) -> list[tuple[int, _Track]]:
        return [(k, t) for t in tracks for k in range(t.span[0], t.span[1] + 1)]

    def _grow(self) -> None:
        """Tracks grow frame-wise beyond their frames while they keep winning: the next ``ext`` frames are first
        MEASURED on the track's time line (ECC from the path extrapolated over the step, ``_extend_paths``), so a
        moving framing is followed rather than held, then scored under the extended path."""
        thr = self.cfg.match_thresh
        for _ in range(10 * self.N):
            pairs: list[tuple[int, _Track]] = []
            grow: dict[int, list[int]] = {}
            for t in sorted(self.tracks.values(), key=lambda t: t.id):
                lo, hi = t.span
                if hi < self.N - 1 and self.win_tid[hi] == t.id and self.win_s[hi] >= thr:
                    nh = min(self.N - 1, hi + self.ext)
                    new = [k for k in range(hi + 1, nh + 1) if t.id not in self.hyp[k]]
                    pairs += [(k, t) for k in new]
                    grow.setdefault(t.id, []).extend(new)
                    t.span[1] = nh
                if lo > 0 and self.win_tid[lo] == t.id and self.win_s[lo] >= thr:
                    nl = max(0, lo - self.ext)
                    new = [k for k in range(nl, lo) if t.id not in self.hyp[k]]
                    pairs += [(k, t) for k in new]
                    grow.setdefault(t.id, []).extend(new)
                    t.span[0] = nl
            if not pairs:
                return
            self._extend_paths(grow)
            for t in {p[1].id: p[1] for p in pairs}.values():
                self._update_support(t)
            self._evaluate(pairs)
            self._assign({k for k, _ in pairs})

    def _extend_paths(self, grow: dict[int, list[int]]) -> None:
        """Measure the frames a fitted track grows into on its time line (FX-03 step 5: init = the path
        extrapolated linearly over the growth step, i.e. previous frames + velocity; extra starts = the nearest
        anchors) and extend its path with the measurements that reach the track's own score level (>= max(
        match_thresh - anchor_zncc_slack, its median - max(0.05, 4 sigma_MAD))) and do not lie beyond a framing
        STEP (``_steps``: a reframe on the same time line is another track). Frames another track already
        explains (>= match_thresh) are not measured."""
        cfg = self.cfg
        thr = cfg.match_thresh
        items, plans = [], []
        for tid, ks in sorted(grow.items()):
            t = self.tracks.get(tid)
            if t is None or t.line is None or not t.meas:
                continue
            u, x = t.line
            jk = {}
            for k in sorted(set(ks)):
                other = int(self.win_tid[k])
                if k in t.meas or (other >= 0 and other != t.id and self.win_s[k] >= thr):
                    continue
                j = int(math.floor(x + u * k + 1e-9))
                if self.raw.has(j):
                    jk[k] = j
            if jk:
                items += [(t, k, j, _sim_at(t.keys, k, self.raw_wh, self.ext)) for k, j in jk.items()]
                plans.append((t, jk))
        if not items:
            return
        self._measure(items)
        for t, jk in plans:
            zs = np.array([m[2] for m in t.meas.values() if np.isfinite(m[2])], np.float64)
            if zs.size == 0:
                continue
            med = float(np.median(zs))
            floor = max(thr - float(cfg.anchor_zncc_slack),
                        med - max(0.05, 4.0 * 1.4826 * float(np.median(np.abs(zs - med)))))
            new = {}
            for k, j in jk.items():
                r = self.meas_cache.get((k, j, t.flip))
                if r is not None and np.isfinite(r[1]) and r[1] >= floor:
                    new[k] = (j, Sim.from_dict(r[0]), float(r[1]), bool(r[2]))
            if not new:
                continue
            old_ks = sorted(t.meas)
            merged = sorted([(k, m[1]) for k, m in t.meas.items()] + [(k, m[1]) for k, m in new.items()])
            for b in _steps(merged, self.center, cfg):
                if b > old_ks[-1]:
                    new = {k: m for k, m in new.items() if k < b}
                elif b <= old_ks[0]:
                    new = {k: m for k, m in new.items() if k >= b}
            if not new:
                continue
            t.meas.update(new)
            t.samples = sorted((k, m[1]) for k, m in t.meas.items())
            t.keys = fit_path(t.samples, self.center, cfg)
            self.dlog.record("refine", "grow_measure", track=t.id, frames=sorted(new), tried=len(jk))
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
            hb = self.hyp[k].pop(B.id, None)
            if hb is None or not (hb.jb >= 0 and np.isfinite(hb.sb)):
                continue
            # keep B's result where it is better: the tracks explain the same frames with the same
            # framing (duplicates / one chain), so B's hypothesis is valid for A until A is re-evaluated
            ha = self.hyp[k].get(A.id)
            if ha is None or not np.isfinite(ha.sb) or hb.sb > ha.sb:
                self.hyp[k][A.id] = hb
        self._assign(range(B.span[0], B.span[1] + 1))
        if not evaluate:
            return
        self._update_support(A)
        need = [(k, A) for k in range(A.span[0], A.span[1] + 1) if A.id not in self.hyp[k]]
        self._evaluate(need)
        self._assign({k for k, _ in need})

    # -- 2. time-line-first framing fit (FX-03 steps 3-6) -----------------------------------------
    def _fit_frames(self, t: _Track) -> list[int]:
        """Frames a track's framing is measured on: the frames it wins, plus every frame between its first and
        last anchor that no other track explains (score >= match_thresh) -- in a fast pan the anchors' model
        need not reach match_thresh between anchors, yet those frames belong to the run."""
        thr = self.cfg.match_thresh
        ks = {int(k) for k in np.flatnonzero(self.win_tid == t.id)}
        if t.anchors:
            a0, a1 = min(a.k for a in t.anchors), max(a.k for a in t.anchors)
            for k in range(a0, a1 + 1):
                tid = int(self.win_tid[k])
                if tid == t.id or tid < 0 or not (self.win_s[k] >= thr):
                    ks.add(k)
            ks |= {int(a.k) for a in t.anchors}
        return sorted(ks)

    def _hints(self, t: _Track, K: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Time evidence of a track: its anchors (k, raw) and the frames it wins at >= match_thresh (argmax)."""
        thr = self.cfg.match_thresh
        d = {int(a.k): int(a.raw) for a in sorted(t.anchors, key=lambda a: (a.k, -a.zncc))[::-1]}
        for k in K:
            k = int(k)
            if k not in d and int(self.win_tid[k]) == t.id and self.win_s[k] >= thr:
                d[k] = int(self.win_j[k])
        ks = sorted(d)
        return np.array(ks, np.float64), np.array([d[k] for k in ks], np.float64)

    def _anchor_starts(self, t: _Track, k: int, init: Sim) -> list[dict]:
        """Extra ECC starts at frame k: the Sims of the track's nearest anchor on each side (<= max_gap away)
        that differ from the init (a framing step between the anchors leaves the interpolated init far off)."""
        out = []
        left = [a for a in t.anchors if k - self.max_gap <= a.k <= k]
        right = [a for a in t.anchors if k < a.k <= k + self.max_gap]
        for grp, pick in ((left, max), (right, min)):
            if grp:
                a = pick(grp, key=lambda a: (a.k, a.zncc))
                ds, dp = _sim_delta(a.sim, init, self.center)
                if ds > 2e-3 or dp > 1.0:
                    out.append(a.sim.to_dict())
        return out

    def _measure(self, items: list[tuple[_Track, int, int, Sim]]) -> None:
        """ECC measurements (k, j, flip) not yet in the cache, in one worker batch (``_w_measure``)."""
        tasks, keys, seen = [], [], set()
        for t, k, j, init in items:
            key = (int(k), int(j), bool(t.flip))
            if key in self.meas_cache or key in seen or not self.raw.has(int(j)):
                continue
            seen.add(key)
            # a fitted track's path is a close init: no phase-correlation start needed
            tasks.append((int(k), int(j), bool(t.flip), init.to_dict(), self._anchor_starts(t, int(k), init),
                          not t.fitted))
            keys.append(key)
        if not tasks:
            return
        self.stats["ecc_tasks"] += len(tasks)
        for key, r in zip(keys, self._map(_w_measure, tasks)):
            self.meas_cache[key] = r

    def _good(self, flip: bool, jd: dict[int, int]) -> list[tuple[int, int, Sim, float, bool]]:
        """Measurements of (k, jd[k]) that count for a path: masked ZNCC >= max(none_thresh, the family's median
        - max(0.05, 4 sigma_MAD)) (a failed ECC or a frame of another shot never bends the path)."""
        rows = [(k, j, self.meas_cache[(k, j, flip)]) for k, j in sorted(jd.items()) if (k, j, flip) in self.meas_cache]
        zs = np.array([r[1] for _, _, r in rows if np.isfinite(r[1])], np.float64)
        if zs.size == 0:
            return []
        med = float(np.median(zs))
        floor = max(float(self.cfg.none_thresh), med - max(0.05, 4.0 * 1.4826 * float(np.median(np.abs(zs - med)))))
        return [(k, j, Sim.from_dict(r[0]), float(r[1]), bool(r[2])) for k, j, r in rows
                if np.isfinite(r[1]) and r[1] >= floor]

    def _refit(self, tracks: list[_Track]) -> list[_Track]:
        """Time-line-first framing fit of each track (FX-03 steps 3-6):

        1. frames (``_fit_frames``) and their time line: the robust snap-slope line through the anchors and the
           frames the track wins (``_robust_line``); none (a ramp, a jump inside the track) -> the frames' own
           argmax is measured as it is.
        2. candidate lines = the floor-phase cells of x within +-1.5 frames, pruned by the competitor's own
           repeat / move labels (FX-07: repeats give speed and fractional phase, never the integer offset; soft --
           the cells with the fewest disagreements (+1) stay); the central cell c0 agrees best with the argmax.
        3. framing measured by ECC (coarse-to-fine, multi-start) at c0's RAW frame on EVERY frame and at c0 +- 1
           every framing_sample_step frames; ONE smooth path per family (``fit_path``).
        4. every cell is scored on every frame under the path of its family (its median offset from c0): the
           line with the highest summed score wins -- per-frame +-1 alternatives under the SAME path, so a time
           error cannot hide behind a compensating framing (a free per-candidate ECC never chooses time).
        5. the chosen line is measured where not yet and its path refitted; framing STEPS in the measurements
           split the track (``_steps``). Model = the path, support = the line, ``meas`` = the per-frame
           measurements (FrameMap sim_meas). Returns the tracks whose model changed and the new pieces."""
        cfg = self.cfg
        tol = float(cfg.line_time_tol)
        plans: list[dict] = []
        items: list[tuple[_Track, int, int, Sim]] = []
        pre = []
        for t in tracks:
            if t.id not in self.tracks:
                continue
            K = np.array(self._fit_frames(t), np.int64)
            if K.size == 0:
                continue
            hk, hj = self._hints(t, K)
            alts = (_robust_line(hk, hj, self.slopes, self.u1, tol, float(cfg.line_min_inlier_frac), True)
                    if len(np.unique(hk)) >= 2 else None)
            pre.append((t, K, hk, hj, alts))
        # the competitor's repeat cadence where a line can repeat RAW frames at all
        self._ensure_labels([(int(K[0]), int(K[-1])) for _, K, _, _, alts in pre
                             if alts and any(0.0 < a[0] <= self.repeat_max_slope for a in alts)])
        for t, K, hk, hj, alts in pre:
            line = None if not alts else self._label_speed(t, K, alts)
            init = {int(k): _sim_at(t.keys, int(k), self.raw_wh, self.max_gap) for k in K}
            plan: dict[str, Any] = {"t": t, "K": K, "line": None, "cells": [], "fam": {}, "init": init}
            if line is None:
                jh = [int(self.win_j[k]) if int(self.win_tid[k]) == t.id and self.win_j[k] >= 0
                      else int(round(t.predict(int(k), self.u1))) for k in K]
                plan["fam"][0] = dict(zip(K.tolist(), jh))
            else:
                u, xc, _ = line
                hint = dict(zip(hk.astype(int).tolist(), hj.astype(int).tolist()))
                lab = {k: self.labels[k] for k in range(int(K[0]), int(K[-1])) if k in self.labels}
                cells = []
                for c in _line_cells(u, xc, K):
                    jl = _line_frames(u, c, K)
                    jd = dict(zip(K.tolist(), jl.tolist()))
                    dis, nlab = _label_disagreements(jd, lab)
                    cells.append({"x": c, "j": jl, "agree": sum(1 for k, j in hint.items() if jd.get(k) == j),
                                  "dis": dis, "nlab": nlab})
                c0 = max(cells, key=lambda c: (c["agree"], -c["dis"], -abs(c["x"] - xc)))
                plan.update(line=(u, xc), cells=cells, c0=c0)
                j0 = c0["j"]
                plan["fam"][0] = dict(zip(K.tolist(), j0.tolist()))
                # +-1 families on the global framing_sample_step grid (shared with the confound check's samples)
                samp = sorted({i for i, k in enumerate(K.tolist()) if k % self.stride == 0} | {0, len(K) - 1})
                for d in (-1, 1):
                    plan["fam"][d] = {int(K[i]): int(j0[i] + d) for i in samp}
            for jd in plan["fam"].values():
                items += [(t, k, j, init[k]) for k, j in jd.items()]
            plans.append(plan)
        self._measure(items)
        # one smooth path per family; every candidate line scored under its family's path
        score_items, owners = [], []
        for plan in plans:
            t = plan["t"]
            plan["paths"] = {}
            for d, jd in plan["fam"].items():
                good = self._good(t.flip, jd)
                if good:
                    plan["paths"][d] = fit_path([(k, sm) for k, _, sm, _, _ in good], self.center, cfg)
            if plan["line"] is None or 0 not in plan["paths"]:
                continue
            fams = sorted(plan["paths"])
            j0 = plan["c0"]["j"]
            for i, k in enumerate(plan["K"]):
                js = list(range(int(j0[i]) - 2, int(j0[i]) + 3))
                score_items.append((int(k), t.flip, [(js, _sim_at(plan["paths"][d], int(k), self.raw_wh, self.cap))
                                                     for d in fams]))
                owners.append((plan, i))
        if score_items:
            self.stats["score_tasks"] += len(score_items)
            res = self._map(_w_pairs, [(k, fl, [(js, sm.to_dict()) for js, sm in its]) for k, fl, its in score_items])
            for (plan, i), sc in zip(owners, res):
                plan.setdefault("S", {})[i] = sc
        final_items = []
        for plan in plans:
            t = plan["t"]
            if "S" not in plan:
                plan["jstar"], plan["dstar"], plan["xstar"], plan["choice"] = plan["fam"][0], 0, None, None
                continue
            plan["choice"] = self._choose_line(plan)
            c, d = plan["choice"]["cell"], plan["choice"]["d"]
            plan["jstar"] = dict(zip(plan["K"].tolist(), c["j"].tolist()))
            plan["dstar"], plan["xstar"] = d, c["x"]
            path = plan["paths"].get(d, plan["paths"][0])
            final_items += [(t, k, j, _sim_at(path, k, self.raw_wh, self.cap)) for k, j in plan["jstar"].items()]
        self._measure(final_items)
        changed: list[_Track] = []
        for plan in plans:
            changed += self._apply_fit(plan)
        return changed

    def _label_speed(self, t: _Track, K: np.ndarray, alts: list) -> tuple:
        """The time line's speed when several snap slopes explain the time evidence alike (``_robust_line``
        alternatives): the competitor's repeat / move labels decide (FX-07: repeats give speed and fractional
        phase) -- the slope whose best floor-phase cell disagrees with the fewest labels, the residual order
        otherwise. Without labels (no repeat possible, or a static / blended shot) the residual choice stands."""
        lab = {k: self.labels[k] for k in range(int(K[0]), int(K[-1])) if k in self.labels}
        best, dis = _label_speed_choice(K, alts, lab)
        if best != 0:
            self.dlog.record("refine", "label_speed", track=t.id, slopes=[round(a[0], 6) for a in alts],
                             disagreements=dis, chosen=round(alts[best][0], 6))
        return alts[best]

    def _choose_line(self, plan: dict) -> dict:
        """The candidate line with the highest summed score under its family's path (see _refit), among the cells
        whose disagreements with the competitor's repeat / move labels are at most the minimum + 1. Ties: the
        central cell, fewer label disagreements, closer to the robust fit."""
        fams = sorted(plan["paths"])
        K, j0, xc = plan["K"], plan["c0"]["j"], plan["line"][1]
        ev = []
        for c in plan["cells"]:
            diff = c["j"] - j0
            d = int(np.round(np.median(diff)))
            if d not in plan["paths"] or int(np.max(np.abs(diff))) > 2:
                continue
            f = fams.index(d)
            vals = np.array([plan["S"][i][f][int(diff[i]) + 2] if i in plan["S"] else np.nan for i in range(len(K))])
            ev.append((c, d, vals))
        if not ev:
            return {"cell": plan["c0"], "d": 0, "total": None, "frames": 0, "families": {}, "cells": len(plan["cells"]),
                    "evaluated": 0, "kept": 0, "label_min_dis": None, "labelled_pairs": plan["c0"]["nlab"]}
        common = np.all(np.array([np.isfinite(v) for _, _, v in ev]), axis=0)
        min_dis = min(c["dis"] for c, _, _ in ev)
        keep = [e for e in ev if e[0]["dis"] <= min_dis + 1]
        tot = {id(e[0]): float(np.sum(e[2][common])) for e in ev}
        best = max(keep, key=lambda e: (round(tot[id(e[0])], 9), e[0] is plan["c0"], -e[0]["dis"],
                                        -abs(e[0]["x"] - xc)))
        fam_best: dict[int, float] = {}
        for c, d, _ in ev:
            fam_best[d] = max(fam_best.get(d, -np.inf), tot[id(c)])
        return {"cell": best[0], "d": best[1], "total": round(tot[id(best[0])], 5), "frames": int(common.sum()),
                "families": {str(d): round(v, 4) for d, v in sorted(fam_best.items())},
                "cells": len(plan["cells"]), "evaluated": len(ev), "kept": len(keep), "label_min_dis": min_dis,
                "labelled_pairs": best[0]["nlab"]}

    def _apply_fit(self, plan: dict) -> list[_Track]:
        """Store a track's fitted line + path; split it at framing steps. Returns the changed / new tracks."""
        cfg = self.cfg
        t = plan["t"]
        jstar = plan["jstar"]
        good = self._good(t.flip, jstar)
        if not good:
            self.dlog.record("refine", "line_fit", track=t.id, frames=[int(plan["K"][0]), int(plan["K"][-1])],
                             result="no usable measurement (model kept)")
            t.fitted = True
            return []
        steps = _steps([(k, sm) for k, _, sm, _, _ in good], self.center, cfg)
        bounds = [-10 ** 9] + steps + [10 ** 9]
        u_x = None if plan["line"] is None else (plan["line"][0], plan["xstar"])
        pieces = []
        for lo, hi in zip(bounds[:-1], bounds[1:]):
            pg = [g for g in good if lo <= g[0] < hi]
            if pg:
                pieces.append((lo, hi, pg))
        changed: list[_Track] = []
        old_span = list(t.span)
        for n, (lo, hi, pg) in enumerate(pieces):
            if n == 0:
                tt = t
            else:
                tt = self._new_track(t.flip, [a for a in t.anchors if lo <= a.k < hi])
                tt.span = [max(int(lo), old_span[0]), old_span[1]]
            if len(pieces) > 1:
                if n == 0:
                    t.anchors = [a for a in t.anchors if a.k < pieces[1][0]]
                    t.span = [old_span[0], max(old_span[0], min(old_span[1], int(pieces[1][0]) - 1))]
                elif n + 1 < len(pieces):
                    tt.span[1] = max(tt.span[0], min(old_span[1], int(pieces[n + 1][0]) - 1))
            old_keys = list(tt.keys)
            tt.keys = fit_path([(k, sm) for k, _, sm, _, _ in pg], self.center, cfg)
            tt.line = u_x
            Kp = [k for k in plan["K"].tolist() if lo <= k < hi]
            tt.meas = {k: (j, sm, z, cv) for k, j, sm, z, cv in pg}
            tt.samples = [(k, sm) for k, _, sm, _, _ in pg]
            tt.sup_k = np.array(Kp, np.int64)
            tt.sup_j = np.array([jstar[k] for k in Kp], np.float64)
            tt.keep_support = True
            tt.fitted = True
            if n > 0 or not old_keys or len(pieces) > 1:
                changed.append(tt)
            else:
                dmax_s = dmax_p = 0.0
                for k in Kp[:: max(1, len(Kp) // 16)] + Kp[-1:]:
                    ds, dp = _sim_delta(_sim_at(tt.keys, k, self.raw_wh, self.cap),
                                        _sim_at(old_keys, k, self.raw_wh, self.cap), self.center)
                    dmax_s, dmax_p = max(dmax_s, ds), max(dmax_p, dp)
                if dmax_s > 5e-4 or dmax_p > 0.25:
                    changed.append(tt)
            ch = plan.get("choice")
            self.dlog.record("refine", "line_fit", track=tt.id,
                             frames=[int(Kp[0]), int(Kp[-1])] if Kp else None, n=len(Kp), measured=len(pg),
                             line=None if u_x is None else [round(u_x[0], 6), round(float(u_x[1]), 4)],
                             family=plan["dstar"], choice=None if ch is None else
                             {k: v for k, v in ch.items() if k != "cell"},
                             keys=tt.keys, steps=steps if n == 0 else None, piece_of=t.id if n > 0 else None)
        if len(pieces) > 1:
            # the first piece no longer covers the later pieces' frames: drop its stale hypotheses there
            cut = int(pieces[1][0])
            frames = [k for k in range(cut, old_span[1] + 1) if t.id in self.hyp[k]]
            for k in frames:
                self.hyp[k].pop(t.id, None)
            self._assign(frames)
            self.dlog.record("refine", "framing_step_split", track=t.id, steps=steps,
                             pieces=[c.id for c in changed])
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
        # a weak (gray-zone) track's residuals would mask exactly where its unverified hypothesis differs (FX-08)
        eligible = [k for k in range(self.N) if self.win_tid[k] >= 0 and np.isfinite(self.win_s[k])
                    and self.win_s[k] >= self.cfg.none_thresh and not self.tracks[int(self.win_tid[k])].weak]
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
        for k in changed:            # framing measured before the mask changed: measure again when needed
            for key in [key for key in self.meas_cache if key[0] == k]:
                del self.meas_cache[key]
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
                    # every stride-th frame between the sparse search's grid (k % stride == 0, already searched
                    # with the same masks unless pass 2 masked them) plus both ends of the run
                    off = (self.stride // 2 + 1) % self.stride
                    sel = sorted({run[0], run[-1]} | {k for k in run if k % self.stride == off})
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
        near: list[Anchor] = []
        for k, anchors, rep in found:
            confirms = []
            for a in anchors:
                if _is_gray(a):
                    a.source = "rescue_gray"
                    self.gray.append(a)
                    continue
                if _is_near(a):
                    a.source = "rescue_near"
                    near.append(a)
                    continue
                a.source = "rescue"
                tid = int(self.win_tid[k])
                if tid >= 0 and self.tracks[tid].flip == a.flip and abs(int(self.win_j[k]) - a.raw) <= 1:
                    ds, dp = _sim_delta(a.sim, _sim_at(self.tracks[tid].keys, k, self.raw_wh, self.cap), self.center)
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
        joined, rest = self._join(new + near)
        rest = [a for a in rest if not _is_near(a)]
        if not joined and not rest:
            return False
        made = self._link(rest)
        self._converge(joined + made)
        return True

    # -- 5b. search before giving up (FX-08) ------------------------------------------------------------------
    def _gap_runs(self) -> list[tuple[int, int, list[tuple[_Track, int]]]]:
        """Runs [a, b) of frames no track explains (score < match_thresh, not uniform) with the tracks winning the
        frame just before (side -1) / just after (side +1) them: the neighbours' time lines."""
        thr = self.cfg.match_thresh
        good = (self.win_tid >= 0) & (self.win_s >= thr)
        out = []
        for a, b in _runs(~good & ~self.uniform):
            nbs = []
            for k, side in ((a - 1, -1), (b, 1)):
                if 0 <= k < self.N and good[k]:
                    t = self.tracks.get(int(self.win_tid[k]))
                    if t is not None and not t.weak:
                        nbs.append((t, side))
            out.append((a, b, nbs))
        return out

    def _line_at(self, t: _Track, k: int) -> int:
        """RAW frame a track's time line predicts at comp frame k (its fitted line, else its support trend)."""
        if t.line is not None:
            return int(math.floor(t.line[1] + t.line[0] * k + 1e-9))
        return int(round(t.predict(k, self.u1)))

    def _propagate(self) -> list[int]:
        """The previous and next runs' time lines scored across short gaps (<= line_gap_s, FX-08): every gap
        frame gets the neighbours' hypotheses (window around the line, widened to track_search_radius when it scores
        below none_thresh), so a frame is NOT-IN-RAW evidence only after its neighbours' lines were tried."""
        gmax = int(math.ceil(float(getattr(self.cfg, "line_gap_s", 1.0)) * float(self.comp.fps)))
        pairs: list[tuple[int, _Track]] = []
        for a, b, nbs in self._gap_runs():
            if b - a > gmax:
                continue
            for t, _side in nbs:
                new = [k for k in range(a, b) if t.id not in self.hyp[k]]
                if new:
                    t.span = [min(t.span[0], a), max(t.span[1], b - 1)]
                    pairs += [(k, t) for k in new]
        if not pairs:
            return []
        self._evaluate(pairs)
        frames = sorted({k for k, _ in pairs})
        self._assign(frames)
        self.dlog.record("refine", "gap_lines", frames=frames[:500], tracks=sorted({t.id for _, t in pairs}))
        return frames

    def _line_search(self) -> bool:
        """Line-constrained SIFT re-search (FX-08): frames of a gap within line_search_reach of a neighbouring run are
        searched pairwise against the RAW frames of that run's time line +- track_search_radius
        (visual_match.line_search: relaxed RANSAC acceptance on a handful of frames, the anchor's ZNCC test unchanged);
        verified matches become anchors -- on an existing track's line they join it, otherwise they start runs."""
        from .visual_match import run_line_searches
        cfg = self.cfg
        reach = int(getattr(cfg, "line_search_reach", 30))
        Rw = int(max(cfg.track_search_radius, cfg.refine_radius))
        tasks: dict[tuple[int, bool], set[int]] = {}
        for a, b, nbs in self._gap_runs():
            for t, side in nbs:
                ks = list(range(a, min(b, a + reach)) if side < 0 else range(max(a, b - reach), b))
                if len(ks) > 2 * self.stride:
                    off = (self.stride // 2 + 1) % self.stride
                    ks = sorted({ks[0], ks[-1]} | {k for k in ks if k % self.stride == off})
                for k in ks:
                    if (k, t.flip) in self.line_done:
                        continue
                    jp = self._line_at(t, k)
                    js = {j for j in range(jp - Rw, jp + Rw + 1) if self.raw.has(j)}
                    if js:
                        tasks.setdefault((k, t.flip), set()).update(js)
        if not tasks:
            return False
        items = sorted(tasks.items())
        self.line_done.update(key for key, _ in items)
        self.stats["line_searches"] = self.stats.get("line_searches", 0) + len(items)
        res = run_line_searches(self.comp, self.raw, self.allowed, self.roi,
                                [(k, sorted(js), fl) for (k, fl), js in items], cfg)
        new: list[Anchor] = []
        for (key, js), (k, anchors, rep) in zip(items, res):
            self.dlog.record("refine", "line_search", comp_frame=int(k), flip=bool(key[1]),
                             window=[min(js), max(js)], found=[{"raw": a.raw, "zncc": round(a.zncc, 4),
                                                                "inliers": a.inliers} for a in anchors],
                             current={"track": int(self.win_tid[k]), "raw": int(self.win_j[k]),
                                      "score": None if not np.isfinite(self.win_s[k]) else round(float(self.win_s[k]), 4)},
                             rejected=rep[:4])
            new += anchors
        if not new:
            return False
        self.anchors.extend(new)
        joined, rest = self._join(new)
        made = self._link(rest)
        self._converge(joined + made)
        return True

    def _weak_tracks(self) -> None:
        """Gray-zone matches (FX-08) on frames still unexplained seed WEAK tracks: their scores give those frames
        UNRESOLVED evidence (best RAW frames, ZNCC) instead of NONE; they never grow, never merge, never set pass-2
        masks and a frame they explain is a match only at >= match_thresh like any other."""
        thr = self.cfg.match_thresh
        seeds = [a for a in self.gray if not self.uniform[a.k] and not (self.win_tid[a.k] >= 0 and self.win_s[a.k] >= thr)]
        if not seeds:
            return
        made = self._link(seeds)
        for t in made:
            t.weak = True
        self.dlog.record("refine", "weak_tracks", tracks=[t.id for t in made],
                         seeds=[[a.k, a.raw, round(a.zncc, 4)] for a in seeds][:200])
        self._converge(made)

    def _join(self, new: list[Anchor]) -> tuple[list[_Track], list[Anchor]]:
        """Rescue anchors on an existing track's time line (|integer residual| <= line_time_tol, within max_gap of
        its span, same flip) join that track -- its framing is re-measured over the frames they cover -- instead
        of starting a run of their own (FX-03 step 3). Returns (joined tracks, remaining anchors)."""
        tol = float(self.cfg.line_time_tol)
        joined: dict[int, _Track] = {}
        rest: list[Anchor] = []
        for a in sorted(new, key=lambda a: (a.k, -a.zncc, a.raw, a.flip)):
            best, cost = None, math.inf
            for tid in sorted(self.tracks):
                t = self.tracks[tid]
                if t.flip != a.flip or t.line is None or not (t.span[0] - self.max_gap <= a.k <= t.span[1] + self.max_gap):
                    continue
                r = abs(a.raw - math.floor(t.line[1] + t.line[0] * a.k + 1e-9))
                if r <= tol and r < cost:
                    best, cost = t, r
            if best is None:
                rest.append(a)
                continue
            best.anchors = sorted(best.anchors + [a], key=lambda b: (b.k, -b.zncc))
            best.span = [max(0, min(best.span[0], a.k - self.ext)), min(self.N - 1, max(best.span[1], a.k + self.ext))]
            joined[best.id] = best
            self.dlog.record("refine", "rescue_join", track=best.id, anchor=[a.k, a.raw], residual=float(cost))
        return [joined[i] for i in sorted(joined)], rest

    # -- time/translation confound check (FX-03 step 7) -------------------------------------------
    def _confound_check(self, fm: FrameMap, delta: dict[int, float]) -> None:
        """Time / translation confound on EVERY track: RAW m(k) +- 1 are measured (ECC from the track's path, every
        framing_sample_step frames) and fitted as their own smooth path -- only the path is refitted, the rule of
        the line choice -- and on every MATCH frame z_d = S_k(m + d | path_d(k)) is compared with the frame's own
        score z_0 = S_k(m | path(k)), with delta = the track's score noise:

        * z_d >= z_0 - delta (a tie): 'confounded' (FrameMap column), soft range widened to include m + d -- the
          time line and the framing trade off and segmentation must not read a time or framing step into it;
        * z_d >  z_0 + 3 delta (strictly better): the frame is reassigned to m + d with path_d's framing (a
          consistent (RAW frame, Sim) pair; dlog 'confound_reassign').

        dlog per track: 'time_translation_confounded' when every frame ties (as before), else 'confound_check'."""
        cfg = self.cfg
        conf = np.zeros(self.N, bool)
        plans, items = [], []
        for tid in sorted(self.tracks):
            t = self.tracks[tid]
            F = np.flatnonzero((fm.track == tid) & (fm.status == Status.MATCH))
            if len(F) == 0:
                continue
            samp = sorted({int(k) for k in F if k % self.stride == 0} | {int(F[0]), int(F[-1])})
            fam = {d: {int(k): int(fm.raw[k]) + d for k in samp if self.raw.has(int(fm.raw[k]) + d)} for d in (-1, 0, 1)}
            for jd in fam.values():
                items += [(t, k, j, fm.sim(k)) for k, j in jd.items()]
            plans.append((t, F, fam))
        self._measure(items)
        score_items, owners = [], []
        for t, F, fam in plans:
            paths = {}
            for d, jd in fam.items():
                good = self._good(t.flip, jd)
                if good:
                    paths[d] = fit_path([(k, sm) for k, _, sm, _, _ in good], self.center, cfg)
            ds = sorted(paths)
            if not ds:
                continue
            for k in F:
                score_items.append((int(k), t.flip, [([int(fm.raw[k]) + d], _sim_at(paths[d], int(k), self.raw_wh,
                                                                                    self.cap)) for d in ds]))
                owners.append((t, int(k), ds, paths))
        if score_items:
            self.stats["score_tasks"] += len(score_items)
            res = self._map(_w_pairs, [(k, fl, [(js, sm.to_dict()) for js, sm in its]) for k, fl, its in score_items])
        else:
            res = []
        per: dict[int, dict] = {}
        reassign = []
        for (t, k, ds, paths), sc in zip(owners, res):
            # m itself under the path fitted from the SAME sampled frames: the alternatives and m are compared with
            # equally constrained paths (the frame's own path is fitted from every frame)
            z0 = float(sc[ds.index(0)][0]) if 0 in ds else float(fm.score[k])
            dl = delta.get(t.id, cfg.soft_delta_min)
            rec = per.setdefault(t.id, {"frames": 0, "ties": 0, "better": 0, "evidence": [], "delta": dl})
            rec["frames"] += 1
            ties, better = [], None
            for d, v in zip(ds, sc):
                if d == 0:
                    continue
                za = float(v[0])
                if not (np.isfinite(za) and np.isfinite(z0)):
                    continue
                if za >= z0 - dl:
                    ties.append(d)
                if za > max(z0, float(fm.score[k])) + 3.0 * dl and (better is None or za > better[1]):
                    better = (d, za)
            if len(rec["evidence"]) < 5:
                rec["evidence"].append({"k": k, "m_path": round(float(fm.score[k]), 5),
                                        **{f"m{d:+d}": round(float(v[0]), 5) for d, v in zip(ds, sc)}})
            if better is not None:
                reassign.append((t, k, better[0], better[1], paths[better[0]]))
                rec["better"] += 1
            elif ties:
                conf[k] = True
                rec["ties"] += 1
                m = int(fm.raw[k])
                fm.soft_lo[k] = min(int(fm.soft_lo[k]), m + min(ties))
                fm.soft_hi[k] = max(int(fm.soft_hi[k]), m + max(ties))
        for tid, rec in sorted(per.items()):
            t = self.tracks[tid]
            t.confounded = rec["ties"] == rec["frames"] and rec["frames"] > 0
            name = "time_translation_confounded" if t.confounded else "confound_check"
            self.dlog.record("refine", name, track=tid, frames=rec["frames"], ties=rec["ties"],
                             reassigned=rec["better"], delta=rec["delta"], evidence=rec["evidence"])
        if reassign:
            self._reassign(fm, reassign, delta)
        fm.d["confounded"] = conf

    def _reassign(self, fm: FrameMap, items: list, delta: dict[int, float]) -> None:
        """Frames whose neighbour RAW frame scores strictly better under its own refitted path: m(k) := m + d with
        that path's framing; candidate vector, identical range, soft range, margin and the measured framing are
        recomputed for the new (RAW frame, Sim) pair."""
        cfg = self.cfg
        R = int(cfg.refine_radius)
        half = CAND_W // 2
        tasks, rows = [], []
        for t, k, d, za, path in items:
            sim = _sim_at(path, k, self.raw_wh, self.cap)
            j = int(fm.raw[k]) + d
            tasks.append((k, [_key(k, sim)], t.flip, j, j, np.zeros(0, np.float32), R,
                          2.0 * float(delta.get(t.id, cfg.soft_delta_min))))
            rows.append((t, k, d, j, sim))
        self._measure([(t, k, j, sim) for t, k, d, j, sim in rows])
        for (t, k, d, j, sim), (lo2, arr, rlo, rhi) in zip(rows, self._map(_w_final, tasks)):
            old = int(fm.raw[k])
            peak = float(arr[j - lo2]) if 0 <= j - lo2 < len(arr) else float("nan")
            js = np.arange(lo2, lo2 + len(arr))
            outside = (js < rlo) | (js > rhi)
            sec = float(np.nanmax(arr[outside])) if np.any(np.isfinite(arr[outside])) else float("nan")
            fm.raw[k], fm.raw_lo[k], fm.raw_hi[k] = j, rlo, rhi
            fm.set_sim(k, sim)
            fm.score[k] = peak
            fm.second[k] = sec
            fm.margin[k] = peak - sec if np.isfinite(sec) else np.nan
            fm.cand_j0[k] = j - half
            fm.cand[k] = np.nan
            for i in range(CAND_W):
                p = j - half + i - lo2
                if 0 <= p < len(arr):
                    fm.cand[k, i] = arr[p]
            dl = float(delta.get(t.id, cfg.soft_delta_min))
            sel = np.flatnonzero(np.isfinite(arr) & (arr >= peak - dl)) + lo2
            fm.soft_lo[k] = int(min(sel.min(), rlo)) if len(sel) else rlo
            fm.soft_hi[k] = int(max(sel.max(), rhi)) if len(sel) else rhi
            mg = float(fm.margin[k])
            fm.low_margin[k] = bool(np.isfinite(mg) and mg <= cfg.low_margin_eps)
            r = self.meas_cache.get((int(k), int(j), bool(t.flip)))
            if r is not None:
                fm.sim_meas[k] = (r[0]["scale"], r[0]["rotation_deg"], r[0]["tx"], r[0]["ty"])
                fm.sim_meas_score[k] = r[1]
            self.dlog.record("refine", "confound_reassign", comp_frame=int(k), track=t.id, raw=[old, int(j)],
                             score=round(peak, 5) if np.isfinite(peak) else None)

    # -- 6. finalize ------------------------------------------------------------------------------
    def _promote(self) -> set[int]:
        """Gray-zone frames promoted to MATCH by the detail-sensitive second score (FX-08): the frame's best
        hypothesis (none_thresh <= score < match_thresh, not uniform) reaches match_thresh on the blur-matched
        gradient ZNCC AND beats RAW jb +- 1, +- 2 under their own re-measured framing by > detail_margin.
        Everything else stays UNRESOLVED (never promoted on the plain score)."""
        cfg = self.cfg
        thr, nt = float(cfg.match_thresh), float(cfg.none_thresh)
        cand = [k for k in range(self.N) if self.win_tid[k] >= 0 and not self.uniform[k]
                and np.isfinite(self.win_s[k]) and nt <= self.win_s[k] < thr]
        if not cand:
            return set()
        tasks = [(k, self.tracks[int(self.win_tid[k])].keys, self.tracks[int(self.win_tid[k])].flip,
                  int(self.win_j[k])) for k in cand]
        margin = float(getattr(cfg, "detail_margin", 0.02))
        out: set[int] = set()
        rows = []
        for k, r in zip(cand, self._map(_w_detail, tasks)):
            sc = r["scores"]
            d0 = sc.get(0, (float("nan"),))[0]
            others = [v[0] for d, v in sc.items() if d != 0 and np.isfinite(v[0])]
            best_other = max(others) if others else float("-inf")
            ok = bool(np.isfinite(d0) and d0 >= thr and d0 > best_other + margin)
            if ok:
                out.add(int(k))
            rows.append({"k": int(k), "raw": int(self.win_j[k]), "score": round(float(self.win_s[k]), 4),
                         "detail": None if not np.isfinite(d0) else round(float(d0), 4),
                         "kernel": sc.get(0, (0, 0, ""))[2],
                         "neighbours": None if not others else round(float(best_other), 4), "promoted": ok})
        self._detail = {r["k"]: r["detail"] for r in rows}
        self.dlog.record("refine", "detail_promotion", frames=len(rows), promoted=sorted(out)[:500],
                         margin=margin, evidence=rows[:300])
        return out

    def _finalize(self) -> FrameMap:
        cfg = self.cfg
        fm = FrameMap(self.N)
        thr = cfg.match_thresh
        R = int(cfg.refine_radius)
        half = CAND_W // 2
        promoted = self._promote()
        match = [k for k in range(self.N) if self.win_tid[k] >= 0 and (self.win_s[k] >= thr or k in promoted)]
        # provisional soft delta per track (margin of the argmax within its own window) -> how far the
        # candidate window must extend so that the soft range {S >= max - delta} is never truncated
        prov: dict[int, list[float]] = {}
        for k in match:
            h = self.hyp[k][int(self.win_tid[k])]
            others = np.delete(h.scores, h.jb - h.lo)
            if np.any(np.isfinite(others)):
                prov.setdefault(int(self.win_tid[k]), []).append(h.sb - float(np.nanmax(others)))
        cap: dict[int, float] = {}
        for tid in prov:
            best = np.array([self.win_s[k] for k in match if int(self.win_tid[k]) == tid], np.float64)
            cap[tid] = min(_SOFT_CAP_MAX, 2.0 * noise_delta(best, float(cfg.soft_delta_min),
                                                             float(getattr(cfg, "soft_delta_max", 0.01))))
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
            sm = _sim_at(t.keys, k, self.raw_wh, self.cap)
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
        # UNIFORM / UNRESOLVED / NONE (FX-08): NONE only when EVERY evaluated hypothesis scored < none_thresh
        nt = float(cfg.none_thresh)
        for k in range(self.N):
            if status[k] == Status.MATCH:
                continue
            if self.uniform[k]:
                status[k] = Status.UNIFORM
            elif track[k] >= 0 and np.isfinite(score[k]) and score[k] >= nt:
                status[k] = Status.UNRESOLVED
        fm.status = status
        # per-track delta, soft ranges, low_margin, confidence
        delta: dict[int, float] = {}
        for tid in sorted(self.tracks):
            sc = np.asarray(fm.score, np.float64)[(fm.track == tid) & (fm.status == Status.MATCH)]
            delta[tid] = noise_delta(sc, float(cfg.soft_delta_min), float(getattr(cfg, "soft_delta_max", 0.01)))
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
            elif st == Status.UNRESOLVED:
                conf[k] = 0.0               # neither a match nor NOT-IN-RAW: no state is supported
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
        self._measured_columns(fm)
        lab = np.full(self.N, -1, np.int8)
        warp = np.full((self.N, 4), np.nan, np.float32)
        for k, v in self.labels.items():
            lab[k] = v
        for k, w in self.warps.items():
            warp[k] = w
        fm.pair_label, fm.pair_warp = lab, warp
        det = np.full(self.N, np.nan, np.float32)
        for k, v in getattr(self, "_detail", {}).items():
            if v is not None:
                det[k] = v
        fm.detail = det
        self._delta = delta
        return fm

    def _measured_columns(self, fm: FrameMap) -> None:
        """FrameMap sim_meas / sim_meas_score: the per-frame ECC measurement of the frame's OWN RAW frame m(k)
        (refine's measurements where they exist, else measured now from the track's path). The Sim columns hold
        the path value (FX-03 step 6)."""
        items, rows = [], []
        for k in np.flatnonzero(fm.status == Status.MATCH):
            k = int(k)
            t = self.tracks.get(int(fm.track[k]))
            if t is None:
                continue
            rows.append((k, int(fm.raw[k]), bool(fm.flip[k])))
            items.append((t, k, int(fm.raw[k]), fm.sim(k)))
        self._measure(items)
        sm = np.full((self.N, 4), np.nan, np.float64)
        sz = np.full(self.N, np.nan, np.float32)
        for k, j, fl in rows:
            r = self.meas_cache.get((k, j, fl))
            if r is not None:
                sm[k] = (r[0]["scale"], r[0]["rotation_deg"], r[0]["tx"], r[0]["ty"])
                sz[k] = r[1]
        fm.sim_meas, fm.sim_meas_score = sm, sz

    # -- 0. the competitor's own temporal signature (FX-07, refine side) ----------------------------
    def _ensure_labels(self, ranges: Iterable[tuple[int, int]]) -> None:
        """Comp-only pair labels (temporal.py: REPEAT / MOVE / UNKNOWN / CUT with per-shot noise floors) and the
        pairs' editor moves over the comp frames [k0, k1] of ``ranges`` (measured once, on the box ROI with
        refine's allowed masks; the whole signature is relabelled). REPEAT / MOVE positions give a time line's
        speed and fractional phase (candidate-line pruning, never the integer offset); a repeat pair's warp is the
        editor's own crop velocity (initial model of a one-anchor run). Measured only where a line can repeat
        RAW frames at all (u <= REPEAT_MAX_SLOPE: e.g. 23.976 / 25 fps RAW at speed 1 on 30 fps); static,
        low-texture and frame-blended content gives no labels and changes nothing (FX-07)."""
        from . import temporal
        todo = set()
        for k0, k1 in ranges:
            for k in range(max(0, int(k0) - 1), min(self.N - 1, int(k1) + 1)):
                if k not in self._sig_done:
                    todo.add(k)
        if not todo:
            return
        ks = sorted(todo)
        self._sig_done.update(ks)
        chunks, run = [], [ks[0]]
        for k in ks[1:]:
            if k == run[-1] + 1 and len(run) < 48:
                run.append(k)
            else:
                chunks.append((run[0], run[-1] + 1))
                run = [k]
        chunks.append((run[0], run[-1] + 1))
        for rows in self._map(_w_temporal, chunks):
            for k, g, v in rows:
                (self._sig.d1 if g == 1 else self._sig.d2)[int(k)] = temporal.PairMeasure(*v)
        lab = self._lab = temporal.label_pairs(self._sig, self.cfg)
        code = {temporal.UNKNOWN: _LAB_UNKNOWN, temporal.REPEAT: _LAB_REPEAT, temporal.MOVE: _LAB_MOVE,
                temporal.CUT: _LAB_CUT}
        self.labels = {int(k): code[v] for k, v in lab.label.items()}
        x, y, w, h = self.roi
        f = temporal.scale_of((h, w), int(getattr(self.cfg, "temporal_max_side", 200)))
        rx, ry = float(self.comp.ratio[0]), float(self.comp.ratio[1])
        self.warps = {int(k): (float(pm.dx) / f / rx, float(pm.dy) / f / ry, float(pm.ds), float(pm.dtheta))
                      for k, pm in self._sig.d1.items() if np.isfinite(pm.cc)}
        self.stats["temporal_pairs"] = len(self._sig.d1) + len(self._sig.d2)

    def _labels_summary(self) -> None:
        from . import temporal
        if self._lab is None:
            self.dlog.record("refine", "temporal_labels", counts=None, pairs_measured=0,
                             note=f"no time line can repeat a RAW frame (slope > {self.repeat_max_slope})")
            return
        summ = temporal.summary(self._lab)
        self.dlog.record("refine", "temporal_labels", counts=summ["counts"], repeats=summ["runs"]["repeat"][:200],
                         pairs_measured=len(self._sig.d1))

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
        if self.near:
            joined, rest = self._join(self.near)
            self.dlog.record("refine", "near_miss_anchors", joined=[[a.k, a.raw] for a in self.near if a not in rest],
                             dropped=len(rest))
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
        # FX-08 search before giving up: neighbours' lines across short gaps, line-constrained SIFT, gray evidence
        for _ in range(2):
            self._propagate()
            found = self._line_search()
            lap("line_search")
            if found:
                self._overlay_pass2()
                lap("overlay_pass2")
            else:
                break
        self._propagate()
        self._weak_tracks()
        lap("weak_tracks")
        fm = self._finalize()
        lap("finalize")
        self._confound_check(fm, self._delta)
        lap("confound")
        self._labels_summary()
        n_png = self._debug_pngs(fm, debug_dir)
        lap("debug_png")
        counts = {name: int(np.sum(fm.status == v)) for name, v in
                  (("match", Status.MATCH), ("none", Status.NONE), ("uniform", Status.UNIFORM),
                   ("unresolved", Status.UNRESOLVED))}
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
