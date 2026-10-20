"""Day 13, section 8: the person speaking centred in the window (export_xml_edl._speaker_centre, SPEAKER_CENTRE).

The layouts are the real ones of the keyed test videos (their cut lists and caches, day 13): the template window, the
RAW size, the competitor's video box and transform, the tool's framing of the clip, and the faces Light-ASD found
there (people.py tracks, the boxes as speakers.extent reads them)."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import export_xml_edl as ex, people as PP, speakers
from match_cuts.geometry import Sim
from match_cuts.model import Box, Segment

WIN = (19.84, 524.84, 1040.32, 1214.32)          # templates/default.png's hole (every keyed new-style video)
FPS = Fraction(60)


@pytest.fixture(autouse=True)
def _rule_on(monkeypatch):
    monkeypatch.setenv("MATCH_CUTS_SPEAKER_CENTRE", "1")


def _clip(t0: float, t1: float, comp: dict, box: Box, framing: Sim | None = None, sid: int = 1, rec0: int = 0):
    """One V1 clip playing RAW [t0, t1) s at 100 %, its competitor transform ``comp`` in the video box ``box`` and
    the tool's framing (default: the competitor's mapped into the window)."""
    seg = Segment(id=sid, type="raw", comp_in=0, comp_out=int(round((t1 - t0) * 30)), raw_in_seconds=t0, speed=1.0,
                  transform=comp, confidence=.9)
    sim = framing if framing is not None else ex._window_map(Sim.from_dict(comp), box, WIN)
    n = int(round((t1 - t0) * 60))
    ev = SimpleNamespace(seg=seg, rec_in=rec0, rec_out=rec0 + n, seg_name=f"S{sid:02d}", dissolve_in=0)   # fac 1
    a = int(round(t0 * 60))
    return ex.PremiereClip(seg, ev, rec0, rec0 + n, rec0, rec0 + n, a, a + n, 1.0, True, 0.0, 1.0, True, [(a, sim)],
                           None, [ev]), sim


def _track(tid: int, t0: float, t1: float, box, score: float, found: float = 1.0) -> PP.Track:
    """A face over RAW [t0, t1) s (25 analysis frames a second), its box steady, Light-ASD ``score`` on every frame,
    detected on the share ``found`` of its frames (the rest filled in, as people.track does)."""
    k = np.arange(int(round(t0 * PP.ANALYSIS_FPS)), int(round(t1 * PP.ANALYSIS_FPS)))
    f = np.ones(len(k), bool)
    if found < 1.0:
        f[np.linspace(0, len(k) - 1, int(round((1.0 - found) * len(k)))).astype(int)] = False
    return PP.Track(tid, k, np.tile(np.asarray(box, float), (len(k), 1)), f, np.full(len(k), float(score)))


def _ctx(tracks, raw_wh, t0: float, t1: float, shots=None) -> speakers.Context:
    """speakers.Context as the pipeline makes it: the tracks analysed over [t0 - 3, t1 + 3) s, speech over the whole
    stretch."""
    p = PP.People(list(tracks), [(max(0.0, t0 - 3.0), t1 + 3.0)], "Light-ASD on cpu", raw_wh)
    return speakers.Context(p, [(t0, t1)], shots, raw_wh)


def _dx(sim: Sim, raw_w: float, seq_w: float = 1080.0) -> float:
    """The picture's sideways offset from the sequence's centre (sequence px) -- what check-all's framing score and
    Premiere's Position read."""
    return sim.tx + sim.s * raw_w / 2.0 - seq_w / 2.0


# video11 (Super Troopers): the competitor shows one crop of the whole 2.39:1 picture
V11_RAW = (1920.0, 1040.0)
V11_BOX = Box(38.517, 576.0, 1003.483, 818.257)
V11_COMP = {"scale": 0.8009995919996613, "rotation_deg": 0.0, "tx": -238.75715781786687, "ty": 568.8819941307354}


def test_a_clear_speaker_off_the_middle_is_centred_as_you_did():
    """video11 S01, RAW 30.05-32.83 s: the captain speaking (Light-ASD 4.6 on every speech frame) sits left of the
    window's middle; you moved the picture +188 px. The rule: +212 px (within check-all's 40 px), zoom kept."""
    clip, sim = _clip(30.05, 32.83, V11_COMP, V11_BOX)
    assert sim.s == pytest.approx(1.188709, abs=1e-5) and _dx(sim, V11_RAW[0]) == pytest.approx(-14.9, abs=0.5)
    sp = _ctx([_track(3, 30.05, 32.83, (615, 78, 949, 568), 4.6)], V11_RAW, 30.05, 32.83)
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 1
    new = clip.keys[0][1]
    assert new.s == sim.s and new.ty == sim.ty                                   # zoom and height kept
    assert abs(_dx(new, V11_RAW[0]) - 187.8) <= 40.0
    assert speakers.passes(new, sp.faces(30.05, 32.83), V11_RAW[0], False, WIN)
    assert "centred on the person speaking" in clip.person_note and "day 13" in clip.framing_note


def test_an_unsure_speaker_is_never_followed():
    """video11 S01, RAW 44.04-46.59 s: Light-ASD's "speaker" (0.73, the man at the left) was the wrong one -- you
    framed the shouting man at the right. A mean score under CENTRE_ASD moves nothing."""
    clip, sim = _clip(44.04, 46.59, V11_COMP, V11_BOX)
    sp = _ctx([_track(10, 44.04, 46.59, (269, 163, 467, 413), 0.73),
               _track(11, 44.04, 46.59, (852, 120, 1098, 380), 0.59),
               _track(12, 44.04, 46.59, (1399, 120, 1683, 450), -0.98)], V11_RAW, 44.04, 46.59)
    f = sp.faces(44.04, 46.59)
    assert f.how == "speaker" and f.track == 10 and f.share == 1.0               # Light-ASD's pick: the wrong man
    asd, found, margin = ex._speaker_strength(sp, f, 44.04, 46.59)
    assert asd == pytest.approx(0.73) and found == 1.0 and margin == pytest.approx(0.14)
    assert margin < ex.CENTRE_MARGIN                                             # two men who might both speak
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim and not clip.person_note


def test_the_biggest_face_is_never_a_speaker():
    """Nobody scored as speaking (every Light-ASD score below 0): People.speaker falls back to the biggest face --
    never followed."""
    clip, sim = _clip(32.82, 33.62, V11_COMP, V11_BOX)
    sp = _ctx([_track(5, 32.82, 33.62, (1409, 129, 1642, 452), -2.2)], V11_RAW, 32.82, 33.62)
    assert sp.faces(32.82, 33.62).how == "biggest face"
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim


def test_a_speaker_nearly_in_the_middle_is_left():
    """video11 S01, RAW 42.75-44.04 s: the man speaking 61 px off the window's middle -- you left it (CENTRE_MIN)."""
    clip, sim = _clip(42.75, 44.04, V11_COMP, V11_BOX)
    sp = _ctx([_track(9, 42.75, 44.04, (823, 120, 1019, 366), 3.14)], V11_RAW, 42.75, 44.04)
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim


# video6 (The Penguins of Madagascar, a cartoon): you kept the competitor's middle on all 9 clips
V6_RAW = (1912.0, 1072.0)
V6_BOX = Box(0.0, 341.0, 720.0, 598.0)
V6_COMP = {"scale": 0.5650224142649679, "rotation_deg": 0.0, "tx": -179.94585541537919, "ty": 331.1318199722193}


def test_a_flickering_cartoon_face_is_left():
    """video6, RAW 36.17-38.52 s: King Julien speaking (Light-ASD 3.48) right of the middle -- but the face detection
    finds the cartoon face on only 82 % of the frames (a live face: 100 %). You kept it: so does the rule."""
    clip, sim = _clip(36.17, 38.52, V6_COMP, V6_BOX, Sim(1.149614, 0.0, -558.748, 506.774))   # the run's framing
    sp = _ctx([_track(1, 36.17, 38.52, (968, 273, 1260, 639), 3.48, found=0.82)], V6_RAW, 36.17, 38.52)
    assert ex._speaker_centre([clip], sp, V6_RAW, WIN, FPS, V6_BOX, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim
    sp = _ctx([_track(1, 36.17, 38.52, (968, 273, 1260, 639), 3.48)], V6_RAW, 36.17, 38.52)     # a steady face
    assert ex._speaker_centre([clip], sp, V6_RAW, WIN, FPS, V6_BOX, 250.0, Fraction(24000, 1001), []) == 1


# video15 (the Doritos ad): the delivery room
V15_RAW = (1920.0, 1080.0)
V15_BOX = Box(0.0, 555.0, 1080.0, 810.0)
V15_COMP = {"scale": 0.7505955526366092, "rotation_deg": 0.0, "tx": -203.2371596250108, "ty": 554.6488228185185}


def test_a_wide_shot_keeps_the_competitors_framing():
    """video15, RAW 8.68-9.68 s: the wife speaking (Light-ASD 4.96) on the bed at the left, the doctor and the husband
    -- a wide shot (her face 15 % of the picture's height). You kept the competitor's framing."""
    clip, sim = _clip(8.68, 9.68, V15_COMP, V15_BOX)
    sp = _ctx([_track(3, 8.68, 9.68, (527, 387, 654, 550), 4.96), _track(4, 8.68, 9.68, (1283, 300, 1420, 486), -1.0)],
              V15_RAW, 8.68, 9.68)
    assert ex._speaker_centre([clip], sp, V15_RAW, WIN, FPS, V15_BOX, 250.0, Fraction(25), []) == 0
    assert clip.keys[0][1] is sim


def test_the_competitors_own_close_framing_is_kept():
    """A competitor whose window shows no more than the template's (021's close-ups): its framing is its choice of
    whom to show -- never moved."""
    box = Box(0.0, 300.0, 720.0, 720.0 * WIN[3] / WIN[2])                       # the template window's own shape
    comp = {"scale": 0.8, "rotation_deg": 0.0, "tx": -400.0, "ty": 300.0}
    clip, sim = _clip(30.05, 32.83, comp, box)
    cw = ex._comp_window_raw_x(clip, box)
    assert (cw[1] - cw[0]) * sim.s < WIN[2] * (1.0 + ex.NARROW_OVERFLOW)
    face = (cw[0] + 40.0, 100.0, cw[0] + 300.0, 500.0)                            # well off the middle, inside it
    sp = _ctx([_track(3, 30.05, 32.83, face, 4.6)], V11_RAW, 30.05, 32.83)
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, box, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim


def test_a_speaker_the_competitor_did_not_show_is_left():
    clip, sim = _clip(30.05, 32.83, V11_COMP, V11_BOX)
    cw = ex._comp_window_raw_x(clip, V11_BOX)
    face = (cw[1] + 20.0, 100.0, cw[1] + 250.0, 500.0)                            # past the competitor's own edge
    sp = _ctx([_track(3, 30.05, 32.83, face, 4.6)], V11_RAW, 30.05, 32.83)
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim


# video14 (Meet the Blacks): S12+S13 and S14+S15+S16 -- one RAW shot (45.13-54.80 s), a jump cut between them, both
# showing the framing --min-move held from S09; you moved both -154 px onto the man in the suit speaking
V14_RAW = (1920.0, 800.0)
V14_BOX = Box(0.0, 369.0, 720.0, 541.637)
V14_COMP = {"scale": 0.6692356861150994, "rotation_deg": 0.0, "tx": -187.43661737952868, "ty": 372.30514481356175}
V14_HELD = Sim(1.5179, 0.0, -917.039, 524.84)


def test_the_pieces_of_one_shot_move_together():
    a, _ = _clip(45.15, 46.68, V14_COMP, V14_BOX, V14_HELD, sid=12, rec0=900)
    b, _ = _clip(46.93, 47.67, V14_COMP, V14_BOX, V14_HELD, sid=14, rec0=992)
    suit = (986, 162, 1157, 383)
    sp = _ctx([_track(12, 45.15, 47.67, suit, 1.5), _track(11, 45.15, 47.67, (436, 60, 606, 329), -1.8)],
              V14_RAW, 45.15, 47.67, shots=[45.13, 54.8])
    assert sp.same_shot(46.68, 46.93)
    assert ex._speaker_centre([a, b], sp, V14_RAW, WIN, FPS, V14_BOX, 250.0, Fraction(24000, 1001),
                              [45.13, 54.8]) == 2
    na, nb = a.keys[0][1], b.keys[0][1]
    assert ex._same_framing(na, nb)                                              # one framing: no jump inside the shot
    assert abs(_dx(na, V14_RAW[0]) - (-153.7)) <= 40.0                            # yours: -153.7
    assert na.s == V14_HELD.s


def test_a_neighbour_in_the_same_shot_keeps_min_move():
    """Only the first of two neighbours in one RAW shot has the speaker clear, and they show different framings:
    moving it would leave them under --min-move apart while the moved framing still shows the second one's person --
    the XML's --min-move check would fail it: not moved. (When the framing before a cut does not show the person
    after it, a smaller change is the person check's -- allowed, as the XML check allows it.)"""
    a, _ = _clip(45.15, 46.68, V14_COMP, V14_BOX, V14_HELD, sid=12, rec0=900)
    other = Sim(V14_HELD.s, 0.0, V14_HELD.tx - 120.0, V14_HELD.ty)              # 120 px away from the held framing
    b, _ = _clip(46.93, 47.67, V14_COMP, V14_BOX, other, sid=14, rec0=992)
    suit = (986, 162, 1157, 383)
    sp = _ctx([_track(12, 45.15, 46.68, suit, 2.0), _track(13, 46.93, 47.67, suit, -1.0)],    # silent after the cut
              V14_RAW, 45.15, 47.67, shots=[45.13, 54.8])
    moved = ex._speaker_centre([a, b], sp, V14_RAW, WIN, FPS, V14_BOX, 250.0, Fraction(24000, 1001), [45.13, 54.8])
    assert moved == 0
    assert a.keys[0][1] is V14_HELD and b.keys[0][1] is other


def test_a_change_at_a_raw_shot_change_the_xml_check_reads_as_one_shot_keeps_min_move():
    """video11 S01 at 46.6 s: the RAW changes shot at 46.589 s, the clip is cut there on the tick it first shows -- but
    the XML's --min-move check reads the clip before as ending half a tick later (export_xml_edl._last_raw_s), i.e.
    in the same shot. Centring the captain after the cut (+245 px, under --min-move 250) while the framing before
    shows him would fail that check: not moved (the rule never makes a hard check fail)."""
    clip, sim = _clip(44.05, 47.85, V11_COMP, V11_BOX)
    change = 1117 / (24000 / 1001)                                                 # 46.5880 s: RAW frame 1117
    sp = _ctx([_track(9, 44.05, 46.58, (823, 120, 1019, 366), -2.0),
               _track(14, 46.60, 47.85, (588, 68, 945, 568), 2.2)], V11_RAW, 44.05, 47.85, shots=[change])
    assert sp.same_shot(ex._last_raw_s(ex._split_clip(clip, 153)[0], FPS), 46.6)  # 153: the tick of 46.6 s - 44.05 s
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), [change]) == 0
    assert clip.keys[0][1] is sim and not clip.shot_framing


def test_only_the_raw_shot_with_a_clear_speaker_moves():
    """video11 S01 plays RAW 30.05-38.83 s over two RAW shots (a change at 32.83 s): the captain speaking in the first
    (off the middle), nobody clearly speaking in the second -- the first shot moves, the second keeps its framing
    (shot_framing, as the XML splits the clip there)."""
    clip, sim = _clip(30.05, 38.83, V11_COMP, V11_BOX)
    sp = _ctx([_track(3, 30.05, 32.82, (615, 78, 949, 568), 4.6),
               _track(4, 32.85, 38.83, (889, 110, 1093, 380), -2.3)], V11_RAW, 30.05, 38.83, shots=[32.83])
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), [32.83]) == 1
    first = clip.keys[0][1]
    assert abs(_dx(first, V11_RAW[0]) - 187.8) <= 40.0
    assert len(clip.shot_framing) == 1 and ex._same_framing(clip.shot_framing[0][1], sim)
    assert "centred on the person speaking" in clip.person_note and "one RAW shot" in clip.person_note


def test_the_rule_can_be_switched_for_one_run(monkeypatch):
    monkeypatch.setenv("MATCH_CUTS_SPEAKER_CENTRE", "0")
    assert not ex._speaker_centre_on()
    monkeypatch.setenv("MATCH_CUTS_SPEAKER_CENTRE", "1")
    assert ex._speaker_centre_on()
    monkeypatch.delenv("MATCH_CUTS_SPEAKER_CENTRE")
    assert ex._speaker_centre_on() is ex.SPEAKER_CENTRE


def test_a_clip_framed_per_shot_already_keeps_its_other_shots():
    """A clip the day-12 rule already framed per RAW shot (shot_framing: its second shot slid onto someone): the
    first shot's clear speaker is centred, the second shot keeps the day-12 framing."""
    clip, sim = _clip(30.05, 38.83, V11_COMP, V11_BOX)
    tick = int(round(32.83 * 60))
    second = Sim(sim.s, 0.0, sim.tx - 300.0, sim.ty)
    clip.shot_framing = [(tick, second)]
    sp = _ctx([_track(3, 30.05, 32.82, (615, 78, 949, 568), 4.6),
               _track(4, 32.85, 38.83, (1300, 110, 1500, 380), -2.3)], V11_RAW, 30.05, 38.83, shots=[32.83])
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), [32.83]) == 1
    assert abs(_dx(clip.keys[0][1], V11_RAW[0]) - 187.8) <= 40.0
    assert len(clip.shot_framing) == 1 and ex._same_framing(clip.shot_framing[0][1], second)
    assert abs(clip.shot_framing[0][0] - tick) <= 2


def test_nothing_moved_leaves_a_clip_framed_per_shot_untouched():
    clip, sim = _clip(30.05, 38.83, V11_COMP, V11_BOX)
    second = Sim(sim.s, 0.0, sim.tx - 300.0, sim.ty)
    clip.shot_framing = [(int(round(32.83 * 60)), second)]
    note = clip.framing_note
    sp = _ctx([_track(4, 30.05, 38.83, (900, 110, 1100, 380), -2.3)], V11_RAW, 30.05, 38.83, shots=[32.83])
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), [32.83]) == 0
    assert clip.keys[0][1] is sim and clip.shot_framing == [(int(round(32.83 * 60)), second)]
    assert clip.framing_note == note and not clip.person_note


def test_a_two_shot_the_window_shows_keeps_its_framing():
    """021 at 82.5 s: two women in the pool, the one on the left shown whole, the one on the right speaking (Light-ASD
    3.9) cut at the window's edge. Centring her would push the other out of the window -- you kept the two-shot."""
    clip, sim = _clip(30.05, 32.83, V11_COMP, V11_BOX)
    v0, v1 = (WIN[0] - sim.tx) / sim.s, (WIN[0] + WIN[2] - sim.tx) / sim.s
    left = (v0 + 25.0, 100.0, v0 + 225.0, 400.0)                                 # shown whole
    right = (v1 - 160.0, 100.0, v1 + 40.0, 400.0)                                 # 20 % past the right edge
    sp = _ctx([_track(1, 30.05, 32.83, left, -1.0), _track(2, 30.05, 32.83, right, 3.9)], V11_RAW, 30.05, 32.83)
    f = sp.faces(30.05, 32.83)
    assert f.track == 2 and speakers.shown(speakers.on_screen(sim, left, V11_RAW[0], False), WIN)
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 0
    assert clip.keys[0][1] is sim
    sp = _ctx([_track(2, 30.05, 32.83, right, 3.9)], V11_RAW, 30.05, 32.83)        # alone: centred on her
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 1


def test_a_lone_face_needs_a_sure_speaker():
    """021 at 54 s: three men laughing, only one face found (the one in the middle, laughing with his mouth wide open,
    was missed) -- Light-ASD 0.6 on the face found. A face with nobody else found next to it needs CENTRE_ALONE_ASD:
    the one the competitor shows may be the face the detection missed."""
    clip, sim = _clip(30.05, 32.83, V11_COMP, V11_BOX)
    face = (1100.0, 100.0, 1300.0, 400.0)
    sp = _ctx([_track(5, 30.05, 32.83, face, 0.6)], V11_RAW, 30.05, 32.83)
    assert sp.faces(30.05, 32.83).how == "speaker"
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 0
    sp = _ctx([_track(5, 30.05, 32.83, face, 2.0)], V11_RAW, 30.05, 32.83)
    assert ex._speaker_centre([clip], sp, V11_RAW, WIN, FPS, V11_BOX, 250.0, Fraction(24000, 1001), []) == 1


def test_the_rule_is_on_by_default():
    assert ex.SPEAKER_CENTRE is True
