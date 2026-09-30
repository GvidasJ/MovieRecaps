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
    assert "s9_8_deliverables" in verify.CHECKS


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


def test_coverage_fullscreen_period_needs_its_own_box():
    """requirements REQ-3 (DESIGN §7 D1): a full-screen period is reproduced (segments carry the whole canvas
    as their box); a RAW segment inside it rebuilt in the dominant box fails c1; split/PiP stay exceptions."""
    lb = {"box": {"x": 5, "y": 5, "w": 50, "h": 20, "corner_radius": 2},
          "periods": [{"comp_in": 0, "comp_out": 10, "mode": "boxed"}, {"comp_in": 10, "comp_out": 20, "mode": "fullscreen"},
                      {"comp_in": 20, "comp_out": 30, "mode": "boxed"}]}
    full = {"x": 0, "y": 0, "w": 64, "h": 36, "corner_radius": 0}
    segs = [seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 300), seg(3, "raw", 20, 30, 500)]
    r = verify.check_coverage(segs, 30, lb)
    assert r["status"] == "fail" and "full-screen" in r["failures"][0] and r["fullscreen_frames"] == [[10, 19]]
    segs[1].box, segs[1].region = dict(full), 1
    r = verify.check_coverage(segs, 30, lb)
    assert r["status"] == "pass", r
    lb["periods"].append({"comp_in": 25, "comp_out": 30, "mode": "split"})
    segs[2].region = 2
    r = verify.check_coverage(segs, 30, lb)
    assert r["status"] == "pass_with_exceptions" and len(r["exceptions"]) == 2


def test_frame_box_fn_follows_periods_and_segment_boxes():
    dom = {"x": 5, "y": 5, "w": 50, "h": 20, "corner_radius": 2}
    full = {"x": 0.0, "y": 0.0, "w": 64.0, "h": 36.0, "corner_radius": 0.0}
    lb = {"periods": [{"comp_in": 10, "comp_out": 20, "mode": "fullscreen"}]}
    s = seg(3, "raw", 25, 30, 10, box={"x": 1, "y": 1, "w": 10, "h": 10})
    f = verify.frame_box_fn([s], lb, dom, full, 40)
    assert f(0) == dom and f(10) == full and f(19) == full and f(20) == dom and f(26) == s.box


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


def test_cuts_spurious_cut_without_discontinuity_fails(phase):
    """verification-honesty F3: a hard cut where both models show the same RAW frame and framing on both
    sides is no discontinuity in m(k) -- a phantom cut -- and fails (it used to be an exception)."""
    a, b = seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 110)        # continuous: no visible discontinuity
    truth = {k: 100 + k for k in range(20)}
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "fail"
    assert {s["result"] for s in r["cuts"][0]["sides"]} == {"fail"}
    assert "spurious cut" in r["failures"][0]
    # a speed-only cut (cut_ambiguity) legitimately has both models agree at the boundary
    b2 = seg(2, "raw", 10, 20, 110, cut_ambiguity=[9, 11])
    r = verify.check_cuts([a, b2], F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert r["status"] == "pass_with_exceptions"
    assert {s["result"] for s in r["cuts"][0]["sides"]} == {"indistinguishable"}
    # so does a layout change (boxed -> full-screen period, DESIGN §7 D1)
    b3 = seg(2, "raw", 10, 20, 110, box={"x": 0, "y": 0, "w": 64, "h": 36, "corner_radius": 0}, region=1)
    assert verify.check_cuts([a, b3], F30, F30, (64, 36), 1000, StubScorer(truth), Config())["status"] == "pass_with_exceptions"


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


class TrueAlphaScorer(StubScorer):
    """Reports the TRUE competitor alpha_B = clip((k - O) / D) of a crossfade (O, D) (review exp_xfade)."""

    def __init__(self, O: int, D: int):
        super().__init__({})
        self.O, self.D = O, D

    def blend(self, k, a, b):
        return min(1.0, max(0.0, (k - self.O) / self.D)), 0.99

    def score(self, k, cands):
        al = min(1.0, max(0.0, (k - self.O) / self.D))
        return np.array([float("nan") if c is None else 1.0 - 0.5 * (al if c[0] < 1000 else 1 - al) for c in cands])


@pytest.mark.parametrize("truth,declared,ok", [
    ((100, 6), (100, 6), True), ((100, 15), (100, 15), True), ((100, 2), (100, 2), True),
    ((100, 6), (100, 5), False), ((100, 6), (100, 7), False), ((100, 8), (101, 8), False),
    ((100, 15), (101, 15), False), ((100, 15), (102, 15), False), ((100, 15), (100, 17), False)])
def test_cuts_crossfade_window_off_by_one_fails(phase, truth, declared, ok):
    """time-math F4: a crossfade declared one or two frames early / late / short / long passed the per-frame
    alpha tolerance (0.15); the re-fitted window (O, D) must now equal the declared one."""
    (Ot, Dt), (Od, Dd) = truth, declared
    xf = {"type": "crossfade", "duration_frames": Dd, "alpha": [i / Dd for i in range(Dd)]}
    a = seg(1, "raw", 0, Od + Dd, 300, transition_out=dict(xf))
    b = seg(2, "raw", Od, 200, 1500, transition_in=dict(xf))
    r = verify.check_cuts([a, b], F30, F30, (64, 36), 5000, TrueAlphaScorer(Ot, Dt), Config())
    assert (r["status"] == "pass") is ok, r["cuts"][0]
    if not ok:
        assert r["cuts"][0]["window_fit"] == {"O": Ot, "D": Dt}


def test_fit_crossfade_window():
    assert verify.fit_crossfade_window([(k, (k - 50) / 10) for k in range(47, 63)]) == (50, 10)
    assert verify.fit_crossfade_window([(49, 0.0), (50, 0.5), (51, 1.0)]) == (49, 2)
    assert verify.fit_crossfade_window([(10, 0.0), (11, 1.0)]) is None


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


def _with_pre_segment(fm: FrameMap) -> FrameMap:
    """Store the current columns as refine's pre-segmentation measurement (what segment.py keeps)."""
    for k in ("status", "raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi", "low_margin", "flip", "track", "tie"):
        fm.d["pre_segment_" + k] = np.asarray(fm.d[k]).copy()
    return fm


def test_ae_sim_compares_with_the_pre_segment_measurement():
    """time-math F1 / verification-honesty F1+F2 (DESIGN §7 D4): segment.py overwrites m(k) with its own
    model frame; c3 must compare the AE frame with refine's MEASUREMENT (fm.d['pre_segment_raw']). Frames
    the model re-assigned form a listed 'reassigned' class counted against frame_exact_min (they were
    counted as exact)."""
    truth = list(range(100, 300))
    fm = _with_pre_segment(frame_map(truth))
    # segmentation re-assigned k = 50 (measured 151, model 150) and absorbed an unmatched frame k = 60
    fm.d["pre_segment_raw"][50] = 151
    fm.d["pre_segment_raw_lo"][50] = fm.d["pre_segment_raw_hi"][50] = 151
    fm.d["pre_segment_status"][60] = Status.NONE
    fm.low_margin[50] = fm.low_margin[60] = True
    r = verify.check_ae_sim(_sim_frames(truth), fm, F30, F30, 200, [], Config())
    assert r["reference"].startswith("pre-segmentation")
    assert r["status"] == "pass_with_exceptions" and r["exact"] == 198
    assert [x["k"] for x in r["reassigned"]] == [50, 60] and r["reassigned"][0]["m"] == 151
    assert r["fraction_ok"] == pytest.approx(198 / 200)
    assert any("re-assigned" in e for e in r["exceptions"])
    # counted against frame_exact_min: 3 re-assigned frames of 200 -> < 99 % -> fail
    fm.d["pre_segment_raw"][70] = 171
    fm.d["pre_segment_raw_lo"][70] = fm.d["pre_segment_raw_hi"][70] = 171
    assert verify.check_ae_sim(_sim_frames(truth), fm, F30, F30, 200, [], Config())["status"] == "fail"
    # refine's own ambiguous range still exempts
    fm2 = _with_pre_segment(frame_map(truth))
    fm2.d["pre_segment_raw"][5] = 106
    fm2.d["pre_segment_raw_lo"][5], fm2.d["pre_segment_raw_hi"][5] = 105, 106
    r = verify.check_ae_sim(_sim_frames(truth), fm2, F30, F30, 200, [], Config())
    assert [x["k"] for x in r["ambiguous_identical"]] == [5] and not r["reassigned"]


def _two_seg_cutlist(n: int = 400, cut: int = 200) -> list[Segment]:
    return [seg(1, "raw", 0, cut, 1000), seg(2, "raw", cut, n, 3000)]


def test_ae_sim_plan_that_disagrees_with_the_cutlist_always_fails(phase):
    """verification-honesty F1: an AE plan whose cut is 3 frames late (0.75 % of the frames) passed as
    'pass_with_exceptions'; a plan that does not reproduce the cutlist now fails whatever the fraction."""
    segs = _two_seg_cutlist()
    truth = [verify.seg_raw_frame(segs[0] if k < 200 else segs[1], k, F30, F30, 10000) for k in range(400)]
    fm = frame_map(truth)
    ok = verify.check_ae_sim(_sim_frames(truth), fm, F30, F30, 400, [200], Config(), segments=segs, raw_fps=F30,
                             n_raw=10000)
    assert ok["status"] == "pass" and ok["n_plan_mismatches"] == 0
    late = list(truth)
    for k in (200, 201, 202):                    # A's layer runs 3 frames into B (extrapolated A frames)
        late[k] = verify.seg_raw_frame(segs[0], k, F30, F30, 10000)
    r = verify.check_ae_sim(_sim_frames(late), fm, F30, F30, 400, [200], Config(), segments=segs, raw_fps=F30,
                            n_raw=10000)
    assert r["fraction_ok"] > 0.99 and r["status"] == "fail"
    assert [x["K"] for x in r["plan_mismatches"]] == [200, 201, 202]
    # without the cutlist (old call) the same simulation only lists the frames
    r = verify.check_ae_sim(_sim_frames(late), fm, F30, F30, 400, [200], Config())
    assert r["status"] == "pass_with_exceptions" and r["n_mismatches"] == 3


def _weighted(ents: dict[int, list[tuple]]) -> dict[int, list[dict]]:
    return {K: [{"layer": f"seg{s}", "seg": s, "raw_frame": j, "opacity": w, "weight": w} for s, j, w in e]
            for K, e in ents.items()}


def test_ae_sim_checks_transitions_and_placeholders_against_the_cutlist(phase):
    """verification-honesty F4: crossfade (BLEND) frames and NOT-IN-RAW / dip frames were skipped by s9_2;
    the plan's two layers, their RAW frames and opacities, and the absence of RAW on placeholder / dip
    frames are now checked against the cutlist."""
    xf = {"type": "crossfade", "duration_frames": 6, "alpha": [i / 6 for i in range(6)]}
    a = seg(1, "raw", 0, 36, 100, transition_out=dict(xf))
    b = seg(2, "raw", 30, 60, 500, transition_in=dict(xf))
    c = seg(3, "not_in_raw", 60, 70)
    d = seg(4, "raw", 70, 90, 900)
    segs = [a, b, c, d]
    fm = frame_map([0] * 90)
    ents: dict[int, list[tuple]] = {}
    for k in range(90):
        if k < 30:
            ents[k] = [(1, verify.seg_raw_frame(a, k, F30, F30, 5000), 1.0)]
        elif k < 36:
            al = (k - 30) / 6
            ents[k] = [(1, verify.seg_raw_frame(a, k, F30, F30, 5000), 1 - al),
                       (2, verify.seg_raw_frame(b, k, F30, F30, 5000), al)]
        elif k < 60:
            ents[k] = [(2, verify.seg_raw_frame(b, k, F30, F30, 5000), 1.0)]
        elif k < 70:
            ents[k] = []
        else:
            ents[k] = [(4, verify.seg_raw_frame(d, k, F30, F30, 5000), 1.0)]
        fm.raw[k] = ents[k][0][1] if len(ents[k]) == 1 else -1
    fm.status[30:36] = Status.BLEND
    fm.status[60:70] = Status.NONE
    fm.raw_lo, fm.raw_hi = fm.raw, fm.raw
    kw = dict(segments=segs, raw_fps=F30, n_raw=5000)
    r = verify.check_ae_sim(_weighted(ents), fm, F30, F30, 90, [30, 60, 70], Config(), **kw)
    assert r["status"] == "pass", r["failures"]
    assert r["transition_frames_checked"] == 6 and r["solid_frames_checked"] == 10
    hard = dict(ents)                              # crossfade dropped: hard cut at 30
    for k in range(30, 36):
        hard[k] = [(2, verify.seg_raw_frame(b, k, F30, F30, 5000), 1.0)]
    r = verify.check_ae_sim(_weighted(hard), fm, F30, F30, 90, [30, 60, 70], Config(), **kw)
    assert r["status"] == "fail" and {x["K"] for x in r["plan_mismatches"]} == {30, 31, 32, 33, 34, 35}
    rev = dict(ents)                               # ramp reversed
    for k in range(30, 36):
        al = 1 - (k - 30) / 6
        rev[k] = [(1, verify.seg_raw_frame(a, k, F30, F30, 5000), 1 - al), (2, verify.seg_raw_frame(b, k, F30, F30, 5000), al)]
    assert verify.check_ae_sim(_weighted(rev), fm, F30, F30, 90, [30, 60, 70], Config(), **kw)["status"] == "fail"
    leak = dict(ents)                              # RAW shows through the NOT-IN-RAW placeholder
    leak[65] = [(2, 530, 1.0)]
    r = verify.check_ae_sim(_weighted(leak), fm, F30, F30, 90, [30, 60, 70], Config(), **kw)
    assert r["status"] == "fail" and r["plan_mismatches"][0]["what"] == "not_in_raw"


def test_ae_sim_skips_blend_and_placeholder_frames():
    truth = list(range(50))
    fm = frame_map(truth)
    fm.status[10:16] = Status.BLEND
    fm.status[30:35] = Status.NONE
    sim = [None if 10 <= K < 16 or 30 <= K < 35 else j for K, j in enumerate(truth)]
    r = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 50, [], Config())
    assert r["status"] == "pass" and r["matched"] == 39


def _grid_cutlist(raw_fps: Fraction, phase_frac: float, n: int = 90, speed: float = 1.0) -> Cutlist:
    """One RAW segment [0, n) in a 30 fps competitor, RAW at raw_fps; raw_in puts frame 240 at phase_frac."""
    s = Segment(id=1, type="raw", comp_in=0, comp_out=n, speed=speed, transform=dict(IDENT),
                raw_in_seconds=float((240 + Fraction(phase_frac)) / raw_fps), raw_in_frame=240)
    comp = {"file": "media/c.mp4", "file_rel": "media/c.mp4", "width": 64, "height": 36, "fps": "30/1",
            "frames": n, "has_audio": False}
    raw = {"file": "media/raw.mp4", "file_rel": "media/raw.mp4", "width": 64, "height": 36,
           "fps": f"{raw_fps.numerator}/{raw_fps.denominator}", "frames": 100000, "has_audio": False}
    return Cutlist(1, comp, raw, {"mode": "match", "box": None}, [s])


def _exact_frame_map(cl: Cutlist) -> FrameMap:
    """m(k) = the AE floor rule on the COMPETITOR grid (what the competitor showed), exact rationals."""
    s = cl.segments[0]
    rf, n = cl.raw_fps, int(cl.competitor["frames"])
    raw_in = Fraction(s.raw_in_seconds)
    return frame_map([math.floor(rf * (raw_in + Fraction(s.speed) * Fraction(k, 30))) for k in range(n)])


@pytest.mark.parametrize("raw_fps", [Fraction(24000, 1001), Fraction(25), Fraction(30000, 1001), Fraction(60)])
@pytest.mark.parametrize("phase_frac", [0.05, 0.3, 0.55, 0.8])
@pytest.mark.parametrize("mode", [{"fps_mode": "source"}, {"layout_mode": "source"}])
def test_ae_sim_on_a_different_main_grid(raw_fps, phase_frac, mode):
    """requirements REQ-2 / time-math F3: with --fps source or --layout source (MAIN at the RAW rate) a
    CORRECT export -- the real ae_plan + simulate_ae -- must not fail criterion 3: at MAIN frame K AE shows
    the RAW frame of time K/main_fps, which lies between m(k) and m(k+1). It is listed ('grid'), not a
    mismatch. (Comparing with m(floor(K comp_fps / main_fps)) failed 25-66 % of the frames.)"""
    from match_cuts import export_ae
    cl = _grid_cutlist(raw_fps, phase_frac)
    fm = _exact_frame_map(cl)
    cfg = Config(**mode)
    plan = export_ae.ae_plan(cl, cfg, export_ae.footage_meta_from_cutlist(cl))
    mf = Fraction(plan["main"]["fps"]["num"], plan["main"]["fps"]["den"])
    assert mf == raw_fps
    sim = export_ae.simulate_ae(plan)
    r = verify.check_ae_sim(sim, fm, F30, mf, plan["main"]["frames"], [], cfg, segments=cl.segments,
                            raw_fps=cl.raw_fps, n_raw=100000)
    assert r["status"] in ("pass", "pass_with_exceptions"), r["summary"]
    assert r["n_mismatches"] == 0 and r["n_plan_mismatches"] == 0 and r["fraction_ok"] == 1.0
    # the same plan one RAW frame late still fails (the plan no longer reproduces the cutlist)
    for L in plan["layers"]:
        if L["kind"] == "raw":
            L["expect"] = [e + 1 for e in L["expect"]]
            L["timeMode"] = "frames"
    r = verify.check_ae_sim(export_ae.simulate_ae(plan), fm, F30, mf, plan["main"]["frames"], [], cfg,
                            segments=cl.segments, raw_fps=cl.raw_fps, n_raw=100000)
    assert r["status"] == "fail" and r["n_plan_mismatches"] > 0


def test_ae_sim_grid_bracket_without_segments_and_near_cuts():
    comp_fps, main_fps = Fraction(30), Fraction(24000, 1001)
    truth = list(range(1000, 1060))
    fm = frame_map(truth)
    n_main = math.floor(60 * main_fps / comp_fps + Fraction(1, 2))
    sim = [truth[verify.main_to_comp(K, comp_fps, main_fps)] + (K % 2) for K in range(n_main)]   # m(k) or m(k)+1
    r = verify.check_ae_sim(_sim_frames(sim), fm, comp_fps, main_fps, n_main, [24], Config())
    assert r["status"] == "pass_with_exceptions" and r["excluded_near_cuts"] == 2 and r["n_mismatches"] == 0
    assert r["n_grid"] > 0
    sim[40] += 3                                                   # outside [m(k), m(k+1)]: a real mismatch
    r = verify.check_ae_sim(_sim_frames(sim), fm, comp_fps, main_fps, n_main, [24], Config())
    assert r["n_mismatches"] == 1
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


def test_framing_is_measured_independently(phase):
    """verification-honesty F5: the FrameMap Sims are refine's track model and the segment transform is their
    median, so a wrong track passed c4 with 0.0 errors. With a measure() (ECC from a perturbed start) a
    framing that the pixels contradict fails, and so does a flip the mirrored hypothesis beats."""
    cfg = Config()
    s = seg(1, "raw", 0, 40, 100)
    fm = frame_map(list(range(100, 140)))                         # FrameMap Sims == the segment model (self-consistent)
    box = Box(0, 0, 64, 36)
    wrong = Sim(1.025, 0.0, 6.0, 0.0)                              # what the pixels say

    def measure(sim_true, own=0.95, other=0.3):
        calls = []

        def f(sg, k, model):
            calls.append(k)
            return {"sim": sim_true, "z": 0.99, "z_model": 0.80 if sim_true is not model else 0.99,
                    "flip_own": own, "flip_other": other}
        f.calls = calls
        return f
    m = measure(wrong)
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001), measure=m)
    assert r["status"] == "fail" and "independently measured framing" in r["failures"][0]
    assert r["segments"][0]["max_scale_err"] == 0.0                # the self-referential comparison saw nothing
    assert 0 in m.calls and 39 in m.calls and len(m.calls) >= 8
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001), measure=measure(Sim(1.0005, 0.0, 0.3, 0.0)))
    assert r["status"] == "pass", r
    # ECC that did not reach the model's score is not evidence against the model
    def unconverged(sg, k, model):
        return {"sim": wrong, "z": 0.7, "z_model": 0.99, "flip_own": 0.95, "flip_other": 0.2}
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001), measure=unconverged)
    assert r["status"] == "pass_with_exceptions" and "could not be measured" in r["exceptions"][0]
    # flip: the mirrored hypothesis wins -> fail; a tie (symmetric content) -> listed
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001), measure=measure(None, 0.3, 0.9))
    assert r["status"] == "fail" and "mirrored hypothesis" in r["failures"][0]
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), box, (64, 36), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001), measure=measure(None, 0.9, 0.895))
    assert r["status"] == "pass_with_exceptions" and any("flip not decidable" in e for e in r["exceptions"])


def _warp_frames(raw: np.ndarray, sim: Sim, flip: bool, out_wh: tuple[int, int]) -> np.ndarray:
    from match_cuts import scoring
    out = []
    for img in raw:
        w, _v = scoring.warp_to_roi(img, sim, flip, raw.shape[2], (1.0, 1.0), (1.0, 1.0), (0, 0, out_wh[0], out_wh[1]))
        out.append(np.clip(np.rint(w), 0, 255).astype(np.uint8))
    return np.stack(out)


def test_framing_measure_with_real_ecc(phase):
    """framing_measure (refine.refine_transform from a perturbed start) on real pixels: the true framing
    passes, a 2.5 % / 6 px wrong segment transform fails, a wrong flip fails."""
    import cv2
    rng = np.random.default_rng(11)
    raw = np.empty((30, 72, 96), np.uint8)
    for i in range(30):
        img = cv2.GaussianBlur(rng.uniform(0, 255, (72, 96)).astype(np.float32), (0, 0), 2.0) * 3.0 - 250
        raw[i] = np.clip(img, 0, 255).astype(np.uint8)
    true = Sim(0.9, 0.0, 6.0, 4.0)
    comp_frames = _warp_frames(raw[5:25], true, False, (96, 72))
    comp, rawp = _proxy(comp_frames, "competitor"), _proxy(raw, "raw")
    cfg = Config()
    box = {"x": 8, "y": 8, "w": 80, "h": 56, "corner_radius": 0}
    fm = frame_map(list(range(5, 25)))
    scorer = verify.ProxyScorer(comp, rawp, box, lambda k: None, 96, cfg)
    meas = verify.framing_measure(comp, rawp, scorer, lambda k: None, lambda k: box, (96, 72), F30, F30, 30, cfg)

    def run(sim, flip=False):
        s = seg(1, "raw", 0, 20, 5, transform=sim.to_dict(), flip_h=flip)
        fm.flip[:] = flip                      # the FrameMap agrees with the model (refine's track model)
        fm.s[:], fm.theta[:], fm.tx[:], fm.ty[:] = sim.s, sim.theta_deg, sim.tx, sim.ty
        return verify.check_speed_framing([s], fm, F30, F30, (96, 72), box, (96, 72), cfg,
                                          feasible_range=lambda *a: (0.999, 1.001), measure=meas, sample_step=5)
    r = run(true)
    assert r["status"] == "pass", r
    ind = r["segments"][0]["independent"]
    assert ind["n_measured"] >= 4 and ind["max_scale_err"] < 0.01 and ind["max_pos_err_px"] < 2.0 and ind["flip"] == "ok"
    r = run(verify.perturb_sim(true, (48, 36), 0.025, 6.0, 0.0))
    assert r["status"] == "fail" and "independently measured framing" in r["failures"][0]
    r = run(true, flip=True)
    assert r["status"] == "fail" and any("mirrored hypothesis" in f for f in r["failures"])


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
    # verification-honesty F6: music_dominated is never invented by the check from added-audio overlap
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, music, cfg, xcorr=_xc(0.05, 0.1))
    assert r["status"] == "fail" and "did not report music dominance" in r["failures"][0]
    s.audio = {**s.audio, "exception": "music_dominated"}         # ... only accepted from audio_align
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, music, cfg, xcorr=_xc(0.05, 0.1))
    assert r["status"] == "pass_with_exceptions" and r["segments"][0]["code"] == "music_dominated"
    s.audio = {**s.audio, "exception": None}
    r = verify.check_audio([s], y, y, sr, F30, {"status": "audio_replaced"}, [], cfg, xcorr=_xc(0.05, 0.1))
    assert r["segments"][0]["code"] == "audio_replaced"
    s.audio = {**s.audio, "pitch_preserved": True}
    r = verify.check_audio([s], y, y, sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0.05, 0.5))
    assert r["segments"][0]["code"] == "pitch_preserved"
    r = verify.check_audio([s], np.zeros(0), np.zeros(0), sr, F30, {"status": "no_audio"}, [], cfg, xcorr=_xc(0, 1))
    assert r["status"] == "pass_with_exceptions" and r["segments"][0]["code"] == "no_audio"
    r = verify.check_audio([s], y, np.zeros_like(y), sr, F30, {"status": "ok"}, [], cfg, xcorr=_xc(0, 1))
    assert r["segments"][0]["code"] == "no_audio"             # silent RAW range


def test_audio_gross_misalignment_and_contradicted_analysis_fail():
    """verification-honesty F6: recreated audio 500 ms off (or from another moment) gave a weak +-100 ms peak
    and verify labelled it 'music_dominated' because a music bed overlapped. Now a wide search finds the
    real lag (fail), and a weak peak on a segment the analysis found aligned fails."""
    from match_cuts import audio_align
    sr = 8000
    rng = np.random.default_rng(3)
    src_y = rng.standard_normal(sr * 12).astype(np.float32)
    music = [{"type": "music", "comp_in": 0, "comp_out": 360}]
    comp_y = src_y.copy()
    s = seg(1, "raw", 30, 150, 100)
    s.audio = {**s.audio, "lag_ms": 0.4, "corr": 0.93, "exception": None}      # audio_align found it aligned
    late = np.concatenate([np.zeros(sr // 2, np.float32), src_y[:-sr // 2]])  # rebuilt 500 ms late
    r = verify.check_audio([s], comp_y, late, sr, F30, {"status": "ok"}, music, Config(), xcorr=audio_align.xcorr_lag)
    assert r["status"] == "fail" and "misaligned by +500" in r["failures"][0], r["failures"]
    other = rng.standard_normal(sr * 12).astype(np.float32)                   # a different RAW moment entirely
    r = verify.check_audio([s], comp_y, other, sr, F30, {"status": "ok"}, music, Config(), xcorr=audio_align.xcorr_lag)
    assert r["status"] == "fail" and "no longer correlates" in r["failures"][0], r["failures"]
    r = verify.check_audio([s], comp_y, comp_y, sr, F30, {"status": "ok"}, music, Config(), xcorr=audio_align.xcorr_lag)
    assert r["status"] == "pass" and r["segments"][0]["result"] == "ok"


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


def test_visual_checks_blend_uniform_and_placeholder_frames(tmp_path):
    """verification-honesty F4: crossfade frames of the recreation were never gated (a frozen crossfade
    passed), dip frames and NOT-IN-RAW placeholder frames were not looked at (a white placeholder passed)."""
    raw = _textures(40, seed=4)
    n = 30
    comp_frames = np.zeros((n, 32, 48), np.uint8)
    for k in range(10):
        comp_frames[k] = raw[k]
    for k in range(10, 16):                               # crossfade raw[k] -> raw[20 + k]
        al = (k - 10) / 6
        comp_frames[k] = np.clip((1 - al) * raw[k].astype(np.float32) + al * raw[20 + k], 0, 255).astype(np.uint8)
    comp_frames[16:20] = 0                                # dip to black
    ph = verify.placeholder_gray()
    comp_frames[20:25] = 200                              # competitor shows stock footage (NOT IN RAW)
    for k in range(25, 30):
        comp_frames[k] = raw[k]
    comp = _proxy(comp_frames, "competitor")
    fm = frame_map(list(range(n)))
    fm.status[10:16] = Status.BLEND
    fm.status[16:20] = Status.UNIFORM
    fm.status[20:25] = Status.NONE
    segs = [seg(1, "raw", 0, 16, 0), seg(2, "dip", 16, 20), seg(3, "not_in_raw", 20, 25), seg(4, "raw", 25, 30, 25)]

    def rec_frames(freeze_blend=False, white_placeholder=False, grey_dip=False):
        out = []
        for k in range(n):
            img = comp_frames[k].copy()
            if 10 <= k < 16 and freeze_blend:
                img = comp_frames[9].copy()
            if 16 <= k < 20 and grey_dip:
                img[:] = 128
            if 20 <= k < 25:
                img[:] = 255 if white_placeholder else int(round(ph))
                img[14:18, 10:38] = 255                    # the drawn label
            out.append((k, img))
        return out
    r = verify.check_visual(comp, rec_frames(), fm, lambda k: None, None, Config(), tmp_path, segments=segs)
    assert r["status"] == "pass", r["failures"]
    assert r["uniform_frames_checked"] == 4 and r["placeholder_frames_checked"] == 5
    r = verify.check_visual(comp, rec_frames(freeze_blend=True), fm, lambda k: None, None, Config(), tmp_path, segments=segs)
    assert r["status"] == "fail" and r["blend_failed_frames"]
    r = verify.check_visual(comp, rec_frames(white_placeholder=True), fm, lambda k: None, None, Config(), tmp_path,
                            segments=segs)
    assert r["status"] == "fail" and len(r["placeholder_failed"]) == 5
    r = verify.check_visual(comp, rec_frames(grey_dip=True), fm, lambda k: None, None, Config(), tmp_path, segments=segs)
    assert r["status"] == "fail" and len(r["uniform_failed"]) == 4


def test_visual_scores_fullscreen_frames_on_the_whole_canvas():
    """requirements REQ-3: s9_3 scored only the dominant box ROI, so a full-screen shot rebuilt inside the
    box passed. With box_fn the frame's own box (the whole canvas) is scored."""
    raw = _textures(12, seed=6)
    comp = _proxy(raw[:10], "competitor")
    fm = frame_map(list(range(10)))
    dom = {"x": 12, "y": 8, "w": 24, "h": 16, "corner_radius": 0}
    boxed = []
    for k in range(10):
        img = np.zeros_like(raw[k])
        img[8:24, 12:36] = raw[k][8:24, 12:36]          # the recreation keeps the box, black around it
        boxed.append((k, img))
    full = {"x": 0, "y": 0, "w": 48, "h": 32, "corner_radius": 0}
    assert verify.check_visual(comp, boxed, fm, lambda k: None, dom, Config())["status"] == "pass"
    r = verify.check_visual(comp, boxed, fm, lambda k: None, dom, Config(), box_fn=lambda k: full if k >= 5 else dom)
    assert r["status"] == "fail" and r["failed_frames"] == [5, 6, 7, 8, 9]


def test_proxy_scorer_uses_the_frame_box():
    raw = _textures(6, seed=7)
    full = {"x": 0, "y": 0, "w": 48, "h": 32, "corner_radius": 0}
    dom = {"x": 12, "y": 8, "w": 24, "h": 16}
    sc = verify.ProxyScorer(_proxy(raw, "competitor"), _proxy(raw, "raw"), dom, lambda k: None, 48, Config(),
                            box_fn=lambda k: full if k == 3 else dom)
    assert sc.roi_at(2) == (12, 8, 24, 16) and sc.roi_at(3) == (0, 0, 48, 32)
    assert sc.score(3, [(3, Sim(1.0, 0.0, 0.0, 0.0), False)])[0] > 0.999


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


def _write_video(path: Path, frames: np.ndarray, fps=F30) -> None:
    from match_cuts.media import FFmpegWriter
    h, w = frames.shape[1:3]
    with FFmpegWriter(path, w, h, fps, codec_args=["-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p"]) as wr:
        for f in frames:
            wr.write(np.repeat(f[:, :, None], 3, axis=2) if f.ndim == 2 else f)


def _fake_aerender(path: Path, frames_dir: Path, n: int, rc: int = 0) -> Path:
    """A fake aerender: copies the first ``n`` PNGs of frames_dir to the -output pattern, exits with ``rc``."""
    path.write_text("#!" + sys.executable + "\n"
                    "import sys, shutil, pathlib\n"
                    "out = pathlib.Path(sys.argv[sys.argv.index('-output') + 1])\n"
                    f"src = sorted(pathlib.Path({str(frames_dir)!r}).glob('*.png'))[:{n}]\n"
                    "if 'PNG' in sys.argv[sys.argv.index('-OMtemplate') + 1]:\n"
                    "    for i, p in enumerate(src):\n"
                    "        shutil.copy(p, out.parent / ('ae_%05d.png' % i))\n"
                    f"sys.exit({rc})\n")
    path.chmod(0o755)
    return path


def test_ae_render_rejects_failed_stale_and_truncated_renders(tmp_path):
    """verification-honesty F9: s9_6 compared stale frames of an earlier run when aerender failed, ignored
    the return code and passed a render that stopped after 10 frames."""
    import cv2
    frames = _textures(20, 64, 48, seed=9)
    preview = tmp_path / "preview.mp4"
    _write_video(preview, frames)
    pngs = tmp_path / "pngs"
    pngs.mkdir()
    for i, f in enumerate(frames):
        cv2.imwrite(str(pngs / f"f{i:03d}.png"), f)
    aep = tmp_path / "recreated_edit.aep"
    aep.write_bytes(b"aep")
    out = tmp_path / "aerender"
    good = _fake_aerender(tmp_path / "aerender_ok", pngs, 20)
    r = verify.check_ae_render({"aerender": str(good)}, str(aep), str(preview), 20, F30, (64, 48), out, Config())
    assert r["status"] == "pass", r
    # a later run whose aerender fails: the 20 PNGs of the previous run must not be compared
    bad = _fake_aerender(tmp_path / "aerender_fail", pngs, 0, rc=1)
    r = verify.check_ae_render({"aerender": str(bad)}, str(aep), str(preview), 20, F30, (64, 48), out, Config())
    assert r["status"] == "fail" and r["summary"] == "aerender produced no frames"
    assert all(t.get("returncode") == 1 for t in r["tried"])
    # a render that stops after 10 frames (exit code 0)
    short = _fake_aerender(tmp_path / "aerender_short", pngs, 10)
    r = verify.check_ae_render({"aerender": str(short)}, str(aep), str(preview), 20, F30, (64, 48), out, Config())
    assert r["status"] == "fail" and any("10 frames, MAIN has 20" in f for f in r["failures"])
    # a non-zero exit with frames left behind is a failed attempt too
    crash = _fake_aerender(tmp_path / "aerender_crash", pngs, 20, rc=3)
    r = verify.check_ae_render({"aerender": str(crash)}, str(aep), str(preview), 20, F30, (64, 48), out, Config())
    assert r["status"] == "fail"


def test_compare_render_to_preview_counts_frames():
    raw = _textures(12, seed=5).astype(np.float32)
    prev = [(k, raw[k]) for k in range(12)]
    assert verify.compare_render_to_preview(iter(prev[:10]), prev, Config())["status"] == "pass"
    r = verify.compare_render_to_preview(iter(prev[:10]), prev, Config(), n_expected=12)
    assert r["status"] == "fail" and "10 frames, MAIN has 12" in r["failures"][0]


def test_preview_file_probe_and_sample_comparison(tmp_path):
    """verification-honesty F12: the delivered preview_recreation.mp4 was never decoded in fill / source /
    --fps source / other --comp-size runs. It is now probed (frame count, fps grid, size) and, outside the
    match mode, sampled frames are compared with render_frame."""
    frames = _textures(30, 64, 48, seed=12)
    p = tmp_path / "preview_recreation.mp4"
    _write_video(p, frames)
    render = lambda ks: {k: np.repeat(frames[k][:, :, None], 3, axis=2) for k in ks}   # noqa: E731
    r = verify.check_preview_file(p, 30, F30, (64, 48), render, [0, 7, 15, 29])
    assert r["status"] == "pass" and r["compared"] == 4, r
    r = verify.check_preview_file(p, 32, F30, (64, 48))
    assert r["status"] == "fail" and "30 frames, expected 32" in r["failures"][0]
    r = verify.check_preview_file(p, 30, Fraction(30000, 1001), (64, 48))
    assert r["status"] == "fail"
    r = verify.check_preview_file(p, 30, F30, (128, 96))
    assert r["status"] == "fail" and "MAIN is 128x96" in r["failures"][0]
    wrong = lambda ks: {k: np.repeat(frames[(k + 3) % 30][:, :, None], 3, axis=2) for k in ks}   # noqa: E731
    r = verify.check_preview_file(p, 30, F30, (64, 48), wrong, [0, 7, 15, 29])
    assert r["status"] == "fail" and "differs from the renderer" in r["failures"][0]
    assert verify.check_preview_file(None, 30, F30, None, skipped="--skip-preview")["status"] == "not_available"
    assert verify.check_preview_file(tmp_path / "nope.mp4", 30, F30, None)["status"] == "fail"


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


def test_check_determinism_fails_when_the_previous_identical_run_differs(monkeypatch, tmp_path):
    """verification-honesty F11: a previous run with identical inputs / parameters / versions that produced a
    different cutlist.json only raised a warning; it is Stage 9.7's failure."""
    from match_cuts import pipeline
    cfg = Config()
    cfg.out_dir = str(tmp_path)
    cur = _cutlist()
    cur.provenance.update(input_hashes={"competitor": "a", "raw": "b"}, analysis_params_hash="p", stage_versions={"x": 1})
    cur.settings = {"layout_mode": "match"}
    ctx = types.SimpleNamespace(cfg=cfg, cutlist=cur)
    monkeypatch.setattr(pipeline, "rerun_assembly", lambda c: cur)
    prev = cur.to_dict()
    ctx.previous_cutlist = json.loads(json.dumps(prev))
    r = verify.check_determinism(ctx)
    assert r["status"] == "pass" and "identical to the previous run" in r["summary"]
    ctx.previous_cutlist["segments"][0]["notes"] = "different"
    r = verify.check_determinism(ctx)
    assert r["status"] == "fail" and "previous run" in r["failures"][0]
    ctx.previous_cutlist["provenance"]["stage_versions"] = {"x": 2}          # the tool changed: not compared
    assert verify.check_determinism(ctx)["status"] == "pass"


def _deliverables_ctx(tmp_path: Path, **over):
    cfg = Config()
    cfg.out_dir, cfg.work_dir = str(tmp_path / "out"), str(tmp_path / "work")
    out = Path(cfg.out_dir)
    files = ["build_ae_project.jsx", "cutlist.json", "cutlist.csv", "recreated_edit.xml", "recreated_edit.edl",
             "preview_recreation.mp4", "compare.mp4", "media/raw.mp4", "media/competitor_ref.mp4",
             "debug/mapping.png", "debug/scores.png", "debug/layout.png", "debug/cuts/cut_01.png"]
    for f in files:
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        (out / f).write_bytes(b"x")
    cl = _cutlist()
    cl.raw.update(file="media/raw.mp4", file_rel="media/raw.mp4")
    cl.competitor.update(file="media/competitor_ref.mp4", file_rel="media/competitor_ref.mp4")
    ctx = types.SimpleNamespace(cfg=cfg, cutlist=cl, env={"ae_app": None}, ae_run={"status": "not_available"},
                                exports={"ok": True}, errors=[], paths={"jsx": str(out / "build_ae_project.jsx")})
    for k, v in over.items():
        setattr(ctx, k, v)
    return ctx, out


def test_deliverables_check(tmp_path):
    """requirements REQ-6 (DESIGN §7 D5): a missing / unvalidated deliverable or a recorded export error used
    to leave 'Overall: PASS' and exit 0; s9_8_deliverables fails on each of them."""
    ctx, out = _deliverables_ctx(tmp_path)
    r = verify.check_deliverables(ctx, n_cuts=1)
    assert r["status"] == "pass", r["failures"]
    assert r["skipped"] == ["recreated_edit.aep (After Effects not installed)"]
    (out / "recreated_edit.xml").unlink()
    r = verify.check_deliverables(ctx, n_cuts=1)
    assert r["status"] == "fail" and any("recreated_edit.xml missing" in f for f in r["failures"])
    ctx, out = _deliverables_ctx(tmp_path / "b", exports={"ok": False, "errors": ["duration 299 != 300"]})
    assert "validation did not pass" in verify.check_deliverables(ctx)["failures"][0]
    ctx, out = _deliverables_ctx(tmp_path / "c", errors=[{"stage": "S8 compare.mp4", "error": "OSError: disk full"}])
    assert "S8 compare.mp4" in verify.check_deliverables(ctx)["failures"][0]
    ctx, out = _deliverables_ctx(tmp_path / "d", env={"ae_app": "/Applications/Adobe After Effects 2025"})
    assert "recreated_edit.aep" in verify.check_deliverables(ctx)["failures"][0]
    ctx, out = _deliverables_ctx(tmp_path / "e")
    (out / "preview_recreation.mp4").unlink()
    (out / "compare.mp4").unlink()
    (out / "media" / "raw.mp4").unlink()
    r = verify.check_deliverables(ctx)
    assert r["status"] == "fail" and len(r["failures"]) == 3
    ctx.cfg.skip_preview = ctx.cfg.skip_compare = True
    (out / "media" / "raw.mp4").write_bytes(b"x")
    r = verify.check_deliverables(ctx, n_cuts=2)
    assert r["status"] == "fail" and r["failures"] == ["debug/cuts has 1 cut images, the edit has 2 cuts"]
    assert len(r["skipped"]) == 3
    (out / "debug" / "scores.png").unlink()
    r = verify.check_deliverables(ctx, n_cuts=1)
    assert r["status"] == "pass" and r["warnings"] == ["debug/scores.png missing"]
    # the pipeline says this run did not produce the EDL: the stale file from an earlier run does not count
    ctx.exports = {"ok": True, "validation_ok": True, "missing": ["edl"], "skipped": {}}
    r = verify.check_deliverables(ctx, n_cuts=1)
    assert r["status"] == "fail" and "'edl' was not produced by this run" in r["failures"][0]


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


def test_unsnapped_speed_judged_on_measured_frames_not_soft_ranges():
    """Integration fix (segment agent note / time-math F2): a genuine 1.03x segment on slow footage has soft
    ranges of ±1 frame that also admit 1.05; segment.py rightly leaves it unsnapped because 1.05 does not
    reproduce refine's MEASURED frames. c4 must judge 'a snap value was feasible' on the measured frames."""
    import math
    n = 60
    truth = [100 + math.floor(1.03 * k + 0.25) for k in range(n)]
    fm = frame_map(truth)
    fm.soft_lo = np.asarray(truth, np.int32) - 1
    fm.soft_hi = np.asarray(truth, np.int32) + 1
    fm.d["pre_segment_raw"] = np.asarray(truth, np.int32)
    fm.d["pre_segment_raw_lo"] = np.asarray(truth, np.int32)
    fm.d["pre_segment_raw_hi"] = np.asarray(truth, np.int32)
    fm.d["pre_segment_status"] = np.full(n, Status.MATCH, np.int8)
    fm.d["pre_segment_flip"] = np.zeros(n, bool)
    s = seg(1, "raw", 0, n, 100, speed=1.03, unsnapped=True)
    cfg = Config()
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), Box(0, 0, 64, 36), (64, 36), cfg)
    assert not any("unsnapped although" in f for f in r["failures"]), r["failures"]
    assert r["status"] in ("pass", "pass_with_exceptions"), r
    # ...while a segment left unsnapped although 1.0 reproduces every measured frame still fails
    truth1 = list(range(100, 100 + n))
    fm1 = frame_map(truth1)
    s1 = seg(1, "raw", 0, n, 100, speed=1.0004, unsnapped=True)
    r1 = verify.check_speed_framing([s1], fm1, F30, F30, (64, 36), Box(0, 0, 64, 36), (64, 36), cfg)
    assert r1["status"] == "fail" and any("unsnapped although" in f for f in r1["failures"]), r1
