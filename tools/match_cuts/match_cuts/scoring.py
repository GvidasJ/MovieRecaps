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


def zncc_rows(vec: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """ZNCC of one vector against each row of mat (same length). Returns float64 [n]."""
    v = vec.astype(np.float64)
    v = v - v.mean()
    m = mat.astype(np.float64)
    m = m - m.mean(axis=1, keepdims=True)
    num = m @ v
    den = np.sqrt((m * m).sum(axis=1) * (v * v).sum())
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
