"""Unit tests for scoring.py's gain-independent blend estimator (review R2-1): alpha_B = beta_B / (beta_A + beta_B)
of the unconstrained fit y ~ beta_A*A + beta_B*B + c, compared with the gain-fixed fit_blend."""
from __future__ import annotations

import math

import numpy as np
import pytest

from match_cuts import scoring


def _tex(seed: int, h: int = 60, w: int = 80) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    x = cv2.GaussianBlur(rng.uniform(0, 255, (h, w)).astype(np.float32), (0, 0), 1.5)
    return ((x - x.mean()) / x.std() * 35.0 + 128.0).astype(np.float32)


def _region(y: np.ndarray) -> scoring.CompRegion:
    return scoring.CompRegion(img=y.astype(np.float32), mask=np.ones(y.shape, bool), roi=(0, 0, y.shape[1], y.shape[0]))


@pytest.mark.parametrize("gain,lift", [(1.0, 0.0), (0.9, 12.0), (0.94, 12.0), (1.1, -12.0), (0.6, 40.0)])
def test_fit_blend_free_is_gain_independent(gain, lift):
    A, B = _tex(1), _tex(2)
    ones = np.ones(A.shape, bool)
    for alpha_a in (1.0, 0.8, 0.5, 0.2, 0.0):
        y = gain * (alpha_a * A + (1 - alpha_a) * B) + lift
        a, g, z = scoring.fit_blend_free(_region(y), A, B, ones)
        assert a == pytest.approx(alpha_a, abs=1e-6)
        assert g == pytest.approx(gain, rel=1e-6) and z == pytest.approx(1.0, abs=1e-9)
    # the gain-fixed fit is biased on the pure frames by (1 - g) / 2 (what tilted the crossfade window)
    a_fixed = scoring.fit_blend(_region(gain * A + lift), A, B, ones)[0]
    if gain != 1.0:
        assert abs(a_fixed - 1.0) > 0.01


def test_fit_blend_free_undefined_cases():
    A, B, C = _tex(1), _tex(2), _tex(3)
    ones = np.ones(A.shape, bool)
    assert math.isnan(scoring.fit_blend_free(_region(0.02 * A + 0.01 * B + 100), A, B, ones)[0])  # no blend of A/B
    assert math.isnan(scoring.fit_blend_free(_region(A), A, A.copy(), ones)[0])                    # A == B
    assert math.isnan(scoring.fit_blend_free(_region(C), A, B, np.zeros(A.shape, bool))[0])       # no pixels
    assert math.isnan(scoring.blend_alpha_cov(1.0, 1.0, 0.0, -0.5, -0.4)[0])                      # negative gain


def test_segment_vectorised_free_alpha_matches_scoring():
    """segment._Scorer.blend_fit's covariance form gives the same gain-free alpha as scoring.fit_blend_free."""
    from match_cuts.config import Config
    from match_cuts.geometry import Sim
    from match_cuts.model import Proxy
    from match_cuts.segment import _Scorer
    from match_cuts.scoring import _blur, prepare_comp, warp_to_roi
    from fractions import Fraction
    W, H = 192, 128
    bank = np.stack([np.clip(_tex(s, H // 2, W // 2), 0, 255).astype(np.uint8) for s in (5, 6)])
    comp = np.clip(np.round(0.9 * (0.3 * bank[0].astype(np.float32) + 0.7 * bank[1]) + 10), 0, 255).astype(np.uint8)
    cp = Proxy("competitor", "", comp[None], (W, H), (0.5, 0.5), Fraction(30), np.zeros(1), 1)
    rp = Proxy("raw", "", bank, (W, H), (0.5, 0.5), Fraction(30), np.zeros(1), 2)
    sc = _Scorer(cp, rp, None, None, Config(work_dir="/nonexistent_match_cuts_test"))
    sim = Sim.identity()
    r = sc.blend_fit(0, [(0, sim, False)], [(1, sim, False)])
    reg = prepare_comp(comp, sc.roi, sc.base_allowed, blur=1.0)
    wa, va = warp_to_roi(bank[0], sim, False, W, (0.5, 0.5), (0.5, 0.5), sc.roi)
    wb, vb = warp_to_roi(bank[1], sim, False, W, (0.5, 0.5), (0.5, 0.5), sc.roi)
    alpha, gain, _z = scoring.fit_blend_free(reg, _blur(wa, 1.0), _blur(wb, 1.0), va & vb)
    assert r["alpha_a_free"] == pytest.approx(alpha, abs=1e-6) and alpha == pytest.approx(0.3, abs=0.02)
    assert r["gain_free"] == pytest.approx(gain, abs=1e-6)
    assert abs(r["alpha_a"] - 0.3) > abs(r["alpha_a_free"] - 0.3)      # the constrained fit is the biased one
