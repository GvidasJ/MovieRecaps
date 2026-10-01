"""FX-10 at pipeline level (DESIGN §2.1 / §7 D3 / §7.3): the exact AE floor-rule slack of every frame of a
layer, the cadence-pinned vs razor-edge classification, the whole-layer max-min-slack placement and the D3
margin in breakpoint cells -- on the first real run's own numbers (output/cutlist.json, decisions.jsonl:
competitor 30 fps, RAW 24000/1001)."""
from __future__ import annotations

import math
import types
from fractions import Fraction

import numpy as np
import pytest

from match_cuts import phase_solve as ps
from match_cuts import pipeline
from match_cuts.common import fmt_seconds, null_dlog
from match_cuts.config import Config
from match_cuts.model import FrameMap, Segment, Status

CF, RF = Fraction(30), Fraction(24000, 1001)
U = RF / CF                                          # 800/1001

# The first real run, output/cutlist.json: (id, comp_in, comp_out, speed, raw_in_seconds, raw_in_interval,
# raw_in_interval_both, ae_margin_ms as reported then = distance to the interval edges only)
REAL_RUN = [
    (1, 0, 39, 1.0, 6.600125, [6.599125, 6.607333333], None, 1.0),
    (18, 118, 130, 1.0, 18.593791667, [18.593708333, 18.593875], None, 0.083333),
    (19, 130, 160, 1.0, 21.297541667, [21.296541667, 21.30475], None, 1.0),
    (20, 160, 241, 1.0, 24.866625, [24.866541667, 24.866708333], None, 0.083333),
    (21, 241, 294, 1.0, 28.297666667, [28.296666667, 28.303375], None, 1.0),
    (26, 368, 460, 1.0, 35.095791668, [35.094791667, 35.101833333], None, 1.000001),
    (30, 559, 571, 1.0, 41.666708333, [41.666625, 41.666791667], None, 0.083333),
    (32, 592, 596, 1.0, 44.102458333, [44.094083333, 44.110833333], None, 8.375),
    (60, 1057, 1141, 1.0, 65.06675, [65.066666667, 65.066833333], None, 0.083333),
    (72, 1360, 1401, 1.0, 82.566666667, [82.566583333, 82.56675], None, 0.083333),
    (87, 1508, 1567, 1.0, 91.533291667, [91.533208333, 91.533375], None, 0.083333),
    (90, 1623, 1657, 1.0, 99.700083333, [99.7, 99.700166667], None, 0.083333),
    (92, 1706, 1742, 1.0, 124.533291667, [124.533208333, 124.533375], None, 0.083333),
    (97, 1768, 1774, 1.0, 126.751708333, [126.751625, 126.751791667], None, 0.083333),
]
# the run's warning: "9 segment(s) have a phase margin below 1 ms (S18, S20, S30, S60, S72, S87, S90, S92, S97)"
PINNED_9 = [18, 20, 30, 60, 72, 87, 90, 92, 97]


def _seg(row) -> Segment:
    sid, a, b, v, raw_in, iv, both, margin = row
    return Segment(sid, "raw", a, b, speed=v, raw_in_seconds=raw_in, raw_in_interval=list(iv),
                   raw_in_interval_both=both, ae_margin_ms=margin, notes="AE-rule-sensitive" if margin < 1 else "")


def test_real_run_cadence_pinned_segments_are_info_not_warnings():
    """The run's 9 'AE-rule-sensitive (0.083333 ms)' segments are cadence-slip cells of 4/1001 frame: the
    measured frames pin raw_in to +-2/1001 frame = +-0.083 ms -- information ('phase pinned by cadence'),
    not a warning. The exact per-frame slack instead finds the real razor edges the edge-only margin missed
    (S32 k594 and S26 k440 at ~0 slack, S19 with 1/3 ms after the 1-ms D3 clamp)."""
    segs = [_seg(r) for r in REAL_RUN]
    cfg = Config()
    info = {s.id: pipeline.phase_slack(s, CF, RF) for s in segs}
    cls = {i: pipeline.ae_phase_class(inf, cfg) for i, inf in info.items()}
    assert sorted(i for i, c in cls.items() if c == "pinned") == PINNED_9
    for i in PINNED_9:
        assert info[i]["slack_ms"] == pytest.approx(1 / 12, abs=1e-5)                # +-0.083 ms
        assert info[i]["cell_ms"] == pytest.approx(1 / 6, abs=1e-5)                  # a 4/1001-frame cell
        assert info[i]["video_pinned"] and info[i]["best"] == pytest.approx(2 / 1001, abs=1e-8)
    assert {i: c for i, c in cls.items() if i not in PINNED_9} == {1: "ok", 19: "razor", 21: "ok", 26: "razor",
                                                                     32: "razor"}
    assert info[32]["k"] == 594 and info[32]["slack_frames"] < 1e-7                   # 8e-9 frame
    assert info[26]["k"] == 440 and info[26]["slack_frames"] < 1e-7                   # 1.3 ns
    assert info[19]["slack_ms"] == pytest.approx(1 / 3, abs=1e-5)
    for i in (19, 26, 32):                                                            # a better phase existed
        assert info[i]["best"] >= cfg.ae_slack_tol_frames
    warns = pipeline.flag_ae_rule_sensitive(segs, cfg, CF, RF)
    assert len(warns) == 1 and warns[0].startswith("3 segment(s) have an AE floor-rule slack below 0.01 RAW frame")
    assert all(f"S{i:02d}" in warns[0] for i in (19, 26, 32)) and "exported frame-exact" in warns[0]
    for s in segs:
        flagged = s.id in (19, 26, 32)
        assert ("AE-rule-sensitive" in (s.notes or "")) == flagged, s.id               # stale notes cleared
        assert (f"S{s.id:02d}" in warns[0]) == flagged
    # the report: the 9 as one INFORMATION line, the real razor edges as the AE-rule-sensitive line
    from match_cuts import report
    lines = report.ae_phase_lines(types.SimpleNamespace(comp_fps=CF, raw_fps=RF, segments=segs), cfg)
    assert len(lines) == 2
    assert lines[0].startswith("- Phase pinned by cadence (information, not a risk): 9 segment(s) — S18 (±0.083 ms, "
                               "frames), S20 (±0.083 ms, frames)")
    assert all(f"S{i:02d} (±0.083 ms, frames)" in lines[0] for i in PINNED_9) and "24000/1001-in-30" in lines[0]
    assert lines[1].startswith("- AE-rule-sensitive segments") and "S19 (0.333334 ms at frame 158)" in lines[1]
    assert "S26 (0.000001 ms at frame 440)" in lines[1] and "S32 (0.000000 ms at frame 594)" in lines[1]
    assert not any(f"S{i:02d}" in lines[1] for i in PINNED_9)
    # forcing stretch mode takes away the frame-exact export: then the pinned phases are real risks too
    warns = pipeline.flag_ae_rule_sensitive(segs, Config(ae_time_mode="stretch"), CF, RF)
    assert len(warns) == 1 and warns[0].startswith("12 segment(s)") and "kept in stretch mode" in warns[0]


def test_real_run_whole_layer_placement_restores_slack_unless_pinned():
    """The pipeline's placement (midpoint of the max-min-slack cell of EVERY frame inside the solved
    interval): the razor segments get far more than the tolerance with their exact frames unchanged; the
    pinned ones stay where they are -- no raw_in of their interval has more slack."""
    cfg = Config()
    for row in REAL_RUN:
        s = _seg(row)
        p = ps.place_raw_in(s.raw_in_interval, s.comp_in, s.comp_out, 1.0, CF, RF)
        raw_in = fmt_seconds(p["raw_in"])
        slack, _k = ps.exact_min_slack(raw_in, 1.0, s.comp_in, s.comp_in, s.comp_out, CF, RF)
        if s.id in PINNED_9:
            assert raw_in == pytest.approx(s.raw_in_seconds, abs=1.5e-9)
            assert float(slack) == pytest.approx(2 / 1001, abs=5e-8)
        else:
            assert float(slack) >= cfg.ae_slack_tol_frames, s.id
        if s.id not in (19, 26, 32):           # (the razor ones may change a non-binding, ambiguous frame)
            old = [ps.ae_frame(s.raw_in_seconds, 1.0, k, s.comp_in, CF, RF) for k in range(s.comp_in, s.comp_out)]
            assert [ps.ae_frame(raw_in, 1.0, k, s.comp_in, CF, RF) for k in range(s.comp_in, s.comp_out)] == old


def _s26_segment() -> tuple[Segment, FrameMap, list[int], Fraction, list[int]]:
    """The real run's S26 (92 frames, 23.976 in 30): frame d0 = 42 (k410, RAW 875) binds the lower edge
    a = 842275/24000 s; frame 3 binds the upper edge a + 169/1001 frame (the run's 7.04 ms interval). The
    frames whose breakpoints fall inside -- d0 + 5, + 10, ..., + 45, every 4/1001 frame, incl. k440 = d0 + 30
    at a + 24/1001 frame = a + 1.000 ms -- are ambiguous (both RAW frames admissible)."""
    n, d0 = 92, 42
    a = Fraction(875) - U * d0
    amb = [d0 + 5 * i for i in range(1, 10)]
    x_true = a + Fraction(2, 1001)
    fm = FrameMap(n)
    fm.status = np.full(n, Status.MATCH, np.int8)
    frames = [math.floor(x_true + U * d) for d in range(n)]
    lo = np.asarray(frames, np.int32)
    hi = lo.copy()
    hi[amb] += 1
    fm.raw, fm.raw_lo, fm.raw_hi, fm.soft_lo, fm.soft_hi = lo.copy(), lo.copy(), hi.copy(), lo.copy(), hi.copy()
    s = Segment(26, "raw", 0, n, speed=1.0)
    pipeline.solve_segment_phase(s, fm, CF, RF, Config(), null_dlog())
    s.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
    return s, fm, frames, a, amb


def _d3(s: Segment, fm: FrameMap, target_s: float) -> tuple[list[int], list[str]]:
    res = {"segments": {s.id: {"lag_ms": (target_s - s.raw_in_seconds) * 1000.0, "corr": 0.95, "exception": None}},
           "status": "ok"}
    pipeline.apply_segment_audio([s], res)
    return pipeline.audio_informed_phase([s], res, fm, None, None, 16000, CF, RF, Config(), null_dlog())


@pytest.mark.parametrize("where", ["below_edge", "on_k440_breakpoint", "wide_cell"])
def test_d3_binding_frame_and_ambiguous_frame_30_later_s26_replica(where):
    """D3 (FX-10) on the real run's S26: the old clamp 'edge + 1.000 ms' put k440 (30 comp frames after the
    binding frame: 30u = 24 - 24/1001) 1.3 ns from a frame boundary. Placed in breakpoint cells of every
    frame, with a margin in cells, no frame of the layer is ever within 1e-6 s of a boundary: a target
    below the edge (within the 10 ms tolerance) or ON k440's breakpoint lands mid-cell in a 4/1001-frame
    cadence cell (pinned by the audio in-point -> info + frame-exact export), a target in the wide cell
    keeps >= ae_slack_tol_frames."""
    s, fm, frames, a, amb = _s26_segment()
    n = s.comp_out
    lo_s, hi_s = s.raw_in_interval
    assert lo_s == pytest.approx(float(a / RF), abs=1e-9) and hi_s - lo_s == pytest.approx(169 / 24000, abs=2e-9)
    assert float(a / RF) == pytest.approx(842275 / 24000, abs=1e-12)
    # the old D3 placement (1.000 ms above the binding edge) is a razor edge on k440
    sl, k = ps.exact_min_slack(fmt_seconds(lo_s + 0.001), 1.0, 0, 0, n, CF, RF)
    assert k == 72 and float(sl / RF) < 1e-9
    target = {"below_edge": lo_s - 0.003, "on_k440_breakpoint": float((a + Fraction(24, 1001)) / RF),
              "wide_cell": float((a + Fraction(40, 1001)) / RF)}[where]
    moved, warns = _d3(s, fm, target)
    assert not warns and s.audio["phase_source"] == "audio"
    sl, k = ps.exact_min_slack(s.raw_in_seconds, 1.0, 0, 0, n, CF, RF)
    assert float(sl / RF) > 1e-6                                                    # no frame near a boundary
    assert s.ae_margin_ms == pytest.approx(float(sl / RF) * 1000.0, abs=1e-6)
    info = pipeline.phase_slack(s, CF, RF)
    cfg = Config()
    if where == "wide_cell":
        assert float(sl) >= cfg.ae_slack_tol_frames and pipeline.ae_phase_class(info, cfg) == "ok"
        assert abs(s.raw_in_seconds - target) < 0.001
    else:
        assert float(sl) == pytest.approx(2 / 1001, abs=5e-8)                       # the middle of a slip cell
        assert pipeline.ae_phase_class(info, cfg) == "pinned" and not pipeline.ae_rule_sensitive(s, cfg, CF, RF)
    shown = [ps.ae_frame(s.raw_in_seconds, 1.0, kk, 0, CF, RF) for kk in range(n)]
    assert all(fm.raw_lo[kk] <= shown[kk] <= fm.raw_hi[kk] for kk in range(n))
    assert [j for kk, j in enumerate(shown) if kk not in amb] == [j for kk, j in enumerate(frames) if kk not in amb]


def test_pipeline_places_over_the_whole_layer_not_only_the_constraint_span():
    """phase_solve sees only the frames up to the last constraint; frames after it (unmatched tail) still
    get the slack: the pipeline re-places raw_in over every frame of [comp_in, comp_out)."""
    n, tail = 12, 6
    # exact frames on the first n frames of a v=1 23.976-in-30 layer whose tail is unmatched
    x_true = Fraction(5000) + Fraction(500, 1001)
    fm = FrameMap(n + tail)
    fm.status = np.full(n + tail, Status.MATCH, np.int8)
    fm.status[n:] = Status.NONE
    fr = np.asarray([math.floor(x_true + U * d) for d in range(n + tail)], np.int32)
    fm.raw = fm.raw_lo = fm.raw_hi = fm.soft_lo = fm.soft_hi = np.where(fm.status == Status.MATCH, fr, -1).astype(np.int32)
    s = Segment(1, "raw", 0, n + tail, speed=1.0)
    pipeline.solve_segment_phase(s, fm, CF, RF, Config(), null_dlog())
    sl_all, _ = ps.exact_min_slack(s.raw_in_seconds, 1.0, 0, 0, n + tail, CF, RF)
    p = ps.place_raw_in(s.raw_in_interval_both or s.raw_in_interval, 0, n + tail, 1.0, CF, RF,
                        round_rule=bool(s.raw_in_interval_both))
    assert float(sl_all) == pytest.approx(min(p["half"], 0.5), abs=1e-7)
    assert s.ae_margin_ms == pytest.approx(float(sl_all / RF) * 1000.0, abs=1e-6)
