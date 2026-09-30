"""Tests for match_cuts.refine (Stage 5.3): m(k) on the synthetic numpy scene of test_visual_match.py
(two framings of one shot + a rotated re-use, a 5-frame jump cut, a 1-frame flash cut, a flipped segment,
a 1.10x segment built with the AE floor rule, a NOT-IN-RAW run, a uniform black frame and a caption
overlay that is found by overlay pass 2), plus small scenes for RAW freeze frames (visually identical
range) and a pure pan (time/translation confound)."""
from __future__ import annotations

import importlib.util
import json
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
    assert {"track", "track_model", "iteration", "overlay_pass2", "rescue_search", "frame_map"} <= kinds
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


def _mini(frames_full: np.ndarray, truth_raw: list[int], sim: Sim | list[Sim], rng) -> tuple[Proxy, Proxy, Layout]:
    """RAW proxy from full-res frames + a boxed competitor showing truth_raw[k] under sim (or sim[k])."""
    n = len(frames_full)
    rw, rh = int(S.RAW_FULL[0] * S.PR), int(S.RAW_FULL[1] * S.PR)
    rawp = np.stack([cv2.resize(f, (rw, rh), interpolation=cv2.INTER_AREA) for f in frames_full])
    raw = Proxy("raw", "", rawp, S.RAW_FULL, (rw / S.RAW_FULL[0], rh / S.RAW_FULL[1]), S.RAW_FPS,
                np.arange(n) / float(S.RAW_FPS), n)
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
