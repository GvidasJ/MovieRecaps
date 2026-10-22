"""The automatic "Uploaded" check (mr_app.core.uploads): the channels' Shorts tabs, the real yt-dlp JSON of the three
channels (tests/data, read once on 2026-10-18), the titles as compared, exact / near / not near on the real titles,
one Short to one card, the upload time, no internet (yt-dlp's real messages), the schedule and the whole check with a
stand-in yt-dlp. Nothing here goes to the internet.
Run: python -m pytest tools/app/tests/test_uploads.py"""
from __future__ import annotations

import itertools
import json
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import download as D, paths, uploads as U   # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
TABS = json.loads((DATA / "shorts_tabs_real.json").read_text(encoding="utf-8"))
VIDEOS = json.loads((DATA / "short_video_real.json").read_text(encoding="utf-8"))
CHANNELS = ("LouLaughs", "MikeMemesYou", "Flick707")
REAL = {ch: U.parse_tab(TABS[ch], ch) for ch in CHANNELS}
ALL = [s for ch in CHANNELS for s in REAL[ch]]
BY_ID = {s.id: s for s in ALL}
# the titles typed for night 10's projects (reports/night10.md) and the mock-up's fourth (docs/ui_preview.html)
NIGHT10 = {"LouLaughs": "The fact that he had this much to catch up on is hilarious 📰😭",
           "MikeMemesYou": "The fact that nobody in the crew wanted to go is hilarious 🙋‍♂️😭",
           "Flick707": "When she finally got the part 💀😭"}
MEME = "The fact that this is where the meme came from is hilarious 🍫😭"
NOW = datetime(2026, 10, 18, 9, 0).astimezone()
# the real chocolate project (D:\Shared\2026-10-18_where-the-nestle-crunch-meme-originated\project.json, trimmed) and
# the Short it became: "The Commercial Behind The Nestle Crunch Meme 🍫😭", 24 s, up 02:34:23, 52 min after the edit
CHOCOLATE = {
    "name": "2026-10-18_where-the-nestle-crunch-meme-originated", "created": "2026-10-18T01:32:44+03:00",
    "computer": "Gvidas", "channel": "LouLaughs", "title": "The fact that this meme was\nan actual ad is hilarious 🍫😭",
    "competitor": {"url": "https://www.youtube.com/shorts/wiFDgtB7VjI",
                   "title": "Where The Nestle Crunch Meme Originated From 🍫😳", "id": "wiFDgtB7VjI",
                   "file": "Where The Nestle Crunch Meme Originated From 🍫😳 [wiFDgtB7VjI].mp4"},
    "steps": {"download": {"state": "done", "started": "2026-10-18T01:32:46+03:00",
                           "finished": "2026-10-18T01:32:52+03:00", "competitor": {"duration": 24.0}},
              "edit": {"state": "done", "started": "2026-10-18T01:36:19+03:00", "finished": "2026-10-18T01:42:42+03:00",
                       "result": "PASS"}}}
CHOC_SHORT = "omBq76V5wQQ"
CHOC_FACTS = U.info_facts(VIDEOS[CHOC_SHORT])

# yt-dlp's real output without internet (a proxy that does not exist / a closed port stood in for the cable)
NO_DNS = ("WARNING: [youtube:tab] ('Unable to connect to proxy', NameResolutionError(\"HTTPSConnection(host='no-such-"
          "host.invalid', port=8080): Failed to resolve 'no-such-host.invalid' ([Errno 11001] getaddrinfo failed)\")). "
          "Retrying (1/3)...\nERROR: [youtube:tab] @Flick707/shorts: Unable to download API page: ('Unable to connect "
          "to proxy', NameResolutionError(\"HTTPSConnection(host='no-such-host.invalid', port=8080): Failed to resolve "
          "'no-such-host.invalid' ([Errno 11001] getaddrinfo failed)\")) (caused by ProxyError('...')); please report "
          "this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
          "Confirm you are on the latest version using  yt-dlp -U")
REFUSED = ("ERROR: [youtube:tab] @Flick707/shorts: Unable to download API page: ('Unable to connect to proxy', "
           "NewConnectionError(\"HTTPSConnection(host='127.0.0.1', port=9): Failed to establish a new connection: "
           "[WinError 10061] No connection could be made because the target machine actively refused it\"))")
NO_TAB = "ERROR: [youtube:tab] @SomeChannel/shorts: This channel does not have a shorts tab"


def card(title, channel="", key=None, uploaded=None, not_same=()):
    return {"key": key or f"2026-10-18_{U.norm(title).replace(' ', '-')[:30]}", "channel": channel, "title": title,
            "uploaded": uploaded, "not_same": list(not_same)}


def result(c, shorts=ALL):
    """('exact' | 'near' | None, the Short's id or None, the score) of one card against the Shorts."""
    m = U.compare([c], shorts)
    if m.exact:
        return "exact", m.exact[0].short.id, m.exact[0].score
    if m.near:
        return "near", m.near[0].short.id, m.near[0].score
    return None, None, 0.0


# ---------------------------------------------------------------------------------------------------------------------
# the channels' Shorts tabs
# ---------------------------------------------------------------------------------------------------------------------

def test_the_three_channels_and_their_shorts_tabs():
    tabs = U.channel_tabs()
    if not paths.channels_dir().is_dir():
        pytest.skip("no assets/channels in this checkout")
    assert tabs == [("Flick707", "https://www.youtube.com/@Flick707/shorts"),
                    ("LouLaughs", "https://www.youtube.com/@LouLaughs/shorts"),
                    ("MikeMemesYou", "https://www.youtube.com/@MikeMemesYou/shorts"),
                    ("SceneProof", "https://www.youtube.com/@SceneProof/shorts")]      # night 15


@pytest.mark.parametrize("link, handle, tab", [
    ("https://www.youtube.com/@LouLaughs", "@LouLaughs", "https://www.youtube.com/@LouLaughs/shorts"),
    ("https://www.youtube.com/@LouLaughs/", "", "https://www.youtube.com/@LouLaughs/shorts"),
    ("https://www.youtube.com/@LouLaughs/videos?view=0", "", "https://www.youtube.com/@LouLaughs/shorts"),
    ("https://www.youtube.com/@Flick707/shorts", "", "https://www.youtube.com/@Flick707/shorts"),
    ("https://www.youtube.com/channel/UCMirYAMpoogf-7hSmZqdTaQ/featured", "",
     "https://www.youtube.com/channel/UCMirYAMpoogf-7hSmZqdTaQ/shorts"),
    ("", "@MikeMemesYou", "https://www.youtube.com/@MikeMemesYou/shorts"),
    ("", "MikeMemesYou", "https://www.youtube.com/@MikeMemesYou/shorts"),
    ("", "", ""),
])
def test_a_channels_link_gives_its_shorts_tab(link, handle, tab):
    assert U.shorts_tab(link, handle) == tab


def test_channel_folders_without_a_link_use_their_name_and_empty_folders_are_left_out(tmp_path):
    (tmp_path / "Zed Channel").mkdir()
    (tmp_path / "Zed Channel" / "template.psd").write_bytes(b"")            # no channel.json: from the name
    (tmp_path / "Abc").mkdir()
    (tmp_path / "Abc" / "channel.json").write_text(json.dumps({"name": "Abc", "link": "https://www.youtube.com/@Abc1"}),
                                                   encoding="utf-8")
    (tmp_path / "not a channel").mkdir()                                    # neither file: not a channel
    assert U.channel_tabs(tmp_path) == [("Abc", "https://www.youtube.com/@Abc1/shorts"),
                                        ("Zed Channel", "https://www.youtube.com/@ZedChannel/shorts")]
    assert U.channel_tabs(tmp_path / "missing") == []


def test_the_tab_command_is_flat_and_has_no_no_playlist():
    cmd = U.tab_command("https://www.youtube.com/@Flick707/shorts")
    assert "--no-playlist" not in cmd and cmd[-1] == "https://www.youtube.com/@Flick707/shorts"
    s = " ".join(cmd)
    assert cmd[1:3] == ["-m", "yt_dlp"] and "--ignore-config" in cmd and "--flat-playlist -J --playlist-end 100" in s
    js = D.js_runtime()
    if js is not None:                                                     # YouTube needs deno on this PC
        assert cmd[cmd.index("--js-runtimes") + 1] == f"{js[0]}:{js[1]}"
    assert U.handle_of("https://www.youtube.com/@Flick707/shorts") == "Flick707" and U.handle_of("x") == ""


# ---------------------------------------------------------------------------------------------------------------------
# yt-dlp's JSON
# ---------------------------------------------------------------------------------------------------------------------

def test_the_real_tabs_parse_into_their_shorts():
    assert [len(REAL[ch]) for ch in CHANNELS] == [10, 33, 12]
    first = REAL["Flick707"][0]
    assert first == U.Short("pXBgisZ34JQ", "When Grandpa Wants You To Lie For Him 💀😭",
                            "https://www.youtube.com/shorts/pXBgisZ34JQ", "Flick707")
    assert REAL["LouLaughs"][0].title == "The Commercial Behind The Nestle Crunch Meme 🍫😭"
    assert all(s.url == f"https://www.youtube.com/shorts/{s.id}" for s in ALL)
    assert len(BY_ID) == 55                                                 # 55 different ids
    assert BY_ID["N5UVbT06J0g"].title == "The Funniest Courtroom Speech of All Time ⚖️😭 #usa"


def test_odd_entries_are_left_out_or_fixed():
    data = {"_type": "playlist", "entries": [
        {"id": "abc", "title": "not a YouTube id"},
        {"id": "pXBgisZ34JQ", "title": None, "url": "pXBgisZ34JQ"},          # no title, no web address
        {"id": "pXBgisZ34JQ", "title": "again"},                               # a repeat
        "junk",
        {"_type": "playlist", "entries": [{"id": "fmpdQoibTEc", "title": "nested", "url": "https://x/y"}]},
    ]}
    got = U.parse_tab(data, "Flick707")
    assert got == [U.Short("pXBgisZ34JQ", "", "https://www.youtube.com/shorts/pXBgisZ34JQ", "Flick707"),
                   U.Short("fmpdQoibTEc", "nested", "https://x/y", "Flick707")]
    assert U.parse_tab({}, "x") == [] and U.parse_tab([], "x") == []


# ---------------------------------------------------------------------------------------------------------------------
# the titles as they are compared
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, normed", [
    ("When she finally got the part 💀😭", "when she finally got the part"),
    ("The Funniest Pool Announcement of All Time 🏊‍♂️😭", "the funniest pool announcement of all time"),
    ("The Funniest Cop Duo of All Time 👮🏽‍♂️😭", "the funniest cop duo of all time"),    # a skin tone
    ("The Funniest Courtroom Speech of All Time ⚖️😭 #usa", "the funniest courtroom speech of all time"),
    ("Why did half of the USSR rely on German trucks — and not their own? #IFAvsGAZ66 #SovietTech",
     "why did half of the ussr rely on german trucks and not their own"),
    ("The Man Who Couldn’t Read The Room 🎤😭", "the man who couldnt read the room"),
    ("The Man Who Couldn't Read The Room", "the man who couldnt read the room"),
    ("The Funniest Word Mix-Up of All Time 👑😭", "the funniest word mix up of all time"),
    ("Why did the USSR rely on German trucks at -50°C?", "why did the ussr rely on german trucks at 50 c"),
    ("Café CRÈME Brûlée", "cafe creme brulee"),
    ("Straße", "strasse"),
    ("Tom & Jerry", "tom and jerry"),
    ("The #1 Reason 1️⃣", "the 1 reason 1"),                   # '#1' is not a hashtag; a keycap is its digit
    ("🇺🇸 USA 🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f", "usa"),     # flags
    ("  lots   of\tspace\n", "lots of space"),
    ("😭😭😭", ""),
    ("", ""),
    (None, ""),
])
def test_norm_drops_emoji_hashtags_punctuation_case_and_accents(title, normed):
    assert U.norm(title) == normed
    assert U.norm(normed) == normed                                         # normalising twice changes nothing


@pytest.mark.parametrize("a, b, same", [
    ("alergic", "allergic", True), ("trainnig", "training", True), ("lesson", "lessons", True),
    ("recieve", "receive", True), ("funiest", "funniest", True),
    ("prank", "drank", True),                                               # one letter: a typo, by the rule
    ("dou", "duo", False), ("cop", "top", False),                           # too short to tell
    ("school", "spelling", False), ("2025", "2026", False), ("time", "time", False),
])
def test_typos(a, b, same):
    assert U.typo(a, b) is same


# ---------------------------------------------------------------------------------------------------------------------
# exact, near and not near -- cards against the 55 real Shorts
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("channel, title, short_id", [
    ("Flick707", "When Grandpa Wants You To Lie For Him 😂", "pXBgisZ34JQ"),                 # another emoji
    ("Flick707", "When Grandpa Wants You To Lie For Him", "pXBgisZ34JQ"),                    # no emoji
    ("Flick707", "The Funniest Courtroom Speech of All Time ⚖️😭", "N5UVbT06J0g"),  # #usa on YouTube only
    ("Flick707", "the man who couldn't read the room", "Y1OByxLfODo"),                       # case, ' for ’
    ("MikeMemesYou", "The Funniest Cop Duo of All Time 👮🏽‍♂️😭", "EZ-Ex7eYx2I"),    # a skin tone
    ("MikeMemesYou", "THE FUNNIEST FATHER SON TALK OF ALL TIME!!", "SWWn90KnmeI"),
    ("", "The Funniest Spelling Lesson of All Time ⛽😭", "GgFw_WDjQnI"),                    # no channel: any
])
def test_exact_matches(channel, title, short_id):
    assert result(card(title, channel))[:2] == ("exact", short_id)


@pytest.mark.parametrize("channel, title, short_id", [
    ("LouLaughs", "The Funniest Alergic Reaction of All Time 🥜😭", "z02sVAeC4wM"),          # a typo
    ("MikeMemesYou", "The Funniest Diversity Trainnig of All Time 🤦‍♂️😭", "Y_QvArZrq8o"),  # swapped
    ("Flick707", "The Funniest Word Mixup of All Time 👑😭", "6Cxw8QyBYNQ"),                 # only the spaces
    ("Flick707", "The Funniest Would You Rather Question Ever 🤔😭", "uXn9_hsijWM"),         # reworded
    ("MikeMemesYou", "The Funniest Police Station Prank Ever 🚔😭", "jRCqaGZ1JT4"),
    ("Flick707", "When Grandpa Wants You To Lie For Him Again 💀😭", "pXBgisZ34JQ"),        # a word added
    ("MikeMemesYou", "They were paid in gold. So why did the Soviet Union need thousands of trucks from West "
                     "Germany?", "xsNuaVq2SRo"),                                             # one word changed
    ("MikeMemesYou", "When She Knew It Was Time To Leave 🏃‍♀️😭", "CO5XnBuyZIU"),  # he -> she: asks
])
def test_near_matches_ask_first(channel, title, short_id):
    kind, sid, score = result(card(title, channel))
    assert (kind, sid) == ("near", short_id) and U.NEAR_SHARE <= score < 1.0


@pytest.mark.parametrize("channel, title", [
    ("MikeMemesYou", "The Funniest Spelling Lesson of All Time ⛽😭"),       # School Lesson is Mike's, Spelling Flick's
    ("Flick707", "The Funniest Breakup Scene of All Time 💔😭"),              # Adoption Scene: another video
    ("Flick707", "The Funniest Family Vacation of All Time 🏖😭"),            # Family Trip: another video
    ("LouLaughs", "The Funniest Tough Guy in Movie History 📞😭"),
    *NIGHT10.items(),                                                         # night 10's titles were not uploaded
    ("MikeMemesYou", MEME),
])
def test_other_videos_of_the_same_template_are_not_near(channel, title):
    assert result(card(title, channel)) == (None, None, 0.0)


def test_every_real_title_matches_only_itself():
    cards = [card(s.title, s.channel, key=s.id) for s in ALL]
    m = U.compare(cards, ALL)
    assert sorted((x.card, x.short.id) for x in m.exact) == sorted((s.id, s.id) for s in ALL)
    assert m.near == []
    w = U.word_weights([s.title for s in ALL])
    for ch in CHANNELS:                                  # 639 pairs of different videos of one channel: none near
        for a, b in itertools.combinations(REAL[ch], 2):
            assert U.similarity(a.title, b.title, w) < 0.55, (a.title, b.title)


def test_the_fact_that_template_different_videos_are_not_near_but_one_small_word_is():
    shorts = [U.Short("aaaaaaaaaa1", NIGHT10["LouLaughs"], "u1", "LouLaughs"),
              U.Short("aaaaaaaaaa2", NIGHT10["MikeMemesYou"], "u2", "LouLaughs"),
              U.Short("aaaaaaaaaa3", MEME, "u3", "LouLaughs"), *ALL]
    for a, b in itertools.combinations([NIGHT10["LouLaughs"], NIGHT10["MikeMemesYou"], MEME], 2):
        w = U.word_weights([s.title for s in shorts])
        assert U.similarity(a, b, w) < 0.45
    c = card("The fact that he had this much to catch up with is hilarious 📰😭", "LouLaughs")   # on -> with
    assert result(c, shorts)[:2] == ("near", "aaaaaaaaaa1")
    c = card("The fact that he did this is hilarious 😭", "LouLaughs")
    assert result(c, shorts + [U.Short("aaaaaaaaaa4", "The fact that she did that is hilarious 😭", "u4",
                                       "LouLaughs")])[0] is None


def test_a_short_is_compared_only_with_its_own_channels_cards():
    c = card("The Funniest Courtroom Speech of All Time", "LouLaughs")      # the Short is Flick707's
    assert result(c)[0] is None
    assert result(dict(c, channel="flick707"))[:2] == ("exact", "N5UVbT06J0g")   # the channel in any case


def test_uploaded_cards_and_not_same_pairs_are_skipped():
    title = "The Funniest Job Advice of All Time 💼😭"
    assert result(card(title, "Flick707", uploaded={"at": "2026-10-17T20:00:00+03:00", "id": "KdZE21qhyRY"}))[0] \
        is None
    assert result(card(title, "Flick707", not_same=["KdZE21qhyRY"]))[0] is None
    # a Short an uploaded card took (by its id, or by its exact title when you moved it by hand) is not offered again
    up = card(title, "Flick707", key="done", uploaded={"at": "2026-10-17T20:00:00+03:00", "how": "you"})
    redo = card("The Funniest Job Advise of All Time", "Flick707", key="redo")
    assert U.compare([up, redo], ALL).near == []
    up_id = card("something else", "Flick707", key="done", uploaded={"id": "KdZE21qhyRY"})
    assert U.compare([up_id, redo], ALL).near == []
    assert U.compare([redo], ALL).near[0].short.id == "KdZE21qhyRY"


def test_one_short_goes_to_one_card_and_exact_wins():
    s = BY_ID["fmpdQoibTEc"]                                                # The Funniest Family Trip of All Time
    exact = card("The Funniest Family Trip of All Time", "Flick707", key="exact")
    near = card("The Funniest Family Trp of All Time", "Flick707", key="near")
    m = U.compare([near, exact], [s])
    assert [x.card for x in m.exact] == ["exact"] and m.near == []
    any_channel = card("The Funniest Family Trip of All Time", "", key="any")
    m = U.compare([any_channel, exact], [s])
    assert [x.card for x in m.exact] == ["exact"]                         # the card with its channel first
    twins = [card("The Funniest Family Trip of All Time", "Flick707", key=k) for k in ("first", "second")]
    assert [x.card for x in U.compare(twins, [s]).exact] == ["first"]     # then the card given first
    # and one card gets one Short: the newer (higher on the tab) of two with its title
    old = U.Short("oldoldold01", s.title, "u", "Flick707")
    m = U.compare([exact], [s, old])
    assert [x.short.id for x in m.exact] == [s.id] and m.near == []


def test_the_match_gives_the_boards_uploaded_dict():
    m = U.compare([card("When Grandpa Wants You To Lie For Him", "Flick707", key="grandpa")], ALL).exact[0]
    at = datetime(2026, 10, 18, 7, 0, 24).astimezone()
    assert replace(m, at=at).as_uploaded() == {"at": at.isoformat(timespec="seconds"), "id": "pXBgisZ34JQ",
                                               "url": "https://www.youtube.com/shorts/pXBgisZ34JQ",
                                               "title": "When Grandpa Wants You To Lie For Him 💀😭", "how": "auto"}
    assert m.as_uploaded("you", now=NOW)["at"] == NOW.isoformat(timespec="seconds")   # unknown time: now
    assert m.card_title == "When Grandpa Wants You To Lie For Him"


def test_matches_unpack_like_the_windows_tuples():
    cards = [card("When Grandpa Wants You To Lie For Him", "Flick707", key="grandpa"),
             card("The Funniest Word Mixup of All Time", "Flick707", key="mixup")]
    m = U.compare(cards, ALL)
    key, short = m.exact[0]
    assert (key, short.id, len(m.exact[0]), m.exact[0][1].id) == ("grandpa", "pXBgisZ34JQ", 2, "pXBgisZ34JQ")
    key, short, score = m.near[0]
    assert (key, short.id, score) == ("mixup", "6Cxw8QyBYNQ", U.SPACES_ONLY) and len(m.near[0]) == 3
    assert [(k, s.id) for k, s in m.exact] == [("grandpa", "pXBgisZ34JQ")]


def test_board_cards_their_folder_is_the_key_and_their_no_answers_count(tmp_path):
    board = pytest.importorskip("mr_app.core.board")
    folder = tmp_path / "Projects" / "2026-10-18_grandpa"
    data = {"name": folder.name, "channel": "Flick707", "title": "The Funniest Word Mixup of All Time",
            "board": {"not_same": []}}
    c = board.Card(folder=folder, name=folder.name, channel="Flick707", title=data["title"], data=data)
    m = U.compare([c], ALL)
    assert [(k, s.id) for k, s, _ in m.near] == [(str(folder), "6Cxw8QyBYNQ")]
    data["board"]["not_same"] = ["6Cxw8QyBYNQ"]                             # you said No: not asked again
    assert U.compare([c], ALL).near == []
    data["board"]["not_same"] = []
    c.not_same = ["6Cxw8QyBYNQ"]                                            # ... as board.card_of() fills it
    assert U.compare([c], ALL).near == []
    c.not_same = []
    c.uploaded = {"at": "2026-10-18T09:00:00+03:00", "id": "6Cxw8QyBYNQ", "how": "you"}
    assert U.compare([c], ALL).near == []                                  # uploaded: skipped
    plain = type("C", (), {"folder": folder, "name": "x", "channel": "Flick707", "title": data["title"],
                           "uploaded": None})()                            # no not_same, no data: none
    assert U.card_key(plain) == str(folder) and U.compare([plain], ALL).near[0].short.id == "6Cxw8QyBYNQ"
    assert U.card_key({"name": "only a name"}) == "only a name" and U.card_key({}) == ""


def test_a_card_from_project_json():
    data = json.loads(json.dumps({
        "name": "2026-10-17_who-should-go", "channel": "MikeMemesYou", "title": NIGHT10["MikeMemesYou"],
        "board": {"not_same": ["VFLfcRr-65I"], "uploaded": None}}))
    assert U.card_of(data) == U.Card("2026-10-17_who-should-go", "MikeMemesYou", NIGHT10["MikeMemesYou"], None,
                                     ("VFLfcRr-65I",))
    assert U.card_of(data).data["channel"] == "MikeMemesYou"
    assert U.card_of({"title": None}, key="folder") == U.Card("folder")
    assert U.card_of(dict(data, upload_title="Who Should Go?")).upload_title == "Who Should Go?"
    assert U.compare([U.card_of({"name": "x", "channel": "Flick707", "title": "the slowest student of all time"})],
                     ALL).exact[0].short.id == "oyZ44SkOqrA"


# ---------------------------------------------------------------------------------------------------------------------
# the title planned for YouTube first, then the headline
# ---------------------------------------------------------------------------------------------------------------------

def test_the_planned_youtube_title_is_matched_first():
    headline = CHOCOLATE["title"]
    c = card(headline, "LouLaughs", key="choc")
    assert result(c)[0] is None                                             # the headline alone: no match
    c["upload_title"] = "The Commercial Behind The Nestle Crunch Meme 🍫😭"
    m = U.compare([c], ALL)
    assert [(x.card, x.short.id, x.why, x.card_title) for x in m.exact] == \
        [("choc", CHOC_SHORT, "the same title", c["upload_title"])]
    c["upload_title"] = "The Comercial Behind The Nestle Crunch Meme"          # a typo in it: asks
    x = U.compare([c], ALL).near[0]
    assert x.short.id == CHOC_SHORT and x.why == f"the titles are {round(100 * x.score)} % alike"
    c = card("The Funniest Allergic Reaction of All Time", "LouLaughs", key="allergic")
    c["upload_title"] = "Something else entirely"                           # the headline still counts
    x = U.compare([c], ALL).exact[0]
    assert (x.short.id, x.why, x.card_title) == ("z02sVAeC4wM", "the same words as the headline", c["title"])


def test_the_planned_title_wins_over_another_cards_headline():
    by_headline = card("When Grandpa Wants You To Lie For Him", "Flick707", key="headline")
    by_upload = card("A headline that says something else", "Flick707", key="upload")
    by_upload["upload_title"] = "When Grandpa Wants You To Lie For Him 💀😭"
    assert [x.card for x in U.compare([by_headline, by_upload], ALL).exact] == ["upload"]


def test_the_planned_title_from_a_board_card_or_its_data():
    cls = type("BoardCard", (), {})
    a = cls()
    a.folder, a.channel, a.title, a.uploaded = Path("P/one"), "LouLaughs", "headline", None
    a.upload_title = "The Funniest Tough Guy of All Time"
    assert U.card_titles(a) == [("upload", "The Funniest Tough Guy of All Time"), ("headline", "headline")]
    b = cls()
    b.folder, b.channel, b.title, b.uploaded = Path("P/two"), "LouLaughs", "headline", None
    b.data = {"upload_title": "The Funniest Age Guess of All Time"}          # no attribute: project.json's
    m = U.compare([a, b], ALL)
    assert sorted((x.card, x.short.id) for x in m.exact) == [(str(Path("P/one")), "4v3FsM0BzWQ"),
                                                             (str(Path("P/two")), "EGIIvltTZB0")]
    same = {"title": "Same Words 😭", "upload_title": "same words"}           # the same twice: once
    assert U.card_titles(same) == [("upload", "same words")] and U.card_titles({}) == []


# ---------------------------------------------------------------------------------------------------------------------
# probable: the same emoji or a close competitor's title, as long as the competitor, uploaded after the project
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, keys", [
    ("When she finally got the part 💀😭", {"💀", "😭"}),
    ("When He Knew It Was Time To Leave 🏃\u200d♂\ufe0f😭", {"🏃", "😭"}),
    ("The fact that he didn't even think twice is hilarious 🏃😭", {"🏃", "😭"}),
    ("The Funniest Cop Duo of All Time 👮🏽\u200d♂\ufe0f😭", {"👮", "😭"}),
    ("The Funniest Father Son Talk of All Time 👨\u200d👦😭", {"👨👦", "😭"}),
    ("The Funniest Courtroom Speech of All Time \u2696\ufe0f😭 #usa", {"\u2696", "😭"}),
    ("🇺🇸 USA ⛽", {"🇺🇸", "⛽"}),
    ("-50°C © ™ 1\ufe0f\u20e3", set()),
    ("", set()),
])
def test_emoji_keys(title, keys):
    assert U.emoji_keys(title) == keys


def test_the_facts_of_a_card():
    title, dur, made, edit = U.card_facts(CHOCOLATE)
    assert (title, dur) == ("Where The Nestle Crunch Meme Originated From 🍫😳", 24.0)
    assert made == datetime.fromisoformat("2026-10-18T01:32:44+03:00")
    assert edit == datetime.fromisoformat("2026-10-18T01:42:42+03:00")
    dropped = {"steps": {"convert": {"competitor": {"checks": {"duration_s": [151.6, 151.6]}}}}}
    assert U.card_facts(dropped)[1] == 151.6                                # a dropped file: the convert's check
    assert U.card_facts({}) == ("", None, None, None)
    board_card = type("BC", (), {"data": CHOCOLATE, "channel": "LouLaughs", "title": "x"})()
    assert U.card_facts(board_card)[1] == 24.0
    assert U.info_facts(VIDEOS[CHOC_SHORT]) == U.Facts(24.0, datetime(2026, 10, 17, 23, 34, 23, tzinfo=timezone.utc))
    assert U.info_facts({"duration": True}) == U.Facts(None, None)


def test_the_real_chocolate_upload_is_found_and_asks_first():
    m = U.compare([CHOCOLATE], ALL, {CHOC_SHORT: CHOC_FACTS})
    assert m.exact == [] and len(m.near) == 1
    key, short, score = m.near[0]
    assert (key, short.id, score) == (CHOCOLATE["name"], CHOC_SHORT, U.PROBABLE_SCORE + 0.05)   # two hints
    why = m.near[0].why
    assert why.startswith("the same 🍫, the competitor's title ") and " % alike, " in why
    assert why.endswith("24 s long like the competitor (24 s), uploaded 51 min after the edit was made")
    assert m.near[0].card_title == CHOCOLATE["title"]
    assert U.compare([CHOCOLATE], ALL).near == []                            # without the facts: nothing


def _variant(**changes):
    c = json.loads(json.dumps(CHOCOLATE))
    for path, value in changes.items():
        d = c
        keys = path.split("__")
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value
    return c


@pytest.mark.parametrize("data, facts, why", [
    (_variant(), U.Facts(30.0, CHOC_FACTS.at), "6 s longer than the competitor"),
    (_variant(), U.Facts(24.0, datetime.fromisoformat("2026-10-18T01:00:00+03:00")), "up before the project"),
    (_variant(), U.Facts(None, CHOC_FACTS.at), "its length unknown"),
    (_variant(), U.Facts(24.0, None), "its upload time unknown"),
    (_variant(channel=""), CHOC_FACTS, "a card without a channel"),
    (_variant(steps__download__competitor={}), CHOC_FACTS, "the competitor's length unknown"),
    (_variant(created=None), CHOC_FACTS, "when the project was made unknown"),
    (_variant(title="The fact that this was an ad 😭", competitor__title="Something Else 😭"), CHOC_FACTS,
     "no hint: only 😭 and an unrelated competitor"),
    (_variant(board={"not_same": [CHOC_SHORT]}), CHOC_FACTS, "you said it is not this video"),
])
def test_probable_needs_everything_to_agree(data, facts, why):
    assert U.compare([data], ALL, {CHOC_SHORT: facts}).near == [], why


def test_probable_has_some_slack_and_one_hint_is_enough():
    early = datetime.fromisoformat("2026-10-18T01:29:00+03:00")             # 4 min before: two computers' clocks
    assert U.compare([CHOCOLATE], ALL, {CHOC_SHORT: U.Facts(26.0, early)}).near[0].short.id == CHOC_SHORT
    long_ = _variant(steps__download__competitor={"duration": 150.0})        # 8 % of 150 s = 12 s
    assert U.compare([long_], ALL, {CHOC_SHORT: U.Facts(139.0, CHOC_FACTS.at)}).near[0].short.id == CHOC_SHORT
    assert U.compare([long_], ALL, {CHOC_SHORT: U.Facts(137.0, CHOC_FACTS.at)}).near == []
    emoji_only = _variant(competitor__title="Something Else Entirely 🍫😳")
    x = U.compare([emoji_only], ALL, {CHOC_SHORT: CHOC_FACTS}).near[0]
    assert x.score == U.PROBABLE_SCORE and x.why.startswith("the same 🍫, 24 s long")
    words_only = _variant(title="The fact that this was an ad is hilarious 😭")
    x = U.compare([words_only], ALL, {CHOC_SHORT: CHOC_FACTS}).near[0]
    assert x.score == U.PROBABLE_SCORE and x.why.startswith("the competitor's title ")
    no_edit = _variant(steps__edit={"state": "running"})
    assert U.compare([no_edit], ALL, {CHOC_SHORT: CHOC_FACTS}).near[0].why.endswith(
        "uploaded 1 h 1 min after the project was made")


# your six real headlines (test_headline.py) and the Shorts they became: four share the emoji that says what the video
# is; "When dad tries to be the tour guide 💀😭" and "...a bad word... 🙊😭" share none with a Short
HEADLINE_SHORTS = [
    ("The fact that almonds sent him\nto the hospital is hilarious 🥜😭", "LouLaughs", "z02sVAeC4wM", "🥜"),
    ("The fact that they're not even\nreal cops is hilarious 👮\u200d♂\ufe0f😭", "MikeMemesYou", "EZ-Ex7eYx2I", "👮"),
    ("The fact that prison changed\nhim this much is hilarious 🔒😭", "MikeMemesYou", "0N95mZPUcgE", "🔒"),
    ("The fact that he didn't even\nthink twice is hilarious 🏃😭", "MikeMemesYou", "CO5XnBuyZIU", "🏃"),
]


@pytest.mark.parametrize("headline, channel, short_id, emoji", HEADLINE_SHORTS)
def test_your_real_headlines_find_their_shorts_by_the_emoji(headline, channel, short_id, emoji):
    made = datetime.fromisoformat("2026-10-01T10:00:00+03:00")
    data = {"name": "p", "channel": channel, "title": headline, "created": made.isoformat(),
            "competitor": {"title": "a competitor's own words"},
            "steps": {"download": {"state": "done", "competitor": {"duration": 40.0}}}}
    after = U.Facts(41.0, made + timedelta(hours=5))
    facts = {s.id: after for s in REAL[channel]}                            # every Short of the channel would fit
    m = U.compare([data], ALL, facts)
    assert [(x.short.id, x.exact) for x in m.near + m.exact] == [(short_id, False)]
    assert m.near[0].why == f"the same {emoji}, 41 s long like the competitor (40 s), uploaded 5 h after the " \
                            f"project was made"


def test_the_shorts_worth_reading_in_full():
    assert [s.id for s in U.probable_candidates([CHOCOLATE], ALL)] == [CHOC_SHORT]
    assert U.probable_candidates([CHOCOLATE], ALL, skip=[CHOC_SHORT]) == []
    assert U.probable_candidates([_variant(steps__download__competitor={})], ALL) == []
    assert U.probable_candidates([_variant(board={"uploaded": {"id": "x", "at": "2026-10-18T03:00:00+03:00"}})],
                                 ALL) == []
    cops = {"channel": "MikeMemesYou", "title": HEADLINE_SHORTS[1][0], "created": "2026-10-01T10:00:00+03:00",
            "steps": {"download": {"competitor": {"duration": 30.0}}}}
    assert [s.id for s in U.probable_candidates([cops], ALL)] == ["EZ-Ex7eYx2I"]


def test_the_shorts_facts_are_read_once_and_kept(tmp_path, monkeypatch):
    calls = []

    def facts(s, timeout_s=60, cancel=None):
        calls.append(s.id)
        return None if s.id == "fmpdQoibTEc" else U.Facts(20.0, NOW)       # one cannot be read
    monkeypatch.setattr(U, "video_facts", facts)
    state = tmp_path / "s.json"
    chk = U.UploadCheck(state, tabs=TABS3)
    three = REAL["Flick707"][:3]
    got = chk.facts(three, now=NOW)
    assert calls == [s.id for s in three] and set(got) == {three[0].id, three[2].id}
    assert got[three[0].id] == U.Facts(20.0, NOW)
    calls.clear()
    assert set(U.UploadCheck(state, tabs=TABS3).facts(three, now=NOW)) == {three[0].id, three[2].id}
    assert calls == ["fmpdQoibTEc"]                                         # only the one not read yet
    monkeypatch.setattr(U, "FACTS_PER_RUN", 2)
    calls.clear()
    chk.facts(REAL["Flick707"], now=NOW)
    assert len(calls) == 2                                                  # the rest next time
    monkeypatch.setattr(U, "FACTS_KEEP", 3)
    chk.facts(REAL["Flick707"][5:8], now=NOW + timedelta(minutes=1))
    assert len(chk.state["videos"]) == 3                                    # the oldest reads went


def test_the_whole_check_finds_the_chocolate_upload(tmp_path, monkeypatch):
    chk = U.UploadCheck(tmp_path / "s.json", tabs=TABS3)
    _fake_fetch(monkeypatch, {ch: REAL[ch] for ch in CHANNELS})
    read = []

    def facts(s, timeout_s=60, cancel=None):
        read.append(s.id)
        return CHOC_FACTS if s.id == CHOC_SHORT else U.Facts(60.0, NOW)
    monkeypatch.setattr(U, "video_facts", facts)
    board_card = type("BoardCard", (), {})()
    board_card.folder, board_card.name, board_card.channel = Path("D:/Shared/Projects") / CHOCOLATE["name"], \
        CHOCOLATE["name"], "LouLaughs"
    board_card.title, board_card.uploaded, board_card.not_same, board_card.data = CHOCOLATE["title"], None, [], \
        CHOCOLATE
    res = chk.run([board_card], NOW)
    assert res.exact == [] and read == [CHOC_SHORT]                         # only the one with a hint was read
    key, short, score = res.near[0]
    assert key == str(board_card.folder) and short.id == CHOC_SHORT and res.near[0].at == CHOC_FACTS.at
    assert "uploaded 51 min after the edit was made" in res.near[0].why
    read.clear()
    assert chk.run([board_card], NOW + timedelta(minutes=30)).near[0].short.id == CHOC_SHORT and read == []


# ---------------------------------------------------------------------------------------------------------------------
# reading a tab: no internet, errors, Stop (yt-dlp replaced)
# ---------------------------------------------------------------------------------------------------------------------

def _fake_run(monkeypatch, code=0, out="", err="", exc=None, seen=None):
    def run(cmd, timeout_s, cancel=None):
        if seen is not None:
            seen.append((cmd, timeout_s))
        if exc is not None:
            raise exc
        return code, out, err
    monkeypatch.setattr(D, "_run_capture", run)


def test_fetch_shorts_reads_the_tab(monkeypatch):
    seen = []
    _fake_run(monkeypatch, out=json.dumps(TABS["Flick707"]), seen=seen)
    got = U.fetch_shorts("https://www.youtube.com/@Flick707/shorts")
    assert got == REAL["Flick707"] and seen[0][1] == U.TAB_TIMEOUT_S
    assert "--no-playlist" not in seen[0][0] and seen[0][0][-1] == "https://www.youtube.com/@Flick707/shorts"
    assert U.fetch_shorts("https://www.youtube.com/@Flick707/shorts", channel="Flick 707")[0].channel == "Flick 707"


@pytest.mark.parametrize("err", [NO_DNS, REFUSED,
                                 "ERROR: [youtube:tab] x: Unable to download API page: The read operation timed out"])
def test_no_internet_is_offline(monkeypatch, err):
    _fake_run(monkeypatch, code=1, err=err)
    with pytest.raises(U.Offline):
        U.fetch_shorts("https://www.youtube.com/@Flick707/shorts")


def test_no_answer_in_time_is_offline(monkeypatch):
    _fake_run(monkeypatch, exc=D.DownloadError("the link did not answer within 2 minutes (slow internet?)",
                                               "timeout"))
    with pytest.raises(U.Offline):
        U.fetch_shorts("https://www.youtube.com/@Flick707/shorts")


@pytest.mark.parametrize("code, out, err, words", [
    (1, "", NO_TAB, "the Shorts tab could not be read: This channel does not have a shorts tab"),
    (1, "", "ERROR: [youtube:tab] x: Sign in to confirm you're not a bot.", D.BOT),
    (0, "{not json", "", "yt-dlp's answer about the Shorts tab cannot be read"),
    (0, "", "", "the Shorts tab could not be read (see the log)"),
])
def test_other_failures_say_why(monkeypatch, code, out, err, words):
    _fake_run(monkeypatch, code=code, out=out, err=err)
    with pytest.raises(U.CheckError) as e:
        U.fetch_shorts("https://www.youtube.com/@SomeChannel/shorts")
    assert e.value.plain == words


def test_yt_dlp_missing_is_a_check_error(monkeypatch):
    _fake_run(monkeypatch, exc=FileNotFoundError(2, "The system cannot find the file specified"))
    with pytest.raises(U.CheckError, match="could not be started"):
        U.fetch_shorts("https://www.youtube.com/@Flick707/shorts")


FAKE_YTDLP = r'''
import json, sys, time
args = sys.argv[1:]
url = args[-1]
mode = open(sys.argv[0] + ".mode", encoding="utf-8").read().strip()
if mode == "hang":
    time.sleep(60)
if mode == "offline":
    sys.stderr.write("ERROR: [youtube:tab] x: Unable to download API page: <urlopen error [Errno 11001] getaddrinfo "
                     "failed>\n")
    sys.exit(1)
tabs = json.load(open(sys.argv[0] + ".json", encoding="utf-8"))
if "--flat-playlist" in args:
    handle = url.split("/@")[1].split("/")[0]
    print(json.dumps(tabs[handle], ensure_ascii=False))
else:
    print(json.dumps(tabs["_videos"][url.rsplit("/", 1)[-1]], ensure_ascii=False))
'''


@pytest.fixture
def fake_ytdlp(tmp_path, monkeypatch):
    """A stand-in yt-dlp (a real subprocess, through download._run_capture) answering with the real JSON."""
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(FAKE_YTDLP, encoding="utf-8")
    Path(str(script) + ".json").write_text(json.dumps(dict(TABS, _videos=VIDEOS), ensure_ascii=False),
                                           encoding="utf-8")
    monkeypatch.setattr(D, "base_command", lambda: [sys.executable, str(script), "--no-playlist"])

    def mode(m="ok"):
        Path(str(script) + ".mode").write_text(m, encoding="utf-8")
    mode()
    return mode


def test_a_stand_in_ytdlp_end_to_end(fake_ytdlp):
    got = U.fetch_shorts("https://www.youtube.com/@MikeMemesYou/shorts")
    assert [s.id for s in got] == [s.id for s in REAL["MikeMemesYou"]]
    assert U.upload_time(BY_ID["pXBgisZ34JQ"]) == datetime(2026, 10, 18, 4, 0, 24, tzinfo=timezone.utc)
    fake_ytdlp("offline")
    with pytest.raises(U.Offline):
        U.fetch_shorts("https://www.youtube.com/@MikeMemesYou/shorts")
    assert U.upload_time(BY_ID["pXBgisZ34JQ"]) is None


def test_a_tab_that_hangs_is_offline_after_the_timeout_and_stop_kills_it(fake_ytdlp):
    fake_ytdlp("hang")
    t0 = time.time()
    with pytest.raises(U.Offline):
        U.fetch_shorts("https://www.youtube.com/@Flick707/shorts", timeout_s=1.5)
    assert time.time() - t0 < 20
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(D.Cancelled):
        U.fetch_shorts("https://www.youtube.com/@Flick707/shorts", cancel=cancel)
    assert time.time() - t0 < 40


# ---------------------------------------------------------------------------------------------------------------------
# the upload time
# ---------------------------------------------------------------------------------------------------------------------

def test_the_upload_time_from_the_real_json():
    at = U.info_time(VIDEOS["omBq76V5wQQ"])
    assert at == datetime(2026, 10, 17, 23, 34, 23, tzinfo=timezone.utc)
    assert at.isoformat() == at.astimezone().isoformat()                  # given in this computer's time
    assert U.info_time({"upload_date": "20261017"}) == datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    assert U.info_time({"release_timestamp": 1792296024}) == datetime(2026, 10, 18, 4, 0, 24, tzinfo=timezone.utc)
    for bad in ({}, {"timestamp": None, "upload_date": "2026-10-17"}, {"timestamp": True}, {"upload_date": "20261399"}):
        assert U.info_time(bad) is None


def test_upload_time_is_none_when_it_cannot_be_known(monkeypatch):
    seen = []
    _fake_run(monkeypatch, out=json.dumps(VIDEOS["pXBgisZ34JQ"]), seen=seen)
    assert U.upload_time(BY_ID["pXBgisZ34JQ"]) == datetime(2026, 10, 18, 4, 0, 24, tzinfo=timezone.utc)
    assert seen[0][0][-1] == "https://www.youtube.com/shorts/pXBgisZ34JQ" and "-J" in seen[0][0]
    assert "--flat-playlist" not in seen[0][0] and seen[0][1] == U.VIDEO_TIMEOUT_S
    _fake_run(monkeypatch, code=1, err=NO_DNS)
    assert U.upload_time(BY_ID["pXBgisZ34JQ"]) is None
    _fake_run(monkeypatch, out="not json")
    assert U.upload_time("https://www.youtube.com/shorts/pXBgisZ34JQ") is None
    _fake_run(monkeypatch, exc=D.Cancelled())
    assert U.upload_time(BY_ID["pXBgisZ34JQ"]) is None
    assert U.upload_time("") is None


# ---------------------------------------------------------------------------------------------------------------------
# the schedule and the whole check
# ---------------------------------------------------------------------------------------------------------------------

TABS3 = [(ch, f"https://www.youtube.com/@{ch}/shorts") for ch in ("Flick707", "LouLaughs", "MikeMemesYou")]


def _fake_fetch(monkeypatch, plan, calls=None):
    """fetch_shorts replaced: plan[channel] = the Shorts, or an exception to raise."""
    def fetch(url, timeout_s=U.TAB_TIMEOUT_S, cancel=None, channel=None, limit=U.TAB_LIMIT):
        if calls is not None:
            calls.append(channel)
        got = plan[channel]
        if isinstance(got, BaseException):
            raise got
        return list(got)
    monkeypatch.setattr(U, "fetch_shorts", fetch)


def test_due_at_the_start_then_every_30_minutes_and_kept_over_a_restart(tmp_path, monkeypatch):
    state = tmp_path / "MovieRecaps" / "uploads_state.json"
    chk = U.UploadCheck(state, tabs=TABS3)
    assert chk.due(NOW) and chk.last_check() is None
    _fake_fetch(monkeypatch, {ch: REAL[ch] for ch in CHANNELS})
    res = chk.run([], NOW, times=False)
    assert not res.offline and res.errors == [] and len(res.shorts) == 55
    assert not chk.due(NOW + timedelta(minutes=29))
    assert chk.due(NOW + timedelta(minutes=30))
    again = U.UploadCheck(state, tabs=TABS3)                                # the app started again
    assert not again.due(NOW + timedelta(minutes=10)) and again.due(NOW + timedelta(minutes=31))
    assert again.last_check("Flick707") == NOW and again.last_check() == NOW
    assert again.channel_state("MikeMemesYou")["shorts"] == 33
    assert again.due(NOW - timedelta(hours=2))                              # the clock went back: due
    assert again.due(NOW + timedelta(minutes=10), minutes=5)


def test_a_broken_state_file_starts_fresh(tmp_path):
    state = tmp_path / "uploads_state.json"
    state.write_text("{broken", encoding="utf-8")
    chk = U.UploadCheck(state, tabs=TABS3)
    assert chk.due(NOW) and chk.state["channels"] == {}


def test_the_state_file_lives_in_the_app_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    assert U.default_state_path() == tmp_path / "appdata" / "uploads_state.json"
    assert U.UploadCheck().path == tmp_path / "appdata" / "uploads_state.json"     # no arguments: the default
    monkeypatch.delenv("MOVIERECAPS_APPDATA")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert U.default_state_path() == tmp_path / "Roaming" / "MovieRecaps" / "uploads_state.json"


def test_no_internet_skips_quietly_and_tries_again_in_5_minutes(tmp_path, monkeypatch):
    chk = U.UploadCheck(tmp_path / "s.json", tabs=TABS3)
    calls = []
    _fake_fetch(monkeypatch, {"Flick707": U.Offline(D.NETWORK), "LouLaughs": REAL["LouLaughs"],
                              "MikeMemesYou": REAL["MikeMemesYou"]}, calls)
    res = chk.run([card("The Funniest Job Advice of All Time", "Flick707")], NOW)
    assert res.offline and res.errors == [] and res.exact == [] and calls == ["Flick707"]   # the rest not tried
    assert all(chk.channel_state(ch)["result"] == "offline" for ch, _ in TABS3)
    assert chk.last_check() is None
    assert not chk.due(NOW + timedelta(minutes=4)) and chk.due(NOW + timedelta(minutes=5))


def test_the_whole_check_finds_exact_and_near_with_their_times(tmp_path, monkeypatch):
    chk = U.UploadCheck(tmp_path / "s.json", tabs=TABS3)
    _fake_fetch(monkeypatch, {"Flick707": REAL["Flick707"], "LouLaughs": U.CheckError(D.RATE),
                              "MikeMemesYou": REAL["MikeMemesYou"]})
    times = {"pXBgisZ34JQ": datetime(2026, 10, 18, 7, 0, 24).astimezone(), "Y_QvArZrq8o": None}
    monkeypatch.setattr(U, "video_facts", lambda s, timeout_s=60, cancel=None: U.Facts(21.0, times[s.id]))
    cards = [card("When Grandpa Wants You To Lie For Him 💀😭", "Flick707", key="grandpa"),
             card("The Funniest Diversity Trainnig of All Time", "MikeMemesYou", key="diversity"),
             card(NIGHT10["Flick707"], "Flick707", key="the-part-is-hers"),
             card("The Funniest Allergic Reaction of All Time", "LouLaughs", key="allergic")]   # Lou not read
    res = chk.run(cards, NOW)
    assert [(m.card, m.short.id, m.at) for m in res.exact] == [("grandpa", "pXBgisZ34JQ", times["pXBgisZ34JQ"])]
    assert [(m.card, m.short.id, m.at) for m in res.near] == [("diversity", "Y_QvArZrq8o", None)]
    assert res.errors == [f"LouLaughs: {D.RATE}"] and not res.offline and not res.cancelled
    assert chk.channel_state("LouLaughs")["result"] == "error" and chk.channel_state("Flick707")["result"] == "ok"
    assert not chk.due(NOW + timedelta(minutes=10))                         # an error waits the 30 minutes too


def test_the_check_never_raises_and_stop_cancels(tmp_path, monkeypatch):
    chk = U.UploadCheck(tmp_path / "s.json", tabs=TABS3)
    _fake_fetch(monkeypatch, {"Flick707": RuntimeError("boom"), "LouLaughs": REAL["LouLaughs"],
                              "MikeMemesYou": D.Cancelled()})
    res = chk.run([], NOW)
    assert res.errors == ["Flick707: the check went wrong (RuntimeError: boom)"] and res.cancelled
    cancel = threading.Event()
    cancel.set()
    res = chk.run([], NOW, cancel=cancel)
    assert res.cancelled and res.shorts == []
    monkeypatch.setattr(U, "compare", lambda cards, shorts: 1 / 0)
    _fake_fetch(monkeypatch, {ch: REAL[ch] for ch in CHANNELS})
    res = chk.run([], NOW)
    assert res.errors and "could not be compared" in res.errors[0]


def test_two_checks_at_once_do_not_overlap(tmp_path, monkeypatch):
    chk = U.UploadCheck(tmp_path / "s.json", tabs=TABS3)
    started, go = threading.Event(), threading.Event()

    def slow(url, timeout_s=U.TAB_TIMEOUT_S, cancel=None, channel=None, limit=U.TAB_LIMIT):
        started.set()
        go.wait(10)
        return []
    monkeypatch.setattr(U, "fetch_shorts", slow)
    box = {}
    th = threading.Thread(target=lambda: box.update(r=chk.run([], NOW)))
    th.start()
    started.wait(10)
    assert chk.run([], NOW).errors == ["a check is running already"]
    go.set()
    th.join(10)
    assert box["r"].errors == []


def test_the_state_file_unwritable_is_not_an_error(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    chk = U.UploadCheck(blocker / "sub" / "s.json", tabs=TABS3)              # its folder cannot be made
    _fake_fetch(monkeypatch, {ch: REAL[ch] for ch in CHANNELS})
    res = chk.run([], NOW, times=False)
    assert res.errors == [] and len(res.shorts) == 55 and not chk.due(NOW + timedelta(minutes=1))
