"""Night 10: the channel's watermark (--watermark) on its own track above the frame for the whole edit -- Scale filling
the sequence (50 % for a 2160x3840 PNG on 1080x1920), Opacity 75 %, Blend Mode Overlay (FCP7 XML: "texturize", which
Premiere's XML import turns into Overlay) -- and its checks; the media copy fix is in test_conform.py."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.keep_speed import keep_speed

from test_keep_speed import cutlist
from test_night3 import _frame_png


def _watermark_png(path: Path, w: int = 2160, h: int = 3840) -> Path:
    """A watermark like the channels' (assets/channels/*/watermark.png): white text on a transparent 2160x3840 PNG."""
    from PIL import Image
    a = np.zeros((h, w, 4), np.uint8)
    a[2796:2873, 760:1430] = (255, 255, 255, 255)
    Image.fromarray(a, "RGBA").save(path)
    return path


def _edit(tmp_path, *, frame: bool = True, watermark: bool = True, speed: float = 1.0):
    from match_cuts import frame as F
    tmp_path.mkdir(parents=True, exist_ok=True)
    kc, _ = keep_speed(cutlist())
    cfg = Config(out_dir=str(tmp_path), premiere=True,
                 frame_png=str(_frame_png(tmp_path / "frame.png")) if frame else "",
                 watermark_png=str(_watermark_png(tmp_path / "watermark.png")) if watermark else "")
    if frame:
        F.apply_to_config(cfg)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg, speed=speed)
    return kc, cfg, xml


def test_the_watermark_is_its_own_track_above_the_frame_like_the_channel_projects(tmp_path):
    kc, cfg, xml = _edit(tmp_path)
    x = ex.parse_premiere_xml(xml)
    assert (x["width"], x["height"]) == (1080, 1920) and x["video_tracks"] == 3
    tracks = ET.parse(xml).getroot().findall("sequence/media/video/track")
    assert tracks[1].find("clipitem").get("id") == ex.FRAME_CLIP                  # V2: the frame
    wm = tracks[2].find("clipitem")                                               # V3: the watermark, on top
    assert wm.get("id") == ex.WATERMARK_CLIP and wm.findtext("file/name") == "watermark.png"
    assert (int(wm.findtext("start")), int(wm.findtext("end"))) == (0, x["duration"])   # over the whole edit
    assert wm.findtext("alphatype") == "straight"
    assert wm.findtext("compositemode") == "texturize"                           # Premiere's Overlay
    params = {(e.findtext("effectid"), p.findtext("parameterid")): p.findtext("value")
              for e in wm.iter("effect") for p in e.findall("parameter")}
    assert params[("basic", "scale")] == "50"                                     # 2160x3840 on 1080x1920
    assert params[("opacity", "opacity")] == "75"
    assert ex.watermark_track_problems(xml, x["duration"]) == []
    assert ex.frame_track_problems(xml, x["duration"]) == []
    v = ex.validate_premiere_exports(kc, xml, None, cfg)
    assert v["ok"], v.get("errors")


def test_without_a_frame_the_watermark_is_v2_and_without_either_v1_stays_alone(tmp_path):
    kc, cfg, xml = _edit(tmp_path / "a", frame=False)
    x = ex.parse_premiere_xml(xml)
    assert x["video_tracks"] == 2 and ex.watermark_track_problems(xml, x["duration"]) == []
    assert ex.validate_premiere_exports(kc, xml, None, cfg)["ok"]
    kc, cfg, xml = _edit(tmp_path / "b", frame=False, watermark=False)
    assert ex.parse_premiere_xml(xml)["video_tracks"] == 1
    assert ex.validate_premiere_exports(kc, xml, None, cfg)["ok"]


def test_the_watermark_checks_catch_a_wrong_blend_opacity_length_or_a_missing_track(tmp_path):
    kc, cfg, xml = _edit(tmp_path)
    n = ex.parse_premiere_xml(xml)["duration"]
    root = ET.parse(xml)
    wm = root.getroot().findall("sequence/media/video/track")[2].find("clipitem")
    wm.find("compositemode").text = "overlay"                         # Premiere imports this as Normal
    wm.find("end").text = str(n - 5)
    next(p for e in wm.iter("effect") if e.findtext("effectid") == "opacity"
         for p in e.findall("parameter")).find("value").text = "100"
    bad = tmp_path / "bad.xml"
    root.write(bad, encoding="UTF-8", xml_declaration=True)
    probs = ex.watermark_track_problems(bad, n)
    assert any("compositemode" in p and "texturize" in p for p in probs)
    assert any("covers 0-" in p for p in probs) and any("opacity 100" in p for p in probs)
    # the watermark asked for but not written: the track count fails the run
    kc2, cfg2, xml2 = _edit(tmp_path / "c", watermark=False)
    cfg2.watermark_png = str(_watermark_png(tmp_path / "w2.png"))
    errs = ex.validate_premiere_exports(kc2, xml2, None, cfg2)["errors"]
    assert any("video tracks" in e and "watermark" in e for e in errs)


def test_at_125_percent_the_watermark_still_covers_the_whole_faster_edit(tmp_path):
    kc, cfg, xml = _edit(tmp_path, speed=1.25)
    x = ex.parse_premiere_xml(xml)
    assert ex.watermark_track_problems(xml, x["duration"]) == []
    assert ex.frame_track_problems(xml, x["duration"]) == []


def test_watermark_settings_never_change_an_analysis_cache_key():
    a = Config().analysis_params()
    b = Config(watermark_png="x.png", watermark_opacity=50.0).analysis_params()
    assert a == b and "watermark_png" not in a


def test_the_command_line_takes_watermark_only_with_premiere(tmp_path, capsys):
    import subprocess
    from match_cuts.cli import build_parser, config_from_args, main
    from match_cuts.common import ffmpeg_bin
    png = _watermark_png(tmp_path / "wm.png")
    comp, raw = tmp_path / "c.mp4", tmp_path / "r.mp4"
    for p, sec in ((comp, 1), (raw, 3)):
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:d={sec}",
                        "-pix_fmt", "yuv420p", str(p)], check=True)
    assert main(["--competitor", str(comp), "--raw", str(raw), "--watermark", str(png)]) == 2
    assert "add --premiere" in capsys.readouterr().err
    (tmp_path / "broken.png").write_bytes(b"not a png")
    assert main(["--competitor", str(comp), "--raw", str(raw), "--premiere", "--watermark",
                 str(tmp_path / "broken.png")]) == 2
    assert "not a readable PNG" in capsys.readouterr().err
    args = build_parser().parse_args(["--competitor", str(comp), "--raw", str(raw), "--premiere", "--watermark",
                                      str(png)])
    cfg = config_from_args(args, str(comp), str(raw))
    assert cfg.watermark_png == str(png) and cfg.watermark_opacity == 75.0


def test_the_framed_preview_puts_the_watermark_on_as_overlay(tmp_path):
    """preview_recreation.mp4 shows the watermark the way Premiere does: Overlay at 75 % of its own alpha -- white
    text lightens a mid grey picture under it and leaves black black; outside the text the picture is unchanged."""
    import subprocess
    from match_cuts.common import ffmpeg_bin
    from match_cuts.premiere_preview import _still
    from PIL import Image
    frame = tmp_path / "frame.png"
    a = np.zeros((1920, 1080, 4), np.uint8)
    a[:, :540] = (128, 128, 128, 255)                     # left half: an opaque mid grey; right half: the black under it
    Image.fromarray(a, "RGBA").save(frame)
    wm = _watermark_png(tmp_path / "wm.png")
    out = _still(ffmpeg_bin(), tmp_path / "s.mp4", str(frame), 3, 30, 1080, 1920, str(wm), 75.0)
    png = tmp_path / "s.png"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(out), "-frames:v", "1", str(png)], check=True)
    im = np.asarray(Image.open(png).convert("RGB")).astype(float)
    text_grey, text_black = im[1420:1430, 400:520].mean(), im[1420:1430, 600:700].mean()
    plain_grey = im[1000:1010, 400:520].mean()
    # Overlay of white on 128: 255 -- at 75 %: about 128 + 0.75 x 127 = 223; on black it stays black
    assert plain_grey == pytest.approx(128, abs=6) and text_grey == pytest.approx(223, abs=12)
    assert text_black < 12
