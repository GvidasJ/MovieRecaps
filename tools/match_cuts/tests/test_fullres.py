"""fullres.py: the full-resolution pass (GPU) -- scoring a warped RAW frame, refining the framing, re-checking an
uncertain frame's time. Skipped without a CUDA GPU."""
from __future__ import annotations

import numpy as np
import pytest

from match_cuts import fullres, gpu
from match_cuts.geometry import Sim, to_cv_matrix
from match_cuts.model import FrameMap, Status

pytestmark = pytest.mark.skipif(gpu.available() is not None, reason=f"no GPU: {gpu.available()}")

RAW_W, RAW_H = 640, 360
COMP_W, COMP_H = 360, 640


class Store(fullres.FrameStore):
    """Frames given beforehand (no video file)."""

    def __init__(self, frames):
        self.frames = dict(frames)

    def load(self, idx):
        pass


def texture(seed=0):
    import cv2
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (RAW_H, RAW_W)).astype(np.uint8)
    return cv2.GaussianBlur(img, (0, 0), 2.0)


def raw_frame(j, base):
    """The static scene plus a bright square that moves 6 px a frame (no global shift explains it)."""
    img = base.copy()
    x = 200 + 6 * j
    img[150:190, x:x + 40] = 250
    return img


def comp_of(raw, sim):
    import cv2
    m = to_cv_matrix(sim, False, float(RAW_W))
    return cv2.warpAffine(raw, m, (COMP_W, COMP_H), flags=cv2.INTER_LINEAR)


SIM = Sim(1.4, 0.0, -150.0, 70.0)        # the RAW 1.4x, its middle in the portrait competitor frame


def test_a_warped_frame_scores_near_one_and_the_refinement_recovers_a_shift():
    base = texture()
    raw = raw_frame(5, base)
    comp = comp_of(raw, SIM)
    sc = fullres.Scorer((RAW_W, RAW_H), blur_px=1.0)
    mask = np.zeros((COMP_H, COMP_W), bool)
    mask[120:520, 30:330] = True
    prep = sc.comp(0, comp, mask)
    rr = sc.raw(5, raw)
    z = sc.score(prep, rr, sc.inverse_map(SIM, False))
    assert z > 0.995
    off = Sim(SIM.s, 0.0, SIM.tx + 2.5, SIM.ty - 1.5)           # 2.5 / 1.5 px off
    z_off = sc.score(prep, rr, sc.inverse_map(off, False))
    p, z_ref = sc.refine(prep, rr, sc.inverse_map(off, False))
    assert z_off < z - 0.01 and z_ref > 0.995
    assert 2.0 < sc.shift_px(p, prep[2]) < 4.0                   # the 2.9 px error found
    sc.close()


def make_fm(n=1, raw=5, soft=(4, 6)):
    fm = FrameMap(n)
    fm.status = np.full(n, int(Status.MATCH), np.int8)
    fm.raw = np.full(n, raw, np.int32)
    fm.raw_lo = np.full(n, raw, np.int32)
    fm.raw_hi = np.full(n, raw, np.int32)
    fm.soft_lo = np.full(n, soft[0], np.int32)
    fm.soft_hi = np.full(n, soft[1], np.int32)
    fm.low_margin = np.ones(n, bool)
    for k in range(n):
        fm.set_sim(k, SIM)
    return fm


def test_the_recheck_narrows_a_soft_range_to_the_frame_that_fits():
    base = texture()
    raws = {j: raw_frame(j, base) for j in range(2, 9)}
    comp = Store({0: comp_of(raws[5], SIM)})
    fm = make_fm(raw=4, soft=(4, 6))                             # refine's pick (4) is off by one, within noise
    mask = np.ones((COMP_H, COMP_W), bool)
    out, res = fullres.recheck(fm, comp, Store(raws), lambda k: mask, (RAW_W, RAW_H), 20, int(Status.MATCH))
    assert res["frames"] == 1 and res["narrowed"] == 1 and res["decided"] == 1
    assert (int(out.soft_lo[0]), int(out.soft_hi[0]), int(out.raw[0])) == (5, 5, 5)
    assert not bool(out.low_margin[0])
    assert (int(fm.soft_lo[0]), int(fm.soft_hi[0])) == (4, 6)  # the input is not changed


def test_a_pan_that_the_framing_explains_stays_undecided():
    import cv2
    base = texture()
    # a pure pan: frame j is the scene moved 3 px -- a neighbour with its own framing fits as well
    raws = {j: cv2.warpAffine(base, np.float32([[1, 0, 3 * j], [0, 1, 0]]), (RAW_W, RAW_H)) for j in range(2, 9)}
    comp = Store({0: comp_of(raws[5], SIM)})
    fm = make_fm(raw=5, soft=(4, 6))
    mask = np.zeros((COMP_H, COMP_W), bool)
    mask[150:490, 60:300] = True
    out, res = fullres.recheck(fm, comp, Store(raws), lambda k: mask, (RAW_W, RAW_H), 20, int(Status.MATCH))
    assert res["narrowed"] == 0 and (int(out.soft_lo[0]), int(out.soft_hi[0])) == (4, 6)


def test_the_recheck_keeps_refines_measured_range_inside_the_narrowed_one():
    """Deadpool 271: the re-check narrowed 2505-2510 to 2508-2509 but left refine's measured 2506 -- outside it: the
    segmenter then charged every line for leaving a frame it may not show. The measured range follows now."""
    base = texture()
    raws = {j: raw_frame(j, base) for j in range(2, 9)}
    comp = Store({0: comp_of(raws[5], SIM)})
    fm = make_fm(raw=4, soft=(4, 6))                             # refine measured 4 (raw_lo = raw_hi = 4)
    mask = np.ones((COMP_H, COMP_W), bool)
    out, res = fullres.recheck(fm, comp, Store(raws), lambda k: mask, (RAW_W, RAW_H), 20, int(Status.MATCH))
    assert (int(out.raw_lo[0]), int(out.raw[0]), int(out.raw_hi[0])) == (5, 5, 5)
    assert int(out.soft_lo[0]) <= int(out.raw_lo[0]) and int(out.raw_hi[0]) <= int(out.soft_hi[0])


def test_full_resolution_decides_against_the_proxy_only_when_clearly_sure():
    """Deadpool 446 (a frame inside a fast pan): the proxy said RAW 2745-2746; full resolution, following its score
    rising past the edge of its candidates, found 2749 far better -- it decides. Within OVERRULE it would not."""
    base = texture()
    raws = {j: raw_frame(j, base) for j in range(0, 12)}
    comp = Store({0: comp_of(raws[7], SIM)})
    fm = make_fm(raw=3, soft=(2, 3))                             # the proxy: 2-3; the picture: RAW 7
    mask = np.ones((COMP_H, COMP_W), bool)
    out, res = fullres.recheck(fm, comp, Store(raws), lambda k: mask, (RAW_W, RAW_H), 20, int(Status.MATCH))
    assert res["overruled"] == 1 and int(out.raw[0]) == 7 and (int(out.raw_lo[0]), int(out.raw_hi[0])) == (7, 7)
    assert int(out.soft_lo[0]) <= 7 <= int(out.soft_hi[0]) and not bool(out.low_margin[0])
    assert "full resolution chose RAW 7" in res["rows"][0]["result"]


def test_the_refined_framing_comes_back_as_a_sim_that_fits():
    base = texture()
    raw = raw_frame(5, base)
    comp = comp_of(raw, SIM)
    sc = fullres.Scorer((RAW_W, RAW_H), blur_px=1.0)
    mask = np.zeros((COMP_H, COMP_W), bool)
    mask[120:520, 30:330] = True
    prep, rr = sc.comp(0, comp, mask), sc.raw(5, raw)
    off = Sim(SIM.s * 1.004, 0.0, SIM.tx + 3.0, SIM.ty - 2.0)
    p, z_ref = sc.refine(prep, rr, sc.inverse_map(off, False))
    new = sc.refined_sim(off, False, p, prep[2])
    assert abs(sc.score(prep, rr, sc.inverse_map(new, False)) - z_ref) < 1e-6 and z_ref > 0.995
    assert abs(new.tx - SIM.tx) < 0.5 and abs(new.ty - SIM.ty) < 0.5 and abs(new.s / SIM.s - 1) < 1e-3
    sc.close()
