"""The person speaking is always in the picture (task 2): people.py (YuNet faces, tracks, Light-ASD speaking scores)
and speakers.py (the framing check and the re-frame), on hand-made data; the Zendaya interview end to end."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import people as P, speakers as SP
from match_cuts.geometry import Sim

WIN = (42.0, 555.0, 998.0, 1037.0)
RAW = (1920.0, 1080.0)
ZEN = Path(__file__).resolve().parents[3] / "tests" / "real" / "zendaya"


def sim_showing(x_centre_raw: float, s: float = 1.16) -> Sim:
    """A rotation-0 framing at zoom s with RAW x ``x_centre_raw`` at the window's centre (covering it)."""
    tx = WIN[0] + WIN[2] / 2.0 - s * x_centre_raw
    ty = WIN[1] + WIN[3] / 2.0 - s * RAW[1] / 2.0
    return Sim(s, 0.0, tx, ty)


def track(tid: int, k0: int, n: int, box, scores) -> P.Track:
    return P.Track(tid, np.arange(k0, k0 + n), np.tile(np.asarray(box, float), (n, 1)), np.ones(n, bool),
                   np.asarray(scores, float))


# ---------------------------------------------------------------------------------------------------------------------
# the check and the re-frame (speakers.py)
# ---------------------------------------------------------------------------------------------------------------------

TOM = (430.0, 180.0, 630.0, 420.0)       # the Zendaya interview's two faces (RAW px)
ZENDAYA = (1310.0, 180.0, 1510.0, 420.0)


def test_the_speaker_inside_the_window_passes_and_outside_fails():
    f = SP.Faces("speaker", True, ZENDAYA, [ZENDAYA])
    assert SP.passes(sim_showing(1410), f, RAW[0], False, WIN)
    assert not SP.passes(sim_showing(530), f, RAW[0], False, WIN)                 # the framing shows Tom
    # a flipped clip mirrors the RAW: Zendaya then sits where Tom was
    assert SP.passes(sim_showing(1920 - 1410), f, RAW[0], True, WIN)


def test_nobody_speaking_needs_one_person_and_no_person_is_not_checked():
    f = SP.Faces("a person", False, None, [TOM, ZENDAYA])
    assert SP.passes(sim_showing(530), f, RAW[0], False, WIN) and SP.passes(sim_showing(1410), f, RAW[0], False, WIN)
    assert not SP.passes(sim_showing(960), f, RAW[0], False, WIN)                 # the poster between them
    assert SP.passes(sim_showing(960), SP.Faces("nobody", True), RAW[0], False, WIN)
    assert SP.passes(sim_showing(960), None, RAW[0], False, WIN)


def test_reframe_keeps_the_zoom_and_centres_the_speaker_sideways():
    sim = sim_showing(530)
    new = SP.centred(sim, ZENDAYA, RAW, False, WIN)
    assert new.s == sim.s and new.ty == sim.ty                                      # zoom and height kept
    x0, y0, x1, y1 = SP.on_screen(new, ZENDAYA, RAW[0], False)
    assert abs((x0 + x1) / 2 - (WIN[0] + WIN[2] / 2)) < 1e-6                        # centred
    from match_cuts.export_xml_edl import _covers
    assert _covers(new, RAW, WIN)


def test_reframe_stops_where_the_picture_would_no_longer_cover_the_window():
    edge = (1800.0, 180.0, 1900.0, 420.0)                                           # a face at the RAW's right edge
    new = SP.centred(sim_showing(530), edge, RAW, False, WIN)
    from match_cuts.export_xml_edl import _covers
    assert _covers(new, RAW, WIN, tol=1e-6)
    assert SP.passes(new, SP.Faces("speaker", True, edge, [edge]), RAW[0], False, WIN)   # inside, not centred


def test_a_face_cut_off_at_the_top_moves_down_only_as_much_as_needed():
    high = (1310.0, 0.0, 1510.0, 240.0)
    s = 1.6                                                                          # zoomed in: the top is cut off
    sim = Sim(s, 0.0, WIN[0] + WIN[2] / 2 - s * 1410, WIN[1] + WIN[3] - s * 1080)
    assert not SP.passes(sim, SP.Faces("speaker", True, high, [high]), RAW[0], False, WIN)
    new = SP.centred(sim, high, RAW, False, WIN)
    assert new is not None and SP.passes(new, SP.Faces("speaker", True, high, [high]), RAW[0], False, WIN)
    assert abs((s * high[1] + new.ty) - WIN[1]) < 1e-6                               # its top on the window's top


def test_a_close_up_the_competitor_crops_at_the_forehead_still_shows_its_person():
    """output/020 (4K RAW, mirrored, 125 %): S03's speaker in close-up, the competitor's own framing. Their face box
    (the 10th-90th percentile over 9 s) is 86 px above the window's top and 12 px past its right edge -- the person
    is plainly shown; a fully-inside rule re-centred the picture onto the other person."""
    W = 3840.0
    comp = Sim(0.6240, 0.0, -657.5, 395.8)
    face = (1100.0, 118.0, 2073.0, 1443.0)
    r = SP.on_screen(comp, face, W, True)
    assert r[1] < WIN[1] and r[2] > WIN[0] + WIN[2]                                  # cut off at the top and the side
    assert SP.passes(comp, SP.Faces("speaker", True, face, [face]), W, True, WIN)
    # the picture 480 px further right: half the face past the window's right edge -- not shown
    assert not SP.passes(Sim(0.6240, 0.0, -657.5 + 480.0, 395.8), SP.Faces("speaker", True, face, [face]), W, True, WIN)
    # more than SHOWN_FRAC cut off at the top: not shown
    low = Sim(0.6240, 0.0, -657.5, 395.8 - 0.2 * 0.6240 * (face[3] - face[1]))
    assert not SP.passes(low, SP.Faces("speaker", True, face, [face]), W, True, WIN)


def test_a_face_wider_than_the_window_cannot_be_framed():
    huge = (200.0, 100.0, 1700.0, 1000.0)
    assert SP.centred(sim_showing(960), huge, RAW, False, WIN) is None


# ---------------------------------------------------------------------------------------------------------------------
# people.py: tracks, background faces, the dominant speaker, MFCC
# ---------------------------------------------------------------------------------------------------------------------

def test_tracks_follow_overlapping_boxes_and_break_at_a_shot_change():
    dets = {k: [(100 + k, 100, 300 + k, 330, 0.9), (1300, 100, 1500, 330, 0.9)] for k in range(20)}
    del dets[7]                                                                      # a missed frame: filled in
    tr = P.track(dets)
    assert len(tr) == 2 and all(len(t.k) == 20 for t in tr) and not tr[0].found[7]
    assert tr[0].box[7][0] == pytest.approx(107)
    assert len(P.track(dets, breaks=[10])) == 4                                    # never across a shot change


def test_the_dominant_speaker_and_the_biggest_face_when_unclear():
    a = track(0, 0, 50, TOM, [2.0] * 30 + [-1.0] * 20)
    b = track(1, 0, 50, ZENDAYA, [-1.0] * 30 + [3.0] * 20)
    pp = P.People([a, b], [(0.0, 2.0)], "test", (1920, 1080))
    v = pp.speaker(0.0, 2.0, [(0.0, 2.0)])
    assert v["how"] == "speaker" and v["track"].id == 0 and v["share"] == pytest.approx(0.6)
    assert pp.speaker(1.2, 2.0, [(0.0, 2.0)])["track"].id == 1                       # her part of it
    quiet = P.People([track(0, 0, 50, TOM, [-1.0] * 50), track(1, 0, 50, (1300, 150, 1560, 450), [-2.0] * 50)],
                     [(0.0, 2.0)], "test", (1920, 1080))
    v = quiet.speaker(0.0, 2.0, [(0.0, 2.0)])
    assert v["how"] == "biggest face" and v["track"].id == 1                         # no face scores: the biggest one
    assert quiet.speaker(0.0, 2.0, [])["how"] == "a person"                          # nobody speaks


def test_mfcc_is_python_speech_features_exactly():
    """Light-ASD was trained on python_speech_features.mfcc(audio, 16000, numcep=13, winlen=0.025, winstep=0.010);
    the values of its 4th frame for this sine (int16 scale) were computed with python_speech_features 0.6."""
    sig = np.sin(np.arange(1600) * 0.07) * 1000
    want = [13.262126, 37.920323, 27.516524, 19.686097, 12.832535, 5.76692, -0.437045, -7.235701, -12.925878,
            -18.019482, -20.677293, -21.74149, -20.358661]
    got = P.mfcc(sig)
    assert got.shape == (9, 13) and np.allclose(got[3], want, atol=1e-5)


def test_yunet_finds_the_two_people_and_not_the_poster_faces_of_the_zendaya_interview():
    pytest.importorskip("cv2")
    if not (ZEN / "raw.mp4").is_file():
        pytest.skip("tests/real/zendaya not in this checkout")
    from match_cuts.media import VideoReader
    with VideoReader(ZEN / "raw.mp4") as rd:
        img = rd.get(int(170.0 * 25))
    faces = P.detect(img)
    xs = sorted(round((f[0] + f[2]) / 2) for f in faces)
    big = [f for f in faces if f[3] - f[1] > 150]
    assert len(big) == 2 and abs(min(xs) - 530) < 60 and abs(max(xs) - 1430) < 60, faces


# ---------------------------------------------------------------------------------------------------------------------
# the framing rules in export_xml_edl: --min-move only inside one RAW shot, never hiding the person; a run framed
# on its speakers
# ---------------------------------------------------------------------------------------------------------------------

def _pclip(label: str, a: int, b: int, src: int, sim: Sim):
    from match_cuts import export_xml_edl as ex
    from match_cuts.model import Segment
    seg = Segment(id=int(label[1:]), type="raw", comp_in=a // 2, comp_out=b // 2, raw_in_seconds=src / 60.0, speed=1.0)
    return ex.PremiereClip(seg, None, a, b, a, b, src, src + (b - a), 1.0, True, 0.0, 1.0, True, [(src, sim)], None)


class _Ctx(SP.Context):
    """A speakers.Context with given faces per RAW stretch."""
    def __init__(self, shots, faces):
        super().__init__(None, [], shots, RAW)
        self._faces = faces

    def faces(self, t0, t1):
        for (a, b), f in self._faces:
            if a <= t0 < b:
                return f
        return None


def test_min_move_holds_alike_framings_across_a_shot_change_and_chooses_fresh_otherwise():
    """--min-move holds a framing inside a shot, and across a RAW shot change when the new shot's own framing is alike
    (your habit: one framing for alike shots -- video018's wide shots, zendaya-age); a close-up after a wide shot, or
    a framing that would not show the clip before's person, is chosen fresh."""
    from match_cuts import export_xml_edl as ex
    near = sim_showing(560)                                                       # 23 px from the framing before
    nobody = SP.Faces("nobody", False)
    for shots in ([], [10.5], [11.0]):                                            # [11.0]: S02 starts a new shot
        clips = [_pclip("S01", 0, 60, 600, sim_showing(540)), _pclip("S02", 60, 120, 660, near)]
        ex._hold_framing(clips, RAW, WIN, 250.0, sp=_Ctx(shots, [((0.0, 100.0), nobody)]), fps=Fraction(60))
        assert ex._same_framing(clips[1].keys[0][1], sim_showing(540))          # held: one framing for alike shots
        assert ("across a RAW shot change" in clips[1].framing_note) is (shots == [11.0])
    # a new shot zoomed in 10 % (a close-up after a wide shot): chosen fresh
    s0 = sim_showing(540)
    zoomed = Sim(s0.s * 1.10, 0.0, s0.tx - 0.05 * s0.s * RAW[0], s0.ty - 0.05 * s0.s * RAW[1])
    clips = [_pclip("S01", 0, 60, 600, s0), _pclip("S02", 60, 120, 660, zoomed)]
    ex._hold_framing(clips, RAW, WIN, 250.0, sp=_Ctx([11.0], [((0.0, 100.0), nobody)]), fps=Fraction(60))
    assert ex._same_framing(clips[1].keys[0][1], zoomed) and "a new shot of the RAW" in clips[1].framing_note
    # the framing on screen does not show the person of the clip before (it is about to be re-framed on them): a new
    # shot is not held to it
    far = SP.Faces("speaker", True, (1500.0, 180.0, 1700.0, 420.0), [(1500.0, 180.0, 1700.0, 420.0)])
    clips = [_pclip("S01", 0, 60, 600, s0), _pclip("S02", 60, 120, 660, near)]
    ctx = _Ctx([11.0], [((10.0, 10.99), far), ((11.0, 100.0), nobody)])
    ex._hold_framing(clips, RAW, WIN, 250.0, sp=ctx, fps=Fraction(60))
    assert ex._same_framing(clips[1].keys[0][1], near) and "a new shot of the RAW" in clips[1].framing_note


def test_a_short_cutaway_the_take_plays_on_through_does_not_make_a_stretch_unreliable():
    """video018 S07 (1 frame) and S12 (2 frames): the competitor's cutaway over which the take plays on keeps the take's
    framing; a 20-frame NOT-IN-RAW replacement still does not."""
    from match_cuts import export_xml_edl as ex
    clip = _pclip("S07", 0, 60, 600, sim_showing(540))
    clip.ev = type("E", (), {"seg": clip.seg, "rec_in": 0, "rec_out": 30})()
    clip.seg.audio = {"broll": {"replaced": "an uncertain match", "ranges": [[0, 1, 7, "keeps playing (short)"]]}}
    assert ex._unreliable(clip) is None
    clip.seg.audio = {"broll": {"replaced": "NOT-IN-RAW insert", "ranges": [[0, 20, 7, "audio"]]}}
    assert ex._unreliable(clip) == "S07 NOT-IN-RAW insert replaced"


def test_a_framing_that_would_hide_the_speaker_is_not_held():
    from match_cuts import export_xml_edl as ex
    clips = [_pclip("S01", 0, 60, 600, sim_showing(780)), _pclip("S02", 60, 120, 660, sim_showing(980))]
    zen = SP.Faces("speaker", True, (1100.0, 180.0, 1300.0, 420.0), [(1100.0, 180.0, 1300.0, 420.0)])
    ctx = _Ctx([], [((10.0, 10.9), SP.Faces("nobody", False)), ((11.0, 12.0), zen)])
    ex._hold_framing(clips, RAW, WIN, 250.0, sp=ctx, fps=Fraction(60))
    # 200 px apart: --min-move would hold S01's framing, but that hides the speaker at RAW x 1100-1300
    assert ex._same_framing(clips[1].keys[0][1], sim_showing(980))
    assert "would not show its person" in clips[1].framing_note


def test_a_run_is_framed_once_on_its_speakers_when_one_position_shows_them_all():
    a = SP.Faces("speaker", True, (1310.0, 180.0, 1450.0, 420.0), [])
    b = SP.Faces("speaker", True, (1370.0, 180.0, 1510.0, 420.0), [])
    news = SP.frame_run(sim_showing(530), [("c1", a, False), ("c2", b, False)], RAW, WIN)
    assert news[0] is news[1] and all(SP.passes(news[0], f, RAW[0], False, WIN) for f in (a, b))
    far = SP.Faces("speaker", True, (430.0, 180.0, 630.0, 420.0), [])           # Tom: no one position shows both
    news = SP.frame_run(sim_showing(530), [("c1", a, False), ("c2", far, False)], RAW, WIN)
    assert news[1] is None and SP.passes(news[0], a, RAW[0], False, WIN)        # c2 already shows Tom: unchanged


def test_light_asd_finds_who_speaks_in_the_zendaya_interview():
    """Tom speaks at RAW 166.7-167.6 s while Zendaya laughs and turns away; Zendaya speaks at 157.8-158.7 s while Tom
    turns (checked by eye on the frames)."""
    pytest.importorskip("torch")
    if not (ZEN / "raw.mp4").is_file():
        pytest.skip("tests/real/zendaya not in this checkout")
    from match_cuts.media import extract_audio
    y = extract_audio(ZEN / "raw.mp4", sr=16000, mono=True)
    pp = P.analyse(str(ZEN / "raw.mp4"), 25.0, [(157.8, 158.7), (166.7, 167.6)], y)
    if pp.asd.startswith("none"):
        pytest.skip(pp.asd)
    tom = pp.speaker(166.7, 167.6, [(166.6, 167.7)])
    zen = pp.speaker(157.8, 158.7, [(157.7, 158.8)])
    cx = lambda v: float(np.median((v["track"].box[:, 0] + v["track"].box[:, 2]) / 2))   # noqa: E731
    assert tom["how"] == "speaker" and cx(tom) < 800, (tom["how"], cx(tom))
    assert zen["how"] == "speaker" and cx(zen) > 1100, (zen["how"], cx(zen))


def test_once_the_takes_are_joined_a_clip_holds_the_framing_that_shows_its_person():
    """Task 5 (the thorough zendaya edit): a clip took its own framing, 61 px from the one before, because the framing
    before would not show the person of its own short piece; joined with the rest of its take, the framing before
    does show the take's person -- so --min-move holds it, as the XML check reads the joined clip."""
    from match_cuts import export_xml_edl as ex

    def clips():
        cs = [_pclip("S01", 0, 60, 600, sim_showing(780)), _pclip("S02", 60, 300, 660, sim_showing(980))]
        cs[1].framing_note = "its own framing: the framing before would not show its person"
        return cs
    shown = SP.Faces("speaker", True, (700.0, 180.0, 900.0, 420.0), [(700.0, 180.0, 900.0, 420.0)])
    cs = clips()
    assert ex._hold_after_merge(cs, _Ctx([], [((0.0, 100.0), shown)]), RAW, WIN, Fraction(60), 250.0) == 1
    assert ex._same_framing(cs[1].keys[0][1], sim_showing(780)) and "kept from S01" in cs[1].framing_note
    hidden = SP.Faces("speaker", True, (1100.0, 180.0, 1300.0, 420.0), [(1100.0, 180.0, 1300.0, 420.0)])
    cs = clips()                                         # the take's person is hidden by it: its own framing stays
    assert ex._hold_after_merge(cs, _Ctx([], [((0.0, 100.0), hidden)]), RAW, WIN, Fraction(60), 250.0) == 0
    assert ex._same_framing(cs[1].keys[0][1], sim_showing(980))
    cs = clips()                                         # a new shot of the RAW: chosen fresh, never held
    assert ex._hold_after_merge(cs, _Ctx([11.0], [((0.0, 100.0), shown)]), RAW, WIN, Fraction(60), 250.0) == 0


def test_the_person_check_decoded_in_chunks_finds_exactly_what_it_finds_in_one_go(monkeypatch):
    """A long stretch of a big RAW is analysed a chunk of frames at a time (people.FRAME_BUDGET_BYTES: it held ~1 GB
    a second of 4K at once): the faces, tracks and speaking scores are identical to decoding the stretch in one go."""
    pytest.importorskip("torch")
    if not (ZEN / "raw.mp4").is_file():
        pytest.skip("tests/real/zendaya not in this checkout")
    from match_cuts.media import extract_audio
    y = extract_audio(ZEN / "raw.mp4", sr=16000, mono=True)
    ranges = [(166.7, 169.2)]
    one = P.analyse(str(ZEN / "raw.mp4"), 25.0, ranges, y)
    monkeypatch.setattr(P, "FRAME_BUDGET_BYTES", 1)                 # the smallest chunk: one second of frames
    chunked = P.analyse(str(ZEN / "raw.mp4"), 25.0, ranges, y)
    assert len(one.tracks) == len(chunked.tracks) >= 1
    for a, b in zip(one.tracks, chunked.tracks):
        assert np.array_equal(a.k, b.k) and np.array_equal(a.box, b.box) and np.array_equal(a.found, b.found)
        assert np.array_equal(np.nan_to_num(a.score, nan=-99), np.nan_to_num(b.score, nan=-99))
