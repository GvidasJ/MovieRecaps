"""Global competitor A/V offset (DESIGN §7 D9): interval-stabbing estimate, prior from the S5.1 windows,
residual lag searches, J/L cuts against the measured switch baseline, D3 on residuals.

The competitor audio of the analysis tests is built from RAW noise with an exact content offset ``lag``
(xcorr convention: the competitor plays RAW time raw_in + (t - t_in) + lag, so lag < 0 = its audio is LATE)
and audio switch points ``switch`` seconds after every picture cut (an offset applied to the finished mix
moves both: switch = -lag; one inside the source only the content: switch = 0).
"""
from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from match_cuts import audio_align as aa
from match_cuts import pipeline
from match_cuts.common import null_dlog
from match_cuts.config import Config
from match_cuts.model import AudioHints, Segment

SR = 16000
FPS = Fraction(30)


# ---------------------------------------------------------------------------------------------
# interval stabbing
# ---------------------------------------------------------------------------------------------

def test_stab_intervals_max_coverage_set():
    st = aa.stab_intervals([0.0, 1.0, 2.0, 10.0], [3.0, 4.0, 5.0, 11.0], [1, 1, 1, 5])
    assert st["best"] == 5 and st["set"] == [10.0, 11.0] and st["total"] == 8
    st = aa.stab_intervals([0.0, 1.0, 2.0], [3.0, 4.0, 5.0], [1, 1, 1])
    assert st["best"] == 3 and st["set"] == [2.0, 3.0]
    st = aa.stab_intervals([0.0, 3.0], [3.0, 4.0], [1, 1])          # closed intervals touch at 3
    assert st["best"] == 2 and st["set"] == [3.0, 3.0]
    assert aa.stab_intervals([], [], [])["set"] is None
    assert aa.coverage_at(2.5, [0.0, 1.0, 2.0], [3.0, 4.0, 5.0], [1, 2, 4]) == 7


def _fake_segments(rng, n, g_ms, width_ms=8.0, noise_ms=0.2):
    """n speed-1 segments: floor interval [lo, lo + width], true in-point at a random phase inside, the video
    raw_in at the interval centre, measured total lag = (true - video) + g + noise."""
    lo, hi, w = [], [], []
    for _ in range(n):
        a = rng.uniform(1.0, 100.0)
        b = a + width_ms / 1000.0
        true = rng.uniform(a, b)
        xv = 0.5 * (a + b)
        lag = (true - xv) + g_ms / 1000.0 + rng.normal(0.0, noise_ms / 1000.0)
        x_a = xv + lag
        lo.append(x_a - b - 0.0005)
        hi.append(x_a - a + 0.0005)
        w.append(rng.uniform(0.5, 2.0))
    return lo, hi, w


def test_solve_av_offset_random_phases_outliers_and_rejections():
    cfg = Config()
    rng = np.random.default_rng(11)
    lo, hi, w = _fake_segments(rng, 30, -86.0)
    sol = aa.solve_av_offset(lo, hi, w, cfg, audio_s=30.0)
    assert sol["status"] == "measured" and abs(sol["lag_s"] * 1000.0 + 86.0) <= 0.5, sol
    assert sol["interval_s"][0] <= -0.086 <= sol["interval_s"][1] and sol["coverage"] > 0.99
    # + 20 % outliers anywhere within +-100 ms
    olo, ohi, ow = list(lo), list(hi), list(w)
    for _ in range(6):
        c = rng.uniform(-0.1, 0.1)
        olo.append(c - 0.004)
        ohi.append(c + 0.004)
        ow.append(rng.uniform(0.5, 2.0))
    sol = aa.solve_av_offset(olo, ohi, ow, cfg, audio_s=36.0)
    assert sol["status"] == "measured" and abs(sol["lag_s"] * 1000.0 + 86.0) <= 1.0, sol
    # only 2 segments: not accepted -> 0 (today's per-segment behaviour)
    sol = aa.solve_av_offset(lo[:2], hi[:2], w[:2], cfg, audio_s=2.5)
    assert sol["status"] == "not_measured" and sol["lag_s"] == 0.0 and "2 segment(s)" in sol["reason"]
    # two disjoint, equally supported offsets: one segment decides where -> not accepted
    blo = [-0.0865, -0.0864, -0.0866, -0.0405, -0.0404, -0.0406]
    sol = aa.solve_av_offset(blo, [x + 0.001 for x in blo], [1, 1, 1.02, 1, 1, 1], cfg, audio_s=6.0)
    assert sol["status"] == "not_measured" and sol["lag_s"] == 0.0


def test_solve_av_offset_zero_exactly_when_zero_explains_the_segments():
    """D8-like input: every segment's audio in-point on its floor interval's lower bound, no offset -> the
    offset intervals [lo - hi, 0] all contain 0 -> g = 0 EXACTLY (the zero-offset run behaves as before)."""
    cfg = Config()
    rng = np.random.default_rng(3)
    lo, hi, w = [], [], []
    for _ in range(12):
        width = rng.uniform(0.01, 0.0334)
        lo.append(-width - 0.0005)
        hi.append(rng.normal(0.0, 0.0001) + 0.0005)
        w.append(1.0)
    sol = aa.solve_av_offset(lo, hi, w, cfg, audio_s=12.0)
    assert sol["status"] == "zero" and sol["lag_s"] == 0.0
    # a tiny real offset (< av_offset_min_ms) is not published either
    sol = aa.solve_av_offset([0.0005, 0.0006, 0.0007], [0.0015, 0.0016, 0.0017], [1, 1, 1], cfg, audio_s=3.0)
    assert sol["status"] == "not_measured" and sol["lag_s"] == 0.0 and "below the minimum" in sol["reason"]


# The 34 strongly correlated segments of the first real run (decisions.jsonl, phase_solve audio_phase):
# (segment, floor raw_in interval lo, hi, video raw_in, first-pass lag ms, corr, audio seconds)
REAL_RUN = [
    (1, 6.599125000, 6.607333333, 6.603229167, -90.042, 0.9953, 1.3),
    (15, 17.292875000, 17.300750000, 17.296812500, -83.454, 0.9436, 0.6667),
    (16, 18.026541667, 18.034750000, 18.030645833, -83.938, 0.9364, 0.5),
    (19, 21.296541667, 21.304750000, 21.300645833, -87.271, 0.9838, 1.0334),
    (21, 28.296666667, 28.303375000, 28.300020833, -86.542, 0.9658, 1.8),
    (22, 30.932000000, 30.939375000, 30.935687500, -88.875, 0.9739, 1.2),
    (23, 33.625458333, 33.633666667, 33.629562500, -82.752, 0.9263, 0.5),
    (25, 34.459958333, 34.467833333, 34.463895833, -83.751, 0.9676, 0.6),
    (26, 35.094791667, 35.101833333, 35.098312500, -84.834, 0.9898, 3.0666),
    (27, 38.164791667, 38.171500000, 38.168145833, -88.001, 0.9895, 1.7667),
    (28, 39.931958333, 39.940000000, 39.935979167, -89.168, 0.9901, 0.5),
    (29, 40.566458333, 40.574000000, 40.570229167, -90.084, 0.9873, 1.0333),
    (31, 43.560750000, 43.568625000, 43.564687500, -84.543, 0.9465, 0.7333),
    (39, 45.595916667, 45.603958333, 45.599937500, -86.458, 0.9386, 0.5),
    (51, 47.165250000, 47.172125000, 47.168687500, -88.542, 0.9626, 1.6667),
    (52, 52.694875000, 52.702750000, 52.698812500, -84.897, 0.9753, 0.7),
    (53, 53.429708333, 53.436750000, 53.433229167, -85.982, 0.9774, 1.5),
    (54, 54.931708333, 54.938250000, 54.934979167, -87.730, 0.9771, 2.2),
    (55, 57.892833333, 57.900208333, 57.896520833, -82.613, 0.9852, 1.5333),
    (56, 59.627666667, 59.634708333, 59.631187500, -83.937, 0.9371, 1.6),
    (57, 61.228666667, 61.236208333, 61.232437500, -85.188, 0.9707, 0.9667),
    (58, 62.196833333, 62.203875000, 62.200354167, -86.438, 0.9830, 1.5333),
    (59, 63.731500000, 63.738708333, 63.735104167, -87.855, 0.9770, 1.3333),
    (61, 67.860625000, 67.867833333, 67.864229167, -83.645, 0.9368, 1.3667),
    (68, 70.297291667, 70.303666667, 70.300479167, -86.563, 0.9420, 2.1667),
    (69, 73.599291667, 73.607000000, 73.603145833, -89.230, 0.9915, 0.9333),
    (70, 80.597583333, 80.605625000, 80.601604167, -87.250, 0.9605, 0.5),
    (71, 81.132416667, 81.139458333, 81.135937500, -88.250, 0.9740, 1.4333),
    (73, 84.693208333, 84.701416667, 84.697312500, -82.974, 0.9767, 0.6334),
    (86, 90.099708333, 90.106750000, 90.103229167, -88.875, 0.9764, 1.3667),
    (88, 95.296333333, 95.303541667, 95.299937500, -85.584, 0.9722, 1.2666),
    (89, 97.164333333, 97.172208333, 97.168270833, -87.251, 0.9650, 0.6334),
    (91, 109.193750000, 109.200791667, 109.197270833, -82.479, 0.9704, 1.7),
    (93, 125.726000000, 125.734041667, 125.730020833, -81.688, 0.9801, 0.5),
]


def _real_run_segments() -> tuple[list[Segment], dict]:
    segs, meas = [], {}
    for i, (sid, lo, hi, xv, lag, corr, dur) in enumerate(REAL_RUN):
        s = Segment(sid, "raw", 100 * i, 100 * i + 40, raw_in_seconds=xv, speed=1.0, raw_in_interval=[lo, hi])
        s.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
        segs.append(s)
        meas[sid] = {"lag_total_ms": lag, "dur_s": dur, "corr": corr}
    return segs, {"_measured": meas}


def test_real_run_regression_fixture():
    """The first real run's 34 audio_phase records: one constant offset explains every segment; the
    published interval is the max-coverage set 85.8-86.2 ms (competitor audio LATE), coverage 34/34."""
    segs, res = _real_run_segments()
    pub = aa.av_offset_estimate(segs, res, Config(), null_dlog(), prior={"lag_s": -0.0855, "accepted": True})
    assert pub["status"] == "measured" and pub["n_segments"] == 34 and pub["coverage"] == pytest.approx(1.0)
    lo, hi = pub["lag_ms_interval"]
    assert -86.25 <= lo <= hi <= -85.75 and hi - lo < 0.5
    assert -86.2 <= pub["lag_ms"] <= -85.8 and pub["lag_s"] == pytest.approx(pub["lag_ms"] / 1000.0, abs=1e-6)
    assert pub["text"] == "competitor audio is 86.0 ms later than its picture, relative to RAW's own A/V sync"
    assert pub["prior"]["lag_ms"] == -85.5 and pub["loo_distance_ms"] <= 2.0
    # the residual of every segment after the offset lands inside its floor interval (D3 can use it)
    g = pub["lag_s"]
    for sid, lo_, hi_, xv, lag, corr, dur in REAL_RUN:
        target = xv + (lag / 1000.0 - g)
        assert lo_ - 0.0006 <= target <= hi_ + 0.0006, sid


def test_av_offset_text_sign():
    assert "86.0 ms later" in aa.av_offset_text(-86.0)
    assert "12.5 ms earlier" in aa.av_offset_text(12.5)
    assert "in sync" in aa.av_offset_text(0.0)


# ---------------------------------------------------------------------------------------------
# prior from the S5.1 windows
# ---------------------------------------------------------------------------------------------

def _hints_for(segs: list[Segment], g: float, wave: float = 0.95, every: float = 0.25) -> AudioHints:
    ct, rt = [], []
    for s in segs:
        t0, t1 = s.comp_in / 30.0, s.comp_out / 30.0
        t = t0 + 0.5
        while t <= t1 - 0.5:
            ct.append(t)
            rt.append(float(s.raw_in_seconds) + (t - t0) + g)
            t += every
    n = len(ct)
    return AudioHints(np.array(ct), np.array(rt), np.ones(n), np.full(n, 3.0, np.float32), np.full(n, 5.0, np.float32),
                      np.full(n, 0.9, np.float32), 1.0, 0.25, np.full(n, wave, np.float32))


def test_prior_from_coarse_windows():
    segs = [Segment(i + 1, "raw", 60 * i, 60 * i + 60, raw_in_seconds=10.0 * (i + 1), speed=1.0,
                    raw_in_interval=[10.0 * (i + 1) - 0.004, 10.0 * (i + 1) + 0.004]) for i in range(4)]
    pr = aa.av_offset_prior(_hints_for(segs, -0.150), segs, FPS, Config())
    assert pr["accepted"] and pr["n_windows"] >= 8 and abs(pr["lag_s"] + 0.150) < 0.0006
    # windows that 0 explains (in-point anywhere in the interval) -> exactly 0
    pr = aa.av_offset_prior(_hints_for(segs, -0.002), segs, FPS, Config())
    assert pr["accepted"] and pr["lag_s"] == 0.0
    # hints without waveform refinement (or an old cache): no prior
    pr = aa.av_offset_prior(_hints_for(segs, -0.150, wave=float("nan")), segs, FPS, Config())
    assert not pr["accepted"] and pr["lag_s"] == 0.0 and pr["n_windows"] == 0
    assert not aa.av_offset_prior(None, segs, FPS, Config())["accepted"]


# ---------------------------------------------------------------------------------------------
# per-segment analysis around the offset
# ---------------------------------------------------------------------------------------------

def _raw_noise(seconds: float = 40.0, seed: int = 5) -> np.ndarray:
    return (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.1).astype(np.float32)


def _competitor(raw: np.ndarray, layout: list[tuple[int, int, float]], lag: float, switch: float,
                jl: dict[int, int] | None = None) -> np.ndarray:
    """Competitor audio of an edit: segment k plays RAW time raw_in_k + (t - t_in_k) + lag from its picture
    cut + switch (+ jl[k] frames) to the next one's."""
    n_frames = layout[-1][1]
    n = int(round(n_frames / 30.0 * SR))
    t = np.arange(n) / SR
    starts = [ci / 30.0 + switch + (jl or {}).get(k, 0) / 30.0 for k, (ci, _co, _r) in enumerate(layout)]
    starts[0] = 0.0
    ends = starts[1:] + [1e9]
    out = np.zeros(n, np.float32)
    for (ci, _co, r), t0, t1 in zip(layout, starts, ends):
        m = (t >= t0) & (t < t1)
        out[m] = aa.resample_at(raw, (r + (t[m] - ci / 30.0) + lag) * SR)
    return out


def _segments(layout: list[tuple[int, int, float]]) -> list[Segment]:
    segs = []
    for k, (ci, co, r) in enumerate(layout):
        s = Segment(k + 1, "raw", ci, co, raw_in_seconds=r, speed=1.0, raw_in_interval=[r - 0.004, r + 0.004])
        s.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
        segs.append(s)
    return segs


# cuts at 30, 60, 90, 94 (a 4-frame segment), 124: three cuts between long segments (30, 60, 124)
LAYOUT = [(0, 30, 2.0), (30, 60, 9.0), (60, 90, 15.0), (90, 94, 21.0), (94, 124, 27.0), (124, 154, 33.0)]


def test_residual_search_reaches_a_4_frame_segment_and_no_fake_jl():
    """An 86 ms offset of the whole mix (content AND switches): today's +-100 ms search around 0 is capped at
    half the range (67 ms for a 4-frame segment) and every cut looks like a +2..+3 frame L-cut. Pre-shifted
    by the offset, the residual of every segment is ~0, the switch baseline is +86 ms and no cut is J/L."""
    raw = _raw_noise()
    comp = _competitor(raw, LAYOUT, -0.086, 0.086)
    segs = _segments(LAYOUT)
    res = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog(), av_offset_s=-0.086)
    per = res["segments"]
    for s in segs:
        assert per[s.id]["corr"] >= 0.95 and abs(per[s.id]["lag_ms"]) < 1.0, (s.id, per[s.id])
        assert per[s.id]["in_offset_frames"] == 0 and per[s.id]["out_offset_frames"] == 0
    assert res["cuts"] == [] and not any("-cut" in n for n in res["notes"])
    assert res["_switch_baseline"]["ms"] == pytest.approx(86.0, abs=4.0) and res["_switch_baseline"]["n"] == 3
    assert res["_measured"][4]["lag_total_ms"] == pytest.approx(-86.0, abs=1.0)
    # searched around 0 (no offset model): the 4-frame segment cannot reach -86 ms (half-range cap); the
    # switch baseline alone already keeps the uniform switch delay from becoming L-cuts
    old = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog())
    assert old["segments"][4]["lag_ms"] is None or old["segments"][4]["lag_ms"] > -70.0
    assert old["_switch_baseline"]["ms"] == pytest.approx(86.0, abs=4.0) and old["cuts"] == []


def test_wide_probe_finds_offsets_beyond_the_residual_search():
    """No S5.1 prior (too few long windows): one wide per-segment search finds a 150 ms offset (beyond the
    +-100 ms residual search), and around it every residual is ~0; a synced competitor probes to exactly 0."""
    raw = _raw_noise()
    segs = _segments(LAYOUT)
    comp = _competitor(raw, LAYOUT, -0.150, 0.150)
    pr = aa.av_offset_probe(segs, comp, raw, SR, FPS, Config(), null_dlog())
    assert pr["accepted"] and pr["source"] == "probe" and abs(pr["lag_s"] + 0.150) < 0.0006, pr
    res = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog(), av_offset_s=pr["lag_s"])
    assert all(abs(v["lag_ms"]) < 1.0 and v["corr"] > 0.95 for v in res["segments"].values()), res["segments"]
    assert res["cuts"] == []
    pr = aa.av_offset_probe(segs, _competitor(raw, LAYOUT, 0.0, 0.0), raw, SR, FPS, Config(), null_dlog())
    assert pr["accepted"] and pr["lag_s"] == 0.0


@pytest.mark.parametrize("kind", ["post_edit", "pre_edit"])
def test_jl_against_the_switch_baseline(kind):
    """48 ms offset applied after the edit (switches move: baseline ~ +48 ms) or inside the source (switches
    stay at the cuts: baseline 0): no J/L either way; one injected 6-frame L-cut is found exactly (+6), a
    genuine 1-frame L-cut too."""
    raw = _raw_noise()
    lag, switch = -0.048, (0.048 if kind == "post_edit" else 0.0)
    segs = _segments(LAYOUT)
    res = aa.analyze_segments_audio(segs, _competitor(raw, LAYOUT, lag, switch), raw, SR, FPS, Config(), null_dlog(),
                                    av_offset_s=lag)
    assert res["cuts"] == []
    want_b = 48.0 if kind == "post_edit" else 0.0
    assert res["_switch_baseline"]["ms"] == pytest.approx(want_b, abs=4.0)
    for frames, cut_index in ((6, 4), (1, 2)):        # L-cut at the cut into S05 (comp 94) / into S03 (comp 60)
        comp = _competitor(raw, LAYOUT, lag, switch, jl={cut_index: frames})
        res = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog(), av_offset_s=lag)
        assert [(c["cut"], c["offset_frames"]) for c in res["cuts"]] == [(LAYOUT[cut_index][0], frames)], res["cuts"]
        per = res["segments"]
        assert per[cut_index]["out_offset_frames"] == frames and per[cut_index + 1]["in_offset_frames"] == frames


def test_jl_ranges_stay_ordered():
    """A short segment between an L-cut at its in-point and a J-cut at its out-point: the switches are
    chosen so its audio range keeps a0 < a1 (the real run's inverted S36 [613, 612))."""
    raw = _raw_noise()
    layout = [(0, 30, 2.0), (30, 36, 9.0), (36, 66, 15.0)]
    comp = _competitor(raw, layout, 0.0, 0.0, jl={1: 4, 2: -4})      # B's audio starts late, C's early
    segs = _segments(layout)
    res = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog())
    b = res["segments"][2]
    assert 30 + b["in_offset_frames"] < 36 + b["out_offset_frames"], b


def test_zero_offset_analysis_is_unchanged():
    """av_offset_s = 0 is today's analysis exactly (same windows, renders and searches)."""
    raw = _raw_noise()
    comp = _competitor(raw, LAYOUT, 0.0, 0.0)
    segs = _segments(LAYOUT)
    a = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog())
    b = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), null_dlog(), av_offset_s=0.0, pass_name="x")
    assert a["segments"] == b["segments"] and a["cuts"] == b["cuts"] and a["_switch_baseline"]["ms"] in (0.0, None)
    assert all(abs(v["lag_ms"]) < 0.5 for v in a["segments"].values())


# ---------------------------------------------------------------------------------------------
# D3 on residuals (pipeline.audio_informed_phase + rebase_segment_lags)
# ---------------------------------------------------------------------------------------------

def _d3_segment() -> tuple[Segment, object]:
    """A 24p-in-30 style segment: floor interval 8 ms wide, video raw_in at its centre."""
    from match_cuts.model import FrameMap, Status
    fm = FrameMap(30)
    fm.status = np.full(30, Status.MATCH, np.int8)
    fm.raw = fm.raw_lo = fm.raw_hi = fm.soft_lo = fm.soft_hi = np.arange(200, 230, dtype=np.int32)
    s = Segment(1, "raw", 0, 30, raw_in_seconds=10.004, speed=1.0, raw_in_interval=[10.0, 10.008], time_mode="stretch")
    s.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
    return s, fm


def test_d3_moves_by_the_residual_after_the_offset():
    """First pass around 0: lag -89 ms (offset -86 + residual -3): rebased on the offset the target is 3 ms
    earlier, inside the 8 ms interval -> moved, no warning. Without the offset the target lies 89 ms outside
    -> raw_in is NOT moved (one run-level warning instead of clamping to the edge)."""
    from match_cuts import phase_solve
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline, "preserved_frames_interval", lambda *a, **k: (-1e9, 1e9, 30))
        s, fm = _d3_segment()
        res = {"segments": {1: {"lag_ms": -89.0, "corr": 0.97, "exception": None}}, "status": "ok"}
        pipeline.apply_segment_audio([s], res)
        pipeline.rebase_segment_lags([s], res, (0.0 - (-0.086)) * 1000.0)
        assert s.audio["lag_ms"] == pytest.approx(-3.0) and res["segments"][1]["lag_ms"] == pytest.approx(-3.0)
        moved, warns = pipeline.audio_informed_phase([s], res, fm, None, None, SR, FPS, FPS, Config(), null_dlog(),
                                                     phase=phase_solve, av_offset_s=-0.086)
        assert moved == [1] and warns == [] and s.audio["phase_source"] == "audio"
        assert s.raw_in_seconds == pytest.approx(10.001, abs=3e-9)          # the target (1 ms inside: the margin)
        s, fm = _d3_segment()
        res = {"segments": {1: {"lag_ms": -89.0, "corr": 0.97, "exception": None}}, "status": "ok"}
        pipeline.apply_segment_audio([s], res)
        moved, warns = pipeline.audio_informed_phase([s], res, fm, None, None, SR, FPS, FPS, Config(), null_dlog(),
                                                     phase=phase_solve)
        assert moved == [] and s.raw_in_seconds == 10.004 and s.audio["phase_source"] == "video"
        assert len(warns) == 1 and "S01 -85.0 ms" in warns[0] and "audio implies raw_in" not in warns[0]


def test_published_block_and_cutlist_fields():
    segs, res = _real_run_segments()
    pub = aa.av_offset_estimate(segs, res, Config(), null_dlog())
    block = pipeline.published_av_offset(pub, {"_switch_baseline": {"ms": 48.0, "n": 16, "spread_ms": 9.0}}, Config())
    assert "lag_s" not in block and block["sync_mode"] == "raw" and block["switch_baseline_ms"] == 48.0
    assert block["switch_baseline"] == {"n": 16, "spread_ms": 9.0} and block["status"] == "measured"
