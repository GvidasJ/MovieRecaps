"""speech.py: no cut lands inside speech -- a clip ends --pad-after after its last word and starts --pad-before before
its first, every audio cut moved into the quiet between words (loudness dips, not only the transcript's timings),
and the hard check that fails a run when a cut is left inside speech.

Synthetic speech (tone bursts) over a quiet room tone for the rules; run 011 (raw_audio.m4a, generated_edit.xml and
my_fixed_edit.xml at the repo root, the word timings in tests/fixtures/run011_words.json) for where my cuts went.
"""
from __future__ import annotations

import json
import sys
import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_export_xml_edl as T  # noqa: E402

from match_cuts import silence as S, speech as SP  # noqa: E402

SR = 16000
FPS = Fraction(60)
PA, PB = 0.15, 0.05
TOL = 0.04                     # the 50 ms loudness window: a sound's edge is known to about half a window


def room(dur: float = 6.0, seed: int = 0) -> np.ndarray:
    return (0.002 * np.random.default_rng(seed).standard_normal(int(dur * SR))).astype(np.float32)   # ~-54 dBFS


def tone(y: np.ndarray, a: float, b: float, amp: float = 0.2) -> np.ndarray:
    n0, n1 = int(a * SR), int(b * SR)
    y[n0:n1] += (amp * np.sin(np.arange(n1 - n0) * 0.3)).astype(np.float32)
    return y


def hiss(y: np.ndarray, a: float, b: float, amp: float = 0.08, seed: int = 1) -> np.ndarray:
    """A breath: loud, but noise -- no pitch."""
    n0, n1 = int(a * SR), int(b * SR)
    y[n0:n1] += (amp * np.random.default_rng(seed).standard_normal(n1 - n0)).astype(np.float32)
    return y


def words(*ws) -> list:
    return [types.SimpleNamespace(text=t, raw=t, start=a, end=b) for t, a, b in ws]


THREE = (("hello", 1.0, 1.6), ("there", 2.4, 3.0), ("again", 3.8, 4.4))


def three() -> tuple[np.ndarray, SP.SpeechMap]:
    y = room()
    for _, a, b in THREE:
        tone(y, a, b)
    return y, SP.speech_map(y, SR, S.Settings(), words(*THREE))


# ---------------------------------------------------------------------------------------------
# where a clip ends and starts
# ---------------------------------------------------------------------------------------------

def test_a_clip_ends_after_its_last_word_and_starts_just_before_its_first():
    _, sm = three()
    assert [s.speech for s in sm.sounds] == [True, True, True]
    # inside "there" (2.4-3.0): the nearer end of it -- plays on to its end, or stops after "hello"
    assert SP.end_at(sm, 2.75, 1.0, PA, PB) == pytest.approx(3.0 + PA, abs=TOL)
    assert SP.end_at(sm, 2.5, 1.0, PA, PB) == pytest.approx(1.6 + PA, abs=TOL)
    assert SP.end_at(sm, 2.5, 2.3, PA, PB) == pytest.approx(3.0 + PA, abs=TOL)   # no speech before it in the clip
    # in a pause: --pad-after after the last word, earlier or later than planned
    assert SP.end_at(sm, 3.6, 1.0, PA, PB) == pytest.approx(3.0 + PA, abs=TOL)
    assert SP.end_at(sm, 3.05, 1.0, PA, PB) == pytest.approx(3.0 + PA, abs=TOL)
    # starts: --pad-before before the first word
    assert SP.start_at(sm, 2.5, 4.4, PA, PB) == pytest.approx(2.4 - PB, abs=TOL)
    assert SP.start_at(sm, 2.9, 4.4, PA, PB) == pytest.approx(3.8 - PB, abs=TOL)    # nearer its end: after it
    assert SP.start_at(sm, 3.3, 4.4, PA, PB) == pytest.approx(3.8 - PB, abs=TOL)
    assert SP.start_at(sm, 1.9, 4.4, PA, PB) == pytest.approx(2.4 - PB, abs=TOL)


def test_a_short_gap_between_two_words_is_split_between_the_pads():
    y = tone(tone(room(), 1.0, 1.6), 1.72, 2.3)
    sm = SP.speech_map(y, SR, S.Settings(), words(("one", 1.0, 1.6), ("two", 1.72, 2.3)))
    g0, g1 = next(g for g in sm.gaps if 1.5 < g[0] < 1.7)
    e = SP.end_at(sm, 1.66, 1.0, PA, PB)
    assert g0 < e < g1 and e == pytest.approx(g0 + (g1 - g0) * PA / (PA + PB), abs=1e-6)


def test_the_soft_end_of_a_word_under_the_threshold_is_still_speech():
    """'-ty five' trailing off below the silence threshold (but well above the room tone) belongs to the word: the
    clip ends --pad-after after it, and a cut inside it is a cut inside speech."""
    y = tone(tone(room(), 1.0, 1.6), 1.6, 1.8, amp=0.006)              # ~-47 dBFS: under the threshold, over the noise
    sm = SP.speech_map(y, SR, S.Settings(), words(("twenty-five", 1.0, 1.75)))
    lv = sm.levels
    assert lv["noise_db"] + S.SOFT_DB < -47 < lv["threshold_db"]
    assert SP.end_at(sm, 1.65, 1.0, PA, PB) == pytest.approx(1.8 + PA, abs=TOL)
    assert SP.check([("A", "end", 1.7, 100)], sm, FPS)                  # inside the soft end: inside speech
    assert not SP.check([("A", "end", 2.2, 130)], sm, FPS)


def test_a_dip_inside_a_word_is_not_a_gap_but_the_dip_at_a_word_boundary_is():
    y = room()
    tone(y, 1.0, 1.3)
    tone(y, 1.38, 1.6)                     # "twen | ty-five": the closure of the t is a dip inside one word
    tone(y, 2.0, 2.4)
    tone(y, 2.48, 2.9)                     # "a | son": the transcript puts the boundary 0.2 s late
    sm = SP.speech_map(y, SR, S.Settings(), words(("25", 1.0, 1.6), ("a", 2.0, 2.6), ("son", 2.6, 2.9)))
    assert sm.sound_at(1.34) is not None                                       # never cut between "twen" and "ty"
    assert sm.sound_at(2.44) is None                                           # the gap between "a" and "son"
    # loudness alone (no transcript) cannot tell the two apart: both are gaps
    bare = SP.speech_map(y, SR, S.Settings(), None)
    assert bare.sound_at(1.34) is None and bare.sound_at(2.44) is None


def test_a_breath_at_a_clips_start_may_be_left_out_but_a_word_never():
    y = hiss(room(), 0.6, 0.9)
    tone(y, 1.2, 1.8)
    sm = SP.speech_map(y, SR, S.Settings(), words(("hi", 1.2, 1.8)))
    assert [s.speech for s in sm.sounds] == [False, True]
    assert SP.start_at(sm, 0.55, 1.8, PA, PB) == pytest.approx(1.2 - PB, abs=TOL)   # starts just before "hi"
    assert SP.end_at(sm, 2.5, 1.0, PA, PB) == pytest.approx(1.8 + PA, abs=TOL)
    heard = SP.speech_map(y, SR, S.Settings(), words(("uh", 0.6, 0.9), ("hi", 1.2, 1.8)))     # said: kept
    assert all(s.speech for s in heard.sounds)
    assert SP.start_at(heard, 0.55, 1.8, PA, PB) == pytest.approx(0.55, abs=TOL)
    second = SP.speech_map(y, SR, S.Settings(), words(("hi", 1.2, 1.8)), also=words(("uh", 0.62, 0.88)))
    assert all(s.speech for s in second.sounds)                       # either transcript hearing a word: speech
    blind = SP.speech_map(y, SR, S.Settings(), None)                  # no transcript: every sound is speech
    assert all(s.speech for s in blind.sounds)
    outside = SP.speech_map(y, SR, S.Settings(), words(("hi", 1.2, 1.8)), heard=[(1.0, 2.0)])
    assert outside.sounds[0].speech                                   # not transcribed there: speech


def test_a_voiced_sound_nobody_transcribed_is_speech():
    y = tone(room(), 0.6, 0.9, amp=0.1)                                # a clear pitch: an "mm" Whisper left out
    tone(y, 1.2, 1.8)
    sm = SP.speech_map(y, SR, S.Settings(), words(("hi", 1.2, 1.8)))
    assert all(s.speech for s in sm.sounds) and "voiced" in sm.sounds[0].why


def test_a_voiced_sound_right_after_a_word_is_the_end_of_that_word():
    """video2: "team, OK?" is timed to end 0.06 s before the "-kay" its speaker says, voiced only 0.04 s -- under
    VOICED_S, but right after a word: that word's end (the edit plays it to its end); a breath (no pitch) right after
    a word, or a short voiced click well after one, stays out."""
    y = tone(room(), 1.2, 1.5)                                          # "OK" as timed
    tone(y, 1.56, 1.60, amp=0.1)                                      # "-kay": 0.04 s voiced ...
    hiss(y, 1.60, 1.69, amp=0.02)                                     # ... and its unvoiced rest
    hiss(y, 2.6, 2.9)                                                 # a breath later
    tone(y, 3.4, 3.44, amp=0.1)                                       # a short voiced click, well after the word
    sm = SP.speech_map(y, SR, S.Settings(), words(("OK?", 1.2, 1.5)))
    ok = sm.sounds[0]                                                 # one sound with the word: no cut between them
    assert ok.speech and "its end" in ok.why and ok.s1 == pytest.approx(1.69, abs=TOL)
    assert [s.speech for s in sm.sounds[1:]] == [False, False]
    assert SP.end_at(sm, 1.55, 1.0, PA, PB) == pytest.approx(ok.s1 + PA, abs=TOL)      # a cut there plays it out
    old, SP.TAIL_VOICED_S = SP.TAIL_VOICED_S, 1.0                     # without the rule: a breath, cut off
    try:
        sm0 = SP.speech_map(y, SR, S.Settings(), words(("OK?", 1.2, 1.5)))
    finally:
        SP.TAIL_VOICED_S = old
    assert [s.speech for s in sm0.sounds] == [True, False, False, False]


# ---------------------------------------------------------------------------------------------
# snapping the cuts of an edit
# ---------------------------------------------------------------------------------------------

def test_cuts_inside_speech_move_and_nothing_plays_twice():
    """A ends inside "there", B starts inside it: A plays on to the end of "there"; B, which would show "there" again,
    starts before "again" instead. The trims / extensions are on the sequence; the rows say what moved."""
    _, sm = three()
    a = SP.Piece("A", 0, 120, 0.9 * 60, 1.0)          # RAW 0.9-2.9: ends inside "there"
    b = SP.Piece("B", 120, 210, 2.7 * 60, 1.0)        # RAW 2.7-4.2: starts inside "there", ends inside "again"
    trims, inserts, rows, shifts = SP.snap_edits([a, b], sm, FPS, PA, PB)
    ext = {(at, side): d for at, d, side, _ in inserts}
    assert ext[(120, "end")] == pytest.approx((3.0 + PA - 2.9) * 60, abs=TOL * 60)      # A: + about 15 frames
    b_trim = next(t for t in trims if t[0] == 120)
    assert (b_trim[1] - 120) / 60 + 2.7 == pytest.approx(3.8 - PB, abs=TOL)          # B starts just before "again"
    assert ext[(210, "end")] == pytest.approx((4.4 + PA - 4.2) * 60, abs=TOL * 60)      # B plays "again" to its end
    assert not shifts and {r["clip"] for r in rows} == {"A", "B"}
    assert any(r["inside"] for r in rows)
    # a clip that only plays what the clip before now plays goes
    c = SP.Piece("C", 120, 150, 2.75 * 60, 1.0)        # RAW 2.75-3.25: the end of "there" (again) and the pause
    trims, _, rows, _ = SP.snap_edits([a, c], sm, FPS, PA, PB)
    assert (120, 150) in trims and [r["edge"] for r in rows if r["clip"] == "C"] == ["whole"]


def test_the_edits_first_frame_is_never_before_the_competitors():
    """A clip starting inside a word starts before the word -- except the edit's very first clip: on all 9 answer keys
    you start at or after the competitor's first frame, and you moved back every earlier start this step made
    (video1 -0.42 s, video017 -0.23 s, video018 -0.35 s with 16 frames of the shot before). A later clip still moves."""
    _, sm = three()
    first = SP.Piece("A", 0, 60, 1.2 * 60, 1.0)         # RAW 1.2-2.2: starts inside "hello"
    b = SP.Piece("B", 60, 120, 3.95 * 60, 1.0)          # RAW 3.95-4.95: starts inside "again"
    _, inserts, _, _ = SP.snap_edits([first, b], sm, FPS, PA, PB)
    ext = {(at, side): d for at, d, side, _ in inserts}
    assert (0, "start") not in ext                      # the first frame stays the competitor's
    assert ext[(60, "start")] == pytest.approx((3.95 - (3.8 - PB)) * 60, abs=TOL * 60)   # B starts before "again"


def test_a_tiny_jump_inside_speech_plays_on_as_one_take():
    """A cut skipping (or repeating) at most 0.1 s of the RAW inside speech: the two clips play on as one take --
    a skip, the clip before plays on to where the next starts; a repeat, the next starts where the one before ends."""
    _, sm = three()
    a = SP.Piece("A", 0, 120, 0.7 * 60, 1.0)          # RAW 0.7-2.7: ends inside "there"
    skip = SP.Piece("B", 120, 150, 2.7 * 60 + 3, 1.0)                  # 3 frames on
    trims, inserts, rows, _ = SP.snap_edits([a, skip], sm, FPS, PA, PB)
    assert (120, 3, "end", 2.7 * 60) in inserts and not any(t[0] == 120 for t in trims)
    again = SP.Piece("B", 120, 150, 2.7 * 60 - 4, 1.0)                 # 4 frames back
    trims, inserts, rows, _ = SP.snap_edits([a, again], sm, FPS, PA, PB)
    assert (120, 124) in trims and not any(i[0] == 120 for i in inserts)


def test_one_take_running_on_is_not_a_cut_and_a_locked_edge_stays():
    _, sm = three()
    a = SP.Piece("A", 0, 100, 1.5 * 60, 1.0)           # RAW 1.5-3.1667 ...
    b = SP.Piece("B", 100, 160, 1.5 * 60 + 100, 1.0)   # ... continues in the very next RAW frame: one take
    trims, inserts, rows, _ = SP.snap_edits([a, b], sm, FPS, PA, PB)
    assert all(r["at"] not in (100,) for r in rows)
    locked = SP.Piece("A", 0, 100, 1.5 * 60, 1.0, lock_end=True)
    other = SP.Piece("B", 100, 160, 4.0 * 60, 1.0, lock_start=True)   # a cross dissolve: both sides stay
    trims, inserts, rows, _ = SP.snap_edits([locked, other], sm, FPS, PA, PB)
    assert all(r["at"] != 100 for r in rows)


def test_an_audio_line_jumping_a_little_inside_speech_plays_on():
    """A1 jumps 2 frames inside "there" where V1 does not cut: the audio line before the jump moves by them."""
    _, sm = three()
    a = SP.Piece("A", 0, 100, 2.5 * 60 - 100, 1.0, shiftable=True)      # ends at RAW 2.5 (inside "there")
    b = SP.Piece("B", 100, 140, 2.5 * 60 + 2, 1.0)                        # starts 2 frames later
    _, _, rows, shifts = SP.snap_edits([a, b], sm, FPS, PA, PB, v1_cuts={0, 140})
    assert shifts == [(0, 2)] and any(r["edge"] == "audio line" for r in rows)


def test_an_a1_cut_under_a_cross_dissolve_slides_out_of_speech(tmp_path):
    """Both sides of a cross dissolve stay where they are; A1 cuts hard there, so a cut inside a word slides under
    the dissolve to where both sides are quiet -- the timeline does not change, and the export passes its check."""
    _, sm = three()
    p = SP.Piece("A", 0, 100, 2.6 * 60 - 100, 1.0, lock_end=True)               # ends at RAW 2.6, inside "there"
    q = SP.Piece("B", 100, 160, 1.5 * 60, 1.0, lock_start=True, slack=40)        # starts at RAW 1.5 (a pause)
    trims, inserts, rows, edits = SP.snap_edits([p, q], sm, FPS, PA, PB)
    (at, d, kind), = [e for e in edits if len(e) == 3]
    assert (at, kind) == (100, "slide") and d > 0 and not SP._inside(sm, 2.6 + d / 60)
    assert not trims or all(t[0] != 100 for t in trims)
    # the export: the Premiere fixture's A1 cut under its cross dissolve slid 4 frames
    from match_cuts import export_xml_edl as ex
    from match_cuts.config import Config
    cl = T.premiere_cutlist()
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    rp = S.Ripple([], 600, slides=[(248, 4)])
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, rp)
    a1 = {(a["start"], a["end"]) for a in ex.parse_premiere_xml(xml)["audio"]}
    assert (200, 252) in a1 and (252, 320) in a1
    v = ex.validate_premiere_exports(cl, xml, None, cfg, rp)
    assert v["ok"], v["errors"]


def test_the_check_reads_the_cuts_of_a1():
    _, sm = three()
    items = [{"name": "A", "start": 0, "end": 60, "in": 60, "out": 120, "speed": 1.0},       # RAW 1.0-2.0
             {"name": "B", "start": 60, "end": 120, "in": 150, "out": 210, "speed": 1.0},    # RAW 2.5-3.5
             {"name": "C", "start": 120, "end": 180, "in": 210, "out": 270, "speed": 1.0}]   # one take with B
    cuts = SP.audio_cuts(items, FPS, 180)
    assert [(c[0], c[1]) for c in cuts] == [("A", "end"), ("B", "start")]
    bad = SP.check(cuts, sm, FPS)
    assert [(r["clip"], r["edge"]) for r in bad] == [("B", "start")] and "there" in bad[0]["said"]


# ---------------------------------------------------------------------------------------------
# the ripple: clips extended where their speech must finish
# ---------------------------------------------------------------------------------------------

def test_a_ripple_with_extensions_moves_everything_after_them():
    rp = S.Ripple([S.Cut(10, 20, 0, 0)], 100, [S.Insert(50, 6, "end"), S.Insert(50, 4, "start")])
    assert (rp.removed, rp.added, rp.new_frames) == (10, 10, 100)
    assert [rp.map(f) for f in (0, 15, 20, 49, 50, 99)] == [0, 10, 10, 39, 50, 99]
    two = S.Ripple([S.Cut(0, 5, 0, 0)], rp.new_frames, before=rp)
    assert two.active and two.first_frames == 100 and two.new_frames == 95 and two.map(50) == 45
    assert len(two.stages()) == 2 and not S.Ripple([], 10).active


def test_the_premiere_export_extends_clips_and_passes_its_own_check(tmp_path):
    from match_cuts import export_xml_edl as ex
    from match_cuts.config import Config
    cl = T.premiere_cutlist()
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    clips, _, _ = ex.premiere_clips(cl, cfg)
    first = clips[0]
    snap = S.Ripple([S.Cut(first.rec_end, first.rec_end + 4, 0, 0)], 600,
                    [S.Insert(first.rec_end, 6, "end", first.src_out)])            # clip 1 plays 6 frames on
    rp = S.Ripple([S.Cut(300, 310, 0, 0)], snap.new_frames, before=snap)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, rp)
    x = ex.parse_premiere_xml(xml)
    assert x["duration"] == 600 + 6 - 4 - 10
    assert (x["clips"][0]["out"] - x["clips"][0]["in"]) == (first.src_out - first.src_in) + 6
    assert x["audio"][0]["out"] == x["clips"][0]["out"]
    v = ex.validate_premiere_exports(cl, xml, None, cfg, rp)
    assert v["ok"], v["errors"]


# ---------------------------------------------------------------------------------------------
# run 011: where I put the cuts
# ---------------------------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]
RUN011 = [ROOT / "raw_audio.m4a", ROOT / "generated_edit.xml", ROOT / "my_fixed_edit.xml"]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "run011_words.json"
# the generated edit (generated_edit.xml): V1 clip, sequence start / end, RAW in (60 fps)
GENERATED = [("S01", 0, 71, 458), ("S01", 71, 268, 567), ("S02", 268, 329, 777), ("S04", 329, 365, 850),
             ("S06", 365, 403, 894), ("S07", 403, 423, 967), ("S08", 423, 475, 1169), ("S08", 475, 534, 1260),
             ("S11", 534, 638, 1330), ("S12", 638, 648, 1436), ("S13", 648, 670, 1446), ("S14", 670, 722, 1470),
             ("S15", 722, 963, 1644), ("S15", 963, 974, 1904)]
# every cut edge I moved in my_fixed_edit.xml (RAW frame), except where I dropped something on purpose ("Okay",
# "Uh", "to", the 1.5 s before "I have a daughter")
MINE = [("S01 end", "out", 549), ("S01b start", "in", 600), ("S01b end", "out", 771), ("S02 end", "out", 852),
        ("S04 start", "in", 854), ("S04 end", "out", 899), ("S06 start", "in", 914), ("S06 end", "out", 952),
        ("S08b end", "out", 1355), ("S11 end", "out", 1457), ("S14 start", "in", 1487), ("S14 end", "out", 1521)]


def run011_cutlist():
    from match_cuts.model import Cutlist, Segment
    segs = [Segment(id=i + 1, type="raw", comp_in=a, comp_out=b, raw_in_seconds=src / 60.0,
                    raw_in_interval=[src / 60.0 - 0.00008, src / 60.0 + 0.00008], speed=1.0, confidence=.97,
                    transform=dict(T.PAN0), label=name) for i, (name, a, b, src) in enumerate(GENERATED)]
    comp = {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "60/1", "frames": 974}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080, "fps": "60/1",
           "frames": 5445, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed", "box": dict(T.PBOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


def matched(clips: list[dict], name: str, side: str, mine: int) -> int | None:
    """The frames between my cut edge and the export's (None: further than 2 frames). A cut I made by skipping at
    most 2 frames that the export plays straight through counts as the same cut."""
    near = min((c[side] for c in clips), key=lambda v: abs(v - mine))
    if abs(near - mine) <= 2:
        return near - mine
    if any(c["in"] < mine - 2 and mine + 2 < c["out"] for c in clips) and name in ("S02 end", "S04 start"):
        return 0                                                       # I cut 852 -> 854; it plays on through
    return None


@pytest.mark.skipif(not all(p.is_file() for p in RUN011), reason="run 011 files not in this checkout")
def test_run_011_no_cut_inside_speech_and_my_cuts_matched(tmp_path):
    from match_cuts import export_xml_edl as ex, media, repeats
    from match_cuts.config import Config
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))["words"]
    w = {m: [types.SimpleNamespace(text=t, raw=t, start=a, end=b) for t, a, b in v] for m, v in fx.items()}
    y = media.extract_audio(ROOT / "raw_audio.m4a", sr=48000, mono=True)
    sm = SP.speech_map(y, 48000, S.Settings(), w["medium.en"], w["small.en"])
    cl = run011_cutlist()
    cfg = Config(out_dir=str(tmp_path), premiere=True, pad_after=0.15, pad_before=0.05)   # that session's pads (the
    assert (cfg.pad_after, cfg.pad_before) == (0.15, 0.05)   # defaults are 0.05 since Task 8 / 0.03 since video018)
    # the generated edit cut inside speech 9 times (the hard check finds them; a 10th lands on the boundary between
    # two words that run together -- a cut may fall there)
    assert len(ex.premiere_speech_problems(ROOT / "generated_edit.xml", sm)) == 9
    plan = repeats.add_to_plan(S.plan_premiere(cl, y, 48000, cfg, None, sm), cl, cfg)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    v = ex.validate_premiere_exports(cl, xml, None, cfg, plan["ripple"], sm)
    assert v["ok"], v["errors"]
    assert v["speech_checked"] and v["speech_problems"] == []
    clips = ex.parse_premiere_xml(xml)["clips"]
    got = {name: matched(clips, name, side, mine) for name, side, mine in MINE}
    assert sorted(k for k, d in got.items() if d is not None) == sorted([
        "S01b start", "S01b end", "S02 end", "S04 start", "S06 start", "S06 end", "S11 end", "S14 start",
        "S14 end"])                                                    # 9 of 12 within 2 frames
    # the 3 others: I kept 0.39 s after "age." and 0.04 s after "am" (the tool keeps --pad-after 0.15), and I ended
    # S08b before "to" to drop it -- the tool keeps "son to a married couple" playing
    assert {k for k, d in got.items() if d is None} == {"S01 end", "S04 end", "S08b end"}


# ---------------------------------------------------------------------------------------------
# a sliver the clip before plays through; an A1 edge next to a muted piece (task 2: the Spider-Man holes)
# ---------------------------------------------------------------------------------------------

def test_a_sliver_whose_start_passes_its_end_goes_and_the_clip_before_plays_its_words():
    """Spider-Man S11: one frame inside "kids," right after S10 (one take, a frame skipped). Its start had to move past
    its own frame: the trim used to reach into the next clip and its extensions belonged to no clip (a 14-frame hole on
    V1 and A1). Now the sliver goes and the clip before plays on to the end of the word."""
    _, sm = three()                                                         # hello 1.0-1.6, there 2.4-3.0
    f = float(FPS)
    p1 = SP.Piece("S10", 0, 60, 30.0, 1.0)                                  # RAW 0.5-1.5: ends inside "hello"
    p2 = SP.Piece("S11", 60, 61, 88.0, 1.0)                                 # RAW 1.467: one frame repeating S10's end
    p3 = SP.Piece("S12", 61, 121, 138.0, 1.0)                               # RAW 2.3-3.3: another moment ("there")
    trims, inserts, rows, _ = SP.snap_edits([p1, p2, p3], sm, FPS, PA, PB, {0, 60, 61, 121})
    assert (60, 61) in trims and not any(a < 61 < b or (a <= 60 < b and (a, b) != (60, 61)) for a, b in trims)
    ext = [i for i in inserts if i[0] == 60 and i[2] == "end"]
    assert ext and sum(i[1] for i in ext) == pytest.approx((1.6 + PA - 1.5) * f, abs=3)   # S10 plays on through "hello"
    for at, n, side, _ in inserts:                                            # every extension belongs to a clip
        assert not any(a < at < b or (at == b and side == "end") or (at == a and side == "start") for a, b in trims)
    assert any(r["clip"] == "S11" and r["edge"] == "whole" for r in rows)


def test_a_clip_the_sliver_before_it_now_runs_into_is_one_take_with_it():
    """Task 10 (a 29.97 fps Zendaya, inside "know what's funny"): S16 ends inside a word, S17 is two frames stepping
    back, S18 goes on 2 frames after S16's end. S17 goes and S16 plays on through S17's place -- to exactly where S18
    starts: one take. S18's start was still taken for a cut inside the word and moved 4 frames later: A1 jumped there
    (the run's hard speech check failed). Now S18 starts where S16 now ends; no cut is left inside the word."""
    _, sm = three()                                                         # "there" 2.4-3.0
    f = float(FPS)
    s16 = SP.Piece("S16", 0, 60, 1.70 * 60, 1.0)                            # RAW 1.70-2.70: ends inside "there"
    s17 = SP.Piece("S17", 60, 62, 2.65 * 60, 1.0)                           # RAW 2.65-2.683: two frames back
    s18 = SP.Piece("S18", 62, 122, 2.70 * 60 + 2, 1.0)                      # RAW 2.733-3.733: 2 frames on from S16
    trims, inserts, rows, _ = SP.snap_edits([s16, s17, s18], sm, FPS, PA, PB, {0, 60, 62, 122})
    assert (60, 62) in trims                                                # the sliver goes
    s16_end = 2.70 + sum(n for at, n, side, _ in inserts if at == 60 and side == "end") / f
    assert s16_end == pytest.approx(2.70 + 2 / f)                           # S16 plays on through S17's place ...
    assert not any(r["clip"] == "S18" and r["edge"] in ("start", "whole") for r in rows)   # ... and S18 goes on
    assert not any(t[0] == 62 for t in trims) and not any(i[0] == 62 and i[2] == "start" for i in inserts)


def test_an_audio_edge_next_to_a_muted_piece_plays_on_into_the_silence_or_stops_before_the_word():
    """Spider-Man S31: A1 ended inside "Spider-Man." where V1 plays on (the next piece is muted: a cutaway over music).
    The A1 edge alone moves (a slide: the picture is untouched): on to the end of the word when the silence after it
    is long enough, else back before the word."""
    _, sm = three()
    f = float(FPS)
    p = SP.Piece("S31", 0, 60, 30.0, 1.0)                                   # RAW 0.5-1.5: ends inside "hello"
    q = SP.Piece("S33", 90, 150, 300.0, 1.0)                                # A1 resumes 0.5 s later (V1 never cut)
    *_, shifts = SP.snap_edits([p, q], sm, FPS, PA, PB, {0, 150})
    slides = [(at, d) for at, d, *kind in shifts if kind]
    assert len(slides) == 1 and slides[0][0] == 60                          # plays on into the silence ...
    assert abs(slides[0][1] - (1.6 + PA - 1.5) * f) <= TOL * f              # ... to the end of "hello" + --pad-after
    _, sm2 = three()
    p = SP.Piece("S31", 0, 150, 30.0, 1.0)                                  # RAW 0.5-3.0: ends inside "there"
    q = SP.Piece("S33", 155, 200, 500.0, 1.0)                               # only 5 frames of silence after it
    *_, shifts = SP.snap_edits([p, q], sm2, FPS, PA, PB, {0, 200})
    slides = [(at, d) for at, d, *kind in shifts if kind]
    assert len(slides) == 1 and slides[0][0] == 150 and slides[0][1] < 0    # stops before "there": after "hello"
    assert (150 + slides[0][1]) / f + 0.5 == pytest.approx(1.6 + PA, abs=TOL)


# ---------------------------------------------------------------------------------------------
# words that run together (task 4): a cut moves to the nearest end of a WORD, never past a run of words
# ---------------------------------------------------------------------------------------------

def test_words_that_run_together_get_a_gap_at_their_boundary():
    """One continuous sound, four words said with no pause: the speech map has a gap at each boundary between the
    (aligned) words, the quietest point there -- so a cut inside it moves at most to the end of one word."""
    y = room()
    tone(y, 1.0, 2.6)                                      # "kids right and I" -- no quiet between the words
    for c in (1.4, 1.8, 2.2):                              # each boundary a little softer
        n = int(c * SR)
        y[n - 160:n + 160] *= 0.35
    ws = words(("kids", 1.0, 1.4), ("right", 1.4, 1.8), ("and", 1.8, 2.2), ("I", 2.2, 2.6))
    sm = SP.speech_map(y, SR, S.Settings(), ws)
    starts = [round(s.s0, 2) for s in sm.sounds if s.speech]
    assert starts == pytest.approx([1.0, 1.4, 1.8, 2.2], abs=0.03)
    # a clip the competitor ended inside "right" (it played 3/4 of it) plays on to its end -- not to "I"
    end = SP.end_at(sm, 1.7, 0.5, PA, PB)
    assert 1.8 <= end <= 1.8 + PA + TOL
    # ... and one ended just after "right" began (a tenth of it) stops before it: "kids" was its last word
    assert SP.end_at(sm, 1.44, 0.5, PA, PB) <= 1.4 + TOL
    # a clip starting inside "and" (a quarter of it left) starts after it; inside its first half, before it
    assert SP.start_at(sm, 2.12, 3.0, PA, PB) >= 2.2 - TOL
    assert SP.start_at(sm, 1.9, 3.0, PA, PB) <= 1.8 + TOL


def test_an_audio_line_jumping_far_inside_speech_where_the_picture_runs_on_takes_its_own_sound():
    """Task 5 (the thorough Deadpool S09): the picture runs on through frame 100, but A1 jumps there from inside
    "there" to another take 1 s on (the competitor's J cut: its sound switches before its picture) -- too far to
    shift. The audio line takes its picture's own sound, so A1 plays on in one take and cuts where V1 cuts (140),
    in the quiet."""
    _, sm = three()
    a = SP.Piece("A", 0, 100, 2.5 * 60 - 100, 1.0, v_off=0.0)                 # ends at RAW 2.5, inside "there"
    b = SP.Piece("B", 100, 140, 3.5 * 60, 1.0, shiftable=True, v_off=-1.0)     # its picture: RAW 2.5 on
    c = SP.Piece("C", 140, 200, 4.6 * 60, 1.0, v_off=0.0)
    _, _, rows, shifts = SP.snap_edits([a, b, c], sm, FPS, PA, PB, v1_cuts={0, 140, 200})
    assert (100, -60) in shifts and any(r["edge"] == "audio line" and r["at"] == 100 for r in rows)
    assert not any(r["at"] == 100 and r["edge"] in ("start", "end") for r in rows)     # no cut at 100 any more
    near = SP.Piece("B", 100, 140, 2.5 * 60 + 3, 1.0, shiftable=True, v_off=-0.05)  # a little: _shift_jumps
    _, _, _, shifts = SP.snap_edits([a, near, c], sm, FPS, PA, PB, v1_cuts={0, 140, 200})
    assert (100, -60) not in shifts and len(shifts) == 1
