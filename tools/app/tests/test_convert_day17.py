"""Day 17, task 6: the Convert check's rule from 295a8d7 -- sound that ran past the picture's last frame may end with
the picture in the copy (up to 1.5 s): OK; sound cut short INSIDE the picture: still a stop. Key & Peele S05E11 had
0.84 s of sound after its last frame (1277.48 s of sound, a 1276.65 s picture, the copy's sound 1276.64 s).

Run: python -m pytest tools/app/tests/test_convert_day17.py"""
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import convert as C  # noqa: E402

FPS = Fraction(24000, 1001)
FRAMES = 30610                                     # 1276.65 s at 23.976 fps


def _info(audio_s: float, video_s: float) -> C.MediaInfo:
    return C.MediaInfo(path=Path("C:/v/kp.mkv"), container="mkv", vcodec="h264", profile="High", pix_fmt="yuv420p",
                       bit_depth=8, width=1920, height=1080, fps=FPS, frames=FRAMES, duration=video_s, acodec="aac",
                       channels=2, audio_duration=audio_s, audio=[{"index": 1, "codec": "aac", "channels": 2}],
                       avg_fps=FPS)


def _check(src_audio: float, out_audio: float, video_s: float = FRAMES / float(FPS)):
    t = C.Timing(FRAMES, video_s, True, 0.0)
    return C.check(_info(src_audio, video_s), _info(out_audio, video_s), t, t)


def test_sound_cut_only_after_the_last_frame_is_ok():                 # the real Key & Peele numbers
    ok, _nums, probs = _check(1277.48, 1276.64)
    assert ok, probs
    assert not any("sound is" in p for p in probs)


def test_sound_cut_inside_the_picture_is_still_a_stop():
    ok, _nums, probs = _check(1277.48, 1270.00)                       # 6.6 s of the picture without its sound
    assert not ok and any("the sound is 1270.00 s long instead of 1277.48 s" in p for p in probs)
    ok2, _n, probs2 = _check(1276.65, 1275.90)                        # ended with the picture, cut 0.75 s inside it
    assert not ok2 and any("sound is" in p for p in probs2)


def test_more_than_1_5_s_after_the_picture_is_still_said():
    ok, _nums, probs = _check(1279.00, 1276.64)                       # 2.35 s gone: more than the rule allows
    assert not ok and any("sound is" in p for p in probs)


def test_a_longer_copy_sound_is_still_said():
    ok, _nums, probs = _check(1276.64, 1277.48)
    assert not ok and any("sound is" in p for p in probs)
