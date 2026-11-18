"""18 Nov: a piece that is 4K already is not doubled (only its 60 fps), a hanging Topaz is stopped and tried again, the
edit's scale follows how much bigger the enhanced picture is, and a lock of this computer's own dead app is free."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import machine as M  # noqa: E402


def test_twice_the_size_only_up_to_4k():
    assert E.topaz_scale(1920, 1080) == 2 and E.topaz_scale(1440, 1080) == 2 and E.topaz_scale(720, 480) == 2
    assert E.topaz_scale(1920, 1440) == 2                              # 3840x2880: the 4:3 4K
    assert E.topaz_scale(3840, 2160) == 1 and E.topaz_scale(2560, 1440) == 1
    assert E.topaz_scale(None, None) == 2 and E.topaz_scale(0, 0) == 2


def test_a_4k_piece_gets_apollo_only():
    tz = E.Topaz(ffmpeg=Path("ffmpeg.exe"))
    two = " ".join(E.topaz_command(tz, "a.mp4", "b.mp4", 1.0, 2))
    one = " ".join(E.topaz_command(tz, "a.mp4", "b.mp4", 1.0, 1))
    assert "tvai_up=" in two and "tvai_fi=" in two
    assert "tvai_up=" not in one and "tvai_fi=" in one and "hevc_nvenc" in one


def test_a_hanging_topaz_is_stopped_and_said(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "STALL_S", 1.5)
    calls = []

    def hang(tz, src, dst, vram=1.0, scale=2):
        calls.append(vram)
        return [sys.executable, "-c", "import time; time.sleep(60)"]
    monkeypatch.setattr(E, "topaz_command", hang)
    monkeypatch.setattr(E, "topaz_env", lambda tz: dict(os.environ))
    tz = type("T", (), {"found": True, "why": "", "fake": True, "ffmpeg": "x", "models": None})()
    t0 = time.monotonic()
    with pytest.raises(E.EnhanceError) as e:
        E.run_topaz(tmp_path / "a.mp4", tmp_path / "b.mp4", 100, tz)
    assert E.STALL_TEXT in str(e.value) and calls == [1.0, 0.6, 0.35]   # tried again with less of the card's memory
    assert time.monotonic() - t0 < 30


def test_this_computers_own_dead_lock_is_free_at_once(tmp_path):
    lock = tmp_path / "x_TOENHANCE.lock"

    def make(pid, age=60.0, machine=None, host=None):
        lock.write_text(json.dumps({"token": f"{host or M.host()}:{pid}:1", "computer": host or M.host(),
                                    "machine": machine or M.which(), "share": 0.0}), encoding="utf-8")
        t = time.time() - age
        os.utime(lock, (t, t))
    make(999999)
    assert E.lock_stale(lock)                                          # its app is gone
    make(os.getppid())
    assert not E.lock_stale(lock)                                      # another app that runs has it
    make(999999, age=5)
    assert not E.lock_stale(lock)                                      # just written
    make(999999, machine="pc" if M.which() != "pc" else "laptop")
    assert not E.lock_stale(lock)                                      # the other computer's (both are "Gvidas")
    make(os.getppid(), age=E.LOCK_STALE_S + 60)
    assert E.lock_stale(lock)                                          # the 15 minutes still decide
