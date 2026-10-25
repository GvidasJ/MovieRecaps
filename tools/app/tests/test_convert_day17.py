"""Day 17, task 6: the Convert check's rule from 295a8d7 -- sound that ran past the picture's last frame may end with
the picture in the copy (up to 10 s after it; the copy's sound within 0.25 s of the picture's end): OK; sound cut short INSIDE the picture: still a stop. Key & Peele S05E11 had
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


def test_two_seconds_after_the_picture_is_ok():                       # 25 Oct: "The Smoothest Office Flirt"
    ok, _nums, probs = _check(1279.00, 1276.50)                       # 2.5 s after the picture, the copy's sound 0.15 s
    assert ok, probs                                                  # short of the picture's end (AAC's last frame)


def test_more_than_10_s_after_the_picture_is_still_said():
    ok, _nums, probs = _check(1290.00, 1276.64)                       # 13.4 s gone: more than the rule allows
    assert not ok and any("sound is" in p for p in probs)


def test_a_longer_copy_sound_is_still_said():
    ok, _nums, probs = _check(1276.64, 1277.48)
    assert not ok and any("sound is" in p for p in probs)


def test_run18_the_real_fletch_lives_numbers():
    """Run 18, task 6: the real Convert of 'Fletch.Lives.1989.720p.HDTV.mkv' (25 Oct, on the PC): the picture copied
    whole (136434 of 136434 frames, 5690.56 s both), the sound 5692.704 s -> 5690.40 s (it ran 2.14 s past the last
    frame; the copy's ends 0.16 s before the picture's end) -- passed. The same file with its sound cut inside the
    picture still stops."""
    frames, v = 136434, 5690.56                       # (as measured on the file and its copy)
    t = C.Timing(frames, v, True, 0.0)
    ok, nums, probs = C.check(_info(5692.704, v), _info(5690.40, v), t, t)
    assert ok, probs
    for cut in (5689.50, 5600.00):                    # 1.06 s / 90 s of the picture without its sound
        ok2, _n, probs2 = C.check(_info(5692.704, v), _info(cut, v), t, t)
        assert not ok2 and any(f"the sound is {cut:.2f} s long instead of 5692.70 s" in p for p in probs2)
