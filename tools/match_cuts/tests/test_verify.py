"""Unit tests for verify.py (Stage 9): coverage tiling, criterion-2 cut logic (stub and real scoring),
AE-simulation comparison, mock-record evaluation, speed/framing, audio exception codes, criteria
aggregation and the determinism comparison. The analysis modules are replaced by small stubs."""
from __future__ import annotations

import copy
import json
import math
import sys
import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import match_cuts
from match_cuts import pipeline  # noqa: F401 - imported with the REAL phase_solve, before any test stubs it
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


def test_coverage_fullscreen_period_boundary_inside_a_dissolve_or_off_by_a_sliver():
    """review R2-5 / real-world D1-c1-transition: a dissolve between a boxed shot and a full-screen shot makes the
    detected full-screen period start (or end) inside the dissolve; segment.py keeps the boxed segment boxless
    (majority rule) and AE renders the dissolve correctly (the full-screen neighbour in MAIN over the Video Box).
    Those transition frames -- and a merged 1-2 frame sliver at the period boundary -- are listed exceptions,
    not c1 failures; a boxless segment's own non-transition frames in a full-screen period still fail."""
    full = {"x": 0.0, "y": 0.0, "w": 1080.0, "h": 1920.0, "corner_radius": 0.0}
    O, D, n = 34, 6, 80

    def pair(period_start: int, a_out_trans: bool = True, b_box: bool = True):
        A = seg(1, "raw", 0, O + D, 100, transition_out=xfade(D) if a_out_trans else None)
        B = seg(2, "raw", O, n, 900, transition_in=xfade(D))
        if b_box:
            B.box, B.region = dict(full), 1
        lb = {"periods": [{"comp_in": 0, "comp_out": period_start, "mode": "boxed"},
                          {"comp_in": period_start, "comp_out": n, "mode": "fullscreen"}]}
        return verify.check_coverage([A, B], n, lb)

    for start in (O + 1, O + 2, O + 4):                   # the period starts inside the dissolve
        r = pair(start)
        assert r["status"] == "pass_with_exceptions" and not r["failures"], r
        assert any("transition overlap with S02" in e for e in r["exceptions"]), r["exceptions"]
        assert r["fullscreen_explained"][0]["why"] == "transition"
    r = pair(O + 2, a_out_trans=False)                    # the transition declared on B only is enough
    assert r["status"] == "pass_with_exceptions", r
    r = pair(O - 1)                                       # detected one frame before the dissolve: a sliver
    assert r["status"] == "pass_with_exceptions" and {e["why"] for e in r["fullscreen_explained"]} == \
        {"transition", "sliver"}, r
    r = pair(O - 4)                                       # 4 non-transition frames full-screen: A needed the box
    assert r["status"] == "fail" and "frames 30-33 show the video full-screen" in r["failures"][0], r
    r = pair(O + 2, b_box=False)                          # the neighbour does not carry the box: not explained
    assert r["status"] == "fail" and any("S01: frames 36-39" in f for f in r["failures"]), r
    # a dip: boxed A fades to black (the whole canvas: the full-screen period starts inside the fade-out), the
    # dip segment carries the canvas box, full-screen B fades in from black
    dip = {"type": "dip_black", "duration_frames": D, "alpha": [i / D for i in range(D)], "color": "#000000"}
    A = seg(1, "raw", 0, O + D, 100, transition_out=dict(dip))
    U = seg(2, "dip", O, 50, transition_in=dict(dip), box=dict(full), region=1)
    B = seg(3, "raw", 50, n, 900, box=dict(full), region=1)
    lb = {"periods": [{"comp_in": 0, "comp_out": O + 2, "mode": "boxed"},
                      {"comp_in": O + 2, "comp_out": n, "mode": "fullscreen"}]}
    r = verify.check_coverage([A, U, B], n, lb)
    assert r["status"] == "pass_with_exceptions" and not r["failures"], r
    assert r["fullscreen_explained"] == [{"segment": 1, "frames": [[36, 39]], "why": "transition", "period": [36, 79]}]
    U.box = None                                          # the dip does not carry the full-screen box
    assert verify.check_coverage([A, U, B], n, lb)["status"] == "fail"
    # the reverse: full-screen shot A dissolving into boxed B; the period ends inside the dissolve
    A = seg(1, "raw", 0, O + D, 100, transition_out=xfade(D), box=dict(full), region=1)
    B = seg(2, "raw", O, n, 900, transition_in=xfade(D))
    lb = {"periods": [{"comp_in": 0, "comp_out": O + 3, "mode": "fullscreen"}, {"comp_in": O + 3, "comp_out": n, "mode": "boxed"}]}
    r = verify.check_coverage([A, B], n, lb)
    assert r["status"] == "pass_with_exceptions" and not r["failures"], r
    lb["periods"][0]["comp_out"] = lb["periods"][1]["comp_in"] = O + D + 2      # ends 2 frames after the dissolve
    assert verify.check_coverage([A, B], n, lb)["status"] == "pass_with_exceptions"
    lb["periods"][0]["comp_out"] = lb["periods"][1]["comp_in"] = O + D + 3
    assert verify.check_coverage([A, B], n, lb)["status"] == "fail"
    # a merged sliver of a hard cut: boxed A runs 2 frames into the full-screen period
    A = seg(1, "raw", 0, 32, 100)
    B = seg(2, "raw", 32, n, 900, box=dict(full), region=1)
    lb = {"periods": [{"comp_in": 0, "comp_out": 30, "mode": "boxed"}, {"comp_in": 30, "comp_out": n, "mode": "fullscreen"}]}
    r = verify.check_coverage([A, B], n, lb)
    assert r["status"] == "pass_with_exceptions" and "merged sliver" in r["exceptions"][0], r
    A.comp_out, B.comp_in = 33, 33
    assert verify.check_coverage([A, B], n, lb)["status"] == "fail"


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


def _soft_textures(n: int, w: int = 64, h: int = 48, seed: int = 3) -> np.ndarray:
    """Textures of moderate contrast (mean 128, std ~35) so a +-10 % contrast change never clips."""
    import cv2
    rng = np.random.default_rng(seed)
    out = np.empty((n, h, w), np.uint8)
    for i in range(n):
        img = cv2.GaussianBlur(rng.uniform(0, 255, (h, w)).astype(np.float32), (0, 0), 1.5)
        img = (img - img.mean()) / max(float(img.std()), 1e-6) * 35.0 + 128.0
        out[i] = np.clip(np.round(img), 0, 255).astype(np.uint8)
    return out


@pytest.mark.parametrize("gain,blur", [(0.9, 0.0), (0.94, 0.0), (0.96, 0.0), (1.1, 0.0), (1.0, 0.8), (0.9, 1.2)])
def test_cuts_crossfade_window_under_contrast_change_and_softness(phase, gain, blur):
    """review R2-1: the repost's contrast change (gain 0.9-1.1, + lift) or a slight softness made the gain-free
    constrained blend fit measure alpha_B ~ (1-g)/2 on the pure frames around a dissolve and tilt the ramp, so
    the re-fitted window of a CORRECT crossfade came out (32, 9) / (32, 10) instead of (34, 6) and c2 failed.
    The gain-independent estimator (beta_B / (beta_A + beta_B)) refits exactly (O, D)."""
    import cv2
    O, D, n = 34, 6, 60
    raw = _soft_textures(200)
    ja, jb = 10, 120                                   # A shows RAW 10 + k, B shows RAW 120 + (k - O)
    comp = np.empty((n,) + raw.shape[1:], np.uint8)
    for k in range(n):
        al = min(1.0, max(0.0, (k - O) / D))
        a = raw[ja + k].astype(np.float32) if k < O + D else 0.0
        b = raw[jb + k - O].astype(np.float32) if k >= O else 0.0
        y = gain * ((1.0 - al) * a + al * b) + 12.0 * (1.0 if gain < 1.0 else -1.0)
        if blur:
            y = cv2.GaussianBlur(np.float32(y), (0, 0), blur)
        comp[k] = np.clip(np.round(y), 0, 255).astype(np.uint8)
    scorer = verify.ProxyScorer(_proxy(comp, "competitor"), _proxy(raw, "raw"), None, lambda k: None, 64, Config())
    xf = xfade(D)
    a = seg(1, "raw", 0, O + D, ja, transition_out=dict(xf))
    b = seg(2, "raw", O, n, jb, transition_in=dict(xf))
    r = verify.check_cuts([a, b], F30, F30, (64, 48), 200, scorer, Config())
    c = r["cuts"][0]
    assert c["window_fit"] == {"O": O, "D": D}, c
    assert r["status"] == "pass", r["failures"]
    # a crossfade declared one frame short under the same grading is still caught
    xf5 = xfade(D - 1)
    a5 = seg(1, "raw", 0, O + D - 1, ja, transition_out=dict(xf5))
    b5 = seg(2, "raw", O, n, jb, transition_in=dict(xf5))
    assert verify.check_cuts([a5, b5], F30, F30, (64, 48), 200, scorer, Config())["status"] == "fail"


def test_proxy_scorer_blend_alpha_is_gain_independent():
    """review R2-1: ProxyScorer.blend measures alpha_B without a gain assumption (pure frames stay ~0 / ~1)."""
    raw = _soft_textures(4)
    for g in (0.85, 1.0, 1.15):
        comp = np.stack([np.clip(np.round(g * ((1 - al) * raw[0].astype(np.float32) + al * raw[1]) + 5), 0, 255)
                         .astype(np.uint8) for al in (0.0, 0.3, 1.0)])
        sc = verify.ProxyScorer(_proxy(comp, "competitor"), _proxy(raw, "raw"), None, lambda k: None, 64, Config())
        ident = Sim(1.0, 0.0, 0.0, 0.0)
        got = [sc.blend(k, (0, ident, False), (1, ident, False))[0] for k in range(3)]
        assert got == pytest.approx([0.0, 0.3, 1.0], abs=0.02), (g, got)
    # the estimator: beta_B / (beta_A + beta_B); undefined for an unrelated frame or collinear sources
    from match_cuts import scoring
    assert scoring.blend_alpha_cov(1.0, 1.0, 0.0, 0.7 * 0.9, 0.3 * 0.9)[0] == pytest.approx(0.7)
    assert math.isnan(scoring.blend_alpha_cov(1.0, 1.0, 0.0, 0.01, 0.02)[0])       # beta_A + beta_B <= 0.05
    assert math.isnan(scoring.blend_alpha_cov(1.0, 1.0, 1.0, 0.5, 0.5)[0])         # A == B


def test_fit_crossfade_window_drops_frames_within_the_purity_tolerance():
    """review R2-1 (second guard): residual alpha on the pure frames around the ramp does not tilt the fit."""
    rows = [(31, 0.04), (32, 0.045), (33, 0.05), (34, 0.05), (35, 1 / 6), (36, 2 / 6), (37, 3 / 6), (38, 4 / 6),
            (39, 5 / 6), (40, 0.95), (41, 0.955), (42, 0.96)]
    assert verify.fit_crossfade_window(rows) != (34, 6)
    assert verify.fit_crossfade_window(rows, pure=max(0.5 / 6, 0.05)) == (34, 6)


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


def test_ae_sim_ties_only_from_the_plans_own_slack(phase):
    """FX-11: a timing tie is a property of sampling a moving line ON a frame boundary. With the cutlist, a frame
    flagged 'tie' is accepted only where its segment's own position is within TIE_SLACK of a boundary: a freeze
    (speed 0, a remap hold) never ties (an AE frame one off there is a mismatch), and neither does a stretch frame
    with plenty of slack."""
    tie_slack = 1e-4                            # phase_solve.TIE_SLACK (the fixture stubs phase_solve)
    n = 40
    # S1: v = 1 at 30 fps whose position at k = 10 sits exactly on a boundary (raw_in = 100 / 30 s: integer
    # positions everywhere); S2: a 10-frame freeze on RAW 200
    s1 = seg(1, "raw", 0, 30, 100)
    s1.raw_in_seconds = 100 / 30.0
    s2 = seg(2, "raw", 30, n, None, speed=0.0)
    s2.time_remap_keys = [{"comp_frame": 30, "raw_seconds": 200.25 / 30.0}, {"comp_frame": 40, "raw_seconds": 200.25 / 30.0}]
    truth = [100 + k for k in range(30)] + [200] * 10
    fm = _with_pre_segment(frame_map(truth))
    sim = list(truth)
    sim[10] = truth[10] - 1                     # the floor of an exact boundary may land one lower: a real tie
    sim[33] = truth[33] + 1                     # one off on the freeze
    fm.tie[10] = fm.tie[33] = True
    fm.d["pre_segment_tie"] = np.asarray(fm.tie).copy()
    r = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, n, [], Config(), segments=[s1, s2], raw_fps=F30)
    assert [x["k"] for x in r["timing_tie"]] == [10] and r["timing_tie"][0]["slack"] < tie_slack
    assert [x["k"] for x in r["mismatches"]] == [33] and r["mismatches"][0]["tie_rejected"] == "hold"
    # a stretch frame with 0.5 RAW frame of slack is no tie either
    s1.raw_in_seconds = 100.5 / 30.0
    r = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, n, [], Config(), segments=[s1, s2], raw_fps=F30)
    assert 10 not in [x["k"] for x in r["timing_tie"]] and "slack" in r["mismatches"][0]["tie_rejected"]


class RefitStub(StubScorer):
    """StubScorer whose refit returns the stub score (its 'own framing' measurement)."""

    def refit(self, k, cand, inits=()):
        return cand[1], float(self.score(k, [cand])[0])


def test_ae_sim_reassigned_rows_carry_reason_gap_delta_and_class(phase):
    """FX-11 / FX-12: every re-assigned / mismatched c3 row says WHY (segment.py's reason column), the scores of
    refine's frame and of the AE frame each under its own per-frame refit, the gap, refine's delta and a class --
    within noise / outside noise / systematic run (consecutive rows in one direction whose summed gap exceeds delta).
    Evidence only: the fraction and the status do not change."""
    from match_cuts.model import reassign_code
    truth = list(range(100, 300))
    fm = _with_pre_segment(frame_map(truth))
    fm.delta = np.full(200, 0.003, np.float32)
    re_ks = {50: 1, 120: 1, 121: 1, 122: 1}
    for k, d in re_ks.items():
        fm.d["pre_segment_raw"][k] = truth[k] + d
        fm.d["pre_segment_raw_lo"][k] = fm.d["pre_segment_raw_hi"][k] = truth[k] + d
    ra = np.zeros(200, np.int8)
    ra[50] = reassign_code("model")
    ra[[120, 121, 122]] = reassign_code("tiny_segment_merged")
    fm.reassigned = ra
    segs = [seg(1, "raw", 0, 200, 100)]
    segs[0].raw_in_seconds = 100.5 / 30.0
    # the stub's 'truth' is refine's measured frame: m beats the AE frame by 0.02 per RAW frame
    scorer = RefitStub({k: int(fm.d["pre_segment_raw"][k]) for k in range(200)})
    base = verify.check_ae_sim(_sim_frames(truth), fm, F30, F30, 200, [], Config(), segments=segs, raw_fps=F30)
    r = verify.check_ae_sim(_sim_frames(truth), fm, F30, F30, 200, [], Config(), segments=segs, raw_fps=F30,
                            scorer=scorer, raw_wh=(1920.0, 1080.0))
    assert r["status"] == base["status"] and r["fraction_ok"] == base["fraction_ok"]
    rows = {x["k"]: x for x in r["reassigned"]}
    assert rows[50]["why"] == "model" and rows[120]["why"] == "tiny_segment_merged"
    assert rows[50]["gap"] == pytest.approx(0.02, abs=1e-6) and rows[50]["delta"] == pytest.approx(0.003)
    assert rows[50]["class"] == "outside noise"
    assert {rows[k]["class"] for k in (120, 121, 122)} == {"systematic run"}
    assert r["reassigned_classes"] == {"outside noise": 1, "systematic run": 3}
    assert any("systematic run" in e for e in r["exceptions"])


def test_ae_sim_plan_and_mock_record_printed_once_when_identical():
    """FX-12: s9_2 from the plan and from the mock-run record classify every frame the same in the usual case: the
    result is printed once ('plan == mock record'), otherwise both are listed."""
    truth = list(range(100, 300))
    fm = frame_map(truth)
    sim = list(truth)
    sim[9] = truth[9] + 3
    p2 = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 200, [], Config(), source="plan")
    m2 = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 200, [], Config(), source="mock record")
    merged = verify.merge_ae_sim(p2, m2)
    assert merged["same"] and len(merged["exceptions"]) == len(p2["exceptions"])
    assert all(e.startswith("plan == mock record:") for e in merged["exceptions"])
    sim[11] = truth[11] + 2
    m3 = verify.check_ae_sim(_sim_frames(sim), fm, F30, F30, 200, [], Config(), source="mock record")
    merged = verify.merge_ae_sim(p2, m3)
    assert not merged["same"] and len(merged["exceptions"]) == len(p2["exceptions"]) + len(m3["exceptions"])


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
    # the JSX self-check switching a stretch layer to frame-exact remap is allowed (and reported); the JSX
    # also renames the layer '<name>  [frames]' (review AE2-1)
    def switch(b):
        L = b["default"]["comps"][1]["layers"][0]
        L.update(timeRemapEnabled=True, startTime=0.0, stretch=100.0, name="S01  RAW  [frames]")
    r = mutated(switch)
    assert r["status"] == "pass" and r["switched_to_frames"] == ["S01  RAW"], r["failures"]
    bad = json.loads(json.dumps(recs))
    switch(bad)
    bad["default"]["comps"][1]["layers"][0]["name"] = "S01  RAW  [other]"
    assert verify.check_mock(plan, bad, fps, 90, tmp_path, "raw.mp4", layer_checker=None)["status"] == "fail"
    # ... and the switched layer is still key-checked, as the frames-mode layer the JSX made of it
    seen = {}

    def checker(P, L, F):
        seen[P["id"]] = (P["timeMode"], P.get("stretch"), P.get("audio"))
        return []
    ok = json.loads(json.dumps(recs))
    switch(ok)
    plan_x = json.loads(json.dumps(plan))
    plan_x["layers"][0]["expect"] = list(range(40))        # export_ae's per-frame list (frames-mode key count)
    assert verify.check_mock(plan_x, ok, fps, 90, tmp_path, "raw.mp4", layer_checker=checker)["status"] == "pass"
    assert seen["seg1"] == ("frames", 100.0, False)
    seen.clear()                                            # a generic plan without it: keys / switches only
    assert verify.check_mock(plan, ok, fps, 90, tmp_path, "raw.mp4", layer_checker=checker)["status"] == "pass"
    assert seen["seg1"] == ("still", 100.0, False)
    na = verify.check_mock(plan, {"default": {"status": "not_available", "reason": "node missing"}}, fps, 90, tmp_path, "raw.mp4")
    assert na["status"] == "not_available"
    # per-layer key / render-switch problems reported by the layer checker fail c6
    r = verify.check_mock(plan, recs, fps, 90, tmp_path, "raw.mp4",
                          layer_checker=lambda P, L, F: ["ADBE Opacity: 0 keys (plan 8)"] if P["id"] == "seg1" else [])
    assert r["status"] == "fail" and "ADBE Opacity" in r["failures"][0]


def test_mock_check_accepts_the_jsx_runtime_switch_to_frames(tmp_path):
    """review AE2-1: with --ae-time-mode stretch, a segment that ends on the RAW's last frame gets the plan warning
    'AE will clamp it (the JSX self-check then falls back to frame-exact remapping)'; the JSX does exactly that
    and renames the layer '<name>  [frames]'. c6 (check_mock on the real mock record) failed on the rename and
    skipped the switched layer's key / render-switch checks; it now passes and still checks that layer."""
    from match_cuts import export_ae as ea
    from match_cuts.model import Cutlist
    if ea._find_node() is None:
        pytest.skip("Node.js not installed (AE mock not available)")
    RF = Fraction(30000, 1001)

    def raw_time(j0: int, phase: float) -> float:
        return float((j0 + Fraction(phase)) / RF)
    sim1 = {"scale": 0.52, "rotation_deg": 0.0, "tx": -10.0, "ty": 480.0}
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=45, raw_in_seconds=raw_time(100, 0.5), speed=1.0,
                    transform=dict(sim1)),
            Segment(id=2, type="raw", comp_in=45, comp_out=90, raw_in_seconds=raw_time(5355, 0.5), speed=1.0,
                    transform=dict(sim1))]
    comp = {"file": "media/competitor_ref.mp4", "file_rel": "media/competitor_ref.mp4", "width": 1080,
            "height": 1920, "fps": "30/1", "frames": 90, "has_audio": True}
    raw = {"file": "media/raw.mp4", "file_rel": "media/raw.mp4", "width": 1920, "height": 1080,
           "fps": "30000/1001", "frames": 5400, "conformed": False, "has_audio": True}
    layout = {"mode": "match", "layout_kind": "boxed", "canvas_bg": "#000000",
              "box": {"x": 60.4, "y": 459.6, "w": 959.3, "h": 1000.5, "corner_radius": 36.0},
              "background": "solid", "background_detail": {"type": "solid", "color": "#000000"}, "zones": [],
              "captions": []}
    cl = Cutlist(1, comp, raw, layout, segs)
    cfg = Config(ae_time_mode="stretch")
    meta = ea.footage_meta_from_cutlist(cl)
    plan = ea.ae_plan(cl, cfg, meta)
    assert any("AE will clamp it" in w for w in plan["warnings"]), plan["warnings"]
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, cfg)
    for rel in ("media/raw.mp4", "media/competitor_ref.mp4"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"")
    recs = {sc: ea.run_jsx_in_mock(jsx, meta, sc) for sc in ("default", "media_missing", "new_project_null",
                                                              "no_marker_property")}
    rec = recs["default"]
    names = [L.get("name") for c in rec["comps"] for L in c["layers"]]
    assert any(str(n).endswith("  [frames]") for n in names), names          # the runtime switch happened
    r = verify.check_mock(plan, recs, Fraction(30), 90, tmp_path, "raw.mp4")
    assert r["status"] == "pass", r["failures"]
    assert len(r["switched_to_frames"]) == 1 and r["switched_to_frames"][0].startswith("S02")
    # the switched layer's render switches are still checked (record_layer_problems on the frames-mode copy)
    bad = json.loads(json.dumps(recs))
    for c in bad["default"]["comps"]:
        for L in c["layers"]:
            if str(L.get("name")).endswith("  [frames]"):
                L["quality"] = "DRAFT"
    r = verify.check_mock(plan, bad, Fraction(30), 90, tmp_path, "raw.mp4")
    assert r["status"] == "fail" and "render switches" in json.dumps(r["failures"]), r["failures"]
    # its sound moved to the runtime audio-only twin ('mc:seg2_audio'): a missing or muted twin fails c6
    twin = [L for c in rec["comps"] for L in c["layers"] if L.get("comment") == "mc:seg2_audio"]
    assert len(twin) == 1 and twin[0]["enabled"] is False and twin[0]["audioEnabled"] is True, twin
    for mutate, needle in ((lambda L: L.update(comment="x", name="x"), "without its audio twin"),
                           (lambda L: L.update(audioEnabled=False), "audio twin: enabled")):
        bad = json.loads(json.dumps(recs))
        for c in bad["default"]["comps"]:
            for L in c["layers"]:
                if L.get("comment") == "mc:seg2_audio":
                    mutate(L)
        r = verify.check_mock(plan, bad, Fraction(30), 90, tmp_path, "raw.mp4")
        assert r["status"] == "fail" and needle in json.dumps(r["failures"]), r["failures"]


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
            # an unconverged sample (sim None) keeps a good model score: only the flip is under test there
            return {"sim": sim_true, "z": 0.99, "z_model": 0.80 if sim_true is not None and sim_true is not model else 0.99,
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


def _calib_plan() -> dict:
    """A 23.976-in-30 plan: one stretch layer (raw_in 100.25 frames) and one layer exported frame-exact
    because of its slack (FX-10)."""
    rf = 24000 / 1001
    return {"main": {"fps": {"num": 30, "den": 1}}, "rawFps": {"num": 24000, "den": 1001},
            "layers": [{"id": "seg1", "kind": "raw", "timeMode": "stretch", "compIn": 0, "compOut": 10,
                        "stretch": 100.0, "startStretch": -100.25 / rf},
                       {"id": "seg2", "kind": "raw", "timeMode": "frames", "compIn": 10, "compOut": 20,
                        "stretch": 100.0, "startStretch": 0.0}],
            "decisions": [{"decision": "time_mode_slack", "segment": 2, "slack_frames": 0.001998}]}


def test_s9_6_calibrates_ae_time_resolution_from_the_jsx_time_check_and_the_render(tmp_path):
    """FX-10: s9_6 reports the plan's slack tolerance, the layers exported frame-exact because of it, the
    JSX's AE source-time check of this run (ae_time_check.txt) and, with a render, the smallest plan slack AE
    rendered right; a frame-exact layer AE still maps to another RAW frame fails."""
    plan = _calib_plan()
    tc = tmp_path / "ae_time_check.txt"
    rows = [["seg1", "stretch", "10", "0", "0.049900000", "2.000e-9", "0.049900000"],
            ["seg2", "frames", "10", "0", "0.250000000", "1.000e-9", "0.250000000"]]
    tc.write_text("# header\n" + "".join("\t".join(r) + "\n" for r in rows))
    r = verify.check_ae_render({"aerender": None}, None, None, 20, F30, (36, 64), tmp_path, Config(), plan=plan,
                               time_check_path=tc)
    assert r["status"] == "not_available"
    cal = r["ae_time"]
    assert cal["slack_tol_frames"] == 0.01 and cal["frame_exact_for_slack"] == [2] and cal["stretch_layers"] == 1
    assert cal["time_check"]["layers"] == 2 and cal["time_check"]["off"] == 0 and cal["rendered"] is None
    assert "AE source-time check: 2 layer(s), 20 frames, 0 off the plan" in r["summary"]
    tc.write_text("\t".join(["seg2", "frames", "10", "1", "0.000001000", "2.500e-1", "0.250000000"]) + "\n")
    r = verify.check_ae_render({"aerender": None}, None, None, 20, F30, (36, 64), tmp_path, Config(), plan=plan,
                               time_check_path=tc)
    assert r["status"] == "fail" and any("seg2" in f for f in r["failures"])
    # with a render: the plan slack of the stretch frames AE rendered right, mismatches at low slack listed
    cal = verify.ae_time_calibration(plan, None, {"frames_rendered": 20, "mismatches": [{"K": 3}]}, Config())
    sl = verify._plan_stretch_slack(plan)
    assert sorted(sl) == list(range(10)) and cal["rendered"]["stretch_frames"] == 10
    assert cal["rendered"]["min_slack_rendered_ok"] == pytest.approx(min(v for k, v in sl.items() if k != 3))
    low = [k for k, v in sl.items() if v < 0.02]
    assert cal["rendered"]["mismatches_low_slack"] == ([3] if 3 in low else [])
    # no time check of this run: nothing read (a stale file of an earlier run is never used)
    assert verify._ae_time_check_path(types.SimpleNamespace(paths={"jsx": str(tmp_path / "x.jsx")},
                                                            ae_run={"status": "not_available"})) is None


def _write_video(path: Path, frames: np.ndarray, fps=F30) -> None:
    from match_cuts.media import FFmpegWriter
    h, w = frames.shape[1:3]
    with FFmpegWriter(path, w, h, fps, codec_args=["-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p"]) as wr:
        for f in frames:
            wr.write(np.repeat(f[:, :, None], 3, axis=2) if f.ndim == 2 else f)


def _fake_aerender(path: Path, frames_dir: Path, n: int, rc: int = 0) -> Path:
    """A fake aerender: copies the first ``n`` PNGs of frames_dir to the -output pattern, exits with ``rc``."""
    from portable import fake_exe
    return fake_exe(path, "import sys, shutil, pathlib\n"
                    "out = pathlib.Path(sys.argv[sys.argv.index('-output') + 1])\n"
                    f"src = sorted(pathlib.Path({str(frames_dir)!r}).glob('*.png'))[:{n}]\n"
                    "if 'PNG' in sys.argv[sys.argv.index('-OMtemplate') + 1]:\n"
                    "    for i, p in enumerate(src):\n"
                    "        shutil.copy(p, out.parent / ('ae_%05d.png' % i))\n"
                    f"sys.exit({rc})\n")


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
    files = ["build_ae_project.jsx", "cutlist.json", "cutlist.csv", "1_edit.xml", "recreated_edit.edl",
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
    (out / "1_edit.xml").unlink()                  # the Premiere sequence, in the run folder (= out here)
    r = verify.check_deliverables(ctx, n_cuts=1)
    assert r["status"] == "fail" and any("1_edit.xml missing" in f for f in r["failures"])
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


def test_previous_run_comparison_ignores_locations_and_gates_on_ffmpeg(monkeypatch, tmp_path):
    """review R2-6: a rerun into the same --out after moving / renaming the (content-identical) inputs, or after
    an ffmpeg upgrade, failed s9_7 ('requires a re-run to reproduce it', exit 1). Location-only media fields are
    canonicalised away; a different ffmpeg version skips the comparison (it may change decoded pixels)."""
    from match_cuts import pipeline
    cur = _cutlist(timings={"S2": 1.0}).to_dict()
    cur["provenance"].update(input_hashes={"competitor": "a", "raw": "b"}, analysis_params_hash="p",
                             stage_versions={"x": 1}, ffmpeg_version="6.1.1")
    cur["settings"] = {"layout_mode": "match"}
    for role, name in (("competitor", "competitor_ref.mp4"), ("raw", "raw.mp4")):
        cur[role] = {"file": f"media/{name}", "file_rel": f"media/{name}", "file_abs": f"/old/out/media/{name}",
                     "source_path": f"/old/inputs/{name}", "hash": role, "width": 64}
    moved = json.loads(json.dumps(cur))
    for role in ("competitor", "raw"):
        moved[role].update(source_path=f"/new/place/{role}_renamed.mp4", file_abs=f"/new/out/media/{role}_x.mp4",
                           file=f"media/{role}_x.mp4", file_rel=f"media/{role}_x.mp4")
    moved["provenance"]["timings"] = {"S2": 9.0}
    assert verify.compare_with_previous_run(moved, cur) == {"compared": True, "identical": True, "differences": []}
    moved["raw"]["hash"] = "other"                          # a real content difference still counts
    r = verify.compare_with_previous_run(moved, cur)
    assert r["compared"] and not r["identical"] and r["differences"] == ["/raw/hash: 'other' != 'raw'"]
    new_ff = json.loads(json.dumps(cur))
    new_ff["provenance"]["ffmpeg_version"] = "7.1"
    r = verify.compare_with_previous_run(new_ff, cur)
    assert r["compared"] is False and r["changed"] == ["ffmpeg_version"]
    new_ff["raw"]["hash"] = "other"                         # not compared at all: no failure either
    assert verify.compare_with_previous_run(new_ff, cur)["compared"] is False
    # end to end through check_determinism: the moved-inputs rerun passes s9_7
    cfg = Config()
    cfg.out_dir = str(tmp_path)
    cl = _cutlist(timings={"S2": 1.0})
    cl.provenance.update(cur["provenance"])
    cl.settings = dict(cur["settings"])
    cl.competitor, cl.raw = dict(cur["competitor"]), dict(cur["raw"])
    monkeypatch.setattr(pipeline, "rerun_assembly", lambda c: cl)
    prev = cl.to_dict()
    for role in ("competitor", "raw"):
        prev[role].update(source_path="/somewhere/else.mp4", file_abs="/elsewhere/media/x.mp4")
    ctx = types.SimpleNamespace(cfg=cfg, cutlist=cl, previous_cutlist=prev)
    r = verify.check_determinism(ctx)
    assert r["status"] == "pass" and "identical to the previous run" in r["summary"], r
    prev = copy.deepcopy(prev)                              # (to_dict shares the provenance dict)
    prev["provenance"]["ffmpeg_version"] = "7.1"            # after an ffmpeg upgrade: skipped, and said so
    prev["segments"] = []
    ctx.previous_cutlist = prev
    r = verify.check_determinism(ctx)
    assert r["status"] == "pass" and "previous run not compared (ffmpeg_version changed)" in r["summary"], r


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


def _measured_fm(meas: np.ndarray, soft: int = 2) -> FrameMap:
    n = len(meas)
    fm = frame_map(list(meas))
    fm.soft_lo = np.asarray(meas, np.int32) - soft
    fm.soft_hi = np.asarray(meas, np.int32) + soft
    for c in ("status", "raw", "raw_lo", "raw_hi", "flip"):
        fm.d["pre_segment_" + c] = np.asarray(getattr(fm, c)).copy()
    return fm


def test_unsnapped_check_drops_isolated_measured_outliers_and_never_uses_soft_ranges():
    """review R2-4: one isolated argmax error in refine's measured frames (segment.py tolerates it as a drop) made
    the measured frames jointly infeasible; c4 then fell back to the soft ranges (+-2 frames on slow footage),
    which admit 1.0 / 1.05 for a genuine 1.03x segment, and failed 'left unsnapped although [1.0, 1.05] are
    feasible'. The outlier is now dropped (max-consistent subset at the segment's speed); when the measured
    frames stay infeasible the snap is 'undecidable' (listed), never judged on the soft ranges."""
    n, v, x0 = 90, 1.03, 1000.4
    meas = np.floor(x0 + v * np.arange(n)).astype(np.int64)
    bad = meas.copy()
    bad[45] += 1                                   # ONE isolated argmax error
    fm = _measured_fm(bad)
    s = Segment(id=1, type="raw", comp_in=0, comp_out=n, speed=v, unsnapped=True, raw_in_seconds=x0 / 30.0,
                transform=dict(IDENT))
    cfg = Config()
    r = verify.check_speed_framing([s], fm, F30, F30, (64, 36), Box(0, 0, 64, 36), (64, 36), cfg)
    assert not any("unsnapped although" in f for f in r["failures"]), r["failures"]
    row = r["segments"][0]
    assert row["snap_check"] == "unsnapped" and row["snap_outliers_dropped"] == [[45, 45]], row
    assert 1.029 < row["snap_range_measured"][0] <= 1.03 <= row["snap_range_measured"][1] < 1.031
    # measured frames that no single line explains (not a few isolated outliers): undecidable, not a failure
    noisy = meas.copy()
    noisy[::7] += 2
    r = verify.check_speed_framing([s], _measured_fm(noisy), F30, F30, (64, 36), Box(0, 0, 64, 36), (64, 36), cfg)
    assert r["segments"][0]["snap_check"] == "undecidable", r["segments"][0]
    assert not r["failures"] and any("snap not decidable" in e for e in r["exceptions"]), r
    # a segment wrongly left unsnapped (1.0 reproduces every measured frame but the outlier) still fails
    ones = np.arange(500, 500 + n).astype(np.int64)
    ones[30] += 1
    s1 = Segment(id=1, type="raw", comp_in=0, comp_out=n, speed=1.0004, unsnapped=True, raw_in_seconds=500.2 / 30.0,
                 transform=dict(IDENT))
    r = verify.check_speed_framing([s1], _measured_fm(ones), F30, F30, (64, 36), Box(0, 0, 64, 36), (64, 36), cfg)
    assert r["status"] == "fail" and any("unsnapped although [1.0" in f for f in r["failures"]), r


# ---------------------------------------------------------------------------------------------
# hypothesis-neutral verification: temporal signature (s9_2b), +-1 refit (s9_2c), c2 no-cut alternative /
# repeat pairs / excursions, layout-only masks, global-start framing (DESIGN §5 verify)
# ---------------------------------------------------------------------------------------------

F24 = Fraction(24)
FIX_BOX = {"x": 20.0, "y": 15.0, "w": 120.0, "h": 90.0, "corner_radius": 0.0}


def _fps_proxy(frames: np.ndarray, role: str, fps: Fraction) -> Proxy:
    n, h, w = frames.shape
    return Proxy(role, f"{role}.mp4", frames, (w, h), (1.0, 1.0), fps, np.arange(n) / float(fps), n)


def _line_seg(id_, a, b, j_at_a: float, rate: Fraction, keys_tx, ty=-15.0, speed=1.0) -> Segment:
    """Raw segment whose AE frame at comp frame a is floor(j_at_a) (RAW at ``rate`` fps, speed v) with linear
    transform keys tx(k) = keys_tx(k) at its first and last frame."""
    s = Segment(id=id_, type="raw", comp_in=a, comp_out=b, speed=speed)
    s.raw_in_seconds = j_at_a / float(rate)
    s.transform_keys = [{"comp_frame": k, "scale": 1.0, "rotation_deg": 0.0, "tx": float(keys_tx(k)), "ty": ty}
                        for k in (a, b - 1)]
    return s


def _cadence_pan(n: int = 60, j0: int = 5, phase: float = 0.1, p1: float = 3.0):
    """Competitor: RAW (24 fps, non-rigid two-layer content panning p1 px per RAW frame) shown at 30 fps with a
    pulldown cadence floor(0.8 k + phase) + j0, under an editor pan of 1.2 px per frame, with noise."""
    import motion_fixtures as mf
    raw = mf.two_layer_raw(n + 20, w=260, p1=p1)      # wide enough for the whole pan
    js = mf.cadence(n, 0.8, phase, j0)
    comp = mf.render(raw, js, mf.pan_sims(n), (160, 120), noise=1.5)
    return _fps_proxy(comp, "competitor", F30), _fps_proxy(raw, "raw", F24), js


def _temporal_check(comp, raw, segs, cfg, raw_fps=F24):
    tf = verify.TemporalFrames(comp, raw, segs, lambda k: None, None, FIX_BOX, (float(raw.size[0]), float(raw.size[1])),
                               F30, raw_fps, raw.n, cfg)
    comp_sig, labels, rec_sig = verify.temporal_signatures(tf, cfg)
    return verify.check_temporal(segs, labels, comp_sig, rec_sig, F30, raw_fps, raw.n, comp.n, cfg), labels


def _scorer(comp, raw, cfg):
    return verify.ProxyScorer(comp, raw, FIX_BOX, lambda k: None, float(raw.size[0]), cfg)


def test_shifted_recreation_with_compensating_framing_fails_refit_and_temporal_signature():
    """(a) The time / translation confound: the recreation shows RAW j+1 (j-1) with its framing shifted by one
    RAW frame's pan, so each frame still scores ~0.97 and every old check passed. Split into +1 / -1 / +1
    pieces at the competitor's repeat pairs, as the tool did in the real run. The +-1 refit finds the true
    neighbour on every frame, the temporal signature sees the recreation change RAW frame where the
    competitor repeats, and c2 fails the cuts placed inside repeat pairs. The truthful recreation passes."""
    cfg = Config()
    comp, raw, js = _cadence_pan()
    n, n_raw = comp.n, raw.n
    pan = lambda off: (lambda k: -20.0 - 1.2 * k + 3.0 * off)            # noqa: E731 - compensating framing
    truth = [_line_seg(1, 0, n, 5.1, F24, pan(0))]
    r, labels = _temporal_check(comp, raw, truth, cfg)
    assert r["status"] == "pass" and r["n_disagreements"] == 0, r
    assert labels.counts()["repeat"] >= 8
    rf = verify.check_refit(truth, frame_map(list(js)), F30, F24, (260.0, 150.0), n_raw, _scorer(comp, raw, cfg), cfg, n,
                            box_centre=(80.0, 60.0))
    assert rf["status"] == "pass" and rf["checked"] == n, rf
    # +1 / -1 / +1 pieces, cuts between the two frames of the repeat pairs (20, 21) and (40, 41)
    assert js[20] == js[21] and js[40] == js[41]
    wrong = [_line_seg(1, 0, 21, 5.1 + 1, F24, pan(+1)), _line_seg(2, 21, 41, 5.1 + 0.8 * 21 - 1, F24, pan(-1)),
             _line_seg(3, 41, n, 5.1 + 0.8 * 41 + 1, F24, pan(+1))]
    sc = _scorer(comp, raw, cfg)
    z = sc.score(30, [verify._Models(F30, F24, (260.0, 150.0), n_raw).cand(wrong[1], 30)])[0]
    assert 0.9 < z < 0.99                       # the compensated wrong frame still looks like a match
    rf = verify.check_refit(wrong, frame_map(list(js)), F30, F24, (260.0, 150.0), n_raw, sc, cfg, n,
                            box_centre=(80.0, 60.0))
    assert rf["status"] == "fail" and rf["n_neighbour_wins"] >= 0.9 * n, rf["summary"]
    w0 = rf["neighbour_wins"][0]
    assert w0["best_neighbour"] == js[w0["k"]] and w0["z_neighbour"] > w0["z_shown"] + 0.01
    r, labels = _temporal_check(comp, raw, wrong, cfg)
    assert r["status"] == "fail", r
    kinds = {(d["k"], d["kind"]) for d in r["disagreements"]}
    assert (20, "recreation_changes") in kinds and (40, "recreation_changes") in kinds
    c = verify.check_cuts(wrong, F30, F24, (260.0, 150.0), n_raw, sc, cfg, labels=labels, box_centre=(80.0, 60.0))
    assert c["status"] == "fail"
    assert all(any(sd["side"] == "repeat_pair" and sd["result"] == "fail" for sd in cut["sides"]) for cut in c["cuts"])


def test_freeze_against_a_moving_competitor_is_a_motion_mismatch():
    """(b) A 10-frame freeze (speed 0) where the competitor keeps playing (one RAW frame per comp frame)."""
    import motion_fixtures as mf
    cfg = Config()
    n = 30
    raw_frames = mf.two_layer_raw(60)
    js = np.arange(10, 10 + n)
    comp = _fps_proxy(mf.render(raw_frames, js, mf.pan_sims(n), (160, 120), noise=1.0), "competitor", F30)
    raw = _fps_proxy(raw_frames, "raw", F30)
    pan = lambda k: -20.0 - 1.2 * k          # noqa: E731
    segs = [_line_seg(1, 0, 10, 10.5, F30, pan), _line_seg(2, 10, 20, 20.5, F30, pan, speed=0.0),
            _line_seg(3, 20, n, 30.5, F30, pan)]
    r, labels = _temporal_check(comp, raw, segs, cfg, raw_fps=F30)
    assert labels.counts()["move"] >= 20 and labels.counts()["repeat"] == 0
    assert r["status"] == "fail" and r["motion_mismatch"], r
    assert r["motion_mismatch"][0]["segment"] == 2 and r["motion_mismatch"][0]["frames"] == [10, 19]
    assert any("motion mismatch: S02" in f for f in r["failures"])
    play = [_line_seg(1, 0, 10, 10.5, F30, pan), _line_seg(2, 10, 20, 20.5, F30, pan), _line_seg(3, 20, n, 30.5, F30, pan)]
    r, _ = _temporal_check(comp, raw, play, cfg, raw_fps=F30)
    assert r["status"] == "pass" and r["n_disagreements"] == 0, r


def _outlined_word(img: np.ndarray, text: str, x: int, y: int, size: int = 14, stroke: int = 2) -> None:
    """Burn an outlined caption word into ``img`` in place: DejaVu Sans Bold, white fill, black stroke (ffmpeg
    drawtext's fontcolor=white:borderw=N:bordercolor=black, like the synthetic captions)."""
    from PIL import Image, ImageDraw, ImageFont
    from portable import font_file
    font = ImageFont.truetype(font_file("bold"), size)
    im = Image.fromarray(img)
    ImageDraw.Draw(im).text((int(x), int(y)), text, font=font, fill=255, stroke_width=stroke, stroke_fill=0)
    img[:] = np.asarray(im)


def _dim(frames: np.ndarray) -> np.ndarray:
    """A mid-gray picture (no texture blob reaches the caption's white level)."""
    return np.clip(np.rint(0.5 * frames.astype(np.float32) + 40.0), 0, 255).astype(np.uint8)


def _temporal_with_overlays(comp, raw, segs, cfg, raw_fps=F30):
    """s9_2b as verify_all runs it: animated text overlays (layout's comp-only detector, checked against the
    recreation) masked from both signatures."""
    from match_cuts import layout as layout_mod
    from match_cuts.model import Layout
    lay = Layout(comp_w=comp.size[0], comp_h=comp.size[1], box=Box.from_dict(FIX_BOX))
    raw_wh = (float(raw.size[0]), float(raw.size[1]))
    zones = verify.animated_text_zones(comp, raw, segs, lay, None, raw_wh, F30, raw_fps, raw.n, cfg)
    shape = (comp.size[1], comp.size[0])
    mask_out = (lambda k: layout_mod.animated_text_mask(zones, k, shape, 3)) if zones else None
    tf = verify.TemporalFrames(comp, raw, segs, lambda k: None, None, FIX_BOX, raw_wh, F30, raw_fps, raw.n, cfg,
                               mask_out=mask_out)
    comp_sig, labels, rec_sig = verify.temporal_signatures(tf, cfg)
    return verify.check_temporal(segs, labels, comp_sig, rec_sig, F30, raw_fps, raw.n, comp.n, cfg, masked=zones), zones


def _freeze_case(play_during_hold: bool):
    """30 competitor frames (30 fps RAW): plays RAW 10..19, then holds RAW 20 (or keeps playing 20..29 when
    ``play_during_hold``), then plays RAW 30..; a caption word slides 3 px / frame over frames 10..19. The
    recreation plays, HOLDS RAW 20 on 10..19, plays."""
    import motion_fixtures as mf
    n = 30
    raw_frames = _dim(mf.two_layer_raw(60))
    js = np.array([10 + k if k < 10 else (20 + (k - 10) if play_during_hold else 20) if k < 20 else 30 + (k - 20)
                   for k in range(n)])
    pan = lambda k: -20.0 - 1.2 * k          # noqa: E731
    frames = mf.render(raw_frames, js, mf.pan_sims(n), (160, 120), noise=1.0).copy()
    for k in range(10, 20):
        _outlined_word(frames[k], "STOP", 30 + 3 * (k - 10), 35, size=10, stroke=1)
    comp = _fps_proxy(frames, "competitor", F30)
    raw = _fps_proxy(raw_frames, "raw", F30)
    hold = [_line_seg(1, 0, 10, 10.5, F30, pan), _line_seg(2, 10, 20, 20.5, F30, pan, speed=0.0),
            _line_seg(3, 20, n, 30.5, F30, pan)]
    return comp, raw, hold


def test_true_freeze_under_a_sliding_caption_is_not_a_motion_mismatch():
    """Wave 4 (b), film24 S19: a TRUE freeze (the competitor holds RAW 20) under a caption word that slides 3 px per
    frame. The word is an animated text overlay (it moves over the picture and the recreation never shows it): it is
    masked from both temporal signatures, so the motion is judged OUTSIDE it -- the hold agrees. Without the mask the
    sliding word reads as competitor motion (the old false 'motion mismatch')."""
    cfg = Config()
    comp, raw, hold = _freeze_case(play_during_hold=False)
    r, zones = _temporal_with_overlays(comp, raw, hold, cfg)
    ov = [z for z in zones if z["kind"] == "overlay"]
    assert len(ov) == 1 and ov[0]["comp_in"] == 10 and ov[0]["comp_out"] == 20, zones
    assert ov[0]["step"][0] == pytest.approx(3.0, abs=0.5) and ov[0]["glyphs"] == 4
    assert not r["motion_mismatch"] and r["status"] == "pass", r
    assert r["animated_text"] and "animated text overlay(s) masked" in r["summary"]
    unmasked, _labels = _temporal_check(comp, raw, hold, cfg, raw_fps=F30)
    assert unmasked["motion_mismatch"], unmasked          # the false positive the overlay mask removes


def test_hold_against_a_moving_competitor_stays_a_motion_mismatch_under_a_sliding_caption():
    """Negative control: the competitor PLAYS during the hold (and a caption slides over it). Masking the caption
    must not hide the picture's motion outside it: still a motion mismatch."""
    cfg = Config()
    comp, raw, hold = _freeze_case(play_during_hold=True)
    r, zones = _temporal_with_overlays(comp, raw, hold, cfg)
    assert any(z["kind"] == "overlay" for z in zones), zones
    assert r["status"] == "fail" and r["motion_mismatch"], r
    assert r["motion_mismatch"][0]["segment"] == 2


def test_animated_text_the_compared_picture_also_shows_is_picture_content():
    """Negative control of the overlay test itself: a word moving over the picture is an overlay only when the
    compared picture (verification: the recreation) never shows it on its place. The same word present in the
    reference -- text carried by the RAW (a scrolling credit, a sign on a moving object) -- is 'picture_content' and
    never masked; a word that moves WITH the picture is never even a candidate."""
    import motion_fixtures as mf
    from match_cuts import layout as layout_mod
    from match_cuts.model import Layout
    cfg = Config()
    n = 12
    base = _dim(mf.two_layer_raw(1))[0]
    frames = np.repeat(mf.render(base[None], [0], [Sim(1.0, 0.0, -20.0, -15.0)], (160, 120), noise=0.0), n, axis=0)
    with_word = frames.copy()
    for k in range(n):
        _outlined_word(with_word[k], "CREDITS", 30 + 3 * k, 35)
    comp = _fps_proxy(with_word, "competitor", F30)
    lay = Layout(comp_w=160, comp_h=120, box=Box.from_dict(FIX_BOX))
    alone = layout_mod.animated_text_overlays(comp, lay, cfg)
    assert [z["kind"] for z in alone] == ["overlay"] and alone[0]["glyphs"] == 7
    no_word = layout_mod.animated_text_overlays(comp, lay, cfg, reference=lambda k: frames[k])
    assert [z["kind"] for z in no_word] == ["overlay"]
    shown = layout_mod.animated_text_overlays(comp, lay, cfg, reference=lambda k: with_word[k])
    assert [z["kind"] for z in shown] == ["picture_content"], shown
    assert layout_mod.animated_text_mask(shown, 5, (120, 160)) is None
    assert layout_mod.animated_text_mask(no_word, 5, (120, 160)).any()
    # no compared picture on any of its frames (a NOT-IN-RAW / uncertain stretch): not judged, never masked or reported
    nothing = layout_mod.animated_text_overlays(comp, lay, cfg, reference=lambda k: None)
    assert [z["kind"] for z in nothing] == ["not_compared"], nothing
    assert layout_mod.animated_text_mask(nothing, 5, (120, 160)) is None
    # the word panning WITH the picture (an editor pan over burned-in text) is no candidate at all
    pan_frames = mf.render(np.repeat(with_word[:1], 1, axis=0), [0] * n,
                           [Sim(1.0, 0.0, -2.0 * k, 0.0) for k in range(n)], (140, 110), noise=0.0)
    panned = _fps_proxy(np.pad(pan_frames, ((0, 0), (5, 5), (10, 10))), "competitor", F30)
    assert layout_mod.animated_text_overlays(panned, lay, cfg) == []


# -- RAW-only overlays (wave 4 (a)) --------------------------------------------------------------

def _disclaimer_case(text_in_raw: bool = True, rec_offset: int = 0, rec_dx: float = 0.0, hold: bool = False,
                     comp_logo: bool = False, big: bool = False, n: int = 30):
    """RAW (30 fps): moving two-layer content; with ``text_in_raw`` a burned-in disclaimer line static in RAW
    coordinates (``big``: a block covering ~40 % of the picture). The competitor's master shows the CLEAN RAW 10..39
    under an editor pan (``comp_logo``: plus a competitor-only outlined word over a STATIC flat patch of the RAW).
    The recreation: one raw segment on the true line shifted by ``rec_offset`` RAW frames, its framing by ``rec_dx``
    px (``hold``: speed 0 on RAW 20)."""
    import motion_fixtures as mf
    clean = mf.two_layer_raw(60)
    if comp_logo:
        clean[:, 100:118, 100:170] = 90                      # a static flat patch of the RAW
    raw_frames = clean.copy()
    if text_in_raw:
        for j in range(60):
            if big:
                raw_frames[j, 30:110, 40:160] = 200
                _outlined_word(raw_frames[j], "BIG GRAPHIC", 55, 60, size=12, stroke=1)
            else:
                _outlined_word(raw_frames[j], "NOT A SUBSTITUTE", 55, 95, size=11, stroke=1)
    js = np.arange(10, 10 + n)
    comp_frames = mf.render(clean, js, mf.pan_sims(n), (160, 120), noise=1.0).copy()
    if comp_logo:
        for k in range(n):
            x0 = int(round(100 - 20 - 1.2 * k))
            _outlined_word(comp_frames[k], "LOGO", x0 + 4, 100 - 15 + 1, size=11, stroke=1)
    comp = _fps_proxy(comp_frames, "competitor", F30)
    raw = _fps_proxy(raw_frames, "raw", F30)
    pan = lambda k: -20.0 - 1.2 * k + rec_dx          # noqa: E731
    seg_ = (_line_seg(1, 0, n, 20.5, F30, pan, speed=0.0) if hold
            else _line_seg(1, 0, n, 10.5 + rec_offset, F30, pan))
    return comp, raw, [seg_], frame_map(list(js))


def _raw_overlays(comp, raw, segs, fm, cfg):
    return verify.find_raw_only_overlays(comp, raw, segs, fm, lambda k: None, lambda k: FIX_BOX,
                                         (float(raw.size[0]), float(raw.size[1])), F30, F30, raw.n, cfg)


def _visual(comp, raw, segs, fm, cfg, ov=None):
    seg_at = verify.single_raw_segments(segs, comp.n)
    rec = [(k, verify.recreation_proxy_frame(comp, raw, seg_at, k, (float(raw.size[0]), float(raw.size[1])), F30, F30,
                                             raw.n)) for k in range(comp.n)]
    return verify.check_visual(comp, rec, fm, lambda k: None, FIX_BOX, cfg, None, "test", segments=segs,
                               overlay_fn=ov.mask if ov else None,
                               overlay_lines=verify.raw_only_overlay_lines(ov.regions) if ov else ())


def test_raw_only_overlay_is_measured_masked_and_reported():
    """Wave 4 (a), film24 clip B / the real run's 605+ shots: the RAW carries a burned-in disclaimer the competitor's
    master does not have; every frame is exact but the visual check failed. The disclaimer is measured as a RAW-only
    overlay (static in RAW coordinates while the RAW plays, persistent residual, a RAW graphic the competitor lacks,
    small), reported with its RAW rectangle and frames, and excluded in s9_3: the frames pass as explained."""
    cfg = Config()
    comp, raw, segs, fm = _disclaimer_case()
    plain = _visual(comp, raw, segs, fm, cfg)
    assert plain["status"] == "fail" and plain["failed_frames"], plain["summary"]
    ov = _raw_overlays(comp, raw, segs, fm, cfg)
    assert len(ov.regions) == 1, (ov.regions, ov.rejected)
    x, y, w, h = ov.regions[0]["raw_rect"]
    assert 50 <= x <= 60 and 90 <= y + h and y <= 100 and w >= 60          # around the drawn line (x 55, y ~95-106)
    lines = verify.raw_only_overlay_lines(ov.regions)
    assert len(lines) == 1 and lines[0].startswith("RAW-only overlay at") and "not shown by the competitor" in lines[0]
    res = _visual(comp, raw, segs, fm, cfg, ov)
    assert res["status"] == "pass_with_exceptions", res["summary"]
    assert set(plain["failed_frames"]) <= set(res["raw_only_overlay_frames"])
    assert any("RAW-only overlay" in e for e in res["exceptions"])
    m = ov.mask(12)
    assert m is not None and m.any() and m.mean() < 0.15               # follows the frame's model, small


@pytest.mark.parametrize("case", ["wrong_frame", "wrong_framing"])
def test_raw_only_overlay_never_explains_a_wrong_frame_or_framing(case):
    """Negative control: with the same RAW-only disclaimer, a recreation one RAW frame... three RAW frames late, or
    misframed by 6 px, still fails s9_3 -- the overlay may be found (it is real), but every frame must still reach
    the threshold on everything else, and a time / framing error lives where the RAW changes."""
    cfg = Config()
    comp, raw, segs, fm = _disclaimer_case(rec_offset=3) if case == "wrong_frame" else _disclaimer_case(rec_dx=6.0)
    ov = _raw_overlays(comp, raw, segs, fm, cfg)
    res = _visual(comp, raw, segs, fm, cfg, ov)
    assert res["status"] == "fail" and len(res["failed_frames"]) >= 0.9 * comp.n, res["summary"]
    for r in ov.regions:                                     # whatever was accepted is the disclaimer, nothing else
        assert r["raw_rect"][1] >= 80 and r["frac"] < 0.15
    # and without any RAW-only content, nothing is explained at all
    comp2, raw2, segs2, fm2 = _disclaimer_case(text_in_raw=False, rec_offset=3)
    assert not _raw_overlays(comp2, raw2, segs2, fm2, cfg).regions


def test_raw_only_overlay_rejects_competitor_side_elements_freezes_and_large_regions():
    """Negative controls of the region tests: (1) a competitor-only word over a STATIC flat patch of the RAW is
    persistent and static in RAW coordinates but the edges are the competitor's -- not a RAW graphic, rejected;
    (2) a hold (one RAW frame shown) never measures 'static in RAW coordinates' -- nothing explained; (3) a RAW-only
    block covering ~40 % of the picture is a mismatch spread over the frame -- nothing is explained, s9_3 fails."""
    cfg = Config()
    comp, raw, segs, fm = _disclaimer_case(text_in_raw=False, comp_logo=True)
    ov = _raw_overlays(comp, raw, segs, fm, cfg)
    assert not ov.regions and any("no graphic the competitor lacks" in r["why"] for r in ov.rejected), ov.rejected
    comp, raw, segs, fm = _disclaimer_case(hold=True)
    ov = _raw_overlays(comp, raw, segs, fm, cfg)
    assert not ov.regions
    comp, raw, segs, fm = _disclaimer_case(big=True)
    ov = _raw_overlays(comp, raw, segs, fm, cfg)
    assert not ov.regions, ov.regions
    assert _visual(comp, raw, segs, fm, cfg, ov)["status"] == "fail"


class PanStub:
    """Stub scorer for a continuous shot under an editor pan: truth RAW frame 100 + k, true framing tx(k) =
    2 k (comp px). score = 1 - 0.02 |dj| - 0.01 |tx - tx(k) - P dj| (a time error dj is compensated by P px of
    framing); ``refit`` (when enabled) re-measures the framing: tx(k) + P dj, score 1 - 0.01 |dj|."""
    P = 6.0

    def __init__(self, refit: bool = True):
        if refit:
            self.refit = self._refit

    @staticmethod
    def tx(k):
        return 2.0 * k

    def truth(self, k):
        return 100 + int(k)

    def _z(self, k, j, tx):
        dj = int(j) - self.truth(k)
        return 1.0 - 0.02 * abs(dj) - 0.01 * abs(tx - self.tx(k) - self.P * dj)

    def score(self, k, cands):
        return np.array([float("nan") if c is None else self._z(k, c[0], c[1].tx) for c in cands])

    def _refit(self, k, cand, inits=()):
        dj = int(cand[0]) - self.truth(k)
        return Sim(1.0, 0.0, self.tx(k) + self.P * dj, 0.0), 1.0 - 0.01 * abs(dj)

    def blend(self, k, a, b):
        return float("nan"), float("nan")

    def uniform(self, k):
        return 0.0, 50.0


def _pan_seg(id_, a, b, j_at_a, off_px=0.0, const_tx=None):
    """30 fps raw segment showing RAW j_at_a + (k - a); framing = the pan tx(k) + off_px (linear keys), or a
    constant tx."""
    s = seg(id_, "raw", a, b, transform=Sim(1.0, 0.0, const_tx or 0.0, 0.0).to_dict())
    s.raw_in_seconds = (j_at_a + 0.5) / 30.0
    if const_tx is None:
        s.transform = None
        s.transform_keys = [{"comp_frame": k, "scale": 1.0, "rotation_deg": 0.0, "tx": PanStub.tx(k) + off_px, "ty": 0.0}
                            for k in (a, b - 1)]
    return s


def test_linear_pan_split_into_constant_segments_is_a_spurious_cut(phase):
    """(c) One continuous shot (RAW 100 + k) under a linear editor pan, cut into three segments with CONSTANT
    framing (each its pan midpoint). With framing held at the neighbour's key, every cut 'verified both
    sides'; with the framing re-measured, the time lines agree and the framing is continuous -> spurious."""
    cfg = Config()
    segs = [_pan_seg(1, 0, 10, 100, const_tx=9.0), _pan_seg(2, 10, 20, 110, const_tx=29.0),
            _pan_seg(3, 20, 30, 120, const_tx=49.0)]
    old = verify.check_cuts(segs, F30, F30, (64, 36), 1000, PanStub(refit=False), cfg, box_centre=(32, 18))
    assert old["status"] == "pass", old                   # the blind spot of held-key framing
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, PanStub(), cfg, box_centre=(32, 18))
    assert r["status"] == "fail" and len(r["failures"]) == 2, r
    assert all("spurious cut" in f and "framing is continuous" in f for f in r["failures"])
    assert r["cuts"][0]["framing_continuity"]["result"] == "continuous"
    # alternating +1 / -1 / +1 time lines whose framing compensates the time error (the real 39-70 case)
    alt = [_pan_seg(1, 0, 10, 101, PanStub.P), _pan_seg(2, 10, 20, 109, -PanStub.P), _pan_seg(3, 20, 30, 121, PanStub.P)]
    r = verify.check_cuts(alt, F30, F30, (64, 36), 1000, PanStub(), cfg, box_centre=(32, 18))
    assert r["status"] == "fail"
    assert all(any(sd["side"] == "no_cut" and "time line extended" in sd["reason"] for sd in c["sides"]) for c in r["cuts"])

    class JumpStub(PanStub):                      # a genuine same-shot jump cut: B skips 10 RAW frames
        def truth(self, k):
            return 100 + int(k) + (10 if k >= 10 else 0)

        def _refit(self, k, cand, inits=()):
            return Sim(1.0, 0.0, self.tx(k), 0.0), 1.0 - 0.02 * abs(int(cand[0]) - self.truth(k))
    jump = [_pan_seg(1, 0, 10, 100), _pan_seg(2, 10, 20, 120)]
    r = verify.check_cuts(jump, F30, F30, (64, 36), 1000, JumpStub(), cfg, box_centre=(32, 18))
    assert r["status"] == "pass", r


def test_cut_inside_a_competitor_repeat_pair_fails(phase):
    """(d) A hard cut between the two frames of a competitor repeat pair (the same image) cannot exist."""
    truth = {k: (100 + k if k < 10 else 500 + k - 10) for k in range(20)}
    r = verify.check_cuts(_two_shots(), F30, F30, (64, 36), 1000, StubScorer(truth), Config(), labels={9: "repeat"})
    assert r["status"] == "fail" and "repeat pair" in r["failures"][0]
    r = verify.check_cuts(_two_shots(), F30, F30, (64, 36), 1000, StubScorer(truth), Config(), labels={9: "move"})
    assert r["status"] == "pass"


def test_excursion_must_beat_its_neighbours_line(phase):
    """A 2-frame segment 12 RAW frames off the line its neighbours share: a misidentification unless its own
    frames beat that line by more than the noise (then it is a verified flash cut)."""
    cfg = Config()
    segs = [_pan_seg(1, 0, 10, 100), _pan_seg(2, 10, 12, 122), _pan_seg(3, 12, 30, 112)]

    class LineStub(PanStub):                   # the competitor shows the line on 10, 11 too; flat scores
        def _z(self, k, j, tx):
            return 1.0 - 0.002 * abs(int(j) - self.truth(k)) - 0.01 * abs(tx - self.tx(k))

        def _refit(self, k, cand, inits=()):
            return Sim(1.0, 0.0, self.tx(k), 0.0), 1.0 - 0.0005 * abs(int(cand[0]) - self.truth(k))
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, LineStub(), cfg, box_centre=(32, 18))
    ex = r["cuts"][0]["excursion"]
    assert ex["offsets"] == [12, 12] and not ex["verified"]
    assert any("suspected misidentification" in f for f in r["failures"])

    class FlashStub(PanStub):                  # the competitor really shows 122, 123 on 10, 11
        def truth(self, k):
            return 112 + int(k) if 10 <= k < 12 else 100 + int(k)

        def _refit(self, k, cand, inits=()):
            return Sim(1.0, 0.0, self.tx(k), 0.0), 1.0 - 0.02 * abs(int(cand[0]) - self.truth(k))
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, FlashStub(), cfg, box_centre=(32, 18))
    assert r["cuts"][0]["excursion"]["verified"] and not any("misidentification" in f for f in r["failures"])


def _dark_scene(seed: int = 21) -> np.ndarray:
    """A dark interior: a smooth vertical illumination ramp (identical under any horizontal shift), faint
    texture, and one bright, detailed display -- the only thing a horizontal misframe disturbs."""
    import motion_fixtures as mf
    h, w = 360, 480
    yy = np.linspace(0.0, 1.0, h)[:, None]
    img = 6.0 + 40.0 * yy ** 1.5 + np.zeros((1, w)) + mf.texture(h, w, seed, sigma=2.0, std=1.5, mean=0.0)
    img[150:220, 180:300] = mf.texture(70, 120, seed + 1, sigma=1.0, std=30.0, mean=70.0)
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def test_dark_misframe_hidden_by_residual_masks_fails_c3_and_c4(tmp_path):
    """(e) A dark segment misframed by 35 px: the only mismatch is the bright display, which refine's pass-2
    residual masks hide (the mismatch masks itself away). verify's masks come from the layout only, so s9_3
    fails; c4's global-start ECC finds the true framing 35 px away and fails too."""
    import motion_fixtures as mf
    from match_cuts import layout as layout_mod
    from match_cuts.layout import OverlayMasks
    from match_cuts.model import Layout
    cfg = Config()
    n = 8
    raw_frames = np.stack([_dark_scene()] * 12)
    true = Sim(1.0, 0.0, -40.0, -30.0)
    wrong = Sim(1.0, 0.0, -40.0 - 35.0, -30.0)
    comp_frames = mf.render(raw_frames, np.zeros(n, int), [true] * n, (400, 300), noise=1.0)
    rec_frames = mf.render(raw_frames, np.zeros(n, int), [wrong] * n, (400, 300), noise=0.0)
    comp, raw = _fps_proxy(comp_frames, "competitor", F30), _fps_proxy(raw_frames, "raw", F30)
    box = Box(20.0, 20.0, 360.0, 260.0, 0.0)
    # layout masks: a caption (rows 250..270); refine's residual masks also cover where the display mismatches
    cap = np.zeros((300, 400), bool)
    cap[250:270, 60:340] = True
    ov_layout, ov_refine = OverlayMasks((300, 400), 3), OverlayMasks((300, 400), 3)
    resid = cap.copy()
    resid[105:200, 100:300] = True
    for k in range(n):
        ov_layout.set(k, cap)
        ov_refine.set(k, resid)
    ov_path = tmp_path / "layout.overlays.npz"
    ov_layout.save(ov_path)
    lay = Layout(400, 300, mode="boxed", box=box, overlay_mask_file=str(ov_path))
    ctx = types.SimpleNamespace(layout=lay, overlays=ov_refine, comp_proxy=comp, cfg=cfg, cutlist=None)
    allowed = verify._allowed_fn(ctx)
    m = allowed(3)
    assert not m[260, 200] and m[150, 200]           # caption excluded, residual-masked display NOT excluded
    fm = frame_map([0] * n)
    s = seg(1, "raw", 0, n, 0, transform=wrong.to_dict())
    s.raw_in_seconds = 0.5 / 30.0
    hidden = lambda k: layout_mod.allowed_mask(lay, ov_refine, k, comp)     # noqa: E731 - the old mask
    old = verify.check_visual(comp, list(enumerate(rec_frames)), fm, hidden, box, cfg)
    assert old["status"] == "pass", old["summary"]                          # the misframe passed
    new = verify.check_visual(comp, list(enumerate(rec_frames)), fm, allowed, box, cfg)
    assert new["status"] == "fail", new["summary"]
    scorer = verify.ProxyScorer(comp, raw, box, allowed, 480.0, cfg)
    meas = verify.framing_measure(comp, raw, scorer, allowed, lambda k: box, (480.0, 360.0), F30, F30, raw.n, cfg)
    r = verify.check_speed_framing([s], fm, F30, F30, (480.0, 360.0), box, (400.0, 300.0), cfg,
                                   feasible_range=lambda *a: (0.999, 1.001), measure=meas)
    assert r["status"] == "fail" and any("independently measured framing" in f for f in r["failures"]), r
    ind = r["segments"][0]["independent"]
    assert ind["n_samples"] >= 5 and ind["max_pos_err_px"] > 30.0


def test_unconverged_framing_with_a_low_model_score_fails():
    """c4: an unconverged sample is an exception only while the model scores like its neighbours; below
    verify_zncc, or with a gradient-domain score far below the neighbouring segments' median, it fails."""
    cfg = Config()
    segs = [seg(1, "raw", 0, 10, 100), seg(2, "raw", 10, 20, 300), seg(3, "raw", 20, 30, 500)]
    fm = frame_map(list(range(100, 110)) + list(range(300, 310)) + list(range(500, 510)))

    def make(zm2, zg2):
        def measure(sg, k, model):
            if sg.id == 2:
                return {"sim": None, "z_model": zm2, "z_grad_model": zg2, "flip_own": 0.95, "flip_other": 0.2}
            return {"sim": model, "z": 0.99, "z_model": 0.99, "z_grad_model": 0.90 + 0.001 * (k % 3),
                    "flip_own": 0.95, "flip_other": 0.2}
        return measure

    def run(m):
        return verify.check_speed_framing(segs, fm, F30, F30, (64, 36), Box(0, 0, 64, 36), (64, 36), cfg,
                                          feasible_range=lambda *a: (0.999, 1.001), measure=m)
    r = run(make(0.97, 0.89))
    assert r["status"] == "pass_with_exceptions" and not r["failures"], r
    r = run(make(0.97, 0.40))                         # plain ZNCC fine, gradients disagree (dark misframe)
    assert r["status"] == "fail" and "S02" in r["failures"][0] and "gradient" in r["failures"][0], r
    r = run(make(0.85, 0.89))
    assert r["status"] == "fail" and "model ZNCC 0.850" in r["failures"][0], r


def test_global_init_recovers_a_large_shift():
    """ProxyScorer.global_init: phase correlation moves the model to the true framing 30 px away."""
    import motion_fixtures as mf
    raw_frames = mf.two_layer_raw(3, h=200, w=260)
    true = Sim(1.0, 0.0, -30.0, -20.0)
    comp = _fps_proxy(mf.render(raw_frames, [1], [true], (200, 160), noise=0.5), "competitor", F30)
    raw = _fps_proxy(raw_frames, "raw", F30)
    box = Box(10.0, 10.0, 180.0, 140.0, 0.0)
    sc = verify.ProxyScorer(comp, raw, box, lambda k: None, 260.0, Config())
    g = sc.global_init(0, (1, Sim(1.0, 0.0, -60.0, -12.0), False))
    assert g is not None and g.tx == pytest.approx(-30.0, abs=1.0) and g.ty == pytest.approx(-20.0, abs=1.0)


def test_layout_overlay_masks_never_include_residual_masks(tmp_path):
    from match_cuts.layout import OverlayMasks, layout_overlay_masks
    from match_cuts.model import Layout
    lay = Layout(64, 36, captions=[{"comp_in": 2, "comp_out": 4, "x": 10.0, "y": 20.0, "w": 20.0, "h": 6.0}])
    ov = layout_overlay_masks(lay, (36, 64), (1.0, 1.0))          # no file: caption rectangles
    assert ov.get(1) is None and ov.get(2)[22, 15] and not ov.get(2)[5, 5]
    m = OverlayMasks((36, 64), 2)
    m.set(0, np.ones((36, 64), bool))
    p = tmp_path / "ov.npz"
    m.save(p)
    lay.overlay_mask_file = str(p)
    assert layout_overlay_masks(lay).get(0).all()
    assert layout_overlay_masks(None) is None
    # the layout's DYNAMIC zones (the caption band over the caption period) cover a word the per-frame text
    # detection missed (the full synthetic's 'EVERYTHING' at 789-800); static zones are the static mask's job
    from match_cuts.model import Zone
    lay.zones = [Zone("captions", 8.0, 18.0, 30.0, 10.0, 5, 9, False), Zone("logo", 0.0, 0.0, 6.0, 6.0)]
    ov = layout_overlay_masks(lay, (36, 64), (1.0, 1.0))
    assert ov.get(6)[20, 20] and not ov.get(6)[0, 30] and not ov.get(6)[2, 2]
    assert ov.get(9) is None and ov.get(4) is None and ov.get(0).all()
    d = ov.get_dilated(6, 2)
    assert d[16, 20] and not d[12, 20]


# ---------------------------------------------------------------------------------------------
# FX-08: honest NOT-IN-RAW / UNCERTAIN accounting
# ---------------------------------------------------------------------------------------------

class SharpStubScorer(StubScorer):
    """1.0 on the truth RAW frame only, 0.3 on any other (no slope that would let a neighbouring frame pass)."""

    def score(self, k, cands):
        out = []
        for c in cands:
            t = self.truth.get(k)
            out.append(float("nan") if c is None else (0.2 if t is None else (1.0 if int(c[0]) == t else 0.3)))
        return np.array(out, float)


def test_placeholder_must_beat_every_neighbour_hypothesis(phase):
    """c2 at a NOT-IN-RAW placeholder (FX-08): every hypothesis its neighbours offer must stay below none_thresh --
    the adjacent neighbour's time line extended AND its boundary RAW frame held (a freeze), and the neighbour across
    the placeholder too. A placeholder frame that IS the neighbour's last frame held fails although the extended
    line misses it."""
    segs = [seg(1, "raw", 0, 10, 100), seg(2, "not_in_raw", 10, 15), seg(3, "raw", 15, 25, 300)]
    truth = {k: (100 + k if k < 10 else (None if k < 15 else 300 + k - 15)) for k in range(25)}
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, SharpStubScorer(truth), Config())
    assert r["status"] == "pass", r["failures"]
    hyp = r["cuts"][0]["sides"][1]["hypotheses"]
    assert {(h["neighbour"], h["hypothesis"]) for h in hyp} == {(1, "line"), (1, "hold"), (3, "line"), (3, "hold")}
    held = dict(truth)
    held[10] = 109                 # the placeholder's first frame repeats A's last frame (a hold)
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, SharpStubScorer(held), Config())
    assert r["status"] == "fail" and r["cuts"][0]["status"] == "fail"
    far = dict(truth)
    far[10] = 300                  # ... or shows B's first frame (the neighbour across the placeholder)
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, SharpStubScorer(far), Config())
    assert r["cuts"][0]["status"] == "fail"


def test_uncertain_segments_fail_c3_and_claim_no_cut_or_audio(phase):
    """An 'uncertain' segment (FX-08) is covered (c1, labelled), its cuts check only the RAW side (c2), its frames are
    criterion-3 FAILURES (never exceptions), and c5 lists it as 'uncertain' without an exception."""
    u = seg(2, "uncertain", 10, 15, label="UNCERTAIN - best RAW 700-704, ZNCC 0.70-0.85", uncertain=True)
    u.audio = {**u.audio, "exception": "uncertain"}
    segs = [seg(1, "raw", 0, 10, 100), u, seg(3, "raw", 15, 60, 300)]
    assert verify.check_coverage(segs, 60)["status"] == "pass"
    nolabel = copy.deepcopy(segs)
    nolabel[1].label = ""
    assert verify.check_coverage(nolabel, 60)["status"] == "fail"
    truth = {k: (100 + k if k < 10 else (None if k < 15 else 300 + k - 15)) for k in range(60)}
    r = verify.check_cuts(segs, F30, F30, (64, 36), 1000, StubScorer(truth), Config())
    assert [c["kind"] for c in r["cuts"]] == ["raw_to_uncertain", "uncertain_to_raw"] and r["status"] == "pass"
    unc = verify.check_uncertain(segs)
    assert unc["status"] == "fail" and unc["frames"] == 5 and "UNCERTAIN" in unc["failures"][0]
    assert verify.check_uncertain([segs[0], segs[2]])["status"] == "pass"
    sr, y = _audio_setup()
    a = verify.check_audio(segs, y, y, sr, F30, {"status": "ok"}, [], Config(), xcorr=_xc(0.002, 0.95))
    row = next(x for x in a["segments"] if x["id"] == 2)
    assert row["result"] == "uncertain" and not any("S02" in e for e in a["exceptions"])


def test_ae_sim_frame_mix_compares_the_dominant_frame():
    """s9_2 on a Frame Mix layer (FX-08): AE shows (1 - f) RAW j + f RAW j + 1; refine's single-frame argmax on a
    frame-blended competitor frame is the heavier source, so it is compared with the mix's DOMINANT frame. The
    lighter source counts only near an even mix (verify_mix_tie, a listed blend tie); anything else is judged like
    any shown frame (a frame outside the mix is a mismatch)."""
    n = 40
    truth = [100 + k // 4 for k in range(n)]                         # refine's measured m(k)
    fm = frame_map(truth)
    # (shown floor frame - m, weight of floor + 1): a pure frame, m dominant at 0.25, an even mix, m dominant at 0.75
    kinds = [(0, 0.0), (0, 0.25), (0, 0.5), (-1, 0.75)]
    ents = {K: [{"layer": 1, "raw_frame": truth[K] + kinds[K % 4][0], "opacity": 100.0, "mix": kinds[K % 4][1]}]
            for K in range(n)}
    r = verify.check_ae_sim(ents, fm, F30, F30, n, [], Config())
    assert r["status"] == "pass_with_exceptions" and r["exact"] == 30 and len(r["frame_mix_tie"]) == 10, r["summary"]
    assert r["n_frame_mix"] == 30 and r["fraction_ok"] == 1.0 and "Frame Mix" in r["summary"]
    # the lighter source of a clear (0.75) mix is no tie: judged as the dominant frame -> a mismatch
    bad = {K: [dict(e[0])] for K, e in ents.items()}
    bad[3][0].update(raw_frame=truth[3], mix=0.75)                  # shows mostly truth + 1, m = truth
    r = verify.check_ae_sim(bad, fm, F30, F30, n, [], Config())
    assert [x["k"] for x in r["mismatches"]] == [3] and r["mismatches"][0]["ae"] == truth[3] + 1
