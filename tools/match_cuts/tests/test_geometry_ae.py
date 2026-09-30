"""Transform -> After Effects conversion checked against cv2.warpAffine renders (DESIGN §2.3, prompt 7.4).

Three independent routes must render identical pixels for random Sims, flips, r != 1 and a Video Box
origin:
  (a) geometry.to_cv_matrix at dst ratio r, minus the integer box origin;
  (b) geometry.sim_to_ae -> geometry.ae_to_matrix (CORNER) -> OpenCV;
  (c) an independent AE model written here: p = Position + R(rot) diag(sx, sy)/100 (p - Anchor), applied
      to the unflipped RAW, and a hand-derived matrix applied to np.fliplr(RAW) for flips.
Plus pixel-exact convention checks (integer shift of a flipped image, 2x upscale == cv2.resize) and
geometry.interpolate_keys vs AE-parameter interpolation with animated rotation.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from match_cuts.export_ae import box_geometry
from match_cuts.geometry import (AETransform, Sim, ae_to_matrix, interpolate_keys, sim_to_ae,
                                 to_cv_matrix)
from match_cuts.model import Box

RAW_W, RAW_H = 64, 48


def _texture(w: int, h: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = rng.uniform(0, 255, (h, w)).astype(np.float32)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    img[h // 3, w // 4] = 400.0          # a sharp marker makes misregistration obvious
    return img


def _corner_to_cv(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """CORNER affine p' = A p + b  ->  OpenCV matrix (pixel centres at integers)."""
    return np.hstack([A, (A @ np.array([0.5, 0.5]) + b - 0.5)[:, None]])


def _ae_model_cv(anchor, scale, rotation, position) -> np.ndarray:
    """Independent AE layer model (unflipped layer pixels -> comp pixels), as an OpenCV matrix."""
    th = math.radians(rotation)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    A = R @ np.diag([scale[0] / 100.0, scale[1] / 100.0])
    b = np.asarray(position, float) - A @ np.asarray(anchor, float)
    return _corner_to_cv(A, b)


def _ae_params_independent(sim: Sim, flip: bool, W: float, H: float, r: float, origin: tuple[int, int]):
    """DESIGN §2.3 written out by hand: Anchor = c, Scale = [(flip?-1:1) 100 s r, 100 s r], Rotation = th,
    Position = r (s R c + t) - origin."""
    c = np.array([W / 2.0, H / 2.0])
    th = math.radians(sim.theta_deg)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    pos = r * (sim.s * R @ c + np.array([sim.tx, sim.ty])) - np.asarray(origin, float)
    return c, ((-1.0 if flip else 1.0) * 100 * sim.s * r, 100 * sim.s * r), sim.theta_deg, pos


def _warp(img: np.ndarray, M: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpAffine(img, np.asarray(M, np.float64), size, flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _interior(img: np.ndarray, M: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Destination pixels whose bilinear footprint lies fully inside the source (no border mixing)."""
    ones = np.ones(img.shape[:2], np.float32)
    v = _warp(ones, M, size)
    return cv2.erode((v > 0.999).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0


def _random_case(rng: np.random.Generator, target: tuple[int, int]):
    s = float(rng.uniform(0.6, 1.8))
    th = float(rng.uniform(-10, 10))
    c = np.array([RAW_W / 2.0, RAW_H / 2.0])
    R = Sim(s, th, 0, 0).linear()
    ctr = np.array(target, float) / 2.0 + rng.uniform(-8, 8, 2)
    t = ctr - R @ c
    return Sim(s, th, float(t[0]), float(t[1]))


@pytest.mark.parametrize("seed", range(12))
def test_ae_render_matches_warpaffine_routes(seed):
    rng = np.random.default_rng(seed)
    raw = _texture(RAW_W, RAW_H, seed)
    flip = bool(seed % 2)
    r = [1.0, 0.5, 2.0 / 3.0, 1.5][seed % 4]
    Wc, Hc = 90, 120                                   # competitor frame (full res)
    sim = _random_case(rng, (Wc, Hc))
    origin = (int(rng.integers(0, 6)), int(rng.integers(0, 6)))     # integer Video Box origin (target px)
    Wt, Ht = int(round(Wc * r)), int(round(Hc * r))

    # (a) geometry.to_cv_matrix at dst ratio r, then the integer origin (pure translation)
    Ma = to_cv_matrix(sim, flip, RAW_W, (1.0, 1.0), (r, r)).copy()
    Ma[:, 2] -= origin
    # (b) sim_to_ae + ae_to_matrix (inside-the-box Position = r (s R c + t) - [bx0, by0])
    ae = sim_to_ae(sim, flip, RAW_W, RAW_H, r=r)
    ae_box = AETransform(ae.anchor, ae.scale, ae.rotation, (ae.position[0] - origin[0], ae.position[1] - origin[1]))
    Mb = _corner_to_cv(ae_to_matrix(ae_box)[:2, :2], ae_to_matrix(ae_box)[:2, 2])
    # (c1) independent AE parameter formula + independent AE layer model
    anchor, scale, rot, pos = _ae_params_independent(sim, flip, RAW_W, RAW_H, r, origin)
    np.testing.assert_allclose(ae_box.anchor, anchor, atol=1e-12)
    np.testing.assert_allclose(ae_box.scale, scale, rtol=1e-12)
    assert ae_box.rotation == pytest.approx(rot, abs=1e-12)
    np.testing.assert_allclose(ae_box.position, pos, atol=1e-9)
    Mc = _ae_model_cv(anchor, scale, rot, pos)
    # (c2) hand-derived matrix on np.fliplr(RAW): p_t = r (s R p' + t) - origin, p' = flipped CORNER coords
    A = r * sim.linear()
    Md = _corner_to_cv(A, r * np.array([sim.tx, sim.ty]) - np.asarray(origin, float))
    src_d = np.ascontiguousarray(np.fliplr(raw)) if flip else raw

    size = (Wt, Ht)
    ra, rb, rc, rd = _warp(raw, Ma, size), _warp(raw, Mb, size), _warp(raw, Mc, size), _warp(src_d, Md, size)
    m = _interior(raw, Ma, size)
    assert m.sum() > 0.3 * RAW_W * RAW_H * (sim.s * r) ** 2, "most of the warped RAW must land inside the target"
    for other in (rb, rc, rd):
        assert np.abs(ra - other)[m].max() < 1e-2
    np.testing.assert_allclose(Ma, Mb, atol=1e-9)
    np.testing.assert_allclose(Ma, Mc, atol=1e-9)


def test_flip_integer_shift_is_pixel_exact():
    """s = 1, theta = 0, integer t, flip: AE must show np.fliplr(RAW) moved by an integer offset, exactly
    (catches half-pixel errors in the flip / CORNER conventions)."""
    raw = _texture(RAW_W, RAW_H, 7)
    tx, ty, origin = 11, 5, (3, 2)
    ae = sim_to_ae(Sim(1.0, 0.0, tx, ty), True, RAW_W, RAW_H, r=1.0)
    M = _ae_model_cv(ae.anchor, ae.scale, ae.rotation, (ae.position[0] - origin[0], ae.position[1] - origin[1]))
    out = _warp(raw, M, (100, 80))
    dx, dy = tx - origin[0], ty - origin[1]
    expect = np.fliplr(raw)
    np.testing.assert_array_equal(out[dy:dy + RAW_H, dx:dx + RAW_W], expect)


def test_upscale_matches_cv_resize():
    """s = 2 at the origin in CORNER convention == cv2.resize (half-pixel-centre aligned) in the interior."""
    raw = _texture(RAW_W, RAW_H, 3)
    ae = sim_to_ae(Sim(2.0, 0.0, 0.0, 0.0), False, RAW_W, RAW_H)
    M = _ae_model_cv(ae.anchor, ae.scale, ae.rotation, ae.position)
    out = _warp(raw, M, (2 * RAW_W, 2 * RAW_H))
    ref = cv2.resize(raw, (2 * RAW_W, 2 * RAW_H), interpolation=cv2.INTER_LINEAR)
    assert np.abs(out[2:-2, 2:-2] - ref[2:-2, 2:-2]).max() < 1e-3


def test_rotation_is_clockwise_on_screen():
    """AE Rotation +90 turns the layer clockwise: a marker right of the anchor ends up below it."""
    img = np.zeros((41, 41), np.float32)
    img[20, 35] = 255.0                                   # right of the centre (20.5, 20.5)
    ae = sim_to_ae(Sim(1.0, 90.0, 0.0, 0.0).translated(0, 0), False, 41, 41)
    # position the anchor at the frame centre
    ae = AETransform(ae.anchor, ae.scale, ae.rotation, (20.5, 20.5))
    out = _warp(img, _ae_model_cv(ae.anchor, ae.scale, ae.rotation, ae.position), (41, 41))
    y, x = np.unravel_index(np.argmax(out), out.shape)
    assert (y, x) == (35, 20)


def test_video_box_precomp_composite_equals_direct_render():
    """Rendering into the integer Video Box pre-comp and placing it at (bx0, by0) in MAIN reproduces the
    direct MAIN render inside the box (fractional box, r = 2/3)."""
    raw = _texture(RAW_W, RAW_H, 11)
    r = 2.0 / 3.0
    box = Box(10.4, 21.7, 60.3, 70.9, 6.0)
    g = box_geometry(box, r)
    assert (g["bx0"], g["by0"]) == (math.floor(10.4 * r), math.floor(21.7 * r))
    assert g["bw"] == math.ceil((10.4 + 60.3) * r) - g["bx0"] and g["bh"] == math.ceil((21.7 + 70.9) * r) - g["by0"]
    assert g["mask"]["x"] == pytest.approx(10.4 * r - g["bx0"]) and g["mask"]["w"] == pytest.approx(60.3 * r)
    sim = Sim(0.9, 2.0, 15.0, 30.0)
    Wt, Ht = 60, 80
    for flip in (False, True):
        main_ae = sim_to_ae(sim, flip, RAW_W, RAW_H, r=r)
        direct = _warp(raw, _ae_model_cv(main_ae.anchor, main_ae.scale, main_ae.rotation, main_ae.position), (Wt, Ht))
        pos_in_box = (main_ae.position[0] - g["bx0"], main_ae.position[1] - g["by0"])
        ae_o = sim_to_ae(sim, flip, RAW_W, RAW_H, r=r, origin=(g["bx0"] / r, g["by0"] / r))
        np.testing.assert_allclose(pos_in_box, ae_o.position, atol=1e-9)
        pre = _warp(raw, _ae_model_cv(main_ae.anchor, main_ae.scale, main_ae.rotation, pos_in_box), (g["bw"], g["bh"]))
        canvas = np.zeros((Ht, Wt), np.float32)
        x0, y0 = g["bx0"], g["by0"]
        canvas[y0:y0 + g["bh"], x0:x0 + g["bw"]] = pre[:Ht - y0, :Wt - x0]
        sl = (slice(y0, min(Ht, y0 + g["bh"])), slice(x0, min(Wt, x0 + g["bw"])))
        # identical up to OpenCV's fixed-point coordinate rounding (grey levels 0..400)
        assert np.abs(canvas[sl] - direct[sl]).max() < 1e-2


def _ae_lerp_matrix(ka: dict, kb: dict, u: float, W: float, H: float) -> np.ndarray:
    """Independent AE keyframe interpolation: Anchor fixed, Scale / Rotation / Position linear."""
    pa = _ae_params_independent(Sim.from_dict(ka), False, W, H, 1.0, (0, 0))
    pb = _ae_params_independent(Sim.from_dict(kb), False, W, H, 1.0, (0, 0))
    scale = tuple(a + u * (b - a) for a, b in zip(pa[1], pb[1]))
    rot = pa[2] + u * (pb[2] - pa[2])
    pos = pa[3] + u * (pb[3] - pa[3])
    th = math.radians(rot)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    A = R @ np.diag([scale[0] / 100.0, scale[1] / 100.0])
    m = np.eye(3)
    m[:2, :2] = A
    m[:2, 2] = pos - A @ pa[0]
    return m


def test_interpolate_keys_matches_ae_parameter_interpolation_with_rotation():
    W, H = 1920, 1080
    ka = {"comp_frame": 0, "scale": 1.0, "rotation_deg": 10.0, "tx": 0.0, "ty": 0.0}
    kb = {"comp_frame": 10, "scale": 1.2, "rotation_deg": 14.0, "tx": 50.0, "ty": 20.0}
    worst_naive = 0.0
    for k in (0, 2.5, 5, 7.5, 10):
        u = k / 10.0
        want = _ae_lerp_matrix(ka, kb, u, W, H)
        got = interpolate_keys([ka, kb], k, W, H).matrix()
        np.testing.assert_allclose(got, want, atol=1e-8)
        naive = Sim(1.0 + 0.2 * u, 10 + 4 * u, 50 * u, 20 * u).matrix()
        corners = np.array([[0, 0, 1], [W, 0, 1], [0, H, 1], [W, H, 1]], float).T
        worst_naive = max(worst_naive, float(np.abs((naive - want) @ corners).max()))
    # sensitivity: linear (s, theta, t) interpolation is visibly wrong here (DESIGN review: ~3 px)
    assert worst_naive > 1.0


def test_interpolate_keys_constant_rotation_is_linear_and_renders_identically():
    W, H = RAW_W, RAW_H
    ka = {"comp_frame": 100, "scale": 0.8, "rotation_deg": 3.0, "tx": 5.0, "ty": 9.0}
    kb = {"comp_frame": 120, "scale": 1.1, "rotation_deg": 3.0, "tx": -4.0, "ty": 2.0}
    raw = _texture(W, H, 5)
    for k in (105, 110, 117):
        u = (k - 100) / 20.0
        sim = interpolate_keys([kb, ka], k)                      # unsorted input, no raw size needed
        assert sim.s == pytest.approx(0.8 + 0.3 * u) and sim.tx == pytest.approx(5.0 - 9.0 * u)
        want = _ae_lerp_matrix(ka, kb, u, W, H)
        np.testing.assert_allclose(sim.matrix(), want, atol=1e-9)
        a = _warp(raw, _corner_to_cv(want[:2, :2], want[:2, 2]), (90, 70))
        b = _warp(raw, to_cv_matrix(sim, False, W), (90, 70))
        assert np.abs(a - b).max() < 1e-2
    # outside the key range AE holds the first / last key
    assert interpolate_keys([ka, kb], 50).s == pytest.approx(0.8)
    assert interpolate_keys([ka, kb], 500).s == pytest.approx(1.1)
