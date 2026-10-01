"""Unit tests for temporal.py (competitor-only temporal signature, DESIGN §5 temporal.py): pair alignment,
repeat / move / unknown / cut labels on numpy sequences -- a 23.976->30 cadence under an editor pan, a 30p
moving shot, a static noise plate, a cut, an accelerating pan whose slow part must not pass for repeats."""
from __future__ import annotations

import numpy as np
import pytest

from match_cuts import temporal
from match_cuts.config import Config
from match_cuts.geometry import Sim

import motion_fixtures as mf

ROI = (20, 15, 120, 90)          # x, y, w, h inside the 160x120 frames


def _getter(frames: np.ndarray, roi=ROI, caption: tuple | None = None):
    x, y, w, h = roi
    mask = np.ones((h, w), bool)
    if caption is not None:
        cy0, cy1 = caption
        mask[cy0:cy1, :] = False

    def get(k: int):
        if not (0 <= k < len(frames)):
            return None
        return temporal.prepare(frames[k][y:y + h, x:x + w].astype(np.float32), mask, 320, blur=1.0)
    return get


def _labels(frames: np.ndarray, cfg=None, **kw) -> temporal.Labels:
    cfg = cfg or Config()
    sig = temporal.measure(_getter(frames, **kw), range(len(frames)), cfg)
    return temporal.label_pairs(sig, cfg)


def test_align_pair_recovers_a_shift_and_scores_a_repeat():
    raw = mf.two_layer_raw(4)
    a = mf.render(raw, [1], [Sim(1.0, 0.0, -20.0, -15.0)], (160, 120), noise=0.0)[0]
    b = mf.render(raw, [1], [Sim(1.0, 0.0, -24.0, -13.0)], (160, 120), noise=0.0)[0]     # same image, moved
    c = mf.render(raw, [2], [Sim(1.0, 0.0, -24.0, -13.0)], (160, 120), noise=0.0)[0]     # next RAW frame
    d = mf.render(raw, [1], [Sim(1.0, 0.0, -23.5, -13.0)], (160, 120), noise=0.0)[0]     # half-pixel move
    g = _getter(np.stack([a, b, c, d]))
    pa, pb, pc, pd = g(0), g(1), g(2), g(3)
    rep = temporal.align_pair(pa[0], pa[1], pb[0], pb[1])
    assert rep.aligned and rep.cc > 0.9995
    assert rep.dx == pytest.approx(-4.0, abs=0.05) and rep.dy == pytest.approx(2.0, abs=0.05)
    half = temporal.align_pair(pa[0], pa[1], pd[0], pd[1])      # sub-pixel: interpolation smoothing only
    assert half.dx == pytest.approx(-3.5, abs=0.1) and half.cc > 0.995
    mov = temporal.align_pair(pa[0], pa[1], pc[0], pc[1])
    assert mov.r > 10 * half.r and mov.r > 100 * rep.r      # a non-rigid change survives the alignment


def test_align_pair_recovers_from_a_phase_correlation_alias(monkeypatch):
    """film24 pair 122 (wave 4): on a blocky periodic texture phase correlation returned an alias (-17.2, +17.4 px)
    for a true 6 px editor pan of the recreation; ECC from there did not converge and the pair read cc = -0.03 --
    'recreation jumps 27x more than the competitor' on a correct frame pair. The identity start rescues it: the
    same measurement as without the alias, aligned, on the true shift."""
    import cv2
    rng = np.random.default_rng(3)
    g0 = (rng.random((24, 30)) < 0.4).astype(np.float32)        # Game-of-Life-like blocky cells (8 px, nearest)
    flips = rng.random(g0.shape) < 0.05
    g1 = np.where(flips, 1.0 - g0, g0).astype(np.float32)          # the next generation
    cells = [cv2.resize(g, (240, 192), interpolation=cv2.INTER_NEAREST) * 180 + 30 for g in (g0, g1)]
    a = cv2.warpAffine(cells[0], np.float32([[1, 0, -20], [0, 1, -20]]), (160, 120))
    c = cv2.warpAffine(cells[1], np.float32([[1, 0, -26], [0, 1, -20]]), (160, 120))     # next frame, panned 6 px
    g = _getter(np.stack([a, c]))
    pa, pc = g(0), g(1)
    good = temporal.align_pair(pa[0], pa[1], pc[0], pc[1])
    assert good.aligned and good.dx == pytest.approx(-6.0, abs=0.2) and good.cc > 0.85
    monkeypatch.setattr(temporal, "_phase_shift", lambda *_a: (-17.16, 17.39))          # the alias start
    calls = []
    real_ecc = temporal._ecc_from
    monkeypatch.setattr(temporal, "_ecc_from", lambda *a_, **k_: calls.append(a_[4].copy()) or real_ecc(*a_, **k_))
    alias = temporal.align_pair(pa[0], pa[1], pc[0], pc[1])
    assert len(calls) >= 2 and np.allclose(calls[1], np.eye(2, 3))      # the identity start (coarse to fine) was tried
    assert alias.aligned and alias.dx == pytest.approx(good.dx, abs=0.05) and alias.cc == pytest.approx(good.cc, abs=1e-4)
    # an exact repeat never needs the second start (its phase shift IS the identity)
    calls.clear()
    monkeypatch.setattr(temporal, "_phase_shift", lambda *_a: (0.2, -0.1))
    rep = temporal.align_pair(pa[0], pa[1], pa[0], pa[1])
    assert len(calls) == 1 and rep.cc > 0.9999


def test_labels_on_a_pulldown_cadence_under_a_pan_match_the_truth():
    """(f) 23.976 -> 30 cadence (a RAW repeat every 5 competitor frames) plus an editor pan of 1.2 px/frame and
    encode noise: every labelled pair is right, and most pairs get a label."""
    n = 60
    raw = mf.two_layer_raw(60)
    js = mf.cadence(n, 0.8, 0.1, j0=3)
    frames = mf.render(raw, js, mf.pan_sims(n), (160, 120), noise=1.5)
    lab = _labels(frames)
    truth = {k: temporal.REPEAT if js[k] == js[k + 1] else temporal.MOVE for k in range(n - 1)}
    wrong = {k: (lab.get(k), t) for k, t in truth.items() if lab.get(k) not in (t, temporal.UNKNOWN)}
    assert not wrong, wrong
    labelled = [k for k in truth if lab.get(k) == truth[k]]
    assert len(labelled) >= 0.9 * len(truth)
    assert sum(1 for k in truth if truth[k] == temporal.REPEAT and lab.get(k) == temporal.REPEAT) >= 10
    sh = lab.shots[0]
    assert sh["mode"] == "split" and sh["floor"] < sh["threshold"]


def test_labels_ignore_a_masked_caption_change():
    """Word-by-word captions change between repeat frames; the layout mask removes them from the measurement."""
    n = 30
    raw = mf.two_layer_raw(30)
    js = mf.cadence(n, 0.8, 0.1)
    frames = mf.render(raw, js, mf.pan_sims(n), (160, 120), noise=1.0).copy()
    for k in range(n):                                   # a caption band whose text changes every frame
        frames[k, 60:72, 30:130] = np.random.default_rng(k).integers(0, 255, (12, 100))
    lab = _labels(frames, caption=(42, 60))              # rows 60..72 of the frame = ROI rows 45..57
    truth = {k: temporal.REPEAT if js[k] == js[k + 1] else temporal.MOVE for k in range(n - 1)}
    assert all(lab.get(k) in (truth[k], temporal.UNKNOWN) for k in truth)
    assert sum(1 for k in truth if lab.get(k) == temporal.REPEAT) >= 4


def test_static_noise_plate_is_unknown():
    """(f) A static picture with fresh noise on every frame: all pairs look alike -- no repeat / move claims."""
    n = 30
    raw = mf.two_layer_raw(1)
    frames = mf.render(raw, np.zeros(n, int), [Sim(1.0, 0.0, -20.0, -15.0)] * n, (160, 120), noise=2.0)
    lab = _labels(frames)
    assert set(lab.label.values()) == {temporal.UNKNOWN}
    assert lab.shots[0]["mode"] == "undecided"


def test_30p_moving_shot_is_move_and_a_cut_is_cut():
    """No repeats at all (one RAW frame per competitor frame): growth of the residual over two frames makes the
    pairs MOVE; a change of shot is CUT; nothing is called a repeat."""
    raw_a = mf.two_layer_raw(25, seed=5)
    raw_b = mf.two_layer_raw(25, seed=40)
    fa = mf.render(raw_a, np.arange(20), mf.pan_sims(20), (160, 120), noise=1.0)
    fb = mf.render(raw_b, np.arange(20), mf.pan_sims(20), (160, 120), noise=1.0)
    lab = _labels(np.concatenate([fa, fb]))
    assert lab.get(19) == temporal.CUT
    vals = [lab.get(k) for k in range(39) if k != 19]
    assert temporal.REPEAT not in vals
    assert vals.count(temporal.MOVE) >= 30


def test_accelerating_pan_slow_part_is_not_a_repeat():
    """A RAW whose motion is slow for 15 frames and fast afterwards gives two residual clusters; the slow pairs
    grow over two frames (they move), so they must not be labelled REPEAT."""
    import cv2
    rng_t = mf.texture(120, 600, 3)
    rng_u = mf.texture(120, 600, 4, sigma=2.0)
    frames = []
    pos1 = pos2 = 0.0
    for k in range(40):
        v1, v2 = (0.6, -0.3) if k < 15 else (5.0, -2.5)
        pos1 += v1
        pos2 += v2
        a = cv2.warpAffine(rng_t, np.array([[1, 0, -50 - pos1], [0, 1, 0]], np.float32), (160, 120))
        b = cv2.warpAffine(rng_u, np.array([[1, 0, -200 - pos2], [0, 1, 0]], np.float32), (160, 120))
        frames.append(np.clip(np.rint(0.8 * a + 0.2 * b + np.random.default_rng(k).normal(0, 1.0, a.shape)), 0, 255))
    lab = _labels(np.stack(frames).astype(np.uint8))
    assert temporal.REPEAT not in lab.label.values(), temporal.summary(lab)


def test_breaks_split_shots_and_summary():
    n = 30
    raw = mf.two_layer_raw(30)
    js = mf.cadence(n, 0.8, 0.1)
    frames = mf.render(raw, js, mf.pan_sims(n), (160, 120), noise=1.0)
    cfg = Config()
    sig = temporal.measure(_getter(frames), range(n), cfg)
    lab = temporal.label_pairs(sig, cfg, breaks=[14])
    assert lab.get(14) == temporal.UNKNOWN and len(lab.shots) == 2
    s = temporal.summary(lab)
    assert s["counts"][temporal.REPEAT] >= 4 and s["runs"][temporal.REPEAT]
