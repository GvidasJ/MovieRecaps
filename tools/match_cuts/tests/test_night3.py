"""Night 3: --speed (the edit made at 100 %, 1_edit.xml played faster), --frame (the channel's PNG with a transparent
hole), the noise / action-beat cut rules learned from 021, the caption cleanups (021, laptop004), batch speed.txt,
learn on a template project / a moved run, and the test-case window copy."""
from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.keep_speed import keep_speed

from test_keep_speed import cutlist


# ---------------------------------------------------------------------------------------------------------------------
# --speed
# ---------------------------------------------------------------------------------------------------------------------

def _speed_pair(tmp_path, k: float):
    cl = cutlist()
    kc, _ = keep_speed(cl)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    base, fast = tmp_path / "edit_100pct.xml", tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, base, cfg)
    ex.write_premiere_xml(kc, fast, cfg, speed=k)
    return kc, cfg, base, fast


@pytest.mark.parametrize("k", [1.25, 1.5, 0.8])
def test_speed_plays_the_100_percent_edit_k_times_as_fast(tmp_path, k):
    kc, cfg, base, fast = _speed_pair(tmp_path, k)
    a, b = ex.parse_premiere_xml(base), ex.parse_premiere_xml(fast)
    assert len(a["clips"]) == len(b["clips"]) and len(a["audio"]) == len(b["audio"])
    assert b["duration"] == pytest.approx(a["duration"] / k, abs=1)
    for p, q in zip(a["clips"] + a["audio"], b["clips"] + b["audio"]):
        assert q["speed"] == pytest.approx(p["speed"] * k, rel=1e-4)
        assert q["start"] == pytest.approx(p["start"] / k, abs=1) and q["end"] == pytest.approx(p["end"] / k, abs=1)
        assert abs(q["in"] - p["in"]) <= 2 * (int(np.ceil(k)) + 1)          # the same RAW moment (the retimed grid)
    assert ex.speed_problems(base, fast, k) == []
    assert ex.premiere_item_problems(fast) == []
    assert ex.validate_premiere_exports(kc, base, None, cfg)["ok"]        # every other check reads the 100 % edit


def test_speed_writes_in_out_on_the_retimed_clip_premiere_reads_not_source_time(tmp_path):
    # the output/020 bug: <in> written as source time on a 125 % clip showed RAW 629 s instead of 503 s in Premiere
    _, _, base, fast = _speed_pair(tmp_path, 1.25)
    root = ET.parse(fast).getroot()
    first = root.find("sequence/media/video/track/clipitem")
    x_in = int(first.findtext("in"))
    played = x_in * 1.25 / 60.0                                          # what Premiere plays: <in> x speed
    assert played == pytest.approx(503.52, abs=0.05)
    assert int(first.findtext("out")) - x_in == int(first.findtext("end")) - int(first.findtext("start"))


def test_speed_problems_catch_a_clip_playing_the_wrong_moment(tmp_path):
    _, _, base, fast = _speed_pair(tmp_path, 1.25)
    root = ET.parse(fast).getroot()
    ci = root.find("sequence/media/video/track/clipitem")
    ci.find("in").text = str(int(ci.findtext("in")) + 600)               # 12.5 s of RAW off
    ci.find("out").text = str(int(ci.findtext("out")) + 600)
    ET.ElementTree(root).write(fast)
    assert any("plays RAW" in p for p in ex.speed_problems(base, fast, 1.25))


def test_scaled_srt_moves_every_caption_with_its_frames():
    from match_cuts.pipeline import scaled_srt
    from match_cuts.captions import parse_srt
    rows = [{"start_ms": 0, "end_ms": 1000, "text": "Excuse me"}, {"start_ms": 1000, "end_ms": 2050, "text": "ladies"},
            {"start_ms": 2050, "end_ms": 2117, "text": "*looks over*"}]
    out = parse_srt(scaled_srt(rows, 1.25, 60.0))
    assert [r["text"] for r in out] == ["Excuse me", "ladies", "*looks over*"]
    assert [(r["start_ms"], r["end_ms"]) for r in out] == [(0, 800), (800, 1633), (1633, 1700)]
    assert all(a["end_ms"] <= b["start_ms"] for a, b in zip(out, out[1:]))


def test_min_clip_scales_with_the_speed_so_no_mini_cut_is_left_after_it():
    assert ex.premiere_settings(Config(premiere=True))["min_clip"] == 10
    assert ex.premiere_settings(Config(premiere=True, premiere_speed=1.25))["min_clip"] == 13


# ---------------------------------------------------------------------------------------------------------------------
# --frame
# ---------------------------------------------------------------------------------------------------------------------

def _frame_png(path: Path, w: int = 1080, h: int = 1920, hole=(22, 527, 1058, 1737), watermark: bool = True) -> Path:
    from PIL import Image
    a = np.full((h, w, 4), 255, np.uint8)
    x0, y0, x1, y1 = hole
    a[y0:y1, x0:x1, 3] = 0
    a[y0:y0 + 40, x0:x0 + 40, 3] = 255                                   # a rounded corner (opaque)
    if watermark:
        a[(y0 + y1) // 2:(y0 + y1) // 2 + 30, x0 + 200:x0 + 400, 3] = 20  # a faint watermark inside the hole
    Image.fromarray(a, "RGBA").save(path)
    return path


def test_load_frame_finds_the_transparent_hole(tmp_path):
    from match_cuts.frame import load_frame
    fr = load_frame(_frame_png(tmp_path / "f.png"))
    assert fr.hole == (22.0, 527.0, 1036.0, 1210.0)
    assert fr.window(2160, 3840) == (44.0, 1054.0, 2072.0, 2420.0)        # a 1080x1920 PNG on the 4K sequence: x 2
    assert fr.premiere_scale(2160, 3840) == pytest.approx(200.0)
    top, bottom = fr.caption_zone(3840, 2160)
    assert 1054.0 < top < bottom < 1054.0 + 2420.0                         # inside the hole, below its top


def test_load_frame_refuses_a_png_without_a_hole(tmp_path):
    from PIL import Image
    from match_cuts.frame import load_frame
    Image.new("RGB", (100, 200), (255, 255, 255)).save(tmp_path / "flat.png")
    with pytest.raises(ValueError, match="no transparency"):
        load_frame(tmp_path / "flat.png")
    Image.new("RGBA", (100, 200), (255, 255, 255, 255)).save(tmp_path / "opaque.png")
    with pytest.raises(ValueError, match="no transparent area"):
        load_frame(tmp_path / "opaque.png")


def test_frame_puts_the_png_on_v2_and_every_clip_covers_its_hole(tmp_path):
    from match_cuts import frame as F
    png = _frame_png(tmp_path / "frame.png")
    cl = cutlist()
    kc, _ = keep_speed(cl)
    cfg = Config(out_dir=str(tmp_path), premiere=True, frame_png=str(png))
    F.apply_to_config(cfg)
    assert cfg.premiere_size == "2160x3840" and cfg.premiere_window == (44.0, 1054.0, 2072.0, 2420.0)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg)
    x = ex.parse_premiere_xml(xml)
    assert (x["width"], x["height"]) == (2160, 3840) and x["video_tracks"] == 2
    assert ex.frame_track_problems(xml, x["duration"]) == []
    v2 = ET.parse(xml).getroot().findall("sequence/media/video/track")[1].find("clipitem")
    assert v2.findtext("alphatype") == "straight" and v2.findtext("file/name") == "frame.png"
    assert ex.premiere_gaps(xml, cfg) == []                                # the hole covered by every V1 clip
    v = ex.validate_premiere_exports(kc, xml, None, cfg)
    assert v["ok"], v.get("errors")


# ---------------------------------------------------------------------------------------------------------------------
# cuts: noise and action-captioned beats (021)
# ---------------------------------------------------------------------------------------------------------------------

def _map(sounds, words, dur=30.0):
    from match_cuts import speech as S
    return S.SpeechMap(S._noise([S.Sound(a, b, sp, why) for a, b, sp, why in sounds], words), dur, list(words))


def test_a_long_sound_with_no_word_is_noise_a_cut_may_land_in():
    from match_cuts import speech as S
    sm = _map([(1.0, 1.4, True, "words: I'm done."), (1.6, 5.0, True, "no words, voiced 2.16 s")],
              [("I'm", 1.0, 1.15), ("done.", 1.2, 1.4)])
    assert not sm.sounds[0].noise and sm.sounds[1].noise
    assert S.end_at(sm, 2.0, 0.5, 0.05, 0.03) == 2.0                      # S44: the competitor's cut stays
    assert S.start_at(sm, 4.0, 8.0, 0.05, 0.03) == 4.0
    assert not S._inside(sm, 3.0)
    assert S.check([("S44", "end", 3.0, 10)], sm, Fraction(60)) == []      # no hard-check failure inside noise


def test_a_word_running_on_into_applause_is_split_at_the_word():
    sm = _map([(10.0, 11.5, True, "words: this.")], [("this", 10.02, 10.2)])
    word, tail = sm.sounds
    assert word.s1 == pytest.approx(10.32) and not word.noise
    assert tail.noise and tail.s1 == 11.5
    from match_cuts import speech as S
    assert S.start_at(sm, 10.6, 14.0, 0.05, 0.03) == 10.6                  # S50: starts where the competitor's does


def test_a_short_untranscribed_sound_stays_speech():
    sm = _map([(1.0, 1.3, True, "no words, voiced 0.07 s")], [])
    assert not sm.sounds[0].noise


def test_a_kept_beat_is_never_trimmed_into():
    from match_cuts import speech as S
    sm = S.SpeechMap([S.Sound(10.0, 10.6, True, "words: gentlemen")], 30.0, [("gentlemen", 10.0, 10.6)])
    p = S.Piece("S06", 0, 60, 600.0, 1.0)                                  # RAW 10.0-11.0 s, quiet after 10.6 s
    trims, _, rows, _ = S.snap_edits([p], sm, Fraction(60), 0.05, 0.03)
    assert trims == [(39, 60)]                                             # the quiet tail goes ...
    trims, _, rows, _ = S.snap_edits([p], sm, Fraction(60), 0.05, 0.03, keep=[(0, 60)])
    assert trims == []                                                     # ... unless "*looks over*" is on it


# ---------------------------------------------------------------------------------------------------------------------
# captions
# ---------------------------------------------------------------------------------------------------------------------

def test_a_space_the_ocr_lost_after_i_is_put_back():
    from match_cuts.caption_style import _split_lost_space
    assert _split_lost_space(["Ishould", "get", "away"]) == ["I", "should", "get", "away"]
    assert _split_lost_space(["Iguess", "it"]) == ["I", "guess", "it"]
    assert _split_lost_space(["It", "Ireland", "Imagine"]) == ["It", "Ireland", "Imagine"]


def test_screen_noise_of_an_animated_caption_is_cleaned():
    from match_cuts.caption_style import _screen_noise_out
    rows = [(272, 339, "keep a secret, huh?", {}), (339, 341, "A\nT.", {}), (341, 343, "1A", {}), (343, 352, "A", {}),
            (352, 386, "I raised Po", {}), (687, 689, "I guêssit would be", {}), (689, 691, "Iguess it wuld be", {}),
            (691, 701, "I guess it would be", {}), (701, 729, "I guess it would be", {}), (786, 795, "1", {}),
            (943, 954, "_____", {}), (2144, 2197, "*high five*", {}), (2197, 2261, "high fives?", {})]
    out = [(a, b, t) for a, b, t, _ in _screen_noise_out(rows, 60.0)]
    assert out == [(272, 352, "keep a secret, huh?"), (352, 386, "I raised Po"), (687, 729, "I guess it would be"),
                   (2144, 2197, "*high five*"), (2197, 2261, "high fives?")]


# ---------------------------------------------------------------------------------------------------------------------
# batch speed.txt
# ---------------------------------------------------------------------------------------------------------------------

def test_batch_speed_comes_from_the_video_folder_then_the_options(tmp_path):
    from match_cuts.batch import video_speed, without_speed
    assert video_speed(tmp_path) == (100.0, "")
    assert video_speed(tmp_path, ["--speed", "110"])[0] == 110.0
    (tmp_path / "speed.txt").write_text("125\n", encoding="utf-8")
    assert video_speed(tmp_path, ["--speed", "110"]) == (125.0, "; from speed.txt")
    assert without_speed(["--fast", "--speed", "110", "--speed=90", "--frame", "f.png"]) == ["--fast", "--frame",
                                                                                            "f.png"]
    (tmp_path / "speed.txt").write_text("fast", encoding="utf-8")
    assert video_speed(tmp_path)[0] == 100.0


# ---------------------------------------------------------------------------------------------------------------------
# learn
# ---------------------------------------------------------------------------------------------------------------------

def test_learn_takes_the_sequence_that_plays_the_runs_raw():
    from match_cuts import learn as L
    from match_cuts import prproj as PR
    tmpl = PR.Sequence("template (020)", 60.0, 1080, 1920, [
        PR.Item(PR.AUDIO, 1, 0, 20, media=r"D:\Shared\old\raw.mp4"), *[
            PR.Item(PR.VIDEO, 6, i, i + 1, media="1196574294", text="cap") for i in range(30)]])
    mine = PR.Sequence("021", 60.0, 1080, 1920, [PR.Item(PR.VIDEO, 1, 0, 29.7, media=r"C:\run\extras\media\raw.mp4"),
                                                PR.Item(PR.AUDIO, 1, 0, 29.7, media=r"C:\run\extras\media\raw.mp4")])
    pr = PR.Project("p.prproj", [tmpl, PR.Sequence("nest", 60.0, 1080, 1920), mine],
                    {r"D:\Shared\old\raw.mp4": {"width": 3840, "height": 2160, "fps": 23.976, "duration": 1281.6},
                     r"C:\run\extras\media\raw.mp4": {"width": 1920, "height": 1080, "fps": 29.97, "duration": 1220.8}})
    assert PR.main_sequence(pr) is tmpl                                   # the most clips: the template's
    cl = {"raw": {"width": 1920, "height": 1080, "fps": "30000/1001", "duration_s": 1220.786}}
    assert L.sequence_of(pr, "raw.mp4", cl) is mine


def test_learn_prefers_the_runs_own_media_over_a_path_from_another_pc(tmp_path):
    from match_cuts import learn as L
    run = tmp_path / "laptop004"
    (run / "extras" / "media").mkdir(parents=True)
    (run / "extras" / "media" / "raw_ae.mp4").write_bytes(b"x")
    other = tmp_path / "input_raw.mp4"
    other.write_bytes(b"y")                                               # that PC's input\raw.mp4: another video here
    cl = {"raw": {"file": "media/raw_ae.mp4", "source_path": str(other), "duration_s": 5694.7}}
    assert L.run_media({"dir": run}, cl, "raw") == run / "extras" / "media" / "raw_ae.mp4"


def test_learn_compares_a_mirrored_clip_as_the_unmirrored_picture(tmp_path):
    from match_cuts import learn as L
    cl = cutlist()                                                       # S01 / S02 mirrored (flip_h)
    kc, _ = keep_speed(cl)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, Config(out_dir=str(tmp_path), premiere=True))
    pic, _, _, _ = L.tool_clips(xml, 3840.0)
    raw, _, _, _ = L.tool_clips(xml, 3840.0, unflip=False)
    assert pic[0].flipped and pic[0].dx == pytest.approx(-raw[0].dx)
    assert not pic[-1].flipped and pic[-1].dx == pytest.approx(raw[-1].dx)


# ---------------------------------------------------------------------------------------------------------------------
# test cases: a sharp window of a long RAW
# ---------------------------------------------------------------------------------------------------------------------

def test_window_copy_keeps_the_frames_of_the_window(tmp_path):
    from match_cuts.common import ffmpeg_bin
    from match_cuts.testcases import probe, window_copy
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24000/1001:d=12",
                    "-f", "lavfi", "-i", "sine=f=440:d=12", "-shortest", "-c:v", "libx264", "-g", "48",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    info = window_copy(src, tmp_path / "w.mp4", 4.0, 7.0)
    fps = Fraction(24000, 1001)
    assert info["offset"] == pytest.approx(float(int(4.0 * fps) / fps), abs=1e-6)
    got = probe(tmp_path / "w.mp4")
    v = next(s for s in got["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"], v["r_frame_rate"]) == (160, 90, "24000/1001")

    def frame(path, t):
        p = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{t:.4f}", "-i", str(path), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
        return np.frombuffer(p.stdout, np.uint8).astype(float)
    k = 30
    a = frame(tmp_path / "w.mp4", (k + 0.25) / float(fps))
    d0 = np.abs(a - frame(src, info["offset"] + (k + 0.25) / float(fps))).mean()
    d1 = np.abs(a - frame(src, info["offset"] + (k + 1.25) / float(fps))).mean()
    assert d0 < d1                                                        # frame k of the copy is frame k0 + k


def test_a_window_case_moves_its_key_onto_the_full_raw(tmp_path):
    from match_cuts import testcases as T
    d = tmp_path / "case"
    d.mkdir()
    for n in ("competitor.mp4", "raw.mp4"):
        (d / n).write_bytes(b"x")
    full = tmp_path / "full_raw.mp4"
    full.write_bytes(b"x")
    (d / "answer_edit.json").write_text(json.dumps({"audio": [{"start": 0, "end": 2, "kind": "raw", "src_in": 30.0,
                                                               "speed": 1.0}]}), encoding="utf-8")
    (d / "case.json").write_text(json.dumps({"full_raw": str(full), "raw_offset": 1112.5,
                                             "options": ["--frame", "{case}/frame.png"]}), encoding="utf-8")
    c = T.load(d)
    assert c.options == ["--frame", f"{d}/frame.png"] and c.raw_offset == 1112.5
    f = T.full_size(c)
    assert f.raw == full and json.loads(f.answer_edit.read_text(encoding="utf-8"))["audio"][0]["src_in"] == 1142.5


# ---------------------------------------------------------------------------------------------------------------------
# the competitor's mirror taken off (your finished 020 / laptop004)
# ---------------------------------------------------------------------------------------------------------------------

def test_unmirror_shows_the_same_part_of_the_raw_the_right_way_round():
    from match_cuts.geometry import Sim
    cl = cutlist()                                                       # S01, S02 mirrored, S03 not
    un, n = ex.unmirror(cl)
    assert n == 2 and not any(s.flip_h for s in un.segments) and cl.segments[0].flip_h    # the input untouched
    W, bc = float(cl.raw["width"]), 540.0                                # full-screen box: its centre x 540
    for old, new in zip(cl.segments[:2], un.segments[:2]):
        for a, b in [(old.transform, new.transform)] + list(zip(old.transform_keys or [], new.transform_keys or [])):
            sa, sb = Sim.from_dict(a), Sim.from_dict(b)
            x_flipped = W - (bc - sa.tx) / sa.s                         # the RAW x at the box centre, mirrored RAW
            x_plain = (bc - sb.tx) / sb.s
            assert x_plain == pytest.approx(x_flipped) and sb.s == sa.s and sb.ty == sa.ty
    assert un.segments[2] is cl.segments[2]


def test_the_premiere_plan_drops_the_mirror_unless_asked(tmp_path):
    cl = cutlist()
    kc, _ = keep_speed(cl)
    un, _ = ex.unmirror(kc)
    xml = tmp_path / "1_edit.xml"
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    ex.write_premiere_xml(un, xml, cfg)
    assert not any(c["flip"] for c in ex.parse_premiere_xml(xml)["clips"])
    assert ex.validate_premiere_exports(un, xml, None, cfg)["ok"]


def test_restyle_checks_the_captions_against_the_frames_caption_zone(tmp_path):
    import shutil
    from match_cuts.frame import load_frame
    from match_cuts.restyle import frame_check
    from match_cuts.testcases import REPO
    styled = tmp_path / "3_captions_styled.prproj"
    shutil.copyfile(REPO / "reference" / "popw_reference.prproj", styled)        # its captions sit at 58 %
    (tmp_path / "extras").mkdir()
    fr = load_frame(_frame_png(tmp_path / "frame.png"))
    (tmp_path / "extras" / "frame.json").write_text(json.dumps(fr.to_dict(1080, 1920)), encoding="utf-8")
    assert "inside the hole" in frame_check(styled)[0]
    high = _frame_png(tmp_path / "high.png", hole=(22, 1300, 1058, 1900))      # a hole below them: they are outside
    (tmp_path / "extras" / "frame.json").write_text(json.dumps(load_frame(high).to_dict(1080, 1920)), encoding="utf-8")
    lines = frame_check(styled)
    assert lines[0].startswith("FRAME:") and len(lines) > 1
    assert frame_check(tmp_path / "elsewhere" / "x.prproj") == []                 # no frame.json: no check
