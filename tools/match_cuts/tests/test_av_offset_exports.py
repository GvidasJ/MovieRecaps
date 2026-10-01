"""The competitor's A/V offset downstream of the analysis (DESIGN §7 D9): criterion 5 (verify.check_audio:
residuals around the published offset, verify's own re-estimate, aggregated short runs, ordered ranges),
the export sync policy (--audio-sync raw|competitor: export_ae audio twins, render_preview.build_audio)
and the report line."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import cli, report, verify
from match_cuts import export_ae as ea
from match_cuts import render_preview as rp
from match_cuts.audio_align import xcorr_lag
from match_cuts.config import Config
from match_cuts.model import Cutlist, Segment

F30 = Fraction(30)
SRV = 8000                                        # verify tests (86 ms = 688 samples)


def _seg(sid: int, a: int, b: int, raw_in: float = 1.0, **audio) -> Segment:
    s = Segment(sid, "raw", a, b, raw_in_seconds=raw_in, speed=1.0)
    s.audio = {"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": 0.0, "corr": 0.99,
               "exception": None, **audio}
    return s


def _offset(lag_ms: float, mode: str = "raw", baseline_ms: float | None = 86.0, width: float = 0.4) -> dict:
    return {"status": "ok", "av_offset": {"status": "measured", "lag_ms": lag_ms,
                                          "lag_ms_interval": [lag_ms - width / 2, lag_ms + width / 2],
                                          "switch_baseline_ms": baseline_ms, "sync_mode": mode,
                                          "text": f"competitor audio is {abs(lag_ms):.1f} ms later than its picture, "
                                                  "relative to RAW's own A/V sync"}}


def _delayed(y: np.ndarray, samples: int) -> np.ndarray:
    return np.concatenate([np.zeros(samples, np.float32), y[:-samples]])


@pytest.fixture(scope="module")
def src() -> np.ndarray:
    return (np.random.default_rng(21).standard_normal(SRV * 12) * 0.1).astype(np.float32)


# ---------------------------------------------------------------------------------------------
# criterion 5
# ---------------------------------------------------------------------------------------------

def test_c5_raw_sync_offset_is_one_run_level_exception(src):
    """comp = recreation delayed by 86 ms (the whole mix): published offset -86 ms -> every segment's residual
    ~0, verify re-measures -86 ms -> pass_with_exceptions with ONE run-level 'av_offset' line."""
    rec, comp = src, _delayed(src, 688)
    segs = [_seg(1, 0, 90), _seg(2, 90, 180), _seg(3, 180, 300)]
    r = verify.check_audio(segs, comp, rec, SRV, F30, _offset(-86.0), [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "pass_with_exceptions" and r["failures"] == [], r["failures"]
    assert len(r["exceptions"]) == 1 and r["exceptions"][0].startswith("av_offset (run)")
    assert all(row["result"] == "ok" and abs(row["residual_ms"]) < 0.5 and abs(row["lag_ms"] + 86.0) < 0.5
               for row in r["segments"])
    assert r["av_offset"]["confirmed"] and abs(r["av_offset"]["verified_ms"] + 86.0) < 0.5
    assert "A/V offset -86.0 ms (raw sync) confirmed" in r["summary"]
    # nothing published (today): every segment confidently misaligned
    r = verify.check_audio(segs, comp, rec, SRV, F30, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "fail" and len(r["failures"]) == 3 and "confidently misaligned" in r["failures"][0]
    # published 5 ms off: residuals are still inside +-10 ms, but verify's own estimate disagrees -> fail
    r = verify.check_audio(segs, comp, rec, SRV, F30, _offset(-81.0), [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "fail" and any("A/V offset not confirmed" in f for f in r["failures"]), r["failures"]
    assert not r["av_offset"]["confirmed"]


def test_c5_competitor_sync_judges_the_raw_lag(src):
    """--audio-sync competitor: the recreation already carries the offset -> lags ~0, plain pass (no exception),
    and the offset is still re-measured (median lag + the offset the recreation carries)."""
    comp = _delayed(src, 688)
    segs = [_seg(1, 0, 90), _seg(2, 90, 180), _seg(3, 180, 300)]
    r = verify.check_audio(segs, comp, comp, SRV, F30, _offset(-86.0, "competitor"), [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "pass" and r["exceptions"] == [] and r["av_offset"]["confirmed"]
    assert all(abs(row["lag_ms"]) < 0.5 for row in r["segments"])
    # a recreation that does NOT carry the offset in competitor mode is misaligned
    r = verify.check_audio(segs, comp, src, SRV, F30, _offset(-86.0, "competitor"), [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "fail"


def test_c5_inverted_range_and_run_level_code_fail(src):
    s = _seg(1, 30, 40, in_offset_frames=8, out_offset_frames=-6)          # [38, 34)
    r = verify.check_audio([_seg(2, 0, 30), s], src, src, SRV, F30, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "fail" and any("inverted audio range [38, 34)" in f for f in r["failures"])
    s = _seg(1, 0, 90, exception="av_offset")
    r = verify.check_audio([s], src, src, SRV, F30, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "fail" and "run-level code" in r["failures"][0]


def test_c5_short_segments_checked_as_an_aggregated_run(src):
    """Twelve 2-frame segments (0.8 s together), correct at the offset -> the aggregated run passes; one
    piece one RAW frame (41.7 ms) off -> exactly that piece fails (leave-one-out). A lone short segment
    stays an inconclusive too_short exception."""
    segs = [_seg(i + 1, 2 * i, 2 * i + 2) for i in range(12)] + [_seg(13, 24, 120)]
    comp = _delayed(src, 688)
    r = verify.check_audio(segs, comp, src, SRV, F30, _offset(-86.0), [], Config(), xcorr=xcorr_lag)
    rows = {row["id"]: row for row in r["segments"]}
    assert r["failures"] == [] and all(rows[i]["result"] == "ok" and rows[i]["run"] == "S01-S12" for i in range(1, 13))
    assert all(rows[i]["piece_corr"] > 0.9 for i in range(1, 13)) and rows[13]["result"] == "ok"
    bad = src.copy()
    a, b = round(10 / 30 * SRV), round(12 / 30 * SRV)                    # S06 = frames 10-11
    bad[a:b] = src[a + 334:b + 334]                                        # one RAW frame (41.7 ms) later
    r = verify.check_audio(segs, comp, bad, SRV, F30, _offset(-86.0), [], Config(), xcorr=xcorr_lag)
    assert [f.split(":")[0] for f in r["failures"]] == ["S06"], r["failures"]
    assert "does not follow its aggregated run" in r["failures"][0]
    three = [_seg(1, 0, 6), _seg(2, 6, 12), _seg(3, 12, 18), _seg(4, 18, 108)]      # 0.6 s of short pieces
    r = verify.check_audio(three, src, src, SRV, F30, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert r["status"] == "pass" and all(row["result"] == "ok" for row in r["segments"])
    r = verify.check_audio(three[:2] + [_seg(4, 12, 108)], src, src, SRV, F30, {"status": "ok"}, [], Config(),
                           xcorr=xcorr_lag)                                          # 0.4 s: inconclusive
    assert [row.get("code") for row in r["segments"]] == ["too_short", "too_short", None]
    r = verify.check_audio([Segment(3, "not_in_raw", 0, 30), _seg(1, 30, 36), Segment(4, "not_in_raw", 36, 60)],
                           src, src, SRV, F30, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert {row["id"]: row.get("code") for row in r["segments"]}[1] == "too_short"
    # a short piece the analysis explains itself keeps its own code and is no run member
    coded = [_seg(1, 0, 6), _seg(2, 6, 12, exception="pitch_preserved"), _seg(3, 12, 18), _seg(4, 18, 108)]
    r = verify.check_audio(coded, src, src, SRV, F30, {"status": "ok"}, [], Config(), xcorr=xcorr_lag)
    assert [row.get("code") for row in r["segments"]] == ["too_short", "pitch_preserved", "too_short", None]


def test_offset_expectation_windows():
    ex = verify.audio_offset_expectation(_offset(-86.0, "raw", 48.0), F30)
    assert ex["expected"] == pytest.approx(-0.086) and ex["shift_lo"] == pytest.approx(0.086)
    assert ex["shift_hi"] == pytest.approx(0.048)                     # comp switches at +48, rec (shifted) at +86
    ex = verify.audio_offset_expectation(_offset(-86.0, "competitor", 48.0), F30)    # rec switches 1 frame late
    assert ex["expected"] == 0.0 and ex["shift_lo"] == pytest.approx(0.048) and ex["shift_hi"] == pytest.approx(1 / 30)
    ex = verify.audio_offset_expectation(_offset(-86.0, "raw", None), F30)          # baseline unknown: 0..86 ms
    assert ex["shift_lo"] == pytest.approx(0.086) and ex["shift_hi"] == 0.0
    ex = verify.audio_offset_expectation({"status": "ok"}, F30)
    assert (ex["expected"], ex["shift_lo"], ex["shift_hi"]) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------------------------
# preview audio (render_preview.build_audio) and AE audio twins (export_ae)
# ---------------------------------------------------------------------------------------------

SR = 48000


def _cutlist(sync: str = "raw", lag_ms: float | None = None, baseline_ms: float | None = None) -> Cutlist:
    comp = {"file": "c.mp4", "width": 270, "height": 480, "fps": "30/1", "frames": 60}
    raw = {"file": "r.mp4", "width": 640, "height": 360, "fps": "30/1", "frames": 300, "has_audio": True,
           "audio_sample_rate": SR, "audio_channels": 1}
    layout = {"mode": "match", "box": None, "background": "solid", "background_detail": {"type": "solid"}}
    segs = [_seg(1, 0, 30, raw_in=1.0), _seg(2, 30, 60, raw_in=3.0)]
    audio = {"status": "ok"}
    if lag_ms is not None:
        audio["av_offset"] = {"status": "measured", "lag_ms": lag_ms, "switch_baseline_ms": baseline_ms}
    return Cutlist(1, comp, raw, layout, segs, audio=audio, settings={"audio_sync": sync})


def test_build_audio_competitor_sync_shift():
    y = (np.random.default_rng(4).standard_normal(SR * 6) * 0.1).astype(np.float32)
    cl = _cutlist()
    base = rp.build_audio(cl, y, SR)
    n = base.size
    # 100 ms = 3 frames: content and switches move together -> exactly the delayed raw-sync rebuild
    shifted = rp.build_audio(cl, y, SR, av_offset_lag_s=-0.1, switch_baseline_s=0.1)
    np.testing.assert_allclose(shifted, np.pad(base, (4800, 0))[:n], atol=1e-6)
    # 86 ms: content sample-accurate; switches on whole frames (3 frames = 100 ms, like the AE twins)
    s86 = rp.build_audio(cl, y, SR, av_offset_lag_s=-0.086, switch_baseline_s=0.086)
    ref = np.pad(base, (4128, 0))[:n]
    keep = np.ones(n, bool)
    keep[4128:4800] = False
    keep[SR + 4128:SR + 4800] = False
    np.testing.assert_allclose(s86[keep], ref[keep], atol=1e-6)
    assert np.all(s86[:4800] == 0.0)
    # the published offset is used in competitor sync only
    assert np.array_equal(rp.build_audio(_cutlist("competitor", -86.0, 86.0), y, SR), s86)
    assert np.array_equal(rp.build_audio(_cutlist("raw", -86.0, 86.0), y, SR), base)
    assert ea.audio_sync_params(_cutlist("competitor", -86.0, 48.0), F30) == (pytest.approx(-0.086), 1)
    assert ea.audio_sync_params(_cutlist("competitor", -86.0, None), F30) == (pytest.approx(-0.086), 0)
    with pytest.raises(ValueError):
        ea.audio_sync_params(_cutlist("bogus"), F30)


def _ae_cutlist(sync: str, jl: bool):
    import test_export_ae as tea
    segs = tea.make_segments()
    if not jl:
        segs[-1].audio = dict(segs[-1].audio, in_offset_frames=0)
    cl = tea.make_cutlist(segments=segs)
    cl.audio = {"status": "ok", "av_offset": {"status": "measured", "lag_ms": -86.0, "switch_baseline_ms": 48.0,
                                              "sync_mode": sync}}
    cl.settings = {"audio_sync": sync}
    return tea, cl


def test_ae_twins_raw_sync_only_for_genuine_jl():
    tea, cl = _ae_cutlist("raw", jl=False)
    plan = ea.ae_plan(cl, Config(), tea.meta_for(cl))
    assert [L for L in plan["layers"] if L["kind"] == "raw_audio"] == []           # 86 ms offset, no J/L: 0 twins
    assert plan["audioSync"] == {"mode": "raw", "lagMs": 0.0, "switchShiftFrames": 0, "twins": 0}
    tea, cl = _ae_cutlist("raw", jl=True)
    twins = [L for L in ea.ae_plan(cl, Config(), tea.meta_for(cl))["layers"] if L["kind"] == "raw_audio"]
    assert [L["id"] for L in twins] == ["seg9_audio"] and twins[0]["note"] == "J/L cut"


def test_ae_twins_competitor_sync(tmp_path):
    """Every RAW segment's audio on an audio-only twin: source time shifted by v * g (sample-accurate through
    the twin's own startTime), in/out at the cut + round(baseline x fps) (+ genuine J/L); video layers
    silent; no container shift on top (no double counting)."""
    tea, cl = _ae_cutlist("competitor", jl=True)
    cfg = Config(audio_sync="competitor")
    plan = ea.ae_plan(cl, cfg, tea.meta_for(cl))
    by = {L["id"]: L for L in plan["layers"]}
    raw_ids = [f"seg{s.id}" for s in cl.segments if s.type == "raw"]
    assert all(by[i]["audio"] is False for i in raw_ids)
    assert plan["audioSync"]["mode"] == "competitor" and plan["audioSync"]["switchShiftFrames"] == 1
    assert plan["audioSync"]["lagMs"] == pytest.approx(-86.0)
    s1, t1 = by["seg1"], by["seg1_audio"]
    assert (t1["compIn"], t1["compOut"]) == (1, 46) and t1["timeMode"] == "stretch" and t1["enabled"] is False
    assert t1["rawIn"] == pytest.approx(s1["rawIn"] + 1 / 30 - 0.086, abs=1e-12)
    assert t1["startTime"] == pytest.approx(s1["startTime"] + 0.086, abs=1e-9)      # the whole mix plays 86 ms later
    t2 = by["seg2_audio"]                                                            # speed 1.1: RAW shift v * g
    assert t2["rawIn"] == pytest.approx(by["seg2"]["rawIn"] + 1.1 * (1 / 30 - 0.086), abs=1e-12)
    t9 = by["seg9_audio"]                                                            # genuine J-cut kept on top
    assert (t9["compIn"], t9["compOut"]) == (280 - 4 + 1, 300)
    assert by["seg8_audio"]["timeMode"] == "remap"                                   # freeze: curve 86 ms later
    assert by["seg8_audio"]["remap"][0]["k"] == pytest.approx(by["seg8"]["remap"][0]["k"] + 0.086 * 30)
    if ea._find_node() is None:
        pytest.skip("node not available for the mock run")
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, cfg)
    tea.place_media(tmp_path, cl)
    res = ea.mock_verify(jsx, plan, tea.meta_for(cl))
    assert res["status"] == "pass", res["failures"]


# ---------------------------------------------------------------------------------------------
# CLI and report
# ---------------------------------------------------------------------------------------------

def test_cli_audio_sync_flag():
    p = cli.build_parser()
    args = p.parse_args(["--competitor", "a.mp4", "--raw", "b.mp4"])
    assert cli.config_from_args(args).audio_sync == "raw"
    args = p.parse_args(["--competitor", "a.mp4", "--raw", "b.mp4", "--audio-sync", "competitor"])
    assert cli.config_from_args(args).audio_sync == "competitor"
    with pytest.raises(SystemExit):
        p.parse_args(["--audio-sync", "both"])
    assert "audio_sync" not in Config().analysis_params()


def test_report_av_offset_line():
    cl = SimpleNamespace(audio={"av_offset": {"status": "measured", "lag_ms": -86.0, "lag_ms_interval": [-86.2, -85.8],
                                              "n_segments": 34, "coverage": 1.0, "switch_baseline_ms": 48.0,
                                              "sync_mode": "raw",
                                              "text": "competitor audio is 86.0 ms later than its picture, relative to "
                                                      "RAW's own A/V sync"}}, settings={})
    line = report.av_offset_line(cl)
    assert line.startswith("competitor audio is 86.0 ms later than its picture, relative to RAW's own A/V sync")
    assert "lag -86.0 ms" in line and "34 segment(s), coverage 100%" in line and "+48.0 ms after each picture cut" in line
    assert "keeps RAW lip-sync" in line
    cl.audio["av_offset"].update(status="zero", lag_ms=0.0, text="competitor audio is in sync with its picture, "
                                                                 "relative to RAW's own A/V sync")
    assert "0 ms is consistent with 34 segment(s)" in report.av_offset_line(cl)
    assert report.av_offset_line(SimpleNamespace(audio={}, settings={})) == ""
