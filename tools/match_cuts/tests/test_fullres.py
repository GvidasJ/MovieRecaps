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


# ---------------------------------------------------------------------------------------------------------------------
# Task 9: the scorer samples each framing once (the score at it and the next step from it share the samples), finds a
# mask's pixels once, caches the pixel grid -- the numbers must be the ones the earlier code gave, bit for bit
# ---------------------------------------------------------------------------------------------------------------------

def _old_coords(sc, A, p, roi):
    t = sc.t
    x0, y0, w, h = roi
    cx, cy = x0 + 0.5 * (w - 1), y0 + 0.5 * (h - 1)
    ys, xs = t.meshgrid(t.arange(h, device=sc.dev, dtype=t.float32) + (y0 - cy),
                        t.arange(w, device=sc.dev, dtype=t.float32) + (x0 - cx), indexing="ij")
    a, b, tx, ty = (float(v) for v in p)
    X = (1.0 + a) * xs - b * ys + tx + cx
    Y = b * xs + (1.0 + a) * ys + ty + cy
    u = float(A[0, 0]) * X + float(A[0, 1]) * Y + float(A[0, 2])
    v = float(A[1, 0]) * X + float(A[1, 1]) * Y + float(A[1, 2])
    return u, v, xs, ys


def _old_zncc(t, a, b, m):
    n = int(m.sum())
    if n < fullres.MIN_PIXELS:
        return float("nan")
    a = a[m].double()
    b = b[m].double()
    a = a - a.mean()
    b = b - b.mean()
    den = t.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if float(den) > 1e-9 else float("nan")


def _old_score(sc, comp, raw, A, p=None):
    T, M, roi = comp
    img = raw[0]
    H, W = img.shape
    u, v, _, _ = _old_coords(sc, A, np.zeros(4) if p is None else p, roi)
    valid = (u >= 1.0) & (u <= W - 2.0) & (v >= 1.0) & (v <= H - 2.0)
    return _old_zncc(sc.t, T, sc._sample(img, u, v), M & valid)


def _old_refine(sc, comp, raw, A, iters=fullres.REFINE_ITERS):
    """fullres.Scorer.refine as it was before Task 9 (the reference)."""
    t = sc.t
    T, M, roi = comp
    img, gu, gv = raw
    H, W = img.shape
    p = np.zeros(4)
    best_p, best_z = p.copy(), _old_score(sc, comp, raw, A, p)
    A2 = t.tensor(A[:, :2], dtype=t.float32, device=sc.dev)
    for _ in range(int(iters)):
        u, v, xs, ys = _old_coords(sc, A, p, roi)
        valid = (u >= 1.0) & (u <= W - 2.0) & (v >= 1.0) & (v <= H - 2.0)
        m = M & valid
        if int(m.sum()) < fullres.MIN_PIXELS:
            break
        I = sc._sample(img, u, v)
        Iu = sc._sample(gu, u, v)
        Iv = sc._sample(gv, u, v)
        Tm, Im = T[m], I[m]
        ts, is_ = Tm.std() + 1e-6, Im.std() + 1e-6
        e = (Tm - Tm.mean()) / ts - (Im - Im.mean()) / is_
        gx = (A2[0, 0] * Iu[m] + A2[1, 0] * Iv[m]) / is_
        gy = (A2[0, 1] * Iu[m] + A2[1, 1] * Iv[m]) / is_
        X, Y = xs[m], ys[m]
        J = t.stack([gx * X + gy * Y, -gx * Y + gy * X, gx, gy], dim=1)
        Hm = (J.T @ J).double().cpu().numpy()
        g = (J.T @ e).double().cpu().numpy()
        try:
            dp = np.linalg.solve(Hm + 1e-9 * np.eye(4), g)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(dp)):
            break
        p = p + dp
        z = _old_score(sc, comp, raw, A, p)
        if np.isfinite(z) and (not np.isfinite(best_z) or z > best_z):
            best_p, best_z = p.copy(), z
        if abs(dp[2]) < 0.01 and abs(dp[3]) < 0.01 and abs(dp[0]) < 1e-5 and abs(dp[1]) < 1e-5:
            break
    return best_p, float(best_z)


def _same(a, b):
    return (np.isnan(a) and np.isnan(b)) or a == b


def test_the_scorer_gives_the_numbers_it_gave_before_bit_for_bit():
    base = texture(3)
    sc = fullres.Scorer((RAW_W, RAW_H), blur_px=1.0)
    full = np.zeros((COMP_H, COMP_W), bool)
    full[120:520, 30:330] = True
    holes = full.copy()
    holes[300:340, 60:300] = False                         # a caption band left out
    tiny = np.zeros((COMP_H, COMP_W), bool)
    tiny[300:330, 100:200] = True                          # 3,000 pixels: under MIN_PIXELS
    cases = 0
    for k, (j, mask, d) in enumerate([(5, full, Sim(1.0, 0.0, 3.0, -2.0)), (6, holes, Sim(1.012, 0.3, -1.5, 4.0)),
                                      (9, full, Sim(0.99, -0.4, 6.0, 1.0)), (7, tiny, Sim(1.0, 0.0, 1.0, 1.0))]):
        raw = raw_frame(j, base)
        comp = comp_of(raw, SIM)
        prep = sc.comp(k, comp, mask)
        if prep is None:                                   # too few pixels for a competitor ROI at all
            continue
        rr = sc.raw(j, raw)
        shifted = Sim(SIM.s * d.s, SIM.theta_deg + d.theta_deg, SIM.tx + d.tx, SIM.ty + d.ty)
        A = sc.inverse_map(shifted, False)
        p_new, z_new, z0 = sc.refine(prep, rr, A, start=True)
        p_old, z_old = _old_refine(sc, prep, rr, A)
        assert np.array_equal(p_new, p_old) and _same(z_new, z_old), (k, p_new, p_old, z_new, z_old)
        assert _same(z0, _old_score(sc, prep, rr, A)) and _same(sc.score(prep, rr, A, p_new), _old_score(sc, prep, rr, A, p_new))
        p2, z2 = sc.refine(prep, rr, A)                     # without the start score: the same
        assert np.array_equal(p2, p_new) and _same(z2, z_new)
        cases += 1
    assert cases >= 3
    sc.close()


def test_the_scorers_gpu_caches_stay_within_their_bytes_and_give_the_same_numbers(monkeypatch):
    """Task 10: the GPU copies a Scorer keeps (RAW frames with their gradients, competitor ROIs) are bounded in bytes --
    96 RAW frames of a 1080p RAW were 2.4 GB in each of the 4 GPU processes. With room for only 3 RAW frames and one
    ROI, every score is the one the full caches give, bit for bit (a dropped frame is computed again the same way)."""
    base = texture(7)
    mask = np.zeros((COMP_H, COMP_W), bool)
    mask[120:520, 30:330] = True
    frames = {j: raw_frame(j, base) for j in range(12)}
    comps = {k: comp_of(frames[k], SIM) for k in range(12)}

    def scores(sc):
        out = []
        for k in list(range(12)) + list(range(11, -1, -1)):     # forwards, then back: dropped frames are asked again
            prep = sc.comp(k, comps[k], mask)
            for j in (k - 1, k, k + 1):
                if 0 <= j < 12:
                    out.append(sc.score(prep, sc.raw(j, frames[j]), sc.inverse_map(SIM, False)))
        return out
    sc = fullres.Scorer((RAW_W, RAW_H), blur_px=1.0)
    want = scores(sc)
    assert len(sc._raw) == 12 and len(sc._comp) == 12           # this RAW: the default bounds keep every frame
    sc.close()
    per_raw = 3 * RAW_W * RAW_H * 4
    monkeypatch.setattr(fullres, "RAW_CACHE_BYTES", 3 * per_raw)
    monkeypatch.setattr(fullres, "COMP_CACHE_BYTES", 1)
    sc = fullres.Scorer((RAW_W, RAW_H), blur_px=1.0)
    assert scores(sc) == want
    assert len(sc._raw) == 3 and sc._raw_bytes == 3 * per_raw == fullres._nbytes([x for v in sc._raw.values() for x in v])
    assert len(sc._comp) == 1
    sc.close()
    assert not sc._raw and sc._raw_bytes == sc._comp_bytes == 0


def test_the_run_gives_back_its_speech_models_before_its_gpu_processes(caplog):
    """Task 10: video4's full-resolution check started its 4 GPU processes next to the 6.6 GB the run's own process
    still held (the speech map's and the captions' models) -- 15.4 of the card's 16.3 GB, 4 times slower. The run now
    unloads them first (loaded again if a later step needs one) and says how much memory is free."""
    import logging
    from match_cuts import align, asr, pipeline

    class Engine:
        unloaded = False

        def unload(self):
            Engine.unloaded = True
    asr._LOADED["test-engine"] = Engine()
    align._MODELS["test-device"] = ("a model", {})
    with caplog.at_level(logging.INFO, logger="match_cuts"):
        pipeline.release_gpu_memory("9.9 full resolution")
    assert Engine.unloaded and "test-engine" not in asr._LOADED and "test-device" not in align._MODELS
    assert "9.9 full resolution:" in caplog.text and "GB of GPU memory free for its processes" in caplog.text


def test_the_recheck_in_several_gpu_processes_decides_exactly_as_in_one():
    """Task 9: the uncertain frames scored in processes sharing the GPU (a run of frames each) give every frame the
    same scores, so the same decisions, as one process."""
    base = texture(5)
    raws = {j: raw_frame(j, base) for j in range(0, 24)}
    shown = [5, 7, 9, 11, 12, 14, 16, 18]
    comp = {k: comp_of(raws[j], SIM) for k, j in enumerate(shown) if k != 6}     # frame 6 cannot be read
    fm = make_fm(n=len(shown), raw=0, soft=(0, 0))
    for k, j in enumerate(shown):
        fm.raw[k] = fm.raw_lo[k] = fm.raw_hi[k] = j - 1 if k % 2 else j
        fm.soft_lo[k], fm.soft_hi[k] = j - 1, j + 1 + (k % 3)
    full = np.ones((COMP_H, COMP_W), bool)
    band = full.copy()
    band[300:340, :] = False
    masks = {k: (band if k in (2, 5) else full) for k in range(len(shown))}
    one, r1 = fullres.recheck(fm, Store(comp), Store(raws), lambda k: masks[k], (RAW_W, RAW_H), 24, int(Status.MATCH))
    many, r3 = fullres.recheck(fm, Store(comp), Store(raws), lambda k: masks[k], (RAW_W, RAW_H), 24,
                               int(Status.MATCH), workers=3)
    assert r3.get("workers") == 3 and r1["frames"] == r3["frames"] == len(shown)
    for col in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi", "low_margin"):
        assert np.array_equal(getattr(one, col), getattr(many, col)), col
    assert r1["rows"] == r3["rows"] and len(r1["rows"]) == len(shown) - 1


def test_requests_computed_beforehand_in_gpu_processes_are_the_ones_computed_when_asked(synthetic_mini, tmp_path,
                                                                                       monkeypatch):
    """Task 9: the segmenter's full-resolution requests prefetched in processes sharing the GPU give exactly what
    asking for each one then gives; and measure keeps the first start of each (k, j, flip), as before."""
    from match_cuts import probe
    comp = probe.probe(str(synthetic_mini["competitor"]), "competitor", tmp_path, decode=True)
    raw = probe.probe(str(synthetic_mini["raw"]), "raw", tmp_path, decode=True)
    wh = (float(raw.width), float(raw.height))
    mask = np.ones((int(comp.height), int(comp.width)), bool)
    base = Sim(float(comp.height) / float(raw.height), 0.0, 0.0, 0.0)
    calls = [(k, [(j, base, False), (j + 1, base.translated(2.0, -1.0), False)], bool(k % 2)) for k, j in
             zip(range(2, 18), range(5, 21))]
    meas = [(k, j, base.translated(0.5 * (k % 3), 0.0), False) for k, j in zip(range(2, 18), range(5, 21))]
    meas.append((4, 7, base.translated(9.0, 9.0), False))          # asked again with another start: the first decides
    monkeypatch.setattr(fullres, "PREFETCH_MIN", 4)
    one = fullres.SideScorer(comp, raw, lambda k: mask, wh, int(raw.nb_frames))
    many = fullres.SideScorer(comp, raw, lambda k: mask, wh, int(raw.nb_frames), workers=2)
    try:
        many.prefetch(calls)
        many.prefetch_measure(meas)
        assert many._pool is not None and len(many._called) == len(calls)
        for k, items, refine in calls:
            a, b = one(k, items, refine), many(k, items, refine)
            assert (a is None and b is None) or np.array_equal(a, b), (k, a, b)
        for k, j, sim, flip in meas:
            a, b = one.measure(k, j, sim, flip), many.measure(k, j, sim, flip)
            assert (a is None) == (b is None)
            if a is not None:
                assert (a[0].s, a[0].theta_deg, a[0].tx, a[0].ty, a[1], a[2]) == \
                       (b[0].s, b[0].theta_deg, b[0].tx, b[0].ty, b[1], b[2]), (k, j)
        assert one.calls == many.calls
    finally:
        one.close()
        many.close()


def test_verifys_frames_measured_in_gpu_processes_are_the_ones_measured_here(synthetic_mini, tmp_path):
    """Task 9: 9.9's per-frame measurement (as delivered, refined, the neighbours; a blend of two RAW frames too) in
    processes sharing the GPU gives exactly what this process gives."""
    from match_cuts import probe
    comp = probe.probe(str(synthetic_mini["competitor"]), "competitor", tmp_path, decode=True)
    raw = probe.probe(str(synthetic_mini["raw"]), "raw", tmp_path, decode=True)
    wh = (float(raw.width), float(raw.height))
    mask = np.ones((int(comp.height), int(comp.width)), bool)
    base = Sim(float(comp.height) / float(raw.height), 0.0, 0.0, 0.0)
    reqs = [("vframe", k, j, (0.25 if k % 4 == 0 else 0.0), base.translated(0.3 * (k % 3), 0.0), False)
            for k, j in zip(range(1, 15), range(3, 17))]
    side = fullres.SideScorer(comp, raw, lambda k: mask, wh, int(raw.nb_frames))
    pool = fullres.GpuPool(2, comp, raw, wh, int(raw.nb_frames))
    try:
        got = pool.run(reqs, lambda k: mask)
        for r, b in zip(reqs, got):
            a = fullres._verify_frame(side.sc, side.comp.get, side.raw_get, mask, r[1], r[2], r[3], r[4], r[5])
            assert (a is None) == (b is None)
            if a is not None:
                assert a[:3] == b[:3] and a[3] == b[3], (r, a, b)
    finally:
        pool.close()
        side.close()
