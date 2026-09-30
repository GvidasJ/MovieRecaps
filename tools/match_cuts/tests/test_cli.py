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

def _fake_result(statuses: dict[str, str], det: str = "pass") -> dict:
    crit = {k: {"status": v, "summary": f"{k} summary"} for k, v in statuses.items()}
    checks = {"s9_7_determinism": {"status": det, "summary": "det"}}
    return {"criteria": crit, "checks": checks, "warnings": ["w1"], "paths": {"cutlist": "out/cutlist.json"},
            "exit_code": pipeline.exit_code_for(crit, checks)}


ALL_PASS = {"c1_coverage": "pass", "c2_cuts": "pass", "c3_source_frames": "pass_with_exceptions",
            "c4_speed_framing": "pass", "c5_audio": "pass_with_exceptions", "c6_after_effects": "pass"}


def test_exit_codes():
    assert pipeline.exit_code_for({k: {"status": v} for k, v in ALL_PASS.items()}) == 0
    assert pipeline.exit_code_for({**{k: {"status": v} for k, v in ALL_PASS.items()}, "c2_cuts": {"status": "fail"}}) == 1
    assert pipeline.exit_code_for({k: {"status": v} for k, v in ALL_PASS.items()},
                                  {"s9_7_determinism": {"status": "fail"}}) == 1
    assert pipeline.exit_code_for({"c1_coverage": {"status": "pass"}}) == 1          # incomplete verification
    assert pipeline.exit_code_for({**{k: {"status": v} for k, v in ALL_PASS.items()},
                                   "c6_after_effects": {"status": "not_available"}}) == 0


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
    assert "out/cutlist.json" in out.out and "w1" in out.out and "swapped" in out.out
    monkeypatch.setattr(pipeline, "run", lambda cfg: _fake_result({**ALL_PASS, "c3_source_frames": "fail"}))
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"])]) == 1
    assert "match_cuts result: FAIL" in capsys.readouterr().out

    def boom(cfg):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(pipeline, "run", boom)
    assert cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"])]) == 2
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
    assert s.ae_margin_ms == pytest.approx(0.5 / 30 * 1000)
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
    assert s3.raw_in_interval_both is None and "AE-rule-sensitive" in s3.notes
    assert any("AE-rule-sensitive" in w and "--ae-time-mode frames" in w for w in warns)
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
            return OverlayMasks()

    def analyze_layout(comp, cfg, cache, debug_dir, dlog):
        (Path(debug_dir) / "layout.png").write_bytes(b"png")
        return Layout(W, H, mode="fullscreen", box=Box(0, 0, W, H)), OverlayMasks()
    install(monkeypatch, "layout", analyze_layout=analyze_layout, OverlayMasks=OverlayMasks,
            allowed_mask=lambda layout, overlays, k, comp, dilate_px=None: np.ones((H, W), bool))
    install(monkeypatch, "audio_align", coarse_align=lambda *a, **k: AudioHints.empty(),
            xcorr_lag=lambda a, b, sr, m: (0.0, 1.0),
            analyze_segments_audio=lambda segs, cy, ry, sr, fps, cfg, dlog: {
                "segments": {}, "added_audio": [], "status": "no_audio", "notes": ["no audio in either file"]})

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
        assert all(isinstance(a.sim, Sim) for a in anchors)
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
    verify = json.loads((out / "verify.json").read_text())
    st = {k: v["status"] for k, v in verify["criteria"].items()}
    assert st == {"c1_coverage": "pass", "c2_cuts": "pass", "c3_source_frames": "pass", "c4_speed_framing": "pass",
                  "c5_audio": "pass_with_exceptions", "c6_after_effects": "pass"}, verify["failures"]
    assert verify["checks"]["s9_7_determinism"]["status"] == "pass"
    assert verify["checks"]["s9_6_ae_render"]["status"] == "not_available"
    assert verify["checks"]["s9_3_visual"]["source"] == "preview_recreation.mp4"
    assert verify["checks"]["s9_3_visual"]["distribution"]["min"] > 0.99
    assert calls["mock"] == list(pipeline.MOCK_SCENARIOS) and calls["compare_src"].endswith("preview_recreation.mp4")
    # deliverables
    for rel in ("cutlist.json", "cutlist.csv", "recreated_edit.xml", "recreated_edit.edl", "build_ae_project.jsx",
                "preview_recreation.mp4", "compare.mp4", "report.md", "verify.json", "media/competitor_ref.mp4",
                f"media/{clips['landscape'].name}", "debug/cuts/cut_01.png", "debug/cuts/cut_03.png"):
        assert (out / rel).exists(), rel
    for rel in ("frame_map.npz", "decisions.jsonl", "match_cuts.log", "layout.json", "ae_plan.json"):
        assert (work / rel).exists(), rel
    cl = json.loads((out / "cutlist.json").read_text())
    assert cl["competitor"]["file"] == "media/competitor_ref.mp4" and cl["competitor"]["fps"] == "30/1"
    assert cl["raw"]["frames"] == RAW_N and cl["raw"]["conformed"] is False and cl["raw"]["source_path"]
    assert cl["layout"]["mode"] == "match" and cl["settings"]["main_fps"] == "30/1"
    assert cl["settings"]["fps_source_max_error_s"] == 0.0
    segs = cl["segments"]
    assert [s["comp_in"] for s in segs] == [0, 12, 20, 26]
    assert segs[0]["raw_in_frame"] == 20 and segs[0]["raw_in_seconds"] == pytest.approx(20.25 / 30, abs=1e-9)
    assert segs[0]["raw_in_interval"] == [pytest.approx(20 / 30), pytest.approx(21 / 30)] and segs[0]["ae_margin_ms"] > 16
    assert segs[2]["label"].startswith("MISSING - not in RAW") and segs[2]["raw_in_seconds"] is None
    assert set(cl["provenance"]["timings"]) >= {"S0 env", "S2 probe+conform", "S9 verify", "total"}
    assert cl["provenance"]["input_hashes"]["competitor"] == file_hash(clips["portrait"])
    fm = FrameMap.load(work / "frame_map.npz")
    assert np.array_equal(fm.raw, truth)
    decisions = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    assert any(d["stage"] == "phase_solve" and d["decision"] == "raw_in" for d in decisions)
    report = (out / "report.md").read_text()
    assert "## 1. Acceptance criteria" in report and "Section could not be rendered" not in report
    for line in ("c1 coverage", "c6 After Effects", "9.7 determinism"):
        assert line in printed
    assert calls["refine"] == 1 and calls["index"] == 1

    # second run: FrameMap cache hit (no search / refine), identical cutlist apart from timings
    first = json.loads((out / "cutlist.json").read_text())
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert calls["refine"] == 1 and calls["index"] == 1
    second = json.loads((out / "cutlist.json").read_text())
    first["provenance"].pop("timings")
    second["provenance"].pop("timings")
    assert first == second
    det = json.loads((out / "verify.json").read_text())["checks"]["s9_7_determinism"]
    assert det["previous_run"] == {"compared": True, "identical": True, "differences": []}
    assert "identical to the previous run" in det["summary"]
    assert second["raw"]["conform_reason"] == "AE-safe: used unchanged"
    # the decision log is truncated per run (not appended): one phase-solve record per raw segment
    again = [json.loads(ln) for ln in (work / "decisions.jsonl").read_text().splitlines()]
    assert sum(1 for d in again if d["stage"] == "phase_solve" and d["decision"] == "raw_in") == 3
    assert any(d["stage"] == "refine" and d["decision"] == "cache_hit" for d in again)


def test_end_to_end_non_match_layout_uses_in_memory_render(monkeypatch, clips, tmp_path, capsys):
    calls: dict = {}
    install_stub_world(monkeypatch, calls)
    out = tmp_path / "o"
    code = cli.main(["--competitor", str(clips["portrait"]), "--raw", str(clips["landscape"]), "--out", str(out),
                     "--work", str(tmp_path / "w"), "--layout", "fill", "--skip-preview"])
    capsys.readouterr()
    verify = json.loads((out / "verify.json").read_text())
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
    # segment.py already solved (e.g. with blend-frame constraints the FrameMap cannot hold): kept as is
    s = Segment(1, "raw", 0, 20, speed=1.0, raw_in_seconds=(300 + 0.4) / 30 + 1e-12,
                raw_in_interval=[300 / 30, 301 / 30], raw_in_interval_both=[300 / 30, 300.5 / 30], ae_margin_ms=13.3)
    dl_entries = []
    dlog = types.SimpleNamespace(record=lambda *a, **k: dl_entries.append((a, k)))
    warns = pipeline.solve_segment_phase(s, fm, F30, F30, Config(), dlog, phase)
    assert s.raw_in_seconds == pytest.approx(300.4 / 30, abs=1e-9) and s.raw_in_seconds == round(s.raw_in_seconds, 9)
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
