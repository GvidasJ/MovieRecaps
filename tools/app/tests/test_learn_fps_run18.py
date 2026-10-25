"""Run 18, task 5: Learn compares your fixed project with the tool's edit BY TIME -- your sequence at 60 fps over the
tool's 29.97 fps is not a fix, and a difference under one frame of the slower rate is not counted (captions, cuts);
a real change still is. The clips and captions are made here (no Premiere project).

Run: python -m pytest tools/app/tests/test_learn_fps_run18.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from match_cuts.learn import Clip  # noqa: E402
from mr_app.core import learn_check as LC  # noqa: E402

T30 = 1001 / 30000                 # one frame of 29.97
T60 = 1 / 60


def _on(grid: float, t: float) -> float:
    return round(t / grid) * grid


def test_one_frame_of_the_slower_rate():
    assert abs(LC.slower_frame_s(30000 / 1001, 60.0) - T30) < 1e-9
    assert abs(LC.slower_frame_s(None, 60.0) - T60) < 1e-9 and LC.slower_frame_s(None, None) == 0.0


def test_your_60_fps_sequence_is_no_fix_but_a_real_change_still_counts():
    # the same edit: the tool's on the 29.97 grid, yours on the 60 fps grid (every time up to half a frame of 29.97 off)
    raw0 = 289.322
    tool = [Clip(0.0, _on(T30, 1.435), raw0, raw0 + _on(T30, 1.435)),
            Clip(_on(T30, 1.435), _on(T30, 3.17), 291.491, 291.491 + _on(T30, 3.17) - _on(T30, 1.435))]
    user = [Clip(0.0, _on(T60, 1.43), raw0, raw0 + _on(T60, 1.43)),
            Clip(_on(T60, 1.43), _on(T60, 3.17), 291.483, 291.483 + _on(T60, 3.17) - _on(T60, 1.43))]
    cc = LC.cut_changes(tool, user, LC.slower_frame_s(30000 / 1001, 60.0))
    assert cc["moved"] == 0 and cc["trimmed"] == 0 and cc["same"] == 1          # the 8 ms move: no trim
    caps_tool = [(_on(T30, 0.2), _on(T30, 1.0), "hello there"), (_on(T30, 1.6), _on(T30, 2.4), "how are you"),
                 (_on(T30, 2.5), _on(T30, 3.1), "fine thanks")]
    caps_user = [(_on(T60, 0.2 + 0.016), _on(T60, 1.0 - 0.016), "hello there"),      # under a frame: not counted
                 (_on(T60, 1.6), _on(T60, 2.4), "how are you"),
                 (_on(T60, 2.5 + 0.2), _on(T60, 3.1), "fine thanks")]                 # 0.2 s later: a real retime
    tol = max(LC.TIMING_S, LC.slower_frame_s(30000 / 1001, 60.0) + 1e-6)
    out = LC.caption_changes(caps_tool, caps_user, tool, user, None, tol)
    assert out["timing"] == 1 and out["changed"] == 1
