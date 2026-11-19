"""19 Nov ("work on the notes that are left ... no flash frames"): the commonest note left in the projects is
"XML FLASH ...: N frame(s) of a different RAW shot" (50 in the 219 projects, half of them 5 frames of a 60 fps
sequence or less). The day-13 fix moves such a cut onto the RAW's shot change -- but since the clips are cut at the
RAW's shot changes (split_at_shots), the frames a take ran past its own shot are a clip of their OWN, and a cut was
never moved so far that a clip is left without a frame: exactly these flashes were never taken (The Best Business
Idea In Key and Peele (2), S21 at 00:00:06:58: 2 frames, left as a note on 17, 18 and 19 Nov).

``_swallowed_cut``: the cut may move all the way over the flash's own clip. The rules stay: at most CUT_FLASH_MAX
RAW frames (3 and more are the competitor's own short shot), only when the edit's flash check then finds fewer
problems and nothing new, no RAW frame played twice, and A1 moves with its picture."""
from __future__ import annotations

from fractions import Fraction

import pytest

from match_cuts import export_xml_edl as ex, shots
from test_day13_editnotes import R24, _a1, _clip24, _framed, _shape


def _take_split_at_its_shot_change():
    # S01 plays RAW 1000-1049 and its next shot starts on RAW 1048: the scene split made its last 2 frames a clip
    # of their own (48-50); then S02 (RAW 5000-). One sound item under the whole take, as the tool writes it.
    x1, x2 = _framed(_clip24(0, 48, 1000, 1), 0.0), _framed(_clip24(48, 50, 1048, 1), 0.0)
    y = _framed(_clip24(50, 100, 5000, 2), 40.0)
    audio = _a1([_clip24(0, 50, 1000, 1), y])
    return [x1, x2, y], audio, shots.seconds([900, 1048, 4900, 6000], R24)


def test_a_flash_that_is_a_clip_of_its_own_was_never_taken(monkeypatch):
    clips, audio, changes = _take_split_at_its_shot_change()
    assert len(ex._flash_lines(clips, changes, R24, R24)) == 1                # the note
    monkeypatch.setattr(ex, "SWALLOW_FLASH_CLIP", False)                      # (as it was until 19 Nov)
    got, _a, done = ex.fix_cut_flashes(clips, audio, changes, R24, R24)
    assert done == [] and _shape(got) == _shape(clips)


def test_the_flash_clip_goes_and_the_next_clip_starts_on_the_raws_shot_change():
    clips, audio, changes = _take_split_at_its_shot_change()
    pm = {125: 50}
    got, a1, done = ex.fix_cut_flashes(clips, audio, changes, R24, R24, pm)
    assert _shape(got) == [(0, 48, 1000, 1048), (48, 100, 4998, 5050)]        # S02 starts 2 frames earlier, on its line
    assert [(a["start"], a["end"], a["in"]) for a in a1] == [(0, 48, 1000), (48, 100, 4998)]   # A1 with its picture
    assert done == [{"clips": "S01|S02", "from": 50, "to": 48, "frames": 2}] and pm == {125: 48}
    assert ex._flash_lines(got, changes, R24, R24) == []
    assert [c.label for c in got] == ["S01", "S02"]
    line = ex.edit_fix_lines([], None, R24, None, done)[0]
    assert line.startswith("2 flash frames removed at 00:00:02:00 (S01|S02): the cut moved 2 frames earlier")


def test_a_clip_whose_first_frames_are_the_shot_before_loses_them_to_the_clip_before():
    # S02 starts on RAW 4998, its shot on 5000: the split made 50-52 a clip of its own
    x = _framed(_clip24(0, 50, 1000, 1), 0.0)
    y1, y2 = _framed(_clip24(50, 52, 4998, 2), 40.0), _framed(_clip24(52, 100, 5000, 2), 40.0)
    changes = shots.seconds([900, 3000, 5000, 6000], R24)
    audio = _a1([x, _clip24(50, 100, 4998, 2)])
    assert len(ex._flash_lines([x, y1, y2], changes, R24, R24)) == 1
    got, a1, done = ex.fix_cut_flashes([x, y1, y2], audio, changes, R24, R24)
    assert _shape(got) == [(0, 52, 1000, 1052), (52, 100, 5000, 5048)]        # S01 plays 2 more of its own shot
    assert [(a["start"], a["end"], a["in"]) for a in a1] == [(0, 52, 1000), (52, 100, 5000)]
    assert done and done[0]["to"] == 52 and ex._flash_lines(got, changes, R24, R24) == []


def test_three_raw_frames_are_the_competitors_own_short_shot_and_stay():
    x1, x2 = _framed(_clip24(0, 47, 1000, 1), 0.0), _framed(_clip24(47, 50, 1047, 1), 0.0)
    y = _framed(_clip24(50, 100, 5000, 2), 40.0)
    changes = shots.seconds([900, 1047, 4900, 6000], R24)
    audio = _a1([_clip24(0, 50, 1000, 1), y])
    assert len(ex._flash_lines([x1, x2, y], changes, R24, R24)) == 1          # still said, for you to look at
    got, _a, done = ex.fix_cut_flashes([x1, x2, y], audio, changes, R24, R24)
    assert done == [] and _shape(got) == _shape([x1, x2, y])


def test_nothing_moves_when_the_next_clip_cannot_start_earlier_or_frames_would_play_twice():
    clips, audio, _ = _take_split_at_its_shot_change()
    # S02's own shot starts on its first frame: starting earlier would only make another flash
    tight = shots.seconds([900, 1048, 5000, 6000], R24)
    got, _a, done = ex.fix_cut_flashes(clips, audio, tight, R24, R24)
    assert done == [] and _shape(got) == _shape(clips)
    # the 2 frames S02 would start earlier on are played by another clip (S03 ends on them): no frame twice
    x1, x2, y = clips
    z = _framed(_clip24(100, 150, 4950, 3), 80.0)
    changes = shots.seconds([900, 1048, 4900, 6000], R24)
    got, _a, done = ex.fix_cut_flashes([x1, x2, y, z], audio + _a1([z]), changes, R24, R24)
    assert done == [] and _shape(got) == _shape([x1, x2, y, z])


def test_a_sound_cut_that_cannot_move_with_its_picture_keeps_the_cut():
    clips, audio, changes = _take_split_at_its_shot_change()
    line = [dict(a, what="audio line") for a in audio]                        # A1 plays another moment of the RAW
    assert ex.fix_cut_flashes(clips, line, changes, R24, R24)[2] == []


def test_a_retimed_or_slowed_clip_is_not_swallowed():
    clips, audio, changes = _take_split_at_its_shot_change()
    x1, x2, y = clips
    slow = ex.dataclasses.replace(y, speed=0.5)
    assert ex._swallowed_cut([x1, x2, slow], 1, 48) is None
    assert ex._swallowed_cut([x1, x2, y], 1, 49) is None                      # (not all the way: _moved_cut's job)
    grown = ex._swallowed_cut([x1, x2, y], 1, 48)
    assert grown is not None and grown[1] == 1 and grown[2] is x2 and _shape(grown[0]) == [(0, 48, 1000, 1048), (48, 100, 4998, 5050)]
    early = _framed(_clip24(50, 100, 1, 2), 40.0)                             # 2 frames before the RAW's start: no
    assert ex._swallowed_cut([x1, x2, early], 1, 48) is None


@pytest.mark.parametrize("n", [1, 2])
def test_on_a_60_fps_sequence_a_flash_of_under_one_raw_frame_goes_too(n):
    """The Best Business Idea (2), 60 fps over a 23.976 fps RAW: clip S21 380-418, its 2 frames 418-420 after the
    RAW's shot change, then a clip from another place of the RAW."""
    f60 = Fraction(60)
    x1, x2 = _framed(_clip24(380, 420 - n, 3088, 21), 0.0), _framed(_clip24(420 - n, 420, 3128 - n, 21), 0.0)
    y = _framed(_clip24(420, 548, 5733, 22), 40.0)
    changes = [40.0, (3128 - n) / 60.0, 90.0, 120.0]
    audio = _a1([_clip24(380, 420, 3088, 21), y])
    k = ex.plan_rate_flash_max(f60, R24)
    assert k == 5 and len(ex._flash_lines([x1, x2, y], changes, f60, R24)) == 1
    got, a1, done = ex.fix_cut_flashes([x1, x2, y], audio, changes, f60, R24, None, k)
    assert _shape(got) == [(380, 420 - n, 3088, 3128 - n), (420 - n, 548, 5733 - n, 5861)]
    assert [(a["start"], a["end"], a["in"]) for a in a1] == [(380, 420 - n, 3088), (420 - n, 548, 5733 - n)]
    assert done[0]["frames"] == n and ex._flash_lines(got, changes, f60, R24) == []


def test_the_sound_item_linked_to_the_flash_clip_goes_with_it():
    """As the tool really writes it (link_pairs: every V1 clip has its own A1 item -- The Best Business Idea (2):
    V1 380-418 | 418-420 | 420-548 and the same three under them on A1)."""
    clips, _audio, changes = _take_split_at_its_shot_change()
    got, a1, done = ex.fix_cut_flashes(clips, _a1(clips), changes, R24, R24)
    assert _shape(got) == [(0, 48, 1000, 1048), (48, 100, 4998, 5050)] and done[0]["to"] == 48
    assert [(a["start"], a["end"], a["in"], a["out"]) for a in a1] == [(0, 48, 1000, 1048), (48, 100, 4998, 5050)]
    # ... and the other way round: the first 2 frames of S02 were the shot before's
    x = _framed(_clip24(0, 50, 1000, 1), 0.0)
    y1, y2 = _framed(_clip24(50, 52, 4998, 2), 40.0), _framed(_clip24(52, 100, 5000, 2), 40.0)
    changes = shots.seconds([900, 3000, 5000, 6000], R24)
    got, a1, done = ex.fix_cut_flashes([x, y1, y2], _a1([x, y1, y2]), changes, R24, R24)
    assert _shape(got) == [(0, 52, 1000, 1052), (52, 100, 5000, 5048)] and done[0]["to"] == 52
    assert [(a["start"], a["end"], a["in"], a["out"]) for a in a1] == [(0, 52, 1000, 1052), (52, 100, 5000, 5048)]
    # a sound item under the flash clip that is NOT its own sound stays, and so does the cut
    odd = _a1(clips)
    odd[1] = dict(odd[1], **{"in": 7000, "out": 7002})
    clips, _audio, changes = _take_split_at_its_shot_change()
    assert ex.fix_cut_flashes(clips, odd, changes, R24, R24)[2] == []
