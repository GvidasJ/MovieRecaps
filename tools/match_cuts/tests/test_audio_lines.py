"""Continuous audio across video-only retimes / uncertain segments / placeholders (FX-14, DESIGN §7 D9 'audio
lines') and the added-audio classification of a music bed (FX-09 remainder).

The competitor audio is built piece by piece from RAW noise: each piece plays RAW time r0 + v (t - t0) over its
own comp seconds, independently of the picture segments -- a freeze or a 0.25x slow motion whose audio keeps
playing at speed 1, a placeholder whose audio continues the next shot's line, a foreign insert (a tone).
"""
from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from match_cuts import audio_align as aa
from match_cuts import export_ae as ea
from match_cuts import pipeline, verify
from match_cuts import render_preview as rp
from match_cuts.audio_align import xcorr_lag
from match_cuts.common import null_dlog
from match_cuts.config import Config
from match_cuts.model import Cutlist, Segment

SR = 16000
FPS = Fraction(30)


def _raw(seconds: float = 40.0, seed: int = 7) -> np.ndarray:
    return (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.1).astype(np.float32)


def _comp(raw: np.ndarray, pieces: list[tuple[int, int, float | None, float]], n_frames: int) -> np.ndarray:
    """pieces = [(k0, k1, RAW seconds at k0 (None: a 2960 Hz tone), speed)] tiling the competitor frames."""
    n = int(round(n_frames / 30.0 * SR))
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    for k0, k1, r0, v in pieces:
        m = (t >= k0 / 30.0) & (t < k1 / 30.0)
        if r0 is None:
            out[m] = 0.25 * np.sin(2 * np.pi * 2960.0 * t[m])
        else:
            out[m] = aa.resample_at(raw, (r0 + v * (t[m] - k0 / 30.0)) * SR, cutoff=min(1.0, 1.0 / v))
    return out


def _seg(sid: int, a: int, b: int, raw_in: float | None, speed: float = 1.0, kind: str = "raw", **kw) -> Segment:
    s = Segment(sid, kind, a, b, raw_in_seconds=raw_in, speed=speed,
                raw_in_interval=[raw_in - 0.004, raw_in + 0.004] if raw_in is not None else None, **kw)
    s.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
    if kind == "not_in_raw":
        s.audio["exception"] = "not_in_raw"
    return s


def _freeze(sid: int, a: int, b: int, raw_s: float) -> Segment:
    s = _seg(sid, a, b, raw_s, speed=0.0, time_mode="remap")
    s.time_remap_keys = [{"comp_frame": a, "raw_seconds": raw_s}, {"comp_frame": b, "raw_seconds": raw_s}]
    return s


def _analyse(segs, comp, raw):
    recs = []
    dlog = type("D", (), {"record": lambda self, *a, **k: recs.append((a, k))})()
    res = aa.analyze_segments_audio(segs, comp, raw, SR, FPS, Config(), dlog)
    return res, recs


def test_freeze_under_continuous_audio_continues_the_previous_line():
    """A 15-frame video-only freeze whose audio keeps playing the previous shot's line: the freeze gets that line
    (one audio line 'S01 continued', residual ~0), no exception, no J/L exported; the next shot keeps its own."""
    raw = _raw()
    segs = [_seg(1, 0, 30, 2.0), _freeze(2, 30, 45, 3.0), _seg(3, 45, 75, 9.0)]
    comp = _comp(raw, [(0, 45, 2.0, 1.0), (45, 75, 9.0, 1.0)], 75)
    res, recs = _analyse(segs, comp, raw)
    per = res["segments"]
    line = per[2]["line"]
    assert line is not None and line["id"] == 0 and line["source"] == "S01 continued" and line["speed"] == 1.0, per[2]
    assert line["raw_in_seconds"] == pytest.approx(3.0, abs=1e-9) and abs(line["lag_ms"]) < 0.5 and line["corr"] > 0.95
    assert per[2]["exception"] is None and per[1]["line"] is None and per[3]["line"] is None
    assert res["cuts"] == [] and per[1]["out_offset_frames"] == 0 and per[2]["in_offset_frames"] == 0
    assert any("continuous audio line" in n and "S02" in n for n in res["notes"])
    assert not res["added_audio"]                        # the freeze's audio is explained by the line


def test_slow_motion_keeps_playing_from_its_own_in_point():
    """A video-only 0.25x slow motion (audio at speed 1 from the picture in-point, which the picture phase puts 6 ms
    off): the line is the segment's own in-point at speed 1, corrected by the measured residual."""
    raw = _raw()
    segs = [_seg(1, 0, 30, 2.0), _seg(2, 30, 60, 5.006, speed=0.25), _seg(3, 60, 90, 20.0)]
    comp = _comp(raw, [(0, 30, 2.0, 1.0), (30, 60, 5.0, 1.0), (60, 90, 20.0, 1.0)], 90)
    res, _ = _analyse(segs, comp, raw)
    line = res["segments"][2]["line"]
    assert line is not None and line["source"] == "own in-point at speed 1" and line["id"] == 30, res["segments"][2]
    assert line["speed"] == 1.0 and line["raw_in_seconds"] == pytest.approx(5.0, abs=2e-5)
    assert res["segments"][2]["exception"] is None


def test_jl_next_to_a_line_piece_is_removed(monkeypatch):
    """A 5-frame freeze over continuous audio takes S01's line. The switch at S01|S02 was measured with the
    freeze's silent PICTURE model (it lands at the edge of the window: [5, 0] sides, not exported); were such a
    switch ever accepted as a small J/L, the line removes it with its note -- inside one line the anchor's audio
    simply continues; at the freeze's other cut (another line) the picture model does not carry its audio
    either. Never two audio layers over the same frames."""
    raw = _raw()
    segs = [_seg(1, 0, 30, 2.0), _freeze(2, 30, 35, 3.0), _seg(3, 35, 65, 9.0)]
    comp = _comp(raw, [(0, 35, 2.0, 1.0), (35, 65, 9.0, 1.0)], 65)
    res, recs = _analyse(segs, comp, raw)
    per = res["segments"]
    assert per[2]["line"] is not None and per[2]["line"]["source"] == "S01 continued", per[2]
    assert res["cuts"] == [] and per[1]["out_offset_frames"] == 0 and per[2]["in_offset_frames"] == 0
    edge = [k for a, k in recs if a[1] == "audio_cut_matches_video" and k["cut"] == 30][0]
    assert edge["measured_offset"] > 0 and min(edge["sides"]) == 0          # one side never observed
    # the same analysis with both switches accepted as J/L (injected before the audio lines are drawn)
    real = aa._audio_lines

    def inject(segs_, out, models, comp_, raw_, sr, fps, g, shifts, cfg, rec, notes, cuts):
        for c in cuts:
            off = 2 if c["cut"] == 30 else -1
            c["offset_frames"] = off
            out[c["a"]]["out_offset_frames"] = out[c["b"]]["in_offset_frames"] = off
            notes.append(f"{'J' if off < 0 else 'L'}-cut at comp frame {c['cut']} (S{c['a']:02d}|S{c['b']:02d}): x")
        return real(segs_, out, models, comp_, raw_, sr, fps, g, shifts, cfg, rec, notes, cuts)
    monkeypatch.setattr(aa, "_audio_lines", inject)
    res, recs = _analyse(segs, comp, raw)
    per = res["segments"]
    assert res["cuts"] == [] and all(per[i][f] == 0 for i in (1, 2, 3) for f in ("in_offset_frames", "out_offset_frames"))
    why = {k["cut"]: k["not_exported"] for a, k in recs if a[1] == "jl_not_exported"}
    assert why == {30: "inside one audio line", 35: "next to a piece whose audio follows an audio line, not its picture map"}
    assert not any(n[1:].startswith("-cut at comp frame") for n in res["notes"]), res["notes"]
    assert sum("not exported as a J/L cut" in n for n in res["notes"]) == 2


def test_placeholder_keeps_foreign_audio_silent_and_takes_a_verified_line():
    """A real NOT-IN-RAW insert with foreign audio (a tone) verifies no line and stays silent; a placeholder whose
    audio is the next shot's line extended backward (the real run's 1180-1191) gets that line."""
    raw = _raw()
    segs = [_seg(1, 0, 30, 2.0), _seg(2, 30, 54, None, kind="not_in_raw"), _seg(3, 54, 84, 9.0),
            _seg(4, 84, 96, None, kind="not_in_raw"), _seg(5, 96, 126, 20.0)]
    comp = _comp(raw, [(0, 30, 2.0, 1.0), (30, 54, None, 1.0), (54, 84, 9.0, 1.0), (84, 126, 20.0 - 12 / 30, 1.0)], 126)
    res, recs = _analyse(segs, comp, raw)
    per = res["segments"]
    assert per[2]["line"] is None and per[2]["exception"] == "not_in_raw"
    line = per[4]["line"]
    assert line is not None and line["source"] == "S05 continued back" and line["id"] == 96, per[4]
    assert line["raw_in_seconds"] == pytest.approx(20.0 - 12 / 30, abs=1e-9) and per[4]["exception"] is None
    trials = [k for a, k in recs if a[1] == "audio_lines"][0]["trials"]
    assert all(not t["ok"] for t in trials if t["seg"] == 2)       # the tone: no line correlates


def test_lines_flow_into_the_preview_the_ae_plan_and_criterion_5(tmp_path):
    """The line plays in build_audio (also under a placeholder), AE gets ONE audio-only layer per line run (the
    picture layer silent) and c5 measures the line piece instead of excusing it."""
    sr = 48000
    raw = (np.random.default_rng(9).standard_normal(sr * 12) * 0.1).astype(np.float32)
    comp_block = {"file": "c.mp4", "file_rel": "media/c.mp4", "width": 270, "height": 480, "fps": "30/1", "frames": 90}
    raw_block = {"file": "r.mp4", "file_rel": "media/r.mp4", "width": 640, "height": 360, "fps": "30/1", "frames": 300,
                 "has_audio": True,
                 "audio_sample_rate": sr, "audio_channels": 1}
    layout = {"mode": "match", "box": None, "background": "solid", "background_detail": {"type": "solid"}}
    q = 1 / 120                                  # a quarter frame inside: no layer goes frame-exact (FX-10)
    s1, s2, s3 = _seg(1, 0, 30, 1.0 + q), _freeze(2, 30, 45, 2.0 + q), _seg(3, 45, 90, 5.0 + q)
    s1.transform = s2.transform = s3.transform = {"scale": 1.0, "rotation_deg": 0.0, "tx": 0.0, "ty": 0.0}
    s2.audio["line"] = {"id": 0, "raw_in_seconds": 2.0 + q, "speed": 1.0, "source": "S01 continued", "lag_ms": 0.0,
                        "corr": 0.99}
    cl = Cutlist(1, comp_block, raw_block, layout, [s1, s2, s3], audio={"status": "ok"}, settings={"audio_sync": "raw"})
    y = rp.build_audio(cl, raw, sr)
    a, b = int(sr * 30 / 30), int(sr * 45 / 30)
    np.testing.assert_allclose(y[a:b], raw[96400:96400 + (b - a)], atol=1e-5)
    np.testing.assert_allclose(y[:a], raw[48400:48400 + a], atol=1e-5)    # S01 unchanged, continuous with the line
    import test_export_ae as tea
    plan = ea.ae_plan(cl, Config(), tea.meta_for(cl))
    by = {L["id"]: L for L in plan["layers"]}
    assert by["seg2"]["audio"] is False and by["seg1"]["audio"] is True
    line_layers = [L for L in plan["layers"] if L["kind"] == "raw_audio"]
    assert [L["id"] for L in line_layers] == ["line2_audio"]
    L = line_layers[0]
    assert (L["compIn"], L["compOut"]) == (30, 45) and L["rawIn"] == pytest.approx(2.0 + q) and L["enabled"] is False
    assert L["audio"] is True and L["timeMode"] == "stretch" and L["stretch"] == pytest.approx(100.0)
    if ea._find_node() is not None:                  # the JSX builds it like any audio twin (c6, mock run)
        jsx = tmp_path / "build_ae_project.jsx"
        ea.write_jsx(cl, plan, jsx, Config())
        tea.place_media(tmp_path, cl)
        mock = ea.mock_verify(jsx, plan, tea.meta_for(cl))
        assert mock["status"] == "pass", mock["failures"]
    # c5: the freeze is measured on its line (the recreation plays it) -> ok; without the line it would be silent
    comp = y.copy()
    r = verify.check_audio([s1, s2, s3], comp, y, sr, FPS, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    row = {x["id"]: x for x in r["segments"]}[2]
    assert row["result"] == "ok" and row.get("audio_line") == 0 and abs(row["lag_ms"]) < 0.5
    # a placeholder carrying a line is measured too (never excused as not_in_raw)
    p2 = _seg(2, 30, 45, None, kind="not_in_raw")
    p2.audio.update(exception=None, line=dict(s2.audio["line"]))
    cl2 = Cutlist(1, comp_block, raw_block, layout, [s1, p2, s3], audio={"status": "ok"}, settings={"audio_sync": "raw"})
    y2 = rp.build_audio(cl2, raw, sr)
    np.testing.assert_allclose(y2, y, atol=1e-6)
    r = verify.check_audio([s1, p2, s3], y2, y2, sr, FPS, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert {x["id"]: x for x in r["segments"]}[2]["result"] == "ok"


def test_audio_line_segments_are_left_out_of_d3_and_the_offset_estimate():
    """A segment whose audio follows a line says nothing about its picture phase (D3) or the run's A/V offset."""
    s = _seg(1, 0, 30, 10.004, speed=0.25)
    s.time_mode = "stretch"
    s.audio.update(lag_ms=-3.0, corr=0.97, line={"id": 0, "raw_in_seconds": 10.0, "speed": 1.0, "source": "x",
                                                 "lag_ms": -3.0, "corr": 0.97})
    from match_cuts.model import FrameMap, Status
    fm = FrameMap(30)
    fm.status = np.full(30, Status.MATCH, np.int8)
    fm.raw = fm.raw_lo = fm.raw_hi = fm.soft_lo = fm.soft_hi = np.arange(300, 330, dtype=np.int32)
    res = {"segments": {1: dict(s.audio)}, "status": "ok", "_measured": {}}
    recs = []
    dlog = type("D", (), {"record": lambda self, *a, **k: recs.append((a, k))})()
    moved, _ = pipeline.audio_informed_phase([s], res, fm, None, None, SR, FPS, FPS, Config(), dlog)
    assert moved == [] and any("audio line" in k.get("reason", "") for a, k in recs if a[1] == "audio_phase_skipped")
    meas = {"_measured": {1: {"lag_total_ms": -3.0, "dur_s": 1.0, "corr": 0.97}}}
    assert aa.av_offset_estimate([s], meas, Config(), null_dlog())["n_segments"] == 0


def test_music_bed_under_a_dynamic_original_is_one_music_run():
    """A steady music bed under a bursty original crosses the -20 dB residual threshold only where the original is
    quiet: its pieces are short. Merged FIRST and classified on the whole run's observable residual it is 'music'
    over the whole edit (film24's -12 dB bed read 'sfx' 0-532 when every < 1.5 s piece was typed alone)."""
    sr = 16000
    n_frames = 300
    n = n_frames * sr // 30
    t = np.arange(n) / sr
    rng = np.random.default_rng(2)
    env = (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 0.9 * t))) * 0.95 + 0.05     # 0.55 s loud, 0.55 s near-silent
    orig = (rng.standard_normal(n) * 0.1 * env).astype(np.float32)
    music = (0.02 * np.sin(2 * np.pi * 110 * t) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.25 * t))
             + 0.015 * np.sin(2 * np.pi * 164.81 * t)).astype(np.float32)
    added = aa._added_audio(orig + music, orig, np.ones(n, bool), sr, FPS, n_frames, -20.0, null_dlog())
    assert len(added) == 1 and added[0]["type"] == "music" and (added[0]["comp_in"], added[0]["comp_out"]) == (0, n_frames)
    # a short isolated burst is still an effect
    fx = np.zeros(n, np.float32)
    fx[3 * sr:int(3.6 * sr)] = 0.3 * rng.standard_normal(int(0.6 * sr))
    steady = (rng.standard_normal(n) * 0.1).astype(np.float32)
    added = aa._added_audio(steady + fx, steady, np.ones(n, bool), sr, FPS, n_frames, -20.0, null_dlog())
    assert [a["type"] for a in added] == ["sfx"]
