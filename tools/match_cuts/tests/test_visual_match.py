"""Tests for match_cuts.visual_match (Stage 5.2) on a synthetic numpy scene.

The scene (``make_scene``) is shared with tests/test_refine.py (imported by path):
RAW = 4 shots x 60 frames of upscaled Game-of-Life cells + moving blobs + a burned-in counter
(960x540 "full res", 30000/1001 fps, proxy 480x270); competitor = 540x960 @ 30 fps, rendered with our
own cv2 renderer by warping RAW frames with known Sims into a rounded box on black (proxy 270x480):
two framings of shot 0, a 5-frame jump cut, a 1-frame flash cut from shot 3, a flipped segment, a
NOT-IN-RAW run, a uniform black frame, a 1.10x segment (AE floor rule), a rotated re-use of shot 0 and
a caption-like overlay.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
from fractions import Fraction

import cv2
import numpy as np
import pytest

from match_cuts.common import Cache, DecisionLog
from match_cuts.config import Config
from match_cuts.geometry import Sim, rounded_rect_mask, to_cv_matrix
from match_cuts.model import AudioHints, Box, Layout, Proxy
from match_cuts import visual_match as vm

RAW_FPS = Fraction(30000, 1001)
COMP_FPS = Fraction(30)
RAW_FULL = (960, 540)
COMP_FULL = (540, 960)
PR = 0.5                                   # proxy ratio of both proxies
BOX = Box(30, 230, 480, 450, 20)           # competitor full-res CORNER coords
SHOT_LEN = 60


def _life_step(g: np.ndarray) -> np.ndarray:
    n = sum(np.roll(np.roll(g, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
    return (n == 3) | (g & (n == 2))


def _sprite(r: float) -> np.ndarray:
    s = int(3 * r)
    yy, xx = np.mgrid[-s:s + 1, -s:s + 1].astype(np.float32)
    return 90.0 * np.exp(-(xx ** 2 + yy ** 2) / (2 * r * r))


def make_shot(seed: int, nframes: int, start: int, upd: float = 0.04) -> list[np.ndarray]:
    """Temporally unique textured frames (full res 960x540, uint8)."""
    W, H = RAW_FULL
    rng = np.random.default_rng(seed)
    cw, ch = 120, 68                                   # 8 px cells -> 4 px at the RAW proxy
    g = rng.random((ch, cw)) < 0.35
    tint = rng.integers(20, 110, (ch, cw)).astype(np.float32)
    alive = rng.integers(150, 250, (ch, cw)).astype(np.float32)
    blobs = [(rng.uniform(100, W - 100), rng.uniform(100, H - 100), rng.uniform(-2, 2), rng.uniform(-2, 2),
              _sprite(rng.uniform(12, 30))) for _ in range(4)]
    out = []
    for t in range(nframes):
        img = cv2.resize(np.where(g, alive, tint), (W, H), interpolation=cv2.INTER_NEAREST)
        for x0, y0, vx, vy, sp in blobs:
            cx = int(round(x0 + vx * t)) % (W - 200) + 100
            cy = int(round(y0 + vy * t)) % (H - 200) + 100
            s = sp.shape[0] // 2
            img[cy - s:cy + s + 1, cx - s:cx + s + 1] += sp
        img = np.clip(img, 0, 255).astype(np.uint8)
        txt = f"{start + t:05d}"
        cv2.putText(img, txt, (W // 2 - 150, H // 2 + 25), cv2.FONT_HERSHEY_SIMPLEX, 2.4, 255, 9, cv2.LINE_AA)
        cv2.putText(img, txt, (W // 2 - 150, H // 2 + 25), cv2.FONT_HERSHEY_SIMPLEX, 2.4, 0, 3, cv2.LINE_AA)
        out.append(img)
        g = np.where(rng.random((ch, cw)) < upd, _life_step(g), g)
    return out


def centred_sim(s: float, theta_deg: float, raw_pt=(480.0, 270.0), comp_pt=(270.0, 455.0)) -> Sim:
    """Sim with scale s / rotation theta mapping RAW point raw_pt (after flip) to comp point comp_pt."""
    lin = Sim(s, theta_deg, 0.0, 0.0).apply(raw_pt)[0]
    return Sim(s, theta_deg, comp_pt[0] - lin[0], comp_pt[1] - lin[1])


def ae_frame(raw_in_frames: float, v: float, k: int, comp_in: int) -> int:
    """AE floor rule with raw_in given in RAW frames: floor(raw_fps (raw_in/raw_fps + v (t_k - t_in)))."""
    return int(math.floor(raw_in_frames + v * float(RAW_FPS / COMP_FPS) * (k - comp_in) + 1e-9))


class Overlays:
    """Minimal OverlayMasks stand-in (bool masks at comp proxy res)."""

    def __init__(self):
        self.m: dict[int, np.ndarray] = {}

    def get(self, k):
        return self.m.get(int(k))

    def set(self, k, mask):
        self.m[int(k)] = np.asarray(mask, bool)

    def union(self, k, mask):
        k = int(k)
        self.m[k] = self.m[k] | np.asarray(mask, bool) if k in self.m else np.asarray(mask, bool).copy()

    def frames(self):
        return sorted(self.m)


def make_scene(seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    src = []
    for s in range(4):
        src += make_shot(100 + s, SHOT_LEN, s * SHOT_LEN)
    src = np.stack(src)
    n_raw = len(src)
    rw, rh = int(RAW_FULL[0] * PR), int(RAW_FULL[1] * PR)
    raw_frames = np.stack([cv2.resize(f, (rw, rh), interpolation=cv2.INTER_AREA) for f in src])
    raw = Proxy("raw", "", raw_frames, RAW_FULL, (rw / RAW_FULL[0], rh / RAW_FULL[1]), RAW_FPS,
                np.arange(n_raw) / float(RAW_FPS), n_raw)

    simA = centred_sim(480 / 576, 0.0)
    simB = centred_sim(0.9, 0.0)                    # flipped segment (Sim of the flipped RAW)
    simC = centred_sim(0.87, 0.0)
    simD = centred_sim(0.95, 1.5)
    truth: list[dict] = []

    def add(n, **kw):
        for _ in range(n):
            truth.append(dict(kw))
    # seg1: shot 0, raw_in 5.5 frames, v=1, simA  (k 0..29)
    for k in range(0, 30):
        truth.append(dict(kind="raw", raw=ae_frame(5.5, 1.0, k, 0), sim=simA, flip=False, seg=1))
    # seg2: jump cut skipping 5 RAW frames (k 30..49), 1-frame flash cut from shot 3 at k=40
    j_last = truth[-1]["raw"]
    for k in range(30, 50):
        truth.append(dict(kind="raw", raw=ae_frame(j_last + 6 + 0.5, 1.0, k, 30), sim=simA, flip=False, seg=2))
    truth[40] = dict(kind="raw", raw=3 * SHOT_LEN + 20, sim=simA, flip=False, seg=3)
    # seg4: flipped, shot 1 (k 50..69)
    for k in range(50, 70):
        truth.append(dict(kind="raw", raw=ae_frame(SHOT_LEN + 10.5, 1.0, k, 50), sim=simB, flip=True, seg=4))
    # NOT-IN-RAW (k 70..77) and one uniform black frame (k 78)
    add(8, kind="none", seg=5)
    add(1, kind="uniform", seg=6)
    # seg7: 1.10x, shot 2 (k 79..103)
    for k in range(79, 104):
        truth.append(dict(kind="raw", raw=ae_frame(2 * SHOT_LEN + 5.3, 1.10, k, 79), sim=simC, flip=False, seg=7))
    # seg8: re-use of shot 0 with another (rotated) framing (k 104..129)
    for k in range(104, 130):
        truth.append(dict(kind="raw", raw=ae_frame(10.5, 1.0, k, 104), sim=simD, flip=False, seg=8))
    N = len(truth)

    cw, chh = COMP_FULL
    cov = np.zeros((chh, cw), np.float32)
    bx, by, bw, bh = int(BOX.x), int(BOX.y), int(BOX.w), int(BOX.h)
    cov[by:by + bh, bx:bx + bw] = rounded_rect_mask(bw, bh, BOX.corner_radius)
    caption_frames = set(range(10, 61))
    comp_full = []
    for k, t in enumerate(truth):
        if t["kind"] == "raw":
            m = to_cv_matrix(t["sim"], t["flip"], RAW_FULL[0], (1.0, 1.0), (1.0, 1.0))
            img = cv2.warpAffine(src[t["raw"]].astype(np.float32), m, (cw, chh), flags=cv2.INTER_LINEAR)
        elif t["kind"] == "none":
            noise = cv2.resize(rng.random((24, 14)).astype(np.float32) * 255, (cw, chh), interpolation=cv2.INTER_CUBIC)
            img = np.clip(noise + rng.normal(0, 25, (chh, cw)), 0, 255).astype(np.float32)
        else:
            img = np.zeros((chh, cw), np.float32)
        img = img * cov + rng.normal(0, 2.0, img.shape).astype(np.float32) * (t["kind"] != "uniform")
        img = np.clip(img, 0, 255).astype(np.uint8)
        if k in caption_frames:
            cv2.putText(img, "CAPTION WORDS", (60, 600), cv2.FONT_HERSHEY_DUPLEX, 1.5, 0, 10, cv2.LINE_AA)
            cv2.putText(img, "CAPTION WORDS", (60, 600), cv2.FONT_HERSHEY_DUPLEX, 1.5, 255, 3, cv2.LINE_AA)
        comp_full.append(img)
    pw, ph = int(cw * PR), int(chh * PR)
    comp_frames = np.stack([cv2.resize(f, (pw, ph), interpolation=cv2.INTER_AREA) for f in comp_full])
    comp = Proxy("competitor", "", comp_frames, COMP_FULL, (pw / cw, ph / chh), COMP_FPS,
                 np.arange(N) / float(COMP_FPS), N)
    layout = Layout(comp_w=cw, comp_h=chh, box=BOX, proxy_ratio=comp.ratio)
    caption_mask = np.zeros((ph, pw), bool)
    caption_mask[int(560 * PR):int(615 * PR), int(50 * PR):int(420 * PR)] = True
    return dict(raw=raw, comp=comp, layout=layout, truth=truth, src=src, caption_frames=caption_frames,
                caption_mask=caption_mask, sims=dict(A=simA, B=simB, C=simC, D=simD))


def make_config(tmp_path, workers: int = 2) -> Config:
    """The CPU path: the sampled index searched with FLANN (the tests of the GPU path build their own config)."""
    cfg = Config(work_dir=str(tmp_path / "work"), out_dir=str(tmp_path / "out")).apply_fast()
    cfg.gpu = False
    cfg.workers = workers
    return cfg




def sim_error(est: Sim, truth: Sim) -> tuple[float, float]:
    """(relative scale error, max position error in comp full-res px over the box corners)."""
    corners = np.array([[BOX.x, BOX.y], [BOX.x + BOX.w, BOX.y], [BOX.x, BOX.y + BOX.h],
                        [BOX.x + BOX.w, BOX.y + BOX.h]], np.float64)
    p = truth.inverse().apply(corners)
    d = est.apply(p) - corners
    return abs(est.s / truth.s - 1.0), float(np.max(np.hypot(d[:, 0], d[:, 1])))


# ----------------------------------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scene():
    return make_scene(0)


@pytest.fixture(scope="module")
def built(scene, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("vm")
    cfg = make_config(tmp)
    cache = Cache(tmp / "work")
    index = vm.RawIndex.build(scene["raw"], cfg, cache)
    return dict(cfg=cfg, cache=cache, index=index, tmp=tmp)


def _allowed(scene, cfg, caption=True):
    am = vm.AllowedMasks(scene["layout"], None, scene["comp"], cfg, use_layout_module=False)
    if caption:
        for k in scene["caption_frames"]:
            am.add_extra(k, scene["caption_mask"])
    return am


# ----------------------------------------------------------------------------------------------
# tests
# ----------------------------------------------------------------------------------------------

def test_parallel_map_order_and_determinism():
    def fn(state, x):
        return (x, state["mul"] * x, cv2.getNumThreads())
    items = list(range(40))
    a = vm.parallel_map(fn, items, 3, {"mul": 7}, seed=1)
    b = vm.parallel_map(fn, items, 1, {"mul": 7}, seed=1)
    assert [r[:2] for r in a] == [r[:2] for r in b] == [(x, 7 * x) for x in items]
    # OpenCV threads restored in the parent after the pool
    assert cv2.getNumThreads() >= 1


def test_raw_index_build_uint8_and_cache(scene, built):
    idx, cfg, cache = built["index"], built["cfg"], built["cache"]
    raw = scene["raw"]
    step = round(float(RAW_FPS) / cfg.raw_index_fps_short)
    assert idx.step == step == 3
    assert list(idx.frames) == list(range(0, raw.n, step))
    assert idx.desc.dtype == np.uint8 and idx.desc.shape[1] == 128
    assert len(idx.owner) == len(idx.desc) == len(idx.pts) == int(idx.offsets[-1])
    assert set(np.unique(idx.owner)) <= set(idx.frames.tolist())
    # lossless uint8 storage: re-running SIFT on an index frame reproduces the stored descriptors
    pts, desc = idx.frame_features(30)
    pts2, desc2 = vm.detect_sift(np.asarray(raw.get(30)), None, cfg.sift_nfeatures)
    assert np.array_equal(desc, desc2) and np.allclose(pts, pts2)
    assert idx.frame_features(31) is None
    # cached: a second build loads the npz and gives identical arrays
    idx2 = vm.RawIndex.build(raw, cfg, cache)
    assert idx2.key == idx.key and np.array_equal(idx2.desc, idx.desc) and np.array_equal(idx2.owner, idx.owner)


def test_cluster_aware_votes_find_the_right_shot(scene, built):
    """Top vote peak is within one index step of the truth for normal frames (and via the mirrored
    query for flipped frames); a plain first-vs-second ratio would reject most of these descriptors."""
    idx, cfg = built["index"], built["cfg"]
    comp, truth = scene["comp"], scene["truth"]
    am = _allowed(scene, cfg)
    roi = vm.box_roi(scene["layout"], comp)
    ok = 0
    frames = [3, 20, 33, 47, 55, 64, 85, 100, 110, 125]
    for k in frames:
        t = truth[k]
        img = np.asarray(comp.get(k))
        mask = am(k)
        if t["flip"]:
            x, y, w, h = roi
            img = np.ascontiguousarray(img[y:y + h, x:x + w][:, ::-1])
            mask = np.ascontiguousarray(mask[y:y + h, x:x + w][:, ::-1])
            _, desc = vm.detect_sift(img, mask, cfg.sift_nfeatures)
        else:
            _, desc = vm.detect_sift(img, mask, cfg.sift_nfeatures, roi)
        cands = idx.query(desc, 3)
        assert cands, k
        if abs(cands[0][0] - t["raw"]) <= idx.step:
            ok += 1
        # the window restricts candidates
        w = idx.query(desc, 3, window=(t["raw"] + 30, t["raw"] + 60))
        assert all(t["raw"] + 30 <= j < t["raw"] + 60 for j, _ in w)
    assert ok >= len(frames) - 1


def test_search_frame_near_misses_are_marked_and_opt_in(scene, built, tmp_path):
    """RANSAC near-misses (near_miss_inliers <= inliers < min_inliers) never become anchors: without near_miss
    the frame has none; with near_miss=True (sparse search / rescue) the ZNCC-verified ones come back as
    '<source>_near' (refine lets them only join an existing time line). min_inliers is raised here so that every
    candidate is a near-miss."""
    import dataclasses
    idx = built["index"]
    cfg = dataclasses.replace(built["cfg"], min_inliers=100000)
    comp, raw, truth = scene["comp"], scene["raw"], scene["truth"]
    am = _allowed(scene, cfg)
    roi = vm.box_roi(scene["layout"], comp)
    k = 88
    assert vm.search_frame(k, comp, raw, idx, am(k), cfg, roi=roi) == []
    near = vm.search_frame(k, comp, raw, idx, am(k), cfg, roi=roi, near_miss=True, source="rescue")
    assert near and all(a.source == "rescue_near" for a in near) and len(near) <= 2
    assert near[0].raw == truth[k]["raw"] and near[0].zncc >= cfg.match_thresh - cfg.anchor_zncc_slack
    assert vm.search_frame(k, comp, raw, idx, am(k), dataclasses.replace(cfg, near_miss_inliers=0), roi=roi,
                           near_miss=True) == []
    # a proper anchor always wins: near-misses only stand in when nothing passes
    a = vm.search_frame(k, comp, raw, idx, am(k), built["cfg"], roi=roi, near_miss=True)
    assert a and all(not x.source.endswith("_near") for x in a)


def test_search_frame_normal_flip_and_not_in_raw(scene, built):
    idx, cfg = built["index"], built["cfg"]
    comp, raw, truth = scene["comp"], scene["raw"], scene["truth"]
    am = _allowed(scene, cfg)
    roi = vm.box_roi(scene["layout"], comp)
    # normal frame of the 1.10x segment
    k = 88
    anchors = vm.search_frame(k, comp, raw, idx, am(k), cfg, roi=roi)
    assert anchors and anchors[0].raw == truth[k]["raw"] and not anchors[0].flip
    ds, dp = sim_error(anchors[0].sim, truth[k]["sim"])
    assert ds < 0.005 and dp < 2.0, (ds, dp)
    assert anchors[0].inliers >= cfg.min_inliers and anchors[0].zncc >= cfg.match_thresh
    # flipped frame: flip=True with a proper (positive-scale) canonical Sim
    k = 60
    anchors = vm.search_frame(k, comp, raw, idx, am(k), cfg, roi=roi)
    assert anchors and anchors[0].flip and anchors[0].raw == truth[k]["raw"]
    assert anchors[0].sim.s > 0
    ds, dp = sim_error(anchors[0].sim, truth[k]["sim"])
    assert ds < 0.005 and dp < 2.0, (ds, dp)
    # NOT-IN-RAW noise and the uniform frame: no anchors
    rep: list = []
    assert vm.search_frame(73, comp, raw, idx, am(73), cfg, roi=roi, report=rep) == []
    assert rep  # the rejection evidence is reported
    assert vm.search_frame(78, comp, raw, idx, am(78), cfg, roi=roi) == []
    # a window that excludes the truth yields nothing
    assert vm.search_frame(88, comp, raw, idx, am(88), cfg, window=(0, 60), roi=roi) == []


def test_run_line_searches_with_precomputed_raw_features_equals_single_searches(scene, built):
    """run_line_searches computes every RAW frame's SIFT features once per batch (wave-4 performance: neighbouring
    competitor frames search largely the same RAW window) -- the anchors must equal one line_search per frame."""
    cfg, comp, raw, truth = built["cfg"], scene["comp"], scene["raw"], scene["truth"]
    am = _allowed(scene, cfg)
    roi = vm.box_roi(scene["layout"], comp)
    tasks = [(k, list(range(max(0, truth[k]["raw"] - 4), truth[k]["raw"] + 5)), bool(truth[k]["flip"]))
             for k in (5, 6, 7, 55, 56, 90, 91, 92, 93)]
    vm._LINE_FEAT.clear()
    from match_cuts.common import single_thread_blas
    with single_thread_blas():               # as every parallel_map task (the ZNCC's last bits follow the BLAS threads)
        single = [vm.line_search(k, comp, raw, am(k), cfg, js, fl, roi=roi) for k, js, fl in tasks]
    vm._LINE_FEAT.clear()
    batch = vm.run_line_searches(comp, raw, am, roi, tasks, cfg)
    assert [k for k, _a, _r in batch] == [k for k, _js, _fl in tasks]
    got = [[a.to_dict() for a in anchors] for _k, anchors, _rep in batch]
    want = [[a.to_dict() for a in anchors[:int(cfg.anchors_per_frame)]] for anchors in single]
    assert got == want
    assert any(got)                                                   # the searches do find the truth
    assert all(a["raw"] == truth[k]["raw"] for (k, _js, _fl), ads in zip(tasks, got) for a in ads[:1])


def test_sparse_search_parallel_deterministic_and_cached(scene, built, tmp_path):
    idx, cfg = built["index"], built["cfg"]
    comp, raw, truth = scene["comp"], scene["raw"], scene["truth"]
    am = _allowed(scene, cfg)
    frames = [0, 12, 40, 52, 66, 73, 78, 81, 95, 106, 120]
    dlog = DecisionLog(tmp_path / "decisions.jsonl")
    cache = Cache(tmp_path / "work")
    a1 = vm.sparse_search(comp, raw, scene["layout"], None, idx, None, cfg, dlog, frames=frames, cache=cache,
                          allowed_fn=am)
    cfg1 = make_config(tmp_path, workers=1)
    a2 = vm.sparse_search(comp, raw, scene["layout"], None, idx, None, cfg1, None, frames=frames, allowed_fn=am)
    assert [a.to_dict() for a in a1] == [a.to_dict() for a in a2]
    best = {}
    for a in a1:
        best.setdefault(a.k, a)
    assert 73 not in best and 78 not in best          # NOT-IN-RAW / uniform
    for k in (0, 12, 40, 52, 66, 81, 95, 106, 120):
        assert k in best, k
        t = truth[k]
        assert best[k].raw == t["raw"] and best[k].flip == t["flip"], (k, best[k], t)
        ds, dp = sim_error(best[k].sim, t["sim"])
        assert ds < 0.005 and dp < 2.0, (k, ds, dp)
    flipped = [a for a in a1 if 50 <= a.k < 70]
    assert flipped and all(a.flip and a.sim.s > 0 for a in flipped)
    # the decision log carries evidence
    dlog.close()
    txt = (tmp_path / "decisions.jsonl").read_text()
    assert '"anchor"' in txt and '"skip_uniform"' in txt and "inliers" in txt
    # cached result is reused (identical, and does not need the index to be queried again)
    a3 = vm.sparse_search(comp, raw, scene["layout"], None, idx, None, cfg, None, frames=frames, cache=cache,
                          allowed_fn=am)
    assert [a.to_dict() for a in a3] == [a.to_dict() for a in a1]


def test_audio_window_restricts_search(scene, built):
    cfg = built["cfg"]
    comp, raw, truth = scene["comp"], scene["raw"], scene["truth"]
    k = 95
    t_k = k / 30.0
    raw_t = (truth[k]["raw"] + 0.5) / float(RAW_FPS)
    hints = AudioHints(np.array([t_k]), np.array([raw_t]), np.array([1.1]), np.array([5.0], np.float32),
                       np.array([10.0], np.float32), np.array([0.9], np.float32))
    w = vm.audio_window(hints, k, COMP_FPS, RAW_FPS, cfg, raw.n)
    assert w is not None and w[0] <= truth[k]["raw"] < w[1] and w[1] - w[0] <= 2 * 60 + 1
    # far from the hint -> no window; unconfident -> no window
    assert vm.audio_window(hints, 10, COMP_FPS, RAW_FPS, cfg, raw.n) is None
    weak = AudioHints(hints.comp_t, hints.raw_t, hints.speed, np.array([1.0], np.float32), hints.psr, hints.peak)
    assert vm.audio_window(weak, k, COMP_FPS, RAW_FPS, cfg, raw.n) is None
    res = vm.run_searches(comp, raw, built["index"], _allowed(scene, cfg), vm.box_roi(scene["layout"], comp),
                          hints, [k], cfg)
    assert res[0][1] and res[0][1][0].source == "audio" and res[0][1][0].raw == truth[k]["raw"]


def _rotated_neighbour_scene(j0: int = 12):
    """RAW = one shot where frame j0+1 is frame j0 rotated 0.9 deg and shifted 10 px (a jolt of the camera); the
    competitor's single frame shows RAW j0 unrotated."""
    frames = np.stack(make_shot(21, 30, 0))
    W, H = RAW_FULL
    M = cv2.getRotationMatrix2D((W / 2.0, H / 2.0), 0.9, 1.0)
    M[0, 2] += 10.0
    frames[j0 + 1] = cv2.warpAffine(frames[j0], M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    rw, rh = int(W * PR), int(H * PR)
    rawp = np.stack([cv2.resize(f, (rw, rh), interpolation=cv2.INTER_AREA) for f in frames])
    raw = Proxy("raw", "", rawp, RAW_FULL, (rw / W, rh / H), RAW_FPS, np.arange(len(frames)) / float(RAW_FPS),
                len(frames))
    cw, chh = COMP_FULL
    cov = np.zeros((chh, cw), np.float32)
    cov[int(BOX.y):int(BOX.y + BOX.h), int(BOX.x):int(BOX.x + BOX.w)] = 1.0
    sim = centred_sim(0.85, 0.0)
    m = to_cv_matrix(sim, False, W, (1.0, 1.0), (1.0, 1.0))
    img = np.clip(cv2.warpAffine(frames[j0].astype(np.float32), m, (cw, chh)) * cov
                  + np.random.default_rng(4).normal(0, 1.5, (chh, cw)), 0, 255).astype(np.uint8)
    cp = np.stack([cv2.resize(img, (int(cw * PR), int(chh * PR)), interpolation=cv2.INTER_AREA)])
    comp = Proxy("competitor", "", cp, COMP_FULL, (PR, PR), COMP_FPS, np.zeros(1), 1)
    return raw, comp, Layout(comp_w=cw, comp_h=chh, box=BOX), sim, frames, M


def test_anchor_keeps_theta_zero_against_a_rotated_neighbour(scene, built, tmp_path):
    """FX-03 step 1 (anchor re-estimation over jb-1..jb+1 from the RANSAC Sim and its derotated version): RAW j+1
    = RAW j rotated 0.9 deg + shifted 10 px explains the competitor's unrotated RAW j as well, with a rotated
    Sim -> the anchor is (j, theta = 0), from the global search and when re-estimation starts from the rotated
    neighbour's own Sim. A competitor framing genuinely rotated 1.5 deg keeps 1.5 +- 0.1."""
    j0 = 12
    raw, comp, layout, sim, frames, M = _rotated_neighbour_scene(j0)
    cfg = make_config(tmp_path)
    index = vm.RawIndex.build(raw, cfg, None)
    am = vm.AllowedMasks(layout, None, comp, cfg, use_layout_module=False)
    roi = vm.box_roi(layout, comp)
    anchors = vm.search_frame(0, comp, raw, index, am(0), cfg, roi=roi)
    assert anchors and anchors[0].raw == j0, [(a.raw, round(a.sim.theta_deg, 3), round(a.zncc, 4)) for a in anchors]
    assert abs(anchors[0].sim.theta_deg) < 0.05
    ds, dp = sim_error(anchors[0].sim, sim)
    assert ds < 0.003 and dp < 1.5, (ds, dp)
    # start from the rotated neighbour's own hypothesis: the Sim mapping RAW j0+1 onto the competitor
    rot = Sim.from_matrix(sim.matrix() @ np.linalg.inv(np.vstack([M, [0.0, 0.0, 1.0]])))
    assert abs(rot.theta_deg) > 0.8
    j, s2, z2, _amb = vm._reestimate(np.asarray(comp.get(0)), raw, j0 + 1, rot, False, am(0), roi, comp, cfg)
    assert j == j0 and abs(s2.theta_deg) < 0.05 and z2 > 0.97, (j, s2, z2)
    # a genuinely rotated competitor framing (the scene's 1.5 deg re-use) keeps its rotation
    sam = _allowed(scene, built["cfg"])
    sroi = vm.box_roi(scene["layout"], scene["comp"])
    for k in (110, 116):
        a = vm.search_frame(k, scene["comp"], scene["raw"], built["index"], sam(k), built["cfg"], roi=sroi)
        assert a and a[0].raw == scene["truth"][k]["raw"] and abs(a[0].sim.theta_deg - 1.5) < 0.1, \
            (k, [(x.raw, x.sim.theta_deg) for x in a])


def test_anchor_roundtrip_and_allowed_masks(scene):
    a = vm.Anchor(3, 17, True, Sim(0.9, 0.0, -1.5, 2.5), 40, 0.8, 12.5, 0.97, "rescue", True)
    assert vm.Anchor.from_dict(a.to_dict()) == a
    legacy = {k: v for k, v in a.to_dict().items() if k != "time_ambiguous"}
    assert vm.Anchor.from_dict(legacy).time_ambiguous is False
    cfg = Config()
    am = vm.AllowedMasks(scene["layout"], None, scene["comp"], cfg, use_layout_module=False)
    m = am(0)
    assert m.shape == (480, 270) and m.dtype == bool
    x, y, w, h = vm.box_roi(scene["layout"], scene["comp"])
    assert (x, y, w, h) == (15, 115, 240, 225)
    assert not m[:y].any() and not m[y + h:].any() and m[y + h // 2, x + w // 2]
    assert not m[y, x]                                   # rounded corner excluded
    ov = Overlays()
    ov.set(5, scene["caption_mask"])
    am2 = vm.AllowedMasks(scene["layout"], ov, scene["comp"], cfg, use_layout_module=False)
    assert am2(5).sum() < m.sum() - scene["caption_mask"].sum()       # overlay removed + dilated
    am2.add_extra(6, scene["caption_mask"])
    assert not (am2(6) & scene["caption_mask"]).any()


def test_mirror_features_equal_sift_of_the_flipped_image(scene):
    """Flip votes / flipped RAW verification use a descriptor permutation instead of a second SIFT."""
    img = np.asarray(scene["raw"].get(33))
    pts, desc = vm.detect_sift(img, None, 500)
    mp, md = vm.mirror_features(pts, desc, img.shape[1])
    fp, fd = vm.detect_sift(np.ascontiguousarray(img[:, ::-1]), None, 500)
    rel = []
    for p, d in zip(mp, md.astype(np.float32)):
        near = np.flatnonzero(np.hypot(*(fp - p).T) < 0.5)
        if len(near):
            dist = np.linalg.norm(fd[near].astype(np.float32) - d, axis=1).min()
            rel.append(dist / max(np.linalg.norm(d), 1.0))
    assert len(rel) >= 0.95 * len(pts)
    assert np.median(rel) < 0.02 and np.mean(np.array(rel) < 0.05) > 0.9


# ----------------------------------------------------------------------------------------------
# worker pools: fork on Linux, spawn on Windows / macOS (review finding real-world:F6, DESIGN D7)
# ----------------------------------------------------------------------------------------------

_PARENT_MARK = {"parent": False}     # set in the parent: forked workers inherit it, spawned ones do not


def _probe(state, x):
    """Module-level (hence picklable) parallel_map item function reporting where it ran."""
    return (x, state["mul"] * x, os.getpid(), _PARENT_MARK["parent"], cv2.getNumThreads())


def _run_probe(workers=3):
    return vm.parallel_map(_probe, list(range(40)), workers, {"mul": 7}, seed=1)


@pytest.fixture
def clean_pool_env(monkeypatch):
    monkeypatch.delenv(vm.START_METHOD_ENV, raising=False)
    monkeypatch.setitem(_PARENT_MARK, "parent", True)
    yield
    vm.shutdown_workers()


def test_start_method_platform_defaults_and_env(monkeypatch):
    monkeypatch.delenv(vm.START_METHOD_ENV, raising=False)
    monkeypatch.setattr(vm, "_platform", lambda: "linux")
    monkeypatch.setattr(vm, "_fork_available", lambda: True)     # the platforms are simulated: Linux has fork
    assert vm.start_method() == "fork"
    for plat in ("win32", "darwin"):
        monkeypatch.setattr(vm, "_platform", lambda plat=plat: plat)
        assert vm.start_method() == "spawn", plat
    monkeypatch.setenv(vm.START_METHOD_ENV, "fork")
    assert vm.start_method() == "fork"                    # darwin has fork: explicit override honoured
    monkeypatch.setattr(vm, "_fork_available", lambda: False)
    assert vm.start_method() == "spawn"                   # Windows: no fork -> spawn
    monkeypatch.setenv(vm.START_METHOD_ENV, "spawn")
    monkeypatch.setattr(vm, "_platform", lambda: "linux")
    assert vm.start_method() == "spawn"


def test_parallel_map_uses_spawn_workers_on_windows(monkeypatch, clean_pool_env):
    """Regression (real-world:F6): without fork (Windows) parallel_map used to run everything in the
    parent process. It must run a spawn pool with the same results as the inline loop."""
    monkeypatch.setattr(vm, "_platform", lambda: "win32")
    monkeypatch.setattr(vm, "_fork_available", lambda: False)
    before = dict(vm.POOL_STATS)
    res = _run_probe(3)
    inline = vm.parallel_map(_probe, list(range(40)), 1, {"mul": 7}, seed=1)
    assert [r[:2] for r in res] == [r[:2] for r in inline] == [(x, 7 * x) for x in range(40)]
    pids = {r[2] for r in res}
    assert pids and os.getpid() not in pids              # really ran in worker processes
    assert not any(r[3] for r in res)                     # spawned (fresh module), not forked
    assert all(r[4] == 1 for r in res)                    # one OpenCV thread per worker
    assert vm.POOL_STATS["spawn"] == before["spawn"] + 1
    assert vm.POOL_STATS["spawn_fallback"] == before["spawn_fallback"]
    # the pool persists across calls (spawn start-up paid once) and still gives the same results
    res2 = _run_probe(3)
    assert [r[:2] for r in res2] == [r[:2] for r in res] and os.getpid() not in {r[2] for r in res2}
    assert vm._POOL["pool"] is not None


def test_parallel_map_macos_uses_spawn_and_env_overrides(monkeypatch, clean_pool_env):
    monkeypatch.setattr(vm, "_platform", lambda: "darwin")
    res = _run_probe(3)
    assert not any(r[3] for r in res) and os.getpid() not in {r[2] for r in res}
    res_f = res
    if vm._fork_available():                              # (Windows has no fork)
        monkeypatch.setenv(vm.START_METHOD_ENV, "fork")
        res_f = _run_probe(3)
        assert all(r[3] for r in res_f) and os.getpid() not in {r[2] for r in res_f}   # forked
        assert vm._POOL["pool"] is None                   # the spawn pool is shut down before forking
    monkeypatch.setattr(vm, "_platform", lambda: "linux")
    monkeypatch.setenv(vm.START_METHOD_ENV, "spawn")
    res_s = _run_probe(3)
    assert not any(r[3] for r in res_s)
    assert [r[:2] for r in res] == [r[:2] for r in res_f] == [r[:2] for r in res_s]


def test_parallel_map_spawn_unpicklable_state_falls_back(monkeypatch, clean_pool_env, caplog):
    monkeypatch.setenv(vm.START_METHOD_ENV, "spawn")

    def local_fn(state, x):                                # a closure cannot be pickled
        return (x, state["mul"] * x, os.getpid())
    before = dict(vm.POOL_STATS)
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res = vm.parallel_map(local_fn, list(range(20)), 3, {"mul": 3}, seed=1)
    assert [r[:2] for r in res] == [(x, 3 * x) for x in range(20)]
    assert {r[2] for r in res} == {os.getpid()}
    assert vm.POOL_STATS["spawn_fallback"] == before["spawn_fallback"] + 1
    assert "not picklable" in caplog.text


def _mm_proxy(p: Proxy, path, a: int = 0, b: int | None = None) -> Proxy:
    """The proxy with its frames saved to ``path`` (.npy) and re-opened as a read-only memmap (pipeline)."""
    b = p.n if b is None else b
    np.save(path, np.ascontiguousarray(p.frames[a:b]))
    return Proxy(p.role, "", np.load(path, mmap_mode="r"), p.full_size, p.ratio, p.fps, p.pts[a:b], b - a,
                 npy_path=str(path))


def test_proxy_pickles_memmaps_as_file_references(scene, tmp_path):
    raw = scene["raw"]
    # dense .npy proxy: pickled as (path, offset, dtype, shape), re-opened as a memmap
    dense = _mm_proxy(raw, tmp_path / "raw.npy", 0, 60)
    blob = pickle.dumps(dense, protocol=pickle.HIGHEST_PROTOCOL)
    assert len(blob) < 16_000 < dense.frames.nbytes
    back = pickle.loads(blob)
    assert isinstance(back.frames, np.memmap) and str(back.frames.filename) == str(dense.frames.filename)
    assert back.frames.shape == dense.frames.shape and np.array_equal(back.frames, dense.frames)
    assert (back.role, back.n, back.full_size, back.ratio, back.fps, back.npy_path) == \
        (dense.role, dense.n, dense.full_size, dense.ratio, dense.fps, dense.npy_path)
    assert np.array_equal(back.pts, dense.pts)
    # sparse store (.u8 rows + index_map)
    rows = [3, 9, 27, 40]
    u8 = tmp_path / "store.u8"
    u8.write_bytes(np.ascontiguousarray(np.stack([raw.get(j) for j in rows])).tobytes())
    h, w = raw.frames.shape[1:]
    im = np.full(raw.n, -1, np.int32)
    im[rows] = np.arange(len(rows), dtype=np.int32)
    sparse = Proxy("raw", "", np.memmap(u8, dtype=np.uint8, mode="r", shape=(len(rows), h, w)), raw.full_size,
                   raw.ratio, raw.fps, raw.pts, raw.n, npy_path=str(u8), index_map=im)
    sb = pickle.dumps(sparse, protocol=pickle.HIGHEST_PROTOCOL)
    assert len(sb) < 16_000
    s2 = pickle.loads(sb)
    assert isinstance(s2.frames, np.memmap) and not s2.dense
    assert all(np.array_equal(s2.get(j), raw.get(j)) for j in rows) and not s2.has(4)
    # a view of a memmap, a writable memmap and an in-memory proxy pickle their pixels
    view = Proxy("raw", "", dense.frames[10:20], raw.full_size, raw.ratio, raw.fps, raw.pts[10:20], 10)
    v2 = pickle.loads(pickle.dumps(view))
    assert np.array_equal(v2.frames, raw.frames[10:20])
    rw = Proxy("raw", "", np.load(tmp_path / "raw.npy", mmap_mode="r+"), raw.full_size, raw.ratio, raw.fps,
               raw.pts[:60], 60)
    assert len(pickle.dumps(rw)) > rw.frames.nbytes
    mem = pickle.loads(pickle.dumps(scene["comp"]))
    assert np.array_equal(mem.frames, scene["comp"].frames)


def test_allowed_masks_pickle_packs_extra_masks(scene, tmp_path):
    cfg = Config()
    comp = _mm_proxy(scene["comp"], tmp_path / "comp.npy")          # pickled as a file reference
    am = vm.AllowedMasks(scene["layout"], None, comp, cfg, use_layout_module=False)
    for k in scene["caption_frames"]:
        am.add_extra(k, scene["caption_mask"])
    blob = pickle.dumps(am, protocol=pickle.HIGHEST_PROTOCOL)
    assert len(blob) < 0.5 * len(scene["caption_frames"]) * scene["caption_mask"].size   # bit-packed
    am2 = pickle.loads(blob)
    for k in (0, 5, 10, 33, 60, 61, 100):
        assert np.array_equal(am2(k), am(k)), k
    am2.add_extra(12, np.roll(scene["caption_mask"], 7, axis=0))
    am.add_extra(12, np.roll(scene["caption_mask"], 7, axis=0))
    assert np.array_equal(am2(12), am(12))
    am3 = pickle.loads(pickle.dumps(am2))                 # re-pickling keeps unpacked + packed masks
    for k in (11, 12, 40):
        assert np.array_equal(am3(k), am(k)), k


def test_raw_index_pickles_as_cache_files(scene, built, tmp_path):
    idx = built["index"]
    q = np.ascontiguousarray(idx.desc[::53][:300])
    ref = idx.votes(q)
    # cached index: side files next to the npz; the pickle holds only paths and scalars
    assert idx.npz_path and os.path.isfile(idx.npz_path)
    idx.prepare_spawn()
    blob = pickle.dumps(idx, protocol=pickle.HIGHEST_PROTOCOL)
    assert len(blob) < 4096
    vm._INDEX_CACHE.clear()
    back = pickle.loads(blob)
    assert back._flann is not None                         # tree loaded at unpickling, before any item runs
    assert isinstance(back.desc, np.memmap) and np.array_equal(back.desc, idx.desc)
    for k in ("frames", "owner", "pts", "offsets"):
        assert np.array_equal(getattr(back, k), getattr(idx, k)), k
    assert np.array_equal(back.votes(q), ref)
    assert pickle.loads(blob) is back                       # per-process cache: loaded once per worker
    # no / unreadable tree file (e.g. OpenCV cannot write a non-ASCII path): re-trained, same seed, same votes
    bad = tmp_path / "bad.flann"
    bad.write_bytes(b"not a flann index" * 64)
    for files in ({k: v for k, v in idx._spawn_files.items() if k != "flann"},
                  {**idx._spawn_files, "flann": str(bad)}):
        st = {k: getattr(idx, k) for k in vm.RawIndex._SCALARS}
        st.update(key="", files=files)                      # no per-process cache
        r = vm._restore_index(st)
        assert r._flann is not None and np.array_equal(r.votes(q), ref)
    # uncached index: pickled by value, FLANN re-trained with the index seed -> identical votes
    raw_idx = vm.RawIndex(idx.frames, idx.desc, idx.owner, idx.pts, idx.offsets, idx.fps, idx.step, built["cfg"])
    b2 = pickle.dumps(raw_idx)
    assert len(b2) > idx.desc.nbytes
    r2 = pickle.loads(b2)
    assert r2._flann is not None and np.array_equal(r2.votes(q), ref)


def test_index_and_search_bit_identical_inline_fork_spawn(scene, tmp_path, monkeypatch):
    """RawIndex.build and sparse_search give identical results inline, in a fork pool and in a spawn
    pool (memmapped proxies re-opened in the workers, the cached index loaded from its side files)."""
    raw = _mm_proxy(scene["raw"], tmp_path / "raw.npy", 0, 2 * SHOT_LEN)       # shots 0 and 1
    comp = _mm_proxy(scene["comp"], tmp_path / "comp.npy")
    frames = [0, 12, 33, 52, 66, 73, 78]
    out = {}
    try:
        for mode, workers in [m for m in (("inline", 1), ("fork", 3), ("spawn", 3))
                              if m[0] != "fork" or vm._fork_available()]:       # (Windows has no fork)
            monkeypatch.setenv(vm.START_METHOD_ENV, "spawn" if mode == "spawn" else "fork")
            cfg = make_config(tmp_path / mode, workers=workers)
            before = dict(vm.POOL_STATS)
            idx = vm.RawIndex.build(raw, cfg, Cache(tmp_path / mode / "work"))
            am = vm.AllowedMasks(scene["layout"], None, comp, cfg, use_layout_module=False)
            anchors = vm.sparse_search(comp, raw, scene["layout"], None, idx, None, cfg, None, frames=frames,
                                       allowed_fn=am)
            if mode != "inline":
                assert vm.POOL_STATS[mode] >= before[mode] + 2, (mode, vm.POOL_STATS)
                assert vm.POOL_STATS["spawn_fallback"] == before["spawn_fallback"]
            out[mode] = (idx, [a.to_dict() for a in anchors])
    finally:
        vm.shutdown_workers()
    i0, a0 = out["inline"]
    for mode in [m for m in ("fork", "spawn") if m in out]:
        i1, a1 = out[mode]
        for k in ("frames", "desc", "owner", "pts", "offsets"):
            assert np.array_equal(getattr(i0, k), getattr(i1, k)), (mode, k)
        assert a1 == a0, mode
    best = {}
    for a in a0:
        best.setdefault(a["k"], a)
    assert set(best) == {0, 12, 33, 52, 66}
    assert all(best[k]["raw"] == scene["truth"][k]["raw"] for k in best)



# ----------------------------------------------------------------------------------------------
# Regression real-world-new-paths:D7-spawn-memory: every spawn worker loads its OWN FLANN tree
# (~800 B per descriptor): the pool is capped by the available RAM when the state holds a RawIndex,
# and states without a RawIndex (S5.3 refine) drop the workers' trees. The fork path is unchanged.
# ----------------------------------------------------------------------------------------------

def _index_probe(state, x):
    """Module-level parallel_map item function: (x, pid, FLANN trees cached in this process, votes)."""
    idx = state.get("index")
    v = idx.votes(np.ascontiguousarray(idx.desc[(x * 7) % 200:(x * 7) % 200 + 40])) if idx is not None else None
    return (x, os.getpid(), len(vm._INDEX_CACHE), None if v is None else v.tolist())


def _ram_for_workers(idx, n_workers: float) -> int:
    """An available-RAM figure for which _spawn_mem_cap allows int(n_workers) workers for ``idx``."""
    n = len(idx.desc)
    per = vm.SPAWN_TREE_BYTES_PER_DESC * n + vm.SPAWN_WORKER_BASE_BYTES
    avail = vm.SPAWN_MEM_MARGIN_BYTES + n * 640 + int(n_workers * per)
    assert avail // 10 < vm.SPAWN_MEM_MARGIN_BYTES
    return avail


def test_spawn_pool_capped_by_available_ram(scene, built, monkeypatch, clean_pool_env, caplog):
    idx = built["index"]
    idx.ensure_built()
    items = list(range(24))
    inline = vm.parallel_map(_index_probe, items, 1, {"index": idx}, seed=1)
    monkeypatch.setenv(vm.START_METHOD_ENV, "spawn")
    monkeypatch.setattr(vm, "_MEM_CAPS", {})
    monkeypatch.setattr(vm, "_pool_private_bytes", lambda: 0)
    monkeypatch.setattr(vm, "_available_ram", lambda: _ram_for_workers(idx, 2.5))
    before = dict(vm.POOL_STATS)
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res = vm.parallel_map(_index_probe, items, 4, {"index": idx}, seed=1)
    assert vm._POOL["n"] == 2                                     # 4 requested, 2 fit
    assert len({r[1] for r in res}) <= 2 and os.getpid() not in {r[1] for r in res}
    assert [(r[0], r[3]) for r in res] == [(r[0], r[3]) for r in inline]   # results do not depend on the pool
    assert vm.POOL_STATS["spawn_mem_capped"] == before["spawn_mem_capped"] + 1
    assert vm.POOL_STATS["spawn"] == before["spawn"] + 1
    assert "spawn workers: 2 instead of 4" in caplog.text and "FLANN tree" in caplog.text
    # decided once per index: the trees the workers now hold do not shrink the pool further, no new log
    caplog.clear()
    monkeypatch.setattr(vm, "_available_ram", lambda: 1)
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res2 = vm.parallel_map(_index_probe, items, 4, {"index": idx}, seed=1)
    assert vm._POOL["n"] == 2 and [r[3] for r in res2] == [r[3] for r in inline] and "instead of" not in caplog.text
    # a state without a RawIndex is capped at SPAWN_WORKER_EST_BYTES a worker (decided once, logged)
    monkeypatch.setattr(vm, "_available_ram", lambda: vm.SPAWN_MEM_MARGIN_BYTES + int(3.5 * vm.SPAWN_WORKER_EST_BYTES))
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        vm.parallel_map(_probe, list(range(40)), 4, {"mul": 7}, seed=1)
    assert vm._POOL["n"] == 3 and "spawn workers: 3 instead of 4 - each worker process needs" in caplog.text
    monkeypatch.setattr(vm, "_available_ram", lambda: 1)
    # only one worker fits: single-process (the parent already holds the tree), no spawn pool used
    monkeypatch.setattr(vm, "_MEM_CAPS", {})
    monkeypatch.setattr(vm, "_available_ram", lambda: _ram_for_workers(idx, 1.5))
    before = dict(vm.POOL_STATS)
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res3 = vm.parallel_map(_index_probe, items, 4, {"index": idx}, seed=1)
    assert {r[1] for r in res3} == {os.getpid()} and [r[3] for r in res3] == [r[3] for r in inline]
    assert vm.POOL_STATS["inline"] == before["inline"] + 1 and vm.POOL_STATS["spawn"] == before["spawn"]
    assert "running single-process" in caplog.text
    # unknown available RAM: no cap
    monkeypatch.setattr(vm, "_MEM_CAPS", {})
    monkeypatch.setattr(vm, "_available_ram", lambda: None)
    assert vm._spawn_mem_cap(4, {"index": idx}) == 4
    # the fork path never consults the memory cap (the tree is shared copy-on-write)
    if not vm._fork_available():                          # (Windows has no fork)
        return
    monkeypatch.setenv(vm.START_METHOD_ENV, "fork")

    def boom(*a, **k):
        raise AssertionError("_spawn_mem_cap called on the fork path")
    monkeypatch.setattr(vm, "_spawn_mem_cap", boom)
    before = dict(vm.POOL_STATS)
    res4 = vm.parallel_map(_index_probe, items, 4, {"index": idx}, seed=1)
    assert vm.POOL_STATS["fork"] == before["fork"] + 1 and [r[3] for r in res4] == [r[3] for r in inline]


def test_memory_cap_counts_what_the_pool_workers_hold(monkeypatch):
    """The memory this process's own pool workers hold is released when the pool is replaced: it counts as
    available (else a second pool size would be decided against the first pool's own memory)."""
    monkeypatch.setattr(vm, "_MEM_CAPS", {})
    monkeypatch.setattr(vm, "_available_ram", lambda: vm.SPAWN_MEM_MARGIN_BYTES + vm.SPAWN_WORKER_EST_BYTES // 2)
    monkeypatch.setattr(vm, "_pool_private_bytes", lambda: 0)
    assert vm._spawn_mem_cap(8, {"mul": 1}) == 1
    monkeypatch.setattr(vm, "_MEM_CAPS", {})
    monkeypatch.setattr(vm, "_pool_private_bytes", lambda: 3 * vm.SPAWN_WORKER_EST_BYTES)
    assert vm._spawn_mem_cap(8, {"mul": 1}) == 3


def test_available_ram_probe_returns_bytes():
    avail = vm._available_ram()
    assert avail is None or avail > 16 << 20


def test_spawn_workers_drop_index_trees_for_states_without_index(scene, built, monkeypatch, clean_pool_env):
    idx = built["index"]
    monkeypatch.setenv(vm.START_METHOD_ENV, "spawn")
    monkeypatch.setattr(vm, "_MEM_CAPS", {}, raising=False)
    monkeypatch.setattr(vm, "_available_ram", lambda: None, raising=False)       # no memory cap here
    items = list(range(24))
    r1 = vm.parallel_map(_index_probe, items, 3, {"index": idx}, seed=1)
    assert all(r[2] >= 1 for r in r1)                           # S5.2: each worker holds the tree
    r2 = vm.parallel_map(_index_probe, items, 3, {"mul": 1}, seed=1)
    assert {r[1] for r in r1} & {r[1] for r in r2}              # the same (persistent) workers
    assert all(r[2] == 0 for r in r2)                           # S5.3 state: the trees were dropped
    r3 = vm.parallel_map(_index_probe, items, 3, {"index": idx}, seed=1)
    assert all(r[2] >= 1 for r in r3) and [r[3] for r in r3] == [r[3] for r in r1]   # reloaded, same votes


# ---------------------------------------------------------------------------------------------------------------------
# the GPU path (the thorough default): every RAW frame indexed, searched exactly on the GPU
# ---------------------------------------------------------------------------------------------------------------------

def _gpu_ok():
    from match_cuts import gpu
    return gpu.available() is None


@pytest.fixture(scope="module")
def built_gpu(scene, tmp_path_factory):
    if not _gpu_ok():
        pytest.skip("no GPU")
    tmp = tmp_path_factory.mktemp("vm_gpu")
    cfg = Config(work_dir=str(tmp / "work"), out_dir=str(tmp / "out"))
    cfg.workers = 2
    cache = Cache(tmp / "work")
    return dict(cfg=cfg, cache=cache, index=vm.RawIndex.build(scene["raw"], cfg, cache))


def _brute_nn(idx, q):
    """The exact neighbours (index order breaking ties), numpy."""
    X = idx.desc.astype(np.int64)
    k = int(min(idx.knn, len(X)))
    out_i, out_d = [], []
    for row in np.asarray(q, np.int64):
        d2 = ((X - row) ** 2).sum(1)
        order = np.lexsort((np.arange(len(d2)), d2))[:k]
        out_i.append(order)
        out_d.append(d2[order].astype(np.float32))
    return np.array(out_i), np.array(out_d)


def test_every_raw_frame_is_indexed_and_searched_exactly_on_the_gpu(scene, built_gpu):
    idx = built_gpu["index"]
    raw = scene["raw"]
    assert idx.step == 1 and list(idx.frames) == list(range(raw.n)) and idx.flann_free
    q = np.ascontiguousarray(idx.desc[::41][:200])
    nn = idx.neighbours([q])[0]
    bi, bd = _brute_nn(idx, q)
    assert np.array_equal(nn[0], bi) and np.array_equal(nn[1], bd)        # the true neighbours, exactly
    assert np.array_equal(idx.votes(q), idx.votes(q, nn=(bi, bd)))


def test_a_gpu_searched_index_reaches_the_workers_without_a_kd_tree(scene, built_gpu):
    idx = built_gpu["index"]
    idx.prepare_spawn()
    assert set(idx._spawn_files) == {"desc"}                             # no float32 copy, no tree file
    vm._INDEX_CACHE.clear()
    back = pickle.loads(pickle.dumps(idx, protocol=pickle.HIGHEST_PROTOCOL))
    assert back.flann_free and back._flann is None and isinstance(back.desc, np.memmap)
    q = np.ascontiguousarray(idx.desc[::53][:100])
    nn = idx.neighbours([q])[0]
    assert np.array_equal(back.votes(q, nn=nn), idx.votes(q, nn=nn))       # the neighbours come with the task
    pts, desc = back.frame_features(31)                                  # every frame's features are there
    assert len(desc) and np.array_equal(desc, idx.frame_features(31)[1])


def test_the_gpu_searches_give_the_anchors_of_direct_searches(scene, built_gpu):
    idx, cfg = built_gpu["index"], built_gpu["cfg"]
    comp = scene["comp"]
    am = _allowed(scene, cfg)
    roi = vm.box_roi(scene["layout"], comp)
    frames = [3, 33, 64, 100]
    got = vm.run_searches(comp, scene["raw"], idx, am, roi, None, frames, cfg)
    for k, anchors, _rep in got:
        direct = vm.search_frame(k, comp, scene["raw"], idx, am(k), cfg, window=None, source="global", roi=roi,
                                 near_miss=True)
        assert [(a.raw, a.flip, round(a.zncc, 6)) for a in anchors] == \
               [(a.raw, a.flip, round(a.zncc, 6)) for a in direct[:len(anchors)]], k
        assert anchors and abs(anchors[0].raw - scene["truth"][k]["raw"]) <= 1, k
    vm.shutdown_workers()


def test_a_gpu_searched_index_does_not_shrink_the_pool_for_trees_it_never_loads(built_gpu, monkeypatch):
    idx = built_gpu["index"]
    monkeypatch.setattr(vm, "_MEM_CAPS", {})
    monkeypatch.setattr(vm, "_pool_private_bytes", lambda: 0)
    # room for 2.5 kd-tree workers -- but no worker of a GPU-searched index loads one: a plain worker each
    monkeypatch.setattr(vm, "_available_ram", lambda: _ram_for_workers(idx, 2.5))
    per_tree = vm.SPAWN_TREE_BYTES_PER_DESC * len(idx.desc) + vm.SPAWN_WORKER_BASE_BYTES
    plain = (_ram_for_workers(idx, 2.5) - vm.SPAWN_MEM_MARGIN_BYTES - len(idx.desc) * 128) // vm.SPAWN_WORKER_EST_BYTES
    assert vm._spawn_mem_cap(4, {"index": idx}) == min(4, max(1, int(plain)))
    assert per_tree > 0 and idx.flann_free


# ---------------------------------------------------------------------------------------------------------------------
# Task 9: the RAW index of the audio regions -- where the audio places the competitor, the whole RAW only when needed
# ---------------------------------------------------------------------------------------------------------------------

def _scene_hints(scene, ks):
    """Confident audio hints placing competitor frames ``ks`` on shot 0's line (seg 1 / seg 2), as a competitor whose
    sound runs on under a flash of another shot (k 40) does."""
    truth = scene["truth"]
    rf = float(RAW_FPS)
    line = {k: (truth[k]["raw"] if truth[k].get("seg") in (1, 2) else truth[39]["raw"] + (k - 39)) for k in ks}
    comp_t = np.array([k / float(COMP_FPS) for k in ks])
    raw_t = np.array([(line[k] + 0.5) / rf for k in ks])
    n = len(ks)
    return AudioHints(comp_t, raw_t, np.ones(n), np.full(n, 5.0, np.float32), np.full(n, 10.0, np.float32),
                      np.full(n, 0.9, np.float32), 1.0, 0.25)


def test_the_index_of_the_audio_regions_holds_the_whole_indexs_rows(scene, built_gpu):
    idx_all, cfg, cache = built_gpu["index"], built_gpu["cfg"], built_gpu["cache"]
    reg = vm.RawIndex.build(scene["raw"], cfg, cache, regions=[(0, 70), (150, 170)])
    assert reg.regions == [(0, 70), (150, 170)] and reg.key != idx_all.key and reg.nfeat
    assert list(reg.frames) == list(range(0, 70)) + list(range(150, 170))
    for j in (0, 33, 69, 150, 169):                  # each frame's features are the whole index's
        a, b = reg.frame_features(j), idx_all.frame_features(j)
        assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    # nearly the whole RAW (90 %): the whole index itself
    assert vm.RawIndex.build(scene["raw"], cfg, cache, regions=[(0, 230)]).regions is None
    # the votes are not smoothed across the gap between two regions: frame 69 (the end of one) and frame 150 (the
    # start of the next) sit side by side in the index, but frame 69's own features do not vote for frame 150
    q = np.ascontiguousarray(idx_all.desc[idx_all.offsets[69]:idx_all.offsets[70]])
    sm = reg.votes(q)
    p69, p150 = int(np.searchsorted(reg.frames, 69)), int(np.searchsorted(reg.frames, 150))
    assert p150 == p69 + 1 and sm[p69] > 0 and sm[p150] < 0.1 * sm[p69], (sm[p69], sm[p150])
    pk = reg.query(q, 5)
    assert pk and pk[0][0] in (68, 69)


def test_frames_are_searched_in_the_audio_regions_and_in_the_whole_raw_only_when_needed(scene, built_gpu):
    cfg, cache = built_gpu["cfg"], built_gpu["cache"]
    comp, raw, truth = scene["comp"], scene["raw"], scene["truth"]
    am = _allowed(scene, cfg)
    roi = vm.box_roi(scene["layout"], comp)
    hints = _scene_hints(scene, list(range(0, 50)))
    built = []

    def whole():
        built.append(1)
        return vm.RawIndex.build(raw, cfg, cache)
    # placed and in the regions: the whole RAW is never built
    reg = vm.RawIndex.build(raw, cfg, cache, regions=[(0, 70)])
    reg.whole_fn = whole
    got = {k: a for k, a, _r in vm.run_searches(comp, raw, reg, am, roi, hints, [3, 33], cfg)}
    assert not built and all(got[k] and got[k][0].raw == truth[k]["raw"] for k in (3, 33))
    # k 40: placed by its sound, but its picture is shot 3's (outside the regions); k 64: placed, its picture outside
    # the regions; k 100: no sound places it -- each found in the whole RAW, built once
    reg = vm.RawIndex.build(raw, cfg, cache, regions=[(0, 70)])
    reg.whole_fn = whole
    assert vm.audio_window(hints, 100, comp.fps, raw.fps, cfg, raw.n) is None
    got = {k: a for k, a, _r in vm.run_searches(comp, raw, reg, am, roi, hints, [3, 40, 64, 100], cfg)}
    assert built == [1]
    for k in (3, 40, 64, 100):
        assert got[k] and abs(got[k][0].raw - truth[k]["raw"]) <= 1, (k, got[k][:1], truth[k])
    assert got[64][0].flip and got[40][0].raw >= 3 * SHOT_LEN
    # the same anchors as the search of the whole RAW finds for them
    full = {k: a for k, a, _r in vm.run_searches(comp, raw, built_gpu["index"], am, roi, hints, [3, 40, 64, 100], cfg)}
    assert [(a.raw, a.flip) for k in (3, 40, 64, 100) for a in got[k][:1]] == \
           [(a.raw, a.flip) for k in (3, 40, 64, 100) for a in full[k][:1]]
    reg.close_gpu()
    vm.shutdown_workers()
