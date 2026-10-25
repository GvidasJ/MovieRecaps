"""Run 18, task 7 (video37, The Dictator's "Worst Bodyguard"): the last framing pass (_person_check_fix) judges the
final clips as the XML PERSON check reads them -- a clip whose window shows nobody while the person speaking is
outside it is centred on them (cut off at the edge: slid in), the clips of one RAW shot with one framing together; a
clip showing someone else whole, keeping the competitor's own framing, or whose move would break --min-move stays.
The people are faked here."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

from match_cuts import export_xml_edl as ex
from match_cuts import speakers
from match_cuts.geometry import Sim
from match_cuts.model import Segment

WIN = (20.0, 525.0, 1040.0, 1214.0)            # the template window on the 1080x1920 sequence
RAW = (1920.0, 1080.0)
F60 = Fraction(60)
MIN_MOVE = 250.0


def _clip(tx: float, rec: tuple[int, int] = (0, 192), src: int = 3279, sid: int = 16):
    seg = Segment(sid, "raw", 0, 300, raw_in_seconds=54.65)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name=f"S{sid}", dissolve_in=0)
    n = rec[1] - rec[0]
    c = ex.PremiereClip(seg, ev, rec[0], rec[1], rec[0], rec[1], src, src + n, 1.0, True, 0.0, 1.0, True,
                        [(src, Sim(1.8, 0.0, tx, 0.0))], None, [ev])
    c.framing_note = "framed on the person speaking (day 12)"
    return c


class _People:
    def __init__(self, faces, one_shot: bool = True):
        self.f, self.one = faces, one_shot

    def faces(self, a, b):
        return self.f

    def same_shot(self, a, b):
        return self.one


OUTSIDE = (1300.0, 300.0, 1450.0, 500.0)       # RAW px: on screen at tx -1000 it is at x 1340-1610 (out)
CUT = (1100.0, 300.0, 1250.0, 500.0)           # ... at x 980-1250: cut off at the window's right edge (1060)


def _fix(clips, sp, own=None):
    return ex._person_check_fix(clips, sp, RAW, WIN, F60, None, own, MIN_MOVE)            # noqa: SLF001


def test_a_speaker_entirely_outside_the_window_is_centred():
    c = _clip(-1000.0)
    sp = _People(speakers.Faces("speaker", True, OUTSIDE, others=[OUTSIDE]))
    assert not speakers.passes(c.keys[0][1], sp.f, RAW[0], False, WIN)
    assert _fix([c], sp) == 1
    new = c.keys[0][1]
    assert speakers.passes(new, sp.f, RAW[0], False, WIN) and ex._covers(new, RAW, WIN)   # noqa: SLF001
    assert new.s == 1.8 and abs((new.tx + 1.8 * 1375.0) - 540.0) < 1e-6        # the face's middle on the window's
    assert "moved onto the person speaking" in c.person_note


def test_a_speaker_cut_off_at_the_edge_slides_in_and_one_shots_clips_move_together():
    a, b = _clip(-1000.0), _clip(-1000.0, rec=(192, 300), sid=17)
    sp = _People(speakers.Faces("speaker", True, CUT, others=[CUT]))
    assert _fix([a, b], sp) == 2
    new = a.keys[0][1]
    assert b.keys[0][1] == new and speakers.passes(new, sp.f, RAW[0], False, WIN)
    assert -260 < new.tx - (-1000.0) < -200                                    # only until the face is in


def test_someone_else_shown_the_competitors_own_framing_or_a_person_shown_stays():
    other = (600.0, 300.0, 700.0, 500.0)                                         # on screen at x 80-260: shown
    c = _clip(-1000.0)
    assert _fix([c], _People(speakers.Faces("speaker", True, OUTSIDE, others=[OUTSIDE, other]))) == 0
    assert c.keys[0][1].tx == -1000.0
    sp = _People(speakers.Faces("speaker", True, OUTSIDE, others=[OUTSIDE]))
    assert _fix([_clip(-1000.0)], sp, own=[(54.0, 58.0)]) == 0                   # the competitor's own framing
    assert _fix([_clip(-1935.0)], sp) == 0                                       # shown already


def test_a_move_that_would_break_min_move_beside_it_is_not_made():
    before = _clip(-1300.0, rec=(0, 100), src=3179, sid=15)                      # shows the person (x 680-950)
    c = _clip(-1000.0, rec=(100, 292), src=3279)
    sp = _People(speakers.Faces("speaker", True, CUT, others=[CUT]))
    assert _fix([before, c], sp) == 0 and c.keys[0][1].tx == -1000.0            # an 83 px change in one shot
    sp2 = _People(speakers.Faces("speaker", True, CUT, others=[CUT]), one_shot=False)
    assert _fix([_clip(-1300.0, rec=(0, 100), src=3179, sid=15), c], sp2) == 1   # another RAW shot: free
