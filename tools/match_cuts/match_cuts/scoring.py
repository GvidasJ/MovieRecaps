"""Shared similarity scoring (DESIGN.md §5.3). Every stage that compares a competitor frame with a
RAW frame (refine, segment/transition fitting, verify) uses these functions so scores are comparable.

Scores are masked ZNCC in COMPETITOR space: candidate RAW proxy frames are warped into the competitor
proxy's video-box ROI with the canonical Sim (+flip), and compared over
    mask = box coverage  &  ~static  &  ~overlay  &  warped-RAW-valid.
Both images are lightly blurred (sigma = cfg.score_blur px at comp-proxy res) to suppress resampling
and compression noise before ZNCC. Optionally the gradient-magnitude ZNCC is averaged in
(``grad_weight``) for colour-graded material.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .geometry import Sim, to_cv_matrix, translate3, h3


@dataclass
class CompRegion:
    """A competitor frame's video-region ROI prepared for scoring."""
    img: np.ndarray        # float32 [h, w] blurred ROI of the comp proxy frame
    mask: np.ndarray       # bool [h, w] pixels allowed in scores (box & ~static & ~overlay)
    roi: tuple[int, int, int, int]   # (x, y, w, h) in comp proxy pixels
    grad: np.ndarray | None = None


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    import cv2
    img = img.astype(np.float32, copy=False)
    if sigma and sigma > 0:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    return img


def _gradmag(img: np.ndarray) -> np.ndarray:
    import cv2
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def prepare_comp(comp_img: np.ndarray, roi: tuple[int, int, int, int], allowed: np.ndarray | None,
                 blur: float = 1.0, with_grad: bool = False) -> CompRegion:
    """comp_img: full comp proxy frame (uint8 gray). allowed: bool mask of the full proxy frame or of
    the ROI (True = usable pixel). roi = (x, y, w, h) at proxy res."""
    x, y, w, h = roi
    sub = comp_img[y:y + h, x:x + w]
    img = _blur(sub, blur)
    if allowed is None:
        m = np.ones((h, w), bool)
    elif allowed.shape == comp_img.shape[:2]:
        m = allowed[y:y + h, x:x + w].astype(bool)
    else:
        m = allowed.astype(bool)
    return CompRegion(img, m, roi, _gradmag(img) if with_grad else None)


def warp_to_roi(raw_img: np.ndarray, sim: Sim, flip: bool, raw_w_full: float,
                raw_ratio: tuple[float, float], comp_ratio: tuple[float, float],
                roi: tuple[int, int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Warp a RAW proxy frame into the comp-proxy ROI. Returns (float32 image, valid bool mask)."""
    import cv2
    m = to_cv_matrix(sim, flip, raw_w_full, raw_ratio, comp_ratio)
    x, y, w, h = roi
    m = (translate3(-x, -y) @ h3(m))[:2, :]
    out = cv2.warpAffine(raw_img.astype(np.float32, copy=False), m, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    ones = np.full(raw_img.shape[:2], 255, np.uint8)
    valid = cv2.warpAffine(ones, m, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    valid = cv2.erode(valid, np.ones((3, 3), np.uint8)) > 0
    return out, valid


def zncc(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Zero-mean normalised cross-correlation over mask (NaN if < 64 px or zero variance)."""
    if mask is not None:
        a = a[mask]
        b = b[mask]
    a = a.astype(np.float64).ravel()
    b = b.astype(np.float64).ravel()
    if a.size < 64:
        return float("nan")
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    if den <= 1e-9:
        return float("nan")
    return float((a * b).sum() / den)


def grad_zncc(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Gradient-domain ZNCC: normalised correlation of the stacked Sobel gradient vectors (gx, gy) of ``a``
    and ``b`` over ``mask`` (eroded by one pixel so the mask edge itself adds no gradient). On dark or
    low-texture frames plain ZNCC is dominated by smooth illumination that stays correlated under a
    misframing; the gradient vectors follow the edges (direction included), so a shifted picture scores
    low. NaN like ``zncc``."""
    import cv2
    a = np.asarray(a, np.float32)
    b = np.asarray(b, np.float32)
    m = np.ones(a.shape, bool) if mask is None else np.asarray(mask, bool)
    m = cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    ga = np.concatenate([cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)[m], cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)[m]])
    gb = np.concatenate([cv2.Sobel(b, cv2.CV_32F, 1, 0, ksize=3)[m], cv2.Sobel(b, cv2.CV_32F, 0, 1, ksize=3)[m]])
    return zncc(ga, gb)


def zncc_rows(vec: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """ZNCC of one vector against each row of mat (same length). Returns float64 [n]."""
    v = np.asarray(vec, np.float64)
    v = v - v.mean()
    m = np.asarray(mat, np.float64)
    n = m.shape[1]
    s1 = m.sum(axis=1)
    s2 = np.einsum("ij,ij->i", m, m)
    num = m @ v                       # v is zero-mean, so the row means drop out of the numerator
    den = np.sqrt(np.maximum(s2 - s1 * s1 / n, 0.0) * float(v @ v))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 1e-9, num / den, np.nan)
    return out


def score_candidates(comp: CompRegion, raw_frames: Sequence[np.ndarray], sim: Sim, flip: bool,
                     raw_w_full: float, raw_ratio: tuple[float, float], comp_ratio: tuple[float, float],
                     blur: float = 1.0, grad_weight: float = 0.0, min_pixels: int = 256) -> np.ndarray:
    """Masked ZNCC of the comp region against several RAW proxy frames under ONE transform.

    The valid region is the intersection over all candidates (identical for one transform), so the
    scores are directly comparable. Returns float64 [len(raw_frames)] (NaN when too few pixels).
    """
    if len(raw_frames) == 0:
        return np.zeros(0)
    warped = []
    valid_all = comp.mask.copy()
    for rf in raw_frames:
        w, v = warp_to_roi(rf, sim, flip, raw_w_full, raw_ratio, comp_ratio, comp.roi)
        warped.append(_blur(w, blur))
        valid_all &= v
    if valid_all.sum() < min_pixels:
        return np.full(len(raw_frames), np.nan)
    mat = np.stack([w[valid_all] for w in warped])
    s = zncc_rows(comp.img[valid_all], mat)
    if grad_weight > 0:
        g = comp.grad if comp.grad is not None else _gradmag(comp.img)
        gm = np.stack([_gradmag(w)[valid_all] for w in warped])
        s = (1 - grad_weight) * s + grad_weight * zncc_rows(g[valid_all], gm)
    return s


def identical_images(a: np.ndarray, b: np.ndarray, mask: np.ndarray, mad_thresh: float, zncc_thresh: float,
                     tiles: int = 4, contrast_ref: float = 128.0, contrast_min: float = 0.25,
                     min_pixels: int = 32) -> tuple[bool, dict]:
    """Are two (warped) RAW frames visually identical inside ``mask`` (FX-08 identity test)? Judged as a MAX OVER
    TILES (``tiles`` x ``tiles`` grid over the mask's bounding box, tiles with >= ``min_pixels`` valid pixels):
    every tile must have mean |a - b| <= the contrast-relative threshold OR its ZNCC >= ``zncc_thresh``. The mean
    |diff| threshold is ``mad_thresh`` scaled by the frame's robust contrast (p98 - p2 of ``a`` in the mask)
    relative to ``contrast_ref``, clipped to [contrast_min, 1]: on a dark, low-contrast frame (a dimming car display)
    the same absolute change is a much larger part of the picture, and a small changing region must not be diluted
    by a large static one (the real run's 4-frame error behind 'ambiguous-identical' 592-595). Returns
    (identical, evidence {'worst_mad', 'thresh', 'tiles'})."""
    m = np.asarray(mask, bool)
    ev: dict = {"worst_mad": None, "thresh": None, "tiles": 0}
    if int(m.sum()) < 64:
        return False, ev
    av = np.asarray(a)[m].astype(np.float64)
    lo, hi = np.percentile(av, [2.0, 98.0])
    thr = float(mad_thresh) * float(np.clip((hi - lo) / max(float(contrast_ref), 1e-6), contrast_min, 1.0))
    ev["thresh"] = round(thr, 4)
    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    ty = np.linspace(y0, y1, int(tiles) + 1).astype(int)
    tx = np.linspace(x0, x1, int(tiles) + 1).astype(int)
    worst, n = 0.0, 0
    for i in range(int(tiles)):
        for j in range(int(tiles)):
            mt = m[ty[i]:ty[i + 1], tx[j]:tx[j + 1]]
            if int(mt.sum()) < min_pixels:
                continue
            at = np.asarray(a)[ty[i]:ty[i + 1], tx[j]:tx[j + 1]][mt].astype(np.float64)
            bt = np.asarray(b)[ty[i]:ty[i + 1], tx[j]:tx[j + 1]][mt].astype(np.float64)
            mad = float(np.mean(np.abs(at - bt)))
            n += 1
            worst = max(worst, mad)
            if mad <= thr:
                continue
            z = zncc(at, bt)
            if not (math.isfinite(z) and z >= zncc_thresh):
                ev.update(worst_mad=round(worst, 4), tiles=n)
                return False, ev
    ev.update(worst_mad=round(worst, 4), tiles=n)
    return n > 0, ev


def blur_kernels(max_len: int = 15) -> list[tuple[str, np.ndarray]]:
    """The zero-phase (symmetric) blur family of the detail score's blur matching: identity, isotropic Gaussians and
    horizontal / vertical box (linear motion) blurs up to ``max_len`` px. A symmetric kernel cannot shift content,
    so fitting it per candidate never trades time for position (unlike a free framing fit)."""
    import cv2
    out: list[tuple[str, np.ndarray]] = [("id", np.ones((1, 1), np.float32))]
    for s in (0.7, 1.2, 2.0, 3.0):
        g = cv2.getGaussianKernel(int(2 * math.ceil(3 * s) + 1), s).astype(np.float32)
        out.append((f"g{s}", g @ g.T))
    for n in range(3, int(max_len) + 1, 4):
        box = np.full((1, n), 1.0 / n, np.float32)
        out.append((f"h{n}", box))
        out.append((f"v{n}", box.T.copy()))
    return out


def detail_score(comp_img: np.ndarray, raw_img: np.ndarray, mask: np.ndarray,
                 kernels: Sequence[tuple[str, np.ndarray]] | None = None) -> tuple[float, float, str]:
    """Detail-sensitive second score (FX-08) of a competitor ROI against a warped RAW candidate (both float, same
    shape; ``mask`` = valid pixels): BLUR MATCHING first -- each zero-phase kernel of ``blur_kernels`` applied to the
    SHARPER of the two images (gradient energy relative to variance), the one maximising the plain masked ZNCC kept
    -- then the gradient-domain ZNCC (``grad_zncc``) of the blur-matched pair. A sharpened or AI-enhanced competitor
    frame correlates with a motion-blurred RAW frame only at low frequencies; after blur matching the detail of the
    TRUE frame lines up, a different frame's does not. Returns (detail, plain ZNCC after blur matching, kernel)."""
    import cv2
    a = np.asarray(comp_img, np.float32)
    b = np.asarray(raw_img, np.float32)
    m = np.asarray(mask, bool)
    if int(m.sum()) < 256:
        return float("nan"), float("nan"), ""

    def sharpness(x: np.ndarray) -> float:
        g = _gradmag(x)[m]
        v = float(np.var(x[m]))
        return float(np.mean(g * g)) / v if v > 1e-6 else 0.0
    sharp_comp = sharpness(a) >= sharpness(b)
    best = (-np.inf, None, "", None)
    for name, K in (kernels or blur_kernels()):
        x = cv2.filter2D(a, -1, K, borderType=cv2.BORDER_REFLECT) if sharp_comp else a
        y = b if sharp_comp else cv2.filter2D(b, -1, K, borderType=cv2.BORDER_REFLECT)
        z = zncc(x, y, m)
        if math.isfinite(z) and z > best[0]:
            best = (z, x, name, y)
    if best[1] is None:
        return float("nan"), float("nan"), ""
    return float(grad_zncc(best[1], best[3], m)), float(best[0]), best[2]


def noise_delta(best_scores, dmin: float = 0.001, dmax: float = 0.01) -> float:
    """Score-noise tolerance delta for one track (DESIGN §3): 3 x the robust std (1.4826·MAD) of the
    track's BEST scores, clamped to [dmin, dmax].

    It is deliberately NOT derived from the margins: margins measure how discriminative the content is
    (0.1 on textured footage), not how noisy a correct frame's score is; using them let a frame that is
    0.18 below its own best be treated as 'explained'."""
    s = np.asarray(best_scores, np.float64)
    s = s[np.isfinite(s)]
    if s.size < 3:
        return float(dmin)
    mad = float(np.median(np.abs(s - np.median(s)))) * 1.4826
    return float(min(dmax, max(dmin, 3.0 * mad)))


def region_stats(comp_img: np.ndarray, roi: tuple[int, int, int, int], allowed: np.ndarray | None) -> tuple[float, float]:
    """(mean, std) of luma in the allowed part of the ROI -- used for UNIFORM (dip/flash) detection."""
    x, y, w, h = roi
    sub = comp_img[y:y + h, x:x + w].astype(np.float32)
    if allowed is not None:
        m = allowed[y:y + h, x:x + w] if allowed.shape == comp_img.shape[:2] else allowed
        vals = sub[m.astype(bool)]
    else:
        vals = sub.ravel()
    if vals.size == 0:
        return float("nan"), float("nan")
    return float(vals.mean()), float(vals.std())


def fit_blend(comp: CompRegion, a_img: np.ndarray, b_img: np.ndarray, valid: np.ndarray) -> tuple[float, float, float]:
    """Least-squares fit comp ≈ alpha*A + (1-alpha)*B + c over valid pixels (A, B already warped and
    blurred into the ROI). Returns (alpha, residual_rms, zncc_of_fit)."""
    m = valid & comp.mask
    if m.sum() < 64:
        return float("nan"), float("nan"), float("nan")
    y = comp.img[m].astype(np.float64)
    a = a_img[m].astype(np.float64)
    b = b_img[m].astype(np.float64)
    # y - b = alpha (a - b) + c
    X = np.stack([a - b, np.ones_like(a)], axis=1)
    coef, *_ = np.linalg.lstsq(X, y - b, rcond=None)
    alpha = float(coef[0])
    fit = alpha * a + (1 - alpha) * b + coef[1]
    res = float(np.sqrt(np.mean((y - fit) ** 2)))
    return alpha, res, zncc(y, fit)


BLEND_MIN_GAIN = 0.05      # beta_A + beta_B at or below this: comp is not a blend of A and B (alpha undefined)
BLEND_MIN_DET = 1e-4       # relative determinant of the [A, B] covariance below this: A ~ B, alpha undefined


def blend_alpha_cov(c_aa: float, c_bb: float, c_ab: float, c_ay: float, c_by: float,
                    min_gain: float = BLEND_MIN_GAIN) -> tuple[float, float]:
    """Gain-independent blend weight from (co)variances: the unconstrained least-squares fit
    y ≈ beta_A*A + beta_B*B + c, alpha_A = beta_A / (beta_A + beta_B). Returns (alpha_A, beta_A + beta_B);
    alpha_A is NaN when A and B are (nearly) collinear or beta_A + beta_B <= ``min_gain``.

    Unlike ``fit_blend`` (y - B = alpha (A - B) + c, i.e. gain fixed at 1) the ratio does not depend on a
    contrast change of the competitor (y = g * blend + c): a gain g != 1 makes the constrained fit measure
    alpha_B ≈ (1 - g) / 2 on a pure frame and scales the ramp slope by g (review R2-1)."""
    det = c_aa * c_bb - c_ab * c_ab
    if not (c_aa > 0 and c_bb > 0) or det <= BLEND_MIN_DET * c_aa * c_bb:
        return float("nan"), float("nan")
    beta_a = (c_bb * c_ay - c_ab * c_by) / det
    beta_b = (c_aa * c_by - c_ab * c_ay) / det
    gain = beta_a + beta_b
    if not math.isfinite(gain) or gain <= min_gain:
        return float("nan"), float(gain)
    return float(beta_a / gain), float(gain)


def fit_blend_free(comp: CompRegion, a_img: np.ndarray, b_img: np.ndarray, valid: np.ndarray,
                   min_gain: float = BLEND_MIN_GAIN) -> tuple[float, float, float]:
    """Gain-independent two-source fit comp ≈ beta_A*A + beta_B*B + c over valid pixels (A, B already
    warped and blurred into the ROI). Returns (alpha_A = beta_A / (beta_A + beta_B), beta_A + beta_B,
    zncc_of_fit); alpha_A is NaN when undefined (see ``blend_alpha_cov``)."""
    m = valid & comp.mask
    if m.sum() < 64:
        return float("nan"), float("nan"), float("nan")
    y = comp.img[m].astype(np.float64)
    a = a_img[m].astype(np.float64)
    b = b_img[m].astype(np.float64)
    y0, a0, b0 = y - y.mean(), a - a.mean(), b - b.mean()
    n = float(y.size)
    alpha, gain = blend_alpha_cov(float(a0 @ a0) / n, float(b0 @ b0) / n, float(a0 @ b0) / n,
                                  float(a0 @ y0) / n, float(b0 @ y0) / n, min_gain)
    if not math.isfinite(alpha):
        return alpha, gain, float("nan")
    fit = gain * (alpha * a0 + (1.0 - alpha) * b0)
    return alpha, gain, zncc(y0, fit)
