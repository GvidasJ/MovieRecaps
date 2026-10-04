"""Unit tests for segment.py (DESIGN §5 segment.py): FrameMaps synthesised directly from ffmpeg-exact frame
selection patterns (see test_phase_solve.ff_select), plus small proxy arrays where pixels are needed
(crossfades made with the exact xfade formula, punch-in, uniform frames, dips)."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction as F
from pathlib import Path

import numpy as np
import pytest

from match_cuts import phase_solve as ps
from match_cuts.common import DecisionLog
from match_cuts.config import Config
from match_cuts.geometry import Sim, to_cv_matrix
from match_cuts.model import FrameMap, Proxy, Status
from match_cuts.segment import build_segments, scenedetect_changes, segment_constraints

C30 = F(30)
R2997 = F(30000, 1001)


def ff_select(n_out: int, v: float, j0: int, tb: F = F(1, 30000), step: int = 1001, out_fps: F = C30) -> np.ndarray:
    """ffmpeg `-ss j0 ... setpts=(PTS-STARTPTS)/v,fps=30` frame selection (see test_phase_solve)."""
    def ts(i: int) -> int:
        p = int(float(i * step) / v) if v != 1.0 else i * step
        num = p * tb.numerator * out_fps.numerator
        den = tb.denominator * out_fps.denominator
        return (2 * num + den) // (2 * den)

    out, i = [], 0
    for n in range(n_out):
        while ts(i + 1) <= n:
            i += 1
        out.append(j0 + i)
    return np.asarray(out, dtype=np.int64)


@dataclass
class Spec:
    kind: str = "raw"           # raw | none | uniform | freeze
    n: int = 30
    j0: int = 0
    v: float = 1.0
    flip: bool = False
    sim: tuple = (1.0, 0.0, 0.0, 0.0)
    sim_fn: object = None       # callable(i) -> (s, th, tx, ty) for animated framing
    track: int = 0
    mean: float = 128.0
    m: object = None            # explicit RAW frames


def build_fm(specs: list[Spec]) -> tuple[FrameMap, list[int]]:
    rows = []
    bounds = []
    k = 0
    for sp in specs:
        bounds.append(k)
        if sp.kind == "raw":
            m = np.asarray(sp.m) if sp.m is not None else ff_select(sp.n, sp.v, sp.j0)
        elif sp.kind == "freeze":
            m = np.full(sp.n, sp.j0)
        else:
            m = np.full(sp.n, -1)
        for i in range(sp.n):
            sim = sp.sim_fn(i) if sp.sim_fn else sp.sim
            rows.append((sp, int(m[i]), sim))
        k += sp.n
    bounds.append(k)
    n = len(rows)
    fm = FrameMap(n)
    st = np.array([Status.MATCH if r[0].kind in ("raw", "freeze") else
                   (Status.UNIFORM if r[0].kind == "uniform" else Status.NONE) for r in rows])
    raw = np.array([r[1] for r in rows])
    fm.status = st
    fm.raw = raw
    fm.raw_lo = raw
    fm.raw_hi = raw
    fm.soft_lo = raw
    fm.soft_hi = raw
    matched = st == Status.MATCH
    fm.score = np.where(matched, 0.99, np.where(st == Status.NONE, 0.2, np.nan))
    fm.second = np.where(matched, 0.95, np.nan)
    fm.margin = np.where(matched, 0.04, np.nan)
    fm.conf = np.where(matched, 0.95, 0.0)
    fm.flip = np.array([r[0].flip for r in rows])
    fm.track = np.array([r[0].track if r[0].kind in ("raw", "freeze") else -1 for r in rows])
    sims = np.array([r[2] for r in rows], dtype=np.float64)
    fm.s = np.where(matched, sims[:, 0], np.nan)
    fm.theta = np.where(matched, sims[:, 1], np.nan)
    fm.tx = np.where(matched, sims[:, 2], np.nan)
    fm.ty = np.where(matched, sims[:, 3], np.nan)
    fm.mean = np.array([r[0].mean for r in rows], dtype=np.float32)
    fm.std = np.where(st == Status.UNIFORM, 0.5, 40.0)
    return fm, bounds


def proxies(n: int, raw_n: int = 200000, comp_frames=None, raw_frames=None, comp_full=(1080, 1920),
            raw_full=(1920, 1080), comp_ratio=(0.5, 0.5), raw_ratio=(1 / 3, 1 / 3), path=""):
    comp = Proxy("competitor", path, comp_frames, comp_full, comp_ratio, C30, np.arange(n) / 30.0, n)
    raw = Proxy("raw", "", raw_frames, raw_full, raw_ratio, R2997, np.zeros(1), raw_n)
    return comp, raw


def cfg_(**kw) -> Config:
    kw.setdefault("work_dir", "/nonexistent_match_cuts_test")
    return Config(**kw)


def raws(segs):
    return [s for s in segs if s.type == "raw"]


def run(fm, comp, raw, layout=None, cfg=None, dlog=None, debug_dir=None, hints=None):
    return build_segments(fm, comp, raw, layout, None, cfg or cfg_(), dlog, debug_dir, hints)


def check_model(seg, fm, allow_ties=True):
    """Every MATCH frame of the segment is reproduced by the AE rule at the solved raw_in."""
    ks, lo, hi = segment_constraints(seg, fm)
    sol = ps.solve_raw_in(ks, lo, hi, seg.comp_in, seg.speed, C30, R2997)
    assert sol["ok"]
    if "time line shared" in (seg.notes or ""):
        # FX-04 2: one phase solve with the segments on its time line -- inside this segment's own interval
        a, b = sol["interval_soft"]
        assert a - 1e-12 <= seg.raw_in_seconds <= b + 1e-12
    else:
        assert sol["raw_in"] == pytest.approx(seg.raw_in_seconds, abs=1e-12)
    pred = ps.ae_frame(seg.raw_in_seconds, seg.speed, ks, seg.comp_in, C30, R2997)
    bad = [int(k) for k, p, a, b in zip(ks, pred, lo, hi) if not a <= p <= b]
    bad += [int(k) for k, p in zip(ks, pred) if fm.status[k] == Status.MATCH and p != fm.raw[k]]
    assert set(bad) <= set(seg.tie_frames if allow_ties else []), bad


# ---------------------------------------------------------------------------------------------------
# cuts
# ---------------------------------------------------------------------------------------------------

def test_jump_cuts_skip3_and_skip1_out_of_order_and_reuse():
    a = ff_select(50, 1.0, 1000)
    b = ff_select(45, 1.0, int(a[-1]) + 4)          # same-shot jump cut: skip 3 RAW frames
    c = ff_select(40, 1.0, int(b[-1]) + 2)          # same-shot jump cut: skip 1 RAW frame
    hook = ff_select(35, 1.0, 5000)                 # out-of-order hook
    reuse = ff_select(30, 1.0, 1010)                # re-uses RAW 1010.. (inside segment 1's range)
    fm, bd = build_fm([Spec(m=a, n=50), Spec(m=b, n=45), Spec(m=c, n=40), Spec(m=hook, n=35, track=1),
                       Spec(m=reuse, n=30)])
    comp, raw = proxies(fm.n)
    segs = run(fm, comp, raw)
    assert [(s.comp_in, s.comp_out) for s in segs] == list(zip(bd[:-1], bd[1:]))
    assert [s.id for s in segs] == [1, 2, 3, 4, 5]
    assert [s.raw_in_frame for s in segs] == [int(x[0]) for x in (a, b, c, hook, reuse)]
    for s in segs:
        assert s.type == "raw" and s.speed == 1.0 and not s.unsnapped and s.speed_range[0] <= 1 <= s.speed_range[1]
        assert s.confidence > 0.5
        check_model(s, fm)


def test_110x_segment_snaps_and_reproduces_frames():
    a = ff_select(40, 1.0, 300)
    fast = ff_select(90, 1.1, 2000)
    c = ff_select(40, 1.0, 900)
    fm, bd = build_fm([Spec(m=a, n=40), Spec(m=fast, n=90), Spec(m=c, n=40)])
    comp, raw = proxies(fm.n)
    segs = run(fm, comp, raw)
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 40), (40, 130), (130, 170)]
    s = segs[1]
    assert s.speed == pytest.approx(1.1) and not s.unsnapped
    assert s.speed_range[0] <= 1.1 <= s.speed_range[1]
    assert s.speed_measured == pytest.approx(1.1, rel=0.02)
    check_model(s, fm)


def test_short_110_segment_with_audio_hint():
    """15-frame 1.10x piece: frames alone are ambiguous with a 1-skip jump cut; audio speed decides."""
    from match_cuts.model import AudioHints
    a = ff_select(40, 1.0, 300)
    fast = ff_select(20, 1.1, 2000)
    c = ff_select(40, 1.0, 900)
    fm, bd = build_fm([Spec(m=a, n=40), Spec(m=fast, n=20), Spec(m=c, n=40)])
    comp, raw = proxies(fm.n)
    t = np.array([0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0])
    hints = AudioHints(t, t.copy(), np.where((t > 1.3) & (t < 2.0), 1.1, 1.0), np.full(t.size, 3.0, np.float32),
                       np.full(t.size, 5.0, np.float32), np.full(t.size, 0.8, np.float32), window=0.3, hop=0.25)
    segs = run(fm, comp, raw, hints=hints)
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 40), (40, 60), (60, 100)]
    assert segs[1].speed == pytest.approx(1.1)


def test_flip_segment_is_split_and_flagged():
    a = ff_select(40, 1.0, 300)
    fl = ff_select(40, 1.0, int(a[-1]) + 1)          # RAW-continuous, only the flip changes
    fm, bd = build_fm([Spec(m=a, n=40), Spec(m=fl, n=40, flip=True, sim=(0.9, 0.0, 10.0, 20.0), track=1)])
    comp, raw = proxies(fm.n)
    segs = run(fm, comp, raw)
    assert [(s.comp_in, s.comp_out, s.flip_h) for s in segs] == [(0, 40, False), (40, 80, True)]
    assert segs[1].transform == pytest.approx({"scale": 0.9, "rotation_deg": 0.0, "tx": 10.0, "ty": 20.0})


def test_push_in_gives_two_linear_keys():
    cx, cy = 540.0, 960.0
    s0, tx0, ty0 = 0.5, 60.0, 690.0

    def zoom(i):
        z = 1 + 0.004 * i
        return (s0 * z, 0.0, z * (tx0 - cx) + cx, z * (ty0 - cy) + cy)

    a = ff_select(30, 1.0, 100)
    p = ff_select(60, 1.0, 2000)
    fm, bd = build_fm([Spec(m=a, n=30, sim=(s0, 0, tx0, ty0)), Spec(m=p, n=60, sim_fn=zoom, track=1)])
    comp, raw = proxies(fm.n)
    segs = run(fm, comp, raw)
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 30), (30, 90)]
    s = segs[1]
    assert s.transform_keys and len(s.transform_keys) == 2
    k0, k1 = s.transform_keys
    assert (k0["comp_frame"], k1["comp_frame"]) == (30, 89)
    for key, i in ((k0, 0), (k1, 59)):
        tr = zoom(i)
        assert key["scale"] == pytest.approx(tr[0], rel=1e-9)
        assert key["tx"] == pytest.approx(tr[2], abs=1e-6) and key["ty"] == pytest.approx(tr[3], abs=1e-6)
    assert segs[0].transform_keys == [] and segs[0].transform["scale"] == pytest.approx(s0)


def test_punch_in_consecutive_step_is_a_cut():
    s0 = (0.5, 0.0, 60.0, 690.0)
    z = 1.25
    s1 = (0.5 * z, 0.0, z * (60 - 540) + 540, z * (690 - 960) + 960)
    m = ff_select(80, 1.0, 400)
    fm, bd = build_fm([Spec(m=m[:37], n=37, sim=s0), Spec(m=m[37:], n=43, sim=s1)])
    comp, raw = proxies(fm.n)
    segs = run(fm, comp, raw)
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 37), (37, 80)]
    assert segs[0].transform["scale"] == pytest.approx(0.5) and segs[1].transform["scale"] == pytest.approx(0.625)
    assert segs[1].raw_in_frame == int(m[37]) and segs[1].speed == 1.0


def test_not_in_raw_flash_and_freeze():
    a = ff_select(40, 1.0, 300)
    b = ff_select(30, 1.0, 1200)
    c = ff_select(40, 1.0, 2500)
    fm, bd = build_fm([Spec(m=a, n=40), Spec(kind="none", n=30), Spec(m=b, n=30, track=1),
                       Spec(kind="uniform", n=1, mean=250.0), Spec(kind="freeze", n=20, j0=1800, track=2),
                       Spec(m=c, n=40, track=3)])
    comp, raw = proxies(fm.n)
    segs = run(fm, comp, raw)
    kinds = [(s.type, s.comp_in, s.comp_out) for s in segs]
    assert kinds == [("raw", 0, 40), ("not_in_raw", 40, 70), ("raw", 70, 100), ("flash", 100, 101),
                     ("raw", 101, 121), ("raw", 121, 161)]
    nir = segs[1]
    assert nir.label.startswith("MISSING - not in RAW (") and nir.audio["exception"] == "not_in_raw"
    assert segs[3].color == "#fafafa"
    fr = segs[4]
    assert fr.speed == 0.0 and fr.time_mode == "remap"
    assert fr.time_remap_keys == [{"comp_frame": 101, "raw_seconds": pytest.approx(1800.25 / float(R2997))},
                                  {"comp_frame": 121, "raw_seconds": pytest.approx(1800.25 / float(R2997))}]
    assert ps.ae_frame(fr.raw_in_seconds, 0.0, 110, 101, C30, R2997) == 1800


def test_isolated_low_margin_frame_is_overridden_not_cut():
    m = ff_select(90, 1.0, 700)
    bad = m.copy()
    bad[40] -= 1
    fm, bd = build_fm([Spec(m=bad, n=90)])
    marg = np.full(90, 0.04)
    marg[40] = 0.0005
    fm.margin = marg
    fm.low_margin = marg < 0.001
    # candidate score vector around the (wrong) argmax: the model frame is within 5 delta
    j0 = np.full(90, -1)
    cand = np.full((90, 15), np.nan, np.float32)
    j0[40] = bad[40] - 7
    cand[40, :] = 0.95
    cand[40, 7] = 0.99
    cand[40, 8] = 0.9895
    fm.cand_j0 = j0
    fm.cand = cand
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1 and segs[0].speed == 1.0
    assert int(fm.raw[40]) == int(m[40]) and bool(fm.low_margin[40])
    assert 40 in segs[0].low_margin_frames
    # FX-12: the re-assignment has its own column and reason; the note says what happened, not 'low-margin'
    from match_cuts.model import REASSIGN_REASONS
    assert int(fm.reassigned[40]) > 0 and REASSIGN_REASONS[int(fm.reassigned[40])] in ("drop", "model")
    assert int(np.count_nonzero(fm.reassigned)) == 1
    assert "frames shown from the segment model instead of refine's best measurement" in segs[0].notes
    assert " 40" in segs[0].notes
    check_model(segs[0], fm)


def test_reassigned_frames_are_not_labelled_low_margin():
    """FX-12: write_back no longer forces low_margin on a frame it re-assigns -- a frame refine measured with a clear
    margin keeps low_margin False and gets the 'reassigned' reason instead; the segment confidence still counts it."""
    m = ff_select(90, 1.0, 700)
    bad = m.copy()
    bad[40] -= 1
    fm, bd = build_fm([Spec(m=bad, n=90)])
    marg = np.full(90, 0.04)
    marg[40] = 0.003                            # above low_margin_eps: refine did NOT flag it low-margin ...
    fm.margin = marg
    fm.low_margin = marg < 0.001
    slo, shi = np.asarray(fm.soft_lo).copy(), np.asarray(fm.soft_hi).copy()
    shi[40] = m[40]                             # ... but the model's frame is inside its soft range (within noise)
    fm.soft_lo, fm.soft_hi = slo, shi
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1 and int(fm.raw[40]) == int(m[40])
    from match_cuts.model import REASSIGN_REASONS
    assert REASSIGN_REASONS[int(fm.reassigned[40])] == "model" and not bool(fm.low_margin[40])
    assert 40 not in segs[0].low_margin_frames
    assert "frames shown from the segment model instead of refine's best measurement: model 40" in segs[0].notes
    assert segs[0].confidence < 0.95 + 1e-9      # still counted in the confidence
    assert not np.any(fm.reassigned[np.arange(90) != 40])


def test_speed_only_cut_has_ambiguity_window():
    a = ff_select(60, 1.0, 1000)
    b = ff_select(60, 1.1, int(a[-1]) + 1)
    fm, bd = build_fm([Spec(m=a, n=60), Spec(m=b, n=60)])
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 2
    A, B = segs
    assert A.speed == 1.0 and B.speed == pytest.approx(1.1)
    assert B.cut_ambiguity is not None and B.cut_ambiguity[0] <= 60 <= B.cut_ambiguity[1]
    assert B.cut_ambiguity[0] <= B.comp_in <= B.cut_ambiguity[1]
    check_model(A, fm)
    check_model(B, fm)


def test_rerun_on_mutated_framemap_is_identical(tmp_path):
    m = ff_select(90, 1.0, 700)
    bad = m.copy()
    bad[40] -= 1
    fm, _ = build_fm([Spec(m=bad, n=90), Spec(kind="none", n=10), Spec(m=ff_select(50, 1.1, 3000), n=50)])
    marg = np.where(np.arange(fm.n) == 40, 0.0005, 0.04)
    fm.margin = marg
    s1 = [s.to_dict() for s in run(fm, *proxies(fm.n))]
    s2 = [s.to_dict() for s in run(fm, *proxies(fm.n))]
    assert json.dumps(s1, sort_keys=True, default=str) == json.dumps(s2, sort_keys=True, default=str)


# ---------------------------------------------------------------------------------------------------
# pixel tests: tiny proxies, identity geometry (comp full == RAW full == 192x128, proxies at 1/2)
# ---------------------------------------------------------------------------------------------------

W, H = 192, 128


def texture_bank(n: int, seed: int = 0) -> np.ndarray:
    import cv2
    rng = np.random.default_rng(seed)
    out = np.empty((n, H // 2, W // 2), np.uint8)
    for j in range(n):
        x = rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
        x = cv2.GaussianBlur(x, (0, 0), 1.2)
        x = (x - x.min()) / (x.max() - x.min()) * 220 + 20
        out[j] = x.astype(np.uint8)
    return out


def pix_proxies(comp_frames: np.ndarray, raw_frames: np.ndarray, path: str = ""):
    return proxies(len(comp_frames), raw_n=len(raw_frames), comp_frames=comp_frames, raw_frames=raw_frames,
                   comp_full=(W, H), raw_full=(W, H), comp_ratio=(0.5, 0.5), raw_ratio=(0.5, 0.5), path=path)


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_crossfade_6_frames_gives_O_and_D(tmp_path):
    bank = texture_bank(400, seed=1)
    O, D = 45, 6
    ma = ff_select(O + D, 1.0, 10)                  # A shown through the overlap (xfade keeps A's timeline)
    mb = ff_select(60, 1.0, 250)                    # B from its frame 0 at comp frame O
    n = O + 60
    comp = np.empty((n, H // 2, W // 2), np.uint8)
    for k in range(n):
        if k < O:
            comp[k] = bank[ma[k]]
        elif k < O + D:
            p = np.float32(1.0 - (k - O) / D)          # xfade 'fade': a*progress + b*(1-progress), truncated
            comp[k] = (bank[ma[k]].astype(np.float32) * p + bank[mb[k - O]].astype(np.float32) * (1 - p)).astype(np.uint8)
        else:
            comp[k] = bank[mb[k - O]]
    # refine-like FrameMap: blend frames O+1..O+2 matched to A, O+3 NONE, O+4..O+5 to B
    rawcol = np.r_[ma[:O + 3], [-1], mb[4:60]]
    specs = [Spec(m=rawcol[:O + 3], n=O + 3, sim=(1, 0, 0, 0)), Spec(kind="none", n=1),
             Spec(m=rawcol[O + 4:], n=n - O - 4, sim=(1, 0, 0, 0), track=1)]
    fm, _ = build_fm(specs)
    cp, rp = pix_proxies(comp, bank)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, cp, rp, dlog=dl)
    dl.close()
    assert [s.type for s in segs] == ["raw", "raw"]
    A, B = segs
    assert (A.comp_in, A.comp_out, B.comp_in, B.comp_out) == (0, O + D, O, n)
    assert B.transition_in["type"] == "crossfade" and B.transition_in["duration_frames"] == D
    assert B.transition_in["alpha"] == pytest.approx([i / D for i in range(D)], abs=1e-6)
    assert A.transition_out == B.transition_in
    assert B.raw_in_frame == int(mb[0]) and A.raw_out_frame == int(ma[O + D - 1])
    assert all(fm.status[k] == Status.BLEND for k in range(O, O + D))
    check_model(A, fm)
    check_model(B, fm)
    assert any(r["decision"] == "crossfade" for r in records(tmp_path / "d.jsonl"))


@pytest.mark.parametrize("gain,D", [(0.9, 6), (0.94, 6), (1.1, 6), (1.1, 8), (1.15, 5)])
def test_crossfade_window_is_exact_under_a_contrast_change(gain, D):
    """review R2-1: the repost is graded (contrast x gain about mid-grey, + lift). The constrained blend fit
    (gain fixed at 1) scales the measured ramp by the gain, so a contrast boost gave D - 1; the crossfade fit
    now takes alpha from the gain-independent estimator (beta_B / (beta_A + beta_B)) and finds (O, D)."""
    bank = texture_bank(400, seed=1)
    O = 45
    ma = ff_select(O + D, 1.0, 10)
    mb = ff_select(60, 1.0, 250)
    n = O + 60
    comp = np.empty((n, H // 2, W // 2), np.uint8)
    for k in range(n):
        if k < O:
            y = bank[ma[k]].astype(np.float32)
        elif k < O + D:
            p = np.float32(1.0 - (k - O) / D)
            y = bank[ma[k]].astype(np.float32) * p + bank[mb[k - O]].astype(np.float32) * (1 - p)
        else:
            y = bank[mb[k - O]].astype(np.float32)
        comp[k] = np.clip(np.round(gain * (y - 128.0) + 128.0 + 6.0), 0, 255).astype(np.uint8)
    rawcol = np.r_[ma[:O + 3], [-1], mb[4:60]]
    specs = [Spec(m=rawcol[:O + 3], n=O + 3, sim=(1, 0, 0, 0)), Spec(kind="none", n=1),
             Spec(m=rawcol[O + 4:], n=n - O - 4, sim=(1, 0, 0, 0), track=1)]
    fm, _ = build_fm(specs)
    segs = run(fm, *pix_proxies(comp, bank))
    assert [s.type for s in segs] == ["raw", "raw"]
    A, B = segs
    assert B.transition_in is not None and B.transition_in["type"] == "crossfade", B.transition_in
    assert (A.comp_out, B.comp_in, B.transition_in["duration_frames"]) == (O + D, O, D), B.transition_in["notes"]


def test_hard_cut_criterion2_and_flash_with_pixels(tmp_path):
    bank = texture_bank(300, seed=2)
    ma = ff_select(40, 1.0, 20)
    mb = ff_select(40, 1.0, 150)
    comp = np.concatenate([bank[ma], np.full((1, H // 2, W // 2), 255, np.uint8), bank[mb]])
    fm, _ = build_fm([Spec(m=ma, n=40), Spec(kind="uniform", n=1, mean=255.0), Spec(m=mb, n=40, track=1)])
    # a jump cut inside B's shot (RAW skip of 3) for criterion 2
    fm2, _ = build_fm([Spec(m=ma, n=40), Spec(m=ff_select(40, 1.0, int(ma[-1]) + 4), n=40)])
    comp2 = bank[np.asarray(fm2.raw)]
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *pix_proxies(comp, bank), dlog=dl)
    segs2 = run(fm2, *pix_proxies(comp2, bank), dlog=dl)
    dl.close()
    assert [(s.type, s.comp_in, s.comp_out) for s in segs] == [("raw", 0, 40), ("flash", 40, 41), ("raw", 41, 81)]
    assert segs[1].color == "#ffffff" and segs[1].transition_in is None
    assert [(s.comp_in, s.comp_out) for s in segs2] == [(0, 40), (40, 80)]
    rec = records(tmp_path / "d.jsonl")
    assert any(r["decision"] == "criterion2_pass" and r["comp_frame"] == 40 for r in rec)
    assert not any(r["decision"] in ("criterion2_fail", "criterion2_move") for r in rec)


def _warp(img: np.ndarray, sim: Sim) -> np.ndarray:
    import cv2
    M = to_cv_matrix(sim, False, W, (0.5, 0.5), (0.5, 0.5))
    return cv2.warpAffine(img, M, (W // 2, H // 2), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def test_punch_in_localised_by_scoring_both_transforms(tmp_path):
    bank = texture_bank(200, seed=3)
    m = ff_select(60, 1.0, 30)
    c = 31
    z = 1.25
    s0 = Sim(1.0, 0.0, 0.0, 0.0)
    s1 = Sim(z, 0.0, (1 - z) * W / 2, (1 - z) * H / 2)
    comp = np.stack([bank[m[k]] if k < c else _warp(bank[m[k]], s1) for k in range(60)])

    def sims(i):     # refine sampled every 3 frames: the per-frame model ramps over [c-2, c+1]
        k = i
        if k <= c - 2:
            return (s0.s, 0, s0.tx, s0.ty)
        if k >= c + 1:
            return (s1.s, 0, s1.tx, s1.ty)
        u = (k - (c - 2)) / 3
        return (s0.s + u * (s1.s - s0.s), 0, s0.tx + u * (s1.tx - s0.tx), s0.ty + u * (s1.ty - s0.ty))

    fm, _ = build_fm([Spec(m=m, n=60, sim_fn=sims)])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *pix_proxies(comp, bank), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, c), (c, 60)]
    assert segs[1].transform["scale"] == pytest.approx(z)
    assert segs[0].transform["scale"] == pytest.approx(1.0)
    rec = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "transform_step"]
    assert rec and rec[0]["evidence"]["method"] == "scored_both_transforms"


def test_dip_to_black_with_fades():
    bank = texture_bank(300, seed=4)
    ma = ff_select(40, 1.0, 20)
    mb = ff_select(40, 1.0, 180)
    Da, Db = 5, 4
    frames, specs = [], []
    for k in range(40):                                    # A fades out over its last frames
        a = max(0.0, (k - (40 - Da)) / Da)
        frames.append((bank[ma[k]].astype(np.float32) * (1 - a)).astype(np.uint8))
    frames += [np.zeros((H // 2, W // 2), np.uint8)] * 3   # black hold
    for i in range(40):                                    # B fades in from the last black frame
        a = min(1.0, (i + 1) / Db)
        frames.append((bank[mb[i]].astype(np.float32) * a).astype(np.uint8))
    fm, _ = build_fm([Spec(m=ma, n=40), Spec(kind="uniform", n=3, mean=0.0), Spec(m=mb, n=40, track=1)])
    segs = run(fm, *pix_proxies(np.stack(frames), bank))
    types = [(s.type, s.comp_in, s.comp_out) for s in segs]
    assert types == [("raw", 0, 40), ("dip", 40 - Da, 42 + Db), ("raw", 42, 83)]
    A, dip, B = segs
    assert dip.color == "#000000"
    assert A.transition_out["type"] == "dip_black" and A.transition_out["duration_frames"] == Da
    assert B.transition_in["type"] == "dip_black" and B.transition_in["duration_frames"] == Db
    assert B.raw_in_frame == int(mb[0]) - 1 or B.raw_in_frame == int(mb[0])   # invisible at O'
    check_model(B, fm)


# ---------------------------------------------------------------------------------------------------
# PySceneDetect cross-check + debug plots
# ---------------------------------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_scenedetect_crosscheck_and_debug_plots(tmp_path):
    clip = tmp_path / "comp.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=160x120:r=30,trim=end_frame=40,setpts=PTS-STARTPTS[a];"
                    "mandelbrot=s=160x120:r=30,trim=end_frame=40,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(clip)], check=True)
    cfg = cfg_(work_dir=str(tmp_path / "work"))
    ch = scenedetect_changes(str(clip), cfg)
    assert 40 in ch or 39 in ch or 41 in ch
    assert scenedetect_changes(str(clip), cfg) == ch            # cached
    fm, _ = build_fm([Spec(m=ff_select(40, 1.0, 100), n=40), Spec(m=ff_select(40, 1.0, 900), n=40)])
    comp, raw = proxies(fm.n, path=str(clip))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = build_segments(fm, comp, raw, None, None, cfg, dl, tmp_path / "debug")
    dl.close()
    rec = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "scenedetect_crosscheck"]
    assert rec and 40 in [c for c in rec[0]["evidence"]["agree"]] + [c + 1 for c in rec[0]["evidence"]["agree"]] + \
        [c - 1 for c in rec[0]["evidence"]["agree"]]
    assert rec[0]["evidence"]["unexplained"] == []
    assert (tmp_path / "debug" / "mapping.png").stat().st_size > 1000
    assert (tmp_path / "debug" / "scores.png").stat().st_size > 1000
    assert len(segs) == 2


# ---------------------------------------------------------------------------------------------------
# reverse, ramps, ambiguous-identical frames, frame-blend retiming, vectorised blend fit
# ---------------------------------------------------------------------------------------------------

def remap_frames(seg, ks):
    """RAW frame AE shows under LINEAR time-remap keys (floor rule on the remapped time)."""
    kf = np.array([k["comp_frame"] for k in seg.time_remap_keys], float)
    vs = np.array([k["raw_seconds"] for k in seg.time_remap_keys], float)
    return np.floor(np.interp(np.asarray(ks, float), kf, vs) * float(R2997) + 1e-9).astype(int)


def test_reverse_playback_gives_remap_keys():
    rev = 5000 - ff_select(60, 1.0, 0)
    fm, _ = build_fm([Spec(m=ff_select(40, 1.0, 300), n=40), Spec(m=rev, n=60, track=1),
                      Spec(m=ff_select(40, 1.0, 900), n=40, track=2)])
    segs = run(fm, *proxies(fm.n))
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 40), (40, 100), (100, 140)]
    r = segs[1]
    assert r.speed == -1.0 and r.time_mode == "remap" and len(r.time_remap_keys) == 2
    ks = np.arange(40, 100)
    assert np.array_equal(remap_frames(r, ks), fm.raw[ks])


def test_speed_ramp_becomes_one_remap_segment():
    pieces, j = [], 1000
    for v in (1.0, 1.1, 1.25, 1.5):
        m = ff_select(40, v, j)
        pieces.append(m)
        j = int(m[-1]) + 1
    m = np.concatenate(pieces)
    fm, _ = build_fm([Spec(m=m, n=m.size)])
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1
    s = segs[0]
    assert s.time_mode == "remap" and "speed ramp" in s.notes
    assert s.speed_range == pytest.approx([1.0, 1.5])
    ks = np.arange(s.comp_in, s.comp_out)
    assert np.array_equal(remap_frames(s, ks), fm.raw[ks])


def test_ambiguous_identical_frames_are_listed():
    m = ff_select(60, 1.0, 400)
    fm, _ = build_fm([Spec(m=m, n=60)])
    lo, hi = m.copy(), m.copy()
    lo[10:20], hi[10:20] = m[10] - 2, m[19] + 2
    fm.raw_lo, fm.raw_hi, fm.soft_lo, fm.soft_hi = lo, hi, lo, hi
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1 and segs[0].speed == 1.0
    assert segs[0].ambiguous_frames == list(range(10, 20))


def test_frame_blend_retiming_detected():
    bank = texture_bank(400, seed=5)
    n = 60
    u = 1.1 * float(R2997) / 30.0
    t = 100.3 + u * np.arange(n)
    comp, raw_col, score = [], [], []
    for tk in t:
        j, f = int(np.floor(tk)), float(tk - np.floor(tk))
        img = bank[j].astype(np.float32) * (1 - f) + bank[j + 1].astype(np.float32) * f
        comp.append(img.astype(np.uint8))
        raw_col.append(int(np.floor(tk + 0.5)))
        score.append(0.8 if 0.1 < f < 0.9 else 0.99)
    fm, _ = build_fm([Spec(m=np.array(raw_col), n=n)])
    fm.score = np.array(score)
    segs = run(fm, *pix_proxies(np.stack(comp), bank))
    assert len(segs) == 1
    s = segs[0]
    assert s.speed == pytest.approx(1.1) and s.retime == "frame_blend" and "frame-blend" in s.notes


def test_vectorised_blend_fit_matches_scoring_fit_blend():
    from match_cuts.scoring import fit_blend, prepare_comp, warp_to_roi, _blur
    from match_cuts.segment import _Scorer
    bank = texture_bank(10, seed=6)
    comp = (0.3 * bank[2].astype(np.float32) + 0.7 * bank[5].astype(np.float32) + 3).astype(np.uint8)
    cp, rp = pix_proxies(comp[None], bank)
    sc = _Scorer(cp, rp, None, None, cfg_())
    sim = Sim.identity()
    r = sc.blend_fit(0, [(2, sim, False)], [(5, sim, False)])
    reg = prepare_comp(comp, sc.roi, sc.base_allowed, blur=1.0)
    wa, va = warp_to_roi(bank[2], sim, False, W, (0.5, 0.5), (0.5, 0.5), sc.roi)
    wb, vb = warp_to_roi(bank[5], sim, False, W, (0.5, 0.5), (0.5, 0.5), sc.roi)
    alpha, _res, z = fit_blend(reg, _blur(wa, 1.0), _blur(wb, 1.0), va & vb)
    assert r["alpha_a"] == pytest.approx(alpha, abs=1e-6) and alpha == pytest.approx(0.3, abs=0.02)
    assert r["zfit"] == pytest.approx(z, abs=1e-6)


def test_repeated_unsnapped_speed_is_reused():
    """Two segments at an odd speed (1.337x): the second reuses the first's speed via snap_speed's
    'preferred' candidates instead of reporting two different unsnapped values."""
    a = ff_select(150, 1.337, 1000)
    b = ff_select(150, 1.337, 4000)
    fm, _ = build_fm([Spec(m=a, n=150), Spec(m=b, n=150, track=1),
                      Spec(m=ff_select(400, 1.0, 8000), n=400, track=2)])
    segs = run(fm, *proxies(fm.n))
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 150), (150, 300), (300, 700)]
    A, B, C = segs
    assert A.unsnapped and abs(A.speed / 1.337 - 1) < 0.005
    assert B.speed == A.speed or (B.unsnapped and abs(B.speed / 1.337 - 1) < 0.005)
    assert C.speed == 1.0


def test_noisy_refine_output_keeps_exact_cuts_and_fixes_frames():
    """Refine-like noise: isolated and paired +-1 argmax errors whose model frame scores within 5 delta,
    widened soft ranges and track ids that change inside shots. Cuts (incl. a genuine 1-frame-skip jump
    cut) must be exact and every wrong m(k) corrected to the segment model's frame."""
    a = ff_select(80, 1.0, 1000)
    b = ff_select(80, 1.0, int(a[-1]) + 2)            # genuine 1-frame skip
    c = ff_select(60, 1.1, 3000)
    d = ff_select(90, 1.0, 4000)
    fm, bd = build_fm([Spec(m=a, n=80), Spec(m=b, n=80), Spec(m=c, n=60, track=1), Spec(m=d, n=90, track=2)])
    n = fm.n
    truth = np.asarray(fm.raw).copy()
    errs = {20: 1, 41: -1, 42: 1, 100: -1, 101: -1, 150: 1, 230: -1, 260: 1, 261: 1, 290: -1}
    raw, lo, hi = truth.copy(), truth.copy(), truth.copy()
    j0 = truth - 7
    cand = np.full((n, 15), 0.95, np.float32)
    cand[:, 7] = 0.99
    cand[:, 6] = cand[:, 8] = 0.985
    marg = np.full(n, 0.012)
    for k, e in errs.items():
        raw[k] = lo[k] = hi[k] = truth[k] + e
        j0[k] = raw[k] - 7
        cand[k] = 0.95
        cand[k, 7] = 0.99
        cand[k, 7 - e] = 0.9885
        marg[k] = 0.0008
    lo[[5, 60, 170, 200]] -= 1                         # soft ranges of 2 frames
    tr = np.asarray(fm.track).copy()
    tr[30:] += 10                                      # refine re-anchored mid-shot
    tr[250:] += 10
    fm.raw = fm.raw_lo = fm.raw_hi = raw
    fm.soft_lo, fm.soft_hi, fm.margin, fm.cand_j0, fm.cand, fm.track = lo, hi, marg, j0, cand, tr
    segs = run(fm, *proxies(n))
    assert [(s.comp_in, s.comp_out) for s in segs] == list(zip(bd[:-1], bd[1:]))
    assert [s.speed for s in segs] == pytest.approx([1.0, 1.0, 1.1, 1.0])
    assert np.array_equal(np.asarray(fm.raw), truth)
    for k in errs:                     # FX-12: re-assigned (with its reason), not relabelled low-margin
        assert int(fm.reassigned[k]) > 0, k
    for s in segs:
        check_model(s, fm)


def test_rotation_threshold_and_recentring():
    cx, cy = 540.0, 960.0
    small = Sim(0.9, 0.1, 30.0, 500.0)
    big = Sim(0.9, 1.5, 30.0, 500.0)
    fm, _ = build_fm([Spec(m=ff_select(40, 1.0, 100), n=40, sim=(small.s, small.theta_deg, small.tx, small.ty)),
                      Spec(m=ff_select(40, 1.0, 900), n=40, sim=(big.s, big.theta_deg, big.tx, big.ty), track=1)])
    segs = run(fm, *proxies(fm.n))
    t0, t1 = Sim.from_dict(segs[0].transform), Sim.from_dict(segs[1].transform)
    assert t0.theta_deg == 0.0 and t1.theta_deg == pytest.approx(1.5)
    # zeroing a sub-threshold rotation keeps the RAW point at the frame centre where it was
    pre = small.inverse().apply([[cx, cy]])[0]
    assert np.allclose(t0.apply([pre])[0], [cx, cy], atol=1e-6)


# ---------------------------------------------------------------------------------------------------
# review v3 regressions: data term (time-math F2), phantom cuts (verification-honesty F3), layout periods (D1)
# ---------------------------------------------------------------------------------------------------

def _ae(segs):
    """RAW frame After Effects shows per comp frame (stretch segments, floor rule)."""
    out = []
    for s in segs:
        ks = np.arange(s.comp_in, s.comp_out)
        out.append(ps.ae_frame(s.raw_in_seconds, s.speed, ks, s.comp_in, C30, R2997))
    return np.concatenate(out)


def _slow_footage(fm, truth, width=1, rel=0.4):
    """Slow footage: every neighbour within `width` frames scores within delta_k of the best, so refine's soft
    range is argmax +- width (delta_k = soft_delta_min here: every best score is 0.99)."""
    n = fm.n
    fm.soft_lo, fm.soft_hi = truth - width, truth + width
    j0 = truth - 7
    cand = np.full((n, 15), 0.95, np.float32)
    cand[:, 7] = 0.99
    for dj in range(1, width + 1):
        cand[:, 7 - dj] = cand[:, 7 + dj] = 0.99 - rel * 0.001 * dj
    fm.cand_j0, fm.cand = j0, cand
    fm.margin = np.full(n, rel * 0.001)


@pytest.mark.parametrize("with_cand", [False, True])
def test_two_frame_skip_jump_cut_with_wide_soft_ranges_is_a_cut(with_cand):
    """time-math F2: with soft ranges of +-1 one 1.0x line fits a 2-frame-skip jump cut inside every soft range
    while contradicting every measured frame. The data term makes it a cut, and AE shows refine's argmax."""
    m1 = ff_select(45, 1.0, 1000)
    m2 = ff_select(45, 1.0, int(m1[-1]) + 3)          # 2 RAW frames skipped
    fm, _ = build_fm([Spec(m=m1, n=45), Spec(m=m2, n=45)])
    truth = np.asarray(fm.raw).copy()
    if with_cand:
        _slow_footage(fm, truth)
    else:
        fm.soft_lo, fm.soft_hi = truth - 1, truth + 1
    segs = run(fm, *proxies(fm.n))
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 45), (45, 90)]
    assert all(s.speed == 1.0 and not s.unsnapped for s in segs)
    assert np.array_equal(_ae(segs), truth)
    assert np.array_equal(np.asarray(fm.raw), truth)          # nothing re-assigned


@pytest.mark.parametrize("w", [0, 1])
def test_103x_segment_is_not_snapped_to_one(w):
    """time-math F2: a 1.03x segment is reported unsnapped at its measured speed (no snap value is within
    speed_snap_tol of the robust slope of the measured frames), not as 1.0 with frames off the argmax (soft
    +-1) or as 1.0 pieces joined by fake 1-frame jump cuts (exact ranges)."""
    fm, _ = build_fm([Spec(n=90, j0=1000, v=1.03)])
    truth = np.asarray(fm.raw).copy()
    if w:
        _slow_footage(fm, truth, w)
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1
    s = segs[0]
    tol = Config().speed_snap_tol
    assert s.speed != 1.0
    if s.unsnapped:
        assert abs(s.speed / 1.03 - 1.0) < 0.005
    else:
        assert abs(s.speed - s.speed_measured) <= tol * s.speed
    assert abs(s.speed_measured / 1.03 - 1.0) < 0.005             # robust slope of the ARGMAX frames
    assert s.speed_range[0] <= s.speed <= s.speed_range[1]
    assert not (s.speed_range[0] <= 1.0 <= s.speed_range[1]) and not (s.speed_range[0] <= 1.05 <= s.speed_range[1])
    assert np.array_equal(_ae(segs), truth)


def test_105x_segment_with_wide_soft_ranges_keeps_its_snap():
    """time-math F2: 1.05x over 40 frames with soft +-1: 1.0 fits the soft ranges but contradicts 19 measured
    frames; the segment must be 1.05 (the dominant speed no longer wins a cost tie by rank)."""
    fm, _ = build_fm([Spec(n=40, j0=1000, v=1.05)])
    truth = np.asarray(fm.raw).copy()
    _slow_footage(fm, truth)
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1 and segs[0].speed == pytest.approx(1.05) and not segs[0].unsnapped
    assert np.array_equal(_ae(segs), truth)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_random_argmax_noise_in_wide_soft_ranges_makes_no_cut(seed):
    """Guard for the data term (time-math F2): isolated random +-1 argmax errors (30 % of the frames, each
    beating the true frame by 0.8 delta) inside wide soft ranges must not buy fake 1-frame cuts; the one 1.0
    line of the truth is kept and the erroneous frames are re-assigned to it."""
    rng = np.random.default_rng(seed)
    n = 150
    fm, _ = build_fm([Spec(m=ff_select(n, 1.0, 1000), n=n)])
    truth = np.asarray(fm.raw).copy()
    noise = np.where(rng.random(n) < 0.3, rng.choice([-1, 1], n), 0)
    noise[0] = noise[-1] = 0
    am = truth + noise
    fm.raw = fm.raw_lo = fm.raw_hi = am
    fm.soft_lo, fm.soft_hi = np.minimum(am - 1, truth), np.maximum(am + 1, truth)
    fm.cand_j0 = am - 7
    cand = np.full((n, 15), 0.95, np.float32)
    cand[:, 7] = 0.99
    cand[:, 6] = cand[:, 8] = 0.99 - 0.8 * 0.001
    fm.cand, fm.margin = cand, np.full(n, 0.8 * 0.001)
    segs = run(fm, *proxies(n))
    assert [(s.comp_in, s.comp_out, s.speed) for s in segs] == [(0, n, 1.0)]
    assert np.array_equal(_ae(segs), truth)


def test_phantom_cut_after_criterion2_move_is_merged(tmp_path):
    """verification-honesty F3: refine's argmax at frame 30 is one frame late (pixels say otherwise) and the
    frames after it are ambiguous pairs. The DP cuts at 30; criterion 2 moves the cut, after which both models
    show the same RAW frame and framing on both sides. That is no discontinuity of m(k): no cut may remain."""
    bank = texture_bank(300, seed=11)
    truth = ff_select(60, 1.0, 20)
    comp = bank[truth]
    fm, _ = build_fm([Spec(m=truth, n=60)])
    raw = truth.copy()
    raw[30] = truth[30] + 1
    lo, hi = raw.copy(), raw.copy()
    lo[31:], hi[31:] = truth[31:], truth[31:] + 1
    fm.raw, fm.raw_lo, fm.raw_hi, fm.soft_lo, fm.soft_hi = raw, lo, hi, lo, hi
    j0 = np.full(60, -1)
    cand = np.full((60, 15), np.nan, np.float32)
    j0[30] = raw[30] - 7
    cand[30, :] = 0.95
    cand[30, 7], cand[30, 6] = 0.99, 0.96
    fm.cand_j0, fm.cand = j0, cand
    marg = np.full(60, 0.04)
    marg[30] = 0.03
    fm.margin = marg
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *pix_proxies(comp, bank), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out, s.speed) for s in segs] == [(0, 60, 1.0)]
    assert np.array_equal(_ae(segs), truth)
    rec = records(tmp_path / "d.jsonl")
    assert any(r["decision"] == "phantom_cut_merged" for r in rec)


def test_layout_periods_set_box_region_and_split_segments(tmp_path):
    """D1 / requirements REQ-3: a fullscreen period inside a boxed edit gives segments with box = the whole
    canvas and region 1, split exactly at the period boundaries even inside one continuous shot; a split-screen
    period is region 2 and flagged; the dominant layout keeps box None / region 0."""
    from match_cuts.model import Box, Layout, LayoutPeriod
    box = Box(60.0, 400.0, 960.0, 1000.0, 30.0)
    full = Box(0.0, 0.0, 1080.0, 1920.0, 0.0)
    m = ff_select(100, 1.0, 1000)                    # ONE continuous shot, constant framing, frames 0-99
    m2 = ff_select(20, 1.0, 3000)
    fm, _ = build_fm([Spec(m=m, n=100, sim=(0.5, 0.0, 60.0, 690.0)), Spec(kind="none", n=10),
                      Spec(m=m2, n=20, track=1, sim=(0.5, 0.0, 60.0, 690.0))])
    lay = Layout(1080, 1920, mode="boxed", box=box, periods=[
        LayoutPeriod(0, 40, "boxed", box), LayoutPeriod(40, 70, "fullscreen", full), LayoutPeriod(70, 85, "boxed", box),
        LayoutPeriod(85, 95, "split", box), LayoutPeriod(95, 115, "boxed", box), LayoutPeriod(115, 130, "fullscreen", full)])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), layout=lay, dlog=dl)
    dl.close()
    fullbox = {"x": 0.0, "y": 0.0, "w": 1080.0, "h": 1920.0, "corner_radius": 0.0}
    got = [(s.type, s.comp_in, s.comp_out, s.box, s.region) for s in segs]
    assert got == [("raw", 0, 40, None, 0), ("raw", 40, 70, fullbox, 1), ("raw", 70, 85, None, 0),
                   ("raw", 85, 95, None, 2), ("raw", 95, 100, None, 0), ("not_in_raw", 100, 110, None, 0),
                   ("raw", 110, 115, None, 0), ("raw", 115, 130, fullbox, 1)]
    rs = raws(segs)
    assert [s.raw_in_frame for s in rs] == [int(m[0]), int(m[40]), int(m[70]), int(m[85]), int(m[95]),
                                            int(m2[0]), int(m2[5])]
    assert all(s.speed == 1.0 for s in rs)
    assert "fullscreen layout period" in segs[1].notes and "'split' layout period" in segs[3].notes
    for s in rs:
        check_model(s, fm)
    # a fullscreen DOMINANT layout: every segment keeps box None / region 0
    lay2 = Layout(1080, 1920, mode="fullscreen", box=full, periods=[LayoutPeriod(0, 130, "fullscreen", full)])
    fm2, _ = build_fm([Spec(m=m, n=100), Spec(kind="none", n=10), Spec(m=m2, n=20, track=1)])
    segs2 = run(fm2, *proxies(fm2.n), layout=lay2)
    assert [(s.comp_in, s.comp_out, s.box, s.region) for s in segs2] == [(0, 100, None, 0), (100, 110, None, 0),
                                                                          (110, 130, None, 0)]


@pytest.mark.parametrize("start", [39, 41])
def test_layout_period_boundary_off_by_one_follows_the_cut(start):
    """D1 robustness: the detected fullscreen period starts one frame early / late relative to the hard cut
    into the fullscreen shot. The 1-frame sliver is merged into the neighbour with the same framing (across
    the period boundary), so the cut stays at 40 and each shot gets its own box."""
    from match_cuts.model import Box, Layout, LayoutPeriod
    box = Box(60.0, 400.0, 960.0, 1000.0, 30.0)
    full = Box(0.0, 0.0, 1080.0, 1920.0, 0.0)
    boxed_sim, full_sim = (0.5, 0.0, 60.0, 690.0), (1.8, 0.0, -1188.0, 0.0)
    fm, _ = build_fm([Spec(m=ff_select(40, 1.0, 1000), n=40, sim=boxed_sim),
                      Spec(m=ff_select(30, 1.0, 3000), n=30, sim=full_sim, track=1),
                      Spec(m=ff_select(30, 1.0, 5000), n=30, sim=boxed_sim, track=2)])
    lay = Layout(1080, 1920, mode="boxed", box=box, periods=[
        LayoutPeriod(0, start, "boxed", box), LayoutPeriod(start, 70, "fullscreen", full),
        LayoutPeriod(70, 100, "boxed", box)])
    segs = run(fm, *proxies(fm.n), layout=lay)
    fullbox = {"x": 0.0, "y": 0.0, "w": 1080.0, "h": 1920.0, "corner_radius": 0.0}
    assert [(s.comp_in, s.comp_out, s.box, s.region) for s in segs] == [(0, 40, None, 0), (40, 70, fullbox, 1),
                                                                        (70, 100, None, 0)]
    for s in segs:
        check_model(s, fm)


def test_full_affine_reported_when_similarity_is_poor():
    import cv2
    bank = texture_bank(120, seed=8)
    m = ff_select(30, 1.0, 20)
    A = np.array([[1.08, 0.0, -0.04 * W / 2], [0.0, 0.94, 0.03 * H / 2]])   # non-uniform scale (comp full px)
    from match_cuts.geometry import CORNER_TO_CV, CV_TO_CORNER, diag3, h3
    Mcv = (CORNER_TO_CV @ diag3(0.5, 0.5) @ h3(A) @ np.linalg.inv(diag3(0.5, 0.5)) @ CV_TO_CORNER)[:2]
    comp = np.stack([cv2.warpAffine(bank[j], Mcv, (W // 2, H // 2), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REFLECT) for j in m])
    s = (1.08 + 0.94) / 2
    fm, _ = build_fm([Spec(m=m, n=30, sim=(s, 0.0, A[0, 2], A[1, 2]))])
    fm.score = np.full(30, 0.85)
    segs = run(fm, *pix_proxies(comp, bank))
    assert len(segs) == 1 and "full affine fits clearly better" in segs[0].notes


def test_criterion2_oscillation_gets_an_honest_verdict(tmp_path, monkeypatch):
    """FX-05: criterion 2 used to run 3 iterations with no visited set and stop wherever the last move ended
    (606/607, 1444/1445 and 1760/1761 in the real run, identical evidence each time). Now a revisit stops the
    mover after at most 2 moves, every visited position is re-evaluated, the best summed score (A's frames under
    A + B's frames under B) is kept, and criterion2_fail records the oscillation with a segment note."""
    from match_cuts import segment as seg_mod
    a, b = ff_select(30, 1.0, 1000), ff_select(30, 1.0, 3000)
    fm, _ = build_fm([Spec(m=a, n=30), Spec(m=b, n=30, track=1)])
    comp, raw = proxies(fm.n)

    def side_scores(self, A, B, k):
        c = B.a
        if c == 30:          # A's last frame prefers B -> move the cut to 29
            return (0.5, 0.9)
        if c == 29:          # B's first frame prefers A -> move it back to 30
            return (0.9, 0.5)
        return (0.9, 0.5) if k < c else (0.5, 0.9)

    def frame_score(self, S, k, refine=False):      # the pixels: the true cut is at 30
        return 1.0 if (S.a == 0) == (k < 30) else 0.4

    monkeypatch.setattr(seg_mod._Builder, "_side_scores", side_scores)
    monkeypatch.setattr(seg_mod._Builder, "_frame_score", frame_score)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, comp, raw, dlog=dl)
    dl.close()
    rec = records(tmp_path / "d.jsonl")
    moves = [r for r in rec if r["decision"] == "criterion2_move"]
    fails = [r for r in rec if r["decision"] == "criterion2_fail"]
    assert len(moves) == 2, moves
    assert len(fails) == 1 and fails[0]["evidence"]["reason"] == "oscillation"
    ev = fails[0]["evidence"]
    assert ev["oscillation"] == [30, 29] and ev["cut"] == 30 and ev["scores"]["30"] > ev["scores"]["29"]
    assert "repeat_pair" in ev
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 30), (30, 60)]
    note = segs[1].notes
    assert "criterion 2 not satisfied" in note and "oscillated" in note and "kept 30" in note, note
    assert np.array_equal(_ae(segs), np.concatenate([a, b]))


def test_criterion2_moves_exhausted_keeps_the_best_position(tmp_path, monkeypatch):
    """The mover keeps failing in one direction (3 moves): the final position is checked too, and the best of
    the visited positions by summed score is kept and reported."""
    from match_cuts import segment as seg_mod
    a, b = ff_select(30, 1.0, 1000), ff_select(30, 1.0, 3000)
    fm, _ = build_fm([Spec(m=a, n=30), Spec(m=b, n=30, track=1)])
    comp, raw = proxies(fm.n)
    monkeypatch.setattr(seg_mod._Builder, "_side_scores", lambda self, A, B, k: (0.5, 0.9))   # always 'move earlier'
    monkeypatch.setattr(seg_mod._Builder, "_frame_score", lambda self, S, k, refine=False: 1.0 if (S.a == 0) == (k < 29) else 0.4)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, comp, raw, dlog=dl)
    dl.close()
    rec = records(tmp_path / "d.jsonl")
    fails = [r for r in rec if r["decision"] == "criterion2_fail"]
    assert len([r for r in rec if r["decision"] == "criterion2_move"]) == 3
    assert len(fails) == 1 and fails[0]["evidence"]["reason"] == "moves_exhausted"
    assert fails[0]["evidence"]["oscillation"] == [30, 29, 28, 27] and fails[0]["evidence"]["cut"] == 29
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 29), (29, 60)]
    assert "was moved over" in segs[1].notes and "kept 29" in segs[1].notes, segs[1].notes


# ---------------------------------------------------------------------------------------------------
# FX-04 / FX-06 / FX-07 (first real run): a cut must beat the continuous hypothesis, measured framing summary,
# framing steps at any pan speed, competitor repeat pairs
# ---------------------------------------------------------------------------------------------------

R24 = F(24000, 1001)
BOX_C = (540.0, 960.0)          # box centre of the test geometry (no layout: the comp centre, 1080 x 1920)


def _px(sim: Sim, ref: Sim) -> float:
    """Box-centre displacement (comp px) between two framings: |sim(ref^-1(c)) - c|."""
    p = ref.inverse().apply([BOX_C])[0]
    return float(np.hypot(*(sim.apply([p])[0] - np.asarray(BOX_C))))


class _StubScorer:
    """Pixel-free stand-in of segment._Scorer: score(k, RAW j, Sim) from a function (e.g. consistent with a known
    pan); blends and regions unavailable."""

    def __init__(self, fn):
        self.ok = True
        self.fn = fn
        self.roi = (0, 0, 8, 8)
        self.raw_w = 1920.0
        self.blur = 1.0

    def zncc_set(self, k, items):
        return np.array([self.fn(int(k), int(j), sim, bool(fl)) for j, sim, fl in items], np.float64)

    def blend_fit(self, *a, **kw):
        return None

    def region(self, k):
        return None

    def allowed(self, k):
        return np.ones((8, 8), bool)


def _stub(monkeypatch, fn):
    from match_cuts import segment as seg_mod
    monkeypatch.setattr(seg_mod, "_Scorer", lambda *a, **kw: _StubScorer(fn))


def _pan_sim(k: int, v_px: float = -3.0, s: float = 0.5, k_punch: int | None = None, z: float = 1.25) -> Sim:
    """A linear editor pan (tx moves v_px comp px per frame); from k_punch on punched in by z about the centre."""
    sim = Sim(s, 0.0, 60.0 + v_px * k, 690.0)
    if k_punch is not None and k >= k_punch:
        cx, cy = BOX_C
        sim = Sim(sim.s * z, 0.0, z * (sim.tx - cx) + cx, z * (sim.ty - cy) + cy)
    return sim


def _alternating_pan_fm(n: int = 16, k_punch: int | None = None):
    """FX-04 fixture: one v = 1 time line under a linear editor pan, as a confounded refine sees it: even frames on
    track 0 (right RAW frame, right framing), odd frames on track 1 (RAW + 1 with a framing 15 px off that
    compensates, confounded: soft range covers the line), two 1-frame +12 RAW excursions (track 2)."""
    m = ff_select(n, 1.0, 1000)
    sims, raw, tr, lo, hi = [], m.copy(), np.zeros(n, np.int64), m.copy(), m.copy()
    conf = np.zeros(n, bool)
    for k in range(n):
        s = _pan_sim(k, k_punch=k_punch)
        if k % 2:
            raw[k] = m[k] + 1
            s = s.translated(15.0, 0.0)
            tr[k], conf[k] = 1, True
            lo[k], hi[k] = m[k], m[k] + 1
        sims.append((s.s, s.theta_deg, s.tx, s.ty))
    for k in (5, 10):
        raw[k] = lo[k] = hi[k] = m[k] + 12
        tr[k], conf[k] = 2, False
        s = _pan_sim(k, k_punch=k_punch)
        sims[k] = (s.s, s.theta_deg, s.tx, s.ty)
    fm, _ = build_fm([Spec(m=raw, n=n, sim_fn=lambda i: sims[i])])
    fm.raw_lo = fm.raw_hi = raw
    fm.soft_lo, fm.soft_hi, fm.track, fm.confounded = lo, hi, tr, conf
    return fm, m


def _pan_score(m, k_punch=None):
    """Stub pixels consistent with the linear pan on the time line m: -0.03 per RAW frame off the line, -0.004 per
    comp px of framing error."""
    def fn(k, j, sim, flip):
        return 0.99 - 0.03 * abs(j - int(m[k])) - 0.004 * _px(sim, _pan_sim(k, k_punch=k_punch))
    return fn


def test_alternating_tracks_on_one_line_give_one_segment_with_two_keys(monkeypatch, tmp_path):
    """FX-04: alternating refine tracks (Sims 15 px apart, RAW +-1 confounded) and two 1-frame +12 excursions on one
    v = 1 line under a linear pan. Pixels (stub) consistent with the pan: no framing step is confirmed, the
    excursions are merged into the line (neighbour's framing extrapolated, not held), the re-assigned frames' foreign
    Sims are left out of the framing: ONE segment with the pan's 2 keys."""
    fm, m = _alternating_pan_fm()
    _stub(monkeypatch, _pan_score(m))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out, s.speed) for s in segs] == [(0, 16, 1.0)]
    assert np.array_equal(_ae(segs), m)
    keys = segs[0].transform_keys
    assert len(keys) == 2 and (keys[0]["comp_frame"], keys[1]["comp_frame"]) == (0, 14)
    for key in keys:
        assert _px(Sim.from_dict(key), _pan_sim(key["comp_frame"])) < 0.01
    rec = records(tmp_path / "d.jsonl")
    assert not any(r["decision"] == "transform_step" for r in rec)
    assert not any(r["decision"] == "flash_cut_verified" for r in rec)


def test_alternating_tracks_with_a_sustained_punch_give_two_segments(monkeypatch, tmp_path):
    """FX-04 / FX-06: the same fixture with a real sustained x1.25 punch at frame 8: the step is confirmed by the
    pixels (old framing wins before, new after) -> 2 segments on one time line (shared phase)."""
    fm, m = _alternating_pan_fm(k_punch=8)
    _stub(monkeypatch, _pan_score(m, k_punch=8))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 8), (8, 16)]
    assert np.array_equal(_ae(segs), m)
    assert segs[1].transform_keys[0]["scale"] == pytest.approx(0.625, rel=1e-6)
    rec = records(tmp_path / "d.jsonl")
    st = [r for r in rec if r["decision"] == "transform_step"]
    assert [r["comp_frame"] for r in st] == [8] and st[0]["evidence"]["method"] == "scored_both_transforms"
    assert any(r["decision"] == "time_tie" for r in rec)
    assert segs[1].raw_in_seconds == pytest.approx(segs[0].raw_in_seconds + 8 / 30, abs=1e-9)


def test_genuine_two_frame_stutter_keeps_its_cuts(monkeypatch, tmp_path):
    """FX-04 guard: a 2-frame stutter (the editor shows RAW 18-19 of the shot again, then continues on the line):
    the pixels say the repeated frames are what they are -- the cuts stay, a VERIFIED flash cut."""
    a = ff_select(20, 1.0, 1000)
    st = a[18:20].copy()
    b = ff_select(20, 1.0, int(a[-1]) + 3)          # C continues A's line
    m = np.concatenate([a, st, b])
    fm, bd = build_fm([Spec(m=a, n=20), Spec(m=st, n=2, track=1), Spec(m=b, n=20, track=2)])
    _stub(monkeypatch, lambda k, j, sim, fl: 0.99 - 0.03 * abs(j - int(m[k])))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 20), (20, 22), (22, 42)]
    assert np.array_equal(_ae(segs), m)
    rec = records(tmp_path / "d.jsonl")
    assert any(r["decision"] == "flash_cut_verified" and r["comp_range"] == [20, 22] for r in rec)


def test_short_segment_verdict_is_logged_only_for_final_segments(monkeypatch, tmp_path):
    """FX-12: merge_tiny's 'flash_cut_verified' / 'flash_cut_unverified' verdict is logged only for short segments that
    are still segments of the RESULT (the real run logged 26 verified flash cuts, several merged away later). A later
    step that replaces the short segment (here: a copy, as any merge creates a new segment) drops its verdict."""
    import copy
    from match_cuts import segment as seg_mod
    a = ff_select(20, 1.0, 1000)
    st = a[18:20].copy()
    b = ff_select(20, 1.0, int(a[-1]) + 3)
    m = np.concatenate([a, st, b])
    fm, bd = build_fm([Spec(m=a, n=20), Spec(m=st, n=2, track=1), Spec(m=b, n=20, track=2)])
    _stub(monkeypatch, lambda k, j, sim, fl: 0.99 - 0.03 * abs(j - int(m[k])))
    orig = seg_mod._Builder.merge_continuous

    def replaced(self, work):
        work = orig(self, work)
        return [copy.copy(S) if (S.a, S.b) == (20, 22) else S for S in work]
    monkeypatch.setattr(seg_mod._Builder, "merge_continuous", replaced)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 20), (20, 22), (22, 42)]
    rec = records(tmp_path / "d.jsonl")
    assert not any(r["decision"] in ("flash_cut_verified", "flash_cut_unverified") for r in rec)


def test_three_frame_skip_inside_a_pan_stays_a_cut(monkeypatch, tmp_path):
    """FX-04 guard: a +3 RAW frame jump cut inside an editor pan whose own frames clearly win (pixels), with the cut
    on a competitor repeat pair and confounded frames around it (union test triggered): the cut is kept."""
    a = ff_select(25, 1.0, 1000)
    b = ff_select(25, 1.0, int(a[-1]) + 4)
    m = np.concatenate([a, b])
    fm, _ = build_fm([Spec(m=a, n=25, sim_fn=lambda i: _tup(_pan_sim(i))),
                      Spec(m=b, n=25, track=1, sim_fn=lambda i: _tup(_pan_sim(25 + i)))])
    fm.pair_label = np.where(np.arange(fm.n) == 24, 1, 0)
    conf = np.zeros(fm.n, bool)
    conf[24:26] = True
    fm.confounded = conf

    def fn(k, j, sim, fl):
        return 0.99 - 0.02 * abs(j - int(m[k])) - 0.004 * _px(sim, _pan_sim(k))
    _stub(monkeypatch, fn)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 25), (25, 50)]
    rec = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "union_test"]
    assert rec and rec[0]["evidence"]["result"] == "cut_verified"


def test_unverifiable_sliver_is_flash_cut_unverified(tmp_path):
    """FX-04 5: a 1-frame segment nothing can test (no pixels, no candidate vector, another track) is no longer
    logged as a verified flash cut: 'flash_cut_unverified', the segment is uncertain and says why."""
    a = ff_select(30, 1.0, 1000)
    one = np.array([3000])
    b = ff_select(30, 1.0, int(a[-1]) + 2)
    fm, _ = build_fm([Spec(m=a, n=30), Spec(m=one, n=1, track=1), Spec(m=b, n=30, track=2)])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *proxies(fm.n), dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 30), (30, 31), (31, 61)]
    rec = records(tmp_path / "d.jsonl")
    assert any(r["decision"] == "flash_cut_unverified" for r in rec)
    assert not any(r["decision"] == "flash_cut_verified" for r in rec)
    assert segs[1].uncertain and "not verified as a flash cut" in segs[1].notes


def test_reframe_on_one_time_line_shares_raw_in():
    """FX-04 2: two segments on ONE v = 1 time line (RAW 23.976 on 30 fps) split by an editor reframe at a
    RAW-native shot change share one phase solve: the second segment's raw_in is the first's line at its comp_in,
    its interval the shifted shared interval."""
    m = np.asarray([int(math.floor(k * float(R24) / 30.0 + 1e-9)) + 400 for k in range(60)])
    s0, s1 = (0.5, 0.0, 60.0, 690.0), (0.55, 0.0, 6.0, 594.0)
    fm, _ = build_fm([Spec(m=m[:25], n=25, sim=s0), Spec(m=m[25:], n=35, sim=s1, track=1)])
    comp = Proxy("competitor", "", None, (1080, 1920), (0.5, 0.5), C30, np.arange(fm.n) / 30.0, fm.n)
    raw = Proxy("raw", "", None, (1920, 1080), (1 / 3, 1 / 3), R24, np.zeros(1), 200000)
    segs = run(fm, comp, raw)
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 25), (25, 60)]
    A, B = segs
    assert B.raw_in_seconds == pytest.approx(A.raw_in_seconds + 25 / 30.0, abs=1e-9)
    assert B.raw_in_interval[0] == pytest.approx(A.raw_in_interval[0] + 25 / 30.0, abs=1e-9)
    assert B.raw_in_interval[1] == pytest.approx(A.raw_in_interval[1] + 25 / 30.0, abs=1e-9)
    pred = np.concatenate([ps.ae_frame(s.raw_in_seconds, 1.0, np.arange(s.comp_in, s.comp_out), s.comp_in, C30, R24)
                           for s in segs])
    assert np.array_equal(pred, m)
    assert "time line shared" in B.notes


def test_solve_shared_raw_in_matches_one_solve():
    """phase_solve.solve_shared_raw_in == solve_raw_in of the union, values moved to every part's comp_in."""
    m = np.asarray([int(math.floor(k * float(R24) / 30.0 + 1e-9)) + 700 for k in range(50)])
    ks = np.arange(50)
    parts = [(ks[:20], m[:20], m[:20], 0), (ks[20:], m[20:], m[20:], 20)]
    sols = ps.solve_shared_raw_in(parts, 1.0, C30, R24)
    one = ps.solve_raw_in(ks, m, m, 0, 1.0, C30, R24)
    assert sols[0]["raw_in"] == pytest.approx(one["raw_in"], abs=1e-12)
    assert sols[1]["raw_in"] == pytest.approx(one["raw_in"] + 20 / 30.0, abs=1e-12)
    assert sols[1]["margin_ms"] == pytest.approx(one["margin_ms"])
    assert sols[1]["shared"] == {"comp_in": 0, "parts": 2, "raw_in": pytest.approx(one["raw_in"])}
    assert len(sols[1]["frame_slack"]) == 30
    for s, (k_, _lo, _hi, c) in zip(sols, parts):
        assert np.array_equal(ps.ae_frame(s["raw_in"], 1.0, k_, c, C30, R24), m[k_])


# -- FX-06 with pixels: textured RAW, editor pans as warps -------------------------------------------------

def _pan_proxies(m: np.ndarray, sim_of, seed: int = 21):
    bank = texture_bank(int(m.max()) + 2, seed=seed)
    comp = np.stack([_warp(bank[j], sim_of(k)) for k, j in enumerate(m)])
    return pix_proxies(comp, bank), bank


def _tup(s: Sim) -> tuple:
    return (s.s, s.theta_deg, s.tx, s.ty)


def test_s60_step_back_inside_a_pan_is_a_cut(tmp_path):
    """FX-06 (S60 replica): a slow editor pan (-0.92 comp px per frame) that snaps back to its start framing at a
    RAW-native cut, on ONE continuous time line. The detrended detector sees the step whatever the pan speed, the
    pixels confirm it: a cut at the step (shared raw_in), framing within 0.5 px on every frame (was 20.9 px)."""
    K, n = 30, 50
    m = ff_select(n, 1.0, 20)

    def truth(k):
        return Sim(1.3, 0.0, -28.8 - 0.92 * (k if k < K else 0), -19.2)
    (cp, rp), _bank = _pan_proxies(m, truth)
    fm, _ = build_fm([Spec(m=m[:K], n=K, sim_fn=lambda i: _tup(truth(i))),
                      Spec(m=m[K:], n=n - K, sim_fn=lambda i: _tup(truth(K + i)), track=1)])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, cp, rp, dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, K), (K, n)]
    assert segs[1].raw_in_seconds == pytest.approx(segs[0].raw_in_seconds + K / 30.0, abs=1e-9)
    from match_cuts.geometry import interpolate_keys
    for s in segs:
        for k in range(s.comp_in, s.comp_out):
            got = interpolate_keys(s.transform_keys, k, W, H) if s.transform_keys else Sim.from_dict(s.transform)
            p = truth(k).inverse().apply([[W / 2, H / 2]])[0]
            assert float(np.hypot(*(got.apply([p])[0] - [W / 2, H / 2]))) < 0.5, k
    st = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "transform_step"]
    assert [r["comp_frame"] for r in st] == [K] and st[0]["evidence"]["method"] == "scored_both_transforms"


def test_s93_punch_then_fast_pan_gets_a_measured_key_at_the_step(tmp_path):
    """FX-06 (S93 replica): a x1.71 punch at k0 followed by a -6.35 px/frame pan; refine's FrameMap ramps over
    k0..k0+4 (ka = k0 - 1, kb = k0 + 5). Only the transition frames before the localised cut lose their Sims; the
    frames after it are measured again by ECC, so the punched segment has a key AT k0 within 0.5 px of the truth
    (AE used to hold the k0 + 5 key backwards over k0..k0+4: 31 px)."""
    k0, n = 20, 40
    m = ff_select(n, 1.0, 30)
    a = Sim(1.0, 0.0, 0.0, 0.0)

    def truth(k):
        if k < k0:
            return a
        z = 1.712
        return Sim(z, 0.0, (1 - z) * W / 2 - 6.35 * 0.2 * (k - k0), (1 - z) * H / 2)
    (cp, rp), _bank = _pan_proxies(m, truth)

    def fm_sim(k):         # refine's ramp: the Sims interpolated between k0 - 1 and k0 + 5
        if k0 - 1 < k < k0 + 5:
            u = (k - (k0 - 1)) / 6.0
            A, B = truth(k0 - 1), truth(k0 + 5)
            return (A.s + u * (B.s - A.s), 0.0, A.tx + u * (B.tx - A.tx), A.ty + u * (B.ty - A.ty))
        return _tup(truth(k))
    fm, _ = build_fm([Spec(m=m, n=n, sim_fn=fm_sim)])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, cp, rp, dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, k0), (k0, n)]
    st = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "transform_step"]
    assert st and st[0]["evidence"]["from"] == k0 - 1 and st[0]["evidence"]["to"] == k0 + 5
    keys = segs[1].transform_keys
    assert keys and keys[0]["comp_frame"] == k0
    c = [W / 2, H / 2]
    for k in range(k0, n):
        from match_cuts.geometry import interpolate_keys
        got = interpolate_keys(keys, k, W, H)
        p = truth(k).inverse().apply([c])[0]
        assert float(np.hypot(*(got.apply([p])[0] - c))) < 0.5, k


def test_single_rotated_sample_does_not_tilt_the_segment():
    """FX-06 5: 11 samples at theta = 0 plus one wrong-frame measurement at 0.92 deg (with the compensating shift):
    every key has theta = 0 (the old 'any sample > 0.2 deg' vote tilted S93 by 0.92 / 0.31 deg)."""
    def sim(i):
        s = _pan_sim(i, v_px=-2.0)
        if i == 6:
            s = Sim(s.s, 0.92, s.tx + 9.0, s.ty - 4.0)
        return _tup(s)
    fm, _ = build_fm([Spec(m=ff_select(12, 1.0, 500), n=12, sim_fn=sim)])
    segs = run(fm, *proxies(fm.n))
    assert len(segs) == 1
    s = segs[0]
    assert s.transform["rotation_deg"] == 0.0 and all(k["rotation_deg"] == 0.0 for k in s.transform_keys)
    assert len(s.transform_keys) == 2
    for key in s.transform_keys:
        assert _px(Sim.from_dict(key), _pan_sim(key["comp_frame"], v_px=-2.0)) < 0.01


def test_reassigned_frame_framing_is_measured_again(tmp_path):
    """FX-06 1: refine put frame 15 on RAW m+1 with a Sim fitted to THAT frame (soft range m..m+1); the segment shows
    m there. The foreign Sim is not a sample of the segment's framing: frame 15 is measured again by ECC on the
    shown frame, and the segment's framing explains it (side score >= 0.95; the merge at 1444 gave 0.685)."""
    n = 30
    m = ff_select(n, 1.0, 40)

    def truth(k):
        return Sim(1.3, 0.0, -28.8 - 0.9 * k, -19.2)
    (cp, rp), bank = _pan_proxies(m, truth, seed=23)
    fm, _ = build_fm([Spec(m=m, n=n, sim_fn=lambda i: _tup(truth(i)) if i != 15 else
                           _tup(truth(i).translated(10.0, -6.0)))])
    raw = np.asarray(fm.raw).copy()
    raw[15] = m[15] + 1
    lo, hi = raw.copy(), raw.copy()
    lo[15] = m[15]
    fm.raw, fm.raw_lo, fm.raw_hi, fm.soft_lo, fm.soft_hi = raw, raw, raw, lo, hi
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, cp, rp, dlog=dl)
    dl.close()
    assert len(segs) == 1 and np.array_equal(_ae(segs), m)
    s = segs[0]
    assert "framing measured again on frames 15" in s.notes
    from match_cuts.geometry import interpolate_keys
    from match_cuts.scoring import prepare_comp, score_candidates
    got = interpolate_keys(s.transform_keys, 15, W, H) if s.transform_keys else Sim.from_dict(s.transform)
    p = truth(15).inverse().apply([[W / 2, H / 2]])[0]
    assert float(np.hypot(*(got.apply([p])[0] - [W / 2, H / 2]))) < 0.5
    reg = prepare_comp(cp.get(15), (0, 0, W // 2, H // 2), np.ones((H // 2, W // 2), bool), 1.0, with_grad=False)
    z = score_candidates(reg, [bank[m[15]]], got, False, float(W), (0.5, 0.5), (0.5, 0.5), blur=1.0)[0]
    assert z >= 0.95
    # the FrameMap gets the consistent pair: the frame now shown and the framing measured on it
    w = Sim(float(fm.s[15]), float(fm.theta[15]), float(fm.tx[15]), float(fm.ty[15]))
    assert int(fm.raw[15]) == int(m[15]) and float(np.hypot(*(w.apply([p])[0] - [W / 2, H / 2]))) < 0.5


def test_held_track_switch_inside_a_linear_pan_is_no_step(tmp_path):
    """FX-06 4: a linear editor pan whose FrameMap holds two tracks (40 px apart at the switch): compared
    EXTRAPOLATED and scored on the pixels, neither held framing wins on both sides -> no framing step, one
    segment (only a DP candidate)."""
    n, K = 40, 20
    m = ff_select(n, 1.0, 60)

    def truth(k):
        return Sim(1.3, 0.0, -28.8 - 0.6 * k, -19.2)
    (cp, rp), _bank = _pan_proxies(m, truth, seed=24)
    held = {0: truth(K - 20), 1: truth(K + 20)}
    fm, _ = build_fm([Spec(m=m[:K], n=K, sim=_tup(held[0])), Spec(m=m[K:], n=n - K, sim=_tup(held[1]), track=1)])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, cp, rp, dlog=dl)
    dl.close()
    rec = records(tmp_path / "d.jsonl")
    assert not any(r["decision"] == "transform_step" for r in rec)
    assert any(r["decision"] == "transform_step_unconfirmed" for r in rec)
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, n)]


# -- FX-07: competitor repeat pairs ---------------------------------------------------------------------

def test_cut_inside_a_repeat_pair_faces_the_continuous_line(monkeypatch, tmp_path):
    """FX-07 (a), the S77/S78 pattern: on a 23.976 -> 30 line, from the second frame of a competitor REPEAT pair
    on, refine measured one RAW frame back (2053 -> 2052, narrow soft ranges): the DP has to cut inside the pair. The
    union test (triggered by the repeat pair) finds the continuous line within the noise (slow content) and the
    repeat pair decides: ONE segment."""
    n = 40
    m = np.asarray([int(math.floor(k * float(R24) / 30.0 + 1e-9)) + 2030 for k in range(n)])
    rep = [k for k in range(1, n) if m[k] == m[k - 1]]
    c = rep[3]                                  # frames (c-1, c) show one RAW frame
    raw = m.copy()
    raw[c:] = m[c:] - 1                      # from the pair's second frame on: one frame back
    fm, _ = build_fm([Spec(m=raw, n=n)])
    fm.pair_label = np.where(np.arange(n) == c - 1, 1, 2)
    comp = Proxy("competitor", "", None, (1080, 1920), (0.5, 0.5), C30, np.arange(n) / 30.0, n)
    rawp = Proxy("raw", "", None, (1920, 1080), (1 / 3, 1 / 3), R24, np.zeros(1), 200000)
    _stub(monkeypatch, lambda k, j, sim, fl: 0.99 - 0.0005 * abs(j - int(m[k])))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, comp, rawp, dlog=dl)
    dl.close()
    assert [(s.comp_in, s.comp_out) for s in segs] == [(0, n)]
    pred = ps.ae_frame(segs[0].raw_in_seconds, 1.0, np.arange(n), 0, C30, R24)
    assert np.array_equal(pred, m)
    u = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "union_test"]
    assert u and u[0]["evidence"]["result"] == "merged" and "repeat_pair" in u[0]["evidence"]["triggers"]


def test_repeat_pair_cut_costs_more_in_the_dp():
    """FX-07 (a) soft evidence: the DP charges lambda_repeat_cut on top of lambda_cut for a cut between the two
    frames of a competitor REPEAT pair."""
    from match_cuts import segment as seg_mod
    fm, _ = build_fm([Spec(m=ff_select(20, 1.0, 100), n=20)])
    fm.pair_label = np.where(np.arange(20) == 9, 1, 0)
    b = seg_mod._Builder(fm, *proxies(20), None, None, cfg_(), None, None, None)
    assert b.cut_cost(10) == pytest.approx(2.0) and b.cut_cost(11) == pytest.approx(1.0)


def test_none_frame_of_a_repeat_pair_takes_its_partners_raw_frame(tmp_path):
    """FX-07 (c) comp-duplicate invariant: frame 19 is NONE (no anchor) but repeats frame 20, which the next segment
    matches: identical frames score alike under the partner's (RAW frame, framing) -> 19 shows the same RAW frame
    and the 1-frame placeholder disappears (the 1191 = 1192 case of the first real run)."""
    bank = texture_bank(400, seed=31)
    a = ff_select(20, 1.0, 10)
    b = np.asarray([int(math.floor(k * float(R24) / 30.0 + 1e-9)) + 200 for k in range(25)])
    first = next(k for k in range(1, 25) if b[k] == b[k - 1])
    b = b[first - 1:]                       # B starts with a repeat pair: frames 19 (NONE) and 20 show b[0]
    nb = len(b)
    comp = np.concatenate([bank[a[:19]], bank[[b[0]]], bank[b[1:]]])
    fm, _ = build_fm([Spec(m=a[:19], n=19), Spec(kind="none", n=1), Spec(m=b[1:], n=nb - 1, track=1)])
    fm.pair_label = np.where(np.arange(fm.n) == 19, 1, 2)
    cp = Proxy("competitor", "", comp, (W, H), (0.5, 0.5), C30, np.arange(fm.n) / 30.0, fm.n)
    rp = Proxy("raw", "", bank, (W, H), (0.5, 0.5), R24, np.zeros(1), len(bank))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, cp, rp, dlog=dl)
    dl.close()
    assert [(s.type, s.comp_in, s.comp_out) for s in segs] == [("raw", 0, 19), ("raw", 19, fm.n)]
    assert int(fm.raw[19]) == int(b[0]) and int(fm.status[19]) == Status.MATCH
    rec = records(tmp_path / "d.jsonl")
    assert any(r["decision"] == "repeat_pair_absorbed" and r["comp_frame"] == 19 for r in rec)


def test_scene_change_with_an_unrepresented_framing_step_is_reported(tmp_path, monkeypatch):
    """FX-06 6: a PySceneDetect change inside one segment is no longer asserted to be 'no transform change': refine's
    per-frame measurement jumps 40 px there while the path (and so the segment's model) does not -> reported as
    'framing step not represented' (and the change is a DP candidate); a time-continuous cut is never called a
    'same-shot jump cut'."""
    from match_cuts import segment as seg_mod
    clip = tmp_path / "comp.mp4"
    clip.write_bytes(b"\0")
    monkeypatch.setattr(seg_mod, "scenedetect_changes", lambda path, cfg: [20, 45])
    s0 = (0.5, 0.0, 60.0, 690.0)
    m = ff_select(60, 1.0, 300)
    fm, _ = build_fm([Spec(m=m[:40], n=40, sim=s0), Spec(m=m[40:], n=20, sim=(0.55, 0.0, 6.0, 594.0), track=1)])
    meas = np.tile(np.asarray(s0, np.float64), (fm.n, 1))
    meas[20:40, 2] += 40.0                                    # measured: a 40 px step at 20 the path smoothed away
    meas[40:] = (0.55, 0.0, 6.0, 594.0)
    fm.sim_meas, fm.sim_meas_score = meas, np.full(fm.n, 0.99)
    comp, raw = proxies(fm.n, path=str(clip))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = build_segments(fm, comp, raw, None, None, cfg_(), dl, None)
    dl.close()
    rec = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "scenedetect_crosscheck"][0]["evidence"]
    assert rec["framing_steps_not_represented"] == [20]
    assert any("framing step not represented" in (s.notes or "") for s in segs)
    miss = {d["cut"]: d["explanation"] for d in rec["cuts_not_detected"]}
    assert 40 in miss and "jump cut" not in miss[40] and "one time line" in miss[40]


def test_scene_changes_at_caption_events_are_explained_once_per_segment(tmp_path, monkeypatch):
    """FX-12: PySceneDetect changes inside a segment at the layout's caption event boundaries are explained
    specifically ('caption event boundary') in ONE aggregated note per segment, not one generic sentence each."""
    from match_cuts import segment as seg_mod
    from match_cuts.model import Layout
    clip = tmp_path / "comp.mp4"
    clip.write_bytes(b"\0")
    monkeypatch.setattr(seg_mod, "scenedetect_changes", lambda path, cfg: [12, 30])
    fm, _ = build_fm([Spec(m=ff_select(60, 1.0, 300), n=60)])
    comp, raw = proxies(fm.n, path=str(clip))
    lay = Layout(comp_w=int(comp.full_size[0]), comp_h=int(comp.full_size[1]),
                 captions=[{"type": "captions", "comp_in": 12, "comp_out": 30, "x": 10.0, "y": 10.0, "w": 50.0, "h": 10.0}])
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = build_segments(fm, comp, raw, lay, None, cfg_(), dl, None)
    dl.close()
    assert len(segs) == 1
    notes = segs[0].notes
    assert notes.count("PySceneDetect changes inside") == 1 and "12, 30 caption event boundary" in notes, notes
    rec = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "scenedetect_crosscheck"][0]["evidence"]
    assert [u["category"] for u in rec["unexplained"]] == ["caption event boundary"] * 2


def test_placeholder_match_split_across_a_repeat_pair_is_flagged(tmp_path):
    """FX-07 (c): the competitor labels frames 19/20 a REPEAT pair, but frame 19 (NONE) does not score like its
    partner under the partner's RAW frame and framing -> it is not absorbed, and the comp-duplicate invariant reports
    the placeholder / match split (comp_duplicate_conflict + a segment note) instead of passing it silently."""
    bank = texture_bank(400, seed=33)
    other = texture_bank(1, seed=34)[0]
    a = ff_select(19, 1.0, 10)
    b = ff_select(30, 1.0, 200)
    comp = np.concatenate([bank[a], other[None], bank[b]])
    fm, _ = build_fm([Spec(m=a, n=19), Spec(kind="none", n=1), Spec(m=b, n=30, track=1)])
    fm.pair_label = np.where(np.arange(fm.n) == 19, 1, 2)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = run(fm, *pix_proxies(comp, bank), dlog=dl)
    dl.close()
    assert [(s.type, s.comp_in, s.comp_out) for s in segs] == [("raw", 0, 19), ("not_in_raw", 19, 20), ("raw", 20, 50)]
    rec = records(tmp_path / "d.jsonl")
    assert any(r["decision"] == "repeat_pair_not_absorbed" and r["comp_frame"] == 19 for r in rec)
    conf = [r for r in rec if r["decision"] == "comp_duplicate_conflict"]
    assert conf and conf[0]["evidence"]["pairs"][0]["pair"] == [19, 20]
    assert "competitor frames 19/20 are identical" in segs[2].notes


@pytest.mark.parametrize("gap", [0.0005, 0.02])
def test_union_test_for_a_callers_cut(monkeypatch, tmp_path, gap):
    """FX-04 3 / FX-09: a cut the caller names (union_cuts, e.g. a large J/L where two lines meet) faces the
    continuous hypothesis. B's measured line is two RAW frames behind A's: when the pixels prefer A's line by more
    than the noise on B's frames the cut goes; inside the noise with no independent evidence (no repeat pair, no
    audio) it stays and is reported uncertain -- never silently."""
    a = ff_select(30, 1.0, 1000)
    line = ff_select(60, 1.0, 1000)
    b = line[30:] - 2                      # B shows two RAW frames again (no single line holds both)
    fm, _ = build_fm([Spec(m=a, n=30), Spec(m=b, n=30, track=1)])
    _stub(monkeypatch, lambda k, j, sim, fl: 0.99 - gap * abs(j - int(line[k])))
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = build_segments(fm, *proxies(fm.n), None, None, cfg_(), dl, None, union_cuts=[30])
    dl.close()
    u = [r for r in records(tmp_path / "d.jsonl") if r["decision"] == "union_test"]
    assert u and "caller" in u[0]["evidence"]["triggers"]
    if gap > 0.01:
        assert [(s.comp_in, s.comp_out) for s in segs] == [(0, 60)] and u[0]["evidence"]["result"] == "merged"
        assert np.array_equal(_ae(segs), line)
    else:      # (criterion 2 may have moved the near-tie cut a few frames: the caller's trigger follows it)
        assert len(segs) == 2 and u[0]["evidence"]["result"] == "undecided"
        assert segs[1].uncertain and "not decidable" in segs[1].notes


# ---------------------------------------------------------------------------------------------
# FX-08: honest NOT-IN-RAW / UNRESOLVED / freeze decisions
# ---------------------------------------------------------------------------------------------

def _unresolved(fm: FrameMap, a: int, b: int, j0: int, scores) -> None:
    """Frames [a, b) UNRESOLVED: best hypotheses RAW j0.. at the given gray-zone scores (refine's columns)."""
    half = fm.cand.shape[1] // 2
    for i, k in enumerate(range(a, b)):
        fm.status[k] = Status.UNRESOLVED
        fm.raw[k] = fm.raw_lo[k] = fm.raw_hi[k] = fm.soft_lo[k] = fm.soft_hi[k] = -1
        fm.score[k] = float(scores[i])
        fm.cand_j0[k] = j0 + i - half
        fm.track[k] = 9
        fm.s[k], fm.theta[k], fm.tx[k], fm.ty[k] = 1.0, 0.0, 0.0, 0.0


def test_unresolved_run_is_an_uncertain_segment_and_a_low_run_a_placeholder():
    """A 12-frame run at 0.70-0.88 is ONE 'uncertain' segment (no placeholder, no match) with its evidence and a
    label; a run whose every hypothesis is < none_thresh stays an exact NOT-IN-RAW placeholder."""
    a, b, c = ff_select(40, 1.0, 300), ff_select(30, 1.0, 1200), ff_select(30, 1.0, 2500)
    fm, _ = build_fm([Spec(m=a, n=40), Spec(kind="none", n=12), Spec(m=b, n=30, track=1), Spec(kind="none", n=10),
                      Spec(m=c, n=30, track=2)])
    _unresolved(fm, 40, 52, 700, np.linspace(0.70, 0.88, 12))
    fm.score[82:92] = 0.45
    segs = run(fm, *proxies(fm.n))
    assert [(s.type, s.comp_in, s.comp_out) for s in segs] == [
        ("raw", 0, 40), ("uncertain", 40, 52), ("raw", 52, 82), ("not_in_raw", 82, 92), ("raw", 92, 122)]
    u = segs[1]
    assert u.label.startswith("UNCERTAIN - best RAW 700-711, ZNCC 0.70-0.88") and u.uncertain
    assert u.audio["exception"] == "uncertain" and u.confidence == 0.0 and u.raw_in_seconds is None
    assert [e["raw"] for e in u.evidence] == list(range(700, 712)) and u.transform is not None
    assert segs[3].audio["exception"] == "not_in_raw"
    assert all(fm.status[k] == Status.UNRESOLVED for k in range(40, 52))


def test_short_none_run_inside_an_unresolved_stretch_joins_it():
    a, c = ff_select(30, 1.0, 300), ff_select(30, 1.0, 2500)
    fm, _ = build_fm([Spec(m=a, n=30), Spec(kind="none", n=12), Spec(m=c, n=30, track=2)])
    _unresolved(fm, 30, 35, 700, [0.7] * 5)
    _unresolved(fm, 37, 42, 707, [0.7] * 5)
    segs = run(fm, *proxies(fm.n))
    assert [(s.type, s.comp_in, s.comp_out) for s in segs] == [("raw", 0, 30), ("uncertain", 30, 42), ("raw", 42, 72)]
    assert [e["raw"] for e in segs[1].evidence][5:7] == [-1, -1]


class _Ov:
    """Overlay masks of an animated caption (comp proxy pixels)."""

    def __init__(self, masks: dict):
        self.masks = masks

    def get(self, k):
        return self.masks.get(k)


def _freeze_scene(moving: bool, caption: bool = False):
    """20 frames of v = 1 on RAW 100.., then 10 frames refine measured all on RAW 125 (a freeze candidate), then 20
    frames elsewhere. moving: a dark bar crosses the competitor's frames 6 px per frame (content motion no editor
    transform compensates; the RAW does not hold it); caption: an animated white bar over the (static) freeze,
    masked by the overlay masks."""
    bank = texture_bank(400, seed=11)
    a, c = ff_select(20, 1.0, 100), ff_select(20, 1.0, 300)
    fm, _ = build_fm([Spec(m=a, n=20), Spec(kind="freeze", n=10, j0=125, track=1), Spec(m=c, n=20, track=2)])
    comp = [bank[j] for j in a]
    ov = {}
    rng = np.random.default_rng(3)
    for i in range(10):
        img = bank[125].copy()
        if moving:
            img[:, 5 + 6 * i:15 + 6 * i] = 10
        img = np.clip(img.astype(np.float32) + rng.normal(0.0, 0.4, img.shape), 0, 255).round().astype(np.uint8)
        if caption:
            img = img.copy()
            img[20:34, 30:30 + 8 * (i + 1)] = 250
            m = np.zeros(img.shape, bool)
            m[18:36, 28:120] = True
            ov[20 + i] = m
        comp.append(img)
    comp += [bank[j] for j in c]
    return fm, np.stack(comp), bank, ov


@pytest.mark.parametrize("caption", [False, True])
def test_true_freeze_is_admitted(tmp_path, caption):
    fm, comp, bank, ov = _freeze_scene(moving=False, caption=caption)
    cp, rp = pix_proxies(comp, bank)
    segs = build_segments(fm, cp, rp, None, _Ov(ov) if caption else None, cfg_(), None, None)
    fz = [s for s in segs if s.type == "raw" and s.speed == 0.0]
    assert len(fz) == 1 and fz[0].comp_in <= 20 and fz[0].comp_out == 30, [(s.type, s.comp_in, s.comp_out, s.speed)
                                                                         for s in segs]


def test_freeze_is_rejected_when_the_competitor_moves(tmp_path):
    """Soft ranges all contain RAW 125, but the competitor's frames move: v = 0 is infeasible (not just costlier) and
    the held still is reported as an 'uncertain' stretch, never a freeze or a near-zero speed shown as exact."""
    fm, comp, bank, _ = _freeze_scene(moving=True)
    cp, rp = pix_proxies(comp, bank)
    dl = DecisionLog(tmp_path / "d.jsonl")
    segs = build_segments(fm, cp, rp, None, None, cfg_(), dl, None)
    dl.close()
    assert not [s for s in segs if s.type == "raw" and s.speed == 0.0]
    cover = [s for s in segs if s.comp_in < 30 and s.comp_out > 20]
    assert all(s.type == "uncertain" for s in cover if s.comp_in >= 20), [(s.type, s.comp_in, s.comp_out) for s in segs]
    assert any(s.type == "uncertain" for s in cover)
    recs = records(tmp_path / "d.jsonl")
    names = {r["decision"] for r in recs}
    assert "freeze_not_static" in names
    # the refused freeze span is summarised once, after the segments record
    rej = [r for r in recs if r["decision"] == "freeze_rejected"]
    assert len(rej) == 1 and any(a <= 25 < b for a, b in rej[0]["evidence"]["spans"]), rej


def test_pulldown_cadence_is_not_static():
    """A dimming display on a near-static shot at v = 1 (23.976 -> 30): exact repeats every 5th pair with small
    changes between them is a cadence (v = 1), not a freeze -- even though every change is tiny."""
    from match_cuts.segment import _Builder
    fm, _ = build_fm([Spec(m=ff_select(30, 1.0, 100), n=30)])
    fm.pair_label[np.arange(2, 30, 5)] = 1
    base = texture_bank(1, seed=2)[0].astype(np.float32)
    frames, level = [], 0.0
    for k in range(30):
        if k == 0 or (k - 1) % 5 != 2:          # a repeat pair (k-1, k) keeps the image
            level += 1.0
        img = base.copy()
        img[40:70, 40:120] = np.clip(img[40:70, 40:120] - level, 0, 255)
        frames.append(img.astype(np.uint8))
    cp, rp = pix_proxies(np.stack(frames), texture_bank(200, seed=3))
    b = _Builder(fm, cp, rp, None, None, cfg_(freeze_static_mad=1.0), None, None, None)
    assert b.static_floor(0, 30) is not None
    assert not b.static(5, 25)


def test_freeze_at_a_single_point_tie_is_never_a_freeze():
    """Frames measured RAW j and j + 1 (both exact) cannot share a v = 0 segment (FX-08, phase_solve.freeze_gap)."""
    m = np.array([1672, 1672] + [1673] * 8)
    a, c = ff_select(20, 1.0, 1500), ff_select(20, 1.0, 1800)
    fm, _ = build_fm([Spec(m=a, n=20), Spec(m=m, n=10, track=1), Spec(m=c, n=20, track=2)])
    segs = run(fm, *proxies(fm.n))
    assert not [s for s in segs if s.type == "raw" and s.speed == 0.0 and s.comp_in <= 20 and s.comp_out >= 22]


def test_frame_blend_slow_motion_snaps_to_025_with_a_verified_path():
    """A 0.25x frame-blended slow motion (ffmpeg framerate blend: linear in the fractional source position): the
    single-frame argmax picks the heavier frame and bends the measured speed; the blends' positions give the
    path, its speed snaps to 0.25 (retime_snap_values) and every frame matches the path's Frame Mix ->
    retime 'frame_blend' with linear remap keys; AE's floor rule shows the pure frames exactly."""
    bank = texture_bank(400, seed=7)
    n = 40
    u = 0.25 * float(R2997) / 30.0
    x0 = 150.0
    p = x0 + u * np.arange(n)
    comp, col = [], []
    for pk in p:
        j, f = int(np.floor(pk + 1e-9)), float(pk - np.floor(pk + 1e-9))
        comp.append((bank[j].astype(np.float32) * (1 - f) + bank[j + 1].astype(np.float32) * f).astype(np.uint8))
        col.append(int(np.floor(pk + 0.5)))
    a, c = ff_select(20, 1.0, 20), ff_select(20, 1.0, 300)
    fm, _ = build_fm([Spec(m=a, n=20), Spec(m=np.array(col), n=n, track=1), Spec(m=c, n=20, track=2)])
    comp = [bank[j] for j in a] + comp + [bank[j] for j in c]
    segs = run(fm, *pix_proxies(np.stack(comp), bank))
    s = next(s for s in segs if s.comp_in <= 25 < s.comp_out)
    assert (s.type, s.comp_in, s.comp_out) == ("raw", 20, 60), [(x.type, x.comp_in, x.comp_out, x.speed) for x in segs]
    assert s.speed == 0.25 and s.retime == "frame_blend" and s.time_remap_keys and "verified" in s.notes
    k0, k1 = s.time_remap_keys[0], s.time_remap_keys[-1]
    for i, pk in enumerate(p):
        k = 20 + i
        val = k0["raw_seconds"] + (k1["raw_seconds"] - k0["raw_seconds"]) * (k - k0["comp_frame"]) / (
            k1["comp_frame"] - k0["comp_frame"])
        assert abs(val * float(R2997) - pk) < 0.05
        if pk - math.floor(pk + 1e-9) < 0.9:      # (f > 0.9: Frame Mix shows ~j + 1 either way)
            assert math.floor(val * float(R2997) + 1e-9) == math.floor(pk + 1e-9), (k, pk)


def test_a_short_free_run_keeps_the_phase_breaks_of_speed_one():
    """Task 5 (thorough zendaya run): competitor frames 133-139 show RAW 3968 3969 3969 3970 3971 3971 3971 -- a 1.0
    stretch at 30 fps of 25 fps footage, then two repeats of its last frame (a cut at 138 to RAW 3971 again). The
    seven frames are also ONE exact 0.6x line, and where the greedy free-run tiling happened to start (the soft ranges
    40 frames earlier) decided whether the 1.0 phase break at 138 was a DP candidate; without it the edit got a
    0.6x slow-motion segment showing other RAW frames than the competitor on 2 of its 7 frames."""
    from match_cuts import segment as seg_mod
    m = np.array([58, 59, 59, 60, 61, 61, 62, 64, 64, 65, 66, 66, 68, 69, 69, 70, 71, 71, 71, 74, 74, 75, 76, 76,
                  78, 78, 79, 80, 81, 81])                  # competitor frames 121-150 (RAW 3958-3981, minus 3900)
    n = len(m)
    fm, _ = build_fm([Spec(m=m, n=n)])
    comp = Proxy("competitor", "", None, (720, 1280), (0.5, 0.5), C30, np.arange(n) / 30.0, n)
    raw = Proxy("raw", "", None, (1920, 1080), (1 / 3, 1 / 3), F(25), np.zeros(1), 200000)
    b = seg_mod._Builder(fm, comp, raw, None, None, cfg_(), None, None, None)
    runs = b._free_runs(0, n, True)
    assert (12, 19) in [(a, e) for a, e, _u0, _u1 in runs]          # 133-139 as one free run (exactly 0.6x) ...
    assert 17 in b.candidates(0, n)                                   # ... still holds the 1.0 cut at 138
    segs = raws(run(fm, comp, raw))
    assert (12, 17) in [(s.comp_in, s.comp_out) for s in segs] and all(abs(s.speed - 1.0) < 1e-6 for s in segs)


def test_criterion_two_asks_the_full_resolution_scorer_the_right_question():
    """The thorough default: two RAW frames at a cut -- which one the competitor shows, each with its framing refined
    (the Deadpool 271 / 412 cuts: the models' framings were off in a fast pan); one RAW frame -- the framing as given.
    The full scorer's answer is the one used."""
    from types import SimpleNamespace as NS
    from match_cuts import segment as seg_mod
    fm, _ = build_fm([Spec(m=ff_select(20, 1.0, 100), n=20)])
    b = seg_mod._Builder(fm, *proxies(20), None, None, cfg_(), None, None, None)
    calls = []

    def full(k, items, refine=False):
        calls.append((k, [j for j, _s, _f in items], refine))
        return np.array([0.9, 0.95])
    b.full = full
    A, B = NS(flip=False), NS(flip=False)
    b.sim_at = lambda S, k: Sim(1.0, 0.0, 0.0, 0.0)
    b.pred = lambda S, k: 100 + k if S is A else 200 + k
    assert b._side_scores(A, B, 5) == (0.9, 0.95) and calls[-1] == (5, [105, 205], True)
    b.pred = lambda S, k: 100 + k                                    # one time line: a framing cut
    b._side_scores(A, B, 6)
    assert calls[-1] == (6, [106, 106], False)


def test_the_thorough_framing_samples_are_measured_at_full_resolution():
    """Every matched frame's framing re-measured at full resolution (the proxy's sample, or the samples around a
    frame without one, refined): the measurement replaces the proxy's sample when it fits (>= FULL_SAMPLE_MIN), and a
    frame the proxy gave none (a pan's transition frame, held before) gets one."""
    from types import SimpleNamespace as NS
    from match_cuts import segment as seg_mod
    fm, _ = build_fm([Spec(m=ff_select(10, 1.0, 100), n=10)])
    b = seg_mod._Builder(fm, *proxies(10), None, None, cfg_(), None, None, None)
    measured = Sim(1.0, 0.0, 5.0, 0.0)

    class Full:
        comp = raw = NS(prefetch=lambda a, b: None)

        def measure(self, k, j, sim, flip):
            return (measured, 0.80 if k == 2 else 0.99, 0.9)       # frame 2: a blur -- no sample
    b.full = Full()
    proxy = Sim(1.0, 0.0, 0.0, 0.0)
    out = [(k, proxy, int(fm.raw[k])) for k in range(10) if k != 7]  # the proxy gave frame 7 no sample
    info: dict = {}
    got = {k: s for k, s, _j in b._full_res_samples(NS(a=0, b=10, flip=False), out, info, False)}
    assert got[2] is proxy and got[7] is measured and got[0] is measured
    assert info["full_res"] == 9 and info["full_res_added"] == 1
