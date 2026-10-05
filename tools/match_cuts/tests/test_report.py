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
    assert "in: crossfade 6f" in rows[2] and "2 frames with identical RAW neighbours" in rows[2]
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
    assert "NOT-IN-RAW ranges (every hypothesis below none_thresh): 150–179" in md and "UNCERTAIN ranges" in md
    # FX-10: the 1.1x layer's phase is fixed by the 29.97-in-30 lattice (cells of ~1/91 frame): information
    assert ("Phase pinned by cadence (information, not a risk): 1 segment(s) — S05 (±0.092 ms, frame-rate lattice)"
            in md)
    assert "AE-rule-sensitive segments (exact floor-rule slack below 0.01 RAW frame although more was possible): none" in md
    assert "pitch-preserved speed change" in md and "compare.mp4 took long" in md and "S8 EDL: ValueError: boom" in md
    # verification details, AE how-to, outputs, timings
    assert "| 0.95-0.98 | 12 |" in md and "pitch_preserved" in md and "[x] MAIN comp created" in md
    assert "File → Scripts → Run Script File…" in md and "Allow Scripts to Write Files and Access Network" in md
    assert "Difference" in md and "media/raw_ae.mov" in md
    assert "| cutlist | cutlist.json |" in md and "../work/decisions.jsonl" in md.replace("\\", "/")
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
    sections[5] = ("Edit-style breakdown", broken)
    monkeypatch.setattr(report, "SECTIONS", sections)
    md = report.render_report(ctx)
    assert "_Section could not be rendered: RuntimeError: section bug_" in md
    assert "## 7. Warnings" in md and "## 11. Environment and timings" in md


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


def test_edit_breakdown_reframe_on_one_time_line_is_no_reuse(tmp_path):
    """FX-04 8: two segments on ONE time line split by a reframe (the cut inside a 23.976 -> 30 repeat: both show
    RAW 4120) are a reframe, not a re-used RAW moment nor a jump back."""
    ctx = make_ctx(tmp_path)
    last = max(ctx.cutlist.segments, key=lambda s: s.comp_out)
    k0 = last.comp_out
    a = Segment(20, "raw", k0, k0 + 20, raw_in_frame=4105, raw_out_frame=4120, speed=1.0, transform=Sim().to_dict())
    b_ = Segment(21, "raw", k0 + 20, k0 + 40, raw_in_frame=4120, raw_out_frame=4135, speed=1.0,
                 transform=Sim(1.25, 0.0, -10.0, -20.0).to_dict())
    ctx.cutlist.segments += [a, b_]
    ctx.cutlist.raw["fps"] = "24000/1001"
    b = report.edit_breakdown(ctx.cutlist)
    assert not any(r[2:] == (20, 21) for r in b["reuse"])
    assert (20, 21) in b["reframes"] and 21 not in b["non_chronological"]


def test_md_table_escapes_pipes():
    t = report.md_table(["a", "b"], [["x|y", None]])
    assert t.splitlines()[2] == "| x\\|y |  |"


# ---------------------------------------------------------------------------------------------
# review fixes: headline (D5), per-event caption counts (REQ-7), re-assigned / not-reproduced frames
# (verification-honesty F1/F2, time-math F1), full-screen / split periods (REQ-3), MAIN-grid note (REQ-2)
# ---------------------------------------------------------------------------------------------

def _ver(statuses: dict, checks: dict | None = None) -> dict:
    crit = {k: {"status": v, "summary": f"{k} summary"} for k, v in statuses.items()}
    return {"criteria": crit, "checks": {k: {"status": v} for k, v in (checks or {}).items()}}


ALL = {k: "pass" for k in ("c1_coverage", "c2_cuts", "c3_source_frames", "c4_speed_framing", "c5_audio",
                           "c6_after_effects")}


def test_headline_d5():
    assert report.headline(None) == "not verified"
    assert report.headline(_ver(ALL, {"s9_7_determinism": "pass", "s9_8_deliverables": "pass"})) == "PASS"
    assert report.headline(_ver({**ALL, "c3_source_frames": "pass_with_exceptions"})) == "PASS"
    assert report.headline(_ver({**ALL, "c2_cuts": "fail"})) == "FAIL"
    assert report.headline(_ver(ALL, {"s9_8_deliverables": "fail"})) == "FAIL"          # a failed check fails
    assert report.headline(_ver(ALL, {"s9_7_determinism": "fail"})) == "FAIL"
    assert report.headline(_ver({k: v for k, v in ALL.items() if k != "c5_audio"})) == "FAIL"   # incomplete
    assert report.headline(_ver({**ALL, "c4_speed_framing": "weird"})) == "FAIL"
    h = report.headline(_ver({**ALL, "c6_after_effects": "not_available"}, {"s9_6_ae_render": "not_available"}))
    assert h == "PASS (criterion 6 not verified: c6_after_effects summary)"
    h = report.headline(_ver({**ALL, "c5_audio": "not_available", "c6_after_effects": "not_available"}))
    assert h.startswith("PASS (criteria 5, 6 not verified: ")


def test_headline_says_which_hard_check_could_not_run():
    """Task 10: a hard check of 1_edit.xml whose analysis failed is named in the headline (as pipeline.headline_for
    names it), never a plain PASS."""
    ver = _ver(ALL, {"s9_8_deliverables": "pass"})
    ver["checks"]["hard_checks_not_run"] = {"status": "not_available", "not_verified": ["no flash frames at a cut (x)"]}
    assert report.headline(ver) == "PASS (not checked: no flash frames at a cut (x))"
    ver["criteria"]["c6_after_effects"] = {"status": "not_available", "summary": "no node"}
    assert report.headline(ver) == "PASS (criterion 6 not verified: no node; not checked: no flash frames at a cut (x))"
    ver["checks"]["s9_8_deliverables"]["status"] = "fail"
    assert report.headline(ver) == "FAIL"


def test_report_overall_uses_the_d5_headline(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.verify["checks"]["s9_8_deliverables"] = {"status": "fail", "summary": "7/8 deliverables present",
                                                 "failures": ["recreated_edit.xml missing"]}
    md = report.render_report(ctx)
    assert "**Overall: FAIL**" in md and "| 9.8 Deliverables | FAIL | 7/8 deliverables present |" in md
    ctx.verify["checks"]["s9_8_deliverables"]["status"] = "pass"
    ctx.verify["criteria"]["c6_after_effects"] = {"status": "not_available", "summary": "mock not available: node missing"}
    md = report.render_report(ctx)
    assert "**Overall: PASS (criterion 6 not verified: mock not available: node missing)**" in md


def test_caption_counts_are_per_event(tmp_path):
    """REQ-7: the aggregate caption zone and other text events were counted as captions (33 / 34 / 35 for
    the same 33 events)."""
    ctx = make_ctx(tmp_path)
    cl = ctx.cutlist
    cl.layout["zones"].append({"type": "captions", "x": 100.0, "y": 1200.0, "w": 880.0, "h": 90.0, "comp_in": 12,
                               "comp_out": 70, "notes": "2 caption events"})
    cl.layout["captions"].append({"type": "text", "comp_in": 80, "comp_out": 120, "x": 300, "y": 500, "w": 200, "h": 40})
    cl.overlays_detected = [{"type": "logo", "comp_in": 0, "comp_out": 300, "x": 40.0, "y": 60.0, "w": 160.0, "h": 160.0,
                             "static": True},
                            {"type": "captions", "comp_in": 12, "comp_out": 70, "x": 100.0, "y": 1200.0, "w": 880.0,
                             "h": 90.0, "static": False, "notes": "zone"},
                            {"type": "captions", "comp_in": 12, "comp_out": 40, "x": 100, "y": 1200, "w": 880, "h": 90},
                            {"type": "captions", "comp_in": 40, "comp_out": 70, "x": 100, "y": 1205, "w": 800, "h": 90},
                            {"type": "text", "comp_in": 80, "comp_out": 120, "x": 300, "y": 500, "w": 200, "h": 40}]
    assert [c["comp_in"] for c in report.caption_events(cl)] == [12, 40]
    md = report.render_report(ctx)
    assert "- Captions: 2 caption events" in md and "- Captions: 2 events" in md
    assert "3 caption" not in md and "Captions: 3" not in md
    assert "Other overlaid text / stickers: 1 text event" in md and "Other overlaid text / stickers: text ×1" in md
    assert "- Static overlays: logo" in md


def test_reassigned_frames_are_listed_separately(tmp_path):
    """verification-honesty F2 / time-math F1: frames segmentation re-assigned to its model were labelled
    'Low-margin frames (... < 0.001)'. They get their own line (measured -> model, score gap); the
    low-margin line keeps refine's own flags only."""
    ctx = make_ctx(tmp_path)
    fm = ctx.fm
    for k in ("status", "raw", "raw_lo", "raw_hi", "low_margin"):
        fm.d["pre_segment_" + k] = np.asarray(fm.d[k]).copy()
    fm.d["pre_segment_raw"][60] = 61                      # refine measured 61, the segment model shows 60
    fm.cand_j0[60] = 55
    fm.cand[60, 5], fm.cand[60, 6] = 0.95, 0.99
    fm.low_margin[60] = True                              # write_back flags it low_margin
    fm.d["pre_segment_low_margin"][200] = True            # refine's own low-margin frame
    fm.low_margin[200] = True
    rows = report.reassigned_frames(fm)
    assert rows == [{"k": 60, "measured": 61, "model": 60, "gap": pytest.approx(0.04)}]
    md = report.render_report(ctx)
    low = next(ln for ln in md.splitlines() if ln.startswith("- Low-margin frames"))
    assert ": 1 — 200" in low
    re_ln = next(ln for ln in md.splitlines() if ln.startswith("- Re-assigned by segmentation"))
    assert "measured 61 → model 60 (score gap 0.0400)" in re_ln


def test_frames_not_reproduced_exactly_are_listed(tmp_path):
    """verification-honesty F1: s9_2 mismatches never reached the report's warnings."""
    ctx = make_ctx(tmp_path)
    ctx.verify["checks"]["s9_2_ae_sim"] = {"status": "fail", "plan": {
        "plan_mismatches": [{"K": 194, "k": 194}, {"K": 195, "k": 195}], "n_plan_mismatches": 2,
        "reassigned": [{"k": 205}], "n_reassigned": 1, "mismatches": [{"k": 300}], "n_mismatches": 1}, "mock": {}}
    md = report.render_report(ctx)
    ln = next(ln for ln in md.splitlines() if ln.startswith("- Frames not reproduced exactly (s9_2)"))
    assert "AE plan: 2 differ from the cutlist (MAIN frames) (194-195)" in ln
    assert "1 re-assigned by segmentation (205)" in ln and "1 AE frame ≠ measured m(k) (300)" in ln


def test_fullscreen_and_split_periods_in_the_report(tmp_path):
    """REQ-3: full-screen periods are listed as reproduced; split / PiP periods under 'Anything AE can't
    reproduce' (they printed 'nothing detected')."""
    ctx = make_ctx(tmp_path)
    ctx.cutlist.layout["periods"] = [{"comp_in": 0, "comp_out": 40, "mode": "fullscreen"},
                                     {"comp_in": 40, "comp_out": 270, "mode": "boxed"},
                                     {"comp_in": 270, "comp_out": 300, "mode": "split"}]
    ctx.cutlist.segments[0].box = {"x": 0, "y": 0, "w": 1080, "h": 1920, "corner_radius": 0}
    md = report.render_report(ctx)
    assert "- Full-screen periods (reproduced: full-canvas layers directly in MAIN): 0–39" in md
    cant = next(ln for ln in md.splitlines() if ln.startswith("- Anything AE can't reproduce"))
    assert "frames 270–299: split layout" in cant and "nothing detected" not in cant
    assert "0–40 fullscreen (reproduced" in md and "270–300 split (NOT reproduced" in md
    ctx.cutlist.segments[0].box = None                   # a full-screen shot rebuilt inside the box is listed
    md = report.render_report(ctx)
    assert "S01: frames 0-39" in md and "shown full-screen by the competitor but rebuilt inside the video box" in md


def test_fullscreen_boundary_sliver_is_not_reported_as_unreproducible(tmp_path):
    """Integration (second review round R2-5): the report uses verify's c1 rule -- a boxless segment whose only
    frames in a full-screen period are a 1-2 frame sliver at the detected boundary (merged by segmentation)
    is explained, not 'rebuilt inside the video box'."""
    ctx = make_ctx(tmp_path)
    s0 = ctx.cutlist.segments[0]
    a = int(s0.comp_out) - 2                             # the period's detected start lies 2 frames early
    ctx.cutlist.layout["periods"] = [{"comp_in": 0, "comp_out": a, "mode": "boxed"},
                                     {"comp_in": a, "comp_out": a + 30, "mode": "fullscreen"}]
    s0.box = None
    nxt = next(s for s in ctx.cutlist.segments if s.comp_in == s0.comp_out)
    nxt.box = {"x": 0, "y": 0, "w": 1080, "h": 1920, "corner_radius": 0}
    md = report.render_report(ctx)
    cant = next(ln for ln in md.splitlines() if ln.startswith("- Anything AE can't reproduce"))
    assert f"S{s0.id:02d}" not in cant, cant


def test_main_grid_note_names_criterion_3(tmp_path):
    """REQ-2 / time-math F3: the note said only criteria 2 and 6 are inexact on a different MAIN grid."""
    ctx = make_ctx(tmp_path)
    ctx.cutlist.settings.update(criteria_exact=False, fps_mode="source", main_fps="30000/1001",
                                fps_source_max_error_s=0.0166)
    md = report.render_report(ctx)
    note = next(ln for ln in md.splitlines() if ln.startswith("_MAIN runs at"))
    assert "criterion 3 accepts" in note and "Criteria 2, 3 and 6 are frame-exact only with `--fps competitor`" in note
    assert "max error 16.600 ms" in note


def test_independent_check_findings_are_listed_with_frames(tmp_path):
    """FX-01: the hypothesis-neutral checks' failures (temporal signature, motion mismatch, +-1 refit, spurious
    cuts / repeat pairs / excursions) reach the warnings and the verification section with frame lists."""
    ctx = make_ctx(tmp_path)
    ch = ctx.verify["checks"]
    ch["s9_2b_temporal"] = {"status": "fail", "summary": "x", "pairs": 59, "n_disagreements": 3,
                            "disagreements": [{"k": 20, "kind": "recreation_changes"}, {"k": 40, "kind": "recreation_changes"},
                                              {"k": 7, "kind": "recreation_repeats"}],
                            "motion_mismatch": [{"segment": 65, "frames": [1194, 1203], "comp_move": 8, "comp_repeat": 0}],
                            "labels": {"counts": {"repeat": 12, "move": 40, "unknown": 5, "cut": 2}}}
    ch["s9_2c_refit"] = {"status": "fail", "summary": "y", "n_neighbour_wins": 2,
                         "neighbour_wins": [{"k": 1423, "raw": 2048, "z_shown": 0.97, "best_neighbour": 2047, "z_neighbour": 0.994},
                                            {"k": 1424, "raw": 2048, "z_shown": 0.968, "best_neighbour": 2047, "z_neighbour": 0.992}]}
    ctx.verify["criteria"]["c2_cuts"]["details"] = {"cuts": [
        {"from": 3, "to": 4, "frame": 44, "kind": "hard", "status": "fail",
         "sides": [{"side": "A_last", "result": "ok"}, {"side": "repeat_pair", "result": "fail"}]},
        {"from": 4, "to": 5, "frame": 49, "kind": "hard", "status": "fail", "sides": [{"side": "no_cut", "result": "fail"}]}]}
    lines = report.independent_findings(ctx.verify)
    assert any(ln.startswith("Motion mismatch (s9_2b): S65 holds one RAW frame on frames 1194–1203") for ln in lines)
    assert any("recreation changes at 20, 40" in ln and "recreation repeats at 7" in ln for ln in lines)
    assert any("s9_2c" in ln and "1423-1424" in ln and "RAW 2047 0.994" in ln for ln in lines)
    assert "Cuts failed as inside a repeat pair (c2): frames 44" in lines and "Cuts failed as spurious cut (c2): frames 49" in lines
    md = report.render_report(ctx)
    assert "- Motion mismatch (s9_2b)" in md and "12 repeat, 40 move" in md
    assert "| 01: S03\\|S04 |" in md or "S03\\|S04" in md or "S03|S04" in md
    assert "inside a repeat pair" in md


def test_uncertain_ranges_and_frame_mix_in_the_report(tmp_path):
    """FX-08: an 'uncertain' segment is listed with its label under UNCERTAIN ranges (not as NOT-IN-RAW), its table row
    shows the label instead of RAW timecodes, the edit breakdown counts it, and a verified frame-blend path is shown
    as 'frame blend (Frame Mix)' in the speed column and among what AE reproduces with Frame Mix."""
    ctx = make_ctx(tmp_path)
    segs = ctx.cutlist.segments
    label = "UNCERTAIN - best RAW 1662-1673, ZNCC 0.50-0.89 (00:00:05:00-00:00:06:00)"
    segs[3] = Segment(4, "uncertain", 150, 180, label=label, uncertain=True, confidence=0.0)
    s5 = segs[4]
    s5.speed, s5.retime, s5.time_mode, s5.unsnapped = 0.25, "frame_blend", "remap", False
    s5.time_remap_keys = [{"comp_frame": 180, "raw_seconds": 700.25 / float(R_FPS)},
                          {"comp_frame": 240, "raw_seconds": 700.25 / float(R_FPS) + 0.5}]
    md = report.render_report(ctx)
    assert "NOT-IN-RAW ranges (every hypothesis below none_thresh): none" in md
    assert f"UNCERTAIN ranges" in md and f"150–179 (00:00:05:00–00:00:06:00) {label}" in md
    assert "0.2500 frame blend (Frame Mix)" in md
    assert "S05: frame-blend retiming (verified path; exported with AE Frame Blending > Frame Mix)" in md
    eb = report.edit_breakdown(ctx.cutlist, ctx.layout, ctx.cfg)
    assert eb["uncertain"] == [(150, 180)] and eb["not_in_raw"] == []


def test_plain_language_summary_with_raw_only_overlays_and_uncertain_frames(tmp_path):
    """FX-12 / wave 4: report.md starts with a plain-language summary for a non-expert user -- the result, one
    simple line per check, what failed and why, what to check by hand in After Effects (uncertain frames to rebuild,
    a RAW-only overlay to mask: the recreation shows it, the competitor does not) and one-line headlines; the RAW-only
    overlay is also listed under 'Anything AE can't reproduce'."""
    ctx = make_ctx(tmp_path)
    segs = ctx.cutlist.segments
    segs[3] = Segment(4, "uncertain", 150, 180, label="UNCERTAIN - best RAW 1662-1673, ZNCC 0.50-0.89", uncertain=True)
    ctx.verify["criteria"]["c3_source_frames"]["status"] = "fail"
    ctx.verify["failures"] = ["c3_source_frames: 30 frames in 1 UNCERTAIN segment(s) (neither matched nor NOT-IN-RAW): ..."]
    line = ("RAW-only overlay at 284,392,393,26 (x,y,w,h in RAW px) over frames 426-465: not shown by the competitor "
            "(segment(s) S20, S21)")
    ctx.verify["raw_only_overlays"] = {"lines": [line], "regions": [
        {"segment": 20, "raw_rect": [283.5, 391.5, 393.0, 25.5], "frames": [426, 465]}], "rejected": []}
    ctx.verify["checks"]["s9_3_visual"]["raw_only_overlay_frames"] = list(range(427, 466))
    md = report.render_report(ctx)
    assert md.index("## 1. Summary") < md.index("## 2. Acceptance criteria")
    summ = md[md.index("## 1. Summary"):md.index("## 2. Acceptance criteria")]
    assert "**Result: FAIL**" in summ
    assert "| Every frame shows the right frame of your RAW video. | NOT OK |" in summ
    assert "the tool is not sure which RAW frame this is" in summ and "It did not guess." in summ
    assert "rebuild them by hand" in summ
    assert "your RAW video shows text or a graphic at x 284, y 392 (size 393 × 26 RAW pixels)" in summ
    assert "add a mask or a blur there in After Effects" in summ
    assert "Headlines:" in summ and "RAW-only overlays: 1" in summ and "Uncertain: 30 frames in 1 segment(s)" in summ
    assert "UNCERTAIN segment" not in summ.split("What failed and why:")[1].split("Check by hand")[0].replace(
        "Other problem", "")                                     # the uncertain failure is said in words once
    cant = next(ln for ln in md.splitlines() if ln.startswith("- Anything AE can't reproduce"))
    assert line in cant and "the recreation (AE / preview) SHOWS it, the competitor does not" in cant
    assert "39 matched frames reach the visual threshold only with them excluded: 427-465" in md


def test_reassigned_rows_are_complete_and_grouped_with_class_gap_delta(tmp_path):
    """FX-12: every re-assigned / mismatched frame is listed (not the first 8), grouped by reason, each with its
    measured / shown frames, class, gap and delta; the low-margin line is refine's own flags only."""
    ctx = make_ctx(tmp_path)
    fm = ctx.fm
    for k in ("status", "raw", "raw_lo", "raw_hi", "low_margin"):
        fm.d["pre_segment_" + k] = np.asarray(fm.d[k]).copy()
    fm.d["pre_segment_low_margin"][200] = True
    rows = [{"k": 10 + i, "m": 100 + i, "ae": 101 + i, "why": "model", "class": "within noise", "gap": 0.0004,
             "delta": 0.002} for i in range(6)]
    rows += [{"k": 50 + i, "m": 300 + i, "ae": 298 + i, "why": "tiny_segment_merged", "class": "systematic run",
              "gap": 0.0011, "delta": 0.001} for i in range(4)]
    rows.append({"k": 90, "m": 500, "ae": 503, "why": "drop", "class": "outside noise", "gap": 0.021, "delta": 0.002})
    ctx.verify["checks"]["s9_2_ae_sim"] = {"status": "fail", "plan": {"reassigned": rows, "n_reassigned": len(rows),
                                                                     "mismatches": [], "n_mismatches": 0}, "mock": {}}
    md = report.render_report(ctx)
    head = next(ln for ln in md.splitlines() if ln.startswith("- Frames that do not show refine's measured best frame"))
    assert ": 11 — by reason:" in head
    lines = md.splitlines()
    i = lines.index(head)
    grp = lines[i + 1:i + 4]
    assert grp[0].startswith("  - model: 6 (6 within noise)") and "k 15: measured 105 → shown 106 (within noise, gap +0.0004, delta 0.0020)" in grp[0]
    assert grp[1].startswith("  - tiny_segment_merged: 4 (4 systematic run)")
    assert grp[2].startswith("  - drop: 1 (1 outside noise)") and "gap +0.0210" in grp[2]
    low = next(ln for ln in lines if ln.startswith("- Low-margin frames"))
    assert ": 1 — 200" in low
    assert "| 90 | 500 | 503 | drop | outside noise | +0.0210 | 0.0020 |" in md


def test_input_facts_list_the_edit_lists_instead_of_guessing(tmp_path):
    """FX-12: the inputs table lists the FACTS -- per-track MP4 edit lists (media_time), iTunSMPB, stream durations --
    instead of 'edit list: yes / no' (which only meant 'no non-benign edit list'): a clip muxed with the default edit
    lists vs remuxed with -use_editlist 0."""
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=30:d=1", "-f", "lavfi",
                    "-i", "sine=f=440:sample_rate=44100:d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(a)],
                   check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a), "-c", "copy", "-use_editlist", "0", str(b)], check=True)
    fa, fb = report.container_facts(a), report.container_facts(b)
    assert "audio track" in fa["elst"] and "media_time 1024/44100" in fa["elst"], fa
    assert "media_time" not in fb["elst"] and "none" in fb["elst"], fb
    assert fa["smpb"] in ("absent",) or fa["smpb"].startswith("present")
    assert fa["durations"].startswith("video 1.0") and " / audio " in fa["durations"]
    assert report.container_facts(tmp_path / "missing.mp4") == {"elst": "n/a", "smpb": "n/a", "durations": "n/a"}
    ctx = make_ctx(tmp_path)
    ctx.comp_input.path = str(a)
    md = report.render_report(ctx)
    assert "| edit lists (elst, per track) |" in md and "| iTunSMPB (encoder gapless info) |" in md
    assert "| edit list |" not in md
