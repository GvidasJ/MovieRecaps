"""Unit tests for segment.py (DESIGN §5 segment.py): FrameMaps synthesised directly from ffmpeg-exact frame
selection patterns (see test_phase_solve.ff_select), plus small proxy arrays where pixels are needed
(crossfades made with the exact xfade formula, punch-in, uniform frames, dips)."""
from __future__ import annotations

import json
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
    assert "re-assigned" in segs[0].notes and "[40]" in segs[0].notes
    check_model(segs[0], fm)


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
    for k in errs:
        assert bool(fm.low_margin[k])
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
