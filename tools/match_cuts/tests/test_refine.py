"""Tests for match_cuts.refine (Stage 5.3): m(k) on the synthetic numpy scene of test_visual_match.py
(two framings of one shot + a rotated re-use, a 5-frame jump cut, a 1-frame flash cut, a flipped segment,
a 1.10x segment built with the AE floor rule, a NOT-IN-RAW run, a uniform black frame and a caption
overlay that is found by overlay pass 2), plus small scenes for RAW freeze frames (visually identical
range) and a pure pan (time/translation confound)."""
from __future__ import annotations

import importlib.util
import json
import math
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import pytest

from match_cuts import refine
from match_cuts import visual_match as vm
from match_cuts.common import Cache, DecisionLog
from match_cuts.geometry import Sim, to_cv_matrix
from match_cuts.model import CAND_W, FrameMap, Layout, Proxy, Status

_spec = importlib.util.spec_from_file_location("_mc_scene", Path(__file__).with_name("test_visual_match.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("refine")
    sc = S.make_scene(0)
    cfg = S.make_config(tmp, workers=4)
    cache = Cache(tmp / "work")
    index = vm.RawIndex.build(sc["raw"], cfg, cache)
    overlays = S.Overlays()                        # captions are NOT pre-masked: overlay pass 2 must find them
    allowed = vm.AllowedMasks(sc["layout"], overlays, sc["comp"], cfg, use_layout_module=False)
    dlog = DecisionLog(tmp / "decisions.jsonl")
    anchors = vm.sparse_search(sc["comp"], sc["raw"], sc["layout"], overlays, index, None, cfg, dlog,
                               allowed_fn=allowed)
    fm = refine.build_frame_map(sc["comp"], sc["raw"], sc["layout"], overlays, anchors, None, index, cfg, cache,
                                dlog, tmp / "debug", allowed_fn=allowed,
                                residual_fn=refine._masks_from_residuals_local)
    dlog.close()
    return dict(sc=sc, cfg=cfg, cache=cache, index=index, overlays=overlays, anchors=anchors, fm=fm, tmp=tmp,
                allowed=allowed)


def test_frame_map_matches_truth_exactly(run):
    fm, sc = run["fm"], run["sc"]
    assert fm.n == sc["comp"].n
    errs = []
    for k, t in enumerate(sc["truth"]):
        if t["kind"] == "raw":
            assert fm.status[k] == Status.MATCH, (k, int(fm.status[k]), float(fm.score[k]))
            assert fm.raw[k] == t["raw"], (k, int(fm.raw[k]), t["raw"])
            assert bool(fm.flip[k]) == t["flip"], k
            ds, dp = S.sim_error(fm.sim(k), t["sim"])
            errs.append((ds, dp))
            assert ds <= 0.005 and dp <= 2.0, (k, ds, dp)
        elif t["kind"] == "none":
            assert fm.status[k] == Status.NONE and fm.raw[k] == -1, k
        else:
            assert fm.status[k] == Status.UNIFORM and fm.raw[k] == -1, k
    errs = np.array(errs)
    # the per-track models are much tighter than the acceptance tolerance
    assert errs[:, 0].max() < 0.002 and errs[:, 1].max() < 0.75, errs.max(axis=0)
    # the 1-frame flash cut (shot 3 inside the jump-cut segment of shot 0) is its own track
    assert fm.raw[40] == sc["truth"][40]["raw"] and fm.track[40] not in (fm.track[39], fm.track[41])
    # the 1.10x segment follows the AE floor rule frame by frame (incl. the 2-frame steps)
    steps = np.diff(fm.raw[79:104])
    assert set(steps.tolist()) == {1, 2}
    # rotation is kept when it is real (1.5 deg re-use), zeroed otherwise
    assert abs(fm.theta[110] - 1.5) < 0.05 and fm.theta[5] == 0.0


def test_frame_map_columns_are_consistent(run):
    fm, cfg = run["fm"], run["cfg"]
    m = fm.status == Status.MATCH
    half = CAND_W // 2
    for k in np.flatnonzero(m):
        assert fm.soft_lo[k] <= fm.raw_lo[k] <= fm.raw[k] <= fm.raw_hi[k] <= fm.soft_hi[k]
        assert fm.raw_lo[k] == fm.raw_hi[k] == fm.raw[k]           # content is temporally unique
        assert fm.cand_j0[k] == fm.raw[k] - half
        assert fm.cand[k, half] == pytest.approx(fm.score[k])
        # at least m-R..m+R evaluated under the frame's model
        R = cfg.refine_radius
        assert np.all(np.isfinite(fm.cand[k, half - R:half + R + 1])) or fm.raw[k] - R < 0
        assert fm.score[k] >= cfg.match_thresh and fm.second[k] < fm.score[k]
        assert fm.margin[k] == pytest.approx(fm.score[k] - fm.second[k], abs=1e-6)
        assert not fm.low_margin[k] and fm.conf[k] > 0.9
    assert np.all(np.isfinite(fm.mean)) and np.all(np.isfinite(fm.std))
    assert fm.std[78] < cfg.uniform_std and fm.conf[78] > 0.5
    # anchor inliers are recorded on searched frames that agree with m(k)
    assert (fm.inliers[m] >= cfg.min_inliers).sum() >= 30
    # NOT-IN-RAW frames: confident NONE (low best score), no RAW frame
    assert np.all(fm.score[70:78][np.isfinite(fm.score[70:78])] < cfg.none_thresh)
    assert np.all(fm.conf[70:78] > 0.9)
    # extra column: time/translation confound flags (none in this content)
    assert not fm.confounded.any()


def test_overlay_pass2_finds_the_caption(run):
    ov, sc = run["overlays"], run["sc"]
    cap = sc["caption_mask"]
    frames = ov.frames()
    assert set(frames) == set(sc["caption_frames"]), sorted(set(frames) ^ set(sc["caption_frames"]))
    for k in frames:
        m = ov.get(k)
        assert (m & cap).sum() >= 0.5 * m.sum()        # the masks sit on the caption
    # masked caption frames score like the others
    fm = run["fm"]
    assert np.min(fm.score[[k for k in frames if sc["truth"][k]["kind"] == "raw"]]) > 0.97


def test_anchors_of_flipped_segment_and_decisions(run):
    anchors, sc = run["anchors"], run["sc"]
    fl = [a for a in anchors if 50 <= a.k < 70]
    assert len(fl) >= 5
    for a in fl:
        assert a.flip and a.sim.s > 0 and a.raw == sc["truth"][a.k]["raw"]
        ds, dp = S.sim_error(a.sim, sc["sims"]["B"])
        assert ds < 0.005 and dp < 2.0
    recs = [json.loads(line) for line in (run["tmp"] / "decisions.jsonl").read_text().splitlines()]
    kinds = {r["decision"] for r in recs if r["stage"] == "refine"}
    assert {"track", "line_fit", "iteration", "overlay_pass2", "rescue_search", "frame_map", "temporal_labels"} <= kinds
    rescued = [r for r in recs if r["decision"] == "rescue_search"]
    assert any(r["comp_frame"] == 40 or any(f["raw"] == sc["truth"][40]["raw"] for f in r["found"])
               for r in rescued) or any(a.k == 40 for a in run["anchors"])


def test_cache_hit_reapplies_overlays(run):
    sc, cfg = run["sc"], run["cfg"]
    ov2 = S.Overlays()
    allowed = vm.AllowedMasks(sc["layout"], ov2, sc["comp"], cfg, use_layout_module=False)
    fm2 = refine.build_frame_map(sc["comp"], sc["raw"], sc["layout"], ov2, run["anchors"], None, run["index"], cfg,
                                 run["cache"], None, None, allowed_fn=allowed,
                                 residual_fn=refine._masks_from_residuals_local)
    for name, col in run["fm"].d.items():
        assert np.array_equal(col, fm2.d[name], equal_nan=True), name
    assert ov2.frames() == run["overlays"].frames()


def _sub_proxy(p: Proxy, a: int, b: int) -> Proxy:
    return Proxy(p.role, "", np.ascontiguousarray(p.frames[a:b]), p.full_size, p.ratio, p.fps, p.pts[a:b], b - a)


def test_determinism_workers_and_debug_pngs(run, tmp_path):
    """workers=1 and workers=3 give bit-identical FrameMaps; low-confidence PNGs are written."""
    sc = run["sc"]
    a0, a1 = 34, 76                                  # jump cut + flash + flipped + NOT-IN-RAW
    comp = _sub_proxy(sc["comp"], a0, a1)
    anchors = [vm.Anchor.from_dict({**a.to_dict(), "k": a.k - a0}) for a in run["anchors"] if a0 <= a.k < a1]
    outs = []
    for w in (1, 3):
        cfg = S.make_config(tmp_path / f"w{w}", workers=w)
        cfg.low_conf_thresh = 0.95                  # make the NOT-IN-RAW / caption frames produce PNGs
        ov = S.Overlays()
        allowed = vm.AllowedMasks(sc["layout"], ov, comp, cfg, use_layout_module=False)
        fm = refine.build_frame_map(comp, sc["raw"], sc["layout"], ov, anchors, None, run["index"], cfg, None, None,
                                    tmp_path / f"dbg{w}", allowed_fn=allowed,
                                    residual_fn=refine._masks_from_residuals_local)
        outs.append(fm)
        pngs = sorted((tmp_path / f"dbg{w}" / "low_confidence").glob("k*.png"))
        low = np.flatnonzero((fm.conf < cfg.low_conf_thresh) & (fm.status != Status.UNIFORM))
        assert [p.name for p in pngs] == [f"k{k:05d}.png" for k in low]
        if pngs:
            im = cv2.imread(str(pngs[0]))
            assert im is not None and im.shape[0] > 50
    for name, col in outs[0].d.items():
        assert np.array_equal(col, outs[1].d[name], equal_nan=True), name
    truth = sc["truth"][a0:a1]
    for k, t in enumerate(truth):
        if t["kind"] == "raw":
            assert outs[0].raw[k] == t["raw"], k


def _mm_proxy(p: Proxy, path: Path, a: int, b: int) -> Proxy:
    """Frames a..b of ``p`` saved to ``path`` and re-opened as a read-only memmap (as the pipeline does)."""
    np.save(path, np.ascontiguousarray(p.frames[a:b]))
    return Proxy(p.role, "", np.load(path, mmap_mode="r"), p.full_size, p.ratio, p.fps, p.pts[a:b], b - a,
                 npy_path=str(path))


def test_frame_map_bit_identical_inline_fork_spawn(run, tmp_path, monkeypatch):
    """Review finding real-world:F6 / DESIGN D7: refinement (tracks, ECC, overlay pass 2, rescue searches
    through the cached RAW index) runs in spawn pools where fork is unavailable, with FrameMaps and pass-2
    masks bit-identical to the inline and fork runs."""
    from match_cuts.layout import OverlayMasks
    sc = run["sc"]
    a0, a1 = 34, 76                                  # jump cut + flash + flipped + NOT-IN-RAW
    comp = _mm_proxy(sc["comp"], tmp_path / "comp.npy", a0, a1)
    raw = _mm_proxy(sc["raw"], tmp_path / "raw.npy", 0, sc["raw"].n)
    anchors = [vm.Anchor.from_dict({**a.to_dict(), "k": a.k - a0}) for a in run["anchors"] if a0 <= a.k < a1]
    outs = {}
    try:
        for mode, workers in (("inline", 1), ("fork", 3), ("spawn", 3)):
            monkeypatch.setenv(vm.START_METHOD_ENV, "spawn" if mode == "spawn" else "fork")
            cfg = S.make_config(tmp_path / mode, workers=workers)
            ov = OverlayMasks(comp.frames.shape[1:])
            allowed = vm.AllowedMasks(sc["layout"], ov, comp, cfg, use_layout_module=False)
            before = dict(vm.POOL_STATS)
            fm = refine.build_frame_map(comp, raw, sc["layout"], ov, anchors, None, run["index"], cfg, None, None,
                                        None, allowed_fn=allowed, residual_fn=refine._masks_from_residuals_local)
            if mode != "inline":
                assert vm.POOL_STATS[mode] >= before[mode] + 3, (mode, vm.POOL_STATS)
                assert vm.POOL_STATS["spawn_fallback"] == before["spawn_fallback"]
            outs[mode] = (fm, {k: ov.get(k) for k in ov.frames()})
    finally:
        vm.shutdown_workers()
    fm0, m0 = outs["inline"]
    for mode in ("fork", "spawn"):
        fm1, m1 = outs[mode]
        for name, col in fm0.d.items():
            assert np.array_equal(col, fm1.d[name], equal_nan=True), (mode, name)
        assert sorted(m1) == sorted(m0) and all(np.array_equal(m0[k], m1[k]) for k in m0), mode
    assert m0                                        # overlay pass 2 found the caption
    for k, t in enumerate(sc["truth"][a0:a1]):
        if t["kind"] == "raw":
            assert fm0.raw[k] == t["raw"], k


def test_refine_transform_recovers_truth_and_falls_back(run):
    sc, cfg = run["sc"], run["cfg"]
    comp, raw = sc["comp"], sc["raw"]
    roi = vm.box_roi(sc["layout"], comp)
    for k in (20, 60, 90):
        t = sc["truth"][k]
        init = Sim(t["sim"].s * 1.004, t["sim"].theta_deg + 0.1, t["sim"].tx + 3.0, t["sim"].ty - 2.0)
        allowed = vm.AllowedMasks(sc["layout"], run["overlays"], comp, cfg, use_layout_module=False)(k)
        sim, z = refine.refine_transform(np.asarray(comp.get(k)), np.asarray(raw.get(t["raw"])), init, t["flip"],
                                         raw.full_size[0], raw.ratio, comp.ratio, allowed, cfg, roi=roi)
        ds, dp = S.sim_error(sim, t["sim"])
        assert ds < 0.001 and dp < 0.5, (k, ds, dp)
        assert z > 0.99
    # uniform comp frame: no crash, returns the init
    init = sc["truth"][20]["sim"]
    sim, z = refine.refine_transform(np.asarray(comp.get(78)), np.asarray(raw.get(10)), init, False,
                                     raw.full_size[0], raw.ratio, comp.ratio, None, cfg, roi=roi)
    assert sim == init and not (z > 0.5)


def test_fit_track_model_constant_animated_rotation(run):
    cfg = run["cfg"]
    center = np.array([270.0, 455.0])
    rng = np.random.default_rng(1)
    base = S.centred_sim(0.85, 0.0)
    noisy = [(k, Sim(base.s * (1 + rng.normal(0, 3e-4)), rng.normal(0, 0.02), base.tx + rng.normal(0, 0.3),
                     base.ty + rng.normal(0, 0.3))) for k in range(0, 60, 3)]
    keys = refine.fit_track_model(noisy, center, cfg)
    assert len(keys) == 1 and keys[0]["rotation_deg"] == 0.0
    ds, dp = S.sim_error(Sim.from_dict(keys[0]), base)
    assert ds < 5e-4 and dp < 0.5
    # a linear push-in 0.85 -> 0.95 about the box centre: keys reproduce every frame
    zoom = [(k, S.centred_sim(0.85 + 0.1 * k / 60.0, 0.0)) for k in range(0, 61, 3)]
    keys = refine.fit_track_model(zoom, center, cfg)
    assert 2 <= len(keys) <= 4 and keys[0]["comp_frame"] == 0 and keys[-1]["comp_frame"] == 60
    for k in range(0, 61):
        est = refine._sim_at(keys, k, (960.0, 540.0))
        ds, dp = S.sim_error(est, S.centred_sim(0.85 + 0.1 * k / 60.0, 0.0))
        assert ds < 1e-3 and dp < 0.6, (k, ds, dp)
    # a real rotation (1.5 deg) is kept
    rot = [(k, S.centred_sim(0.9, 1.5)) for k in range(0, 30, 3)]
    keys = refine.fit_track_model(rot, center, cfg)
    assert len(keys) == 1 and abs(keys[0]["rotation_deg"] - 1.5) < 1e-6


def _mini(frames_full: np.ndarray, truth_raw: list[int], sim: Sim | list[Sim], rng,
          raw_fps=S.RAW_FPS) -> tuple[Proxy, Proxy, Layout]:
    """RAW proxy from full-res frames + a boxed competitor showing truth_raw[k] under sim (or sim[k])."""
    n = len(frames_full)
    rw, rh = int(S.RAW_FULL[0] * S.PR), int(S.RAW_FULL[1] * S.PR)
    rawp = np.stack([cv2.resize(f, (rw, rh), interpolation=cv2.INTER_AREA) for f in frames_full])
    raw = Proxy("raw", "", rawp, S.RAW_FULL, (rw / S.RAW_FULL[0], rh / S.RAW_FULL[1]), raw_fps,
                np.arange(n) / float(raw_fps), n)
    cw, ch = S.COMP_FULL
    cov = np.zeros((ch, cw), np.float32)
    b = S.BOX
    cov[int(b.y):int(b.y + b.h), int(b.x):int(b.x + b.w)] = 1.0
    sims = sim if isinstance(sim, list) else [sim] * len(truth_raw)
    comp = []
    for j, sk in zip(truth_raw, sims):
        m = to_cv_matrix(sk, False, S.RAW_FULL[0], (1.0, 1.0), (1.0, 1.0))
        img = cv2.warpAffine(frames_full[j].astype(np.float32), m, (cw, ch)) * cov
        img = np.clip(img + rng.normal(0, 1.5, img.shape), 0, 255).astype(np.uint8)
        comp.append(cv2.resize(img, (int(cw * S.PR), int(ch * S.PR)), interpolation=cv2.INTER_AREA))
    comp = np.stack(comp)
    cp = Proxy("competitor", "", comp, S.COMP_FULL, (S.PR, S.PR), S.COMP_FPS, np.arange(len(comp)) / 30.0, len(comp))
    return raw, cp, Layout(comp_w=cw, comp_h=ch, box=b)


def _mini_run(raw: Proxy, comp: Proxy, layout: Layout, tmp: Path) -> FrameMap:
    cfg = S.make_config(tmp, workers=4)
    idx = vm.RawIndex.build(raw, cfg, None)
    anchors = vm.sparse_search(comp, raw, layout, None, idx, None, cfg, None)
    return refine.build_frame_map(comp, raw, layout, None, anchors, None, idx, cfg, None, None, None,
                                  residual_fn=False)


def test_visually_identical_raw_frames_give_a_range(tmp_path):
    """RAW frames 20..23 are identical (a freeze in RAW): comp frames showing them get raw_lo=20, raw_hi=23
    (the criterion-3 exemption); other frames stay exact."""
    frames = np.stack(S.make_shot(7, 36, 0))
    frames[21:24] = frames[20]
    truth = [S.ae_frame(8.5, 1.0, k, 0) for k in range(24)]
    raw, comp, layout = _mini(frames, truth, S.centred_sim(0.85, 0.0), np.random.default_rng(3))
    fm = _mini_run(raw, comp, layout, tmp_path)
    for k, j in enumerate(truth):
        assert fm.status[k] == Status.MATCH, k
        if 20 <= j <= 23:
            assert (fm.raw_lo[k], fm.raw_hi[k]) == (20, 23) and 20 <= fm.raw[k] <= 23, (k, fm.raw_lo[k], fm.raw_hi[k])
            assert fm.soft_lo[k] <= 20 and fm.soft_hi[k] >= 23
        else:
            assert fm.raw[k] == j and fm.raw_lo[k] == fm.raw_hi[k] == j, (k, fm.raw[k], j)


def test_pure_pan_is_flagged_time_translation_confounded(tmp_path):
    """RAW = one static texture panning 4 px/frame: frame j+1 under a shifted transform explains the
    competitor as well as frame j -> the track is flagged (dlog + FrameMap 'confounded' column)."""
    rng = np.random.default_rng(5)
    big = cv2.resize(rng.integers(0, 255, (80, 260)).astype(np.float32), (2600, 800), interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), 2.0)
    frames = np.stack([np.clip(big[100:640, 4 * j:4 * j + 960], 0, 255).astype(np.uint8) for j in range(30)])
    truth = [S.ae_frame(5.5, 1.0, k, 0) for k in range(20)]
    raw, comp, layout = _mini(frames, truth, S.centred_sim(0.85, 0.0), rng)
    fm = _mini_run(raw, comp, layout, tmp_path)
    m = fm.status == Status.MATCH
    assert m.sum() >= 18
    assert fm.confounded[m].all()


def test_push_in_is_one_animated_track(tmp_path):
    """A linear push-in 0.85 -> 1.0 over 45 frames (1 % scale change per anchor stride, above the 0.5 % link
    tolerance) ends up as ONE animated track (chain merge) whose per-frame Sims follow the zoom."""
    frames = np.stack(S.make_shot(11, 40, 0))
    n = 33
    sims = [S.centred_sim(0.85 + 0.15 * k / (n - 1), 0.0) for k in range(n)]
    truth = [S.ae_frame(5.5, 1.0, k, 0) for k in range(n)]
    raw, comp, layout = _mini(frames, truth, sims, np.random.default_rng(9))
    fm = _mini_run(raw, comp, layout, tmp_path)
    for k in range(n):
        assert fm.status[k] == Status.MATCH and fm.raw[k] == truth[k], (k, int(fm.raw[k]), truth[k])
        ds, dp = S.sim_error(fm.sim(k), sims[k])
        assert ds < 0.005 and dp < 2.0, (k, ds, dp)
    vals, cnt = np.unique(fm.track, return_counts=True)
    assert cnt.max() >= n - 2, dict(zip(vals.tolist(), cnt.tolist()))


# ---------------------------------------------------------------------------------------------
# time-line-first refine (FX-03) and the competitor's repeat cadence on the refine side (FX-07)
# ---------------------------------------------------------------------------------------------

FILM_FPS = Fraction(24000, 1001)


def _texture(h: int, w: int, seed: int, cell: int = 6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.uniform(0, 255, (h // cell + 2, w // cell + 2)).astype(np.float32)
    big = cv2.resize(small, ((w // cell + 2) * cell, (h // cell + 2) * cell), interpolation=cv2.INTER_CUBIC)
    return cv2.GaussianBlur(big[:h, :w], (0, 0), 1.0)


def _camera_pan_raw(n: int, pan: int = 20, parallax: int = -4) -> np.ndarray:
    """Full-res RAW: a textured world panning ``pan`` px per RAW frame (the camera) under a 20 % layer moving
    ``parallax`` px per frame -- RAW j+1 is NOT RAW j shifted, so a compensating shift never explains a time error
    exactly (the 1411 regime)."""
    W, H = S.RAW_FULL
    bg = _texture(H, W + abs(pan) * n + 8, 31)
    fg = _texture(H, W + abs(parallax) * n + 8, 32, cell=10)
    out = []
    for j in range(n):
        a = bg[:, pan * j:pan * j + W]
        x = abs(parallax) * (n - j) if parallax < 0 else parallax * j
        out.append(np.clip(0.8 * a + 0.2 * fg[:, x:x + W], 0, 255).astype(np.uint8))
    return np.stack(out)


def _fm_run(raw: Proxy, comp: Proxy, layout: Layout, tmp: Path, anchors=None, rescue: bool = True):
    """build_frame_map with a decision log; real sparse-search anchors unless given. Returns (fm, records)."""
    cfg = S.make_config(tmp, workers=4)
    idx = vm.RawIndex.build(raw, cfg, None) if (anchors is None or rescue) else None
    if anchors is None:
        anchors = vm.sparse_search(comp, raw, layout, None, idx, None, cfg, None)
    dlog = DecisionLog(tmp / "decisions.jsonl")
    fm = refine.build_frame_map(comp, raw, layout, None, anchors, None, idx if rescue else None, cfg, None, dlog,
                                None, residual_fn=False)
    dlog.close()
    recs = [json.loads(line) for line in (tmp / "decisions.jsonl").read_text().splitlines()]
    return fm, recs


def _final_keys(recs: list[dict], track: int) -> list[dict]:
    fits = [r for r in recs if r["decision"] == "line_fit" and r["track"] == track and "keys" in r]
    assert fits, track
    return fits[-1]["keys"]


def test_camera_pan_under_editor_pan_one_run_on_the_truth_line(tmp_path):
    """FX-03 (a), the 1411 case: the RAW camera pans 20 px per RAW frame, the editor pans the crop -10.8 px per
    competitor frame (24000/1001 RAW on a 30 fps line: a pulldown repeat every 5th pair) and the anchors every 3
    frames sit +-1 RAW frame off the truth with framings shifted to compensate. Time line first: ONE run, every
    frame on the truth line, the path's tx slope within 0.1 px/frame, theta = 0; the competitor's own repeat pairs
    are REPEAT with the editor's -10.8 px as their warp (FX-07), and sim_meas holds the per-frame measurement."""
    n = 24
    u = float(FILM_FPS / S.COMP_FPS)
    truth = [int(math.floor(4.3 + u * k + 1e-9)) for k in range(n)]
    frames = _camera_pan_raw(truth[-1] + 6)
    s = 0.85
    sims = [Sim(s, 0.0, 21.5 - 10.8 * k, 225.3) for k in range(n)]
    raw, comp, layout = _mini(frames, truth, sims, np.random.default_rng(13), raw_fps=FILM_FPS)
    errs = [0, 1, -1, 1, 0, -1, 1, -1]
    anchors = [vm.Anchor(k, truth[k] + e, False, Sim(s, 0.0, sims[k].tx + 20.0 * s * e, sims[k].ty), 40, 0.8, 10.0,
                         0.95) for k, e in zip(range(0, n, 3), errs)]
    fm, recs = _fm_run(raw, comp, layout, tmp_path, anchors, rescue=False)
    assert (fm.status == Status.MATCH).all(), np.flatnonzero(fm.status != Status.MATCH)
    assert len(set(fm.track.tolist())) == 1, fm.track
    bad = [(k, int(fm.raw[k]), truth[k]) for k in range(n) if fm.raw[k] != truth[k]]
    assert not bad, bad
    slope = float(np.polyfit(np.arange(n), fm.tx, 1)[0])
    assert abs(slope + 10.8) < 0.1, slope
    assert np.all(fm.theta == 0.0)
    for k in range(n):
        ds, dp = S.sim_error(fm.sim(k), sims[k])
        assert ds < 0.003 and dp < 1.0, (k, ds, dp)
        meas = fm.sim_measured(k)
        assert meas is not None and fm.sim_meas_score[k] > 0.97
        ds, dp = S.sim_error(meas, sims[k])
        assert ds < 0.003 and dp < 1.0, (k, ds, dp)
    rep = [k for k in range(n - 1) if truth[k] == truth[k + 1]]
    assert len(rep) >= 4 and all(fm.pair_label[k] == 1 for k in rep), (rep, fm.pair_label.tolist())
    assert all(fm.pair_label[k] == 2 for k in range(n - 1) if k not in rep), fm.pair_label.tolist()
    assert np.all(np.abs(fm.pair_warp[rep, 0] + 10.8) < 0.5) and np.all(np.abs(fm.pair_warp[rep, 1]) < 0.5), \
        fm.pair_warp[rep]
    fit = [r for r in recs if r["decision"] == "line_fit"][-1]
    assert abs(fit["line"][0] - u) < 1e-6


def test_accelerating_pan_is_one_track_with_at_most_three_keys(tmp_path):
    """FX-03 (b): an editor pan accelerating from 4.2 to 13.8 px/frame at frame 19 (the 39-69 case) is ONE track
    on its time line whose path has <= 3 keys and reproduces every frame."""
    frames = np.stack(S.make_shot(41, 36, 0))
    n = 30
    tx = [21.5 - (4.2 * k if k <= 19 else 4.2 * 19 + 13.8 * (k - 19)) for k in range(n)]
    sims = [Sim(0.85, 0.0, x, 225.3) for x in tx]
    truth = [S.ae_frame(3.5, 1.0, k, 0) for k in range(n)]
    raw, comp, layout = _mini(frames, truth, sims, np.random.default_rng(17))
    fm, recs = _fm_run(raw, comp, layout, tmp_path)
    for k in range(n):
        assert fm.status[k] == Status.MATCH and fm.raw[k] == truth[k], (k, int(fm.raw[k]), truth[k])
        ds, dp = S.sim_error(fm.sim(k), sims[k])
        assert ds < 0.003 and dp < 1.0, (k, ds, dp)
    assert len(set(fm.track.tolist())) == 1, fm.track
    keys = _final_keys(recs, int(fm.track[0]))
    assert 2 <= len(keys) <= 3, keys


def test_raw_native_zoom_under_constant_editor_framing(tmp_path):
    """FX-03 (c), the escalade case: the RAW itself zooms 2 % per RAW frame under a constant editor framing; RAW
    j+1 with a 2 % smaller scale explains most of the picture, the time line does not move -> every frame exact
    and the framing constant within 0.3 %."""
    W, H = S.RAW_FULL
    tex = _texture(H, W, 51, cell=8)
    shot = S.make_shot(52, 40, 0)
    frames = []
    for j in range(40):
        M = cv2.getRotationMatrix2D((W / 2.0, H / 2.0), 0.0, 1.02 ** j)
        zt = cv2.warpAffine(tex, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        frames.append(np.clip(0.6 * zt + 0.4 * shot[j], 0, 255).astype(np.uint8))
    n = 30
    truth = [S.ae_frame(2.5, 1.0, k, 0) for k in range(n)]
    sim = S.centred_sim(0.85, 0.0)
    raw, comp, layout = _mini(np.stack(frames), truth, sim, np.random.default_rng(23))
    fm, _ = _fm_run(raw, comp, layout, tmp_path)
    for k in range(n):
        assert fm.status[k] == Status.MATCH and fm.raw[k] == truth[k], (k, int(fm.raw[k]), truth[k])
        ds, dp = S.sim_error(fm.sim(k), sim)
        assert ds < 0.003 and dp < 1.5, (k, ds, dp)
    s = fm.s[:n]
    assert (s.max() - s.min()) / float(np.median(s)) < 0.003, s


def test_punch_step_on_one_time_line_is_kept(tmp_path):
    """FX-03 (d): a x1.25 punch-in at frame 15 on ONE time line: the anchors link by time, the per-frame
    measurement finds the framing step and splits the track there (no smooth path through a step)."""
    frames = np.stack(S.make_shot(61, 40, 0))
    n = 30
    s1, s2 = 0.85, 0.85 * 1.25
    sims = [S.centred_sim(s1 if k < 15 else s2, 0.0) for k in range(n)]
    truth = [S.ae_frame(3.5, 1.0, k, 0) for k in range(n)]
    raw, comp, layout = _mini(frames, truth, sims, np.random.default_rng(29))
    fm, recs = _fm_run(raw, comp, layout, tmp_path)
    for k in range(n):
        assert fm.status[k] == Status.MATCH and fm.raw[k] == truth[k], (k, int(fm.raw[k]), truth[k])
        ds, dp = S.sim_error(fm.sim(k), sims[k])
        assert ds < 0.003 and dp < 1.0, (k, ds, dp)
    assert fm.track[14] != fm.track[15]
    assert len(set(fm.track[:15].tolist())) == 1 and len(set(fm.track[15:].tolist())) == 1, fm.track


def test_sustained_jump_inside_a_pan_is_kept(tmp_path):
    """FX-03 (e): a genuine +10 RAW frame jump sustained for 8 frames inside a continuous editor pan (-3 px/frame)
    is kept frame-exact: the time line breaks, no smooth path or +-2 tolerance absorbs it."""
    frames = np.stack(S.make_shot(71, 50, 0))
    n = 30
    truth = [S.ae_frame(3.5, 1.0, k, 0) + (10 if 12 <= k < 20 else 0) for k in range(n)]
    sims = [Sim(0.85, 0.0, 21.5 - 3.0 * k, 225.3) for k in range(n)]
    raw, comp, layout = _mini(frames, truth, sims, np.random.default_rng(31))
    fm, _ = _fm_run(raw, comp, layout, tmp_path)
    for k in range(n):
        assert fm.status[k] == Status.MATCH and fm.raw[k] == truth[k], (k, int(fm.raw[k]), truth[k])
        ds, dp = S.sim_error(fm.sim(k), sims[k])
        assert ds < 0.003 and dp < 1.5, (k, ds, dp)


def test_near_miss_anchors_only_join_an_existing_time_line(tmp_path):
    """FX-03 step 3: RANSAC near-misses never start a run. One on the run's time line joins it (and is used); one
    at another RAW time (a lookalike) is dropped -- no track of its own, every frame stays on the truth."""
    frames = np.stack(S.make_shot(91, 80, 0))
    n = 30
    sim = S.centred_sim(0.85, 0.0)
    truth = [S.ae_frame(3.5, 1.0, k, 0) for k in range(n)]
    raw, comp, layout = _mini(frames, truth, sim, np.random.default_rng(37))

    def anchor(k, j, src):
        return vm.Anchor(k, j, False, sim, 8 if src.endswith("_near") else 40, 0.6, 5.0, 0.97, src)
    anchors = [anchor(k, truth[k], "global") for k in range(0, 13, 3)]
    anchors += [anchor(21, truth[21], "global_near"), anchor(27, truth[27] + 30, "global_near")]
    fm, recs = _fm_run(raw, comp, layout, tmp_path, anchors, rescue=False)
    assert (fm.status == Status.MATCH).all() and all(fm.raw[k] == truth[k] for k in range(n))
    assert len(set(fm.track.tolist())) == 1
    rec = [r for r in recs if r["decision"] == "near_miss_anchors"]
    assert rec and rec[0]["joined"] == [[21, truth[21]]] and rec[0]["dropped"] == 1
    tracks = [r for r in recs if r["decision"] == "track"]
    assert all(all(a[0] <= 12 for a in r["anchors"]) for r in tracks)       # near-misses start no run


def test_pyramid_ecc_converges_where_single_level_sticks(tmp_path):
    """FX-03 step 2: a strong 20 px periodic texture over coarse structure, init 13 px off: single-level ECC
    settles on a neighbouring period (the stuck refit keys of the real run), the coarse-to-fine measurement
    converges (with and without the phase-correlation start); the theta-locked fit returns theta = 0 exactly
    from a rotated init."""
    W, H = S.RAW_FULL
    rng = np.random.default_rng(81)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    coarse = cv2.resize(rng.uniform(0, 255, (H // 60 + 1, W // 60 + 1)).astype(np.float32), (W, H),
                        interpolation=cv2.INTER_CUBIC)
    img = 128.0 + 50.0 * np.sin(2 * np.pi * xx / 20.0) * np.sin(2 * np.pi * yy / 20.0) + 0.5 * (coarse - 128.0)
    img = np.clip(img, 0, 255).astype(np.uint8)
    sim = S.centred_sim(0.85, 0.0)
    raw, comp, layout = _mini(np.stack([img, img]), [0], sim, np.random.default_rng(2))
    cfg = S.make_config(tmp_path)
    roi = vm.box_roi(layout, comp)
    allowed = vm.AllowedMasks(layout, None, comp, cfg, use_layout_module=False)(0)
    init = sim.translated(13.0 * 0.8, -13.0 * 0.6)
    args = (np.asarray(comp.get(0)), np.asarray(raw.get(0)), init, False, raw.full_size[0], raw.ratio, comp.ratio,
            allowed, cfg)
    s1, z1 = refine.refine_transform(*args, roi=roi)
    assert S.sim_error(s1, sim)[1] > 5.0, S.sim_error(s1, sim)
    for phase in (True, False):
        r = refine.ecc_measure(*args, roi=roi, phase=phase)
        ds, dp = S.sim_error(r.sim, sim)
        assert r.converged and ds < 0.001 and dp < 0.5 and r.z > z1 + 0.05, (phase, ds, dp, r.z, z1)
    rot = Sim.from_matrix(sim.matrix() @ Sim(1.0, 0.4, 0.0, 0.0).matrix())
    r = refine.ecc_measure(*args[:2], rot.translated(3.0, 2.0), *args[3:], roi=roi, lock_theta=True)
    assert r.sim.theta_deg == 0.0 and S.sim_error(r.sim, sim)[1] < 0.5 and r.z > 0.98, (r, S.sim_error(r.sim, sim))


def test_path_line_and_extrapolation_helpers():
    """Pure helpers: fit_path (accelerating pan -> 3 keys, wrong-frame outliers removed, constant -> 1 key),
    _steps (a punch step, never a velocity knot), _sim_at extrapolation (capped, then held), the snap-slope
    line, floor-phase cells and the repeat-label test."""
    cfg = refine_cfg = S.make_config(Path("."))
    center = np.array([270.0, 455.0])
    tx = lambda k: 21.5 - (4.2 * k if k <= 19 else 4.2 * 19 + 13.8 * (k - 19))  # noqa: E731
    smp = [(k, Sim(0.85, 0.0, tx(k), 225.3)) for k in range(30)]
    keys = refine.fit_path(smp, center, cfg)
    assert len(keys) == 3 and [kk["comp_frame"] for kk in keys] == [0, 19, 29], keys
    # every 5th frame measured on a wrong RAW frame with a compensating 17 px shift: same path
    bad = [(k, s.translated(17.0 * (1 if k % 10 == 3 else -1), 0.0)) if k % 5 == 3 else (k, s) for k, s in smp]
    keys2 = refine.fit_path(bad, center, cfg)
    assert len(keys2) <= 3
    for k in range(30):
        assert abs(refine._sim_at(keys2, k, (960.0, 540.0)).tx - tx(k)) < 0.5, k
    assert len(refine.fit_path([(k, Sim(0.85, 0.0, 21.5 + 0.1 * (k % 2), 225.3)) for k in range(20)], center,
                               cfg)) == 1
    # steps: a x1.25 punch at 15 is a step; the velocity knot of the pan is not
    punch = [(k, S.centred_sim(0.85 if k < 15 else 0.85 * 1.25, 0.0)) for k in range(30)]
    assert refine._steps(punch, center, cfg) == [15]
    assert refine._steps(smp, center, cfg) == []
    shift = [(k, Sim(0.85, 0.0, 21.5 - k + (28.0 if k >= 12 else 0.0), 225.3)) for k in range(24)]
    assert refine._steps(shift, center, cfg) == [12]
    # extrapolation beyond the last key: linear for cap frames, then held
    kk = [refine._key(0, Sim(1.0, 0.0, 0.0, 0.0)), refine._key(10, Sim(1.0, 0.0, -50.0, 0.0))]
    assert refine._sim_at(kk, 12, (960.0, 540.0), 3).tx == pytest.approx(-60.0)
    assert refine._sim_at(kk, 20, (960.0, 540.0), 3).tx == pytest.approx(-65.0)
    assert refine._sim_at(kk, -2, (960.0, 540.0), 3).tx == pytest.approx(10.0)
    assert refine._sim_at(kk, 20, (960.0, 540.0)).tx == pytest.approx(-50.0)        # cap 0: the old hold
    # time line: +-1 errors around floor(4.3 + 0.7992 k) -> the 1.0-speed slope, x inside the truth cell
    u1 = float(FILM_FPS / S.COMP_FPS)
    ks = np.arange(0, 30, 3, dtype=np.float64)
    truth = np.floor(4.3 + u1 * ks + 1e-9)
    hj = truth + np.array([0, 1, -1, 1, 0, -1, 1, -1, 0, 1])
    u, x, inl = refine._robust_line(ks, hj, refine._snap_slopes(u1, refine_cfg), u1, 2.0, 0.7)
    assert abs(u - u1) < 1e-9 and inl.all() and abs(x - 4.3) < 1.0
    cells = refine._line_cells(u1, x, np.arange(30))
    lines = [refine._line_frames(u1, c, np.arange(30)) for c in cells]
    want = np.floor(4.3 + u1 * np.arange(30) + 1e-9).astype(int)
    assert any(np.array_equal(ln, want) for ln in lines)
    # repeat labels: the truth cadence has no disagreement, a line one phase cell away has some
    lab = {k: (1 if want[k] == want[k + 1] else 2) for k in range(29)}
    assert refine._label_disagreements(dict(enumerate(want.tolist())), lab) == (0, 29)
    other = np.floor(4.55 + u1 * np.arange(30) + 1e-9).astype(int)
    assert refine._label_disagreements(dict(enumerate(other.tolist())), lab)[0] > 0
    # repeat cadence as speed evidence (the S74 fake 0.667x): +-1-noisy hints over 12 frames fit the 0.667x slope
    # as well as 1.0x; the competitor's repeat every 5th pair picks 1.0x, and without labels the order stands
    K = np.arange(12)
    alts = [(u1 / 1.5, 4.8, None), (u1, 4.8, None)]
    lab12 = {k: (1 if want[k] == want[k + 1] else 2) for k in range(11)}
    best, dis = refine._label_speed_choice(K, alts, lab12)
    assert best == 1 and dis[1] == 0 < dis[0], dis
    assert refine._label_speed_choice(K, alts, {k: 0 for k in range(11)}) == (0, [])
