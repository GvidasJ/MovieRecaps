"""28 Oct: no song is added on any channel unless MOVIERECAPS_MUSIC=1.

Run: python -m pytest tools/app/tests/test_music_off_oct28.py"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import run as R  # noqa: E402


def test_no_song_by_default(monkeypatch):
    monkeypatch.delenv("MOVIERECAPS_MUSIC", raising=False)
    for ch in ("SceneProof", "MikeMemesYou", "Flick707", "LouLaughs"):
        assert R.channel_music(SimpleNamespace(channel=ch)) == []
