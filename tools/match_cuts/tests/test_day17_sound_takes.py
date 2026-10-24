"""Day 17, task 7: look-alike scenes -- the sound decides (sound_takes). Where the competitor's sound plays one RAW
take straight through, the pieces the picture search put on look-alike RAW frames (repeats, flashes, uncertain
stretches) go on that take's line when the line's picture scores as well; a real cut the line doesn't fit stays; the
take reaches on past the sound while the picture fits. The picture scores are faked here."""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

from match_cuts import sound_takes as ST
from match_cuts.model import AudioHints, Cutlist, Segment

CF, RF = 30000 / 1001, 24000 / 1001
D = 100.0                                        # the take: RAW s = competitor s + 100


def _seg(i, a, b, off, typ="raw", conf=0.6):
    s = Segment(i, typ, a, b, raw_in_seconds=None if typ != "raw" else round(a / CF + off, 6), speed=1.0,
                speed_measured=1.0, confidence=conf,
                transform={"scale": 0.77, "rotation_deg": 0.0, "tx": -199.0, "ty": 799.0} if typ == "raw" else None)
    if typ == "raw":
        s.raw_in_frame = int(math.floor(s.raw_in_seconds * RF))
    return s


def _cl(segs):
    return Cutlist(1, {"fps": "30000/1001", "width": 1080, "height": 1920}, {"fps": "24000/1001", "width": 1920,
                   "height": 800}, {}, segs)


def _ctx(sound_from=4.0, sound_to=10.0):
    t = np.arange(0.5, 12.0, 0.25)
    raw = np.where((t >= sound_from) & (t <= sound_to), t + D - 0.14, np.nan)      # the sound 0.14 s off the picture
    h = AudioHints(t, raw, np.ones_like(t), np.where(np.isfinite(raw), 5.0, 0.5).astype(np.float32),
                   np.ones_like(t, dtype=np.float32), np.ones_like(t, dtype=np.float32))
    return SimpleNamespace(hints=h, cfg=SimpleNamespace(audio_min_conf=1.3, sound_takes=True),
                           raw_info=SimpleNamespace(nb_frames=100000))


def _scorer(cut_from: int = 10 ** 9):
    """0.90 for the take's own RAW frame at every competitor frame (and its look-alikes 0.89-0.90), 0.30 for the
    take's line after a real cut (competitor frame >= cut_from: another shot)."""
    def score(k, cands):
        out = []
        for c in cands:
            if c is None:
                out.append(np.nan)
                continue
            j = int(c[0])
            line = int(math.floor((k / CF + D) * RF + 1e-6))
            if k >= cut_from:
                out.append(0.30 if abs(j - line) <= 1 else 0.90)
            else:
                out.append(0.90 if abs(j - line) <= 1 else 0.89)
        return out
    return score


def test_look_alike_pieces_go_on_the_sounds_take_and_never_repeat(monkeypatch):
    monkeypatch.setenv("MATCH_CUTS_SOUND_TAKES", "1")
    segs = [_seg(1, 0, 150, D),                  # before the sound: on the line already
            _seg(2, 150, 160, D + 3.2),          # a look-alike flash
            _seg(3, 160, 200, D),
            _seg(4, 200, 230, D - 1.5),          # a repeat of an earlier RAW moment
            _seg(5, 230, 260, 0.0, "uncertain"),  # the picture didn't guess
            _seg(6, 260, 300, D)]
    cl2, done = ST.apply(_ctx(), _cl(segs), _scorer())
    assert {d["segment"] for d in done} == {2, 4, 5}
    for s in cl2.segments:
        assert s.type == "raw" and abs(s.raw_in_seconds - (s.comp_in / CF + D)) < 1e-6 and s.speed == 1.0
    starts = [s.raw_in_frame for s in sorted(cl2.segments, key=lambda s: s.comp_in)]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)               # never a RAW moment twice
    assert cl2.segments[4].transform == segs[3].transform or cl2.segments[4].transform   # the take's framing
    assert all(s.raw_in_seconds is None for s in segs if s.type == "uncertain")        # the input is not changed


def test_a_real_cut_the_line_does_not_fit_stays(monkeypatch):
    monkeypatch.setenv("MATCH_CUTS_SOUND_TAKES", "1")
    segs = [_seg(1, 0, 150, D), _seg(2, 150, 165, D + 3.0), _seg(3, 165, 240, D),
            _seg(4, 240, 300, 250.0)]            # another shot from frame 240
    _cl2, done = ST.apply(_ctx(), _cl(segs), _scorer(cut_from=240))
    assert {d["segment"] for d in done} == {2}


def test_straight_through_past_the_sound_while_the_picture_fits(monkeypatch):
    monkeypatch.setenv("MATCH_CUTS_SOUND_TAKES", "1")
    segs = [_seg(1, 0, 40, D + 6.0), _seg(2, 40, 90, D - 2.0),     # before the sound (music): look-alikes
            _seg(3, 90, 150, D + 4.0, "raw"), _seg(4, 150, 300, D)]
    cl2, done = ST.apply(_ctx(sound_from=5.0), _cl(segs), _scorer())
    assert {d["segment"] for d in done} == {1, 2, 3}
    assert {d["how"] for d in done} >= {"straight through"}


def test_off_by_switch_and_without_sound(monkeypatch):
    segs = [_seg(1, 0, 150, D), _seg(2, 150, 160, D + 3.2), _seg(3, 160, 300, D)]
    monkeypatch.setenv("MATCH_CUTS_SOUND_TAKES", "0")
    cl = _cl(segs)
    assert ST.apply(_ctx(), cl, _scorer()) == (cl, [])
    monkeypatch.setenv("MATCH_CUTS_SOUND_TAKES", "1")
    quiet = _ctx(sound_from=50.0, sound_to=60.0)
    assert ST.apply(quiet, cl, _scorer()) == (cl, [])
    monkeypatch.delenv("MATCH_CUTS_SOUND_TAKES")
    assert ST.enabled(SimpleNamespace()) is ST.DEFAULT_ON


def test_one_competitor_shot_becomes_one_take_at_its_own_speed(monkeypatch):
    """Step 5: inside one competitor shot, doubted pieces jumping between look-alike RAW moments go on one line --
    here the RAW shown frame for frame (80 %) -- when every piece fits it as well as its own frames."""
    monkeypatch.setenv("MATCH_CUTS_SOUND_TAKES", "1")

    def score(k, cands):
        line = int(math.floor((D + 0.8 * k / CF) * RF + 1e-6))
        return [np.nan if c is None else (0.90 if abs(int(c[0]) - line) <= 1 else 0.89) for c in cands]
    first = _seg(1, 0, 20, D, conf=0.3)
    first.speed = first.speed_measured = 0.8
    segs = [first, _seg(2, 20, 28, D + 2.0, conf=0.2), _seg(3, 28, 60, D - 1.0, conf=0.2),
            _seg(4, 60, 90, 300.0, conf=0.9)]                       # the next competitor shot: another scene
    ctx = _ctx()
    ctx.comp_shots = [60]
    cl2, done = ST.apply(ctx, _cl(segs), score)
    assert {d["segment"] for d in done} == {2, 3} and all(d["speed"] == 0.8 for d in done)
    s2 = next(s for s in cl2.segments if s.id == 2)
    assert abs(s2.raw_in_seconds - (D + 0.8 * 20 / CF)) < 0.5 / RF and s2.speed == 0.8   # (lines on RAW frames)
    assert next(s for s in cl2.segments if s.id == 4).raw_in_seconds == segs[3].raw_in_seconds
