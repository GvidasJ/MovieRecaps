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


# ---- --premiere default: B-roll always follows the audio -----------------------------------------------------------

@pytest.fixture(scope="module")
def follow() -> dict:
    cl = make_cutlist()
    res = broll.apply_no_broll(cl, competitor_audio(), RAW_Y, SR, Config(premiere=True), follow_audio=True)
    res["faithful"] = cl
    return res


def test_follow_audio_fills_every_spot_and_lets_the_previous_clip_play_over_music(follow):
    segs = follow["cutlist"].segments
    assert not [s for s in segs if s.type != "raw"]                     # V1 is never left empty
    rows = {r["segment"]: r for r in follow["replaced"]}
    assert set(rows) == {2, 4, 6, 8} and not follow["kept"]
    assert rows[2]["how"] == "audio" and rows[4]["how"] == "audio"      # the RAW of the audio heard there
    # S06: B-roll over music -> S05 keeps playing (RAW 18.0 s at 8.0 s), with no RAW audio under it
    s6 = next(s for s in segs if s.comp_in == 240)
    assert rows[6]["how"] == "keeps playing" and abs(s6.raw_in_seconds - 18.0) < 1e-6 and s6.audio.get("mute")
    assert s6.comp_out == 285


def test_follow_audio_premiere_xml_marks_each_spot_and_a1_plays_the_picture_under_music(follow, tmp_path):
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml, edl = tmp_path / "recreated_edit.xml", tmp_path / "recreated_edit.edl"
    ex.write_premiere_xml(follow["cutlist"], xml, cfg)
    ex.write_edl(follow["cutlist"], edl, cfg)
    v = ex.validate_premiere_exports(follow["cutlist"], xml, edl, cfg)
    assert v["ok"], v["errors"]
    x = ex.parse_premiere_xml(xml)
    ms = {m["name"]: m for m in x["markers"] if m["name"].startswith("B-ROLL")}
    assert set(ms) == {"B-ROLL REPLACED S02", "B-ROLL REPLACED S04", "B-ROLL REPLACED S06", "B-ROLL REPLACED S08"}
    assert "music / voice-over" in ms["B-ROLL REPLACED S06"]["comment"]
    assert (ms["B-ROLL REPLACED S06"]["in"], ms["B-ROLL REPLACED S06"]["out"]) == (480, 570)
    # A1 under the music: the picture's own RAW sound at 0 dB (every audio clip has sound; --audio-lines: silent)
    under = [a for a in x["audio"] if a["start"] < 570 and a["end"] > 480]
    assert under and all(not a.get("levels") for a in under)
    for a in under:
        c = next(c for c in x["clips"] if c["start"] <= a["start"] and a["end"] <= c["end"])
        assert a["in"] == c["in"] + (a["start"] - c["start"])
    cfg2 = Config(out_dir=str(tmp_path), premiere=True, premiere_normal_audio=False)
    ex.write_premiere_xml(follow["cutlist"], xml, cfg2)
    x2 = ex.parse_premiere_xml(xml)
    assert not [a for a in x2["audio"] if a["start"] < 570 and a["end"] > 480]      # --audio-lines: muted
    v1 = sorted((c["start"], c["end"]) for c in x["clips"])
    assert v1[0][0] == 0 and v1[-1][1] == 720 and all(a[1] == b[0] for a, b in zip(v1, v1[1:]))   # no V1 gap


def _hints(spans: list[tuple[float, float, float]]):
    """AudioHints whose confident windows put competitor time t at RAW t + offset over each (t0, t1, offset)."""
    from match_cuts.model import AudioHints
    ct = np.arange(0.5, 12.0, 0.25)
    rt = np.full(ct.shape, np.nan)
    for t0, t1, off in spans:
        m = (ct >= t0) & (ct < t1)
        rt[m] = ct[m] + off
    n = ct.size
    return AudioHints(ct, rt, np.ones(n), np.where(np.isfinite(rt), 2.0, 0.5).astype(np.float32),
                      np.full(n, 3.0, np.float32), np.full(n, 0.9, np.float32))


def test_a_cutaway_over_trimmed_audio_shows_each_raw_moment_heard():
    # S02 is a NOT-IN-RAW insert over speech the editor cut together from two RAW moments (40.0 s, then 47.0 s)
    segs = [shot(1, 0, 60, 10.0), Segment(id=2, type="not_in_raw", comp_in=60, comp_out=150, label="MISSING"),
            shot(3, 150, 240, 15.0)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    t = np.arange(int(8.0 * SR)) / SR
    pos = np.where(t < 2.0, 10.0 + t, np.where(t < 3.5, 38.0 + t, np.where(t < 5.0, 43.5 + t, 10.0 + t)))
    comp = RAW_Y[np.clip((pos * SR).astype(int), 0, RAW_Y.size - 1)]
    hints = _hints([(0.0, 2.0, 10.0), (2.0, 3.5, 38.0), (3.5, 5.0, 43.5), (5.0, 8.0, 10.0)])
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True, hints=hints)
    row = res["replaced"][0]
    assert row["segment"] == 2 and row["how"] == "audio"
    parts = [(p["comp_in"], p["comp_out"], round(p["raw_in_seconds"], 2)) for p in row["parts"]]
    assert parts == [(60, 105, 40.0), (105, 150, 47.0)]                # the audio cut, frame-exact
    assert all(p["corr"] > 0.9 for p in row["parts"])
    new = [s for s in res["cutlist"].segments if 60 <= s.comp_in < 150]
    assert len(new) == 2 and all(s.transform == PAN for s in new)       # framed like the clip before


def test_main_clip_shots_with_an_av_shift_and_one_frame_glitches():
    # S02: the main clip at a different moment whose sound the analysis did not explain (it is 50 ms late in the
    # competitor) -- not B-roll; S03: a 1-frame uncertain glitch -- the previous clip plays on, with its audio
    segs = [shot(1, 0, 60, 10.0), broll_shot(2, 60, 150, 20.0), Segment(id=3, type="uncertain", comp_in=150,
                                                                        comp_out=151), shot(4, 151, 240, 23.0333)]
    segs[1].transform = dict(PAN)
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    t = np.arange(int(8.0 * SR)) / SR
    pos = np.where(t < 2.0, 10.0 + t, np.where(t < 5.0, 18.0 + t - 0.05, 18.0 + t))
    comp = RAW_Y[np.clip((pos * SR).astype(int), 0, RAW_Y.size - 1)]
    hints = _hints([(0.0, 2.0, 10.0), (2.0, 5.0, 17.95), (5.0, 8.0, 18.0)])
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True, hints=hints)
    assert [r["segment"] for r in res["replaced"]] == [3]
    assert res["replaced"][0]["how"] == "keeps playing (short)"
    s2 = next(s for s in res["cutlist"].segments if s.comp_in == 60)
    assert s2.raw_in_seconds == 20.0 and s2.transform == PAN             # S02 untouched ...
    assert s2.comp_out == 240 and not (s2.audio or {}).get("mute")        # ... and plays on through the glitch into S04
    assert s2.audio["broll"]["ranges"] == [[150, 151, 3, "keeps playing (short)"]]


def test_dips_are_filled_too():
    segs = [shot(1, 0, 60, 10.0), Segment(id=2, type="dip", comp_in=60, comp_out=66, color="#000000"),
            shot(3, 66, 120, 12.2)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    res = broll.apply_no_broll(cl, None, None, SR, Config(premiere=True), follow_audio=True)
    assert all(s.type == "raw" for s in res["cutlist"].segments)
    assert res["replaced"][0]["segment"] == 2


def _comp_from(spans: list[tuple[float, float, float]], seconds: float = 8.0) -> np.ndarray:
    """The competitor's audio: RAW t + offset over each (t0, t1, offset)."""
    t = np.arange(int(seconds * SR)) / SR
    pos = np.full(t.shape, np.nan)
    for t0, t1, off in spans:
        m = (t >= t0) & (t < t1)
        pos[m] = t[m] + off
    return RAW_Y[np.clip((np.nan_to_num(pos) * SR).astype(int), 0, RAW_Y.size - 1)]


def test_a_piece_too_short_to_hear_between_two_cutaways_on_one_line_follows_that_line():
    """video4: two cutaways over the main clip's continuing speech, and between them 2 frames of another RAW moment
    whose sound could not be measured -- they follow the same line (as between two shots of one line), not a blip of
    another moment's sound inside the speech."""
    short = Segment(id=3, type="raw", comp_in=90, comp_out=92, raw_in_seconds=45.0, raw_in_frame=1078, speed=1.0,
                    transform=dict(PAN), confidence=0.9, audio={"in_offset_frames": 0, "out_offset_frames": 0,
                                                                "pitch_preserved": None, "lag_ms": None, "corr": None,
                                                                "exception": None, "line": None})
    segs = [shot(1, 0, 60, 10.0), broll_shot(2, 60, 90, 40.0), short, broll_shot(4, 92, 120, 42.0),
            shot(5, 120, 240, 50.0)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    comp = _comp_from([(0.0, 4.0, 10.0), (4.0, 8.0, 46.0)])
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)
    rows = {r["segment"]: r for r in res["replaced"]}
    assert sorted(rows) == [2, 3, 4] and not res["kept"]
    assert {r["line"] for r in rows.values()} == {"S01 continued"}
    assert rows[3]["bridged"] and not rows[2]["bridged"] and not rows[4]["bridged"]
    first = res["cutlist"].segments[0]
    assert first.comp_out == 120 and first.raw_in_seconds == 10.0      # one clip: S01 plays through to S05


def test_a_main_clip_shot_whose_own_sound_is_a_little_off_its_picture_is_not_replaced():
    """video4's S27: a jump to another moment of the interview whose own sound is 22 ms off its picture -- too far
    for an anchor (10 ms), but its sound is its own: the main clip, never replaced by the shot before it playing on."""
    segs = [shot(1, 0, 60, 10.0), shot(2, 60, 120, 20.0, audio={"lag_ms": 22.0, "corr": 0.95}),
            shot(3, 120, 240, 30.0)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    comp = _comp_from([(0.0, 2.0, 10.0), (2.0, 4.0, 18.0 - 0.022), (4.0, 8.0, 26.0)])
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)
    assert not res["replaced"]
    s2 = next(s for s in res["cutlist"].segments if s.comp_in == 60)
    assert s2.raw_in_seconds == 20.0 and not (s2.audio or {}).get("broll")


def _on_line(id_, a, b, raw_in, line_id, line_raw_in, source):
    """A RAW piece whose sound the audio stage (FX-14) found on a line: the line's corr / lag are copied into its own
    audio fields, as audio_align._audio_lines does."""
    s = broll_shot(id_, a, b, raw_in)
    s.audio = dict(s.audio, corr=0.986, lag_ms=0.0, exception=None,
                   line={"id": line_id, "raw_in_seconds": line_raw_in, "speed": 1.0, "source": source, "lag_ms": 0.0,
                         "corr": 0.986})
    return s


def test_short_pieces_whose_sound_follows_another_clips_line_are_filled_from_it():
    """Task 10 (video4 on the full-size files): right after the main clip, 1 and 4 frames of other RAW moments under
    its continuing speech. The audio stage put them on the main clip's line ("S01 continued (bridged)", corr 0.986,
    lag 0, copied into their own audio fields) and the "its own sound is its picture's RAW" test read those numbers
    as theirs: both were kept -- two flash frames in 1_edit.xml, the export's hard check failed. Their sound is the
    line's: they show its RAW video, one clip through the cutaway after them."""
    segs = [shot(1, 0, 60, 10.0), _on_line(2, 60, 61, 45.0, 0, 12.0, "S01 continued (bridged)"),
            _on_line(3, 61, 65, 50.0, 0, 10.0 + 61 / 30, "S01 continued (bridged)"), broll_shot(4, 65, 120, 40.0),
            shot(5, 120, 240, 30.0)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    comp = _comp_from([(0.0, 4.0, 10.0), (4.0, 8.0, 26.0)])      # S01's speech runs on to 4 s, then S05 (RAW 30 s)
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)
    rows = {r["segment"]: r for r in res["replaced"]}
    assert {2, 3, 4} <= set(rows) and not res["kept"], rows
    first = res["cutlist"].segments[0]
    assert first.comp_out == 120 and first.raw_in_seconds == 10.0      # one clip: S01 plays through, no flash frame


def test_a_piece_on_its_own_in_point_line_keeps_its_own_sound():
    """The other side of that fix: a piece whose line starts at itself (the audio stage's "own in-point at speed 1",
    a video-only slow motion over its own sound) has its own sound -- left as the competitor has it."""
    slow = _on_line(2, 60, 90, 40.0, 60, 40.0, "own in-point at speed 1")
    slow.speed, slow.retime = 0.5, "constant"
    segs = [shot(1, 0, 60, 10.0), slow, shot(3, 90, 240, 30.0)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    comp = _comp_from([(0.0, 2.0, 10.0), (2.0, 3.0, 38.0), (3.0, 8.0, 27.0)])
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)
    assert 2 not in {r["segment"] for r in res["replaced"]}
    s2 = next(s for s in res["cutlist"].segments if s.comp_in == 60)
    assert s2.raw_in_seconds == 40.0 and s2.speed == 0.5


def test_a_piece_whose_sound_continues_the_clip_before_plays_at_its_sounds_time():
    """Task 10 (your video1, final.mp4): S09's sound continues S08's line, its picture ran 3 frames behind that line
    -- no cutaway (within JUMP_FRAMES), so it was left as it was: V1 repeated those frames at the cut while A1 played
    on, and the repeat removal then cut A1 inside a word. With --premiere's follow-the-audio it plays at the RAW time
    of its sound, its own framing kept: one clip with the shot before, nothing repeated, no B-roll marker."""
    line = {"id": 0, "raw_in_seconds": 12.0, "speed": 1.0, "source": "S01 continued", "lag_ms": 0.0, "corr": 0.994}
    piece = shot(2, 60, 120, 12.0 - 0.05, audio={"corr": 0.994, "lag_ms": 0.0, "line": line})
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, [shot(1, 0, 60, 10.0), piece, shot(3, 120, 240, 30.0)])
    comp = _comp_from([(0.0, 4.0, 10.0), (4.0, 8.0, 26.0)])      # S01's sound runs on to 4 s, then S03 (RAW 30 s)
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)
    assert [r["segment"] for r in res["slipped"]] == [2] and res["slipped"][0]["picture_ms"] == -50.0
    assert not res["replaced"] and not res["kept"]
    first = res["cutlist"].segments[0]
    assert first.comp_out == 120 and first.raw_in_seconds == 10.0          # one clip on S01's line
    assert not ((first.audio or {}).get("broll") or {}).get("ranges")      # no B-ROLL REPLACED marker
    assert next(s for s in cl.segments if s.comp_in == 60).raw_in_seconds == 11.95     # the input is untouched
    # without follow-the-audio nothing moves; nor does a piece whose line starts at itself (its own sound)
    plain = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(), follow_audio=False)
    assert not plain["slipped"] and next(s for s in plain["cutlist"].segments if s.comp_in == 60).raw_in_seconds == 11.95
    own = copy.deepcopy(piece)
    own.audio["line"] = dict(line, id=60, source="own in-point at speed 1")
    cl2 = Cutlist(1, base.competitor, base.raw, base.layout, [shot(1, 0, 60, 10.0), own, shot(3, 120, 240, 30.0)])
    assert not broll.apply_no_broll(cl2, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)["slipped"]


def test_a_raw_piece_too_short_to_be_a_shot_is_never_left_as_a_flash():
    """video4: 4 frames of another RAW moment at the end of the competitor's rewind effect, their sound too short to
    measure: left as they are they would be a flash frame (shorter than shots.MIN_SHOT_S, a hard failure of the
    export) -- the clip before plays on over them, as over a flash."""
    tiny = Segment(id=3, type="raw", comp_in=120, comp_out=124, raw_in_seconds=45.0, raw_in_frame=1078, speed=1.0,
                   transform=dict(PAN), confidence=0.9, audio={"in_offset_frames": 0, "out_offset_frames": 0,
                                                               "pitch_preserved": None, "lag_ms": None, "corr": None,
                                                               "exception": None, "line": None})
    segs = [shot(1, 0, 60, 10.0), broll_shot(2, 60, 120, 40.0), tiny, shot(4, 124, 240, 30.0)]
    base = make_cutlist()
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    t = np.arange(int(8.0 * SR)) / SR
    comp = RAW_Y[np.clip(((10.0 + t) * SR).astype(int), 0, RAW_Y.size - 1)].copy()
    comp[int(2.0 * SR):int(124 / 30 * SR)] = np.resize(MUSIC, int(124 / 30 * SR) - int(2.0 * SR))
    k = int(124 / 30 * SR)
    comp[k:] = RAW_Y[np.clip(((30.0 + t[k:] - 124 / 30) * SR).astype(int), 0, RAW_Y.size - 1)]
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True)
    rows = {r["segment"]: r for r in res["replaced"]}
    assert 3 in rows and rows[3]["how"] == "keeps playing (short)" and not res["kept"]


def test_pieces_under_another_clips_sound_with_no_shot_beside_them_play_at_their_sounds_time():
    """video018's ending: S20 dissolves into S21, reframed on the other person 0.1 s back on the same take, then S22
    0.3 s back, under S20's sound ("Yeah I have insurance"); the audio stage put them on S20's line. The dissolve
    keeps S20 from being the shot right before them and the shot after them (S23, its own sound under music: corr
    0.59) is no anchor either -- no main-clip shot beside them, so they kept their pictures: V1 repeated 0.3 s of the
    take while A1 played on, and the repeat removal cut "insurance". Within BROLL_GAP_S of their sound they are the
    same take, no cutaway: they play at the RAW time of their sound, each its own framing kept (you cut that line in
    sync, on that framing). A piece over 1 s off its line is not the same take: never slipped."""
    xf = {"type": "crossfade", "duration_frames": 2}
    line = {"id": 0, "raw_in_seconds": 0.0, "speed": 1.0, "source": "S01 continued", "lag_ms": 0.0, "corr": 0.86}
    other = {**PAN, "tx": PAN["tx"] + 300.0, "scale": 0.6}

    def piece(id_, a, b, back_s):
        at = 10.0 + a / 30
        s = shot(id_, a, b, at - back_s, transition_in=dict(xf) if a == 58 else None,
                 audio={"corr": 0.86, "lag_ms": 0.0, "line": dict(line, raw_in_seconds=at)})
        s.transform = dict(other)
        return s
    base = make_cutlist()
    s1 = shot(1, 0, 60, 10.0, transition_out=dict(xf))
    after = shot(4, 100, 240, 30.0, audio={"corr": 0.59, "lag_ms": 1.3})       # its own sound, under music
    segs = [s1, piece(2, 58, 80, 0.1), piece(3, 80, 100, 0.3), after]
    cl = Cutlist(1, base.competitor, base.raw, base.layout, segs)
    comp = _comp_from([(0.0, 100 / 30, 10.0), (100 / 30, 8.0, 30.0 - 100 / 30)])
    res = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True, hints=None)
    assert sorted(r["segment"] for r in res["slipped"]) == [2, 3], res
    assert not [r for r in res["replaced"] if r["segment"] in (2, 3)]
    out = {s.comp_in: s for s in res["cutlist"].segments}
    # one clip on S01's line from 58 to 100 (the two pieces, one framing: joined), its own framing
    assert out[58].comp_out == 100 and out[58].raw_in_seconds == pytest.approx(10.0 + 58 / 30)
    assert out[58].transform == other and out[0].transform == PAN
    assert out[100].raw_in_seconds == 30.0                                     # the real cut after them: untouched
    assert next(s for s in cl.segments if s.comp_in == 80).raw_in_seconds == pytest.approx(10.0 + 80 / 30 - 0.3)
    # over BROLL_GAP_S off its sound: not the same take
    far = Cutlist(1, base.competitor, base.raw, base.layout,
                  [copy.deepcopy(s1), piece(2, 58, 80, 1.5), shot(4, 80, 240, 30.0 - 20 / 30, audio={"corr": 0.59})])
    res2 = broll.apply_no_broll(far, comp, RAW_Y, SR, Config(premiere=True), follow_audio=True, hints=None)
    assert 2 not in [r["segment"] for r in res2["slipped"]]
    # without follow-the-audio nothing moves
    plain = broll.apply_no_broll(cl, comp, RAW_Y, SR, Config(), follow_audio=False)
    assert not plain["slipped"]
    # the own in-point line of a sped-up piece is no take playing on (zendaya: S20-S23 after S19's 1.2x speed-up --
    # slipped, an A1 cut fell inside "funny it's"): not slipped
    fast = copy.deepcopy(s1)
    fast.speed = 1.2
    res3 = broll.apply_no_broll(Cutlist(1, base.competitor, base.raw, base.layout, [fast] + segs[1:]), comp, RAW_Y,
                                SR, Config(premiere=True), follow_audio=True, hints=None)
    assert not [r for r in res3["slipped"] if r["segment"] in (2, 3)]
