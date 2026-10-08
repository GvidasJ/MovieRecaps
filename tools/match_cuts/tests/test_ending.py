"""Your ending (loop item 3): the edit stops --pad-after after the sound holding its last word -- the laughter,
reaction or outro the competitor plays after its last line goes like a trailing silence. video017 ends 0.03 s after
"Sorry", video018 0.04 s after the sound holding "insurance", video3 after "very lonely!" (the tool played 1.5, 2.1
and 4.0 s more); on 7 of 8 answer keys you ended before the tool did.
"""
from __future__ import annotations

import types
from fractions import Fraction

import numpy as np

from match_cuts import silence as S, speech as SP
from match_cuts.config import Config
from match_cuts.model import Cutlist, Segment

SR = 16000
FPS = Fraction(60)


def room(dur: float = 6.0) -> np.ndarray:
    return (0.002 * np.random.default_rng(0).standard_normal(int(dur * SR))).astype(np.float32)     # ~-54 dBFS


def tone(y: np.ndarray, a: float, b: float, amp: float = 0.2, step: float = 0.3) -> np.ndarray:
    n0, n1 = int(a * SR), int(b * SR)
    y[n0:n1] += (amp * np.sin(np.arange(n1 - n0) * step)).astype(np.float32)
    return y


def words(*ws) -> list:
    return [types.SimpleNamespace(text=t, raw=t, start=a, end=b) for t, a, b in ws]


WORDS = (("hello", 0.5, 1.1), ("there", 1.6, 2.2), ("again", 2.8, 3.4))


def one_clip(seconds: float = 6.0) -> Cutlist:
    """The competitor plays RAW 0..seconds as one clip (30 fps competitor, 60 fps RAW)."""
    n = int(seconds * 30)
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=n, raw_in_seconds=0.0, raw_in_frame=0, speed=1.0,
                  raw_in_interval=[0.0, 0.0001], confidence=0.97,
                  transform={"scale": 0.5625, "rotation_deg": 0.0, "tx": 0.0, "ty": 0.0})
    comp = {"file": "media/competitor_ref.mp4", "width": 1080, "height": 1920, "fps": "30/1", "frames": n}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080, "fps": "60/1",
           "frames": int(seconds * 60), "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "fullscreen", "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, [seg])


def test_the_edit_stops_after_the_sound_holding_its_last_word(tmp_path):
    """Three words, then a laugh (voiced, no words) from 4.0 to 5.2 s and room tone to 6.0 s: the competitor plays
    all of it. The edit ends --pad-after after "again" -- the laugh is cut with the room tone after it (it is no
    silence: before, it stayed)."""
    from match_cuts import export_xml_edl as ex
    y = room()
    for _, a, b in WORDS:
        tone(y, a, b)
    tone(y, 4.0, 5.2, amp=0.15, step=0.21)                       # the laugh: loud and voiced, no words
    sm = SP.speech_map(y, SR, S.Settings(), words(*WORDS))
    assert any(s.speech and s.s0 >= 3.9 for s in sm.sounds)    # the laugh counts as speech in the map
    cfg = Config(premiere=True, out_dir=str(tmp_path))
    cl = one_clip()
    plan = S.plan_premiere(cl, y, SR, cfg, None, sm)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    a1 = ex.parse_premiere_xml(xml)["audio"]
    last = max(a1, key=lambda it: it["end"])
    # RAW: the end of "again" (a sound edge is known to half a 50 ms loudness window) + --pad-after
    assert abs(last["out"] / 60 - (3.4 + cfg.pad_after)) <= 0.04 + 1 / 60, last
    assert plan["levels"]["ending"]["from_s"] < plan["speech_s"]


def test_an_edit_that_ends_on_a_word_keeps_its_end():
    y = room(4.0)
    for _, a, b in WORDS:
        tone(y, a, b)
    sm = SP.speech_map(y, SR, S.Settings(), words(*WORDS))
    plan = S.plan_premiere(one_clip(3.5), y, SR, Config(premiere=True), None, sm)
    assert "ending" not in plan["levels"]


def test_the_tail_is_never_cut_into_a_protected_range():
    st = S.Settings.from_cfg(Config(premiere=True))
    ws = words(("again", 2.8, 3.4))
    quiet = [(3.45, 4.0), (5.2, 6.0)]
    t = S.tail_after_last_word(ws, quiet, 360, FPS, st)
    assert t is not None and t.b == 360 and abs(t.a / 60 - (3.45 + st.pad_after)) <= 1 / 60
    assert S.tail_after_last_word(ws, quiet, 360, FPS, st, protect=[(300, 330)]) is None    # another video there
    assert S.tail_after_last_word([], quiet, 360, FPS, st) is None
