"""report.py renders every prompt Stage 10 section from a hand-made context (no analysis modules)."""
from __future__ import annotations

import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import report
from match_cuts.config import Config
from match_cuts.geometry import Sim
from match_cuts.model import Box, Cutlist, FrameMap, Layout, Segment, Status, StreamInfo, Zone, cutlist_layout

C_FPS, R_FPS = Fraction(30), Fraction(30000, 1001)


def _info(role, path, w, h, fps, n, **kw) -> StreamInfo:
    return StreamInfo(path=path, role=role, container="mov,mp4,m4a,3gp,3g2,mj2", vcodec="h264", vprofile="High",
                      pix_fmt="yuv420p", width=w, height=h, display_width=w, display_height=h, fps=fps,
                      r_frame_rate=fps, avg_frame_rate=fps, nb_frames=n, duration=n / float(fps), has_audio=True,
                      acodec="aac", a_sample_rate=48000, a_channels=2, file_hash="ab" * 20, **kw)


def _segments() -> list[Segment]:
    ident = Sim(0.5, 0.0, 60.0, 460.0).to_dict()
    xf = {"type": "crossfade", "duration_frames": 6, "alpha": [i / 6 for i in range(6)]}
    keys = [{"comp_frame": 40, "scale": 0.5, "rotation_deg": 0.0, "tx": 60.0, "ty": 460.0},
            {"comp_frame": 89, "scale": 0.6, "rotation_deg": 0.0, "tx": 10.0, "ty": 400.0}]

    def raw(id_, a, b, j0, speed=1.0, **kw):
        s = Segment(id_, "raw", a, b, raw_in_frame=j0, raw_in_seconds=(j0 + 0.25) / float(R_FPS),
                    raw_out_frame=j0 + int((b - a) * speed * float(R_FPS) / 30) - 1, speed=speed, confidence=0.97, **kw)
        if s.transform is None and not s.transform_keys:
            s.transform = dict(ident)
        s.raw_in_interval = [s.raw_in_seconds - 1e-4, s.raw_in_seconds + 1e-4]
        s.raw_in_interval_both = list(s.raw_in_interval)
        s.ae_margin_ms = 0.1 if speed != 1.0 else 16.0
        return s
    s1 = raw(1, 0, 40, 3000)                                       # hook from later in RAW
    s2 = raw(2, 40, 96, 100, flip_h=True, transform_keys=keys, transition_out=xf, easing="ease_in")
    s3 = raw(3, 90, 150, 400, transition_in=xf, ambiguous_frames=[120, 121])
    s4 = Segment(4, "not_in_raw", 150, 180, label="MISSING - not in RAW (00:00:05:00-00:00:06:00, frames 150-179)")
    s5 = raw(5, 180, 240, 700, speed=1.1, unsnapped=True, tie_frames=[200])
    s5.raw_in_interval_both = None
    s5.audio = {"in_offset_frames": -4, "out_offset_frames": 0, "pitch_preserved": True, "lag_ms": 1.2, "corr": 0.8,
                "exception": "pitch_preserved"}
    s6 = Segment(6, "dip", 240, 243, color="#000000")
    s7 = raw(7, 243, 270, 5000, speed=0.0, time_remap_keys=[{"comp_frame": 243, "raw_seconds": 5000.25 / float(R_FPS)},
                                                          {"comp_frame": 269, "raw_seconds": 5000.25 / float(R_FPS)}])
    s7.time_mode, s7.raw_out_frame = "remap", 5000                  # freeze frame
    s8 = raw(8, 270, 300, 6000, transform=Sim(0.55, 1.5, 40.0, 430.0).to_dict())    # rotated shot
    return [s1, s2, s3, s4, s5, s6, s7, s8]


def _layout() -> Layout:
    return Layout(1080, 1920, mode="boxed", box=Box(60, 460, 960, 1000, 36),
                  background={"type": "solid", "color": "#000000"},
                  zones=[Zone("logo", 40, 60, 160, 160), Zone("title", 90, 260, 900, 140, text="multicoloured title")],
                  captions=[{"comp_in": 12, "comp_out": 40, "x": 100, "y": 1200, "w": 880, "h": 90},
                            {"comp_in": 40, "comp_out": 70, "x": 100, "y": 1205, "w": 800, "h": 90}],
                  notes=["box edges refined to sub-pixel"])


def _fm() -> FrameMap:
    n = 300
    fm = FrameMap(n)
    fm.status = np.full(n, Status.MATCH, np.int8)
    fm.status[150:180] = Status.NONE
    fm.status[90:96] = Status.BLEND
    fm.raw = np.arange(n, dtype=np.int32)
    fm.raw_lo = fm.raw
    fm.raw_hi = fm.raw
    fm.raw_hi[120:122] = fm.raw[120:122] + 1
    fm.conf = np.full(n, 0.95, np.float32)
    fm.conf[[30, 31, 200]] = 0.2
    fm.tie[200] = True
    return fm


def make_ctx(tmp_path: Path, verified: bool = True):
    cfg = Config()
    cfg.competitor, cfg.raw = "input/their_edit.mp4", "input/source.webm"
    cfg.out_dir, cfg.work_dir = str(tmp_path / "output"), str(tmp_path / "work")
    comp_info = _info("competitor", str(tmp_path / "output/media/competitor_ref.mp4"), 1080, 1920, C_FPS, 300)
    raw_in = _info("raw", "input/source.webm", 1920, 1080, R_FPS, 9000, vfr=True, pts_jitter=0.4, v_start_time=0.021,
                   ae_issues=["codec vp9", "VFR", "start_time 0.021 s"])
    raw_in.vcodec, raw_in.container, raw_in.acodec = "vp9", "matroska,webm", "opus"
    raw_info = _info("raw", str(tmp_path / "output/media/raw_ae.mov"), 1920, 1080, R_FPS, 9000)
    raw_info.vcodec = "prores"
    raw_conf = types.SimpleNamespace(path=raw_info.path, conformed=True, reason="codec vp9, VFR, start_time 0.021 s",
                                     verification={"frames_ok": True, "sampled": 50, "min_ssim": 0.991, "ok": True},
                                     source_path="input/source.webm", file_rel="media/raw_ae.mov", file_abs=raw_info.path)
    comp_conf = types.SimpleNamespace(path=comp_info.path, conformed=False, reason="already AE-safe", verification={},
                                      source_path="input/their_edit.mp4", file_rel="media/competitor_ref.mp4",
                                      file_abs=comp_info.path)
    lay = _layout()
    segs = _segments()
    cl = Cutlist(1, {"file": "media/competitor_ref.mp4", "width": 1080, "height": 1920, "fps": "30/1", "frames": 300},
                 {"file": "media/raw_ae.mov", "width": 1920, "height": 1080, "fps": "30000/1001", "frames": 9000,
                  "conformed": True},
                 cutlist_layout(lay, "match"), segs,
                 overlays_detected=[{"type": "logo", "comp_in": 0, "comp_out": 300},
                                    {"type": "captions", "comp_in": 12, "comp_out": 40, "x": 100, "y": 1200, "w": 880, "h": 90},
                                    {"type": "captions", "comp_in": 40, "comp_out": 70, "x": 100, "y": 1205, "w": 800, "h": 90}],
                 added_audio=[{"type": "music", "comp_in": 0, "comp_out": 300, "level_db": -12.0}],
                 audio={"status": "ok", "notes": ["music bed under the whole edit"]},
                 settings={"layout_mode": "match", "comp_size": "competitor", "main_size": [1080, 1920], "fps_mode": "competitor",
                           "main_fps": "30/1", "fps_source_max_error_s": 0.0, "ae_time_mode": "auto", "criteria_exact": True},
                 warnings=["S05: speed 1.1000 could not be snapped to a common value"],
                 provenance={"version": "0.1.0", "timings": {"S2 probe+conform": 12.5}})
    ver = {}
    if verified:
        ver = {"criteria": {
            "c1_coverage": {"status": "pass", "summary": "8 segments, 300/300 frames"},
            "c2_cuts": {"status": "pass", "summary": "7 cuts verified",
                        "details": {"cuts": [{"from": 1, "to": 2, "frame": 40, "tc": "00:00:01:10", "kind": "hard",
                                              "status": "pass"}]}},
            "c3_source_frames": {"status": "pass_with_exceptions", "summary": "2 ambiguous-identical"},
            "c4_speed_framing": {"status": "pass_with_exceptions", "summary": "S05 unsnapped"},
            "c5_audio": {"status": "pass_with_exceptions", "summary": "S05 pitch_preserved"},
            "c6_after_effects": {"status": "pass", "summary": "mock run: 13/13 checks ok",
                                 "details": {"mock_only": True, "mock": {"checks": [{"check": "MAIN comp created", "ok": True}]}}}},
            "checks": {"s9_3_visual": {"status": "pass", "summary": "min 0.95",
                                       "distribution": {"min": 0.95, "p1": 0.96, "p5": 0.97, "median": 0.99, "mean": 0.985,
                                                        "hist": {"0.90-0.95": 0, "0.95-0.98": 12, "0.98-0.99": 40}},
                                       "threshold": 0.9, "source": "preview_recreation.mp4", "failed_frames": []},
                       "s9_5_audio": {"status": "pass_with_exceptions", "summary": "ok", "tolerance_ms": 10.0,
                                      "audio_source": "analysis rate 16000 Hz",
                                      "segments": [{"id": 1, "result": "ok", "lag_ms": 0.3, "corr": 0.9},
                                                   {"id": 5, "result": "exception", "code": "pitch_preserved"}]},
                       "s9_7_determinism": {"status": "pass", "summary": "byte-identical"}},
            "failures": []}
    return types.SimpleNamespace(
        cfg=cfg, env={"os": "Linux", "platform": "Linux-x86_64", "python": "3.12.3", "ffmpeg_version": "6.1.1",
                      "node_version": "v22", "ae_app": None, "aerender": None, "tool_version": "0.1.0"},
        comp_input=comp_info, raw_input=raw_in, comp_info=comp_info, raw_info=raw_info, comp_conform=comp_conf,
        raw_conform=raw_conf, layout=lay, cutlist=cl, fm=_fm(), verify=ver,
        timings={"S0 env": 0.2, "S2 probe+conform": 12.5, "S9 verify": 30.1, "total": 88.0},
        warnings=["S05: speed 1.1000 could not be snapped to a common value", "compare.mp4 took long"],
        errors=[{"stage": "S8 EDL", "error": "ValueError: boom"}],
        paths={"cutlist": str(tmp_path / "output/cutlist.json"), "jsx": str(tmp_path / "output/build_ae_project.jsx"),
               "decisions": str(tmp_path / "work/decisions.jsonl")})


def test_report_renders_every_section(tmp_path):
    ctx = make_ctx(tmp_path)
    path = tmp_path / "output" / "report.md"
    report.write_report(ctx, path)
    md = path.read_text()
    titles = [t for t, _ in report.SECTIONS]
    for i, t in enumerate(titles, start=1):
        assert f"## {i}. {t}" in md
    assert "Section could not be rendered" not in md
    # criteria table + overall
    assert "**Overall: PASS**" in md
    for title in report.CRITERIA_TITLES.values():
        assert f"| {title} |" in md
    assert "PASS (with exceptions)" in md and "9.7 Determinism" in md and "mock" in md
    # inputs: codecs, fps, VFR, offsets, conform + why
    assert "vp9" in md and "30000/1001 (29.970)" in md and "VFR (PTS jitter 0.400 frames)" in md
    assert "0.021000s" in md and "transcoded — codec vp9, VFR, start_time 0.021 s" in md and "min_ssim=0.991" in md
    # layout
    assert "x 60.00, y 460.00, w 960.00, h 1000.00" in md and "corner radius 36.00" in md
    assert "Background: solid (color #000000)" in md and "| title |" in md and "debug/layout.png" in md
    assert "2 caption events" in md
    # segment table: one row per segment with the special cases
    rows = [ln for ln in md.splitlines() if ln.startswith("| S0") and "f / " in ln]
    assert len(rows) == 8
    assert "animated (2 keys, ease_in)" in rows[1] and "| yes |" in rows[1] and "out: crossfade 6f" in rows[1]
    assert "in: crossfade 6f" in rows[2] and "2 ambiguous-identical" in rows[2]
    assert "MISSING - not in RAW" in rows[3]
    assert "1.1000 (unsnapped)" in rows[4] and "J/L audio -4/0f" in rows[4] and "1 timing-tie" in rows[4]
    assert "dip #000000" in rows[5] and "freeze" in rows[6] and "rot 1.50°" in rows[7]
    assert "00:00:01:10" in rows[1]                                 # comp timecode of frame 40
    # breakdown
    assert "cuts: 7" in md and "hook taken from later in RAW" in md and "1.100× (1 segment)" in md
    assert "Horizontal flips: 1 (S02)" in md and "Animated zooms/pans: 1 (S02)" in md
    assert "Transitions: crossfade ×1, dip ×1" in md and "Freeze / reverse / ramp (time-remapped): S07" in md
    assert "Captions: 2 events" in md and "music 00:00:00:00–00:00:10:00 (-12.0 dB)" in md
    assert "Pitch preserved on speed-changed segments" in md
    # warnings
    assert "Low-confidence frames (conf < 0.5): 3" in md and "debug/low_confidence/" in md
    assert "Ambiguous-identical frames (neighbouring RAW frames identical): 2" in md
    assert "Timing-tie frames (AE floor/round may differ by one frame): 1" in md
    assert "NOT-IN-RAW ranges: 150–179" in md
    assert "AE-rule-sensitive segments" in md and "S05 (0.1 ms)" in md
    assert "pitch-preserved speed change" in md and "compare.mp4 took long" in md and "S8 EDL: ValueError: boom" in md
    # verification details, AE how-to, outputs, timings
    assert "| 0.95-0.98 | 12 |" in md and "pitch_preserved" in md and "[x] MAIN comp created" in md
    assert "File → Scripts → Run Script File…" in md and "Allow Scripts to Write Files and Access Network" in md
    assert "Difference" in md and "media/raw_ae.mov" in md
    assert "| cutlist | cutlist.json |" in md and "../work/decisions.jsonl" in md
    assert "| S9 verify | 30.10 |" in md and "| total | 88.00 |" in md


def test_report_without_verification_and_with_a_broken_section(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, verified=False)
    ctx.fm = None
    md = report.render_report(ctx)
    assert "**Overall: not verified**" in md and "Verification did not run." in md
    assert "Ambiguous-identical frames: 2" in md                   # falls back to the segments' lists

    def broken(ctx):
        raise RuntimeError("section bug")
    sections = list(report.SECTIONS)
    sections[4] = ("Edit-style breakdown", broken)
    monkeypatch.setattr(report, "SECTIONS", sections)
    md = report.render_report(ctx)
    assert "_Section could not be rendered: RuntimeError: section bug_" in md
    assert "## 6. Warnings" in md and "## 10. Environment and timings" in md


def test_edit_breakdown_numbers(tmp_path):
    ctx = make_ctx(tmp_path)
    b = report.edit_breakdown(ctx.cutlist, ctx.layout, ctx.cfg)
    assert b["cuts"] == 7 and b["raw_segments"] == 6 and b["segments"] == 8
    assert b["hook"] and b["non_chronological"] == [2]             # only S02 jumps back (3000+ -> 100)
    assert b["flips"] == [2] and b["animated"] == [2] and b["rotations"] == [8]
    assert b["freeze_reverse_ramp"] == [7]
    assert b["not_in_raw"] == [(150, 180)]
    assert b["reuse"] == [] and b["raw_frames"] == 9000
    used = b["raw_used_frames"]
    assert 0 < used < 9000 and b["raw_used_pct"] == pytest.approx(100 * used / 9000)
    assert b["raw_cut_out"][0] == (0, 99)                          # nothing before RAW frame 100 is used
    # re-use: a second segment on the same RAW moment
    extra = Segment(9, "raw", 300, 310, raw_in_frame=105, raw_out_frame=114, speed=1.0, transform=Sim().to_dict())
    ctx.cutlist.segments.append(extra)
    b = report.edit_breakdown(ctx.cutlist)
    assert (105, 114, 2, 9) in b["reuse"]


def test_md_table_escapes_pipes():
    t = report.md_table(["a", "b"], [["x|y", None]])
    assert t.splitlines()[2] == "| x\\|y |  |"
