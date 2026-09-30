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

import math
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
    cfg = Config(work_dir=str(tmp_path / "work"), out_dir=str(tmp_path / "out"))
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


def test_anchor_roundtrip_and_allowed_masks(scene):
    a = vm.Anchor(3, 17, True, Sim(0.9, 0.0, -1.5, 2.5), 40, 0.8, 12.5, 0.97, "rescue")
    assert vm.Anchor.from_dict(a.to_dict()) == a
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
