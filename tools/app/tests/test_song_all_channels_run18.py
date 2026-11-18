"""Run 18, task 2: MikeMemesYou, Flick707 (removed 31 Oct) and LouLaughs get SceneProof's song on A2 too (the same file, from 0.52 s,
split at 12.32 s, Clip Gain +5 / -1 dB, Volume 8.5 dB), and the song clips get your CorrectVolume's Hard Limiter +
Amplify in the saved project (core.song_gain, the same way as A1's chain).

Run: python -m pytest tools/app/tests/test_song_all_channels_run18.py"""
from __future__ import annotations

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import channels as C  # noqa: E402


@pytest.fixture(autouse=True)
def _music_on(monkeypatch):
    """28 Oct: no song by default; these tests check the song switched on."""
    monkeypatch.setenv("MOVIERECAPS_MUSIC", "1")



@pytest.fixture(autouse=True)
def _a1_chain_on(monkeypatch):
    """27 Oct: A1's chain is OFF by default (A1 left at 0 dB); these tests check it switched on."""
    monkeypatch.setenv("MOVIERECAPS_A1_CHAIN", "1")


SONG = "One Piece OST - Mother Sea (First Part) Extended Version.mp3"


# 18 Nov: Flick707 was removed on 31 Oct (your call, c93409c: its folder is in work/old_templates, not a channel any
# more) -- the song's settings are kept by the three channels that had it; the channels made after "no song on any
# channel" (28 Oct, bf17aa7) -- MikeCuts, DanCuts, TOMSCUTS -- never got one
WITH_SONG = ("SceneProof", "MikeMemesYou", "LouLaughs")
NO_SONG = ("MikeCuts", "DanCuts", "TOMSCUTS")


def test_every_channel_has_sceneprofs_song_with_the_same_values():
    chans = {c.name: c for c in C.channels()}
    assert set(WITH_SONG) | set(NO_SONG) == set(chans) and "Flick707" not in chans
    files = set()
    for name in WITH_SONG:
        m = chans[name].music
        assert m is not None, name
        assert m.file.name == SONG and m.file.is_file()
        files.add(m.file.resolve())
        assert (m.start_s, m.split_s, m.gains_db, m.volume_db) == (0.52, 12.32, (5.0, -1.0), 8.5)
        assert m.args()[:2] == ["--music", str(m.file)] and m.args()[-2:] == ["--music-volume", "8.5"]
    assert len(files) == 1                                     # one file: SceneProof's (no copies)
    for name in NO_SONG:
        assert chans[name].music is None, name


def test_the_run_command_of_a_job_carries_its_channels_song(monkeypatch):
    from types import SimpleNamespace
    from mr_app.core import run as R
    chans = {c.name: c for c in C.channels()}
    monkeypatch.setattr("mr_app.core.flow.find_channel", lambda name: chans.get(name))
    for name in ("MikeMemesYou", "LouLaughs", "SceneProof"):
        args = R.channel_music(SimpleNamespace(channel=name))
        assert args[0] == "--music" and Path(args[1]).name == SONG
    for name in NO_SONG + ("Flick707", ""):                    # no song set / not a channel: no --music, no error
        assert R.channel_music(SimpleNamespace(channel=name)) == []
    monkeypatch.setenv("MOVIERECAPS_MUSIC", "0")               # 28 Oct: the switch off -- no song on any channel
    assert R.channel_music(SimpleNamespace(channel="SceneProof")) == []
