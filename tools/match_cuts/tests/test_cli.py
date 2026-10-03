"""Tests for cli.py and the pipeline orchestration: argument parsing, defaults == the prompt's
Configuration, input auto-detection / swap on tiny lavfi clips, --help, summary + exit codes, pipeline
helpers (timeline fps, comp size, AE search, phase fill), and a stub-world end-to-end run of
``python -m match_cuts`` in which every analysis/export module is replaced by a small consistent stub
(so pipeline.run, verify.verify_all, the cache round trips, s9_7 and report.md all execute)."""
from __future__ import annotations

import dataclasses
import json
import math
import os
import shutil
import subprocess
import sys
import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import match_cuts
from match_cuts import cli, pipeline
from match_cuts.verify import previous_run_canonical
from match_cuts.common import file_hash, null_dlog
from match_cuts.config import Config
from match_cuts.geometry import Sim
from match_cuts.model import AudioHints, Box, FrameMap, Layout, Proxy, Segment, Status, StreamInfo

PY = "/home/user/MovieRecaps/.venv/bin/python"
F30 = Fraction(30)


def make_clip(path: Path, w: int, h: int, frames: int, fps: int = 30) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}",
                    "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    return {"portrait": make_clip(d / "short_portrait.mp4", 36, 64, 40),
            "landscape": make_clip(d / "long_landscape.mp4", 64, 36, 90),
            "landscape_short": make_clip(d / "short_landscape.mp4", 64, 36, 40)}


# ---------------------------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------------------------

def test_defaults_match_prompt_configuration():
    args = cli.build_parser().parse_args([])
    cfg = cli.config_from_args(args)
    assert cfg.competitor == "./input/competitor.mp4"
    assert cfg.raw == "./input/raw.mp4"
    assert cfg.out_dir == "./output"
    assert cfg.work_dir == "./work"
    assert cfg.layout_mode == "match"
    assert cfg.comp_size == "competitor"
    assert cfg.fps_mode == "competitor"
    assert cfg.ae_time_mode == "auto" and cfg.workers == 0 and not cfg.force_conform and not cfg.verbose
    base = Config()   # the CLI never changes an analysis threshold
    assert cfg.analysis_params() == base.analysis_params()


def test_flags_map_to_config():
    args = cli.build_parser().parse_args([
        "--competitor", "c.mp4", "--raw", "r.mov", "--out", "o", "--layout", "fill", "--comp-size", "1080X1920",
        "--fps", "source", "--work", "w", "--workers", "3", "--force-conform", "--ae-time-mode", "frames", "-v",
        "--seed", "7", "--skip-preview", "--skip-compare"])
    cfg = cli.config_from_args(args)
    assert (cfg.competitor, cfg.raw, cfg.out_dir, cfg.work_dir) == ("c.mp4", "r.mov", "o", "w")
    assert (cfg.layout_mode, cfg.comp_size, cfg.fps_mode) == ("fill", "1080x1920", "source")
    assert (cfg.workers, cfg.force_conform, cfg.ae_time_mode, cfg.verbose, cfg.seed) == (3, True, "frames", True, 7)
    assert cfg.skip_preview and cfg.skip_compare


@pytest.mark.parametrize("bad", [["--comp-size", "1080"], ["--comp-size", "2x2"], ["--layout", "boxed"],
                                 ["--fps", "25"], ["--workers", "-1"], ["--ae-time-mode", "fast"]])
def test_invalid_arguments_exit_2(bad, capsys):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(bad)
    assert e.value.code == 2


def test_help_lists_every_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--competitor", "--raw", "--out", "--layout", "--comp-size", "--fps", "--work", "--workers",
                 "--force-conform", "--ae-time-mode", "-v"):
        assert flag in out


def test_module_entry_point_help():
    res = subprocess.run([PY, "-m", "match_cuts", "--help"], capture_output=True, text=True,
                         cwd="/home/user/MovieRecaps/tools/match_cuts")
    assert res.returncode == 0 and "match|fill|source" in res.stdout.replace("{", "").replace("}", "").replace(",", "|")


# ---------------------------------------------------------------------------------------------
# input auto-detection
# ---------------------------------------------------------------------------------------------

def test_quick_probe(clips):
    p = cli.quick_probe(clips["portrait"])
    assert (p["width"], p["height"]) == (36, 64)
    assert p["duration"] == pytest.approx(40 / 30, abs=0.05)


def test_explicit_inputs_kept_or_swapped(clips):
    c, r, notes = cli.resolve_inputs(str(clips["portrait"]), str(clips["landscape"]))
    assert (c, r, notes) == (str(clips["portrait"]), str(clips["landscape"]), [])
    c, r, notes = cli.resolve_inputs(str(clips["landscape"]), str(clips["portrait"]))
    assert (c, r) == (str(clips["portrait"]), str(clips["landscape"]))
    assert notes and "swapped" in notes[0] and "portrait" in notes[0]
    # same orientation: the shorter one is the competitor
    c, r, notes = cli.resolve_inputs(str(clips["landscape"]), str(clips["landscape_short"]))
    assert c == str(clips["landscape_short"]) and "shorter" in notes[0]
    c, r, notes = cli.resolve_inputs(str(clips["landscape"]), str(clips["portrait"]), no_swap=True)
    assert c == str(clips["landscape"]) and not notes


def test_input_dir_autodetect_and_ambiguity(clips, tmp_path):
    d = tmp_path / "input"
    d.mkdir()
    shutil.copy(clips["landscape"], d / "source video.mp4")
    shutil.copy(clips["portrait"], d / "their_short.mp4")
    c, r, notes = cli.resolve_inputs(None, None, d)
    assert Path(c).name == "their_short.mp4" and Path(r).name == "source video.mp4"
    assert "portrait" in notes[0]
    # one given, the other found
    c, r, notes = cli.resolve_inputs(str(d / "their_short.mp4"), None, d)
    assert Path(r).name == "source video.mp4"
    # genuinely ambiguous: same orientation and duration -> ask
    amb = tmp_path / "amb"
    amb.mkdir()
    shutil.copy(clips["landscape_short"], amb / "a.mp4")
    shutil.copy(clips["landscape_short"], amb / "b.mp4")
    with pytest.raises(cli.InputError, match="cannot tell"):
        cli.resolve_inputs(None, None, amb)
    with pytest.raises(cli.InputError, match="exactly two"):
        cli.resolve_inputs(None, None, tmp_path / "missing")


def test_main_reports_input_errors(tmp_path, capsys):
    assert cli.main(["--competitor", str(tmp_path / "nope.mp4"), "--raw", str(tmp_path / "nope2.mp4")]) == 2
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# summary + exit codes (pipeline.run stubbed)
# ---------------------------------------------------------------------------------------------

def _x(out: Path) -> Path:
    """The extras folder of the newest numbered run folder in --out."""
    runs = sorted((int(p.name), p) for p in Path(out).iterdir() if p.is_dir() and p.name.isdigit())
    return runs[-1][1] / "extras"


def _fake_result(statuses: dict[str, str], det: str = "pass") -> dict:
    crit = {k: {"status": v, "summary": f"{k} summary"} for k, v in statuses.items()}
    checks = {"s9_7_determinism": {"status": det, "summary": "det"}}
    return {"criteria": crit, "checks": checks, "warnings": ["w1"],
            "paths": {"xml": "out/001/1_edit.xml", "report": "out/001/extras/report.md"},
            "checklist": {"broll": ["00:00:01:02-00:00:03:48  B-ROLL REPLACED S03: the RAW of the audio there"],
                          "spots": [], "captions": ["00:00:04,067-00:00:04,300  caption 'a' / spoken 'b'"]},
            "exit_code": pipeline.exit_code_for(crit, checks)}


ALL_PASS = {"c1_coverage": "pass", "c2_cuts": "pass", "c3_source_frames": "pass_with_exceptions",
            "c4_speed_framing": "pass", "c5_audio": "pass_with_exceptions", "c6_after_effects": "pass"}


def test_exit_codes():
    """DESIGN §7 D5: 0 pass, 1 anything failed, 3 nothing failed but a criterion is not_available."""
    assert pipeline.exit_code_for({k: {"status": v} for k, v in ALL_PASS.items()}) == 0
    assert pipeline.exit_code_for({**{k: {"status": v} for k, v in ALL_PASS.items()}, "c2_cuts": {"status": "fail"}}) == 1
    assert pipeline.exit_code_for({k: {"status": v} for k, v in ALL_PASS.items()},
                                  {"s9_7_determinism": {"status": "fail"}}) == 1
    assert pipeline.exit_code_for({"c1_coverage": {"status": "pass"}}) == 1          # incomplete verification
    na6 = {**{k: {"status": v} for k, v in ALL_PASS.items()},
           "c6_after_effects": {"status": "not_available", "summary": "mock not available: Node.js missing"}}
    assert pipeline.exit_code_for(na6) == 3                                          # was 0 (verification-honesty F10)
    assert pipeline.exit_code_for(na6, {"s9_8_deliverables": {"status": "fail"}}) == 1   # a failure wins over N/A
    assert pipeline.exit_code_for({k: {"status": v} for k, v in ALL_PASS.items()},
                                  {"s9_8_deliverables": {"status": "fail"}}) == 1     # REQ-6: a missing deliverable
    assert pipeline.exit_code_for({k: {"status": v} for k, v in ALL_PASS.items()},
                                  {"s9_6_ae_render": {"status": "not_available"}}) == 0   # only criteria count for 3
    assert pipeline.exit_code_for({**{k: {"status": v} for k, v in ALL_PASS.items()}, "c4_speed_framing": {}}) == 1
    # headline
    assert pipeline.headline_for({k: {"status": v} for k, v in ALL_PASS.items()}) == "PASS"
    assert pipeline.headline_for(na6) == "PASS (criterion 6 not verified: mock not available: Node.js missing)"
    na56 = {**na6, "c5_audio": {"status": "not_available", "summary": "no audio"}}
    assert pipeline.headline_for(na56).startswith("PASS (criteria 5, 6 not verified: no audio; mock")
    assert pipeline.headline_for({**na6, "c2_cuts": {"status": "fail"}}) == "FAIL"


def test_main_prints_one_line_per_criterion(monkeypatch, clips, tmp_path, capsys):
    seen = {}

    def fake_run(cfg):
        seen["cfg"] = cfg
        return _fake_result(ALL_PASS)
    monkeypatch.setattr(pipeline, "run", fake_run)
    code = cli.main(["--competitor", str(clips["landscape"]), "--raw", str(clips["portrait"]), "--out", str(tmp_path)])
    out = capsys.readouterr()
    assert code == 0
    assert seen["cfg"].competitor == str(clips["portrait"])            # swapped
    lines = out.out.splitlines()
    for label in ("c1 coverage", "c2 frame-exact cuts", "c3 frame-exact source frames", "c4 speed / framing",
                  "c5 audio", "c6 After Effects"):
        assert sum(1 for ln in lines if ln.strip().startswith(label)) == 1
    assert "match_cuts result: PASS" in out.out and "PASS*" in out.out
    assert "out/001/1_edit.xml" in out.out and "w1" in out.out and "swapped" in out.out
    assert "B-ROLL REPLACED spots: 1" in out.out and "Uncertain / NOT-IN-RAW / retimed spots: none" in out.out
    assert "Captions worth a look: 1" in out.out and "caption 'a' / spoken 'b'" in out.out
    assert out.out.rstrip().endswith(f"Run folder: {tmp_path / '001'}")                # printed at the end
    assert seen["cfg"].deliver_dir == str(tmp_path / "001") and seen["cfg"].out_dir == str(tmp_path / "001" / "extras")
    o = ["--out", str(tmp_path)]
    monkeypatch.setattr(pipeline, "run", lambda cfg: _fake_result({**ALL_PASS, "c3_source_frames": "fail"}))
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"])] + o) == 1
    assert "match_cuts result: FAIL" in capsys.readouterr().out
    # criterion 6 never checked (no Node, no AE): not a plain PASS and not exit 0 (verification-honesty F10)
    monkeypatch.setattr(pipeline, "run", lambda cfg: _fake_result({**ALL_PASS, "c6_after_effects": "not_available"}))
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"])] + o) == 3
    out = capsys.readouterr().out
    assert "match_cuts result: PASS (criterion 6 not verified: c6_after_effects summary)" in out
    assert "match_cuts result: PASS\n" not in out
    # a failed deliverables check fails the run and is printed (REQ-6)
    res = _fake_result(ALL_PASS)
    res["checks"]["s9_8_deliverables"] = {"status": "fail", "summary": "1 problem(s): deliverable missing: xml"}
    res["exit_code"] = pipeline.exit_code_for(res["criteria"], res["checks"])
    monkeypatch.setattr(pipeline, "run", lambda cfg: res)
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"])] + o) == 1
    out = capsys.readouterr().out
    assert "match_cuts result: FAIL" in out and "9.8 deliverables" in out and "deliverable missing: xml" in out

    def boom(cfg):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(pipeline, "run", boom)
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"])] + o) == 2
    assert "kaputt" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# pipeline helpers
# ---------------------------------------------------------------------------------------------

def test_timeline_fps_and_errors():
    cfg = Config()
    assert pipeline.resolve_main_fps(cfg, F30, Fraction(24000, 1001)) == F30
    cfg.fps_mode = "source"
    assert pipeline.resolve_main_fps(cfg, F30, Fraction(24000, 1001)) == Fraction(24000, 1001)
    cfg.fps_mode, cfg.layout_mode = "competitor", "source"
    assert pipeline.resolve_main_fps(cfg, F30, Fraction(25)) == Fraction(25)
    assert pipeline.to_main_frame(30, F30, Fraction(25)) == 25
    assert pipeline.to_main_frame(7, F30, Fraction(25)) == math.floor(7 * 25 / 30 + 0.5)
    segs = [Segment(1, "raw", 0, 7), Segment(2, "raw", 7, 30)]
    err, per = pipeline.fps_mapping_errors(segs, F30, Fraction(25))
    assert per[1]["K_out"] == 6 and per[2]["K_in"] == 6
    assert err == pytest.approx(abs(6 / 25 - 7 / 30), abs=1e-9)
    assert pipeline.fps_mapping_errors(segs, F30, F30)[0] == 0.0


def test_main_size_rules():
    cfg = Config()
    assert pipeline.resolve_main_size(cfg, (1080, 1920), (1920, 1080)) == (1080, 1920)
    cfg.comp_size = "720x1280"
    assert pipeline.resolve_main_size(cfg, (1080, 1920), (1920, 1080)) == (720, 1280)
    cfg.comp_size = "1080x1080"
    with pytest.raises(ValueError, match="aspect"):
        pipeline.resolve_main_size(cfg, (1080, 1920), (1920, 1080))
    cfg.layout_mode = "fill"
    assert pipeline.resolve_main_size(cfg, (1080, 1920), (1920, 1080)) == (1080, 1080)
    cfg.comp_size = "competitor"
    assert pipeline.resolve_main_size(cfg, (720, 1280), (1920, 1080)) == (1080, 1920)
    cfg.layout_mode = "source"
    assert pipeline.resolve_main_size(cfg, (720, 1280), (1920, 1080)) == (1920, 1080)


def test_find_after_effects_layouts(tmp_path):
    win = tmp_path / "win"
    for v in ("2022", "2024"):
        sf = win / f"Adobe After Effects {v}" / "Support Files"
        sf.mkdir(parents=True)
        (sf / "AfterFX.exe").write_text("")
        (sf / "aerender.exe").write_text("")
    r = pipeline.find_after_effects("Windows", {"Windows": str(win)})
    assert r["ae_version"] == "2024" and r["ae_app"].endswith("AfterFX.exe") and r["aerender"].endswith("aerender.exe")
    mac = tmp_path / "mac"
    d = mac / "Adobe After Effects CC 2019"
    (d / "Adobe After Effects CC 2019.app").mkdir(parents=True)
    (d / "aerender").write_text("")
    r = pipeline.find_after_effects("Darwin", {"Darwin": str(mac)})
    assert r["ae_app_name"] == "Adobe After Effects CC 2019" and r["aerender"].endswith("aerender")
    assert pipeline.find_after_effects("Linux")["ae_app"] is None


def test_check_env_here():
    env = pipeline.check_env()
    assert env["ffmpeg"] and env["ffprobe"] and env["ffmpeg_ok"]
    assert env["os"] == "Linux" and env["ae_app"] is None and env["aerender"] is None
    assert env["versions"].get("cv2")


def test_run_after_effects_not_available(tmp_path):
    assert pipeline.run_after_effects({"os": "Linux"}, tmp_path / "x.jsx")["status"] == "not_available"


def test_max_consistent_subset_drops_outlier():
    ks = np.arange(10, 20)
    lo = np.arange(100, 110)
    lo[4] += 3                                     # one isolated argmax error
    keep = pipeline.max_consistent_subset(ks, lo, lo.copy(), 10, 1.0)
    assert keep.sum() == 9 and not keep[4]


def _stub_phase():
    def solve_raw_in(ks, lo, hi, comp_in, v, comp_fps, raw_fps):
        ks, lo, hi = (np.asarray(x, float) for x in (ks, lo, hi))
        u = v * float(raw_fps) / float(comp_fps)
        d = ks - comp_in
        a, b = float(np.max(lo - u * d)), float(np.min(hi + 1 - u * d))
        ok = b > a
        ar, br = float(np.max(lo - 0.5 - u * d)), float(np.min(hi + 0.5 - u * d))
        both = [max(a, ar), min(b, br)]
        both = both if both[1] > both[0] else None
        x = (both[0] + both[1]) / 2 if both else (a + b) / 2
        rf = float(raw_fps)
        return {"raw_in": x / rf, "slack": (b - a) / 2, "interval_floor": [a / rf, b / rf] if ok else None,
                "interval_both": [both[0] / rf, both[1] / rf] if both else None,
                "margin_ms": max(0.0, (b - a) / 2) / rf * 1000, "tie_frames": [], "ok": ok}

    def ae_frame(raw_in, v, k, comp_in, comp_fps, raw_fps, rule="floor"):
        x = (float(raw_in) + float(v) * (int(k) - int(comp_in)) / float(comp_fps)) * float(raw_fps)
        return int(math.floor(x + 1e-9)) if rule == "floor" else int(math.floor(x + 0.5))
    return {"solve_raw_in": solve_raw_in, "ae_frame": ae_frame,
            "feasible_speed_range": lambda ks, lo, hi, comp_in, cf, rf, **kw: (0.99, 1.01),
            "is_feasible": lambda *a, **k: True, "snap_speed": lambda v, r, cfg, preferred=(): (1.0, False)}


def install(monkeypatch, name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(f"match_cuts.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"match_cuts.{name}", mod)
    monkeypatch.setattr(match_cuts, name, mod, raising=False)
    return mod


def test_solve_segment_phase_fills_fields_and_drops_outliers(monkeypatch):
    phase = install(monkeypatch, "phase_solve", **_stub_phase())
    fm = FrameMap(30)
    truth = np.arange(200, 230, dtype=np.int32)
    fm.status = np.full(30, Status.MATCH, np.int8)
    for col in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi"):
        setattr(fm, col, truth)
    s = Segment(1, "raw", 0, 30, speed=1.0)
    warns = pipeline.solve_segment_phase(s, fm, F30, F30, Config(), null_dlog(), phase)
    assert s.raw_in_frame == 200 and s.raw_out_frame == 229 and s.time_mode == "stretch"
    assert s.raw_in_interval == [pytest.approx(200 / 30), pytest.approx(201 / 30)]
    assert s.raw_in_seconds == pytest.approx(200.25 / 30)          # centre of floor ∩ round
    assert s.ae_margin_ms == pytest.approx(0.25 / 30 * 1000)          # exact floor-rule slack of every frame (FX-10)
    assert not warns
    fm.raw[12] = fm.raw_lo[12] = fm.raw_hi[12] = fm.soft_lo[12] = fm.soft_hi[12] = 250
    s2 = Segment(2, "raw", 0, 30, speed=1.0)
    warns = pipeline.solve_segment_phase(s2, fm, F30, F30, Config(), null_dlog(), phase)
    assert s2.raw_in_frame == 200 and "12" in s2.notes and any("inconsistent" in w for w in warns)
    assert not s2.uncertain                                          # a single outlier frame
    # 1.10x: floor-feasible raw_in exists but no raw_in satisfies the round rule too -> AE-rule-sensitive
    s3 = Segment(3, "raw", 0, 30, speed=1.1)
    fm2 = FrameMap(30)
    fm2.status = np.full(30, Status.MATCH, np.int8)
    t2 = np.floor(200.3 + 1.1 * np.arange(30)).astype(np.int32)
    for col in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi"):
        setattr(fm2, col, t2)
    warns = pipeline.solve_segment_phase(s3, fm2, F30, F30, Config(), null_dlog(), phase)
    # no floor-and-round raw_in alone is not a risk (AE samples with the floor rule): no per-segment warning;
    # the final, aggregated check flags it only for a real razor edge (FX-10: exact slack of every frame)
    assert s3.raw_in_interval_both is None
    assert not any("AE-rule-sensitive" in w for w in warns)
    agg = pipeline.flag_ae_rule_sensitive([s3], Config(), F30, F30)
    info = pipeline.phase_slack(s3, F30, F30)
    assert pipeline.ae_phase_class(info, Config()) == "ok" and info["slack_frames"] >= 0.01
    assert agg == [] and "AE-rule-sensitive" not in (s3.notes or "")
    assert s3.raw_in_interval[0] <= s3.raw_in_seconds <= s3.raw_in_interval[1]
    assert [phase.ae_frame(s3.raw_in_seconds, 1.1, k, 0, F30, F30) for k in range(30)] == t2.tolist()
    # remap segments take raw_in from their keys
    s4 = Segment(4, "raw", 10, 20, speed=0.0, time_remap_keys=[{"comp_frame": 10, "raw_seconds": (50 + 0.25) / 30},
                                                                {"comp_frame": 19, "raw_seconds": (50 + 0.25) / 30}])
    pipeline.solve_segment_phase(s4, fm, F30, F30, Config(), null_dlog(), phase)
    assert s4.time_mode == "remap" and s4.raw_in_frame == 50 and s4.raw_out_frame == 50


def test_overlays_and_placeholder_label():
    lay = Layout(1080, 1920, box=Box(60, 460, 960, 1000, 36),
                 zones=[__import__("match_cuts.model", fromlist=["Zone"]).Zone("title", 90, 260, 900, 140)],
                 captions=[{"comp_in": 12, "comp_out": 40, "x": 100, "y": 1200, "w": 800, "h": 90}])
    ov = pipeline.overlays_detected(lay, 1800)
    assert ov[0]["type"] == "title" and ov[0]["comp_in"] == 0 and ov[0]["comp_out"] == 1800
    assert ov[1] == {"type": "captions", "comp_in": 12, "comp_out": 40, "x": 100.0, "y": 1200.0, "w": 800.0, "h": 90.0}
    s = Segment(3, "not_in_raw", 30, 60)
    assert pipeline.placeholder_label(s, F30).startswith("MISSING - not in RAW (00:00:01:00-00:00:02:00")


# ---------------------------------------------------------------------------------------------
# stub-world end-to-end: python -m match_cuts with every stage module stubbed
# ---------------------------------------------------------------------------------------------

W, H = 64, 36
RAW_N, COMP_N = 90, 40
EDIT = [  # (id, type, comp_in, comp_out, raw_start)
    (1, "raw", 0, 12, 20), (2, "raw", 12, 20, 60), (3, "not_in_raw", 20, 26, None), (4, "raw", 26, 40, 5)]


def _world():
    import cv2
    rng = np.random.default_rng(11)

    def tex():
        img = rng.uniform(0, 255, (H, W)).astype(np.float32)
        return np.clip(cv2.GaussianBlur(img, (0, 0), 1.5) * 3 - 255, 0, 255).astype(np.uint8)
    raw = np.stack([tex() for _ in range(RAW_N)])
    comp = np.empty((COMP_N, H, W), np.uint8)
    truth = np.full(COMP_N, -1, np.int32)
    for _id, typ, a, b, j0 in EDIT:
        for k in range(a, b):
            if typ == "raw":
                truth[k] = j0 + (k - a)
                comp[k] = raw[truth[k]]
            else:
                comp[k] = tex()
    return raw, comp, truth


def install_stub_world(monkeypatch, calls: dict):
    raw_frames, comp_frames, truth = _world()
    phase = install(monkeypatch, "phase_solve", **_stub_phase())

    def probe(path, role, work_dir, decode=True):
        n = COMP_N if role == "competitor" else RAW_N
        return StreamInfo(path=str(path), role=role, container="mov,mp4,m4a,3gp,3g2,mj2", vcodec="h264", vprofile="High",
                          pix_fmt="yuv420p", width=W, height=H, display_width=W, display_height=H, fps=F30,
                          r_frame_rate=F30, avg_frame_rate=F30, nb_frames=n, duration=n / 30, has_audio=False,
                          file_size=os.path.getsize(path), file_hash=file_hash(path))
    install(monkeypatch, "probe", probe=probe, ae_issues=lambda info: [])

    def conform(info, role, cfg, dlog):
        name = "competitor_ref.mp4" if role == "competitor" else Path(info.path).name
        dst = Path(cfg.media_dir) / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(info.path, dst)
        return types.SimpleNamespace(path=str(dst), conformed=False, reason="already AE-safe", verification={},
                                     source_path=str(info.path), file_rel=f"media/{name}", file_abs=str(dst.resolve()))
    install(monkeypatch, "conform", conform=conform)

    def build_proxy(info, role, cfg, cache, windows=None):
        fr = comp_frames if role == "competitor" else raw_frames
        return Proxy(role, info.path, fr, (W, H), (1.0, 1.0), F30, np.arange(len(fr)) / 30.0, len(fr))
    install(monkeypatch, "proxies", build_proxy=build_proxy, extend_proxy=lambda p, w, cfg, cache: p,
            load_audio=lambda info, sr, cache: np.zeros(0, np.float32),
            load_audio_full=lambda info: (np.zeros((0, 1), np.float32), 48000))

    class OverlayMasks:
        def __init__(self):
            self.m = {}

        def get(self, k):
            return self.m.get(k)

        def frames(self):
            return sorted(self.m)

        def save(self, path):
            np.savez_compressed(path, frames=np.array(sorted(self.m), np.int32))

        @staticmethod
        def load(path):
            calls.setdefault("ov_load", []).append(str(path))
            return OverlayMasks()

    def analyze_layout(comp, cfg, cache, debug_dir, dlog):
        (Path(debug_dir) / "layout.png").write_bytes(b"png")
        marker = cache.root / "layout_stub.done"            # behaves like layout's own cache
        if marker.exists():
            dlog.record("layout", "cache_hit", key="stub")
        else:
            dlog.record("layout", "box_full_res", box={"x": 0, "y": 0, "w": W, "h": H}, evidence={"std": 9.5})
            marker.write_text("1")
        return Layout(W, H, mode="fullscreen", box=Box(0, 0, W, H)), OverlayMasks()
    install(monkeypatch, "layout", analyze_layout=analyze_layout, OverlayMasks=OverlayMasks,
            allowed_mask=lambda layout, overlays, k, comp, dilate_px=None: np.ones((H, W), bool))
    install(monkeypatch, "audio_align", coarse_align=lambda *a, **k: AudioHints.empty(),
            xcorr_lag=lambda a, b, sr, m: (0.0, 1.0),
            analyze_segments_audio=lambda segs, cy, ry, sr, fps, cfg, dlog, **k: {
                "segments": {}, "added_audio": [], "status": "no_audio", "notes": ["no audio in either file"]},
            av_offset_prior=lambda *a, **k: {"lag_s": 0.0, "accepted": False, "reason": "no audio hints"},
            av_offset_estimate=lambda *a, **k: {"status": "not_measured", "lag_s": 0.0, "lag_ms": 0.0,
                                                "text": "no audio"})

    @dataclasses.dataclass
    class Anchor:
        k: int
        raw: int
        flip: bool
        sim: Sim
        inliers: int
        inlier_ratio: float
        votes: float
        zncc: float
        source: str

    class RawIndex:
        @staticmethod
        def build(raw, cfg, cache):
            calls["index"] = calls.get("index", 0) + 1
            return RawIndex()

    def sparse_search(comp, raw, layout, overlays, index, hints, cfg, dlog, frames=None):
        return [Anchor(k, int(truth[k]), False, Sim(1.0, 0.0, 0.0, 0.0), 60, 0.9, 5.0, 0.99, "global")
                for k in range(0, COMP_N, 3) if truth[k] >= 0]
    install(monkeypatch, "visual_match", RawIndex=RawIndex, Anchor=Anchor, sparse_search=sparse_search)

    def build_frame_map(comp, raw, layout, overlays, anchors, hints, index, cfg, cache, dlog, debug_dir):
        calls["refine"] = calls.get("refine", 0) + 1
        calls.setdefault("refine_boxes", []).append(layout.box.to_dict() if layout.box else None)
        assert all(isinstance(a.sim, Sim) for a in anchors)
        dlog.record("refine", "track", frames=[0, COMP_N], anchors=len(anchors))
        fm = FrameMap(COMP_N)
        matched = truth >= 0
        fm.status = np.where(matched, Status.MATCH, Status.NONE).astype(np.int8)
        for col in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi"):
            setattr(fm, col, truth)
        fm.score = np.where(matched, 0.995, 0.2).astype(np.float32)
        fm.conf = np.where(matched, 0.95, 0.0).astype(np.float32)
        fm.s = np.where(matched, 1.0, np.nan)
        fm.theta = np.where(matched, 0.0, np.nan)
        fm.tx = np.where(matched, 0.0, np.nan)
        fm.ty = np.where(matched, 0.0, np.nan)
        return fm
    install(monkeypatch, "refine", build_frame_map=build_frame_map)

    def build_segments(fm, comp, raw, layout, overlays, cfg, dlog, debug_dir, hints=None):
        (Path(debug_dir) / "mapping.png").write_bytes(b"png")
        (Path(debug_dir) / "scores.png").write_bytes(b"png")
        out = []
        for sid, typ, a, b, j0 in EDIT:
            s = Segment(sid, typ, a, b, speed=1.0, speed_measured=1.0, speed_range=[0.99, 1.01], confidence=0.95)
            if typ == "raw":
                s.raw_in_frame, s.transform = j0, Sim(1.0, 0.0, 0.0, 0.0).to_dict()
            out.append(s)
            dlog.record("segment", "segment", id=sid, comp_in=a, comp_out=b)
        return out
    install(monkeypatch, "segment", build_segments=build_segments)

    def T(k, fr):
        return k * fr.denominator / fr.numerator

    def ae_plan(cutlist, cfg, meta):
        """A plan in export_ae's format (subset): main, segComp, rawFps, layers with ids / kinds / timing."""
        fr = cutlist.comp_fps
        F = {"num": fr.numerator, "den": fr.denominator}
        layers = []
        for s in cutlist.segments:
            t_in, t_out = T(s.comp_in, fr), T(s.comp_out, fr)
            if s.type == "raw":
                st = 100.0 / s.speed
                layers.append({"id": f"seg{s.id}", "kind": "raw", "comp": "main", "source": "raw", "seg": s.id,
                               "name": f"S{s.id:02d}  RAW", "compIn": s.comp_in, "compOut": s.comp_out,
                               "timeMode": "stretch", "stretch": st, "rawIn": s.raw_in_seconds,
                               "startTime": t_in - s.raw_in_seconds / (100.0 / st), "inPoint": t_in, "outPoint": t_out,
                               "enabled": True, "guide": False, "opacity": [], "opacityValue": 100.0})
            else:
                layers.append({"id": f"nir{s.id}", "kind": "placeholder", "comp": "main", "source": "solid", "seg": s.id,
                               "name": s.label, "compIn": s.comp_in, "compOut": s.comp_out, "timeMode": "still",
                               "stretch": None, "rawIn": None, "startTime": 0.0, "inPoint": t_in, "outPoint": t_out,
                               "enabled": True, "guide": False, "opacity": [], "opacityValue": 100.0})
        return {"main": {"name": "Recreated Edit", "w": W, "h": H, "fps": F, "frames": COMP_N}, "segComp": "main",
                "rawFps": {"num": 30, "den": 1}, "layers": layers, "footage": {"raw": {}, "ref": None}}

    def simulate_ae(plan, time_mode_override=None):
        F = plan["main"]["fps"]
        fr = Fraction(F["num"], F["den"])
        out = {}
        for K in range(plan["main"]["frames"]):
            t, remaining, ents = T(K, fr), 1.0, []
            for L in plan["layers"]:
                if not (L["compIn"] <= K < L["compOut"]):
                    continue
                if L["kind"] == "raw":
                    j = math.floor((t - L["startTime"]) * 100.0 / L["stretch"] * 30 + 1e-9)
                    ents.append({"layer": L["id"], "seg": L["seg"], "raw_frame": j, "opacity": 1.0, "weight": remaining})
                remaining *= 0.0
            out[K] = ents
        return out

    def write_jsx(cutlist, plan, out_path, cfg):
        Path(out_path).write_text("#target aftereffects\n(function(){ /* stub */ })();\n")

    def run_jsx_in_mock(jsx_path, meta, scenario="default"):
        """A record in the real ae_mock format (comments 'mc:<id>', footage ids, saved list, call counters)."""
        calls.setdefault("mock", []).append(scenario)
        base = {"record_type": "ae_mock", "scenario": scenario, "status": "ok", "mock_errors": [], "dialogs": []}
        if scenario == "media_missing":
            return {**base, "alerts": ["Cancelled: RAW video not found"], "saved": [], "dialogs": ["Locate the RAW video"],
                    "calls": {"openDialog": 1, "beginUndoGroup": 0, "endUndoGroup": 0}}
        if scenario == "new_project_null":
            return {**base, "alerts": ["Cancelled: no new project"], "saved": [], "calls": {"newProject": 1}}
        raw_name = [k for k in meta if k != "competitor_ref.mp4"][0]
        plan = calls["plan"]
        fr = Fraction(plan["main"]["fps"]["num"], plan["main"]["fps"]["den"])
        layers = []
        for i, L in enumerate(plan["layers"], start=1):
            raw = L["kind"] == "raw"
            layers.append({"index": i, "name": L["name"], "comment": "mc:" + L["id"], "sourceId": 1 if raw else 100 + i,
                           "sourceType": "footage" if raw else "solid", "enabled": True, "guideLayer": False,
                           "startTime": L["startTime"], "stretch": L["stretch"] if raw else 100.0,
                           "inPoint": L["inPoint"], "outPoint": L["outPoint"], "timeRemapEnabled": False,
                           "props": {"ADBE Opacity": {"value": 100, "keys": []}}})
        dur = plan["main"]["frames"] * fr.denominator / fr.numerator
        return {**base, "alerts": ["match_cuts: built Recreated Edit"], "calls": {"beginUndoGroup": 1, "endUndoGroup": 1},
                "footage": [{"id": 1, "name": raw_name, "comment": "mc:raw", "fps_num": 30, "fps_den": 1, "conformFrameRate": 0}],
                "comps": [{"id": 2, "name": "Recreated Edit", "comment": "mc:main", "frameRate": float(fr), "duration": dur,
                           "workAreaStart": 0, "workAreaDuration": dur, "width": W, "height": H, "layers": layers}],
                "saved": [str(Path(jsx_path).resolve().parent / "recreated_edit.aep")]}

    def plan_capture(cutlist, cfg, meta):
        p = ae_plan(cutlist, cfg, meta)
        calls["plan"] = p
        return p
    install(monkeypatch, "export_ae", ae_plan=plan_capture, write_jsx=write_jsx, run_jsx_in_mock=run_jsx_in_mock,
            simulate_ae=simulate_ae)

    def write_file(name):
        def f(cutlist, path, cfg=None):
            Path(path).write_text(f"{name}: {len(cutlist.segments)} segments\n")
        return f
    install(monkeypatch, "export_xml_edl", write_csv=write_file("csv"), write_fcp7_xml=write_file("xml"),
            write_edl=write_file("edl"), validate_exports=lambda cl, x, e: {"ok": True, "total_frames": COMP_N})

    def frame_for(cutlist, k):
        for s in cutlist.segments:
            if s.type == "raw" and s.comp_in <= k < s.comp_out:
                return raw_frames[phase.ae_frame(s.raw_in_seconds, s.speed, k, s.comp_in, F30, F30)]
        return np.full((H, W), 128, np.uint8)

    def render_preview(cutlist, raw_path, out_path, cfg, layout_mode=None):
        from match_cuts.media import FFmpegWriter
        with FFmpegWriter(out_path, W, H, F30, codec_args=["-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p"]) as wr:
            for k in range(COMP_N):
                g = frame_for(cutlist, k)
                wr.write(np.repeat(g[:, :, None], 3, axis=2))
        return {"frames": COMP_N, "raw_frames": {}}

    def render_compare(comp_path, src, cutlist, out_path, cfg):
        calls["compare_src"] = src
        Path(out_path).write_bytes(b"mp4")
    install(monkeypatch, "render_preview", render_preview=render_preview, render_compare=render_compare,
            build_audio=lambda cl, y, sr: np.zeros(0, np.float32),
            make_context=lambda cl, cfg, layout_mode=None, target_size=None, fps=None: {"cutlist": cl},
            render_frame=lambda k, ctx, raw_frames: np.repeat(frame_for(ctx["cutlist"], k)[:, :, None], 3, axis=2))
    return truth


def test_end_to_end_with_stub_modules(monkeypatch, clips, tmp_path, capsys):
    calls: dict = {}
    truth = install_stub_world(monkeypatch, calls)
    out, work = tmp_path / "output", tmp_path / "work"
    argv = ["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out), "--work", str(work)]
    code = cli.main(argv)
    printed = capsys.readouterr().out
    assert code == 0, printed
    verify = json.loads((_x(out) / "verify.json").read_text())
    st = {k: v["status"] for k, v in verify["criteria"].items()}
    # the stub world has 64x36 frames: verify's own measurements may list explained exceptions there
    assert set(st) == {"c1_coverage", "c2_cuts", "c3_source_frames", "c4_speed_framing", "c5_audio",
                       "c6_after_effects"}, verify["failures"]
    assert all(v in ("pass", "pass_with_exceptions") for v in st.values()), (st, verify["failures"])
    assert st["c1_coverage"] == st["c2_cuts"] == st["c6_after_effects"] == "pass", st
    assert verify["checks"]["s9_7_determinism"]["status"] == "pass"
    assert verify["checks"]["s9_6_ae_render"]["status"] == "not_available"
    assert verify["checks"]["s9_3_visual"]["source"] == "preview_recreation.mp4"
    assert verify["checks"]["s9_3_visual"]["distribution"]["min"] > 0.99
    assert calls["mock"] == list(pipeline.MOCK_SCENARIOS) and calls["compare_src"].endswith("preview_recreation.mp4")
    # deliverables
    run = out / "001"                               # each run its own numbered folder; the files used at the top
    assert sorted(p_.name for p_ in run.iterdir()) == ["1_edit.xml", "2_captions.srt", "extras"] or \
        sorted(p_.name for p_ in run.iterdir()) == ["1_edit.xml", "extras"]
    for rel in ("cutlist.json", "cutlist.csv", "recreated_edit.edl", "build_ae_project.jsx",
                "preview_recreation.mp4", "compare.mp4", "report.md", "verify.json", "match_cuts.log",
                "media/competitor_ref.mp4", f"media/{clips['landscape'].name}", "debug/cuts/cut_01.png",
                "debug/cuts/cut_03.png"):
        assert (run / "extras" / rel).exists(), rel
    for rel in ("frame_map.npz", "decisions.jsonl", "layout.json", "ae_plan.json"):
        assert (work / rel).exists(), rel
    assert f"Run folder: {run}" in printed and str(run / "1_edit.xml") in printed
    cl = json.loads((_x(out) / "cutlist.json").read_text())
    assert cl["competitor"]["file"] == "media/competitor_ref.mp4" and cl["competitor"]["fps"] == "30/1"
    assert cl["raw"]["frames"] == RAW_N and cl["raw"]["conformed"] is False and cl["raw"]["source_path"]
    assert cl["layout"]["mode"] == "match" and cl["settings"]["main_fps"] == "30/1"
    assert cl["settings"]["fps_source_max_error_s"] == 0.0
    segs = cl["segments"]
    assert [s["comp_in"] for s in segs] == [0, 12, 20, 26]
    assert segs[0]["raw_in_frame"] == 20 and segs[0]["raw_in_seconds"] == pytest.approx(20.25 / 30, abs=1e-9)
    assert segs[0]["raw_in_interval"] == [pytest.approx(20 / 30), pytest.approx(21 / 30)]
    assert segs[0]["ae_margin_ms"] == pytest.approx(0.25 / 30 * 1000, abs=1e-6)     # exact slack (FX-10)
    assert segs[2]["label"].startswith("MISSING - not in RAW") and segs[2]["raw_in_seconds"] is None
    assert set(cl["provenance"]["timings"]) >= {"S0 env", "S2 probe+conform", "S9 verify", "total"}
    assert cl["provenance"]["input_hashes"]["competitor"] == file_hash(clips["portrait"])
    fm = FrameMap.load(work / "frame_map.npz")
    assert np.array_equal(fm.raw, truth)
    decisions = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    assert any(d["stage"] == "phase_solve" and d["decision"] == "raw_in" for d in decisions)
    report = (_x(out) / "report.md").read_text()
    assert report.index("## 1. Summary") < report.index("## 2. Acceptance criteria")   # plain-language summary first
    assert "Section could not be rendered" not in report
    for line in ("c1 coverage", "c6 After Effects", "9.7 determinism"):
        assert line in printed
    assert calls["refine"] == 1 and calls["index"] == 1

    # second run: FrameMap cache hit (no search / refine), identical cutlist apart from timings
    first = json.loads((_x(out) / "cutlist.json").read_text())
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert calls["refine"] == 1 and calls["index"] == 1
    second = json.loads((_x(out) / "cutlist.json").read_text())
    first["provenance"].pop("timings")
    second["provenance"].pop("timings")
    assert previous_run_canonical(first) == previous_run_canonical(second)      # same apart from the run folder
    assert _x(out) == out / "002" / "extras"           # a new folder: the previous run is never overwritten
    det = json.loads((_x(out) / "verify.json").read_text())["checks"]["s9_7_determinism"]
    assert det["previous_run"] == {"compared": True, "identical": True, "differences": []}
    assert "identical to the previous run" in det["summary"]
    assert second["raw"]["conform_reason"] == "AE-safe: used unchanged"
    # the decision log is truncated per run (not appended): one phase-solve record per raw segment
    again = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    assert sum(1 for d in again if d["stage"] == "phase_solve" and d["decision"] == "raw_in") == 3
    assert any(d["stage"] == "refine" and d["decision"] == "cache_hit" for d in again)
    # REQ-5 / D6: the cached stages' evidence is replayed (cached=true), not reduced to 'cache_hit'
    replayed = [d for d in again if d.get("cached") is True]
    assert any(d["stage"] == "layout" and d["decision"] == "box_full_res" and d["evidence"] == {"std": 9.5}
               for d in replayed)
    assert any(d["stage"] == "refine" and d["decision"] == "track" for d in replayed)
    assert all(d.get("cache_key") for d in replayed)
    assert not any(d.get("cached") for d in decisions)                  # the first run computed everything
    assert (work / "cache" / "decisions").is_dir() and any((work / "cache" / "decisions").glob("frame_map-*.jsonl"))
    # each report links its own evidence: <out>/debug/decisions.jsonl is this run's complete log
    assert (_x(out) / "debug" / "decisions.jsonl").read_text() == (work / "decisions.jsonl").read_text()
    assert json.loads((_x(out) / "verify.json").read_text())["checks"]["s9_8_deliverables"]["status"] == "pass"


def test_end_to_end_non_match_layout_uses_in_memory_render(monkeypatch, clips, tmp_path, capsys):
    calls: dict = {}
    install_stub_world(monkeypatch, calls)
    out = tmp_path / "o"
    code = cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out),
                     "--work", str(tmp_path / "w"), "--layout", "fill", "--skip-preview"])
    capsys.readouterr()
    verify = json.loads((_x(out) / "verify.json").read_text())
    assert verify["checks"]["s9_3_visual"]["source"] == "in-memory match render"
    assert verify["checks"]["s9_3_visual"]["status"] == "pass"
    assert isinstance(calls["compare_src"], dict)          # match-geometry RenderContext, not the fill preview
    assert code == 0


def _fake_exe(path: Path, body: str) -> Path:
    path.write_text("#!" + PY + "\nimport sys, re, time, pathlib\n" + body)
    path.chmod(0o755)
    return path


def test_run_after_effects_polls_for_the_saved_project(tmp_path, monkeypatch):
    import time as _time
    jsx = tmp_path / "out" / "build_ae_project.jsx"
    jsx.parent.mkdir()
    jsx.write_text("//")
    # Windows: AfterFX.exe -r <jsx> saves the project and keeps running (After Effects stays open)
    afx = _fake_exe(tmp_path / "AfterFX.exe", "p = pathlib.Path(sys.argv[2]).parent / 'recreated_edit.aep'\n"
                                              "p.write_bytes(b'aep'); time.sleep(4)\n")
    t0 = _time.monotonic()
    r = pipeline.run_after_effects({"os": "Windows", "ae_app": str(afx)}, jsx, timeout=15, poll_s=0.1)
    assert r["status"] == "ok" and r["aep"] == str(jsx.parent / "recreated_edit.aep")
    assert _time.monotonic() - t0 < 10                      # did not wait for the AE process to exit
    # macOS: osascript DoScriptFile returns when the script is done
    (jsx.parent / "recreated_edit.aep").unlink()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_exe(bindir / "osascript", "m = re.search(r'DoScriptFile \"(.*)\"', sys.argv[2])\n"
                                    "pathlib.Path(m.group(1)).parent.joinpath('recreated_edit.aep').write_bytes(b'x')\n")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    r = pipeline.run_after_effects({"os": "Darwin", "ae_app": "/Applications/Adobe After Effects 2024/x.app",
                                    "ae_app_name": "Adobe After Effects 2024"}, jsx, timeout=15, poll_s=0.1)
    assert r["status"] == "ok" and 'tell application "Adobe After Effects 2024"' in r["cmd"][2]
    # a script that never saves -> failed with a reason (no hang)
    (jsx.parent / "recreated_edit.aep").unlink()
    _fake_exe(bindir / "osascript", "pass\n")
    r = pipeline.run_after_effects({"os": "Darwin", "ae_app": "x.app", "ae_app_name": "AE"}, jsx, timeout=15, poll_s=0.1)
    assert r["status"] == "failed" and "did not appear" in r["error"]


def test_segment_phase_solution_is_adopted_and_validated(monkeypatch):
    phase = install(monkeypatch, "phase_solve", **_stub_phase())
    fm = FrameMap(20)
    truth = np.arange(300, 320, dtype=np.int32)
    fm.status = np.full(20, Status.MATCH, np.int8)
    for col in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi"):
        setattr(fm, col, truth)
    # segment.py already solved (e.g. with blend-frame constraints the FrameMap cannot hold): its intervals are
    # kept; raw_in is re-placed inside them at the max-min-slack cell midpoint of EVERY frame (FX-10)
    s = Segment(1, "raw", 0, 20, speed=1.0, raw_in_seconds=(300 + 0.4) / 30 + 1e-12,
                raw_in_interval=[300 / 30, 301 / 30], raw_in_interval_both=[300 / 30, 300.5 / 30], ae_margin_ms=13.3)
    dl_entries = []
    dlog = types.SimpleNamespace(record=lambda *a, **k: dl_entries.append((a, k)))
    warns = pipeline.solve_segment_phase(s, fm, F30, F30, Config(), dlog, phase)
    assert s.raw_in_seconds == pytest.approx(300.25 / 30, abs=1e-9) and s.raw_in_seconds == round(s.raw_in_seconds, 9)
    assert s.raw_in_interval == [pytest.approx(300 / 30), pytest.approx(301 / 30)]
    assert s.ae_margin_ms == pytest.approx(0.25 / 30 * 1000, abs=1e-6)
    assert dl_entries[-1][1]["ae_phase"] == "ok" and dl_entries[-1][1]["ae_slack_frames"] == pytest.approx(0.25)
    assert s.raw_in_frame == 300 and s.raw_out_frame == 319 and not warns
    assert dl_entries[-1][1]["source"] == "segment" and dl_entries[-1][1]["violations"] == []
    # a raw_in that contradicts the FrameMap is kept but the frames are listed
    s2 = Segment(2, "raw", 0, 20, speed=1.0, raw_in_seconds=(301 + 0.5) / 30, raw_in_interval=[301 / 30, 302 / 30])
    warns = pipeline.solve_segment_phase(s2, fm, F30, F30, Config(), dlog, phase)
    assert "outside their soft range" in s2.notes and any("0-19" in w for w in warns)


def test_blend_frame_constraints_from_segment_are_used(monkeypatch):
    phase = install(monkeypatch, "phase_solve", **_stub_phase())
    fm = FrameMap(20)
    fm.status = np.full(20, Status.MATCH, np.int8)
    fm.status[:6] = Status.BLEND                                   # crossfade overlap: not MATCH
    truth = np.arange(500, 520, dtype=np.int32)
    for col in ("raw", "raw_lo", "raw_hi", "soft_lo", "soft_hi"):
        setattr(fm, col, truth)
    fm.flip[15] = True                                             # a frame of the other flip is not a constraint
    s = Segment(2, "raw", 0, 20, speed=1.0)
    s.__dict__["_phase_extra"] = {k: (500 + k, 500 + k) for k in range(6)}
    ks, lo, hi = pipeline.segment_constraints(s, fm)
    assert ks.tolist() == [k for k in range(20) if k != 15] and lo.tolist() == [500 + k for k in ks.tolist()]
    pipeline.solve_segment_phase(s, fm, F30, F30, Config(), null_dlog(), phase)
    assert s.raw_in_frame == 500 and "_phase_extra" not in s.to_dict()


def test_conform_reason_is_deterministic():
    src = StreamInfo(path="in.webm", role="raw", ae_issues=["codec vp9", "VFR"])
    info = StreamInfo(path="media/raw_ae.mov", role="raw")
    conf = types.SimpleNamespace(conformed=True, reason="transcoded (cached)", file_rel="media/raw_ae.mov", file_abs="/x")
    assert pipeline.conform_reason(info, conf, src) == "conformed: codec vp9, VFR"
    conf = types.SimpleNamespace(conformed=False, reason="AE-safe; hardlink into media/", file_rel="media/raw.mp4", file_abs="/x")
    assert pipeline.conform_reason(info, conf, StreamInfo(path="in.mp4", role="raw")) == "AE-safe: used unchanged"
    conf = types.SimpleNamespace(conformed=False, reason="large", file_rel="", file_abs="/big/raw.mp4")
    assert "absolute path" in pipeline.conform_reason(info, conf, StreamInfo(path="in.mp4", role="raw"))


# ---------------------------------------------------------------------------------------------
# review fixes (DESIGN §7): decision capture/replay, audio-informed phase, long-RAW windows, box
# refinement against RAW, layout periods, input warnings, deliverables
# ---------------------------------------------------------------------------------------------

def test_decision_log_capture_and_replay(tmp_path):
    """D6 / REQ-5: capture() collects the records emitted inside (nested captures too, JSON-normalised);
    replay() writes them again with the extra fields."""
    from match_cuts.common import DecisionLog, load_decisions, save_decisions
    dl = DecisionLog(tmp_path / "d.jsonl")
    dl.record("pre", "outside")
    with dl.capture("layout") as outer:
        dl.record("layout", "box", box=Fraction(3, 2), arr=np.arange(2))
        with dl.capture("inner") as inner:
            dl.record("layout", "zone", n=np.int64(2))
    dl.record("post", "outside")
    assert outer.tag == "layout" and inner.tag == "inner"
    assert [r["decision"] for r in outer] == ["box", "zone"] and [r["decision"] for r in inner] == ["zone"]
    assert outer[0]["box"] == "3/2" and outer[0]["arr"] == [0, 1] and inner[0]["n"] == 2   # as written to the log
    save_decisions(tmp_path / "store.jsonl", outer)
    assert dl.replay(load_decisions(tmp_path / "store.jsonl") + [{"junk": 1}], cached=True, cache_key="K") == 2
    dl.close()
    recs = load_decisions(tmp_path / "d.jsonl")
    assert [(r["stage"], r["decision"], r.get("cached")) for r in recs] == [
        ("pre", "outside", None), ("layout", "box", None), ("layout", "zone", None), ("post", "outside", None),
        ("layout", "box", True), ("layout", "zone", True)]
    assert recs[-1]["cache_key"] == "K" and recs[-1]["n"] == 2
    # the null log captures too (stages called without a log file)
    with null_dlog().capture("x") as cap:
        null_dlog().record("s", "d", v=1)
    assert cap == [{"stage": "s", "decision": "d", "v": 1}]


def _phase_fm(n: int, raw0: int, speed_u: float = 1.0, soft_pad: int = 0) -> FrameMap:
    fm = FrameMap(n)
    fm.status = np.full(n, Status.MATCH, np.int8)
    truth = np.floor(raw0 + speed_u * np.arange(n) + 1e-9).astype(np.int32)
    for col in ("raw", "raw_lo", "raw_hi"):
        setattr(fm, col, truth)
    fm.soft_lo = truth - soft_pad
    fm.soft_hi = truth + soft_pad
    return fm


def _solved(seg: Segment, fm: FrameMap, cf: Fraction, rf: Fraction) -> Segment:
    pipeline.solve_segment_phase(seg, fm, cf, rf, Config(), null_dlog())
    seg.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
    return seg


def test_audio_informed_phase_sign_and_clamping():
    """D3 (REQ-1 / F7, FX-10): raw_in := raw_in + v * lag (lag_ms > 0 = rebuilt audio late = raw_in too
    small), placed in the breakpoint cell of floor∩round with a margin of max(5 % of the cell,
    ae_slack_tol_frames) -- in cells, never integer ms -- never changing a matched frame."""
    from match_cuts import phase_solve
    cf = rf = F30
    fm = _phase_fm(30, 200)
    cfg = Config()

    def run(lag_ms, corr=0.9, exception=None, speed=1.0, fmap=fm):
        s = _solved(Segment(1, "raw", 0, 30, speed=speed), fmap, cf, rf)
        before = (s.raw_in_seconds, [phase_solve.ae_frame(s.raw_in_seconds, speed, k, 0, cf, rf) for k in range(30)])
        res = {"segments": {1: {"lag_ms": lag_ms, "corr": corr, "exception": exception}}, "status": "ok"}
        pipeline.apply_segment_audio([s], res)
        moved, warns = pipeline.audio_informed_phase([s], res, fmap, None, None, 16000, cf, rf, cfg, null_dlog())
        return s, before, moved, warns

    s, (old, frames), moved, _ = run(+3.0)                           # rebuilt 3 ms late -> raw_in 3 ms later
    assert old == pytest.approx(200.25 / 30, abs=1e-9)                # video-only: centre of floor ∩ round
    assert s.raw_in_seconds == pytest.approx(old + 0.003, abs=1e-9) and moved == [1]
    assert s.audio["phase_source"] == "audio" and s.audio["lag_ms_video"] == 3.0
    assert [phase_solve.ae_frame(s.raw_in_seconds, 1.0, k, 0, cf, rf) for k in range(30)] == frames
    assert [phase_solve.ae_frame(s.raw_in_seconds, 1.0, k, 0, cf, rf, rule="round") for k in range(30)] == frames
    assert s.raw_in_frame == 200 and s.raw_out_frame == 229
    assert s.ae_margin_ms == pytest.approx((old + 0.003 - 200 / 30) * 1000, abs=1e-5)
    s, (old, frames), _, _ = run(-3.0)                                # rebuilt early -> earlier
    assert s.raw_in_seconds == pytest.approx(old - 0.003, abs=1e-9)
    # NLE in-point at the frame boundary: the audio asks for the lower bound; clamped 5 % of the 0.5-frame
    # floor∩round cell inside (0.833 ms at 30p; formerly a fixed 1 ms)
    m = pipeline.audio_phase_margin(0.5, cfg.ae_slack_tol_frames) / 30
    assert m == pytest.approx(0.025 / 30)
    s, (old, frames), _, _ = run(-8.333)
    assert s.raw_in_seconds == pytest.approx(200 / 30 + m, abs=3e-9)
    assert s.ae_margin_ms == pytest.approx(m * 1000, abs=2e-6) and "AE-rule-sensitive" not in (s.notes or "")
    s, _, _, _ = run(+14.0)                    # outside by 5.67 ms (<= the 10 ms tolerance): upper bound - margin
    assert s.raw_in_seconds == pytest.approx(200.5 / 30 - m, abs=3e-9)
    assert s.raw_in_interval_both[1] - s.raw_in_seconds >= m - 1e-9
    # far outside (> tolerance): the audio says nothing about the phase -> raw_in never moves (the AE
    # margin is not shrunk towards an edge the audio does not reach); ONE run-level warning lists them
    s, (old, _), moved, warns = run(+50.0)
    assert s.raw_in_seconds == old and not moved and s.audio["phase_source"] == "video"
    assert len(warns) == 1 and "1 segment(s) keep their video phase" in warns[0] and "S01 +41.7 ms" in warns[0]
    assert "audio implies raw_in" not in warns[0]
    # speed 1.1: the RAW shift is v * lag
    fm11 = _phase_fm(30, 200, 1.1)
    s11 = _solved(Segment(1, "raw", 0, 30, speed=1.1), fm11, cf, rf)
    width = s11.raw_in_interval[1] - s11.raw_in_interval[0] if s11.raw_in_interval_both is None else \
        s11.raw_in_interval_both[1] - s11.raw_in_interval_both[0]
    s, (old, frames), _, _ = run(+1.0, speed=1.1, fmap=fm11)
    lo_b, hi_b = (s11.raw_in_interval_both or s11.raw_in_interval)
    # 1.1x at 30p: the breakpoints of the 30 frames are 0.1 frame apart, so the interval is ONE cell
    assert width * 30 <= 0.1 + 1e-6
    m = pipeline.audio_phase_margin(width * 30, cfg.ae_slack_tol_frames) / 30
    assert m == pytest.approx(max(0.05 * width, (0.01 + 1e-6) / 30))
    assert s.raw_in_seconds == pytest.approx(min(max(old + 1.1 * 0.001, lo_b + m), hi_b - m), abs=2e-9)
    assert [phase_solve.ae_frame(s.raw_in_seconds, 1.1, k, 0, cf, rf) for k in range(30)] == frames
    # weak correlation / an audio exception / a replaced track: the video phase stays
    for kw in ({"corr": 0.79}, {"exception": "music_dominated"}, {"exception": "pitch_preserved"}):
        s, (old, _), moved, _ = run(+3.0, **kw)
        assert s.raw_in_seconds == old and not moved and s.audio["phase_source"] == "video"
        assert s.audio["lag_ms_video"] == 3.0


def test_audio_informed_phase_keeps_measured_frames():
    """Soft ranges wider than refine's measurement: the audio may only move raw_in where every frame the
    video phase showed correctly (pre-segmentation measurement) stays on its RAW frame (criterion 3)."""
    from match_cuts import phase_solve
    cf = rf = F30
    fm = _phase_fm(30, 200, soft_pad=1)            # soft [j-1, j+1], measured j (unique)
    s = _solved(Segment(1, "raw", 0, 30, speed=1.0), fm, cf, rf)
    old = s.raw_in_seconds
    frames = [phase_solve.ae_frame(old, 1.0, k, 0, cf, rf) for k in range(30)]
    assert frames == list(range(200, 230))
    res = {"segments": {1: {"lag_ms": 15.0, "corr": 0.95, "exception": None}}, "status": "ok"}   # 6.7 ms past
    pipeline.apply_segment_audio([s], res)
    pipeline.audio_informed_phase([s], res, fm, None, None, 16000, cf, rf, Config(), null_dlog())
    assert s.raw_in_seconds > old                                   # moved towards the audio ...
    assert [phase_solve.ae_frame(s.raw_in_seconds, 1.0, k, 0, cf, rf) for k in range(30)] == frames   # ... not past it
    assert [phase_solve.ae_frame(s.raw_in_seconds, 1.0, k, 0, cf, rf, rule="round") for k in range(30)] == frames


F24 = Fraction(24000, 1001)


def _time_line(n_slot: int, bounds: list[int], pad: int = 1, v: float = 1.0):
    """A time-tied group as segment.py hands it over (FX-04 2): 23.976 RAW on the 30 fps grid (raw_in = n/30), soft
    ranges +-pad around the exact frames, ONE shared phase solve over all members (phase_solve.solve_shared_raw_in);
    every member carries the line's values at its own comp_in and Segment.time_line = the first comp_in."""
    from match_cuts import phase_solve
    n = bounds[-1]
    fm = FrameMap(n)
    fm.status = np.full(n, Status.MATCH, np.int8)
    truth = np.array([math.floor(F24 * (Fraction(n_slot, 30) + Fraction(k, 30))) for k in range(n)], np.int32)
    for col in ("raw", "raw_lo", "raw_hi"):
        setattr(fm, col, truth)
    fm.soft_lo, fm.soft_hi = truth - pad, truth + pad
    parts = [(np.arange(a, b), truth[a:b] - pad, truth[a:b] + pad, a) for a, b in zip(bounds[:-1], bounds[1:])]
    sols = phase_solve.solve_shared_raw_in(parts, v, F30, F24)
    segs = [Segment(i, "raw", a, b, speed=v, raw_in_seconds=float(sol["raw_in"]), raw_in_interval=list(sol["interval_floor"]),
                    raw_in_interval_both=list(sol["interval_both"]) if sol["interval_both"] else None,
                    ae_margin_ms=float(sol["margin_ms"]), time_line=int(bounds[0]))
            for i, ((a, b), sol) in enumerate(zip(zip(bounds[:-1], bounds[1:]), sols), start=1)]
    return fm, segs, truth


def _line_drift_ms(segs: list[Segment]) -> list[float]:
    """Each member's raw_in minus the first member's line at its comp_in (ms)."""
    s0 = segs[0]
    return [(s.raw_in_seconds - (s0.raw_in_seconds + float(s.speed) * float(Fraction(s.comp_in - s0.comp_in, 30)))) * 1000.0
            for s in segs]


@pytest.mark.parametrize("slot,bounds", [(811, [0, 15, 38, 58]), (1362, [0, 14, 40]), (251, [0, 4, 9])])
def test_time_line_group_is_placed_as_one_line(slot, bounds):
    """FX-04 2 x FX-10: the members of one time line were re-placed each over its OWN frames (per-member breakpoint
    cells) and drifted apart by up to 7.7 ms; place_time_lines places the line ONCE over every frame of the group
    (one common shift): members on one line to the 9-decimal rounding, every frame inside its soft range, each
    member's ae_margin_ms its own exact slack, and the group's min slack = the max-min slack of all frames."""
    from match_cuts import phase_solve
    fm, segs, truth = _time_line(slot, bounds)
    for s in segs:
        pipeline.solve_segment_phase(s, fm, F30, F24, Config(), null_dlog())
    before = _line_drift_ms(segs)
    assert max(abs(d) for d in before) > 0.2                          # the per-member placement drifts apart
    recs = []
    dlog = types.SimpleNamespace(record=lambda *a, **k: recs.append((a, k)))
    assert pipeline.place_time_lines(segs, fm, F30, F24, Config(), dlog) == []
    assert max(abs(d) for d in _line_drift_ms(segs)) <= 1e-6         # one line (9-decimal seconds)
    assert all(s.raw_in_seconds == round(s.raw_in_seconds, 9) for s in segs)
    for s in segs:
        for k in range(s.comp_in, s.comp_out):
            assert truth[k] - 1 <= phase_solve.ae_frame(s.raw_in_seconds, 1.0, k, s.comp_in, F30, F24) <= truth[k] + 1
        sl, _k = phase_solve.exact_min_slack(s.raw_in_seconds, 1.0, s.comp_in, s.comp_in, s.comp_out, F30, F24)
        assert s.ae_margin_ms == pytest.approx(float(sl / F24) * 1000.0, abs=1e-6)
    allowed = segs[0].raw_in_interval_both or segs[0].raw_in_interval
    best = phase_solve.place_raw_in(allowed, bounds[0], bounds[-1], 1.0, F30, F24,
                                    round_rule=bool(segs[0].raw_in_interval_both))["best_half"]
    assert min(s.ae_margin_ms for s in segs) == pytest.approx(best / float(F24) * 1000.0, abs=2e-6)
    placed = [k for a, k in recs if a[1] == "time_line_placed"]
    assert len(placed) == 1 and placed[0]["segments"] == [s.id for s in segs]
    # the classification of a member pinned by its LINE uses the line's cell (pinned / ok, never 'razor')
    spans = pipeline.time_line_spans(segs)
    assert spans == {s.id: (bounds[0], bounds[-1]) for s in segs}
    for s in segs:
        assert pipeline.ae_phase_class(pipeline.phase_slack(s, F30, F24, spans[s.id]), Config()) in ("ok", "pinned")


def test_time_line_groups_split_on_gaps_speed_and_resolved_members():
    """Only maximal runs of >= 2 adjacent forward stretch members at one speed are a line; a member re-solved on
    its own (no raw_in interval), another speed or a gap splits the group (logged)."""
    def mk(i, a, b, v=1.0, tl=0, iv=(1.0, 1.01)):
        return Segment(i, "raw", a, b, speed=v, raw_in_seconds=1.0 + a / 30, raw_in_interval=list(iv) if iv else None,
                       time_line=tl)
    segs = [mk(1, 0, 10), mk(2, 10, 20), mk(3, 20, 30, v=1.1), mk(4, 30, 40, v=1.1), mk(5, 40, 50, v=1.1, iv=None),
            mk(6, 50, 60, v=1.1), mk(7, 70, 80, tl=70), mk(8, 81, 90, tl=70), mk(9, 90, 99, tl=None)]
    recs = []
    groups = pipeline.time_line_groups(segs, types.SimpleNamespace(record=lambda *a, **k: recs.append(k)))
    assert [[s.id for s in g] for g in groups] == [[1, 2], [3, 4]]
    assert [(r["time_line"], r["runs"]) for r in recs] == [(0, [[1, 2], [3, 4]]), (70, [])]
    assert pipeline.time_line_groups([mk(1, 0, 10), Segment(2, "not_in_raw", 10, 20, time_line=0)]) == []


def test_audio_informed_phase_moves_a_time_line_by_one_shift():
    """D3 per time line: ONE common shift from the members' combined residual (weights = audio seconds x corr^2);
    members that disagree beyond the lag tolerance keep their video phase; a member without confident audio
    still moves with its line."""
    from match_cuts import phase_solve

    def run(lags: dict, corrs: dict | None = None):
        fm, segs, truth = _time_line(811, [0, 15, 38, 58])
        for s in segs:
            pipeline.solve_segment_phase(s, fm, F30, F24, Config(), null_dlog())
            s.audio = dict(pipeline.DEFAULT_SEG_AUDIO)
        pipeline.place_time_lines(segs, fm, F30, F24, Config(), null_dlog())
        old = [s.raw_in_seconds for s in segs]
        corrs = corrs or {}
        res = {"segments": {s.id: {"lag_ms": lags.get(s.id), "corr": corrs.get(s.id, 0.95 if lags.get(s.id) is not None
                                                                              else None), "exception": None}
                            for s in segs}, "status": "ok",
               "_measured": {s.id: {"dur_s": s.length / 30.0} for s in segs}}
        pipeline.apply_segment_audio(segs, res)
        recs = []
        dlog = types.SimpleNamespace(record=lambda *a, **k: recs.append((a, k)))
        moved, warns = pipeline.audio_informed_phase(segs, res, fm, None, None, 16000, F30, F24, Config(), dlog)
        return segs, old, moved, warns, recs, truth

    segs, old, moved, warns, recs, truth = run({1: 1.0, 2: 1.4, 3: 0.6})
    shifts = [(s.raw_in_seconds - o) * 1000.0 for s, o in zip(segs, old)]
    assert moved == [1, 2, 3] and not warns
    assert max(shifts) - min(shifts) <= 2e-6 and abs(shifts[0]) > 0.1   # one shift for the whole line
    assert max(abs(d) for d in _line_drift_ms(segs)) <= 1e-6
    w = np.array([15, 23, 20]) * 0.95 ** 2
    want = float(np.dot(w, [1.0, 1.4, 0.6]) / w.sum())
    used = {k["lag_ms_used"] for a, k in recs if a[1] == "audio_phase"}
    assert used == {round(want, 3)} and all(s.audio["phase_source"] == "audio" for s in segs)
    for s in segs:
        for k in range(s.comp_in, s.comp_out):
            assert truth[k] - 1 <= phase_solve.ae_frame(s.raw_in_seconds, 1.0, k, s.comp_in, F30, F24) <= truth[k] + 1
    # a member without confident audio moves with its line
    segs, old, moved, _, _, _ = run({1: 1.0, 3: 1.2}, {2: 0.2})
    assert moved == [1, 2, 3] and max(abs(d) for d in _line_drift_ms(segs)) <= 1e-6
    # members that disagree by more than the lag tolerance: one line cannot follow both -> video phase kept
    segs, old, moved, _, recs, _ = run({1: 1.0, 2: 14.0})
    assert not moved and [s.raw_in_seconds for s in segs] == old
    assert any("disagree" in k.get("reason", "") for a, k in recs if a[1] == "audio_phase_skipped")


def _assembly_ctx(tmp_path: Path, fps: Fraction, n_comp: int, n_raw: int, comp_y, raw_y, sr: int) -> "pipeline.Context":
    cfg = Config()
    cfg.out_dir, cfg.work_dir = str(tmp_path / "o"), str(tmp_path / "w")
    ctx = pipeline.Context(cfg=cfg)
    for role, n in (("competitor", n_comp), ("raw", n_raw)):
        info = StreamInfo(path=str(tmp_path / f"{role}.mp4"), role=role, fps=fps, nb_frames=n, width=W, height=H,
                          display_width=W, display_height=H, has_audio=True, file_hash=role, duration=n / float(fps))
        setattr(ctx, "comp_info" if role == "competitor" else "raw_info", info)
    ctx.comp_audio, ctx.raw_audio, ctx.audio_sr = comp_y, raw_y, sr
    ctx.main_fps, ctx.main_size = fps, (W, H)
    return ctx


def test_audio_phase_same_rate_24p_nle_inpoint_and_static_shot(monkeypatch, tmp_path):
    """REQ-1 / F7 through the real assembly (phase_solve + audio_align): 23.976p competitor and RAW, NLE
    in-points on RAW frame boundaries. S1 (unique frames): the video-only centre leaves the audio
    10.43 ms early (> the 10 ms c5 tolerance); after D3 the residual is the 1 ms edge margin. S2 (static,
    every frame ambiguous-identical over 61 RAW frames): the video centre is 156 ms off, beyond the
    +-100 ms per-segment search; the wide search puts raw_in on the in-point."""
    from match_cuts import phase_solve
    fps, sr = Fraction(24000, 1001), 16000
    rng = np.random.default_rng(5)
    raw_y = rng.standard_normal(20 * sr).astype(np.float32) * 0.1
    n_raw = int(20 * fps)

    def samples(frames: int) -> int:                                 # exact for multiples of 3 frames
        v = Fraction(frames) * sr / fps
        assert v.denominator == 1
        return int(v)
    j1, j2, n1 = 33, 303, 48
    comp_y = np.concatenate([raw_y[samples(j1):samples(j1) + samples(n1)],
                             raw_y[samples(j2):samples(j2) + samples(n1)]])
    comp_y = comp_y + rng.standard_normal(comp_y.size).astype(np.float32) * 0.02   # a little independent noise
    fm = FrameMap(2 * n1)
    fm.status = np.full(2 * n1, Status.MATCH, np.int8)
    raw = np.concatenate([j1 + np.arange(n1), np.full(n1, 330)]).astype(np.int32)
    lo = np.concatenate([j1 + np.arange(n1), np.full(n1, 300)]).astype(np.int32)
    hi = np.concatenate([j1 + np.arange(n1), np.full(n1, 360)]).astype(np.int32)
    fm.raw, fm.raw_lo, fm.raw_hi, fm.soft_lo, fm.soft_hi = raw, lo, hi, lo, hi
    install(monkeypatch, "segment", build_segments=lambda fm_, *a, **k: [
        Segment(1, "raw", 0, n1, speed=1.0, raw_in_frame=j1, transform=Sim(1, 0, 0, 0).to_dict()),
        Segment(2, "raw", n1, 2 * n1, speed=1.0, raw_in_frame=330, transform=Sim(1, 0, 0, 0).to_dict(),
                ambiguous_frames=list(range(n1, 2 * n1)))])
    ctx = _assembly_ctx(tmp_path, fps, 2 * n1, n_raw, comp_y, raw_y, sr)
    dlog = types.SimpleNamespace(record=lambda *a, **k: None)
    fm_out, segs, audio_result, cutlist = pipeline.segment_and_assemble(ctx, fm, dlog, tmp_path / "dbg")
    s1, s2 = sorted(segs, key=lambda s: s.id)
    # S1: the video-only phase was a quarter frame late in RAW = audio 10.43 ms early
    assert s1.audio["lag_ms_video"] == pytest.approx(-0.25 * 1001 / 24, abs=0.3)
    assert abs(s1.audio["lag_ms_video"]) > 10.0
    assert s1.audio["phase_source"] == "audio" and abs(s1.audio["lag_ms"]) < 3.0 and s1.audio["corr"] >= 0.8
    both_w = s1.raw_in_interval_both[1] - s1.raw_in_interval_both[0]
    m = pipeline.audio_phase_margin(both_w * float(fps), Config().ae_slack_tol_frames) / float(fps)
    assert m == pytest.approx(0.05 * both_w)          # 5 % of the 0.5-frame floor∩round cell = 1.04 ms (FX-10)
    assert abs(s1.raw_in_seconds - float(Fraction(j1) / fps)) <= m + 1e-6
    assert [phase_solve.ae_frame(s1.raw_in_seconds, 1.0, k, 0, fps, fps) for k in range(n1)] == list(range(j1, j1 + n1))
    # S2: static shot; the audio places the in-point -- in its breakpoint cell, the cell margin away from the
    # frame boundary the in-point sits on (FX-10: not ON it, where every frame of the layer would be at a
    # boundary; same 0.5-frame floor∩round cells as S1)
    assert s2.audio["phase_source"] == "audio" and abs(s2.audio["lag_ms"]) < 3.0 and s2.audio["exception"] is None
    assert abs(s2.raw_in_seconds - float(Fraction(j2) / fps)) <= m + 0.0005
    assert pipeline.phase_slack(s2, fps, fps)["slack_frames"] >= Config().ae_slack_tol_frames
    assert "wide audio search" in s2.notes
    assert s2.raw_in_interval_both[0] < s2.raw_in_seconds < s2.raw_in_interval_both[1]
    for k in range(n1, 2 * n1):
        assert 300 <= phase_solve.ae_frame(s2.raw_in_seconds, 1.0, k, n1, fps, fps) <= 360
    assert cutlist.segments[0].audio["phase_source"] == "audio"


def test_frame_map_windows_cover_every_referenced_raw_frame():
    fm = FrameMap(6)
    fm.raw = np.array([-1, 100, 101, 102, -1, 900], np.int32)
    fm.raw_lo = np.array([-1, 99, 101, 102, -1, 900], np.int32)
    fm.raw_hi = np.array([-1, 100, 101, 104, -1, 905], np.int32)
    cfg = Config()
    wins = pipeline.frame_map_windows(fm, F30, F30, 950, cfg)
    pad = int(math.ceil(max(60.0, 2 * (cfg.transition_search + cfg.track_search_radius + cfg.refine_radius + 2))))
    assert wins == [(99 - pad, 104 + pad + 1), (900 - pad, 950)]          # clipped to the RAW length
    assert pipeline.frame_map_windows(fm, F30, F30, 2000, cfg)[1] == (900 - pad, 905 + pad + 1)
    assert pipeline.frame_map_windows(FrameMap(3), F30, F30, 1000, cfg) == []


def _install_sparse_raw(monkeypatch, calls: dict) -> None:
    """Replace the stub proxies with a long-RAW style sparse RAW proxy (every 8th frame exposed) whose
    extend_proxy exposes the requested windows (like proxies.py: exactly the frames requested so far)."""
    raw_frames, comp_frames, _ = _world()

    def mk(role, info, frames, index_map):
        return Proxy(role, info.path, frames, (W, H), (1.0, 1.0), F30, np.arange(len(frames)) / 30.0, len(frames),
                     index_map=index_map)

    def build_proxy(info, role, cfg, cache, windows=None):
        if role == "competitor":
            return mk(role, info, comp_frames, None)
        im = np.full(RAW_N, -1, np.int32)
        im[::8] = np.arange(RAW_N)[::8]
        for a, b in windows or []:
            im[a:b] = np.arange(a, b)
        return mk(role, info, raw_frames, im)

    def extend_proxy(p, windows, cfg, cache):
        calls.setdefault("extend", []).append([list(w) for w in windows])
        im = np.asarray(p.index_map).copy()
        for a, b in windows:
            im[a:b] = np.arange(a, b)
        return Proxy(p.role, p.path, p.frames, p.full_size, p.ratio, p.fps, p.pts, p.n, index_map=im)
    mod = sys.modules["match_cuts.proxies"]
    monkeypatch.setattr(mod, "build_proxy", build_proxy)
    monkeypatch.setattr(mod, "extend_proxy", extend_proxy)
    seg_mod = sys.modules["match_cuts.segment"]
    orig = seg_mod.build_segments

    def build_segments(fm, comp, raw, *a, **k):
        calls.setdefault("seg_raw_frames", []).append(np.flatnonzero(np.asarray(raw.index_map) >= 0).tolist())
        return orig(fm, comp, raw, *a, **k)
    monkeypatch.setattr(seg_mod, "build_segments", build_segments)


def test_long_raw_proxy_windows_survive_the_frame_map_cache(monkeypatch, clips, tmp_path, capsys):
    """real-world F5: on a FrameMap cache hit the sparse RAW proxy is extended with windows re-derived
    from the cached FrameMap, so segmentation/verification see the same RAW frames as the first run."""
    calls: dict = {}
    install_stub_world(monkeypatch, calls)
    _install_sparse_raw(monkeypatch, calls)
    out, work = tmp_path / "output", tmp_path / "work"
    argv = ["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out), "--work",
            str(work), "--skip-compare"]
    cli.main(argv)
    first = json.loads((_x(out) / "cutlist.json").read_text())
    n_ext_first = len(calls["extend"])
    cli.main(argv)
    capsys.readouterr()
    assert calls["refine"] == 1                                    # second run: FrameMap cache hit
    assert len(calls["extend"]) > n_ext_first                      # ... and still extended (hit branch)
    runs = calls["seg_raw_frames"]
    # each run segments once and verify s9_7 re-assembles once: all four see the same RAW frames
    assert len(runs) == 4 and all(r == runs[0] for r in runs)
    assert set(range(5, 77)) <= set(runs[0]) and len(runs[0]) > len(range(0, RAW_N, 8))
    second = json.loads((_x(out) / "cutlist.json").read_text())
    first["provenance"].pop("timings")
    second["provenance"].pop("timings")
    assert previous_run_canonical(first) == previous_run_canonical(second)      # same apart from the run folder
    decisions = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    assert any(d["decision"] == "frame_map_windows" and d["n"] >= 1 for d in decisions)


def test_box_refined_against_raw_reruns_visual_stages(monkeypatch, clips, tmp_path, capsys):
    """real-world F3 / D2: when layout.refine_box_from_raw changes the box, S5.2 + S5.3 run once more
    with the refined layout (own cache keys); a cached re-run reproduces it without recomputing.
    Also real-world F8: probe.input_warnings reach the warnings and cutlist.warnings."""
    calls: dict = {}
    install_stub_world(monkeypatch, calls)
    lay_mod = sys.modules["match_cuts.layout"]
    grown = Box(2.0, 3.0, W - 4.0, H - 6.0, 4.0)

    def refine_box_from_raw(layout, overlays, comp, raw, fm, cfg, cache, dlog, debug_dir):
        calls["refine_box"] = calls.get("refine_box", 0) + 1
        assert fm.n == COMP_N and raw is not None and comp is not None
        dlog.record("layout", "box_grown_to_raw", old=layout.box.to_dict(), new=grown.to_dict())
        new = Layout.from_dict(layout.to_dict())
        new.box = grown
        ov = Path(cache.root) / "refined_overlays.npz"             # the re-analysis' initial overlay masks
        np.savez_compressed(ov, frames=np.zeros(0, np.int32))
        new.overlay_mask_file = str(ov)
        return new, True
    monkeypatch.setattr(lay_mod, "refine_box_from_raw", refine_box_from_raw, raising=False)
    probe_mod = sys.modules["match_cuts.probe"]
    monkeypatch.setattr(probe_mod, "input_warnings", lambda info: [
        "decoded duration 3.0 s < container duration 10.0 s (truncated/partial file?)"] if info.role == "raw" else [],
        raising=False)
    out, work = tmp_path / "output", tmp_path / "work"
    argv = ["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out), "--work", str(work)]
    assert cli.main(argv) == 0
    printed = capsys.readouterr().out
    assert calls["refine"] == 2 and calls["refine_boxes"] == [Box(0, 0, W, H).to_dict(), grown.to_dict()]
    assert any(p.endswith("refined_overlays.npz") for p in calls["ov_load"])   # pass 2 starts from its masks
    cl = json.loads((_x(out) / "cutlist.json").read_text())
    assert cl["layout"]["box"] == grown.to_dict()
    assert any("truncated/partial file" in w and w.startswith("raw input") for w in cl["warnings"])
    assert "truncated/partial file" in printed
    decisions = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    ev = [d for d in decisions if d["decision"] == "box_refined_from_raw"]
    assert ev and ev[0]["changed"] is True and ev[0]["new_box"] == grown.to_dict()
    # cached re-run: no search / refine, same layout and cutlist
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert calls["refine"] == 2
    cl2 = json.loads((_x(out) / "cutlist.json").read_text())
    cl["provenance"].pop("timings")
    cl2["provenance"].pop("timings")
    assert previous_run_canonical(cl) == previous_run_canonical(cl2)
    again = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    assert sum(1 for d in again if d["stage"] == "refine" and d["decision"] == "cache_hit") == 2
    assert any(d["decision"] == "track" and d.get("cached") for d in again)


def test_box_refinement_missing_or_failing_is_not_fatal(monkeypatch, clips, tmp_path, capsys):
    calls: dict = {}
    install_stub_world(monkeypatch, calls)

    def boom(*a, **k):
        raise ValueError("no agreeing pixels")
    monkeypatch.setattr(sys.modules["match_cuts.layout"], "refine_box_from_raw", boom, raising=False)
    out = tmp_path / "o"
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out),
                     "--work", str(tmp_path / "w"), "--skip-compare"]) == 0
    capsys.readouterr()
    assert calls["refine"] == 1
    cl = json.loads((_x(out) / "cutlist.json").read_text())
    assert any("box refinement against RAW failed" in w for w in cl["warnings"])


def test_layout_period_warnings():
    """F4 / AE-1 / REQ-3 with D1: fullscreen periods are reproduced (a segment inside carries the whole
    canvas as its box) -- warn only when a segment there has no box or straddles the boundary; split/PiP
    periods are always warned (not reproducible)."""
    from match_cuts.model import LayoutPeriod
    full = {"x": 0, "y": 0, "w": W, "h": H, "corner_radius": 0}
    lay = Layout(W, H, box=Box(4, 4, W - 8, H - 8, 2), periods=[
        LayoutPeriod(0, 10, "fullscreen", Box(0, 0, W, H)), LayoutPeriod(10, 40, "boxed", Box(4, 4, W - 8, H - 8, 2))])
    ok = [Segment(1, "raw", 0, 10, box=full, region=1), Segment(2, "raw", 10, 40)]
    assert pipeline.layout_period_warnings(ok, lay, "match") == []
    bad = [Segment(1, "raw", 0, 10), Segment(2, "raw", 10, 40)]
    w = pipeline.layout_period_warnings(bad, lay, "match")
    assert len(w) == 1 and "frames 0-9 are fullscreen" in w[0] and "S01" in w[0] and "S02" not in w[0]
    straddle = [Segment(1, "raw", 0, 13, box=full, region=1), Segment(2, "raw", 13, 40)]
    w = pipeline.layout_period_warnings(straddle, lay, "match")
    assert len(w) == 1 and "straddle" in w[0] and "S01 (frames 10-12)" in w[0]
    # a merged 1-2 frame sliver past the boundary (detected boundary off by a frame or two) is no warning
    sliver = [Segment(1, "raw", 0, 12, box=full, region=1), Segment(2, "raw", 12, 40)]
    assert pipeline.layout_period_warnings(sliver, lay, "match") == []
    assert pipeline.layout_period_warnings(bad, lay, "source") == []
    # a fullscreen segment across two ADJACENT fullscreen periods is inside fullscreen throughout: no
    # straddle; one reaching past both is reported once, with the frames outside every fullscreen period
    lay2 = Layout(W, H, box=Box(4, 4, W - 8, H - 8, 2), periods=[
        LayoutPeriod(0, 10, "fullscreen", Box(0, 0, W, H)), LayoutPeriod(10, 20, "fullscreen", Box(0, 0, W, H)),
        LayoutPeriod(20, 40, "boxed", Box(4, 4, W - 8, H - 8, 2))])
    assert pipeline.layout_period_warnings([Segment(1, "raw", 0, 20, box=full, region=1), Segment(2, "raw", 20, 40)],
                                           lay2, "match") == []
    w = pipeline.layout_period_warnings([Segment(1, "raw", 0, 25, box=full, region=1), Segment(2, "raw", 25, 40)],
                                        lay2, "match")
    assert w == ["S01 (frames 20-24) straddle the fullscreen period 0-9: part of the segment is shown with the "
                 "wrong layout"]
    # stage-level: split/PiP warned (analysis), fullscreen only logged
    ctx = pipeline.Context(cfg=Config())
    ctx.layout = Layout(W, H, box=Box(4, 4, W - 8, H - 8, 2), periods=[
        LayoutPeriod(0, 10, "fullscreen", Box(0, 0, W, H)), LayoutPeriod(10, 20, "split", None),
        LayoutPeriod(20, 40, "boxed", Box(4, 4, W - 8, H - 8, 2))])
    recs = []
    ctx.dlog = types.SimpleNamespace(record=lambda *a, **k: recs.append((a, k)))
    pipeline.layout_warnings(ctx)
    assert ctx.analysis_warnings == ["frames 10-19: split-screen layout: only the dominant region is recreated "
                                     "(After Effects cannot reproduce it from this cutlist)"]
    assert not any("fullscreen" in w for w in ctx.warnings)
    assert any(a[1] == "fullscreen_periods" and k["periods"] == [[0, 10]] for a, k in recs)


def test_layout_period_warnings_transition_and_sliver_at_fullscreen_boundary():
    """Review R2-5 / D1-c1-transition: a dissolve between a boxed shot and a fullscreen shot puts the
    detected period boundary inside the declared crossfade overlap; those frames show both framings and
    are no warning (neither the boxless outgoing A 'cropped' nor the fullscreen B 'straddling'). Frames
    of a boxless segment in the fullscreen period OUTSIDE the declared overlap still warn, and so does an
    overlap that is not a declared transition or whose neighbour has the same framing."""
    from match_cuts.model import LayoutPeriod
    full = {"x": 0, "y": 0, "w": W, "h": H, "corner_radius": 0}
    boxed = Box(4, 4, W - 8, H - 8, 2)
    xf = {"type": "crossfade", "duration_frames": 6}

    def lay(fs_in: int, fs_out: int = 80, n: int = 80) -> Layout:
        ps = [LayoutPeriod(0, fs_in, "boxed", boxed), LayoutPeriod(fs_in, fs_out, "fullscreen", Box(0, 0, W, H))]
        if fs_out < n:
            ps.append(LayoutPeriod(fs_out, n, "boxed", boxed))
        return Layout(W, H, box=boxed, periods=ps)

    # boxed -> fullscreen, crossfade O=34 D=6 (declared on B only), period starts mid-dissolve (36)
    A = Segment(1, "raw", 0, 40, raw_in_seconds=1.0)
    B = Segment(2, "raw", 34, 80, raw_in_seconds=20.0, box=dict(full), region=1, transition_in=dict(xf))
    recs = []
    dlog = types.SimpleNamespace(record=lambda *a, **k: recs.append((a, k)))
    assert pipeline.layout_period_warnings([A, B], lay(36), "match", dlog) == []
    ev = {k["segment"]: k for a, k in recs if a[1] == "period_boundary_explained"}
    assert ev[1]["transition_frames"] == [[36, 39]] and ev[1]["unexplained"] == []
    assert ev[2]["transition_frames"] == [[34, 35]]
    # the exp_fs_xfade shape: A [0,36) transition_out, B [30,60) transition_in, period from 31
    A2 = Segment(1, "raw", 0, 36, transition_out=dict(xf))
    B2 = Segment(2, "raw", 30, 60, box=dict(full), region=1, transition_in=dict(xf))
    assert pipeline.layout_period_warnings([A2, B2], lay(31, 60, 60), "match") == []
    # fullscreen -> boxed dissolve: B boxless incoming, A fullscreen outgoing; period ends mid-dissolve
    A3 = Segment(1, "raw", 0, 36, box=dict(full), region=1, transition_out=dict(xf))
    B3 = Segment(2, "raw", 30, 60)
    L3 = Layout(W, H, box=boxed, periods=[LayoutPeriod(0, 33, "fullscreen", Box(0, 0, W, H)),
                                          LayoutPeriod(33, 60, "boxed", boxed)])
    assert pipeline.layout_period_warnings([A3, B3], L3, "match") == []
    # the boxless A overlaps B for 10 frames but only a 6-frame crossfade is declared: nothing explained
    A4 = Segment(1, "raw", 0, 44)
    B4 = Segment(2, "raw", 34, 80, box=dict(full), region=1, transition_in=dict(xf))
    w = pipeline.layout_period_warnings([A4, B4], lay(36), "match")
    assert len(w) == 1 and "S01 (frames 36-43)" in w[0] and "cropped" in w[0]     # (B: a 2-frame sliver)
    # period detected late in the dissolve: the fullscreen B's 4 frames before it are the declared overlap
    assert pipeline.layout_period_warnings([A, B], lay(38), "match") == []
    w = pipeline.layout_period_warnings([A, dataclasses.replace(B, transition_in=None)], lay(38), "match")
    assert len(w) == 1 and "S02 (frames 34-37) straddle" in w[0]
    # a boxless segment after the dissolve, inside the fullscreen period: warned even though A/B are explained
    B6 = Segment(2, "raw", 34, 60, box=dict(full), region=1, transition_in=dict(xf))
    w = pipeline.layout_period_warnings([A, B6, Segment(3, "raw", 60, 80)], lay(36), "match")
    assert len(w) == 1 and "S03 (frames 60-79)" in w[0] and "S01" not in w[0]
    # an overlap that no transition declares (or of another length) explains nothing
    B5 = Segment(2, "raw", 34, 80, box=dict(full), region=1, transition_in={"type": "crossfade", "duration_frames": 5})
    w = pipeline.layout_period_warnings([A, B5], lay(36), "match")
    assert len(w) == 1 and "S01 (frames 36-39)" in w[0]
    # a boxless segment wholly inside the fullscreen period (no neighbour of the other framing): warned
    w = pipeline.layout_period_warnings([Segment(1, "raw", 0, 36), Segment(2, "raw", 36, 80)], lay(36), "match")
    assert len(w) == 1 and "S02 (frames 36-79)" in w[0]
    # a boxless 1-2 frame sliver merged across the boundary into the boxed neighbour: no warning; 3 frames: warned
    assert pipeline.layout_period_warnings([Segment(1, "raw", 0, 38), Segment(2, "raw", 38, 80, box=dict(full),
                                                                                region=1)], lay(36), "match") == []
    w = pipeline.layout_period_warnings([Segment(1, "raw", 0, 39), Segment(2, "raw", 39, 80, box=dict(full),
                                                                           region=1)], lay(36), "match")
    assert len(w) == 1 and "S01 (frames 36-38)" in w[0]


def test_layout_period_rule_matches_verify_c1():
    """The pipeline warning and verify's c1 use ONE rule for boxless RAW frames in a fullscreen period
    (declared transition overlap with a boxed neighbour, merged 1-2 frame sliver): the unexplained frames
    agree on a grid of dissolve / sliver / plain layouts."""
    from match_cuts import verify
    fn = getattr(verify, "boxless_fullscreen_frames", None)
    if fn is None:
        pytest.skip("verify.boxless_fullscreen_frames not available")
    full = {"x": 0, "y": 0, "w": W, "h": H, "corner_radius": 0}
    cases = 0
    for a_out in (36, 38, 39, 40, 44):
        for b_in in (30, 34, 36):
            for d_decl in (None, 4, 6, 10):
                for fs_in in (31, 34, 36, 38, 40):
                    if b_in >= a_out + 1 or b_in < 1:
                        continue
                    xf = None if d_decl is None else {"type": "crossfade", "duration_frames": d_decl}
                    A = Segment(1, "raw", 0, a_out, transition_out=xf)
                    B = Segment(2, "raw", b_in, 80, box=dict(full), region=1, transition_in=xf)
                    mine, _ = pipeline.period_mismatch_frames(A, fs_in, 80, [A, B])
                    theirs = fn(A, [A, B], fs_in, 80)["unexplained"]
                    assert sorted(mine) == sorted(theirs), (a_out, b_in, d_decl, fs_in, mine, theirs)
                    cases += 1
    assert cases > 50


def test_pass1_visual_cache_keys_depend_on_the_layout(monkeypatch, clips, tmp_path, capsys):
    """Review R2-3: the FIRST S5.2 + S5.3 pass keys its anchors and FrameMap on the layout geometry and
    the starting overlay masks (like the D2 second pass), so a WORK_DIR whose layout changed (new layout
    algorithm, other box or overlays) never reuses a FrameMap matched against another box; the stage
    versions of frame_map / sparse_search / probe / layout were bumped for caches written before."""
    from match_cuts import common, layout as layout_mod
    assert common.STAGE_VERSION["frame_map"] >= 3 and common.STAGE_VERSION["sparse_search"] >= 2
    assert common.STAGE_VERSION["probe"] >= 2 and common.STAGE_VERSION["layout"] >= 2
    boxed = Box(4, 4, W - 8, H - 8, 2)
    l1 = Layout(W, H, box=boxed)
    l2 = Layout(W, H, box=Box(6, 4, W - 12, H - 8, 2))
    ov1 = layout_mod.OverlayMasks((H, W))
    ov2 = layout_mod.OverlayMasks((H, W))
    m = np.zeros((H, W), bool)
    m[2:5, 3:9] = True
    ov2.set(7, m)
    p = pipeline.visual_pass_key_parts
    assert p(l1, ov1) == p(Layout.from_dict(l1.to_dict()), ov1.copy())         # deterministic, content-based
    assert p(l1, ov1) != p(l2, ov1) and p(l1, ov1) != p(l1, ov2)
    # the static-pixel mask (excluded from the box ROI by visual_match) is keyed by CONTENT, not by path
    sm = np.zeros((H, W), bool)
    for name, val in (("a", False), ("b", False), ("c", True)):
        sm[0, 0] = val
        np.save(tmp_path / f"static_{name}.npy", sm)
    la, lb, lc = (Layout.from_dict(dict(l1.to_dict(), static_mask_file=str(tmp_path / f"static_{n}.npy")))
                  for n in "abc")
    assert p(la, ov1) == p(lb, ov1) != p(lc, ov1)
    ctx = types.SimpleNamespace(comp_info=types.SimpleNamespace(file_hash="c" * 40),
                                raw_info=types.SimpleNamespace(file_hash="r" * 40), cfg=Config(), keys={})
    k1 = pipeline._analysis_key(ctx, "frame_map", *p(l1, ov1))
    assert k1 != pipeline._analysis_key(ctx, "frame_map", *p(l2, ov1))
    assert k1 != pipeline._analysis_key(ctx, "frame_map")                     # the pre-R2-3 pass-1 key
    # stage level: the stub world's first pass is keyed on its layout; another layout -> another key
    calls: dict = {}
    install_stub_world(monkeypatch, calls)
    out = tmp_path / "o"
    base = ["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out),
            "--work", str(tmp_path / "w"), "--skip-compare"]
    fm_dir = tmp_path / "w" / "cache" / "frame_map"

    def run() -> tuple[set, bool]:
        calls.clear()
        cli.main(base)
        capsys.readouterr()
        decs = [json.loads(x) for x in (_x(out) / "debug" / "decisions.jsonl").read_text().splitlines() if x.strip()]
        hit = any(d.get("stage") == "refine" and d.get("decision") == "cache_hit" for d in decs)
        return {p_.name for p_ in fm_dir.glob("*.npz")}, hit
    files_a, hit = run()
    assert not hit and calls["refine"] == 1
    assert run() == (files_a, True) and "refine" not in calls                  # same layout: a cache hit
    lay_mod = sys.modules["match_cuts.layout"]
    orig = lay_mod.analyze_layout

    def moved_box(*a, **k):
        lay, ov = orig(*a, **k)
        d = lay.to_dict()
        d["box"] = dict(d["box"], x=2, w=int(d["box"]["w"]) - 4)
        return Layout.from_dict(d), ov
    monkeypatch.setattr(lay_mod, "analyze_layout", moved_box)
    files_b, hit = run()
    assert not hit and calls["refine"] == 1 and files_b > files_a              # re-matched against the new box
    assert calls["refine_boxes"][-1]["x"] == 2


def test_missing_or_invalid_deliverable_fails_the_run(monkeypatch, clips, tmp_path, capsys):
    """REQ-6 / D5: an export that raised (EDL) or failed validation is a failed run (exit 1, s9_8 fail),
    not 'PASS' with a warning; explicitly skipped renders are not missing."""
    calls: dict = {}
    install_stub_world(monkeypatch, calls)
    xe = sys.modules["match_cuts.export_xml_edl"]

    def edl_boom(cl, path, cfg=None):
        raise OSError("disk full")
    monkeypatch.setattr(xe, "write_edl", edl_boom)
    out = tmp_path / "o"
    base = ["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--work", str(tmp_path / "w")]
    assert cli.main(base + ["--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "match_cuts result: FAIL" in printed and "9.8 deliverables" in printed
    v = json.loads((_x(out) / "verify.json").read_text())
    chk = v["checks"]["s9_8_deliverables"]
    assert chk["status"] == "fail" and "edl" in json.dumps(chk)
    # validation failure alone also fails; skipped renders are listed as skipped, not missing
    monkeypatch.setattr(xe, "write_edl", lambda cl, path, cfg=None: Path(path).write_text("edl\n"))
    monkeypatch.setattr(xe, "validate_exports", lambda cl, x, e: {"ok": False, "errors": ["EDL: 39 frames != 40"]})
    out2 = tmp_path / "o2"
    assert cli.main(base + ["--out", str(out2), "--skip-preview", "--skip-compare"]) == 1
    capsys.readouterr()
    chk = json.loads((_x(out2) / "verify.json").read_text())["checks"]["s9_8_deliverables"]
    assert chk["status"] == "fail" and "39 frames != 40" in json.dumps(chk)
    monkeypatch.setattr(xe, "validate_exports", lambda cl, x, e: {"ok": True, "errors": []})
    out3 = tmp_path / "o3"
    assert cli.main(base + ["--out", str(out3), "--skip-preview", "--skip-compare"]) == 0
    capsys.readouterr()
    chk = json.loads((_x(out3) / "verify.json").read_text())["checks"]["s9_8_deliverables"]
    assert chk["status"] == "pass"


def test_collect_deliverables_records_files_skips_and_errors(tmp_path):
    cfg = Config()
    cfg.out_dir, cfg.work_dir = str(tmp_path / "o"), str(tmp_path / "w")
    cfg.skip_compare = True
    ctx = pipeline.Context(cfg=cfg)
    (tmp_path / "o" / "debug").mkdir(parents=True)
    for rel in ("build_ae_project.jsx", "cutlist.json", "cutlist.csv", "1_edit.xml", "preview_recreation.mp4",
                "debug/mapping.png", "debug/scores.png", "debug/layout.png"):
        (tmp_path / "o" / rel).write_text("x")
    ctx.paths["jsx"] = str(tmp_path / "o" / "build_ae_project.jsx")
    ctx.cutlist = object()
    ctx.ae_run = {"status": "not_available", "reason": "After Effects not installed on this machine (Linux)"}
    ctx.exports = {"ok": False, "errors": ["EDL: does not exist"]}
    media = tmp_path / "o" / "media"
    media.mkdir()
    (media / "raw.mp4").write_text("x")
    ctx.raw_conform = types.SimpleNamespace(path=str(media / "raw.mp4"))
    ctx.comp_conform = types.SimpleNamespace(path=str(media / "competitor_ref.mp4"))     # missing
    d = pipeline.collect_deliverables(ctx, {"csv": True, "xml": True, "edl": False, "preview": True})
    assert set(d["files"]) >= {"jsx", "aep", "cutlist", "csv", "xml", "edl", "preview", "compare", "debug_mapping",
                               "debug_scores", "debug_layout", "media_raw", "media_competitor"}
    assert d["files"]["xml"].endswith("1_edit.xml") and d["files"]["edl"] is None
    assert d["skipped"] == {"aep": "After Effects not installed on this machine (Linux)", "compare": "--skip-compare"}
    assert d["missing"] == ["edl", "media_competitor"] and d["ok"] is False
    assert d["errors"] == ["EDL: does not exist"]
    ctx.exports.update(d)
    ctx.errors.append({"stage": "S8 EDL", "error": "OSError: disk full"})
    chk = pipeline.deliverables_check(ctx)
    assert chk["status"] == "fail" and chk["missing"] == ["edl", "media_competitor"]
    assert any("stage error: S8 EDL" in f for f in chk["failures"])
    # AE ran but produced no project: missing, not skipped
    ctx.ae_run = {"status": "failed", "error": "recreated_edit.aep did not appear"}
    assert "aep" in pipeline.collect_deliverables(ctx, {})["missing"]
    # debug plots are diagnostics: listed when missing, never a failure on their own
    (tmp_path / "o" / "debug" / "scores.png").unlink()
    ctx.ae_run = {"status": "not_available"}
    ctx.errors.clear()
    ctx.exports = {"ok": True, "errors": []}
    (media / "competitor_ref.mp4").write_text("x")
    d = pipeline.collect_deliverables(ctx, {"csv": True, "xml": True, "edl": True, "preview": True})
    (tmp_path / "o" / "recreated_edit.edl").write_text("x")
    d = pipeline.collect_deliverables(ctx, {"csv": True, "xml": True, "edl": True, "preview": True})
    assert d["missing"] == [] and d["missing_diagnostics"] == ["debug_scores"]
    ctx.exports.update(d)
    chk = pipeline.deliverables_check(ctx)
    assert chk["status"] == "pass" and chk["warnings"] == ["debug file missing: debug_scores"]


def test_audio_phase_static_only_edit_is_not_mistaken_for_replaced_audio(monkeypatch, tmp_path):
    """A single static shot whose video-centre phase is 156 ms off: the +-100 ms per-segment search sees
    no correlation (the run looks 'audio replaced'); the wide search still finds the in-point, and the
    re-measured run is 'ok' with a small residual."""
    fps, sr = Fraction(24000, 1001), 16000
    rng = np.random.default_rng(9)
    raw_y = (rng.standard_normal(20 * sr) * 0.1).astype(np.float32)
    j2, n1 = 303, 48
    a0 = int(Fraction(j2) * sr / fps)
    n_s = int(Fraction(n1) * sr / fps)
    comp_y = raw_y[a0:a0 + n_s].copy()
    fm = FrameMap(n1)
    fm.status = np.full(n1, Status.MATCH, np.int8)
    fm.raw, fm.raw_lo, fm.raw_hi = np.full(n1, 330, np.int32), np.full(n1, 300, np.int32), np.full(n1, 360, np.int32)
    fm.soft_lo, fm.soft_hi = fm.raw_lo, fm.raw_hi
    install(monkeypatch, "segment", build_segments=lambda fm_, *a, **k: [
        Segment(1, "raw", 0, n1, speed=1.0, raw_in_frame=330, transform=Sim(1, 0, 0, 0).to_dict())])
    ctx = _assembly_ctx(tmp_path, fps, n1, int(20 * fps), comp_y, raw_y, sr)
    recs = []
    dlog = types.SimpleNamespace(record=lambda st, dec, **k: recs.append((st, dec, k)))
    _, segs, audio_result, _ = pipeline.segment_and_assemble(ctx, fm, dlog, tmp_path / "dbg")
    first = [k for st, dec, k in recs if st == "audio_segments" and dec == "summary"][0]
    assert first["status"] == "audio_replaced"                     # what the +-100 ms search concluded
    s = segs[0]
    assert s.audio["phase_source"] == "audio" and abs(s.audio["lag_ms"]) < 3.0
    assert audio_result["status"] == "ok" and s.audio["exception"] is None
    # on the in-point up to the cell margin (5 % of the 0.5-frame floor∩round cell, FX-10)
    m = pipeline.audio_phase_margin(0.5, Config().ae_slack_tol_frames) / float(fps)
    assert abs(s.raw_in_seconds - float(Fraction(j2) / fps)) <= m + 0.0005
    assert pipeline.phase_slack(s, fps, fps)["slack_frames"] >= Config().ae_slack_tol_frames


@pytest.mark.parametrize("run_len", [180, 900])
def test_audio_phase_static_shot_inpoint_on_the_interval_edge(run_len):
    """Review R2-2: RAW holds a static run of ``run_len`` identical frames (29.97p); the competitor uses
    60 frames of it FROM ITS FIRST FRAME (NLE in-point on the RAW shot boundary = the lower edge of the
    feasible raw_in interval, which spans seconds). The D3 margin is a fraction of a RAW frame, not 5 % of
    the ambiguity span (which left raw_in 201 ms late and failed c5), and the wide search is centred on
    the feasible interval and covers all of it (the 900-frame run's interval is 28 s wide, beyond the old
    +-5 s cap): raw_in lands within 2 ms of the truth and a c5-style +-2 s xcorr leaves < 3 ms."""
    from match_cuts import audio_align, phase_solve
    cf, rf, sr = Fraction(30), Fraction(30000, 1001), 16000
    S, N = 300, 60
    fm = FrameMap(N)
    fm.status = np.full(N, Status.MATCH, np.int8)
    fm.raw = (S + np.arange(N)).astype(np.int32)                   # refine's 'best' frame: any of the run
    fm.raw_lo = fm.soft_lo = np.full(N, S, np.int32)
    fm.raw_hi = fm.soft_hi = np.full(N, S + run_len - 1, np.int32)
    seg = _solved(Segment(1, "raw", 0, N, speed=1.0), fm, cf, rf)
    lo, hi = seg.raw_in_interval_both or seg.raw_in_interval
    true_in = float(Fraction(S) / rf)
    assert lo == pytest.approx(true_in, abs=1e-6) and hi - lo > 3.0 and seg.raw_in_seconds - true_in > 1.0
    rng = np.random.default_rng(0)
    raw_y = rng.standard_normal(int((S + run_len) / float(rf) * sr) + 5 * sr).astype(np.float32)   # unique audio
    a0 = int(round(true_in * sr))
    comp_y = raw_y[a0:a0 + int(N / 30 * sr)].copy()
    res = {"status": "ok", "segments": {1: {"lag_ms": None, "corr": 0.05, "exception": "audio_replaced"}}}
    recs = []
    dlog = types.SimpleNamespace(record=lambda st, dec, **k: recs.append((st, dec, k)))
    moved, warns = pipeline.audio_informed_phase([seg], res, fm, comp_y, raw_y, sr, cf, rf, Config(), dlog)
    assert moved == [1] and not warns and seg.audio["phase_source"] == "audio"
    # FX-10: the margin is relative to the BREAKPOINT CELL the in-point lies in, not to the 4-28 s interval;
    # at 29.97 in 30 the 60 frames' breakpoints sit 1/1001 frame apart right after a frame boundary, so the
    # in-point lands in a cadence-narrow cell (its midpoint): pinned by the audio, exported frame-exact
    assert 0.0 <= seg.raw_in_seconds - true_in <= 0.002
    info = pipeline.phase_slack(seg, cf, rf)
    assert info["cell_ms"] == pytest.approx(1000.0 / 1001 / float(rf), rel=1e-3)
    assert info["slack_frames"] == pytest.approx(0.5 / 1001, rel=1e-3)
    assert pipeline.ae_phase_class(info, Config()) == "pinned" and not pipeline.ae_rule_sensitive(seg, Config(), cf, rf)
    rebuilt = audio_align.resample_at(raw_y, (seg.raw_in_seconds + np.arange(comp_y.size) / sr) * sr)
    lag, peak = audio_align.xcorr_lag(comp_y, rebuilt, sr, 2.0)                   # verify c5's +-2 s search
    assert abs(lag) * 1000.0 < 3.0 and peak > 0.9
    wide = [k for st, dec, k in recs if dec == "audio_phase"][0]["wide_search"]
    assert wide["max_lag_s"] >= 0.5 * (hi - lo)                                   # the whole interval searched
    for k in range(N):                                                            # every frame stays on the run
        assert S <= phase_solve.ae_frame(seg.raw_in_seconds, 1.0, k, 0, cf, rf) <= S + run_len - 1


_TOOL_DIR = Path(pipeline.__file__).resolve().parent.parent


def _doc_section(text: str, heading: str) -> str:
    """The body of the markdown section ``heading`` (up to the next heading of the same or a higher level)."""
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("#")
                and len(lines[i]) - len(lines[i].lstrip("#")) <= level and not lines[i].startswith("#" * (level + 1))),
               len(lines))
    return "\n".join(lines[start:end])


def test_readme_usage_exit_codes_match_d5():
    """Review R2-7: the README usage section documents the D5 exit codes (incl. 3 = nothing failed but a
    criterion is not_available), consistently with pipeline.exit_code_for -- not the pre-D5 '0 when
    nothing failed, 1 otherwise' sentence a wrapper script would misread."""
    readme = (_TOOL_DIR / "README.md").read_text(encoding="utf-8")
    usage = _doc_section(readme, "## Usage")
    assert "`0` when no acceptance criterion" not in readme
    rows = {}
    for line in usage.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].strip("`").isdigit():
            rows[int(cells[0].strip("`"))] = (cells[1], cells[2])
    assert sorted(rows) == [pipeline.EXIT_PASS, pipeline.EXIT_FAIL, pipeline.EXIT_ERROR, pipeline.EXIT_NOT_VERIFIED]
    assert "not_available" in rows[pipeline.EXIT_NOT_VERIFIED][0]
    assert rows[pipeline.EXIT_NOT_VERIFIED][1].startswith("`PASS (criterion 6 not verified")
    assert rows[pipeline.EXIT_PASS][1] == "`PASS`" and rows[pipeline.EXIT_FAIL][1] == "`FAIL`"
    # the table agrees with the implementation
    from match_cuts.verify import CRITERIA
    ok = {c: {"status": "pass"} for c in CRITERIA}
    assert pipeline.exit_code_for(ok, {}) == pipeline.EXIT_PASS
    na = dict(ok, **{CRITERIA[-1]: {"status": "not_available", "summary": "no Node.js"}})
    assert pipeline.exit_code_for(na, {}) == pipeline.EXIT_NOT_VERIFIED
    assert pipeline.headline_for(na, {}).startswith("PASS (criterion 6 not verified")
    assert pipeline.exit_code_for(na, {"s9_8_deliverables": {"status": "fail"}}) == pipeline.EXIT_FAIL


def test_design_contract_crossfade_keying_and_d3_margin():
    """Review AE2-4 / R2-2: DESIGN.md (the contract every module follows) states the crossfade keying
    export_ae implements -- the UPPER layer of the pair is keyed, an incoming MAIN-level (D1) layer
    RISING -- and the D3 margin rule pipeline.audio_phase_margin implements (FX-10: in breakpoint cells,
    never integer milliseconds)."""
    design = (_TOOL_DIR / "DESIGN.md").read_text(encoding="utf-8")
    flat = " ".join(line.strip().lstrip("#").strip() for line in design.splitlines())
    assert "ONLY the upper (outgoing) layer A is keyed" not in flat and "B stays 100 %" not in flat
    assert "ONLY the UPPER layer of the pair is keyed" in flat
    assert "B is keyed rising: 100·α_B at O..O+D-1, 100 at O+D" in flat
    d1 = flat[flat.index("**D1 Per-period layout.**"):flat.index("**D2 Box refinement")]
    assert "keys B rising" in d1 and "UPPER layer of the pair is keyed" in d1
    d3 = flat[flat.index("**D3 Audio-informed phase.**"):flat.index("**D4 Verification")]
    assert "min(cell / 2, max(5 % of the cell, ae_slack_tol_frames))" in d3
    assert "max(ae_min_margin_ms" not in d3 and "max(1 ms, 5 % of its width)" not in d3
    assert pipeline.AUDIO_PHASE_MARGIN_FRAC == 0.05
    assert pipeline.audio_phase_margin(0.5, 0.01) == pytest.approx(0.025)               # 5 % of the cell
    assert pipeline.audio_phase_margin(0.1, 0.01) == pytest.approx(0.01 + 1e-6)         # the slack tolerance
    assert pipeline.audio_phase_margin(4 / 1001, 0.01) == pytest.approx(2 / 1001)       # a cadence cell: its midpoint


def test_no_ae_flag_and_timeout_reach_the_config():
    from match_cuts import cli
    args = cli.build_parser().parse_args(["--no-ae", "--ae-timeout", "42"])
    cfg = cli.config_from_args(args)
    assert cfg.run_ae is False and cfg.ae_timeout_s == 42.0
    cfg2 = cli.config_from_args(cli.build_parser().parse_args([]))
    assert cfg2.run_ae is True and cfg2.ae_timeout_s == 600.0


def test_after_effects_wait_can_be_skipped_with_ctrl_c(tmp_path, monkeypatch):
    """User report: the run looked stuck after 'wrote build_ae_project.jsx' -- it was silently waiting (up to an
    hour) for After Effects. The wait is announced, and Ctrl+C skips only this step instead of killing the run."""
    import sys as _sys
    from match_cuts import pipeline
    jsx = tmp_path / "build_ae_project.jsx"
    jsx.write_text("// test")
    fake = tmp_path / "fake_ae.py"
    fake.write_text("import time\ntime.sleep(30)\n")
    env = {"ae_app": _sys.executable, "os": "Windows"}
    calls = {"n": 0}

    def sleepy(_s):
        calls["n"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline.time, "sleep", sleepy)
    # AfterFX.exe -r <jsx>  ->  here: python -r <jsx> exits at once with an error; the wait loop still runs
    res = pipeline.run_after_effects(env, jsx, timeout=5.0, poll_s=0.01)
    assert calls["n"] == 1
    assert res["status"] == "not_available" and "Ctrl+C" in res["reason"]
