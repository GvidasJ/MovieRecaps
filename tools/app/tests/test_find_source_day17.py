"""Day 17, task 2: Find source proves the episode -- the clip's lines searched for on YouTube (titled clips, their
transcripts), TVmaze's episode summaries compared with the clip, the top 3 when not sure, a second opinion, and
everything cached. Fakes only: no network, no credits.

Run: python -m pytest tools/app/tests/test_find_source_day17.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import ai_watch as A  # noqa: E402
from mr_app.core import find_source as FS  # noqa: E402
from mr_app.core import source_proof as SP  # noqa: E402

# the real failure: Algrow's AI said S03E11 "Everybody Hates the Port Authority" (TVmaze has that title for S03E11,
# so day 16 said Sure); the scene is S03E07 "Everybody Hates Houseguests" (Greg stays the week at Chris's house)
EHC = {"title": "Everybody Hates Chris", "year": 2008, "kind": "show", "season": 3, "episode": 11,
       "episode_title": "Everybody Hates the Port Authority", "sure": 95,
       "why": "Greg staying at Chris's house and falling asleep in Ms. Morello's class",
       "spoken_lines": ["Greg, what's wrong with you? You're usually so alert.",
                        "It's because he's staying at my house for the week.", "Oh, my God! He's drunk!",
                        "Did he have a 40 for breakfast?", "I'm just not used to getting up so early."],
       "characters": ["Greg", "Ms. Morello", "Chris"], "setting": "a classroom",
       "guesses": [{"title": "Everybody Hates Chris", "kind": "show", "season": 3, "episode": 11,
                    "episode_title": "Everybody Hates the Port Authority", "why": "Greg sleeps in class"}]}
EPS = {"name": "Everybody Hates Chris", "eps": [
    {"season": 3, "number": 7, "name": "Everybody Hates Houseguests",
     "summary": "Greg's friendship with Chris is put to the test when he spends the week at Chris's house, and "
                "Julius has an eventful first week as a taxicab driver."},
    {"season": 3, "number": 11, "name": "Everybody Hates the Port Authority",
     "summary": "Chris gets lost in the Port Authority bus terminal on his way home."},
    {"season": 1, "number": 10, "name": "Everybody Hates Greg",
     "summary": "Chris hangs out at Greg's house without permission and must hide from Greg's father."},
    {"season": 2, "number": 15, "name": "Everybody Hates Cutting School",
     "summary": "With Ms. Morello out for the day, Chris and Greg cut school to see Ghostbusters."}]}
TRANSCRIPT = ("greg what's wrong with you you're usually so alert it's because he's staying at my house for the "
              "week oh my god he's drunk did he have a 40 for breakfast")


def _watch(d: dict) -> A.Watch:
    return A.parse_answer(json.dumps(d), "https://www.instagram.com/reel/Ddm3LHisOm6/", "Algrow")


def _services(search=None, tr=None, eps=None, log=None, web=None):
    log = log if log is not None else []
    return SP.Services(search=lambda q: log.append(q) or (search(q) if search else []),
                       transcript=lambda v: (tr or {}).get(v, ""), episodes=lambda s: eps or {},
                       web=lambda q: (web(q) if web else []))


def test_a_titled_clip_with_the_same_words_proves_the_episode_the_ai_got_wrong():
    w = _watch(EHC)
    log: list = []
    sv = _services(lambda q: [{"video_id": "DWGS8oOdObg", "title": "Everybody Hates Houseguests - Mrs. Morello.m4v",
                               "secs": 37}], {"DWGS8oOdObg": TRANSCRIPT}, EPS, log)
    pr = SP.prove(w, sv)
    assert pr.sure and (pr.season, pr.episode, pr.episode_title) == (3, 7, "Everybody Hates Houseguests")
    assert "Mrs. Morello" in pr.how and pr.others[0]["episode"] == 7
    assert 1 <= len(log) <= SP.LINES_SEARCHED and all(q.startswith("Everybody Hates Chris ") for q in log)


def test_the_episode_summaries_rank_the_episodes_and_the_ais_number_alone_is_never_sure():
    w = _watch(EHC)
    pr = SP.prove(w, _services(None, None, EPS))                # nothing on YouTube: only the summaries
    assert (pr.season, pr.episode) == (3, 7)                    # the summary that fits, not the AI's S03E11
    assert not pr.sure and len(pr.others) >= 2
    assert {(o["season"], o["episode"]) for o in pr.others} >= {(3, 7), (3, 11)}
    assert "summary fits" in pr.others[0]["why"]


def test_lines_match_word_for_word_only():
    lines = ["It's because he's staying at my house for the week.", "What?"]
    assert SP.line_hits(lines, "and IT'S BECAUSE he's staying at my house, really") == 1
    assert SP.line_hits(lines, "because staying house week") == 0              # the words, not in a row
    assert SP.distinctive(["What?", "Oh no.", "Did he have a 40 for breakfast?",
                           "Chris's people have a history of being up when the rooster crows"], 2)[0].startswith(
        "Chris's people")


def test_episode_named_by_title_or_number():
    eps = EPS["eps"]
    assert SP.episode_named("EHC | Everybody Hates Houseguests (2007)", eps)["number"] == 7
    assert SP.episode_named("Everybody Hates Chris S03E11 clip", eps)["number"] == 11
    assert SP.episode_named("Everybody Hates Chris season 1 episode 10", eps)["number"] == 10
    assert SP.episode_named("Everybody Hates Chris funny moments", eps) is None


def _clip(monkeypatch):
    monkeypatch.setattr(FS, "read_clip", lambda s, c=None: FS.Clip(s, "Reel Ddm3LHisOm6", "Instagram", 20.0,
                                                                   "Who says like that?"))
    monkeypatch.setattr(FS, "_clip_file", lambda *a: None)
    monkeypatch.setattr(FS, "online", lambda name, ep=None: FS.Title(
        "Everybody Hates Chris", 2005, ep[0], ep[1], "Everybody Hates the Port Authority", "episode") if ep else
        FS.Title("Everybody Hates Chris", 2005, kind="show"))


def test_find_source_says_the_proven_episode_and_the_top_3_otherwise(monkeypatch):
    _clip(monkeypatch)
    sv = _services(lambda q: [{"video_id": "v1", "title": "Everybody Hates Houseguests - Mrs. Morello", "secs": 37}],
                   {"v1": TRANSCRIPT}, EPS)
    a = FS.find("https://www.instagram.com/reel/Ddm3LHisOm6/", lib=[], watcher=lambda l, say=None: _watch(EHC),
                prover=lambda w: SP.prove(w, sv), second=lambda *a, **k: pytest.fail("proven: no second opinion"))
    assert a.verdict == FS.SURE and a.title.text() == "Everybody Hates Chris — S03E07" and "proven" in a.how
    # without a titled clip: Check, the summary's episode first, the AI's S03E11 among the top 3
    seconds = []
    b = FS.find("https://www.instagram.com/reel/Ddm3LHisOm6/", lib=[], watcher=lambda l, say=None: _watch(EHC),
                prover=lambda w: SP.prove(w, _services(None, None, EPS)),
                second=lambda link, w, say=None: seconds.append(link) or None)
    assert b.verdict == FS.CHECK and b.title.text() == "Everybody Hates Chris — S03E07" and len(seconds) == 1
    assert any(o["episode"] == 11 for o in b.others) and "(or " in b.line(1)
    back = FS.Answer.from_dict(json.loads(json.dumps(b.to_dict())))
    assert back.others == b.others


def test_the_second_opinion_joins_its_guesses_and_is_said(monkeypatch):
    _clip(monkeypatch)
    w2 = _watch(dict(EHC, season=3, episode=7, episode_title="Everybody Hates Houseguests",
                     guesses=[{"title": "Everybody Hates Chris", "kind": "show", "season": 3, "episode": 7,
                               "episode_title": "Everybody Hates Houseguests", "why": "Greg stays the week"}]))
    eps = {"name": EPS["name"], "eps": EPS["eps"] + [   # a summary that ties with S03E07: no clear lead alone
        {"season": 4, "number": 1, "name": "Everybody Hates the Week", "summary": "Greg stays at Chris's house."}]}
    a = FS.find("https://www.instagram.com/reel/Ddm3LHisOm6/", lib=[], watcher=lambda l, say=None: _watch(EHC),
                prover=lambda w: SP.prove(w, _services(None, None, eps)), second=lambda link, w, say=None: w2)
    assert "Algrow's AI says Everybody Hates Chris — S03E07 too" in a.how


def test_searches_and_transcripts_are_cached_never_asked_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(SP, "_cache_path", lambda: tmp_path / "source_proof.json")
    calls = []
    monkeypatch.setattr(SP, "_algrow", lambda tool, args, timeout=90.0: calls.append(tool) or (
        json.dumps({"results": [{"type": "video", "video_id": "v1", "title": "x", "duration_seconds": 30}]})
        if tool == "youtube_search" else json.dumps([{"video_id": "v1", "transcript": "hello there"}])))
    assert SP.yt_search("Show line")[0]["video_id"] == "v1" and SP.yt_search("show line")[0]["video_id"] == "v1"
    assert SP.transcript("v1") == "hello there" == SP.transcript("v1")
    assert calls == ["youtube_search", "fetch_transcript"]


def test_the_newer_prompt_asks_once_more_and_keeps_the_older_answer_when_that_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "_cache_path", lambda: tmp_path / "ai_watch.json")
    monkeypatch.setattr(A, "key", lambda name: "SECRET-" + name)
    link = "https://www.instagram.com/reel/Ddm3LHisOm6/"
    old = A.parse_answer(json.dumps(EHC), link, "Algrow")
    old.version = 1
    A._store(old)                                                                     # noqa: SLF001
    def bad(_u):
        raise A.ServiceError("away")
    assert A.watch(link, nexlev=bad, algrow=bad).title == "Everybody Hates Chris"     # the older answer kept
    new = A.watch(link, nexlev=lambda u: json.dumps(EHC), algrow=bad)
    assert new.version == A.PROMPT_VERSION and new.guesses[0]["episode"] == 11
    assert A.watch(link, nexlev=lambda u: pytest.fail("asked twice")).version == A.PROMPT_VERSION
    assert "guesses" in A.PROMPT and "top 3" in A.PROMPT


FG = {"title": "Family Guy", "kind": "show", "season": 16, "episode": 9, "episode_title": "Don't Be a Dickens at "
      "Christmas", "sure": 100, "why": "a cutaway gag",
      "spoken_lines": ["Both of you are under arrest for prostitution.", "You paid her to have sex.",
                       "No, I paid her to have sex and we're filming it. So technically it's not prostitution, "
                       "it's a porno."]}
FG_EPS = {"name": "Family Guy", "eps": [
    {"season": 9, "number": 11, "name": "German Guy", "summary": "Chris meets a puppeteer named Franz."},
    {"season": 16, "number": 9, "name": "Don't Be a Dickens at Christmas", "summary": "Peter fills in as Santa."},
    {"season": 3, "number": 2, "name": "Mr. Griffin Goes to Washington", "summary": "Peter lobbies Washington."}]}


def test_a_quote_site_with_the_exact_words_names_the_episode():
    """The real day-17 case: YARN's snippet "Family Guy (1999) - S09E11 Comedy clip with quote So, technically it's
    not prostitution, it's a porno." -- the AI's S16E09 was wrong."""
    w = A.parse_answer(json.dumps(FG), "https://www.instagram.com/reel/DZJfIY3IlTD/", "NexLev")

    def web(q):
        if "porno" not in q:
            return []
        return [{"title": "YARN | So, technically it's not prostitution, it's a porno. | Family ...",
                 "url": "https://getyarn.io/yarn-clip/a571", "snippet": "Family Guy (1999) - S09E11 Comedy clip with "
                 "quote So, technically it's not prostitution, it's a porno. Yarn is the best search for video clips"}]
    pr = SP.prove(w, _services(None, None, FG_EPS, web=web))
    assert (pr.season, pr.episode, pr.episode_title) == (9, 11, "German Guy")
    assert not pr.sure                       # the number alone: no other check agrees (another ordering?)
    assert "getyarn.io has the clip's exact words" in pr.others[0]["why"]
    # with a second line's quote (or the AI, a titled clip, a top summary) agreeing: Sure
    def web2(q):                             # both searched lines found on the quote site
        return [dict(x, snippet=x["snippet"] + " Both of you are under arrest for prostitution.")
                for x in web("porno")]
    pr2 = SP.prove(w, _services(None, None, FG_EPS, web=web2))
    assert pr2.sure and (pr2.season, pr2.episode) == (9, 11) and "getyarn.io" in pr2.how
    assert SP.QUOTE_RX.search("The Dictator (2012) Comedy clip with quote You mean your Porsche?").group(1) ==         "The Dictator"


def test_the_web_search_is_polite_and_pauses_after_a_refusal(tmp_path, monkeypatch):
    import io
    import urllib.request
    monkeypatch.setattr(SP, "_cache_path", lambda: tmp_path / "source_proof.json")
    monkeypatch.setattr(SP, "WEB_GAP_S", 0.0)
    monkeypatch.setitem(SP._web_state, "blocked_until", 0.0)                       # noqa: SLF001
    calls = []
    page = ['<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgetyarn.io%2Fx&rut=1">'
            'YARN | So, technically</a> <a class="result__snippet" href="x">Family Guy (1999) - S09E11 Comedy clip '
            'with quote</a>']

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        calls.append(req.full_url)
        return R(page[0].encode())
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    got = SP.web_search("\"technically it's not prostitution\" Family Guy")
    assert got and got[0]["url"] == "https://getyarn.io/x" and "S09E11" in got[0]["snippet"]
    assert SP.web_search("\"technically it's not prostitution\" family guy") == got and len(calls) == 1   # cached
    page[0] = "<html>... an anomaly in your traffic ...</html>"
    assert SP.web_search("another line") == [] and len(calls) == 2
    assert SP.web_search("a third line") == [] and len(calls) == 2           # paused: not asked again


def test_a_quote_page_titled_with_the_episode_proves_it():
    """The real day-17 result: IMDb's quotes page '"Everybody Hates Chris" Everybody Hates Houseguests (TV Episode
    2007) - Quotes' with the clip's words -- the episode by its title (no numbering to disagree on)."""
    w = _watch(EHC)

    def web(q):
        return [{"title": "\"Everybody Hates Chris\" Everybody Hates Houseguests (TV Episode 2007) - Quotes - IMDb",
                 "url": "https://www.imdb.com/title/tt1/quotes/", "snippet": "Chris: It's because he's staying at my "
                 "house for the week. Ms. Morello: Oh my God, he's drunk! Did he have a 40 for breakfast?"}]
    pr = SP.prove(w, _services(None, None, EPS, web=web))
    assert pr.sure and (pr.season, pr.episode) == (3, 7) and "imdb.com" in pr.how
