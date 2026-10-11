"""Night 4: every A1 clip at 0 dB (no level keyframes: the laptop's 125 % output\\005 imported muted), the competitor's
cut points and ending by default (--remove-silence for the old silence removal), and your template as the default
frame (templates/default.png; --frame PNG, --no-frame)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import cli
from match_cuts import export_xml_edl as ex
from match_cuts import frame as F
from match_cuts.config import Config, removes_silence
from match_cuts.keep_speed import keep_speed

from test_keep_speed import cutlist
from test_night3 import _frame_png


# ---------------------------------------------------------------------------------------------------------------------
# A1: 0 dB, no keyframes, no effect but the speed
# ---------------------------------------------------------------------------------------------------------------------

def _xml(tmp_path, k: float = 1.0, ripple=None) -> Path:
    kc, _ = keep_speed(cutlist())
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    p = tmp_path / f"edit_{k:g}.xml"
    ex.write_premiere_xml(kc, p, cfg, ripple, speed=k)
    return p


@pytest.mark.parametrize("k", [1.0, 1.25])
def test_every_a1_clip_imports_at_0_db_without_keyframes(tmp_path, k):
    from match_cuts import silence as S
    kc, _ = keep_speed(cutlist())
    n = int(kc.competitor["frames"]) * ex.premiere_factor(kc.comp_fps, ex.premiere_settings(Config())["fps"])
    rp = S.Ripple([S.Cut(20, 40, 0, 0)], n)            # a cut that used to get fade keyframes on both sides
    xml = _xml(tmp_path, k, rp)
    root = ET.parse(xml).getroot()
    items = root.findall("sequence/media/audio/track/clipitem")
    assert items, "no A1 clips"
    for ci in items:
        assert ci.findtext("enabled") == "TRUE"
        assert ci.find(".//keyframe") is None
        assert {e.findtext("effectid") for e in ci.findall(".//effect")} <= {"timeremap"}
    assert ex.premiere_audio_problems(xml) == []
    assert all(a["levels"] == [] for a in ex.parse_premiere_xml(xml)["audio"])


def test_the_audio_check_catches_levels_keyframes_and_a_muted_clip(tmp_path):
    xml = _xml(tmp_path)
    tree = ET.parse(xml)
    ci = tree.getroot().find("sequence/media/audio/track/clipitem")
    ci.find("enabled").text = "FALSE"
    f = ET.SubElement(ci, "filter")
    e = ET.SubElement(f, "effect")
    ET.SubElement(e, "effectid").text = "audiolevels"
    kf = ET.SubElement(ET.SubElement(e, "parameter"), "keyframe")
    ET.SubElement(kf, "when").text = "0"
    ET.SubElement(kf, "value").text = "0"
    bad = tmp_path / "bad.xml"
    tree.write(bad)
    probs = ex.premiere_audio_problems(bad)
    assert any("switched off" in p for p in probs)
    assert any("audiolevels" in p for p in probs)
    assert any("keyframes" in p for p in probs)


# ---------------------------------------------------------------------------------------------------------------------
# the competitor's cut points and ending by default
# ---------------------------------------------------------------------------------------------------------------------

def test_silence_removal_is_off_by_default():
    assert not removes_silence(Config())                                  # a run with a competitor
    assert removes_silence(Config(remove_silence=True))                   # --remove-silence
    assert not removes_silence(Config(remove_silence=True, keep_silence=True))
    assert removes_silence(Config(competitor=""))                         # RAW-only: its edit is the removal
    assert not removes_silence(Config(competitor="", keep_silence=True))


def test_the_cli_turns_silence_removal_on_only_when_asked():
    p = cli.build_parser()
    assert cli.config_from_args(p.parse_args(["--premiere"])).remove_silence is False
    assert cli.config_from_args(p.parse_args(["--premiere", "--remove-silence"])).remove_silence is True


def test_the_ending_check_wants_the_competitors_last_raw_moment(tmp_path):
    kc, _ = keep_speed(cutlist())
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg)
    plan, _, _ = ex.premiere_clips(kc, cfg)
    x = ex.parse_premiere_xml(xml)
    assert ex.ending_problems(plan, x) == []
    last = max((c for c in x["clips"] if c["end"] >= 0), key=lambda c: c["end"])
    short = dict(x, clips=[dict(c, out=c["out"] - 30) if c is last else c for c in x["clips"]])
    probs = ex.ending_problems(plan, short)
    assert probs and "short" in probs[0]                                  # 0.5 s short: the run fails


def test_the_default_run_validates_without_the_speech_and_silence_checks(tmp_path):
    kc, _ = keep_speed(cutlist())
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml, edl = tmp_path / "1_edit.xml", tmp_path / "e.edl"
    ex.write_premiere_xml(kc, xml, cfg)
    ex.write_edl(kc, edl, cfg)
    v = ex.validate_premiere_exports(kc, xml, edl, cfg)
    assert v["ok"], v["errors"]
    assert v["ending_problems"] == [] and v["audio_problems"] == []
    assert v["speech_checked"] is False and v["silence_checked"] is False


# ---------------------------------------------------------------------------------------------------------------------
# your template as the default frame
# ---------------------------------------------------------------------------------------------------------------------

def test_the_default_template_is_in_the_repository_with_its_hole():
    d = F.default_frame()
    assert d is not None and d.name == "default.png"
    fr = F.load_frame(d)
    assert (fr.width, fr.height) == (1080, 1920)
    x, y, w, h = fr.hole
    assert (x, y) == (22.0, 527.0) and (w, h) == (1036.0, 1210.0)       # the rounded box of your mike005 template


def test_every_premiere_run_uses_the_default_frame_unless_told_otherwise(tmp_path):
    p = cli.build_parser()
    png = _frame_png(tmp_path / "mine.png")
    assert cli.config_from_args(p.parse_args(["--premiere"])).frame_png == str(F.default_frame())
    assert cli.config_from_args(p.parse_args(["--premiere", "--no-frame"])).frame_png == ""
    assert cli.config_from_args(p.parse_args(["--premiere", "--frame", str(png)])).frame_png == str(png)
    assert cli.config_from_args(p.parse_args([])).frame_png == ""           # the After Effects path: no frame


def test_frame_and_no_frame_together_stop_the_run(tmp_path, capsys):
    png = _frame_png(tmp_path / "mine.png")
    rc = cli.main(["--premiere", "--frame", str(png), "--no-frame", "--competitor", str(png), "--raw", str(png)])
    assert rc == 2 and "--no-frame" in capsys.readouterr().err


def test_the_sequence_is_the_frames_own_size_unless_set(tmp_path):
    small = _frame_png(tmp_path / "small.png")                                # 1080x1920
    big = _frame_png(tmp_path / "big.png", 2160, 3840, hole=(44, 1054, 2116, 3474))
    assert F.sequence_size(Config(frame_png=str(small))) == (1080, 1920)
    assert F.sequence_size(Config(frame_png=str(big))) == (2160, 3840)
    assert F.sequence_size(Config(frame_png=str(small), frame_size="2160x3840")) == (2160, 3840)


def test_the_default_frame_is_filled_by_every_clip(tmp_path):
    kc, _ = keep_speed(cutlist())
    cfg = Config(out_dir=str(tmp_path), premiere=True, frame_png=str(F.default_frame()))
    F.apply_to_config(cfg)
    assert cfg.premiere_size == "1080x1920" and cfg.premiere_window == (19.84, 524.84, 1040.32, 1214.32)   # 2 px more
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg)
    assert ex.premiere_gaps(xml, cfg) == []                                   # no black strip anywhere in the box
    assert ex.frame_track_problems(xml, ex.parse_premiere_xml(xml)["duration"]) == []
