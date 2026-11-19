"""19 Nov ("work on the notes that are left"): "XML ITEM V1 S49+S50+S51 raw.mp4 at 00:00:22:28: in / out 1602-1622
outside its media (0-1611)" -- The Worst Prank In The Office, 17 Nov. The competitor played 0.18 s on after the RAW's
last frame; two mini cuts there went into the clip before ("the clip before plays on"), which nobody stopped at the
end of the RAW. Premiere skips or misplaces a clip like that, and the piece for Topaz could never hold it: it was
sent 3 times ("1 clip(s) were not enhanced") and the clip stayed on the normal RAW.

Now the edit ends with the RAW's last frame: the frames past it are cut from the end, picture, sound, markers and the
captions' positions together (``past_media_frames`` in ``_cut_end_flash``)."""
from __future__ import annotations

import pytest

from match_cuts import export_xml_edl as ex, shots
from test_day13_editnotes import R24, _a1, _clip24, _framed, _shape


def _edit_running_past_the_raw():
    # S01 plays RAW 1000-1079, S02 the RAW's end: RAW 1602-1621 asked, but the RAW has 1611 frames
    x, y = _framed(_clip24(0, 80, 1000, 1), 0.0), _framed(_clip24(80, 100, 1602, 2), 40.0)
    return [x, y], _a1([x, y]), [{"in": 90, "out": 100, "name": "m"}]


def test_how_many_frames_the_last_clip_plays_past_the_end_of_the_raw():
    clips, _a, _m = _edit_running_past_the_raw()
    assert ex.past_media_frames(clips, 100, 1611) == 11
    assert ex.past_media_frames(clips, 100, 1622) == 0             # it ends with the RAW: nothing
    assert ex.past_media_frames(clips, 100, 5000) == 0
    assert ex.past_media_frames(clips, 100, None) == 0             # (the RAW's length not given: as before)
    assert ex.past_media_frames(clips, 120, 1611) == 0             # not the edit's last frames
    assert ex.past_media_frames(clips, 100, 1600) == 0             # the clip as a whole past the end: not this repair
    assert ex.past_media_frames([], 100, 1611) == 0


def test_the_edit_ends_with_the_raws_last_frame_picture_sound_markers_and_positions():
    clips, audio, markers = _edit_running_past_the_raw()
    pm = {i: i for i in range(101)}
    got, a1, mk, n, info = ex._cut_end_flash(clips, audio, markers, 100, pm, 100, R24, R24, None, 1611)
    assert n == 89 and _shape(got) == [(0, 80, 1000, 1080), (80, 89, 1602, 1611)]
    assert [(a["start"], a["end"], a["in"], a["out"]) for a in a1] == [(0, 80, 1000, 1080), (80, 89, 1602, 1611)]
    assert (mk[0]["in"], mk[0]["out"]) == (89, 89)
    assert info == {"frames": 11, "at": 89, "label": "S02", "was": 100, "past_media": 11}
    assert pm[100] == pm[95] == pm[89] == 89 and pm[88] == 88 and pm[50] == 50
    assert ex.past_media_frames(got, n, 1611) == 0
    line, = ex.edit_fix_lines([], info, R24)
    assert line.startswith("the edit ends 11 frames earlier (S02): its last clip would have played 11 frames past "
                           "the last frame of the RAW")


def test_without_the_raws_length_or_switched_off_nothing_changes(monkeypatch):
    clips, audio, markers = _edit_running_past_the_raw()
    got, a1, mk, n, info = ex._cut_end_flash(clips, audio, markers, 100, {}, 100, R24, R24, None)
    assert n == 100 and info is None and _shape(got) == _shape(clips) and a1 == audio
    monkeypatch.setattr(ex, "PAST_MEDIA_CUT", False)
    got, a1, mk, n, info = ex._cut_end_flash(clips, audio, markers, 100, {}, 100, R24, R24, None, 1611)
    assert n == 100 and info is None and _shape(got) == _shape(clips)


def test_an_edit_inside_its_raw_is_left_as_it_was():
    x, y = _framed(_clip24(0, 80, 1000, 1), 0.0), _framed(_clip24(80, 100, 1500, 2), 40.0)
    pm = {i: i for i in range(101)}
    got, a1, mk, n, info = ex._cut_end_flash([x, y], _a1([x, y]), [], 100, pm, 100, R24, R24, None, 1611)
    assert n == 100 and info is None and _shape(got) == _shape([x, y]) and pm == {i: i for i in range(101)}


def test_a_flash_of_the_next_shot_that_is_then_at_the_end_goes_too():
    # the RAW's last shot starts on RAW 1609: after the cut to the RAW's end, the edit's last 2 frames are a sliver
    # of it (the day-13 end flash) -- both go, and each is said in its own words
    clips, audio, markers = _edit_running_past_the_raw()
    changes = shots.seconds([900, 1500, 1609], R24)
    pm = {i: i for i in range(101)}
    assert ex.end_flash_frames(clips, 100, changes, R24) == 0      # (hidden behind the frames past the RAW)
    got, a1, mk, n, info = ex._cut_end_flash(clips, audio, markers, 100, pm, 100, R24, R24, changes, 1611)
    assert n == 87 and _shape(got)[-1] == (80, 87, 1602, 1609) and a1[-1]["end"] == 87
    assert info == {"frames": 13, "at": 87, "label": "S02", "was": 100, "past_media": 11} and pm[100] == 87
    lines = ex.edit_fix_lines([], info, R24)
    assert len(lines) == 2 and lines[0].startswith("the edit ends 11 frames earlier")
    assert lines[1].startswith("2 flash frames removed at 00:00:03:15 (S02): the edit's last 2 frames showed")


@pytest.mark.parametrize("speed, want", [(0.5, 2), (2.0, 16)])
def test_a_slowed_or_faster_last_clip_is_cut_by_its_own_count(speed, want):
    # 2x: asks RAW 1602-1642, 31 past the end -> 16 sequence frames; 0.5x: RAW 1602-1612, 1 past -> 2 sequence frames
    x = _framed(_clip24(0, 80, 1000, 1), 0.0)
    y = ex.dataclasses.replace(_framed(_clip24(80, 100, 1602, 2), 40.0), speed=speed,
                               src_out=1602 + int(round(20 * speed)))
    assert ex.past_media_frames([x, y], 100, 1611) == want
    got, _a, _m, n, info = ex._cut_end_flash([x, y], [], [], 100, {}, 100, R24, R24, None, 1611)
    assert n == 100 - want and got[-1].src_out <= 1611 and info["past_media"] == want
