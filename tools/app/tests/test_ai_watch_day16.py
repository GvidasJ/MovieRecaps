"""Day 16, task 5: Find source with an AI that watches the clip (core.ai_watch: NexLev, else Algrow) -- its answer
read, cached by the link (never watched twice), the keys never written anywhere; its guess Sure only when checked
(the public lists, your RAW's sound). Fakes only: no network, no credits.

Run: python -m pytest tools/app/tests/test_ai_watch_day16.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import ai_watch as A  # noqa: E402
from mr_app.core import find_source as FS  # noqa: E402

DICTATOR = json.dumps({"spoken_lines": ["You mean your Porsche?", "Yes... the 911."], "on_screen_text": ["911"],
                       "actors": ["Sacha Baron Cohen"], "characters": ["Aladeen"], "setting": "a helicopter",
                       "title": "The Dictator", "year": 2012, "kind": "movie", "season": None, "episode": None,
                       "episode_title": "", "sure": 100, "why": "the helicopter scene"})
AMDAD = json.dumps({"title": "American Dad!", "year": 2007, "kind": "show", "season": 3, "episode": 2,
                    "episode_title": "The American Dad After School Special", "sure": 90, "why": "Stan, Steve"})


@pytest.fixture(autouse=True)
def cache_here(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "_cache_path", lambda: tmp_path / "ai_watch.json")
    monkeypatch.setattr(A, "key", lambda name: "SECRET-" + name)
    yield


def test_the_answer_is_read_and_cached_by_the_link_never_twice(tmp_path):
    calls = []
    w = A.watch("https://www.instagram.com/p/Dd1e2BmyHCX/?igsh=x", nexlev=lambda u: calls.append(u) or
                f"Here it is:\n```json\n{DICTATOR}\n```", algrow=lambda u: pytest.fail("not needed"))
    assert (w.by, w.title, w.year, w.kind, w.sure) == ("NexLev", "The Dictator", 2012, "movie", 100)
    assert w.lines[0] == "You mean your Porsche?" and w.actors == ["Sacha Baron Cohen"] and w.guess() == \
        "The Dictator (2012)"
    again = A.watch("https://instagram.com/reel/Dd1e2BmyHCX", nexlev=lambda u: pytest.fail("watched twice"))
    assert again.title == "The Dictator" and len(calls) == 1                 # the same clip: from the cache
    assert "SECRET" not in (tmp_path / "ai_watch.json").read_text(encoding="utf-8")


def test_algrow_when_nexlev_fails_and_errors_never_hold_a_key():
    def bad(_u):
        raise A.ServiceError("HTTP 500 for SECRET-NEXLEV_API_KEY")
    w = A.watch("https://www.tiktok.com/@x/video/7357048330179841323", nexlev=bad, algrow=lambda u: AMDAD)
    assert w.by == "Algrow" and (w.season, w.episode) == (3, 2) and w.guess() == "American Dad! — S03E02"
    both = A.watch("https://www.youtube.com/shorts/AbCdEf12345", nexlev=bad, algrow=bad)
    assert not both.by and "SECRET" not in both.error and "***" in both.error
    assert A.watch("C:/clips/x.mp4").error.startswith("not a public")
    assert A._scrub("Bearer SECRET-ALGROW_API_KEY") == "Bearer ***"                          # noqa: SLF001
    assert A.key_status() == {"NexLev": "key found", "Algrow": "key found"}


def test_links_are_one_key_per_clip():
    assert A.norm_link("https://www.instagram.com/reel/DczA1q3MTge/?utm=1") == "instagram:DczA1q3MTge" == \
        A.norm_link("https://instagram.com/p/DczA1q3MTge")
    assert A.norm_link("https://www.tiktok.com/@cultureratedpv/video/7357048330179841323?lang=en") == \
        "tiktok:7357048330179841323"
    assert A.norm_link("https://youtu.be/AbCdEf12345") == "youtube:AbCdEf12345" == \
        A.norm_link("https://www.youtube.com/shorts/AbCdEf12345")
    assert A.parse_answer("no json here", "l", "NexLev").error


def _fake_watch(text):
    return lambda link, say=None: A.parse_answer(text, link, "NexLev")


def _clip(monkeypatch, text=""):
    monkeypatch.setattr(FS, "read_clip", lambda s, c=None: FS.Clip(s, "Reel x", "Instagram", 30.0, text))
    monkeypatch.setattr(FS, "_clip_file", lambda *a: None)


def test_sure_only_when_the_public_lists_confirm_the_ais_guess(monkeypatch):
    _clip(monkeypatch)
    monkeypatch.setattr(FS, "online", lambda name, ep=None: FS.Title("The Dictator", 2012, kind="movie"))
    a = FS.find("https://www.instagram.com/p/Dd1e2BmyHCX/", lib=[], watcher=_fake_watch(DICTATOR))
    assert a.verdict == FS.SURE and a.title.text() == "The Dictator (2012)" and "IMDb has The Dictator" in a.how
    assert a.ai["by"] == "NexLev" and a.ai["lines"][0] == "You mean your Porsche?"
    monkeypatch.setattr(FS, "online", lambda name, ep=None: None)                    # not confirmed: Check
    b = FS.find("https://www.instagram.com/p/Dd1e2BmyHCX/", lib=[], watcher=_fake_watch(DICTATOR))
    assert b.verdict == FS.CHECK and "only the AI's guess" in b.how


def test_an_episode_is_sure_only_when_tvmaze_has_the_title_the_ai_named(monkeypatch):
    # day 17: TVmaze having the AI's own episode title is no proof any more (Everybody Hates Chris S03E11 was
    # "Sure" for S03E07): without the proof of core.source_proof an episode is Check
    _clip(monkeypatch)
    monkeypatch.setattr(FS, "online", lambda name, ep=None: FS.Title(
        "American Dad!", 2005, ep[0], ep[1], "The American Dad After School Special", "episode"))
    a = FS.find("https://www.instagram.com/reel/DczA1q3MTge/", lib=[], watcher=_fake_watch(AMDAD))
    assert a.verdict == FS.CHECK and a.title.text() == "American Dad! — S03E02"
    monkeypatch.setattr(FS, "online", lambda name, ep=None: FS.Title("American Dad!", 2005, ep[0], ep[1],
                                                                       "Tears of a Clooney", "episode"))
    b = FS.find("https://www.instagram.com/reel/DczA1q3MTge/", lib=[], watcher=_fake_watch(AMDAD))
    assert b.verdict == FS.CHECK


def test_the_ais_title_is_looked_for_in_your_raws_first(monkeypatch, tmp_path):
    _clip(monkeypatch)
    monkeypatch.setattr(FS, "_clip_file", lambda *a: tmp_path / "c.mp4")
    lib = [FS.LibItem(FS.Title("Family Guy", None, 11, 1, "", "episode"), "fg.mp4", "fg.wav"),
           FS.LibItem(FS.Title("American Dad!", None, 3, 2, "", "episode"), "ad.mp4", "ad.wav")]
    seen = []
    monkeypatch.setattr(FS, "_sound_found", lambda c, s: seen.append(s) or (0.95 if s == "ad.wav" else 0.0))
    a = FS.find("https://www.instagram.com/reel/DczA1q3MTge/", lib=lib, watcher=_fake_watch(AMDAD))
    assert seen[0] == "ad.wav" and a.verdict == FS.SURE and "its sound found in your RAW" in a.how
    assert "NexLev's AI said American Dad!" in a.how


def test_nothing_known_says_why_and_the_answer_survives_the_history(monkeypatch):
    _clip(monkeypatch)
    monkeypatch.setattr(FS, "online", lambda name, ep=None: None)
    a = FS.find("https://www.instagram.com/reel/Zz/", lib=[], watcher=_fake_watch(json.dumps(
        {"title": "", "sure": 0, "why": "a talk show I don't know"})))
    assert a.verdict == FS.NOT_FOUND and "AI watched it and does not know it" in a.reason
    back = FS.Answer.from_dict(json.loads(json.dumps(a.to_dict())))
    assert back.ai["why"] == "a talk show I don't know"


def test_algrows_wrapped_answer_is_read_and_an_empty_one_is_not_kept():
    wrapped = json.dumps({"analysis_text": "```json\n" + AMDAD + "\n```"})
    w = A.parse_answer(wrapped, "https://www.instagram.com/reel/DZ56TJHuM75/", "Algrow")
    assert w.title == "American Dad!" and (w.season, w.episode) == (3, 2)
    assert A.parse_answer(json.dumps({"status": "done"}), "l", "Algrow").error
    got = A.watch("https://www.instagram.com/reel/DZ56TJHuM75/", nexlev=lambda u: '{"status": "done"}',
                  algrow=lambda u: wrapped)
    assert got.by == "Algrow" and got.title == "American Dad!"
