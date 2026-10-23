"""Day 16, task 8: (1) refine does not rescue-search competitor frames with no picture track inside a stretch where
the sound search found no candidate in the RAW (another video's sound); (2) a short slow "reverse" over a few RAW
frames (the competitor holding them, and zooming: the 12 Year Old S08) is a still of the RAW frame its frames match
most -- never played backwards."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

import numpy as np

from match_cuts import refine as RF
from match_cuts import segment as SG
from match_cuts.model import AudioHints, Status


def _ref(hints, n=240, fps=24.0):
    r = RF._Refiner.__new__(RF._Refiner)            # (only what _off_sound reads)
    r.hints, r.N, r.comp = hints, n, SimpleNamespace(fps=Fraction(fps))
    r._off_sound_mask = None
    return r


def _hints(nan_from, nan_to, total_s=10.0, hop=0.25):
    ct = np.arange(0.5, total_s, hop)
    raw = np.where((ct >= nan_from) & (ct <= nan_to), np.nan, ct + 100.0)
    conf = np.where(np.isnan(raw), 0.0, 3.0).astype(np.float32)
    z = np.zeros(len(ct), np.float32)
    return AudioHints(ct, raw, np.ones(len(ct)), conf, z, z, 1.0, hop)


def test_a_long_stretch_without_any_raw_sound_is_marked_and_a_short_one_is_not():
    m = _ref(_hints(4.0, 7.0))._off_sound()
    assert m[5 * 24] and m[int(6.5 * 24)] and not m[24] and not m[9 * 24]
    short = _ref(_hints(4.0, 4.5))._off_sound()                      # 1.5 s with the window: under 2 s
    assert not short.any()
    assert not _ref(None)._off_sound().any()


def test_the_switch_and_the_environment_turn_it_off(monkeypatch):
    monkeypatch.setenv("MATCH_CUTS_RESCUE_ALL", "1")
    assert not _ref(_hints(4.0, 7.0))._off_sound().any()
    monkeypatch.delenv("MATCH_CUTS_RESCUE_ALL")
    monkeypatch.setattr(RF, "RESCUE_SKIP_OFF_SOUND", False)
    assert not _ref(_hints(4.0, 7.0))._off_sound().any()


def test_a_short_slow_reverse_over_a_few_frames_is_a_hold():
    cf, rf = Fraction(30000, 1001), Fraction(24000, 1001)
    assert SG._held(-0.30, 18, cf, rf)                          # the 12 Year Old S08: 0.6 s, ~4 RAW frames
    assert not SG._held(-1.0, 18, cf, rf)                       # a real reverse
    assert not SG._held(-0.30, 90, cf, rf)                      # 3 s long: it plays backwards visibly
    assert not SG._held(0.30, 18, cf, rf) and not SG._held(-0.45, 30, cf, rf)    # forward / 10.8 RAW frames


def test_the_still_is_the_raw_frame_matched_most(monkeypatch):
    F = SimpleNamespace(raw=np.array([7203, 7203, 7202, 7202, 7202, 7201, -1]),
                        status=np.array([Status.MATCH] * 6 + [Status.NONE if hasattr(Status, "NONE") else 0]))
    assert SG._held_frame(F, 0, 7, 7203) == 7202
    G = SimpleNamespace(raw=np.array([-1, -1]), status=np.array([0, 0]))
    assert SG._held_frame(G, 0, 2, 7203) == 7203
    monkeypatch.setenv("MATCH_CUTS_NO_STILL", "1")
    assert not SG._held(-0.30, 18, Fraction(30000, 1001), Fraction(24000, 1001))
