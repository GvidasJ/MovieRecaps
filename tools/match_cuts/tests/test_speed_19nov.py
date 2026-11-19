"""19 Nov ("make the slowest stages faster without changing the result"): the search and refine workers spend most
of their time in ``scoring.score_candidates`` (py-spy on The Best Business Idea In Key and Peele (2): 78 % of a
refine worker). It made copies nobody needs -- the same "valid" mask warped again for every RAW frame, the warped
pictures picked through the mask, stacked as float32 and then copied once more as float64. The function without those
copies must give the SAME numbers, bit for bit: every decision after it compares these scores with thresholds.

``_before`` is the function as it was until 19 Nov, kept here word for word as the reference."""
from __future__ import annotations

import numpy as np
import pytest

from match_cuts import scoring as S
from match_cuts.common import single_thread_blas
from match_cuts.geometry import Sim


def _before(comp, raw_frames, sim, flip, raw_w_full, raw_ratio, comp_ratio, blur=1.0, grad_weight=0.0,
            min_pixels=256):
    if len(raw_frames) == 0:
        return np.zeros(0)
    warped = []
    valid_all = comp.mask.copy()
    for rf in raw_frames:
        w, v = S.warp_to_roi(rf, sim, flip, raw_w_full, raw_ratio, comp_ratio, comp.roi)
        warped.append(S._blur(w, blur))
        valid_all &= v
    if valid_all.sum() < min_pixels:
        return np.full(len(raw_frames), np.nan)
    mat = np.stack([w[valid_all] for w in warped])
    s = S.zncc_rows(comp.img[valid_all], mat)
    if grad_weight > 0:
        g = comp.grad if comp.grad is not None else S._gradmag(comp.img)
        gm = np.stack([S._gradmag(w)[valid_all] for w in warped])
        s = (1 - grad_weight) * s + grad_weight * S.zncc_rows(g[valid_all], gm)
    return s


def _case(rng, r: int):
    import cv2
    rw, rh = (640, 360) if r % 3 else (640, 480)
    cw, ch = 540, 960
    roi = (0, 117, 540, 726) if r % 4 else (30, 200, 480, 500)
    comp_img = cv2.GaussianBlur(rng.integers(0, 256, (ch, cw), dtype=np.uint8), (0, 0), 3)
    allowed = rng.random((ch, cw)) > (0.1 if r % 5 else 0.9995)       # (every 5th: almost nothing is allowed)
    if r % 2:
        allowed[400:470] = False                                     # a caption band
    n = int(rng.integers(1, 12))
    frames = [cv2.GaussianBlur(rng.integers(0, 256, (rh, rw), dtype=np.uint8), (0, 0), 2) for _ in range(n)]
    sim = Sim(float(rng.uniform(0.7, 2.6)), float(rng.uniform(-0.05, 0.05)) if r % 3 == 0 else 0.0,
              float(rng.uniform(-900, 300)), float(rng.uniform(-300, 600)))
    return comp_img, roi, allowed, frames, sim, bool(r % 2), (rw / 1920.0, rh / 1080.0)


def test_the_scores_are_the_same_bit_for_bit_as_before_the_copies_were_taken_out():
    import cv2
    cv2.setNumThreads(1)
    rng = np.random.default_rng(19)
    nan_rows = scored = 0
    with single_thread_blas():
        for r in range(120):
            comp_img, roi, allowed, frames, sim, flip, rr = _case(rng, r)
            region = S.prepare_comp(comp_img, roi, allowed, 1.0)
            args = (region, frames, sim, flip, 1920.0, rr, (0.5, 0.5))
            a, b = _before(*args, blur=1.0), S.score_candidates(*args, blur=1.0)
            assert a.shape == b.shape == (len(frames),) and a.dtype == b.dtype
            assert a.tobytes() == b.tobytes(), (r, a, b)             # not "close": the same bits
            nan_rows += int(np.isnan(a).all())
            scored += int(np.isfinite(a).all())
    assert nan_rows >= 5 and scored >= 60                            # both ends were really tried


def test_no_frames_too_few_pixels_and_frames_of_two_sizes_as_before():
    import cv2
    rng = np.random.default_rng(5)
    comp_img, roi, allowed, frames, sim, flip, rr = _case(rng, 1)
    region = S.prepare_comp(comp_img, roi, allowed, 1.0)
    assert S.score_candidates(region, [], sim, flip, 1920.0, rr, (0.5, 0.5)).shape == (0,)
    none = S.prepare_comp(comp_img, roi, np.zeros_like(allowed), 1.0)
    out = S.score_candidates(none, frames, sim, flip, 1920.0, rr, (0.5, 0.5))
    assert out.shape == (len(frames),) and np.isnan(out).all()
    # RAW frames of two sizes in one call (nobody does it, but the mask of each size is still its own)
    mixed = [frames[0], cv2.resize(frames[0], (640, 480))[:300]]
    with single_thread_blas():
        a = _before(region, mixed, sim, flip, 1920.0, rr, (0.5, 0.5))
        b = S.score_candidates(region, mixed, sim, flip, 1920.0, rr, (0.5, 0.5))
    assert a.tobytes() == b.tobytes()


@pytest.mark.parametrize("gw", [0.25, 0.5])
def test_with_the_edge_score_mixed_in_the_numbers_stay_the_same(gw):
    """(The edge score is only used by the picture's second look. OpenCV's Sobel gives other last bits from one call
    to the next -- also in the function as it was -- so here "the same" is to 12 digits.)"""
    rng = np.random.default_rng(23)
    with single_thread_blas():
        for r in range(12):
            comp_img, roi, allowed, frames, sim, flip, rr = _case(rng, r * 4 + 1)
            region = S.prepare_comp(comp_img, roi, allowed, 1.0, with_grad=bool(r % 2))
            args = (region, frames, sim, flip, 1920.0, rr, (0.5, 0.5))
            a = _before(*args, blur=1.0, grad_weight=gw)
            b = S.score_candidates(*args, blur=1.0, grad_weight=gw)
            np.testing.assert_allclose(a, b, rtol=0, atol=1e-12, equal_nan=True)


# ---------------------------------------------------------------------------------------------------------------------
# refine._gn_level -- 82 % of a search worker's time (py-spy, the same video)
# ---------------------------------------------------------------------------------------------------------------------

def _gn_before(L, sim, flip, raw_w, cfg, lock_theta):
    """refine._gn_level as it was until 19 Nov, word for word."""
    import math

    import cv2

    from match_cuts import scoring
    ox, oy, w, h = L.roi
    cx, cy = L.cr
    X = (np.arange(w, dtype=np.float64) + ox + 0.5)[None, :] / cx
    Y = (np.arange(h, dtype=np.float64) + oy + 0.5)[:, None] / cy
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
        dX, dY = X[m] - cur.tx, Y[m] - cur.ty
        cols = [-(gx * cx * dX + gy * cy * dY) / cur.s, -gx * cx, -gy * cy]
        if not lock_theta:
            th = math.radians(1.0)
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
        if abs(step * dtx * cx) < 0.01 and abs(step * dty * cy) < 0.01 and abs(step * ds) / cur.s < 1e-5 \
                and abs(step * dth) < 1e-4:
            Iw, v = scoring.warp_to_roi(L.inp, cur, flip, raw_w, L.rr, L.cr, L.roi)
            m = L.mask & (cv2.erode(v.astype(np.uint8), ker) > 0)
            z = scoring.zncc(L.tmpl, Iw, m) if int(m.sum()) >= 64 else float("nan")
            if math.isfinite(z) and z >= best_z:
                best_z, best = z, cur
            break
    return best


def test_the_gauss_newton_fit_gives_the_same_framing_bit_for_bit():
    import cv2

    from match_cuts import refine
    from match_cuts.config import Config
    from match_cuts.geometry import to_cv_matrix
    cv2.setNumThreads(1)
    cfg = Config()
    rng = np.random.default_rng(31)
    rr, cr, roi = (640 / 1920.0, 360 / 1080.0), (0.5, 0.5), (0, 117, 540, 726)
    moved = found = 0
    with single_thread_blas():
        for r in range(14):
            raw = cv2.GaussianBlur(rng.integers(0, 256, (360, 640), dtype=np.uint8), (0, 0), 4)
            true = Sim(float(rng.uniform(1.5, 1.8)), 0.0, float(rng.uniform(-1000, -850)), float(rng.uniform(200, 260)))
            flip = bool(r % 2)
            comp = cv2.warpAffine(raw.astype(np.float32), to_cv_matrix(true, flip, 1920.0, rr, cr), (540, 960))
            comp = np.clip(comp * 0.9 + 8, 0, 255).astype(np.uint8)               # (a gain and an offset to solve)
            allowed = rng.random((960, 540)) > 0.05
            region = S.prepare_comp(comp, roi, allowed, cfg.score_blur)
            start = Sim(true.s * float(rng.uniform(0.985, 1.015)), float(rng.uniform(-0.4, 0.4)) if r % 3 else 0.0,
                        true.tx + float(rng.uniform(-6, 6)), true.ty + float(rng.uniform(-6, 6)))
            for L in refine._pyramid(region, raw, rr, cr, cfg, 3):                 # every pyramid level
                for lock in (True, False):
                    a = _gn_before(L, start, flip, 1920.0, cfg, lock)
                    b = refine._gn_level(L, start, flip, 1920.0, cfg, lock)
                    assert (a is None) == (b is None)
                    if a is not None:
                        assert (a.s, a.theta_deg, a.tx, a.ty) == (b.s, b.theta_deg, b.tx, b.ty), (r, lock, a, b)
                        found += 1
                        moved += int(abs(a.tx - start.tx) > 0.5 or abs(a.ty - start.ty) > 0.5)
    assert found >= 60 and moved >= 30                                           # the fit really ran and moved
