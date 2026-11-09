"""9 Nov (What About Bob): the sound check found nothing (the competitor's sound sped up 5 %), the picture check found
15 of 16 frames at 1:00:44 -- the piece is cut around the picture's frames, not the whole movie analysed."""
from types import SimpleNamespace as NS

from match_cuts import excerpt as X


def _pair(sound_state, sound_where, pic_state=None, pic_where=()):
    pic = NS(state=pic_state, where=list(pic_where)) if pic_state else None
    return NS(sound=NS(state=sound_state, where=list(sound_where)), picture=pic)


def test_the_sound_wins_when_it_found_the_place():
    assert X.found_places(_pair("found", [100.0, 104.0], "found", [900.0])) == [100.0, 104.0]


def test_the_picture_places_the_piece_when_the_sound_did_not():
    p = _pair("unclear", [], "found", [3644.72, 3644.72, 3656.11, 3656.11, 3650.0])
    assert X.found_places(p) == [3644.72, 3644.72, 3650.0, 3656.11, 3656.11]
    assert X.applies(NS(premiere=True, no_excerpt=False), p) == ""


def test_a_look_alike_frame_far_away_is_left_out_and_nothing_found_means_the_whole_raw():
    p = _pair("unclear", [], "found", [3644.0, 3650.0, 3655.0, 120.0])
    assert X.found_places(p) == [3644.0, 3650.0, 3655.0]
    assert X.found_places(_pair("unclear", [], "unclear", [3644.0])) == []
    assert X.applies(NS(premiere=True, no_excerpt=False), _pair("not_found", [], None)) != ""
