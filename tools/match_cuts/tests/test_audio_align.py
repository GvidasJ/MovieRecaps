"""Unit tests for match_cuts.audio_align (Stage 5.1 coarse alignment, Stage 5.6 audio per segment).

All signals are synthesised with numpy at 16 kHz (the pitch-preserving stretch uses ffmpeg atempo on a
temporary wav). The competitor is built from out-of-order RAW snippets, one of them sped up 1.10x
tape-style (scipy.signal.resample_poly -- independent of the module's own resampler), with a music bed
mixed at -12 dB.
"""
from __future__ import annotations

import subprocess
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import lfilter, resample_poly

from match_cuts import audio_align as aa
from match_cuts.common import DecisionLog
from match_cuts.config import Config
from match_cuts.model import AudioHints, Segment

SR = 16000
FPS = Fraction(30)


# ---------------------------------------------------------------------------------------------
# synthetic audio
# ---------------------------------------------------------------------------------------------

def unique_audio(dur: float, seed: int, sr: int = SR) -> np.ndarray:
    """Every 1 s window distinct: FM tones with incommensurate AM, enveloped coloured noise and random
    decaying noise bursts (onsets)."""
    rng = np.random.default_rng(seed)
    n = int(round(dur * sr))
    t = np.arange(n) / sr
    y = np.zeros(n)
    for f0, dev, fr, am, amp in [(220, 4.5, 0.13, 0.71, 0.16), (523, 9.3, 0.37, 1.9, 0.12),
                                 (1187, 17.0, 0.53, 2.7, 0.08), (2750, 40.0, 0.23, 3.3, 0.05)]:
        ph = 2 * np.pi * (f0 * t - dev / (2 * np.pi * fr) * np.cos(2 * np.pi * fr * t))
        y += amp * np.sin(ph) * (0.55 + 0.45 * np.sin(2 * np.pi * am * t + f0 + seed))
    noise = lfilter([1.0], [1.0, -0.95], rng.standard_normal(n)) * 0.03
    env = 0.15 + 0.85 * np.abs(np.sin(2 * np.pi * 1.37 * t) * np.sin(2 * np.pi * 0.83 * t + 1)) ** 3
    y += noise * env
    for c in rng.uniform(0, dur, int(dur * 3)):
        a = int(c * sr)
        L = int(rng.uniform(0.02, 0.12) * sr)
        if a + L < n:
            y[a:a + L] += rng.standard_normal(L) * np.exp(-np.arange(L) / (0.3 * L)) * rng.uniform(0.05, 0.3)
    return y.astype(np.float32)


def music(n: int, sr: int = SR) -> np.ndarray:
    t = np.arange(n) / sr
    m = (0.30 * np.sin(2 * np.pi * 110 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
         + 0.22 * np.sin(2 * np.pi * 164.81 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t + 2.1))
         + 0.18 * np.sin(2 * np.pi * 220 * t + np.sin(2 * np.pi * 0.5 * t)) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)))
    return m.astype(np.float32)


def f2s(k: int) -> int:
    """comp frame -> sample (exact rational, 30 fps at 16 kHz = 533.33 samples/frame)."""
    return int(round(Fraction(k) * SR / FPS))


def build_competitor(raw: np.ndarray, plan: list, music_db: float | None = -12.0):
    """plan: [(raw_start_s | None, n_frames, speed)] with speed 1.0, 1.1 (tape via resample_poly) or
    'atempo' (ffmpeg, pitch preserved, 1.1). None raw_start = NOT-IN-RAW tone. Returns (audio, segments,
    truth [(comp_s0, comp_s1, raw_s0, v)])."""
    parts, segs, truth, k = [], [], [], 0
    for sid, (r0, n, sp) in enumerate(plan, start=1):
        L = f2s(k + n) - f2s(k)
        c0 = f2s(k) / SR
        if r0 is None:
            t = np.arange(L) / SR
            parts.append((0.25 * np.sin(2 * np.pi * 2960 * t)).astype(np.float32))
            segs.append(Segment(id=sid, type="not_in_raw", comp_in=k, comp_out=k + n))
        else:
            a = int(round(r0 * SR))
            if sp == 1.0:
                parts.append(raw[a:a + L])
                v = 1.0
            elif sp == "atempo":
                v = 1.1
                parts.append(atempo(raw[a:a + int(L * v) + 3200], v)[:L])
            else:
                v = float(sp)
                up, down = Fraction(v).limit_denominator(100).denominator, Fraction(v).limit_denominator(100).numerator
                parts.append(resample_poly(raw[a:a + int(np.ceil(L * v)) + 64].astype(np.float64), up, down)
                             .astype(np.float32)[:L])
            segs.append(Segment(id=sid, type="raw", comp_in=k, comp_out=k + n, raw_in_seconds=float(r0), speed=v))
            truth.append((c0, c0 + L / SR, float(r0), v))
        k += n
    y = np.concatenate(parts)
    if music_db is not None:
        y = y + music(y.size) * np.float32(10 ** (music_db / 20))
    return y.astype(np.float32), segs, truth


_ATEMPO_DIR: list[Path] = []


def atempo(y: np.ndarray, v: float) -> np.ndarray:
    """Pitch-preserving time stretch with ffmpeg atempo (WSOLA)."""
    import soundfile as sf
    d = _ATEMPO_DIR[0]
    src, dst = d / f"in_{len(list(d.iterdir()))}.wav", d / f"out_{len(list(d.iterdir()))}.wav"
    sf.write(str(src), y, SR, subtype="FLOAT")
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(src), "-af", f"atempo={v}",
                    "-c:a", "pcm_f32le", str(dst)], check=True)
    out, sr = sf.read(str(dst), dtype="float32")
    assert sr == SR
    return out.reshape(-1)


@pytest.fixture(scope="module")
def tmpdir_mod(tmp_path_factory):
    d = tmp_path_factory.mktemp("audio_align")
    _ATEMPO_DIR[:] = [d]
    return d


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config()


@pytest.fixture(scope="module")
def raw() -> np.ndarray:
    return unique_audio(180.0, seed=3)


# out-of-order snippets (frames at 30 fps), one 1.10x tape-speed snippet, music at -12 dB
PLAN = [(40.0, 90, 1.0), (10.0, 75, 1.0), (62.0, 90, 1.1), (25.0, 90, 1.0), (150.0, 75, 1.0), (120.0, 90, 1.0)]


@pytest.fixture(scope="module")
def edit(raw):
    return build_competitor(raw, PLAN)


@pytest.fixture(scope="module")
def hints(edit, raw, cfg, tmpdir_mod) -> tuple[AudioHints, float]:
    comp, _, _ = edit
    dlog = DecisionLog(tmpdir_mod / "decisions.jsonl")
    t0 = time.perf_counter()
    h = aa.coarse_align(comp, raw, SR, cfg, dlog)
    dt = time.perf_counter() - t0
    dlog.close()
    return h, dt


def _truth_at(truth, ct: float, half: float):
    """(raw_t, v) for a window centred at ct lying fully inside one snippet, else None."""
    for c0, c1, r0, v in truth:
        if c0 - 1e-9 <= ct - half and ct + half <= c1 + 1e-9:
            return r0 + v * (ct - c0), v
    return None


# ---------------------------------------------------------------------------------------------
# DSP helpers
# ---------------------------------------------------------------------------------------------

def test_features_shapes_rate_and_mel(cfg):
    t = np.arange(int(2.0 * SR)) / SR
    y = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    y[SR:SR + 160] += 0.8        # an onset at t = 1 s
    f = aa.features(y, SR, cfg)
    T = (len(y) - 1) * cfg.audio_feat_rate // SR + 1
    assert f["rate"] == cfg.audio_feat_rate == 100
    assert f["logmel"].shape == (T, cfg.audio_n_mels) and f["logmel"].dtype == np.float32
    assert f["onset"].shape == (T,) and f["broad"].shape == (T, 6)
    # the 1 kHz tone peaks in the mel band whose centre is nearest to 1 kHz
    st = aa._feature_setup(SR, cfg)
    centres = aa.mel_to_hz(np.linspace(aa.hz_to_mel(st["fmin"]), aa.hz_to_mel(st["fmax"]), cfg.audio_n_mels + 2))[1:-1]
    band = int(np.argmax(np.median(f["logmel"][20:80], axis=0)))
    assert abs(band - int(np.argmin(np.abs(centres - 1000.0)))) <= 1
    # onset envelope peaks at the burst (frame 100 +- 3)
    assert abs(int(np.argmax(f["onset"])) - 100) <= 3
    # empty input
    e = aa.features(np.zeros(0, np.float32), SR, cfg)
    assert e["logmel"].shape == (0, cfg.audio_n_mels) and e["onset"].shape == (0,)


def test_mel_filterbank_warp_matches_tape_speedup(cfg):
    """A tape-style 1.1x speed-up seen through the 1.1-warped filterbank has the original's band energies."""
    rng = np.random.default_rng(0)
    y = rng.standard_normal(SR * 4).astype(np.float32) * 0.002
    t = np.arange(y.size) / SR
    for f in (310.0, 730.0, 1530.0, 2330.0, 4130.0):
        y += (0.1 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    fast = resample_poly(y.astype(np.float64), 10, 11).astype(np.float32)
    st = aa._feature_setup(SR, cfg)
    n_fft = st["n_fft"]

    def band_energy(sig, warp):
        fb = aa.mel_filterbank(SR, n_fft, cfg.audio_n_mels, st["fmin"], st["fmax"], warp=warp)
        P = np.concatenate([p for _, p in aa._power_chunks(sig, SR, 100, n_fft)])
        return 10 * np.log10(np.mean(P @ fb.T, axis=0))
    e0, e1 = band_energy(y, 1.0), band_energy(fast, 1.1)
    assert np.max(np.abs(e0 - e1)) < 1.5 and np.median(np.abs(e0 - e1)) < 0.5      # dB
    assert np.max(np.abs(e0 - band_energy(fast, 1.0))) > 6.0     # the unwarped bank does not match


def test_resample_at_is_band_limited_interpolation():
    t = np.arange(4000) / SR
    y = np.sin(2 * np.pi * 1234.5 * t).astype(np.float32)
    pos = 100.0 + np.arange(3000) * 1.1 + 0.3717
    got = aa.resample_at(y, pos, cutoff=1.0)
    want = np.sin(2 * np.pi * 1234.5 * pos / SR)
    assert np.max(np.abs(got - want)) < 2e-3
    # outside the signal -> zeros
    assert np.all(aa.resample_at(y, np.array([-100.0, 5000.0])) == 0)


def test_xcorr_lag_sign_subsample_and_silence():
    rng = np.random.default_rng(1)
    a = lfilter([1.0], [1.0, -0.8], rng.standard_normal(SR)).astype(np.float32)
    d = 37.3                                            # samples: b is a delayed by d
    b = aa.resample_at(a, np.arange(a.size) - d)
    lag, peak = aa.xcorr_lag(a, b, SR, 0.1)
    assert abs(lag * SR - d) < 0.05 and peak > 0.98
    lag2, _ = aa.xcorr_lag(b, a, SR, 0.1)
    assert abs(lag2 * SR + d) < 0.05
    assert aa.xcorr_lag(a, np.zeros_like(a), SR, 0.1) == (0.0, 0.0)
    assert aa.xcorr_lag(np.zeros(0), np.zeros(0), SR, 0.1) == (0.0, 0.0)


# ---------------------------------------------------------------------------------------------
# Stage 5.1
# ---------------------------------------------------------------------------------------------

def test_coarse_align_out_of_order_snippets_tape_speed_and_music(hints, edit, cfg):
    h, dt = hints
    _, _, truth = edit
    assert isinstance(h, AudioHints)
    assert np.allclose(np.diff(h.comp_t), cfg.audio_hop) and h.window == cfg.audio_window
    conf = h.confident(cfg.audio_min_conf)
    inside = conf_inside = 0
    per_speed = {1.0: 0, 1.1: 0}
    for i, ct in enumerate(h.comp_t):
        tr = _truth_at(truth, float(ct), h.window / 2)
        if tr is None:
            continue
        inside += 1
        if not conf[i]:
            continue
        conf_inside += 1
        per_speed[round(tr[1], 2)] += 1
        assert abs(h.raw_t[i] - tr[0]) <= 0.005, (ct, h.raw_t[i], tr)       # raw_t within ±5 ms
        assert abs(h.speed[i] - tr[1]) <= 0.01, (ct, h.speed[i], tr)         # speed within ±0.01
        assert h.psr[i] > 3.0 and h.peak[i] > 0.5
    assert conf_inside >= 0.9 * inside, (conf_inside, inside)
    assert per_speed[1.1] >= 6                   # the 1.10x tape snippet is found despite the pitch shift
    # every confident window (also those straddling a cut) maps its centre correctly
    for i in np.nonzero(conf)[0]:
        ct = float(h.comp_t[i])
        for c0, c1, r0, v in truth:
            if c0 <= ct < c1:
                assert abs(h.raw_t[i] - (r0 + v * (ct - c0))) < 0.03, (ct, h.raw_t[i])


def test_coarse_align_decisions_logged(hints, tmpdir_mod):
    import json
    recs = [json.loads(line) for line in (tmpdir_mod / "decisions.jsonl").read_text().splitlines()]
    summ = [r for r in recs if r["stage"] == "audio_align" and r["decision"] == "coarse_align"]
    assert len(summ) == 1
    ev = summ[0]["evidence"]["windows"]
    assert len(ev) == len(hints[0].comp_t)
    assert any("second" in e and "null_floor" in e for e in ev)
    assert 1.1 in [round(s, 2) for s in summ[0]["speeds_found"]]


def test_coarse_align_deterministic(raw, cfg):
    comp, _, _ = build_competitor(raw, [(70.0, 60, 1.0), (30.0, 60, 1.1)])
    h1 = aa.coarse_align(comp, raw, SR, cfg, None)
    h2 = aa.coarse_align(comp, raw, SR, cfg, None)
    for f in ("comp_t", "raw_t", "speed", "conf", "psr", "peak"):
        assert np.array_equal(getattr(h1, f), getattr(h2, f), equal_nan=True), f


def test_coarse_align_unrelated_audio_not_confident(raw, cfg):
    other = unique_audio(15.0, seed=99) * 0.8 + music(15 * SR) * 0.2
    h = aa.coarse_align(other, raw, SR, cfg, None)
    assert len(h.comp_t) > 40
    assert h.confident(cfg.audio_min_conf).mean() <= 0.05


def test_coarse_align_no_audio(raw, cfg):
    for comp, rw in ((np.zeros(0, np.float32), raw), (raw[:SR * 5], np.zeros(0, np.float32)),
                     (np.zeros(SR * 5, np.float32), raw)):
        h = aa.coarse_align(comp, rw, SR, cfg, None)
        assert len(h.comp_t) == 0 and len(h.raw_t) == 0


def test_coarse_align_speed_scan_finds_pitch_preserving_stretch(raw, cfg, tmpdir_mod):
    """atempo keeps the pitch: the tempo (unwarped) hypothesis + onset/octave coarse feature find it."""
    comp, _, truth = build_competitor(raw, [(50.0, 120, "atempo")], music_db=None)
    h = aa.coarse_align(comp, raw, SR, cfg, None)
    conf = h.confident(cfg.audio_min_conf)
    ok = 0
    for i in np.nonzero(conf)[0]:
        tr = _truth_at(truth, float(h.comp_t[i]), h.window / 2)
        if tr is not None:
            # WSOLA has no exact sample correspondence: feature-level precision
            assert abs(h.raw_t[i] - tr[0]) < 0.04 and abs(h.speed[i] - 1.1) <= 0.02
            ok += 1
    assert ok >= 3


def test_coarse_align_performance_smoke(hints):
    """20 s competitor vs 3 min RAW (the 90 s vs 30 min figure is in the module report)."""
    assert hints[1] < 45.0


# ---------------------------------------------------------------------------------------------
# Stage 5.6
# ---------------------------------------------------------------------------------------------

def test_segments_lag_pitch_and_music_bed(edit, raw, cfg):
    comp, segs, _ = edit
    out = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None)
    assert out["status"] == "ok"
    n = segs[-1].comp_out
    for s in segs:
        a = out["segments"][s.id]
        assert set(a) == {"in_offset_frames", "out_offset_frames", "pitch_preserved", "lag_ms", "corr", "exception",
                          "line"}
        assert a["in_offset_frames"] == 0 and a["out_offset_frames"] == 0
        assert abs(a["lag_ms"]) < 0.5 and a["corr"] > 0.8 and a["exception"] is None
        assert a["pitch_preserved"] is (False if s.speed != 1.0 else None)   # tape: pitch follows speed
    music_runs = [x for x in out["added_audio"] if x["type"] == "music"]
    assert len(music_runs) == 1 and music_runs[0]["comp_in"] == 0 and music_runs[0]["comp_out"] == n
    assert -15.0 < music_runs[0]["level_db"] < 0.0


def test_segments_clean_audio_has_no_added_audio(raw, cfg):
    comp, segs, _ = build_competitor(raw, PLAN, music_db=None)
    out = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None)
    assert out["status"] == "ok" and out["added_audio"] == []
    assert all(abs(v["lag_ms"]) < 0.1 and v["corr"] > 0.99 for v in out["segments"].values())


@pytest.mark.parametrize("shift", [-8, 6])
def test_segments_j_and_l_cuts(raw, cfg, shift):
    """shift < 0: J-cut (B's audio starts |shift| frames before its picture); shift > 0: L-cut (A's audio
    continues shift frames under B's picture). Sign convention (DESIGN §3): A.out = B.in = shift."""
    plan = [(40.0, 90, 1.0), (100.0, 90, 1.0), (20.0, 90, 1.0)]
    comp, segs, _ = build_competitor(raw, plan, music_db=None)
    A, B = segs[0], segs[1]
    cut = B.comp_in
    lo, hi = sorted((cut, cut + shift))
    src = B if shift < 0 else A
    t0 = src.raw_in_seconds + (f2s(lo) - f2s(src.comp_in)) / SR
    a = int(round(t0 * SR))
    comp[f2s(lo):f2s(hi)] = raw[a:a + f2s(hi) - f2s(lo)]
    comp = comp + music(comp.size) * np.float32(10 ** (-12 / 20))
    out = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None)
    sa, sb = out["segments"][A.id], out["segments"][B.id]
    assert sa["out_offset_frames"] == shift and sb["in_offset_frames"] == shift
    assert sa["in_offset_frames"] == 0 and sb["out_offset_frames"] == 0
    assert out["segments"][segs[2].id]["in_offset_frames"] == 0          # the plain cut stays a plain cut
    assert abs(sa["lag_ms"]) < 0.5 and abs(sb["lag_ms"]) < 0.5
    assert [c["cut"] for c in out["cuts"]] == [cut]
    assert any(("J-cut" if shift < 0 else "L-cut") in n for n in out["notes"])


def test_segments_pitch_preserving_stretch(raw, cfg, tmpdir_mod):
    plan = [(40.0, 60, 1.0), (62.0, 90, 1.1), (100.0, 90, "atempo"), (20.0, 60, 1.0)]
    comp, segs, _ = build_competitor(raw, plan)
    out = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None)
    tape_seg, tempo_seg = out["segments"][2], out["segments"][3]
    assert tape_seg["pitch_preserved"] is False and tape_seg["exception"] is None
    assert tempo_seg["pitch_preserved"] is True and tempo_seg["exception"] == "pitch_preserved"
    assert out["status"] == "ok"
    # the stretched segment is not mistaken for added audio
    assert all(not (x["comp_in"] < 240 and x["comp_out"] > 150) or x["type"] == "music" for x in out["added_audio"])


def flite_speech(text: str, d: Path) -> np.ndarray:
    """Synthetic speech via ffmpeg's flite source (mono, 16 kHz)."""
    import soundfile as sf
    dst = d / "vo.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
                    "-ar", str(SR), "-ac", "1", "-c:a", "pcm_f32le", str(dst)], check=True)
    y, sr = sf.read(str(dst), dtype="float32")
    assert sr == SR
    y = y.reshape(-1)
    nz = np.nonzero(np.abs(y) > 0.02 * np.abs(y).max())[0]      # trim leading / trailing silence
    return y[nz[0]:nz[-1] + 1].copy()


def test_segments_not_in_raw_voice_over_and_sfx(raw, cfg, tmpdir_mod):
    plan = [(40.0, 90, 1.0), (None, 30, 1.0), (100.0, 150, 1.0), (20.0, 90, 1.0)]
    comp, segs, _ = build_competitor(raw, plan, music_db=None)
    rng = np.random.default_rng(5)
    # SFX: a 0.4 s decaying noise burst at comp frame 40
    L = int(0.4 * SR)
    comp[f2s(40):f2s(40) + L] += (rng.standard_normal(L) * np.exp(-np.arange(L) / (0.1 * SR)) * 0.5).astype(np.float32)
    # voice-over: synthetic speech (ffmpeg flite) from comp frame 130, at the level of the original
    voice = flite_speech("Welcome back everyone. Today we look at the strangest scene in the whole film, "
                         "and I promise the ending will surprise you.", tmpdir_mod)
    n0 = f2s(130)
    voice = voice[:comp.size - n0]
    voice *= np.float32(np.sqrt(np.mean(comp[n0:n0 + voice.size] ** 2)) / max(1e-9, np.sqrt(np.mean(voice ** 2))))
    comp[n0:n0 + voice.size] += voice
    vo_end = int(np.ceil((n0 + voice.size) / SR * 30))
    out = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None)
    assert out["segments"][2]["exception"] == "not_in_raw" and out["segments"][2]["lag_ms"] is None
    types = {(x["type"], x["comp_in"], x["comp_out"]) for x in out["added_audio"]}
    sfx = [x for x in out["added_audio"] if x["type"] == "sfx"]
    vo = [x for x in out["added_audio"] if x["type"] == "voice_over"]
    assert len(sfx) == 1 and abs(sfx[0]["comp_in"] - 40) <= 2 and sfx[0]["comp_out"] - sfx[0]["comp_in"] <= 20, types
    assert len(vo) == 1 and abs(vo[0]["comp_in"] - 130) <= 8 and abs(vo[0]["comp_out"] - min(vo_end, 360)) <= 8, types
    # the NOT-IN-RAW tone is not reported as added audio (its range is unobservable)
    assert not any(x["comp_in"] < 120 and x["comp_out"] > 90 for x in out["added_audio"]), types


def test_segments_audio_replaced(raw, cfg):
    comp, segs, _ = build_competitor(raw, PLAN)
    noise = lfilter([1.0], [1.0, -0.6], np.random.default_rng(9).standard_normal(comp.size)).astype(np.float32) * 0.1
    out = aa.analyze_segments_audio(segs, noise, raw, SR, FPS, cfg, None)
    assert out["status"] == "audio_replaced"
    assert all(v["exception"] == "audio_replaced" and v["pitch_preserved"] is None for v in out["segments"].values())
    assert out["added_audio"] and out["added_audio"][0]["comp_in"] == 0
    assert out["added_audio"][-1]["comp_out"] == segs[-1].comp_out


def test_segments_music_dominated_segment(raw, cfg):
    plan = [(40.0, 90, 1.0), (100.0, 90, 1.0), (20.0, 90, 1.0)]
    comp, segs, _ = build_competitor(raw, plan, music_db=None)
    a, b = f2s(90), f2s(180)
    comp[a:b] = comp[a:b] * 0.05 + music(b - a) * 1.2          # original ducked -26 dB under loud music
    out = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None)
    assert out["status"] == "ok"
    assert out["segments"][2]["exception"] == "music_dominated"
    assert out["segments"][1]["exception"] is None and out["segments"][3]["exception"] is None
    assert any(x["type"] == "music" and x["comp_in"] <= 92 and x["comp_out"] >= 178 for x in out["added_audio"])


def test_segments_crossfade_and_remap_segments(raw, cfg):
    """Crossfade overlaps are excluded from the measurement; a freeze (remap) segment is handled."""
    D = 6
    plan = [(40.0, 96, 1.0), (100.0, 90, 1.0)]
    comp, segs, _ = build_competitor(raw, plan, music_db=None)
    A, B = segs
    # B starts D frames earlier (overlap [90, 96)), linear crossfade of the two sources
    B.comp_in = 90
    B.raw_in_seconds = 100.0 - D / 30
    alpha = [(k) / D for k in range(D)]
    tr = {"type": "crossfade", "duration_frames": D, "alpha": alpha}
    A.transition_out, B.transition_in = tr, tr
    n0, n1 = f2s(90), f2s(96)
    g = np.linspace(0, 1, n1 - n0, endpoint=False).astype(np.float32)
    bsrc = raw[int(round(B.raw_in_seconds * SR)):][:n1 - n0]
    comp[n0:n1] = comp[n0:n1] * (1 - g) + bsrc * g
    freeze = Segment(id=3, type="raw", comp_in=186, comp_out=216, raw_in_seconds=10.0, speed=0.0, time_mode="remap",
                     time_remap_keys=[{"comp_frame": 186, "raw_seconds": 10.0}, {"comp_frame": 216, "raw_seconds": 10.0}])
    comp = np.concatenate([comp, np.zeros(f2s(216) - comp.size, np.float32)])
    out = aa.analyze_segments_audio([A, B, freeze], comp, raw, SR, FPS, cfg, None)
    assert out["segments"][1]["corr"] > 0.99 and out["segments"][2]["corr"] > 0.99
    assert out["cuts"] == []
    # freeze: AE plays no audio and the competitor is silent there too -> no_audio, nothing measured
    assert out["segments"][3]["exception"] == "no_audio" and out["segments"][3]["lag_ms"] is None


def test_segments_no_audio(raw, cfg):
    _, segs, _ = build_competitor(raw, [(40.0, 60, 1.0), (None, 30, 1.0)], music_db=None)
    for comp, rw in ((np.zeros(0, np.float32), raw), (raw[:SR * 5], np.zeros(0, np.float32))):
        out = aa.analyze_segments_audio(segs, comp, rw, SR, FPS, cfg, None)
        assert out["status"] == "no_audio" and out["added_audio"] == []
        assert out["segments"][1]["exception"] == "no_audio"
        assert out["segments"][2]["exception"] == "not_in_raw"


def test_segments_deterministic(edit, raw, cfg):
    comp, segs, _ = edit
    import json
    a = json.dumps(aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None), sort_keys=True)
    b = json.dumps(aa.analyze_segments_audio(segs, comp, raw, SR, FPS, cfg, None), sort_keys=True)
    assert a == b


def test_single_thread_blas_restores_setting():
    ctl = aa._blas_ctl()
    if ctl is None:                       # platform without OpenBLAS control: the context is a no-op
        with aa.single_thread_blas():
            x = np.ones((8, 8)) @ np.ones((8, 8))
        assert x[0, 0] == 8
        return
    before = int(ctl[1]())
    with aa.single_thread_blas():
        assert int(ctl[1]()) == 1
    assert int(ctl[1]()) == before
