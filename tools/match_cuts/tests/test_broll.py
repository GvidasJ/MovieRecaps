"""--no-broll (broll.py): cutaways over the main clip's continuing RAW audio are replaced by the main clip in the
export; cutaways over music stay and are listed. Synthetic audio only (no video): a speech-like RAW track, and a
competitor track that plays the main clip's RAW audio under three cutaways (B-roll from the RAW, a NOT-IN-RAW insert,
a 2-frame flash of B-roll) and music under a fourth -- 20 ms late, like a competitor with an A/V offset."""
from __future__ import annotations

import copy
import types
from fractions import Fraction

import numpy as np
import pytest

from match_cuts import broll, cli, report
from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.model import Cutlist, Segment

SR = 16000
FPS = Fraction(30)
DELAY = 0.020                                   # the competitor's audio is 20 ms late
PAN = {"scale": 0.546, "rotation_deg": 0.0, "tx": -246.7, "ty": 306.6}
BOX = {"x": 30.0, "y": 316.0, "w": 548.0, "h": 569.37, "corner_radius": 48.0}


def speechy(seconds: float, seed: int) -> np.ndarray:
    """Band-limited noise with a syllable-rate envelope: unique at every lag, like speech."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * SR))
    x = np.convolve(x, np.hanning(9), "same") - np.convolve(x, np.hanning(41), "same")
    t = np.arange(x.size) / SR
    env = 0.25 + 0.75 * np.abs(np.sin(2 * np.pi * 2.3 * t + rng.uniform(0, 6)))
    return (0.3 * x * env / np.std(x)).astype(np.float32)


RAW_Y = speechy(60.0, 1)
MUSIC = (0.2 * sum(np.sin(2 * np.pi * f * np.arange(int(2 * SR)) / SR) for f in (220.0, 277.2, 329.6))).astype(
    np.float32)


def main_line(t: np.ndarray) -> np.ndarray:
    """The competitor's audio: the main clip's RAW time at competitor time t (two shots: RAW 10 s and RAW 30 s)."""
    return np.where(t < 9.5, 10.0 + t, 30.0 + (t - 9.5))


def competitor_audio() -> np.ndarray:
    t = np.arange(int(12.0 * SR)) / SR
    pos = (main_line(t - DELAY) * SR).astype(int)
    y = RAW_Y[np.clip(pos, 0, RAW_Y.size - 1)].copy()
    m0, m1 = int(8.0 * SR), int(9.5 * SR)           # S06: music under the B-roll, the RAW audio stops
    y[m0:m1] = MUSIC[: m1 - m0]
    return y


def shot(id_, a, b, raw_in, **kw) -> Segment:
    """A main-clip shot: its audio follows its own picture (as the analysis measured it)."""
    au = {"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": 0.4, "corr": 0.95,
          "exception": None, "line": None}
    au.update(kw.pop("audio", {}))
    return Segment(id=id_, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw_in,
                   raw_in_frame=int(raw_in * 24000 / 1001), speed=1.0, transform=dict(PAN), confidence=0.97,
                   raw_in_interval=[raw_in - 0.0001, raw_in + 0.0001], audio=au, **kw)


def broll_shot(id_, a, b, raw_in) -> Segment:
    """B-roll from elsewhere in the RAW: its audio does not follow its picture."""
    return Segment(id=id_, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw_in,
                   raw_in_frame=int(raw_in * 24000 / 1001), speed=1.0, transform={**PAN, "scale": 0.7},
                   confidence=0.95, audio={"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None,
                                           "lag_ms": None, "corr": 0.12, "exception": "replaced", "line": None})


def make_cutlist() -> Cutlist:
    segs = [
        shot(1, 0, 90, 10.0, audio={"out_offset_frames": 2}),        # main clip (a J/L edge into the cutaway)
        broll_shot(2, 90, 135, 40.0),                                  # B-roll from the RAW, audio continues
        shot(3, 135, 180, 14.5),                                       # main clip again, same line
        Segment(id=4, type="not_in_raw", comp_in=180, comp_out=210, label="MISSING - not in RAW"),   # insert
        shot(5, 210, 240, 17.0),
        broll_shot(6, 240, 285, 50.0),                                 # B-roll over MUSIC: kept
        shot(7, 285, 330, 30.0),                                       # a new main-clip shot (RAW 30 s)
        broll_shot(8, 330, 332, 45.0),                                 # 2-frame flash: too short to hear
        shot(9, 332, 360, 31.5667),                                    # same line as S07
    ]
    comp = {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "30/1", "frames": 360}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080,
           "fps": "24000/1001", "frames": 1438, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed", "box": dict(BOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


@pytest.fixture(scope="module")
def result() -> dict:
    cl = make_cutlist()
    before = copy.deepcopy(cl.to_dict())
    res = broll.apply_no_broll(cl, competitor_audio(), RAW_Y, SR, Config())
    res["faithful"], res["before"] = cl, before
    return res


def test_cutaways_over_continuing_raw_audio_are_replaced_and_music_is_kept(result):
    assert [r["segment"] for r in result["replaced"]] == [2, 4, 8]
    assert [r["segment"] for r in result["kept"]] == [6]
    assert "not the main clip's RAW audio" in result["kept"][0]["why"] and result["kept"][0]["corr"] < 0.8
    by = {r["segment"]: r for r in result["replaced"]}
    assert by[2]["corr"] >= 0.8 and abs(by[2]["lag_ms"]) <= 10 and by[4]["corr"] >= 0.8
    assert by[8]["bridged"]                             # 2 frames: bridged between two shots of the same line
    assert abs(by[2]["raw_in_seconds"] - 13.0) < 1e-6 and abs(by[4]["raw_in_seconds"] - 16.0) < 1e-6
    assert abs(by[8]["raw_in_seconds"] - 31.5) < 1e-6
    assert by[2]["showed"] == "RAW 40.000s" and by[4]["showed"] == "NOT-IN-RAW insert"


def test_the_main_clip_plays_through_as_one_clip(result):
    segs = result["cutlist"].segments
    assert [(s.id, s.comp_in, s.comp_out) for s in segs] == [(1, 0, 240), (6, 240, 285), (7, 285, 360)]
    s1 = segs[0]
    assert s1.type == "raw" and s1.raw_in_seconds == 10.0 and s1.speed == 1.0 and s1.transform == PAN
    assert s1.audio["out_offset_frames"] == 0           # the J/L edge into the cutaway is gone: no cut there any more
    assert [r[2] for r in s1.audio["broll"]["ranges"]] == [2, 4]
    assert segs[1].raw_in_seconds == 50.0 and segs[1].transform["scale"] == 0.7     # kept exactly
    assert segs[2].audio["broll"]["ranges"] == [[330, 332, 8]]


def test_the_faithful_cutlist_is_untouched(result):
    assert result["faithful"].to_dict() == result["before"]


def test_premiere_xml_of_the_export_validates_and_marks_every_replaced_spot(result, tmp_path):
    cfg = Config(out_dir=str(tmp_path), premiere=True, no_broll=True)
    xml, edl = tmp_path / "recreated_edit.xml", tmp_path / "recreated_edit.edl"
    ex.write_premiere_xml(result["cutlist"], xml, cfg)
    ex.write_edl(result["cutlist"], edl, cfg)
    v = ex.validate_premiere_exports(result["cutlist"], xml, edl, cfg)
    assert v["ok"], v["errors"]
    x = ex.parse_premiere_xml(xml)
    ms = {m["name"]: (m["in"], m["out"]) for m in x["markers"] if m["name"].startswith("B-ROLL")}
    assert ms == {"B-ROLL REPLACED S02": (180, 270), "B-ROLL REPLACED S04": (360, 420),
                  "B-ROLL REPLACED S08": (660, 664)}
    starts = sorted(c["start"] for c in x["clips"])
    assert starts == [0, 480, 570]                       # three V1 clips: main clip, kept B-roll, second shot


def test_report_lists_every_replaced_and_kept_cutaway_with_timecodes(result):
    ctx = types.SimpleNamespace(cfg=Config(no_broll=True, premiere=True), cutlist=result["faithful"], broll=result)
    md = "\n".join(report._broll(ctx))
    assert "3 cutaway(s) replaced" in md and "1 kept" in md
    assert "| S02 | 90–135 | 00:00:03:00–00:00:04:15 | 00:00:03:00–00:00:04:30 | RAW 40.000s |" in md
    assert "| S04 | 180–210 |" in md and "| S08 | 330–332 |" in md
    assert "| S06 | 240–285 | 00:00:08:00–00:00:09:15 |" in md and "music / voice-over" in md
    off = types.SimpleNamespace(cfg=Config(), cutlist=result["faithful"], broll={})
    assert "Not used" in report._broll(off)[0]


def test_no_audio_or_no_cutaways_changes_nothing():
    cl = make_cutlist()
    res = broll.apply_no_broll(cl, None, None, SR, Config())
    assert not res["replaced"] and [s.id for s in res["cutlist"].segments] == [s.id for s in cl.segments]
    assert all("could not be checked" in r["why"] for r in res["kept"])
    plain = Cutlist(1, cl.competitor, cl.raw, cl.layout, [shot(1, 0, 180, 10.0), shot(2, 180, 360, 16.0)])
    res = broll.apply_no_broll(plain, competitor_audio(), RAW_Y, SR, Config())
    assert not res["replaced"] and not res["kept"]


def test_flag_and_config():
    args = cli.build_parser().parse_args(["--no-broll"])
    cfg = cli.config_from_args(args, "c.mp4", "r.mp4")
    assert cfg.no_broll is True and cli.config_from_args(cli.build_parser().parse_args([]), "c", "r").no_broll is False
    assert "no_broll" not in cfg.analysis_params()      # an export option: never recomputes the analysis
