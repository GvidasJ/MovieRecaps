"""17 Nov: the computer is kept awake while the app works (core.awake) -- only a change asks Windows."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import awake  # noqa: E402


def test_only_a_change_asks_windows(monkeypatch):
    asked = []
    monkeypatch.setattr(awake, "_ask", lambda flags: asked.append(flags) or True)
    monkeypatch.setattr(awake, "_on", False)
    monkeypatch.delenv("MOVIERECAPS_KEEP_AWAKE", raising=False)
    assert awake.keep(True) and awake.is_on()
    assert not awake.keep(True) and not awake.keep(True)
    assert awake.keep(False) and not awake.is_on()
    assert not awake.keep(False)
    assert asked == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED, awake.ES_CONTINUOUS]


def test_the_switch_turns_it_off(monkeypatch):
    asked = []
    monkeypatch.setattr(awake, "_ask", lambda flags: asked.append(flags) or True)
    monkeypatch.setattr(awake, "_on", False)
    monkeypatch.setenv("MOVIERECAPS_KEEP_AWAKE", "0")
    assert not awake.keep(True) and not awake.is_on() and asked == []
