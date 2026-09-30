"""Unit tests for verify.py (Stage 9): coverage tiling, criterion-2 cut logic (stub and real scoring),
AE-simulation comparison, mock-record evaluation, speed/framing, audio exception codes, criteria
aggregation and the determinism comparison. The analysis modules are replaced by small stubs."""
from __future__ import annotations

import json
import math
import sys
import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import match_cuts
from match_cuts import verify
from match_cuts.config import Config
from match_cuts.geometry import Sim
from match_cuts.model import Box, Cutlist, FrameMap, Proxy, Segment, Status

F30 = Fraction(30)


# ---------------------------------------------------------------------------------------------
# helpers / stubs
# ---------------------------------------------------------------------------------------------

def install(monkeypatch, name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(f"match_cuts.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"match_cuts.{name}", mod)
    monkeypatch.setattr(match_cuts, name, mod, raising=False)
    return mod


def ae_frame(raw_in, v, k, comp_in, comp_fps, raw_fps, rule="floor"):
    x = (float(raw_in) + float(v) * (int(k) - int(comp_in)) / float(comp_fps)) * float(raw_fps)
    return int(math.floor(x + 1e-9)) if rule == "floor" else int(math.floor(x + 0.5))


@pytest.fixture
def phase(monkeypatch):
    return install(monkeypatch, "phase_solve", ae_frame=ae_frame,
                   feasible_speed_range=lambda ks, lo, hi, comp_in, cf, rf, **kw: (0.999, 1.001))


IDENT = Sim(1.0, 0.0, 0.0, 0.0).to_dict()


def seg(id, type_, a, b, j0=None, speed=1.0, fps=F30, **kw) -> Segment:
    s = Segment(id=id, type=type_, comp_in=a, comp_out=b, speed=speed, **kw)
    if type_ == "raw":
        if j0 is not None:
            s.raw_in_seconds = (j0 + 0.5) / float(fps)
            s.raw_in_frame = j0
            s.raw_out_frame = j0 + (b - a) - 1
        if s.transform is None and not s.transform_keys:
            s.transform = dict(IDENT)
    if type_ == "not_in_raw" and "label" not in kw:
        s.label = "MISSING - not in RAW"
    return s


def xfade(d: int) -> dict:
    return {"type": "crossfade", "duration_frames": d, "alpha": [i / d for i in range(d)]}


def frame_map(truth: list[int], status=Status.MATCH) -> FrameMap:
    n = len(truth)
    fm = FrameMap(n)
    t = np.asarray(truth, np.int32)
    fm.status = np.full(n, status, np.int8)
    fm.raw = t
    fm.raw_lo = t
    fm.raw_hi = t
    fm.soft_lo = t
    fm.soft_hi = t
    fm.score = np.full(n, 0.99, np.float32)
    fm.s = np.ones(n)
    fm.theta = np.zeros(n)
    fm.tx = np.zeros(n)
    fm.ty = np.zeros(n)
    return fm


class StubScorer:
    """score = 1 when the candidate is the truth RAW frame (with the right flip), decreasing with distance."""

    def __init__(self, truth: dict[int, int | None], alpha: dict[int, float] | None = None, std: dict[int, float] | None = None,
                 flips: dict[int, bool] | None = None):
        self.truth, self.alpha, self.std, self.flips = truth, alpha or {}, std or {}, flips or {}

    def score(self, k, cands):
        out = []
        for c in cands:
            if c is None:
                out.append(float("nan"))
                continue
            t = self.truth.get(k)
            if t is None:
                out.append(0.2)
            else:
                ok_flip = bool(c[2]) == self.flips.get(k, False)
                out.append(max(0.0, 1.0 - 0.02 * abs(int(c[0]) - t)) - (0 if ok_flip else 0.5))
        return np.array(out, float)

    def blend(self, k, a, b):
        return self.alpha.get(k, float("nan")), 0.99

    def uniform(self, k):
        return 0.0, self.std.get(k, 50.0)


# ---------------------------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------------------------

def test_aggregate_statuses():
    assert verify.aggregate(["pass", "pass"]) == "pass"
    assert verify.aggregate(["pass", "pass_with_exceptions"]) == "pass_with_exceptions"
    assert verify.aggregate(["pass", "fail", "pass_with_exceptions"]) == "fail"
    assert verify.aggregate(["not_available", "pass"]) == "pass"
    assert verify.aggregate(["not_available", "not_available"]) == "not_available"
    assert verify.aggregate([]) == "not_available"
    assert verify.aggregate(["pass", "weird"]) == "fail"


def test_crashed_result_fails_every_criterion():
    r = verify.crashed_result("boom")
    assert set(r["criteria"]) == set(verify.CRITERIA)
    assert all(c["status"] == "fail" for c in r["criteria"].values())
    assert set(r["checks"]) == set(verify.CHECKS)


# ---------------------------------------------------------------------------------------------
# s9_1 coverage
# ---------------------------------------------------------------------------------------------

def test_coverage_exact_tiling_passes():
    segs = [seg(1, "raw", 0, 10, 100), seg(2, "not_in_raw", 10, 15), seg(3, "raw", 15, 30, 400)]
    r = verify.check_coverage(segs, 30)
    assert r["status"] == "pass", r
    assert r["covered"] == 30 and r["gaps"] == [] and r["overlaps"] == []


def test_coverage_gap_and_short_timeline_fail():
    r = verify.check_coverage([seg(1, "raw", 0, 10, 100), seg(2, "raw", 12, 30, 400)], 30)
    assert r["status"] == "fail"
    assert r["gaps"] == [[10, 11]]
    r = verify.check_coverage([seg(1, "raw", 0, 28, 100)], 30)
    assert r["status"] == "fail" and any("instead of" in f for f in r["failures"])


def test_coverage_unexplained_overlap_fails():
    r = verify.check_coverage([seg(1, "raw", 0, 12, 100), seg(2, "raw", 10, 30, 400)], 30)
    assert r["status"] == "fail"
    assert r["overlaps"] == [[10, 11]]


def test_coverage_crossfade_overlap_is_explained():
    a = seg(1, "raw", 0, 16, 100, transition_out=xfade(6))
    b = seg(2, "raw", 10, 30, 400, transition_in=xfade(6))
    r = verify.check_coverage([a, b], 30)
    assert r["status"] == "pass", r["failures"]
    assert r["transitions"] == [{"frames": [10, 15], "from": 1, "to": 2, "type": "crossfade"}]


def test_coverage_crossfade_wrong_duration_fails():
    a = seg(1, "raw", 0, 16, 100)
    b = seg(2, "raw", 10, 30, 400, transition_in=xfade(5))
    assert verify.check_coverage([a, b], 30)["status"] == "fail"


def test_coverage_mapping_and_label_required():
    s1 = seg(1, "raw", 0, 10)            # no raw_in_seconds, no remap keys
    s2 = seg(2, "not_in_raw", 10, 30, label="")
    r = verify.check_coverage([s1, s2], 30)
    assert r["status"] == "fail"
    assert any("without a RAW mapping" in f for f in r["failures"])
    assert any("without a label" in f for f in r["failures"])
    s3 = seg(3, "raw", 0, 30, time_remap_keys=[{"comp_frame": 0, "raw_seconds": 1.0}])
    assert verify.check_coverage([s3], 30)["status"] == "pass"


def test_coverage_extra_region_is_an_exception():
    lb = {"regions": [{"x": 0, "y": 0, "w": 10, "h": 10}],
          "periods": [{"comp_in": 5, "comp_out": 9, "mode": "pip", "box": None}]}
    r = verify.check_coverage([seg(1, "raw", 0, 30, 100)], 30, lb)
    assert r["status"] == "pass_with_exceptions"
    assert r["extra_region_frames"] == [[5, 8]]


# ---------------------------------------------------------------------------------------------
# c2 cuts -- decision logic with a stub scorer
# ---------------------------------------------------------------------------------------------

def _two_shots():
    return [seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 500)]


def test_cuts_hard_cut_passes(phase):
    truth = {k: (100 + k if k < 10 else 500 + k - 10) for k in range(20)}
    r = verify.check_cuts(_two_shots(), F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "pass", r
    sides = r["cuts"][0]["sides"]
    assert [s["result"] for s in sides] == ["ok", "ok"]
    assert sides[0]["own"] == 109 and sides[0]["other"] == 499      # B's model extended back one frame


def test_cuts_off_by_one_cut_fails(phase):
    truth = {k: (100 + k if k < 11 else 500 + k - 10) for k in range(20)}   # the real cut is at 11
    r = verify.check_cuts(_two_shots(), F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "fail"
    assert r["cuts"][0]["sides"][1]["result"] == "fail"
    assert "frame 10" in r["failures"][0]


def test_cuts_flip_decides(phase):
    a, b = seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 110, flip_h=True)   # same RAW moment, B flipped
    truth = {k: 100 + k for k in range(20)}
    flips = {k: k >= 10 for k in range(20)}
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 1000, StubScorer(truth, flips=flips), Config())
    assert r["status"] == "pass", r


def test_cuts_indistinguishable_models_are_exceptions(phase):
    a, b = seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 110)        # continuous: no visible discontinuity
    truth = {k: 100 + k for k in range(20)}
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "pass_with_exceptions"
    assert {s["result"] for s in r["cuts"][0]["sides"]} == {"indistinguishable"}


def test_cuts_speed_only_ambiguity_exempts(phase):
    a, b = seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 500, cut_ambiguity=[9, 11])
    truth = {k: (100 + k if k < 11 else 500 + k - 10) for k in range(20)}
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "pass_with_exceptions"
    assert "exempt" in [s["result"] for s in r["cuts"][0]["sides"]]


def test_cuts_placeholder_neighbours(phase):
    segs = [seg(1, "raw", 0, 10, 100), seg(2, "not_in_raw", 10, 15), seg(3, "raw", 15, 25, 300)]
    truth = {k: (100 + k if k < 10 else (None if k < 15 else 300 + k - 15)) for k in range(25)}
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "pass", r
    assert [c["kind"] for c in r["cuts"]] == ["raw_to_placeholder", "placeholder_to_raw"]
    truth[10] = 110       # the placeholder's first frame is really A's next frame -> cut too early
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "fail"


def test_cuts_crossfade_alpha_ramp(phase):
    a = seg(1, "raw", 0, 16, 100, transition_out=xfade(6))
    b = seg(2, "raw", 10, 30, 500, transition_in=xfade(6))
    truth = {k: (100 + k if k < 10 else 500 + k - 10) for k in range(30)}
    good = {10 + i: i / 6 for i in range(6)}
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 1000, StubScorer(truth, alpha=good), Config())
    assert r["status"] == "pass", r
    assert r["cuts"][0]["kind"] == "crossfade" and r["cuts"][0]["alpha_max_err"] == 0.0
    bad = {10 + i: 0.5 for i in range(6)}
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 1000, StubScorer(truth, alpha=bad), Config())
    assert r["status"] == "fail"


def test_cuts_dip_neighbours(phase):
    segs = [seg(1, "raw", 0, 10, 100), seg(2, "dip", 10, 12, color="#000000"), seg(3, "raw", 12, 20, 300)]
    truth = {k: (100 + k if k < 10 else 300 + k - 12) for k in range(20)}
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, StubScorer(truth, std={10: 0.5, 11: 0.5}), Config())
    assert r["status"] == "pass", r
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, StubScorer(truth, std={10: 30.0, 11: 0.5}), Config())
    assert r["status"] == "fail"


# ---------------------------------------------------------------------------------------------
# c2 with real masked-ZNCC scoring on small arrays
# ---------------------------------------------------------------------------------------------

def _textures(n: int, w: int = 48, h: int = 32, seed: int = 0) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    out = np.empty((n, h, w), np.uint8)
    for i in range(n):
        img = rng.uniform(0, 255, (h, w)).astype(np.float32)
        out[i] = np.clip(cv2.GaussianBlur(img, (0, 0), 1.2) * 2.5 - 190, 0, 255).astype(np.uint8)
    return out


def _proxy(frames: np.ndarray, role: str) -> Proxy:
    n, h, w = frames.shape
    return Proxy(role, f"{role}.mp4", frames, (w, h), (1.0, 1.0), F30, np.arange(n) / 30.0, n)


def test_cuts_with_real_scoring(phase):
    raw = _textures(40)
    truth = [5 + k if k < 10 else 30 + (k - 10) for k in range(20)]
    comp = raw[truth]
    scorer = verify.ProxyScorer(_proxy(comp, "competitor"), _proxy(raw, "raw"), None, lambda k: None, 48, Config())
    segs = [seg(1, "raw", 0, 10, 5), seg(2, "raw", 10, 20, 30)]
    r = verify.check_cuts(segs, F30, F30, (48, 32), 40, scorer, Config())
    assert r["status"] == "pass", r
    s = r["cuts"][0]["sides"][0]
    assert s["s_own"] > 0.99 and s["s_other"] < 0.5
    late = [seg(1, "raw", 0, 11, 5), seg(2, "raw", 11, 20, 31)]    # cut placed one frame late
    r = verify.check_cuts(late, F30, F30, (48, 32), 40, scorer, Config())
    assert r["status"] == "fail"


# ---------------------------------------------------------------------------------------------
# s9_2 AE simulation vs m(k)
# ---------------------------------------------------------------------------------------------

def _sim_frames(js: list[int | None]) -> dict[int, list[dict]]:
    return {K: ([] if j is None else [{"layer": 1, "raw_frame": j, "opacity": 100.0}]) for K, j in enumerate(js)}


def test_visible_raw_frame_order_and_opacity():
    ents = [{"layer": 2, "raw_frame": 7, "opacity": 100}, {"layer": 1, "raw_frame": 3, "opacity": 100}]
    assert verify.visible_raw_frame(ents) == 3                       # layer 1 is on top
    ents = [{"layer": 1, "raw_frame": 3, "opacity": 40}, {"layer": 2, "raw_frame": 7, "opacity": 100}]
    assert verify.visible_raw_frame(ents) == 7                       # upper layer semi-transparent
    assert verify.visible_raw_frame([{"layer": 1, "raw_frame": 3, "opacity": 0.5}]) is None
    assert verify.visible_raw_frame([{"layer": 1, "raw_frame": 3, "opacity": 1.0}]) == 3   # 0..1 scale
    assert verify.visible_raw_frame([]) is None


def test_ae_sim_exact_ambiguous_tie_and_mismatch():
    truth = list(range(100, 300))
    fm = frame_map(truth)
    r = verify.check_ae_sim(_sim_frames(truth), fm, F30, F30, 200, [], Config())
    assert r["status"] == "pass" and r["exact"] == 200
    sim = list(truth)
    sim[5] = truth[5] + 1
    fm.raw_lo[5], fm.raw_hi[5] = truth[5] - 1, truth[5] + 1          # ambiguous-identical neighbours
    sim[7] = truth[7] - 1
    fm.tie[7] = True                                                  # timing-tie frame
    sim[9] = truth[9] + 3                                             # a real mismatch (0.5 % of frames)
    r = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 200, [], Config())
    assert r["status"] == "pass_with_exceptions", r
    assert [x["k"] for x in r["ambiguous_identical"]] == [5]
    assert [x["k"] for x in r["timing_tie"]] == [7]
    assert [x["k"] for x in r["mismatches"]] == [9]
    for k in (20, 21):                                               # 1.5 % mismatched -> fail
        sim[k] = truth[k] + 5
    r = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 200, [], Config())
    assert r["status"] == "fail"


def test_ae_sim_skips_blend_and_placeholder_frames():
    truth = list(range(50))
    fm = frame_map(truth)
    fm.status[10:16] = Status.BLEND
    fm.status[30:35] = Status.NONE
    sim = [None if 10 <= K < 16 or 30 <= K < 35 else j for K, j in enumerate(truth)]
    r = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 50, [], Config())
    assert r["status"] == "pass" and r["matched"] == 39


def test_ae_sim_on_a_different_main_grid():
    comp_fps, main_fps = Fraction(30), Fraction(24000, 1001)
    truth = list(range(1000, 1060))
    fm = frame_map(truth)
    n_main = math.floor(60 * main_fps / comp_fps + Fraction(1, 2))
    sim = [truth[verify.main_to_comp(K, comp_fps, main_fps)] for K in range(n_main)]
    r = verify.check_ae_sim(_sim_frames(sim), fm, comp_fps, main_fps, n_main, [24], Config())
    assert r["status"] == "pass" and r["excluded_near_cuts"] == 2
    assert verify.main_to_comp(10, comp_fps, main_fps) == math.floor(10 * 30 / (24000 / 1001))


# ---------------------------------------------------------------------------------------------
# c6 mock checks
# ---------------------------------------------------------------------------------------------

def _plan_and_record(tmp_path: Path, fps=Fraction(30000, 1001)):
    """A plan and mock records in export_ae's formats: RAW layers inside a 'Video Box' pre-comp,
    'mc:<id>' tags, footage by id, saved lists, call counters, the four DESIGN scenarios."""
    num, den = fps.numerator, fps.denominator
    T = lambda k: k * den / num  # noqa: E731
    st1 = 100 / 1.1
    plan = {"main": {"name": "Recreated Edit", "w": 1080, "h": 1920, "fps": {"num": num, "den": den}, "frames": 90},
            "segComp": "box", "rawFps": {"num": 30000, "den": 1001}, "footage": {"raw": {}, "ref": None},
            "layers": [{"id": "seg1", "kind": "raw", "name": "S01  RAW", "compIn": 0, "compOut": 40, "timeMode": "stretch",
                        "stretch": st1, "rawIn": 12.345, "startTime": T(0) - 12.345 / (100 / st1),
                        "inPoint": T(0), "outPoint": T(40)},
                       {"id": "seg2", "kind": "raw", "name": "S02  RAW  FREEZE", "compIn": 40, "compOut": 90,
                        "timeMode": "remap", "stretch": None, "rawIn": 3.0, "startTime": T(40), "inPoint": T(40),
                        "outPoint": T(90)},
                       {"id": "seg2_audio", "kind": "raw_audio", "name": "S02 audio", "compIn": 36, "compOut": 90,
                        "timeMode": "stretch", "stretch": 100.0, "rawIn": 2.9, "startTime": T(36) - 2.9,
                        "inPoint": T(36), "outPoint": T(90)}]}
    raw_layers = [
        {"index": 1, "name": "S01  RAW", "comment": "mc:seg1", "sourceType": "footage", "sourceId": 6, "stretch": st1,
         "startTime": T(0) - 12.345 / (100 / st1), "inPoint": T(0), "outPoint": T(40), "timeRemapEnabled": False},
        {"index": 2, "name": "S02  RAW  FREEZE", "comment": "mc:seg2", "sourceType": "footage", "sourceId": 6,
         "stretch": 100.0, "startTime": T(40), "inPoint": T(40), "outPoint": T(90), "timeRemapEnabled": True,
         "props": {"ADBE Time Remapping": {"keys": [
             {"layerTime": 0.0, "value": 3.0, "inInterp": "LINEAR", "outInterp": "LINEAR"},
             {"layerTime": T(50), "value": 3.0, "inInterp": "LINEAR", "outInterp": "LINEAR"}]}}},
        {"index": 3, "name": "S02 audio", "comment": "mc:seg2_audio", "sourceType": "footage", "sourceId": 6,
         "enabled": False, "stretch": 100.0, "startTime": T(36) - 2.9, "inPoint": T(36), "outPoint": T(90)}]
    main_layers = [
        {"index": 1, "name": "REFERENCE", "comment": "mc:ref", "sourceType": "footage", "sourceId": 7, "guideLayer": True,
         "enabled": False, "startTime": 0, "inPoint": 0, "outPoint": T(90), "stretch": 100},
        {"index": 2, "name": "Video Box", "comment": "mc:box", "sourceType": "comp", "sourceId": 9, "startTime": 0.0,
         "stretch": 100.0, "inPoint": 0.0, "outPoint": T(90)}]
    rec = {"record_type": "ae_mock", "status": "ok", "mock_errors": [], "calls": {"beginUndoGroup": 1, "endUndoGroup": 1},
           "footage": [{"id": 6, "name": "raw.mp4", "comment": "mc:raw", "fps_num": 30000, "fps_den": 1001},
                       {"id": 7, "name": "competitor_ref.mp4", "comment": "mc:ref", "fps_num": num, "fps_den": den}],
           "comps": [{"id": 8, "name": "Recreated Edit", "comment": "mc:main", "frameRate": num / den,
                      "duration": 90 * den / num, "workAreaStart": 0.0, "workAreaDuration": 90 * den / num,
                      "width": 1080, "height": 1920, "layers": main_layers},
                     {"id": 9, "name": "Video Box", "comment": "mc:box", "layers": raw_layers}],
           "saved": [str(tmp_path / "recreated_edit.aep")], "alerts": ["match_cuts: built Recreated Edit"]}
    mm = {"record_type": "ae_mock", "status": "ok", "alerts": ["Cancelled: RAW video not found"], "saved": [],
          "calls": {"openDialog": 1}, "dialogs": ["Locate the RAW video"]}
    npn = {"record_type": "ae_mock", "status": "ok", "alerts": ["Cancelled: no project"], "saved": [], "calls": {}}
    nmp = json.loads(json.dumps(rec))
    return plan, {"default": rec, "media_missing": mm, "new_project_null": npn, "no_marker_property": nmp}


def test_simulate_record_walks_the_video_box_and_remap_keys(tmp_path):
    fps = Fraction(30000, 1001)
    plan, recs = _plan_and_record(tmp_path, fps)
    frames = verify.simulate_record(recs["default"], "raw.mp4", fps, fps, 90)
    got = [verify.visible_raw_frame(frames[K]) for K in range(90)]
    want = [math.floor((12.345 + 1.1 * K * 1001 / 30000) * 30000 / 1001 + 1e-9) for K in range(40)] \
        + [math.floor(3.0 * 30000 / 1001 + 1e-9)] * 50
    assert got == want
    assert all(len(frames[K]) == 1 for K in range(90))          # disabled audio duplicate and guide ignored


def test_mock_checks_pass_and_fail(tmp_path):
    fps = Fraction(30000, 1001)
    plan, recs = _plan_and_record(tmp_path, fps)
    r = verify.check_mock(plan, recs, fps, 90, tmp_path, "raw.mp4", layer_checker=None)
    assert r["status"] == "pass", r["failures"]
    assert {c["check"] for c in r["checks"]} >= {"MAIN frameRate == main_fps", "work area == whole comp",
                                                 "media_missing: File.openDialog called", "new_project_null: clean abort"}

    def mutated(fn):
        bad = json.loads(json.dumps(recs))
        fn(bad)
        return verify.check_mock(plan, bad, fps, 90, tmp_path, "raw.mp4", layer_checker=None)
    assert mutated(lambda b: b["default"]["comps"][0].update(frameRate=29.97))["status"] == "fail"
    assert mutated(lambda b: b["default"]["comps"][0].update(frameRate=float(np.float32(30000 / 1001))))["status"] == "pass"
    assert mutated(lambda b: b["default"]["comps"][1]["layers"][0].update(startTime=-1.0))["status"] == "fail"
    assert mutated(lambda b: b["default"]["comps"][0].update(workAreaDuration=1.0))["status"] == "fail"
    assert mutated(lambda b: b["media_missing"]["calls"].update(openDialog=0))["status"] == "fail"
    assert mutated(lambda b: b["media_missing"].update(saved=["x.aep"]))["status"] == "fail"
    assert mutated(lambda b: b["new_project_null"].update(saved=["x.aep"]))["status"] == "fail"
    assert mutated(lambda b: b["default"]["alerts"].append("build_ae_project.jsx failed (line 12): Error: boom"))["status"] == "fail"
    assert mutated(lambda b: b["default"].update(saved=[str(tmp_path / "elsewhere.aep")]))["status"] == "fail"
    assert mutated(lambda b: b["default"].update(mock_errors=["unknown member layer.guidelayer"]))["status"] == "fail"
    assert mutated(lambda b: b["default"].update(status="script_error", error="TypeError"))["status"] == "fail"
    assert mutated(lambda b: b["default"]["comps"][1]["layers"].pop(0))["status"] == "fail"
    # the JSX self-check switching a stretch layer to frame-exact remap is allowed (and reported)
    def switch(b):
        L = b["default"]["comps"][1]["layers"][0]
        L.update(timeRemapEnabled=True, startTime=0.0, stretch=100.0)
    r = mutated(switch)
    assert r["status"] == "pass" and r["switched_to_frames"] == ["S01  RAW"]
    na = verify.check_mock(plan, {"default": {"status": "not_available", "reason": "node missing"}}, fps, 90, tmp_path, "raw.mp4")
    assert na["status"] == "not_available"
    # per-layer key / render-switch problems reported by the layer checker fail c6
    r = verify.check_mock(plan, recs, fps, 90, tmp_path, "raw.mp4",
                          layer_checker=lambda P, L, F: ["ADBE Opacity: 0 keys (plan 8)"] if P["id"] == "seg1" else [])
    assert r["status"] == "fail" and "ADBE Opacity" in r["failures"][0]


def test_mock_checks_generic_record_format(tmp_path):
    """Records without mc: tags / footage ids are matched by layer name and source file name."""
    fps = Fraction(30)
    plan = {"main": {"name": "Recreated Edit", "fps": {"num": 30, "den": 1}, "frames": 30},
            "layers": [{"name": "S01", "compIn": 0, "compOut": 30, "stretch": 100.0, "rawIn": 2.0}]}
    rec = {"comps": [{"name": "Recreated Edit", "frameRate": 30.0, "duration": 1.0, "workAreaStart": 0, "workAreaDuration": 1.0,
                      "layers": [{"name": "S01", "source": "raw.mp4", "startTime": -2.0, "stretch": 100.0,
                                  "inPoint": 0.0, "outPoint": 1.0}]}],
           "saved": str(tmp_path / "recreated_edit.aep"), "alerts": []}
    mm = {"alerts": ["Cancelled"], "open_dialog_calls": 1}
    r = verify.check_mock(plan, {"default": rec, "media_missing": mm}, fps, 30, tmp_path, "raw.mp4", layer_checker=None)
    assert r["status"] == "pass", r["failures"]
    frames = verify.simulate_record(rec, "raw.mp4", fps, fps, 30)
    assert [verify.visible_raw_frame(frames[K]) for K in range(30)] == list(range(60, 90))


# ---------------------------------------------------------------------------------------------
# c4 speed / framing
# ---------------------------------------------------------------------------------------------

def test_speed_and_framing(phase):
    cfg = Config()
    s = seg(1, "raw", 0, 20, 100)
    fm = frame_map(list(range(100, 120)))
    box = Box(0, 0, 64, 36)
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001))
    assert r["status"] == "pass", r
    s.speed = 1.02
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg, feasible_range=lambda *a: (0.999, 1.001))
    assert r["status"] == "fail"
    s.speed, s.unsnapped = 1.0004, True
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg, feasible_range=lambda *a: (0.999, 1.001))
    assert r["status"] == "fail" and "unsnapped" in r["failures"][0]          # 1.0 was feasible
    s.speed = 1.03
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg, feasible_range=lambda *a: (1.029, 1.031))
    assert r["status"] == "pass_with_exceptions"                               # genuinely unsnappable
    s.speed, s.unsnapped = 1.0, False
    fm.s[5] = 1.02                                                             # 2 % scale error on one frame
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg, feasible_range=lambda *a: (0.999, 1.001))
    assert r["status"] == "fail" and "framing" in r["failures"][0]
    fm.s[5] = 1.0
    fm.tx[6] = 3.0                                                             # 3 px: inside ±4 px
    fm.flip[7] = True
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg, feasible_range=lambda *a: (0.999, 1.001))
    assert r["status"] == "fail" and "flip" in r["failures"][0]
    assert r["segments"][0]["max_pos_err_px"] == pytest.approx(3.0)


def test_framing_follows_animated_keys(phase):
    keys = [{"comp_frame": 0, "scale": 1.0, "rotation_deg": 0.0, "tx": 0.0, "ty": 0.0},
            {"comp_frame": 19, "scale": 1.19, "rotation_deg": 0.0, "tx": -19.0, "ty": -9.5}]
    s = seg(1, "raw", 0, 20, 100, transform_keys=keys)
    fm = frame_map(list(range(100, 120)))
    fm.s = 1.0 + 0.01 * np.arange(20)
    fm.tx = -1.0 * np.arange(20)
    fm.ty = -0.5 * np.arange(20)
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), None, (64, 36), Config(),
                                   feasible_range=lambda *a: (0.999, 1.001))
    assert r["status"] == "pass", r


# ---------------------------------------------------------------------------------------------
# c5 audio exception codes
# ---------------------------------------------------------------------------------------------

def _audio_setup():
    sr = 1000
    y = np.random.default_rng(1).standard_normal(sr * 10).astype(np.float32)
    return sr, y


def _xc(lag_s, corr):
    return lambda a, b, sr, max_lag: (lag_s, corr)


def test_audio_ok_and_codes():
    sr, y = _audio_setup()
    cfg = Config()
    segs = [seg(1, "raw", 0, 60, 100), seg(2, "not_in_raw", 60, 90), seg(3, "raw", 90, 96, 300)]
    r = verify.check_audio(segs, y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.002, 0.95))
    assert r["status"] == "pass_with_exceptions"           # not_in_raw + too_short (0.2 s) are exceptions
    codes = {row["id"]: row.get("code") for row in r["segments"]}
    assert codes == {1: None, 2: "not_in_raw", 3: "too_short"}
    only = [seg(1, "raw", 0, 60, 100)]
    assert verify.check_audio(only, y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.002, 0.95))["status"] == "pass"


def test_audio_unexplained_lag_fails_and_codes_explain():
    sr, y = _audio_setup()
    cfg = Config()
    s = seg(1, "raw", 0, 60, 100)
    assert verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.03, 0.4))["status"] == "fail"
    s.audio = {**s.audio, "exception": "music_dominated"}
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.03, 0.4))
    assert r["status"] == "pass_with_exceptions" and r["segments"][0]["code"] == "music_dominated"
    # a confident correlation at a wrong lag is a failure whatever the code
    assert verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.03, 0.95))["status"] == "fail"
    s.audio = {**s.audio, "exception": "foo"}                  # not in the closed list
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.001, 0.95))
    assert r["status"] == "fail" and "closed list" in r["failures"][0]


def test_audio_derived_codes_and_no_audio():
    sr, y = _audio_setup()
    cfg = Config()
    s = seg(1, "raw", 0, 60, 100)
    music = [{"type": "music", "comp_in": 0, "comp_out": 300}]
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, music, cfg, xcorr=_xc(0.05, 0.1))
    assert r["status"] == "pass_with_exceptions" and r["segments"][0]["code"] == "music_dominated"
    r = verify.check_audio([s], y, y, sr, F30, {"status": "audio_replaced"}, [], cfg, xcorr=_xc(0.05, 0.1))
    assert r["segments"][0]["code"] == "audio_replaced"
    s.audio = {**s.audio, "pitch_preserved": True}
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.05, 0.5))
    assert r["segments"][0]["code"] == "pitch_preserved"
    r = verify.check_audio([s], np.zeros(0), np.zeros(0), sr, F30, {"status": "no_audio"}, [], cfg, xcorr=_xc(0, 1))
    assert r["status"] == "pass_with_exceptions" and r["segments"][0]["code"] == "no_audio"
    r = verify.check_audio([s], y, np.zeros_like(y), sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0, 1))
    assert r["segments"][0]["code"] == "no_audio"             # silent RAW range


def test_audio_uses_jl_offsets():
    sr, y = _audio_setup()
    seen = {}

    def xc(a, b, sr_, max_lag):
        seen["n"] = len(a)
        return 0.0, 0.99
    s = seg(1, "raw", 30, 90, 100)
    s.audio = {**s.audio, "in_offset_frames": -6, "out_offset_frames": 3}
    verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], Config(), xcorr=xc)
    assert seen["n"] == round((90 + 3 - 30 + 6) / 30 * sr)


# ---------------------------------------------------------------------------------------------
# s9_3 visual, s9_4 cut images, s9_6 render comparison
# ---------------------------------------------------------------------------------------------

def test_visual_check_and_failure_thumbnails(tmp_path):
    raw = _textures(30, seed=3)
    truth = list(range(5, 25))
    comp = _proxy(raw[truth], "competitor")
    fm = frame_map(truth)
    rng = np.random.default_rng(0)
    rec = [(k, np.clip(raw[j].astype(np.float32) + rng.normal(0, 1.0, raw[j].shape), 0, 255).astype(np.uint8))
           for k, j in enumerate(truth)]
    r = verify.check_visual(comp, rec, fm, lambda k: None, None, Config(), tmp_path / "fail")
    assert r["status"] == "pass", r["summary"]
    assert r["distribution"]["min"] > 0.97
    rec[7] = (7, raw[29])                                   # wrong RAW frame on one comp frame
    r = verify.check_visual(comp, rec, fm, lambda k: None, None, Config(), tmp_path / "fail")
    assert r["status"] == "fail" and r["failed_frames"] == [7]
    assert (tmp_path / "fail" / "k00007.png").exists()
    r = verify.check_visual(comp, rec[:10], fm, lambda k: None, None, Config(), None)
    assert r["status"] == "fail" and any("missing" in f for f in r["failures"])


def test_proxy_roi_clips_and_scales():
    assert verify.proxy_roi(None, (100, 50), (0.5, 0.5)) == (0, 0, 100, 50)
    assert verify.proxy_roi({"x": 10.5, "y": 20, "w": 100, "h": 60, "corner_radius": 4}, (100, 50), (0.5, 0.5)) == (5, 10, 51, 30)   # covers 5.25..55.25
    assert verify.proxy_roi(Box(-10, -10, 400, 400), (100, 50), (0.5, 0.5)) == (0, 0, 100, 50)


def test_write_cut_images(tmp_path):
    rng = np.random.default_rng(2)
    imgs = {k: rng.integers(0, 255, (64, 36, 3), dtype=np.uint8) for k in range(20)}
    get = lambda ks: {k: imgs[k] for k in ks}  # noqa: E731
    r = verify.write_cut_images([5, 19], 20, get, get, tmp_path / "cuts", height=64,
                                frame_label=verify.segment_labeler([seg(1, "raw", 0, 5, 0), seg(2, "raw", 5, 20, 50)]))
    assert r["status"] == "pass"
    assert sorted(p.name for p in (tmp_path / "cuts").iterdir()) == ["cut_01.png", "cut_02.png"]
    import cv2
    im = cv2.imread(str(tmp_path / "cuts" / "cut_01.png"))
    assert im.shape[0] == 128 and im.shape[1] == 4 * 36     # 2 rows x 4 frames (k-1..k+2)


def test_compare_render_to_preview():
    raw = _textures(12, seed=5).astype(np.float32)
    prev = [(k, raw[k]) for k in range(12)]
    r = verify.compare_render_to_preview(iter(prev), prev, Config())
    assert r["status"] == "pass"
    shifted = [(k, raw[min(k + 1, 11)]) for k in range(12)]    # AE one frame late
    r = verify.compare_render_to_preview(iter(shifted), prev, Config())
    assert r["status"] == "fail"


def test_ae_render_not_available_without_aerender(tmp_path):
    r = verify.check_ae_render({"os": "Linux", "aerender": None}, None, None, 10, F30, (36, 64), tmp_path, Config())
    assert r["status"] == "not_available"


# ---------------------------------------------------------------------------------------------
# s9_7 determinism
# ---------------------------------------------------------------------------------------------

def _cutlist(speed=1.0, timings=None) -> Cutlist:
    return Cutlist(1, {"file": "media/c.mp4", "fps": "30/1", "frames": 20, "width": 64, "height": 36},
                   {"file": "media/raw.mp4", "fps": "30/1", "frames": 1000, "width": 64, "height": 36},
                   {"mode": "match"}, [seg(1, "raw", 0, 20, 100, speed=speed)],
                   provenance={"version": "x", "timings": timings or {"S2": 1.23}})


def test_compare_cutlists_ignores_timings_only():
    a, b = _cutlist(timings={"S2": 1.0}).to_dict(), _cutlist(timings={"S2": 9.0, "total": 3}).to_dict()
    assert verify.compare_cutlists(a, b)["identical"]
    c = _cutlist(speed=1.1).to_dict()
    r = verify.compare_cutlists(a, c)
    assert not r["identical"]
    assert any(d.startswith("/segments[0]/speed") for d in r["differences"])
    assert "timings" not in verify.canonical_json(a)


def test_check_determinism_uses_fresh_rerun(monkeypatch, tmp_path):
    from match_cuts import pipeline
    cfg = Config()
    cfg.out_dir = str(tmp_path)
    ctx = types.SimpleNamespace(cfg=cfg, cutlist=_cutlist())
    ctx.cutlist.save(tmp_path / "cutlist.json")
    monkeypatch.setattr(pipeline, "rerun_assembly", lambda c: _cutlist(timings={}))
    assert verify.check_determinism(ctx)["status"] == "pass"
    monkeypatch.setattr(pipeline, "rerun_assembly", lambda c: _cutlist(speed=1.1))
    r = verify.check_determinism(ctx)
    assert r["status"] == "fail" and r["differences"]


def test_segment_labeler_names_covering_segments():
    a = seg(1, "raw", 0, 16, 100, transition_out=xfade(6))
    b = seg(2, "raw", 10, 30, 400, transition_in=xfade(6))
    lab = verify.segment_labeler([b, a])
    assert (lab(9), lab(10), lab(15), lab(16), lab(40)) == ("S01", "S01+S02", "S01+S02", "S02", "")


def test_compare_with_previous_run():
    cur = _cutlist(timings={"S2": 1.0}).to_dict()
    cur["provenance"].update(input_hashes={"competitor": "a", "raw": "b"}, analysis_params_hash="p", stage_versions={"x": 1})
    cur["settings"] = {"layout_mode": "match"}
    same = json.loads(json.dumps(cur))
    same["provenance"]["timings"] = {"S2": 7.0}
    assert verify.compare_with_previous_run(same, cur) == {"compared": True, "identical": True, "differences": []}
    changed = json.loads(json.dumps(same))
    changed["segments"][0]["notes"] = "different"
    r = verify.compare_with_previous_run(changed, cur)
    assert r["compared"] and not r["identical"] and r["differences"]
    other_settings = json.loads(json.dumps(changed))
    other_settings["settings"] = {"layout_mode": "fill"}
    assert verify.compare_with_previous_run(other_settings, cur)["compared"] is False
    assert verify.compare_with_previous_run(None, cur)["compared"] is False
