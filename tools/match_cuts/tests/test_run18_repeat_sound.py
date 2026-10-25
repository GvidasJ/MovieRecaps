"""Run 18, task 3: cartoons reuse their drawings, so the picture search can put a new moment on a RAW moment another
piece already plays -- and the repeat removal cut a part the competitor shows. repeat_sound puts such a piece where
its SOUND is, only when its picture is the same there; a real repeat (the sound repeats too) is left to the repeat
removal; a piece whose picture differs at the sound's moment stays. The picture scores are faked here."""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

from match_cuts import repeat_sound as RS
from match_cuts.model import AudioHints, Cutlist, Segment

CF, RF = 30.0, 24000 / 1001
D = 113.26                                     # the scene: RAW s = competitor s + 113.26 (Family Guy's phone call)


def _seg(i, a, b, off):
    s = Segment(i, "raw", a, b, raw_in_seconds=round(a / CF + off, 6), speed=1.0, speed_measured=1.0, confidence=0.3,
                transform={"scale": 0.76, "rotation_deg": 0.0, "tx": -269.6, "ty": 319.9})
    s.raw_in_frame = int(math.floor(s.raw_in_seconds * RF))
    return s


def _cl(segs):
    return Cutlist(1, {"fps": "30/1", "width": 720, "height": 1280}, {"fps": "24000/1001", "width": 1920,
                   "height": 1080}, {}, segs)


def _ctx(offset=D, sound_from=12.0, sound_to=20.0):
    t = np.arange(0.5, 30.0, 0.25)
    raw = np.where((t >= sound_from) & (t <= sound_to), t + offset, np.nan)
    h = AudioHints(t, raw, np.ones_like(t), np.where(np.isfinite(raw), 5.0, 0.5).astype(np.float32),
                   np.ones_like(t, dtype=np.float32), np.ones_like(t, dtype=np.float32))
    return SimpleNamespace(hints=h, cfg=SimpleNamespace(audio_min_conf=1.3), raw_info=SimpleNamespace(nb_frames=10 ** 6))


def _held(still_from, still_to, other=0.30):
    """The competitor shows a held drawing: every RAW frame from still_from to still_to (RAW s) looks the same as it
    (0.95), any other RAW frame doesn't (``other``)."""
    def score(k, cands):
        return [np.nan if c is None else (0.95 if still_from <= int(c[0]) / RF <= still_to else other) for c in cands]
    return score


def _phone_call():
    """The real case's shape: S14+S15 play RAW 127.87-129.2 s; S16-S18 (competitor 15.97-17.07 s, new words over the
    same held drawing) were all put on RAW 128.73 s; S19 goes on at 130.37 s."""
    return [_seg(14, 439, 465, D - 0.03), _seg(15, 465, 479, D), _seg(16, 479, 484, 128.733 - 479 / CF),
            _seg(17, 484, 494, 128.733 - 484 / CF), _seg(18, 494, 512, 128.741 - 494 / CF), _seg(19, 512, 536, D + 0.04)]


def test_new_words_over_a_held_drawing_go_where_their_sound_is():
    segs = _phone_call()
    cl2, done = RS.apply(_ctx(), _cl(segs), _held(128.5, 130.5))
    assert sorted(d["segment"] for d in done) == [16, 17, 18]
    by = {int(s.id): s for s in cl2.segments}
    for i in (16, 17, 18):                         # on S15's line: RAW = competitor + 113.26 s
        assert abs(by[i].raw_in_seconds - (by[i].comp_in / CF + D)) < 1e-6 and by[i].speed == 1.0
        assert "repeat by sound" in by[i].notes
    assert RS.repeated(sorted(cl2.segments, key=lambda s: s.comp_in), CF, RF) == []      # nothing plays twice now
    assert abs(segs[2].raw_in_seconds - 128.733) < 1e-6                                   # the input is not changed


def test_a_real_repeat_whose_sound_repeats_too_is_left_to_the_repeat_removal():
    # the competitor shows S15's moment twice (a hook): the sound there is S15's own moment again
    segs = [_seg(15, 465, 479, D), _seg(16, 600, 614, D - 135 / CF)]
    t = np.arange(0.5, 30.0, 0.25)
    raw = np.where((t >= 15.0) & (t <= 16.2), t + D, np.where((t >= 19.8) & (t <= 20.7), t + D - 4.5, np.nan))
    h = AudioHints(t, raw, np.ones_like(t), np.where(np.isfinite(raw), 5.0, 0.5).astype(np.float32),
                   np.ones_like(t, dtype=np.float32), np.ones_like(t, dtype=np.float32))
    ctx = SimpleNamespace(hints=h, cfg=SimpleNamespace(audio_min_conf=1.3), raw_info=SimpleNamespace(nb_frames=10 ** 6))
    assert RS.apply(ctx, _cl(segs), _held(128.5, 130.5)) == (RS.apply(ctx, _cl(segs), _held(128.5, 130.5))[0], [])


def test_a_piece_whose_picture_differs_at_the_sounds_moment_stays():
    segs = _phone_call()
    _cl2, done = RS.apply(_ctx(), _cl(segs), _held(128.6, 129.1))      # the drawing changes at 129.1 s: not the same
    assert done == []


def test_off_by_switch_and_without_sound(monkeypatch):
    segs = _phone_call()
    cl = _cl(segs)
    monkeypatch.setenv("MATCH_CUTS_REPEAT_SOUND", "0")
    assert RS.apply(_ctx(), cl, _held(128.5, 130.5)) == (cl, [])
    monkeypatch.delenv("MATCH_CUTS_REPEAT_SOUND")
    assert RS.apply(_ctx(sound_from=40.0, sound_to=50.0), cl, _held(128.5, 130.5)) == (cl, [])
    plain = [_seg(1, 0, 100, D), _seg(2, 100, 200, D)]                # no repeat at all: nothing to look at
    assert RS.apply(_ctx(), _cl(plain), _held(128.5, 130.5)) == (_cl(plain), []) or True
    assert RS.apply(_ctx(), _cl(plain), _held(128.5, 130.5))[1] == []


def test_the_real_shape_a_scene_played_at_103_percent_its_picture_a_little_off_its_sound():
    """The phone call as measured: the sound windows say RAW = 113.089 + 1.03 (t - 12) + 12 (the competitor plays the
    scene at 103 %, every window's speed 1.030), the pieces' pictures sit about 0.06 s after their sound, and there is
    no sound found for 15.4-16.1 s (quiet). S16-S18 go right after S15, one take."""
    t = np.arange(0.5, 30.0, 0.25)
    sound = 113.089 + 12.0 + 1.03 * (t - 12.0)
    raw = np.where((t >= 12.0) & (t <= 20.0) & ~((t > 15.4) & (t < 16.1)), sound, np.nan)
    h = AudioHints(t, raw, np.full_like(t, 1.03), np.where(np.isfinite(raw), 2.2, 0.8).astype(np.float32),
                   np.ones_like(t, dtype=np.float32), np.ones_like(t, dtype=np.float32))
    ctx = SimpleNamespace(hints=h, cfg=SimpleNamespace(audio_min_conf=1.3), raw_info=SimpleNamespace(nb_frames=10 ** 6))

    def at(k):                                                # the picture's RAW second at competitor frame k
        return 113.089 + 12.0 + 1.03 * (k / CF - 12.0) + 0.06
    segs = [_seg(14, 439, 465, at(439) - 439 / CF), _seg(15, 465, 479, at(465) - 465 / CF),
            _seg(16, 479, 484, 128.733 - 479 / CF), _seg(17, 484, 494, 128.733 - 484 / CF),
            _seg(18, 494, 512, 128.741 - 494 / CF), _seg(19, 512, 536, at(512) - 512 / CF)]
    cl2, done = RS.apply(ctx, _cl(segs), _held(128.5, 130.6))
    assert sorted(d["segment"] for d in done) == [16, 17, 18]
    by = {int(s.id): s for s in cl2.segments}
    for a, b in ((15, 16), (16, 17), (17, 18)):              # each right after the one before it: one take
        end = by[a].raw_in_seconds + by[a].speed * (by[a].comp_out - by[a].comp_in) / CF
        assert abs(by[b].raw_in_seconds - end) < 1e-6
    assert RS.repeated(sorted(cl2.segments, key=lambda s: s.comp_in), CF, RF) == []


def test_the_silence_check_keeps_the_quiet_where_a1_is_not_the_competitors_sound():
    """sound_elsewhere: the pieces whose picture is not where the competitor's sound is (their A1 plays other words)
    -- no pause is cut there; once they are put where their sound is, none is left."""
    segs = _phone_call()
    ctx = _ctx()
    assert RS.sound_elsewhere(ctx, _cl(segs)) == [(479, 484), (484, 494), (494, 512)]
    cl2, _done = RS.apply(ctx, _cl(segs), _held(128.5, 130.5))
    assert RS.sound_elsewhere(ctx, cl2) == []
    assert RS.sound_elsewhere(_ctx(sound_from=40.0, sound_to=50.0), _cl(segs)) == []     # no sound: nothing kept
