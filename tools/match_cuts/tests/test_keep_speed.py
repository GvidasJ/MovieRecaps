"""--keep-speed (keep_speed.py): every RAW clip at 100 % -- the same moments of the RAW in the same order, on a
timeline stretched by each clip's speed; output/020's 125 % competitor as the example."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.keep_speed import keep_map, keep_speed
from match_cuts.model import Cutlist, Segment

PAN = {"scale": 0.5, "rotation_deg": 0.0, "tx": -420.0, "ty": 420.0}


def cutlist(extra: list[Segment] | None = None, frames: int = 700) -> Cutlist:
    def seg(id_, a, b, raw_s, **kw):
        kw.setdefault("transform", dict(PAN))
        return Segment(id=id_, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw_s, speed=kw.pop("speed", 1.25),
                       flip_h=kw.pop("flip_h", True), confidence=.98, **kw)
    segs = [seg(1, 0, 304, 503.52), seg(2, 304, 568, 511.0, transform_keys=[
                {"comp_frame": 304, **PAN}, {"comp_frame": 567, **dict(PAN, tx=-470.0)}]),
            seg(3, 568, 700, 600.0, speed=1.0, flip_h=False)] + list(extra or [])
    comp = {"file": "media/competitor_ref.mp4", "width": 1080, "height": 1920, "fps": "60/1", "frames": frames}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 3840, "height": 2160,
           "fps": "24000/1001", "frames": 30728, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "full", "box": {"x": 0.0, "y": 0.0, "w": 1080.0, "h": 1920.0,
                                                              "corner_radius": 0.0},
              "background": "solid", "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


def test_every_clip_plays_at_100_percent_from_the_same_moment_for_the_same_stretch_of_the_raw():
    cl = cutlist()
    kc, km = keep_speed(cl)
    assert km.stretched
    assert [(s.comp_in, s.comp_out) for s in kc.segments] == [(0, 380), (380, 710), (710, 842)]
    assert int(kc.competitor["frames"]) == 842 and int(cl.competitor["frames"]) == 700      # the input is untouched
    for old, new in zip(cl.segments, kc.segments):
        assert new.speed == 1.0 and new.raw_in_seconds == old.raw_in_seconds and new.flip_h == old.flip_h
        # the same stretch of the RAW: (frames at 100 %) == (frames x the competitor's speed)
        assert (new.comp_out - new.comp_in) == pytest.approx((old.comp_out - old.comp_in) * old.speed, abs=1)
    # S02's framing keys move with it (its last key on its last frame)
    keys = kc.segments[1].transform_keys
    assert keys[0]["comp_frame"] == pytest.approx(380) and keys[-1]["comp_frame"] == pytest.approx(380 + 263 * 1.25)


def test_the_premiere_xml_has_no_speed_change_and_the_same_moments(tmp_path):
    cl = cutlist()
    kc, _ = keep_speed(cl)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg)
    root = ET.parse(xml).getroot()
    assert not [e for e in root.iter("effect") if e.findtext("effectid") == "timeremap"]
    orig, _, _ = ex.premiere_clips(cl, cfg)
    x = ex.parse_premiere_xml(xml)
    assert len(x["clips"]) == len(orig)
    for got, o in zip(x["clips"], orig):
        assert got["speed"] == 1.0
        assert abs(got["in"] - o.src_in) <= 1                                       # the same first moment
        assert abs((got["out"] - got["in"]) - (o.src_out - o.src_in)) <= 2           # the same stretch of the RAW
        assert got["end"] - got["start"] == got["out"] - got["in"]                   # ... played at 100 %
    assert x["duration"] == 842 * 1                                                  # 60 fps sequence: 1 frame each
    assert ex.validate_premiere_exports(kc, xml, None, cfg)["ok"]


def test_a_placeholder_and_a_freeze_keep_their_length_and_a_dissolve_stretches_with_its_clip():
    xf = {"type": "crossfade", "duration_frames": 20, "alpha": [i / 20 for i in range(20)]}
    cl = cutlist(frames=820)
    cl.segments[2].comp_out, cl.segments[2].transition_out = 720, dict(xf)          # S03 (100 %) dissolves into S04
    cl.segments += [
        Segment(id=4, type="raw", comp_in=700, comp_out=760, raw_in_seconds=700.0, speed=1.5, transform=dict(PAN),
                transition_in=dict(xf)),
        Segment(id=5, type="not_in_raw", comp_in=760, comp_out=790, label="MISSING"),
        Segment(id=6, type="raw", comp_in=790, comp_out=820, raw_in_seconds=710.0, speed=0.0, transform=dict(PAN),
                time_mode="remap", time_remap_keys=[{"comp_frame": 790, "raw_seconds": 710.0},
                                                    {"comp_frame": 820, "raw_seconds": 710.0}])]
    kc, km = keep_speed(cl)
    s = {t.id: t for t in kc.segments}
    assert s[3].comp_out - s[4].comp_in == 20 == s[3].transition_out["duration_frames"]   # the overlap: S03's speed
    assert len(s[3].transition_out["alpha"]) == 20
    assert s[4].speed == 1.0 and s[4].comp_out - s[4].comp_in == pytest.approx(20 + 40 * 1.5, abs=1)
    assert s[5].comp_out - s[5].comp_in == 30                                       # a placeholder: its own length
    assert s[6].comp_out - s[6].comp_in == 30 and s[6].time_remap_keys                # a freeze stays a freeze
    assert [k["comp_frame"] for k in s[6].time_remap_keys] == [pytest.approx(s[6].comp_in),
                                                                pytest.approx(s[6].comp_out)]


def test_a_competitor_without_speed_changes_is_left_as_it_is():
    cl = cutlist()
    for sg in cl.segments:
        sg.speed = 1.0
    kc, km = keep_speed(cl)
    assert kc is cl and not km.stretched and km.map(500) == 500


def test_the_map_moves_a_caption_with_its_moment():
    km = keep_map(cutlist())
    assert km.map(0) == 0 and km.map(304) == 380 and km.map(568) == 710 and km.map(700) == 842
    assert km.map(152) == 190                                                       # halfway through S01
    assert Fraction(km.map(436)) == 380 + Fraction(132 * 5, 4)                      # halfway through S02
