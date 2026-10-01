"""Unit tests for phase_solve.py (DESIGN §2.1 / §5): exact-Fraction simulations of ffmpeg's frame
selection (setpts=(PTS-STARTPTS)/v with double arithmetic + truncation, then fps=<out> with round=near)
on common source rates, the AE floor rule, speed ranges, snapping, and the segment DP on 1-frame skips."""
from __future__ import annotations

import math
from fractions import Fraction as F

import numpy as np
import pytest

from match_cuts import phase_solve as ps
from match_cuts.config import Config
from match_cuts.model import FrameMap, Proxy, Status

C30 = F(30)
R2997 = F(30000, 1001)


def ff_select(n_out: int, tb: F, step: int, v: float, out_fps: F, j0: int = 0) -> np.ndarray:
    """RAW frame shown at each output frame of `-ss <j0> ... setpts=(PTS-STARTPTS)/v,fps=out_fps`.

    Source frame i (relative to j0) has pts i*step in time base tb. setpts evaluates in double and
    truncates to int64 (D2TS); fps rescales to 1/out_fps with AV_ROUND_NEAR_INF and outputs, for every
    output timestamp n, the latest input frame whose rescaled pts <= n.
    """
    def ts(i: int) -> int:
        p = int(float(i * step) / v) if v != 1.0 else i * step
        num = p * tb.numerator * out_fps.numerator
        den = tb.denominator * out_fps.denominator
        return (2 * num + den) // (2 * den)       # round half away from zero (p >= 0)

    out, i = [], 0
    for n in range(n_out):
        while ts(i + 1) <= n:
            i += 1
        out.append(j0 + i)
    return np.asarray(out, dtype=np.int64)


def exact_select(n_out: int, raw_fps: F, v: F, out_fps: F, j0: int = 0) -> np.ndarray:
    """Same selection with exact rational timestamps (no setpts truncation)."""
    out, i = [], 0

    def ts(i: int) -> int:
        x = F(i) / raw_fps / v * out_fps
        return int((x + F(1, 2)).__floor__())

    for n in range(n_out):
        while ts(i + 1) <= n:
            i += 1
        out.append(j0 + i)
    return np.asarray(out, dtype=np.int64)


# (label, raw fps, time base, pts step, v)
CASES = [
    ("29.97->30 v1.0", R2997, F(1, 30000), 1001, 1.0),
    ("29.97->30 v1.1", R2997, F(1, 30000), 1001, 1.1),
    ("23.976->30 v1.0", F(24000, 1001), F(1, 24000), 1001, 1.0),
    ("25->30 v1.0", F(25), F(1, 12800), 512, 1.0),
    ("60->30 v1.0", F(60), F(1, 15360), 256, 1.0),
    ("29.97->30 v0.9091", R2997, F(1, 30000), 1001, 1 / 1.1),
]


# ---------------------------------------------------------------------------------------------------
# the simulator itself
# ---------------------------------------------------------------------------------------------------

def test_simulator_reproduces_measured_tie_count():
    """The design review measured 15 frames (of 2728) at v=1.1 that differ from the exact-rational rule
    because setpts truncates exact .5 ties; the simulation must reproduce that."""
    m = ff_select(2728, F(1, 30000), 1001, 1.1, C30)
    e = exact_select(2728, R2997, F(11, 10), C30)
    assert int((m != e).sum()) == 15
    m1 = ff_select(3000, F(1, 30000), 1001, 1.0, C30)
    assert np.array_equal(m1, exact_select(3000, R2997, F(1), C30))
    assert int((np.diff(m1) == 0).sum()) == 3        # 3 duplicates in 3000 frames (review)


# ---------------------------------------------------------------------------------------------------
# solve_raw_in / feasible_speed_range on simulated segments
# ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("label,rf,tb,step,v", CASES)
@pytest.mark.parametrize("j0,n,comp_in", [(0, 900, 0), (123457, 400, 5000)])
def test_solve_reproduces_every_frame(label, rf, tb, step, v, j0, n, comp_in):
    m = ff_select(n, tb, step, v, C30, j0)
    ks = np.arange(comp_in, comp_in + n)
    vr = ps.feasible_speed_range(ks, m, m, comp_in, C30, rf)
    assert vr is not None, label
    assert vr[0] - 1e-9 <= v <= vr[1] + 1e-9, (label, vr)
    sol = ps.solve_raw_in(ks, m, m, comp_in, v, C30, rf)
    assert sol["ok"], label
    pred = ps.ae_frame(sol["raw_in"], v, ks, comp_in, C30, rf)
    bad = set(ks[pred != m].tolist())
    assert bad <= set(sol["tie_frames"]), (label, sorted(bad)[:10])
    # ties are rare (< 1 %) and only exist where setpts rounding is involved
    assert len(sol["tie_frames"]) <= 0.03 * n
    if v == 1.0:
        assert not bad
    # the interval contains raw_in and has the reported margin
    a, b = sol["interval_floor"]
    assert a - 1e-12 <= sol["raw_in"] <= b + 1e-12
    assert sol["margin_ms"] == pytest.approx(min(sol["raw_in"] - a, b - sol["raw_in"]) * 1000, abs=1e-6)


def test_tie_frames_at_v110_are_listed_and_ae_rule_matches_elsewhere():
    m = ff_select(2728, F(1, 30000), 1001, 1.1, C30)
    ks = np.arange(m.size)
    sol = ps.solve_raw_in(ks, m, m, 0, 1.1, C30, R2997)
    assert sol["ok"] and sol["slack"] < ps.TIE_SLACK
    pred = ps.ae_frame(sol["raw_in"], 1.1, ks, 0, C30, R2997)
    bad = np.nonzero(pred != m)[0]
    assert 0 < bad.size <= 15
    assert set(bad.tolist()) <= set(sol["tie_frames"])
    assert np.all(np.abs(pred - m) <= 1)
    # the strict (non-tolerant) half-open model would be infeasible here: the tolerant one is not
    assert ps.is_feasible(ks, m, m, 0, C30, R2997, v=1.1)
    assert not ps.is_feasible(ks, m, m, 0, C30, R2997, v=1.1, tau=-1e-7)


def test_round_rule_overlap_used_when_non_empty():
    # same fps (30 in 30), v=1: floor interval [j, j+1), round interval [j-0.5, j+0.5) -> overlap [j, j+0.5)
    ks = np.arange(100)
    m = 1000 + ks
    sol = ps.solve_raw_in(ks, m, m, 0, 1.0, C30, C30)
    assert sol["used_both"] and sol["interval_both"] is not None
    assert sol["raw_in"] * 30 == pytest.approx(1000.25, abs=1e-9)
    for rule in ("floor", "round"):
        assert np.array_equal(ps.ae_frame(sol["raw_in"], 1.0, ks, 0, C30, C30, rule=rule), m)
    # a freeze: u = 0, lo = hi = j  ->  (j + 0.25) / raw_fps (DESIGN freeze key value)
    sol0 = ps.solve_raw_in(ks, np.full(100, 77), np.full(100, 77), 0, 0.0, C30, R2997)
    assert sol0["raw_in"] == pytest.approx((77 + 0.25) / float(R2997), abs=1e-12)


def test_v110_long_segment_has_no_round_overlap_and_small_margin():
    m = ff_select(90, F(1, 30000), 1001, 1.1, C30, 2000)
    ks = np.arange(90)
    sol = ps.solve_raw_in(ks, m, m, 0, 1.1, C30, R2997)
    assert sol["ok"]
    assert sol["margin_ms"] < 5.0           # review: 0.37-2.2 ms wide at N=45-90


def test_chebyshev_closed_form_equals_linprog():
    from scipy.optimize import linprog
    rng = np.random.default_rng(3)
    for _ in range(30):
        n = int(rng.integers(2, 60))
        d = np.sort(rng.choice(200, n, replace=False)).astype(float)
        u = float(rng.uniform(0.5, 1.5))
        x0 = float(rng.uniform(0, 1))
        lo = np.floor(x0 + u * d + rng.normal(0, 0.05, n))
        hi = lo + rng.integers(0, 2, n)
        x, t, lmax, umin = ps.chebyshev_x(d, lo, hi, u)
        # max t s.t. lo + t <= x + u d <= hi + 1 - t, t <= 0.5  (variables x, t)
        A = np.r_[np.c_[-np.ones(n), np.ones(n)], np.c_[np.ones(n), np.ones(n)]]
        b = np.r_[-(lo - u * d), hi + 1 - u * d]
        r = linprog([0, -1], A_ub=A, b_ub=b, bounds=[(None, None), (None, 0.5)], method="highs")
        assert r.status == 0
        assert t == pytest.approx(r.x[1], abs=1e-7)


def test_feasible_range_infeasible_and_single_frame():
    ks = np.arange(10)
    m = np.r_[np.arange(5), np.arange(5) + 50]           # a jump of 45 frames
    assert ps.feasible_speed_range(ks, m, m, 0, C30, R2997) is None or \
        ps.feasible_speed_range(ks, m, m, 0, C30, R2997)[0] > 1.5
    assert ps.feasible_speed_range([7], [100], [100], 7, C30, R2997) == pytest.approx((-8, 8))
    with pytest.raises(ValueError):
        ps.solve_raw_in([1, 2], [5, 6], [4, 6], 1, 1.0, C30, R2997)


def test_ae_frame_rules():
    rf = R2997
    assert ps.ae_frame(10 / float(rf), 1.0, 0, 0, C30, rf) == 10
    assert ps.ae_frame((10 - 1e-12) / float(rf), 1.0, 0, 0, C30, rf) == 10     # 1e-9 epsilon
    assert ps.ae_frame(10.6 / float(rf), 1.0, 0, 0, C30, rf, rule="round") == 11
    arr = ps.ae_frame(0.0, 1.0, np.arange(4), 0, C30, C30)
    assert arr.tolist() == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        ps.ae_frame(0.0, 1.0, 0, 0, C30, C30, rule="nearest")


# ---------------------------------------------------------------------------------------------------
# snapping
# ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("L", [15, 20, 25, 30])
def test_short_110_segments_snap_to_110_all_phases(L):
    cfg = Config()
    m = ff_select(1200, F(1, 30000), 1001, 1.1, C30, 700)
    kk = np.arange(L)
    # every 2nd start: the phase advances by ~0.2 frame per step, so ~585 windows cover all phases
    for s in range(0, 1200 - L, 2):
        w = m[s:s + L]
        vr = ps.feasible_speed_range(kk, w, w, 0, C30, R2997)
        assert vr is not None and vr[0] <= 1.1 <= vr[1] + 1e-9
        vo = ps.estimate_speed(kk, w, w, C30, R2997)
        for pref in ((), [1.0, 1.0, 1.0]):
            v, uns = ps.snap_speed(vo, vr, cfg, preferred=pref)
            assert not uns and v == pytest.approx(1.1), (L, s, vr, vo, v)


def test_snap_preferences():
    cfg = Config()
    # dominant speed of the edit wins when inside the range
    assert ps.snap_speed(1.04, (0.98, 1.12), cfg, preferred=[1.1, 1.1, 1.0]) == (1.1, False)
    # then 1.0
    assert ps.snap_speed(1.04, (0.98, 1.12), cfg) == (1.0, False)
    # then the candidate closest to v_ols (never the LP centre)
    assert ps.snap_speed(1.13, (1.02, 1.16), cfg) == (pytest.approx(1.15), False)
    # nothing inside -> clipped v_ols, unsnapped
    v, uns = ps.snap_speed(1.33, (1.31, 1.32), cfg)
    assert uns and v == pytest.approx(1.32)
    # preferred speeds of solved segments are candidates too
    assert ps.snap_speed(1.337, (1.335, 1.34), cfg, preferred={1.3372: 50}) == (pytest.approx(1.3372), False)
    assert ps.dominant_speed([(1.0, 10), (1.1, 30)]) == pytest.approx(1.1)
    assert ps.dominant_speed([]) is None


def test_snap_speed_tolerance_against_measured_frames():
    """time-math F2 / prompt 5.4 'snap only if within 0.3 % and the residuals don't get worse': a soft range
    that merely CONTAINS 1.0 / 1.05 never licenses a snap when the measured frames' exact range excludes them
    and the robust slope is further than speed_snap_tol away."""
    cfg = Config()
    assert cfg.speed_snap_tol == pytest.approx(0.003)
    # 1.03x on slow footage: soft range [0.989, 1.06] contains 1.0 and 1.05; the measured frames do not
    v, uns = ps.snap_speed(1.0303, (0.989, 1.06), cfg, preferred={1.0: 1e9}, exact_range=(1.0296, 1.0304))
    assert uns and v == pytest.approx(1.0303)
    # within 0.3 % of the measured slope: snapped although just outside the exact range
    assert ps.snap_speed(1.0985, (1.05, 1.15), cfg, exact_range=(1.0980, 1.0990)) == (pytest.approx(1.1), False)
    # inside the exact range (the residuals do not get worse): snapped even when v_ols is imprecise
    assert ps.snap_speed(1.12, (1.05, 1.15), cfg, exact_range=(1.08, 1.13)) == (pytest.approx(1.1), False)
    # no exact range: the old behaviour (every snap inside the range qualifies)
    assert ps.snap_speed(1.04, (0.98, 1.12), cfg) == (1.0, False)
    # unsnapped result is clipped into the exact range
    v, uns = ps.snap_speed(1.045, (1.0, 1.3), cfg, exact_range=(1.036, 1.038), preferred=[])
    assert uns and v == pytest.approx(1.038)


def test_solve_raw_in_prefers_measured_frames_inside_asymmetric_soft_ranges():
    """time-math F2 (c): soft range [m - 1, m] on every frame (slow footage, the previous frame scores within
    delta). Centring the soft intersection shows m - 1 on EVERY frame; with the measured (argmax) range as the
    preference the phase reproduces every measured frame, and the interval / margin refer to that phase."""
    m = ff_select(40, F(1, 30000), 1001, 1.0, C30, 500)
    ks = np.arange(40)
    old = ps.solve_raw_in(ks, m - 1, m, 0, 1.0, C30, R2997)
    assert np.all(ps.ae_frame(old["raw_in"], 1.0, ks, 0, C30, R2997) == m - 1)
    sol = ps.solve_raw_in(ks, m - 1, m, 0, 1.0, C30, R2997, prefer=(m, m))
    assert sol["ok"] and sol["data_cost"] == 0.0
    assert np.array_equal(ps.ae_frame(sol["raw_in"], 1.0, ks, 0, C30, R2997), m)
    a, b = sol["interval_floor"]
    sa, sb = sol["interval_soft"]
    assert sa - 1e-12 <= a <= sol["raw_in"] <= b <= sb + 1e-12
    for x in np.linspace(a, b, 7)[1:-1]:          # every raw_in in the reported interval shows the measured frames
        assert np.array_equal(ps.ae_frame(float(x), 1.0, ks, 0, C30, R2997), m)
    assert sol["margin_ms"] == pytest.approx(min(sol["raw_in"] - a, b - sol["raw_in"]) * 1000, abs=1e-6)
    # without a preference nothing changes (interval_soft == interval_floor)
    assert old["interval_soft"] == old["interval_floor"] and old["data_cost"] == 0.0
    # a measurement no 1.0x line reproduces (a +2 step at frame 20): a minimum-penalty phase, its cost reported
    mm = m.copy()
    mm[:20] -= 1
    sol2 = ps.solve_raw_in(ks, m - 1, m, 0, 1.0, C30, R2997, prefer=(mm, mm))
    pred = ps.ae_frame(sol2["raw_in"], 1.0, ks, 0, C30, R2997)
    assert int((pred != mm).sum()) == 20 and sol2["data_cost"] == pytest.approx(20.0)


def test_best_subinterval_sweep():
    # two unit penalties overlapping on [0.5, 1): minimum 0 on the widest free piece
    c, a, b = ps.best_subinterval([0.0, 0.5], [1.0, 1.5], [1.0, 1.0], -1.0, 2.0)
    assert c == 0.0 and (a, b) == (-1.0, 0.0)
    # every x penalised: the least penalised piece
    c, a, b = ps.best_subinterval([-2.0, 0.5], [0.5, 3.0], [2.0, 1.0], -1.0, 2.0)
    assert c == 1.0 and (a, b) == (0.5, 2.0)
    # a floating-point sliver between abutting penalties is no minimum
    s1 = 0.1 - 0.3 * 7
    c, a, b = ps.best_subinterval([s1, (0.1 + 1.0) - 0.3 * 7], [s1 + 1.0, (0.1 + 2.0) - 0.3 * 7], [1.0, 1.0],
                                  s1, s1 + 2.0)
    assert c == 1.0
    assert ps.min_penalty(np.array([0.0, 0.5]), np.array([1.0, 1.0]), 0.0, 1.0) == 1.0
    # degenerate interval: the cost at its centre
    assert ps.best_subinterval([0.0], [1.0], [3.0], 0.5, 0.5) == (3.0, 0.5, 0.5)


def test_estimate_speed_robust():
    ks = np.arange(60)
    m = ff_select(60, F(1, 30000), 1001, 1.0, C30, 400)
    m2 = m.copy()
    m2[20] += 30                                   # one wild outlier
    assert ps.estimate_speed(ks, m2, m2, C30, R2997) == pytest.approx(1.0, abs=0.01)
    assert np.isnan(ps.estimate_speed([3], [5], [5], C30, R2997))


# ---------------------------------------------------------------------------------------------------
# DP: a 1-frame-skip jump cut stays a cut (segment.py)
# ---------------------------------------------------------------------------------------------------

def _fm_from_m(m: np.ndarray) -> FrameMap:
    n = m.size
    fm = FrameMap(n)
    fm.status = np.full(n, Status.MATCH)
    fm.raw = m
    fm.raw_lo = m
    fm.raw_hi = m
    fm.soft_lo = m
    fm.soft_hi = m
    fm.score = np.full(n, 0.99)
    fm.second = np.full(n, 0.95)
    fm.margin = np.full(n, 0.04)
    fm.conf = np.full(n, 0.95)
    fm.track = np.zeros(n)
    fm.s = np.full(n, 1.0)
    fm.theta = np.zeros(n)
    fm.tx = np.zeros(n)
    fm.ty = np.zeros(n)
    return fm


def _proxies(n: int):
    comp = Proxy("competitor", "", None, (1080, 1920), (0.5, 0.5), C30, np.arange(n) / 30.0, n)
    raw = Proxy("raw", "", None, (1920, 1080), (1 / 3, 1 / 3), R2997, np.zeros(1), 100000)
    return comp, raw


@pytest.mark.parametrize("L,c", [(30, 15), (40, 13), (60, 30), (90, 30), (90, 60)])
def test_dp_one_frame_skip_is_a_cut_not_a_fake_speed(L, c):
    from match_cuts.segment import build_segments
    m = ff_select(L, F(1, 30000), 1001, 1.0, C30, 1000)
    m = np.r_[m[:c], m[c:] + 1]
    ks = np.arange(L)
    vr = ps.feasible_speed_range(ks, m, m, 0, C30, R2997)
    assert vr is not None and vr[1] > 1.0015          # merged, it WOULD be feasible at a fake speed
    fm = _fm_from_m(m)
    comp, raw = _proxies(L)
    segs = build_segments(fm, comp, raw, None, None, Config(work_dir="/nonexistent"), None, None)
    raws = [s for s in segs if s.type == "raw"]
    assert [(s.comp_in, s.comp_out) for s in raws] == [(0, c), (c, L)]
    assert all(s.speed == 1.0 and not s.unsnapped for s in raws)


def test_exact_speed_range_equals_highs():
    rng = np.random.default_rng(11)
    cases = []
    for label, rf, tb, step, v in CASES:
        m = ff_select(300, tb, step, v, C30, 900)
        cases.append((np.arange(300), m, m, rf))
        mm = m.copy()
        mm[150:] += 1                                 # a 1-frame skip: still feasible at a fake speed
        cases.append((np.arange(300), mm, mm, rf))
    for _ in range(40):
        n = int(rng.integers(1, 80))
        ks = np.sort(rng.choice(400, n, replace=False))
        u = float(rng.uniform(-1.5, 2.5))
        lo = np.floor(rng.uniform(0, 1) + u * ks + rng.normal(0, 0.12, n)).astype(int) + 5000
        hi = lo + rng.integers(0, 2, n)
        cases.append((ks, lo, hi, R2997))
    n_feas = 0
    for ks, lo, hi, rf in cases:
        a = ps.feasible_speed_range(ks, lo, hi, int(ks[0]), C30, rf, method="exact")
        b = ps.feasible_speed_range(ks, lo, hi, int(ks[0]), C30, rf, method="highs")
        assert (a is None) == (b is None)
        if a is not None:
            n_feas += 1
            assert a[0] == pytest.approx(b[0], abs=2e-6) and a[1] == pytest.approx(b[1], abs=2e-6)
    assert n_feas > 20


# ---------------------------------------------------------------------------------------------------
# FX-10: AE floor-rule safety -- breakpoint cells, max-min-slack placement, exact slack
# ---------------------------------------------------------------------------------------------------

R23976 = F(24000, 1001)
U24 = R23976 / C30                                    # 800/1001 RAW frames per comp frame


def _brute_slack(raw_in, v, comp_in, k0, k1, cf, rf):
    best = None
    for k in range(k0, k1):
        p = rf * (F(raw_in) + F(v) * F(k - comp_in) / cf)
        fl = p.numerator // p.denominator
        s = min(p - fl, fl + 1 - p)
        if best is None or s < best[0]:
            best = (s, k)
    return best


def test_exact_min_slack_equals_brute_force_fractions():
    rng = np.random.default_rng(4)
    for _ in range(30):
        rf = [R2997, R23976, F(25), F(60)][int(rng.integers(4))]
        v = [1.0, 1.1, 0.5, 1 / 1.1][int(rng.integers(4))]
        raw_in = round(float(rng.uniform(1, 600)), 9)
        n = int(rng.integers(1, 120))
        s, k = ps.exact_min_slack(raw_in, v, 10, 10, 10 + n, C30, rf)
        assert (s, k) == _brute_slack(raw_in, v, 10, 10, 10 + n, C30, rf)
    assert ps.exact_min_slack(1.0, 1.0, 0, 0, 1, C30, F(30))[0] == 0


def _slip_cell(n: int) -> tuple[F, F]:
    """A cadence-slip cell of n frames at 23.976 in 30: the breakpoint gap of width 4/1001 frame."""
    bp = sorted({(-U24 * d) % 1 for d in range(n)})
    for a, b in zip(bp, bp[1:]):
        if b - a == F(4, 1001):
            return a, b
    raise AssertionError("no slip cell")


@pytest.mark.parametrize("n", [12, 81])
def test_slip_cell_pins_the_phase(n):
    """u = 800/1001: exact frames across a cadence slip (a 5-frame window with 3 RAW advances) pin raw_in to
    ONE breakpoint cell of 4/1001 frame; the placement is its midpoint, +-2/1001 frame = +-0.083 ms. That is
    maximal information (the real run's 9 'AE-rule-sensitive 0.083333 ms' segments), reported as pinned."""
    a, b = _slip_cell(n)
    j0 = 5000
    x_true = j0 + (a + b) / 2 - F(1, 8008)             # anywhere inside the cell
    frames = [math.floor(x_true + U24 * d) for d in range(n)]
    sol = ps.solve_raw_in(np.arange(100, 100 + n), frames, frames, 100, 1.0, C30, R23976)
    assert sol["ok"] and sol["pinned"]
    assert sol["cell_width"] == pytest.approx(4 / 1001, abs=1e-9)
    assert sol["min_slack"] == pytest.approx(2 / 1001, abs=1e-9)
    assert sol["best_slack"] == pytest.approx(2 / 1001, abs=1e-9)
    assert sol["margin_ms"] == pytest.approx(2 / 24000 * 1000, abs=1e-9)          # 0.083333 ms
    raw_in = round(sol["raw_in"], 9)
    slack, _k = ps.exact_min_slack(raw_in, 1.0, 100, 100, 100 + n, C30, R23976)
    assert abs(float(slack) - 2 / 1001) < 2e-8                                     # the 9-decimal raw_in as written
    assert [ps.ae_frame(raw_in, 1.0, 100 + d, 100, C30, R23976) for d in range(n)] == frames
    adv = [frames[d + 5] - frames[d] for d in range(n - 5)]                       # the slip: 3 advances in 5
    assert min(adv) == 3 and max(adv) == 4


def test_s32_replica_centre_on_a_breakpoint_is_moved_to_the_best_cell():
    """The real run's S32 (k 592-596, v = 1, 23.976 in 30): k594 is ambiguous (RAW 1058 or 1059), so its
    breakpoint is NOT an interval edge; the floor interval [1058258, 1058660]/24000 s has its centre exactly
    on that breakpoint (x + u*2 = 1059 -> AE showed 1058 only through 8e-9 frame of 9-decimal rounding). The
    max-min-slack placement keeps every exact frame and leaves >= 1/(4*1001) frame of slack on every frame."""
    ks = np.arange(592, 596)
    lo = np.array([1057, 1058, 1058, 1059])
    hi = np.array([1057, 1058, 1059, 1059])
    sol = ps.solve_raw_in(ks, lo, hi, 592, 1.0, C30, R23976)
    a, b = sol["interval_floor"]
    assert a == pytest.approx(1058258 / 24000, abs=1e-12) and b == pytest.approx(1058660 / 24000, abs=1e-12)
    centre = round((a + b) / 2, 9)
    assert centre == 44.102458333                                                   # the run's raw_in
    s_old, k_old = ps.exact_min_slack(centre, 1.0, 592, 592, 596, C30, R23976)
    assert k_old == 594 and float(s_old) < 1e-7                                     # razor edge
    raw_in = round(sol["raw_in"], 9)
    s_new, _k = ps.exact_min_slack(raw_in, 1.0, 592, 592, 596, C30, R23976)
    assert float(s_new) >= 1 / (4 * 1001)
    assert not sol["pinned"] and sol["best_slack"] == pytest.approx(sol["min_slack"], abs=1e-9)
    for k, l_, h_ in zip(ks, lo, hi):                                               # exact frames unchanged
        j = ps.ae_frame(raw_in, 1.0, int(k), 592, C30, R23976)
        assert l_ <= j <= h_
        if l_ == h_:
            assert j == ps.ae_frame(centre, 1.0, int(k), 592, C30, R23976)


def test_place_in_cells_widest_cell_target_margin_and_wide_window():
    u = float(U24)
    d = np.arange(12)
    a, b = 0.0, 3.0
    p = ps.place_in_cells(a, b, u, d)
    edges = np.concatenate([[a], ps.breakpoints_in(a, b, u, d), [b]])
    widths = np.diff(edges)
    assert p["hi"] - p["lo"] == pytest.approx(widths.max()) and p["x"] == pytest.approx((p["lo"] + p["hi"]) / 2)
    assert p["best_half"] == pytest.approx(widths.max() / 2) and not p["pinned"]
    # a target inside a wide cell is kept; near an edge it is clamped to the margin
    t = p["lo"] + 0.3 * (p["hi"] - p["lo"])
    q = ps.place_in_cells(a, b, u, d, target=t, margin=lambda w: 0.05 * w)
    assert q["x"] == pytest.approx(t)
    q = ps.place_in_cells(a, b, u, d, target=p["lo"] + 1e-6, margin=lambda w: 0.05 * w)
    assert q["x"] == pytest.approx(q["lo"] + 0.05 * (q["hi"] - q["lo"]))
    # outside [a, b]: the nearest cell
    q = ps.place_in_cells(a, b, u, d, target=-0.4, margin=lambda w: 0.05 * w)
    assert q["lo"] == pytest.approx(a)
    # a seconds-wide interval (static shot) gives the same result as a full enumeration around the target
    big = ps.place_in_cells(10.0, 2000.0, u, d, target=1234.567, margin=lambda w: 0.05 * w)
    small = ps.place_in_cells(1230.0, 1240.0, u, d, target=1234.567, margin=lambda w: 0.05 * w)
    assert big["x"] == pytest.approx(small["x"], abs=1e-9) and big["lo"] == pytest.approx(small["lo"], abs=1e-9)
    assert ps.place_in_cells(10.0, 2000.0, u, d)["best_half"] == pytest.approx(
        ps.place_in_cells(0.0, 1.0, u, d)["best_half"], abs=1e-9)
    # floor∩round (u = 1): the round-rule breakpoints cut too -> the quarter-frame point of the overlap
    r = ps.place_in_cells(199.0, 201.5, 1.0, np.arange(30), round_rule=True)
    assert r["x"] == pytest.approx(200.25) and r["half"] == pytest.approx(0.25)


def test_layer_cell_and_place_raw_in_in_seconds():
    iv = [1058258 / 24000, 1058660 / 24000]                                         # the S32 replica
    p = ps.place_raw_in(iv, 592, 596, 1.0, C30, R23976)
    c = ps.layer_cell(p["raw_in"], 592, 596, 1.0, C30, R23976)
    assert c["cell"][0] == pytest.approx(p["cell"][0], abs=1e-12) and c["half"] == pytest.approx(p["half"], abs=1e-9)
    assert iv[0] <= p["raw_in"] <= iv[1]
