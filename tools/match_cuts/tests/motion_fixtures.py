"""Small numpy image sequences for the hypothesis-neutral verification tests (test_temporal.py,
test_verify.py): a RAW whose content changes NON-rigidly from frame to frame (two textures moving at different
speeds -- a similarity alignment can never turn one RAW frame into the next), a 23.976->30 style cadence
(``cadence``: every 5th competitor pair repeats a RAW frame), an editor pan over it, competitor noise."""
from __future__ import annotations

import math

import numpy as np

from match_cuts.geometry import Sim


def texture(h: int, w: int, seed: int, sigma: float = 1.5, std: float = 35.0, mean: float = 128.0) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.uniform(0, 255, (h, w)).astype(np.float32), (0, 0), sigma)
    return ((img - img.mean()) / max(float(img.std()), 1e-6) * std + mean).astype(np.float32)


def _shift(img: np.ndarray, dx: float, dy: float, out_wh: tuple[int, int]) -> np.ndarray:
    import cv2
    m = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], np.float32)       # out(x) = img(x + d)
    return cv2.warpAffine(img, m, out_wh, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def two_layer_raw(n: int, h: int = 150, w: int = 200, p1: float = 3.0, p2: float = -1.5, w1: float = 0.85,
                  seed: int = 5, dark: bool = False) -> np.ndarray:
    """uint8 [n, h, w]: frame j = w1 * T1(x + p1 j) + (1 - w1) * T2(x + p2 j) -- the dominant layer pans at
    p1 px per RAW frame, the second one at p2 (non-rigid change between frames)."""
    t1 = texture(h, w + int(abs(p1) * n) + 8, seed)
    t2 = texture(h, w + int(abs(p2) * n) + 8, seed + 1, sigma=2.0)
    out = np.empty((n, h, w), np.uint8)
    for j in range(n):
        a = _shift(t1, p1 * j if p1 >= 0 else abs(p1) * (n - j), 0.0, (w, h))
        b = _shift(t2, p2 * j if p2 >= 0 else abs(p2) * (n - j), 0.0, (w, h))
        img = w1 * a + (1.0 - w1) * b
        if dark:
            img = 18.0 + (img - 128.0) * 0.12
        out[j] = np.clip(np.rint(img), 0, 255).astype(np.uint8)
    return out


def cadence(n: int, ratio: float = 0.8, phase: float = 0.1, j0: int = 0) -> np.ndarray:
    """RAW frame shown at competitor frame k: floor(ratio k + phase) + j0 (0.8 = 24 in 30: a repeat every 5)."""
    return np.array([j0 + int(math.floor(ratio * k + phase + 1e-9)) for k in range(n)], np.int64)


def render(raw: np.ndarray, js: np.ndarray, sims: list[Sim], out_wh: tuple[int, int], noise: float = 1.5,
           seed: int = 9) -> np.ndarray:
    """Competitor-like uint8 frames: RAW frame js[k] warped with sims[k] (match_cuts conventions, RAW and
    competitor at ratio 1) + gaussian noise (the competitor's encode)."""
    from match_cuts import scoring
    rng = np.random.default_rng(seed)
    out = []
    for j, sim in zip(js, sims):
        img, _v = scoring.warp_to_roi(raw[int(j)], sim, False, raw.shape[2], (1.0, 1.0), (1.0, 1.0),
                                      (0, 0, out_wh[0], out_wh[1]))
        if noise > 0:
            img = img + rng.normal(0.0, noise, img.shape)
        out.append(np.clip(np.rint(img), 0, 255).astype(np.uint8))
    return np.stack(out)


def pan_sims(n: int, tx0: float = -20.0, ty0: float = -15.0, v: float = 1.2) -> list[Sim]:
    """Editor pan: the picture moves by -v px per competitor frame (constant scale 1)."""
    return [Sim(1.0, 0.0, tx0 - v * k, ty0) for k in range(n)]
