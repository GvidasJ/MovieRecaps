"""18 Nov: the day-13 / run-18 fixes of 1_edit.xml on a sequence that stays at the planning rate (--sequence-fps 60).

The projects' notes (219 projects in D:\\Shared\\Projects): 50 x "1 frame(s) of a RAW shot at the framing of another
clip of it (0.017 s ...)", 35 x "n frame(s) of a different RAW shot", 52 x "the RAW skips N ms at the cut: one take,
cut". The fixes for exactly these (a sliver of the next RAW shot at the end cut off, a flash of one or two RAW frames
at a cut moved onto the RAW's shot change, a skipped frame of one take played again) were only made on a sequence at
the RAW's rate -- and since 7 Nov every video that gets Topaz is a 60 fps sequence, where none of them ran.
``write_premiere_xml(..., fix_plan_rate=True)`` makes them there too; pipeline.fix_at_plan_rate keeps the result only
when the edit's own checks find no more than before."""
from __future__ import annotations

from fractions import Fraction

from match_cuts import export_xml_edl as ex, pipeline, shots

from test_day13_editnotes import (F60, R24, V12_SHOTS, _a1, _clip24, _framed, _shape, config, video12_like)


def test_a_60_fps_sequence_ends_on_the_raws_shot_change_too(tmp_path):
    cl = video12_like()                                   # its last 6 ticks show the first frames of the next shot
    cfg = config(tmp_path, V12_SHOTS)
    base, final = tmp_path / "edit_60fps.xml", tmp_path / "1_edit.xml"
    r60 = ex.write_premiere_xml(cl, base, cfg)
    assert r60["end_cut"] is None and r60["pos_map"] == {}                # the checked edit: as it always was
    assert ex.premiere_flash_problems(base, V12_SHOTS, R24)               # ... with its flash (the note you got)
    res = ex.write_premiere_xml(cl, final, cfg, fix_plan_rate=True)
    k = res["end_cut"]["frames"]
    assert 4 <= k <= 7 and res["end_cut"]["at"] == 300 - k and res["fps"] == "60/1"       # 2 RAW frames of 60 fps
    xf = ex.parse_premiere_xml(final)
    assert xf["duration"] == 300 - k
    assert ex.premiere_flash_problems(final, V12_SHOTS, R24) == []        # the flash is gone
    assert ex.final_xml_problems(base, final, 1.0, V12_SHOTS, R24, end_cut=k) == []
    pm = {int(a): b for a, b in res["pos_map"].items()}
    assert pm[300] == 300 - k and pm[299] == 300 - k and pm[100] == 100   # a caption ending there ends on the new end
    assert res["edit_fixes"] and "flash frame" in res["edit_fixes"][0]
    # nothing to fix: the very same edit, and no map (the captions are written as before)
    calm = ex.write_premiere_xml(cl, tmp_path / "calm.xml", config(tmp_path, shots.seconds([900], R24)),
                                 fix_plan_rate=True)
    assert calm["pos_map"] == {} and calm["edit_fixes"] == [] and calm["frames"] == 300


def test_one_60_fps_frame_of_the_next_shot_at_the_clip_befores_framing_moves_onto_the_shot_change():
    """The note 50 of the projects' edits carry: one take, reframed at the competitor's cut (tick 100) -- but the
    RAW's next shot (frame 439 of a 23.976 fps RAW = 1098.6 ticks) begins one tick before it: tick 99 shows the new
    shot's first frame at S01's framing."""
    x, y = _framed(_clip24(0, 100, 1000, 1), 0.0), _framed(_clip24(100, 200, 1100, 2), 40.0)
    changes = shots.seconds([300, 439, 800], R24)
    before = ex._flash_lines([x, y], changes, F60, R24)
    assert before == ["S01 at 00:00:01:39: 1 frame(s) of a RAW shot at the framing of another clip of it (0.017 s, one "
                      "RAW frame or less) -- a flash frame"]
    pm = {t: t for t in range(201)}
    clips, audio, done = ex.fix_cut_flashes([x, y], _a1([x, y]), changes, F60, R24, pm,
                                            ex.plan_rate_flash_max(F60, R24))
    assert _shape(clips) == [(0, 99, 1000, 1099), (99, 200, 1099, 1200)]   # the cut one tick earlier, each on its line
    assert [(a["start"], a["end"], a["in"]) for a in audio] == [(0, 99, 1000), (99, 200, 1099)]   # A1 with its picture
    assert done == [{"clips": "S01|S02", "from": 100, "to": 99, "frames": 1}] and pm[100] == 99 and pm[101] == 101
    assert ex._flash_lines(clips, changes, F60, R24) == []
    assert ex.plan_rate_flash_max(F60, R24) == 5 and ex.plan_rate_flash_max(R24, R24) == 2
    assert ex.plan_rate_flash_max(F60, Fraction(30000, 1001)) == 4 and ex.plan_rate_flash_max(F60, None) == 2


def test_a_raw_frame_of_the_next_shot_before_a_cut_is_three_frames_of_a_60_fps_sequence_and_is_fixed_too():
    # S01's take runs 3 ticks (a good RAW frame) into its next shot before the cut to S02 (another place in the RAW):
    # more than the 2 frames a flash may be at the RAW's rate -- the limit counts RAW frames (5 ticks at 60 fps)
    x, y = _framed(_clip24(0, 100, 1000, 1), 0.0), _framed(_clip24(100, 200, 5000, 2), 40.0)
    changes = shots.seconds([300, 438, 1900, 2400], R24)                # frame 438 = 1096.1 ticks; S02: its own shot
    before = ex._flash_lines([x, y], changes, F60, R24)
    assert len(before) == 1 and "3 frame(s) of a different RAW shot" in before[0]
    assert ex.fix_cut_flashes([x, y], _a1([x, y]), changes, F60, R24, {}, 2)[2] == []     # the RAW-rate limit: left
    clips, _audio, done = ex.fix_cut_flashes([x, y], _a1([x, y]), changes, F60, R24, {},
                                             ex.plan_rate_flash_max(F60, R24))
    assert _shape(clips) == [(0, 97, 1000, 1097), (97, 200, 4997, 5100)] and done[0]["frames"] == 3
    assert ex._flash_lines(clips, changes, F60, R24) == []


def test_the_captions_follow_what_moved_on_a_60_fps_sequence():
    rows = [{"start_ms": 1000.0, "end_ms": 2000.0, "text": "one"}, {"start_ms": 2000.0, "end_ms": 4000.0, "text": "two"}]
    same = pipeline.final_srt(rows, 1.0, F60, F60, {})
    assert "00:00:01,000 --> 00:00:02,000" in same and "00:00:02,000 --> 00:00:04,000" in same
    pm = {p: p for p in range(301)}
    pm[120] = 117                                          # the cut the second caption starts on moved 3 frames
    moved = pipeline.final_srt(rows, 1.0, F60, F60, pm)
    assert "00:00:01,000 --> 00:00:01,950" in moved and "00:00:01,950 --> 00:00:04,000" in moved   # back to back


def test_only_a_sequence_asked_for_by_its_rate_is_fixed_at_the_planning_rate(tmp_path):
    assert not pipeline.plan_rate_fixes(config(tmp_path, []))                       # the default: the RAW's rate
    assert pipeline.plan_rate_fixes(config(tmp_path, [], sequence_fps="60"))        # the app, a video that gets Topaz
    assert Fraction(60) == F60
