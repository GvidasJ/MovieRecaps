"""UPLOADED: the automatic "Uploaded" check. Every 30 minutes (and at the app's start) the Shorts tab of each channel
is read with yt-dlp -- a flat playlist: only the Shorts' ids and titles, about a second a channel -- and the titles are
matched to the board's saved titles: the title planned for YouTube (upload_title) first, then the headline. An exact
match moves the card to Uploaded with the time the Short went up; a near match asks first ("Is this the same
video?"). No internet: the check is skipped quietly and tried again a few minutes later.

* ``channel_tabs()``: (channel, its Shorts tab) from assets/channels/<Channel>/channel.json.
* ``fetch_shorts(url)``: the newest 100 Shorts of a tab (``Offline`` when there is no internet, ``CheckError`` in
  plain words for anything else).
* ``norm(title)``: a title as it is compared -- emoji, symbols, #hashtags, punctuation, case and accents dropped.
* ``compare(cards, shorts, facts)``: the exact and the near matches, one Short to one card at most.
* ``upload_time(short)`` / ``video_facts(short)``: when a Short went up and how long it is (one more yt-dlp call,
  about 2 s).
* ``UploadCheck``: when the next check is due (a small JSON keeps the last check of each channel) and the check itself.

Near: the titles of one channel share a template ("The Funniest ... of All Time", "The fact that ... is hilarious"),
so two different videos often differ by one word only. similarity() therefore weights each word by how rare it is
among all the titles at hand (the Shorts read and the cards): the template's words count little, the words that say
what the video is count a lot. A typo (one letter changed, added, dropped or two neighbours swapped, in a word of 5+
letters) counts as the same word; titles that differ only in their spaces ("Spider-Man" / "Spiderman") are near too.

Probable (a near match too: it asks first): your YouTube title is usually NOT the headline saved in the app ("The
fact that this meme was an actual ad is hilarious 🍫😭" went up as "The Commercial Behind The Nestle Crunch Meme
🍫😭"), so a card also matches a Short of its channel when everything else agrees: the same emoji that says what the
video is (not 😭), or the competitor's title close to the Short's; the Short as long as the competitor (within
DURATION_SLACK_S or DURATION_SLACK_SHARE); and uploaded after the project was made. Lengths and upload times come from
video_facts(), kept per Short in the state file.

Everything slow (yt-dlp) runs hidden and is safe in a worker thread: a timeout, a cancel event, and no exception the
window has to catch from ``UploadCheck.run``."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import download as D, paths

log = logging.getLogger(__name__)

TAB_LIMIT = 100                 # the newest this many Shorts of a tab are read
TAB_TIMEOUT_S = 90.0
VIDEO_TIMEOUT_S = 60.0
CHECK_EVERY_MIN = 30.0          # a check of every channel this often ...
RETRY_OFFLINE_MIN = 5.0         # ... and this soon again after one that found no internet
NEAR_SHARE = 0.72               # near: at least this share of the (weighted) words the same (chosen on the real titles
#                                 of the three channels: their distinct titles reach 0.50 at most, a typo scores 0.85+,
#                                 the template's slot swapped for another title's -- another video -- 0.72+ in 1 %)
SPACES_ONLY = 0.99              # the score of two titles that differ only in their spaces
NOT_EQUAL_MAX = 0.995           # titles that are not equal score at most this (the same words in another order)
TYPO_SHARE = 0.9                # a word one typo away counts as this much of a word
SMOOTHING = 10                  # word weights: as if this many more titles had none of the words (few titles: every
#                                 word counts about the same)
TYPO_LETTERS = 5                # a typo counts in words this long (the shorter one at least TYPO_LETTERS - 1)
# probable: the Short is as long as the competitor ...
DURATION_SLACK_S = 2.5          # ... within this many seconds (YouTube rounds to whole seconds; your trims) ...
DURATION_SLACK_SHARE = 0.08     # ... or this share of the competitor's length, the bigger
COMPETITOR_SHARE = 0.35         # the competitor's title this alike to the Short's counts as a hint
MADE_SLACK_MIN = 5.0            # uploaded no earlier than this before the project was made (two computers' clocks)
PROBABLE_SCORE = 0.6            # a probable match's score (below every title-near one): 0.65 with two hints
EMOJI_COMMON_SHARE = 0.2        # an emoji in more than this share of the titles says nothing about the video ...
ALWAYS_COMMON = frozenset("\U0001F62D\U0001F602\U0001F923")      # ... and 😭 😂 🤣 never do
FACTS_PER_RUN = 20              # at most this many Shorts read in full in one check (2 s each); the rest next time
FACTS_KEEP = 500                # the state file keeps the facts of this many Shorts at most
STATE_NAME = "uploads_state.json"
STATE_VERSION = 1

_YT_ID = re.compile(r"[0-9A-Za-z_-]{11}")
_HASHTAG = re.compile(r"#[^\W\d_]\w*")          # '#usa', '#shorts' -- not '#1'
_APOSTROPHES = dict.fromkeys(map(ord, "'’‘‛`´ʼ′"), None)
_TABS = r"videos|featured|shorts|streams|playlists|community|about|live|releases|podcasts|courses|store|posts"


class Offline(Exception):
    """No internet (no name lookup, no connection, or no answer in time): skip quietly, try again later."""


class CheckError(Exception):
    """A channel's Shorts could not be read for another reason: ``plain`` says why in plain words."""

    def __init__(self, plain: str, detail: str = ""):
        super().__init__(plain)
        self.plain = plain
        self.detail = detail


@dataclass(frozen=True)
class Short:
    """One Short on a channel's Shorts tab."""
    id: str
    title: str
    url: str
    channel: str


@dataclass(frozen=True)
class Card:
    """What the check needs of a board card (``card_of`` makes one from project.json). board.Card objects, plain
    dicts (project.json's data itself, or with these names) and other objects with these names work as well."""
    key: str                          # the project's folder name
    channel: str = ""                 # '' = any channel
    title: str = ""                   # the headline (the template's)
    uploaded: Any = None              # None until it is uploaded; the board's {"at", "id", "url", "title", "how"}
    not_same: tuple[str, ...] = ()    # Short ids you said are not this video
    upload_title: str = ""            # the title planned for YouTube ('' = none)
    data: Mapping = field(default_factory=dict, repr=False, compare=False)   # project.json (for the probable rule)


@dataclass(frozen=True)
class Match:
    """A card and the Short that is (exact) or may be (near) its upload. It also unpacks like a tuple: an exact one
    as (card, short), a near one as (card, short, score)."""
    card: str                         # the card's key (a board card's folder, as text)
    short: Short
    exact: bool
    score: float                      # 1.0 exact; near: how alike (NEAR_SHARE..1; a probable one PROBABLE_SCORE)
    card_title: str = ""
    at: datetime | None = None        # when the Short went up (UploadCheck.run fills it); None: unknown -- use now
    why: str = ""                     # in plain words: "the same title", "the titles are 84 % alike", "the same 🍫 ..."

    def _items(self) -> tuple:
        return (self.card, self.short) if self.exact else (self.card, self.short, self.score)

    def __iter__(self):
        return iter(self._items())

    def __len__(self) -> int:
        return len(self._items())

    def __getitem__(self, i):
        return self._items()[i]

    def as_uploaded(self, how: str = "auto", now: datetime | None = None) -> dict:
        """The board's "uploaded" dict for this match: {"at", "id", "url", "title", "how"} (how: "auto" -- found by the
        check -- or "you" -- the Yes of a near match); "at" is now when the upload time is unknown."""
        at = self.at or _local(now)
        return {"at": at.isoformat(timespec="seconds"), "id": self.short.id, "url": self.short.url,
                "title": self.short.title, "how": how}


@dataclass
class Matches:
    exact: list[Match] = field(default_factory=list)
    near: list[Match] = field(default_factory=list)


@dataclass
class CheckResult:
    """One check of every channel. ``offline``: no internet (nothing to show, it is tried again soon); ``errors``:
    channels that could not be read, in plain words ('Flick707: ...')."""
    exact: list[Match] = field(default_factory=list)
    near: list[Match] = field(default_factory=list)
    offline: bool = False
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    shorts: list[Short] = field(default_factory=list)
    seconds: float = 0.0


# ---------------------------------------------------------------------------------------------------------------------
# the channels' Shorts tabs
# ---------------------------------------------------------------------------------------------------------------------

def shorts_tab(link: str, handle: str = "") -> str:
    """A channel's link -> its Shorts tab: 'https://www.youtube.com/@LouLaughs' -> '.../@LouLaughs/shorts' (a link to
    another tab -- /videos, /featured ... -- goes to /shorts; no link: from the @handle)."""
    u = (link or "").strip()
    if not u and handle:
        h = handle.strip()
        u = "https://www.youtube.com/" + (h if h.startswith("@") else "@" + h)
    u = re.split(r"[?#]", u)[0].rstrip("/")
    u = re.sub(rf"/(?:{_TABS})$", "", u, flags=re.I)
    return u + "/shorts" if u else ""


def channel_tabs(root: str | os.PathLike | None = None) -> list[tuple[str, str]]:
    """(channel, its Shorts tab) of every channel folder -- assets/channels/<Channel> with a channel.json or a
    template.psd -- sorted by name: [('Flick707', 'https://www.youtube.com/@Flick707/shorts'), ...]."""
    from . import channels as C
    folder = Path(root) if root is not None else paths.channels_dir()
    out: list[tuple[str, str]] = []
    if not folder.is_dir():
        return out
    for d in sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if not ((d / C.INFO_NAME).is_file() or (d / C.PSD_NAME).is_file()):
            continue
        info = C.read_info(d)
        tab = shorts_tab(str(info.get("link") or ""), str(info.get("handle") or ""))
        if tab:
            out.append((str(info.get("name") or d.name), tab))
    return out


def handle_of(url: str) -> str:
    """'https://www.youtube.com/@LouLaughs/shorts' -> 'LouLaughs' ('' when the link has no @handle)."""
    m = re.search(r"/@([^/?#]+)", url or "")
    return m.group(1) if m else ""


def tab_command(url: str, limit: int = TAB_LIMIT) -> list[str]:
    """yt-dlp's command for a Shorts tab: download.base_command() (the venv's yt-dlp, deno for YouTube) without its
    --no-playlist -- a tab is a playlist -- flat (ids and titles only, no video pages), one JSON, the newest
    ``limit``."""
    return [*(c for c in D.base_command() if c != "--no-playlist"), "--flat-playlist", "-J", "--playlist-end",
            str(int(limit)), url]


def _plain(err: str, out: str = "") -> str:
    """yt-dlp's failure in plain words (download.plain_error), said for a Shorts tab, without the tab's own name
    ('@Flick707/shorts: ')."""
    words = D.plain_error((err or "") + "\n" + (out or ""))
    if words.startswith("the download did not work"):
        words = "the Shorts tab could not be read" + words[len("the download did not work"):]
        words = re.sub(r"^(the Shorts tab could not be read: )@[\w.-]+(?:/[\w.-]+)?:\s+", r"\1", words)
    return words


def fetch_shorts(url: str, timeout_s: float = TAB_TIMEOUT_S, cancel: threading.Event | None = None,
                 channel: str | None = None, limit: int = TAB_LIMIT) -> list[Short]:
    """The newest ``limit`` Shorts of a Shorts tab (about a second). ``channel`` names them (default: the @handle).
    Offline: no internet, no name lookup, or no answer within ``timeout_s``; CheckError: anything else, in plain
    words; download.Cancelled when ``cancel`` is set (the yt-dlp process is killed)."""
    name = channel if channel is not None else handle_of(url)
    try:
        with D._using_ytdlp():
            code, out, err = D._run_capture(tab_command(url, limit), timeout_s, cancel)
    except D.DownloadError as e:              # _run_capture's timeout: the connection hangs
        raise Offline(e.plain) from e
    except OSError as e:
        raise CheckError(f"yt-dlp could not be started: {e}") from e
    if code != 0 or not out.strip():
        words = _plain(err, out)
        if words == D.NETWORK:
            raise Offline(words)
        raise CheckError(words, D._last_error(err))
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise CheckError("yt-dlp's answer about the Shorts tab cannot be read", str(e)) from e
    return parse_tab(data, name)


def parse_tab(data: Mapping, channel: str) -> list[Short]:
    """yt-dlp's flat JSON of a tab -> its Shorts, in the tab's order (newest first); entries without a YouTube id are
    left out, a repeated id is kept once."""
    out: list[Short] = []
    seen: set[str] = set()

    def walk(entries: Any) -> None:
        for e in entries or []:
            if not isinstance(e, Mapping):
                continue
            if e.get("_type") == "playlist" or isinstance(e.get("entries"), list):
                walk(e.get("entries"))
                continue
            vid = str(e.get("id") or "")
            if not _YT_ID.fullmatch(vid) or vid in seen:
                continue
            seen.add(vid)
            url = str(e.get("url") or e.get("webpage_url") or "")
            if not url.startswith("http"):
                url = f"https://www.youtube.com/shorts/{vid}"
            out.append(Short(vid, str(e.get("title") or ""), url, channel))
    walk(data.get("entries") if isinstance(data, Mapping) else None)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# when a Short went up
# ---------------------------------------------------------------------------------------------------------------------

def info_time(info: Mapping) -> datetime | None:
    """The upload time in yt-dlp's JSON of a video, local: its timestamp (or release_timestamp); only a date
    (upload_date '20261017'): that day's midday UTC, so the day stays right; None when it says neither."""
    for key in ("timestamp", "release_timestamp"):
        ts = info.get(key)
        if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts) and ts > 0:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone()
    for key in ("upload_date", "release_date"):
        day = str(info.get(key) or "")
        if re.fullmatch(r"\d{8}", day):
            try:
                return datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 12, tzinfo=timezone.utc).astimezone()
            except ValueError:
                continue
    return None


@dataclass(frozen=True)
class Facts:
    """What a Short's own page says: how long it is and when it went up (local)."""
    duration: float | None
    at: datetime | None


def info_facts(info: Mapping) -> Facts:
    """yt-dlp's JSON of a video -> its Facts."""
    d = info.get("duration")
    ok = isinstance(d, (int, float)) and not isinstance(d, bool) and math.isfinite(d) and d > 0
    return Facts(float(d) if ok else None, info_time(info))


def video_facts(short: Short | str, timeout_s: float = VIDEO_TIMEOUT_S,
                cancel: threading.Event | None = None) -> Facts | None:
    """A Short's (or a video link's) length and upload time: one non-flat ``yt-dlp -J`` of it (about 2 s). None when
    it cannot be read -- no internet, an error, Stop."""
    url = short.url if isinstance(short, Short) else str(short or "")
    if not url:
        return None
    try:
        with D._using_ytdlp():
            code, out, _err = D._run_capture([*D.base_command(), "-J", url], timeout_s, cancel)
        if code != 0 or not out.strip():
            return None
        info = json.loads(out)
        return info_facts(info) if isinstance(info, Mapping) else None
    except (D.DownloadError, D.Cancelled, OSError, ValueError) as e:
        log.debug("facts of %s: %s", url, e)
        return None


def upload_time(short: Short | str, timeout_s: float = VIDEO_TIMEOUT_S,
                cancel: threading.Event | None = None) -> datetime | None:
    """When a Short (or a video's link) went up, local time (video_facts: about 2 s). None when it cannot be known --
    no internet, an error, Stop -- and the caller uses "now"."""
    f = video_facts(short, timeout_s, cancel)
    return f.at if f is not None else None


# ---------------------------------------------------------------------------------------------------------------------
# the titles
# ---------------------------------------------------------------------------------------------------------------------

def norm(title: str) -> str:
    """A title as it is compared: emoji and symbols dropped (with their joiners, skin tones and variation selectors),
    #hashtags dropped ('#usa', '#shorts': tags, not what the title says; '#1' stays '1'), lower case, apostrophes
    dropped ("Couldn't" -> 'couldnt'), other punctuation a space, '&' -> 'and', accents made plain, spaces collapsed.
    'When she finally got the part 💀😭' -> 'when she finally got the part'."""
    t = unicodedata.normalize("NFKC", str(title or ""))
    t = _HASHTAG.sub(" ", t).replace("&", " and ").translate(_APOSTROPHES)
    t = unicodedata.normalize("NFKD", t.casefold())
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Cf", "Cs"):         # accents, keycaps, variation selectors; ZWJ, tags; surrogates
            continue
        out.append(ch if cat[0] in "LN" else " ")   # punctuation, symbols, emoji, skin tones, spaces: a space
    return " ".join("".join(out).split())


def _regional(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _emoji_char(ch: str) -> bool:
    """A picture character: a symbol from the arrows on (not ° © ™), or half a flag."""
    return _regional(ch) or (unicodedata.category(ch) == "So" and ord(ch) >= 0x2190)


def emoji_keys(title: str) -> set[str]:
    """The emoji of a title, each as its plain picture: skin tones, variation selectors, keycaps and the ♂ / ♀ of a
    joined emoji dropped ('🏃‍♂️' -> '🏃', '👮🏽‍♂️' -> '👮', '⚖️' -> '⚖'); joined emoji stay one ('👨‍👦'), a flag's
    two letters stay one."""
    keys: list[str] = []
    cur, join = "", False
    for ch in unicodedata.normalize("NFC", str(title or "")):
        o = ord(ch)
        if o == 0x200D:                                         # zero width joiner
            join = bool(cur)
            continue
        if o in (0xFE0E, 0xFE0F, 0x20E3) or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F:
            continue                                            # variation selectors, keycap, skin tones, tags
        if _emoji_char(ch):
            if join and o in (0x2640, 0x2642):                  # the ♀ / ♂ of a joined emoji
                join = False
                continue
            if cur and (join or (_regional(ch) and len(cur) == 1 and _regional(cur))):
                cur += ch
            else:
                if cur:
                    keys.append(cur)
                cur = ch
        elif cur:
            keys.append(cur)
            cur = ""
        join = False
    if cur:
        keys.append(cur)
    return set(keys)


def _one_edit(a: str, b: str) -> bool:
    """One letter changed, added or dropped, or two neighbours swapped."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]]
                                  and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def typo(a: str, b: str) -> bool:
    """Two words that are one typo apart: letters only, the longer TYPO_LETTERS+ letters, the shorter at least one
    letter less ('alergic' / 'allergic', 'recieve' / 'receive', 'lesson' / 'lessons'); never two numbers."""
    return (a != b and a.isalpha() and b.isalpha() and max(len(a), len(b)) >= TYPO_LETTERS
            and min(len(a), len(b)) >= TYPO_LETTERS - 1 and _one_edit(a, b))


Weight = Callable[[str], float]


def word_weights(titles: Iterable[str]) -> Weight:
    """How much each word counts: the rarer among ``titles``, the more -- log((N + SMOOTHING + 1) / (titles with it +
    1)). A word every title has ('the', 'funniest', 'of all time') counts little; a word of one title a lot."""
    docs = [set(norm(t).split()) for t in titles]
    df = Counter(w for d in docs for w in d)
    top = len(docs) + SMOOTHING + 1
    return lambda w: math.log(top / (df.get(w, 0) + 1))


def _same_weight(a: Sequence[str], b: Sequence[str], weight: Weight) -> float:
    """The weight of the words both titles have (counted with repeats); then each word left pairs with a word left on
    the other side that is one typo apart (the heaviest first), counted TYPO_SHARE of their mean weight."""
    ca, cb = Counter(a), Counter(b)
    common = ca & cb
    total = sum(weight(w) * n for w, n in common.items())
    left_b = list((cb - common).elements())
    for x in sorted((ca - common).elements(), key=weight, reverse=True):
        for k, y in enumerate(left_b):
            if typo(x, y):
                total += TYPO_SHARE * (weight(x) + weight(y)) / 2
                del left_b[k]
                break
    return total


def similarity(a: str, b: str, weight: Weight | None = None) -> float:
    """How alike two titles are, 0..1 (normalised first): 1.0 the same; SPACES_ONLY when only the spaces differ;
    else the weight of the words both have (a typo counts TYPO_SHARE of a word) over the heavier title's weight, at
    most NOT_EQUAL_MAX. Without ``weight`` every word counts the same."""
    return _score(norm(a), norm(b), weight)


def _score(na: str, nb: str, weight: Weight | None = None) -> float:
    """similarity() of two titles normalised already."""
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na.replace(" ", "") == nb.replace(" ", ""):
        return SPACES_ONLY
    wt = weight or (lambda w: 1.0)
    wa, wb = na.split(), nb.split()
    total = max(sum(map(wt, wa)), sum(map(wt, wb)))
    return min(NOT_EQUAL_MAX, _same_weight(wa, wb, wt) / total) if total > 0 else 0.0


# ---------------------------------------------------------------------------------------------------------------------
# cards against Shorts
# ---------------------------------------------------------------------------------------------------------------------

def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def card_key(card: Any) -> str:
    """A card's key: its "key", else its folder (a board.Card's, as text), else its name."""
    for name in ("key", "folder", "name"):
        v = _field(card, name)
        if v:
            return str(v)
    return ""


def _board(card: Any) -> Mapping:
    """The card's project.json "board" dict ({} without one)."""
    b = _data(card).get("board")
    return b if isinstance(b, Mapping) else {}


def _not_same(card: Any) -> set[str]:
    """The Short ids you said are not this card's video: its not_same together with its project.json's
    board.not_same (a board.Card has both: .not_same and .data)."""
    out: set[str] = set()
    for ns in (_field(card, "not_same"), _board(card).get("not_same")):
        if isinstance(ns, (list, tuple, set, frozenset)):
            out.update(str(x) for x in ns)
    return out


def _uploaded(card: Any) -> Any:
    """The card's upload (the board's {"at", "id", ...}): its uploaded, else its project.json's board.uploaded."""
    return _field(card, "uploaded") or _board(card).get("uploaded") or None


def card_of(data: Mapping, key: str | None = None) -> Card:
    """A board card from a project's project.json data (``key``: its folder name; default: data["name"])."""
    board = data.get("board") if isinstance(data.get("board"), Mapping) else {}
    return Card(key=str(key or data.get("name") or ""), channel=str(data.get("channel") or ""),
                title=str(data.get("title") or ""), uploaded=board.get("uploaded") or None,
                not_same=tuple(str(x) for x in (board.get("not_same") or ())),
                upload_title=str(data.get("upload_title") or ""), data=dict(data))


def _data(card: Any) -> Mapping:
    """The card's project.json data: a board.Card's (or Card's) .data, else a dict card itself."""
    d = _field(card, "data")
    if isinstance(d, Mapping):
        return d
    return card if isinstance(card, Mapping) else {}


def card_titles(card: Any) -> list[tuple[str, str]]:
    """(kind, title) a card is matched by: its planned YouTube title first ("upload": .upload_title, or its
    project.json's), then its headline ("headline": .title) when that says something else."""
    up = str(_field(card, "upload_title") or "") or str(_data(card).get("upload_title") or "")
    head = str(_field(card, "title") or "")
    out = [("upload", up)] if norm(up) else []
    if norm(head) and norm(head) != norm(up):
        out.append(("headline", head))
    return out


def _num(v: Any) -> float | None:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
    return float(v) if ok else None


def card_facts(card: Any) -> tuple[str, float | None, datetime | None, datetime | None]:
    """(the competitor's title, its length in seconds, when the project was made, when its edit was made) from the
    card's project.json data (the length: the download's, else the convert's check); what is not known: '' / None."""
    data = _data(card)
    steps = data.get("steps") if isinstance(data.get("steps"), Mapping) else {}

    def sub(*keys: str) -> Any:
        d: Any = steps
        for k in keys:
            d = d.get(k) if isinstance(d, Mapping) else None
        return d
    dur = _num(sub("download", "competitor", "duration"))
    if dur is None:
        ds = sub("convert", "competitor", "checks", "duration_s")
        dur = _num(ds[-1]) if isinstance(ds, (list, tuple)) and ds else None
    comp = data.get("competitor") if isinstance(data.get("competitor"), Mapping) else {}
    edit = sub("edit")
    done = _parse(edit.get("finished")) if isinstance(edit, Mapping) and edit.get("state") == "done" else None
    return str(comp.get("title") or ""), dur, _parse(data.get("created")), done


def common_emoji(titles: Iterable[str]) -> set[str]:
    """The emoji that say nothing about which video it is: in more than EMOJI_COMMON_SHARE of ``titles`` (and in more
    than 3), and 😭 😂 🤣 always."""
    docs = [emoji_keys(t) for t in titles]
    df = Counter(k for d in docs for k in d)
    limit = max(3.0, EMOJI_COMMON_SHARE * len(docs))
    return {k for k, n in df.items() if n > limit} | set(ALWAYS_COMMON)


def _same_channel(card_channel: str, short_channel: str) -> bool:
    return not card_channel or card_channel.casefold() == (short_channel or "").casefold()


@dataclass
class _Open:
    """A card not uploaded yet, ready to compare."""
    card: Any
    titles: list[tuple[str, str, str]]          # (kind, title, normalised)
    channel: str
    not_same: set[str]
    facts: tuple[str, float | None, datetime | None, datetime | None]     # card_facts()
    emoji: set[str]


@dataclass
class _Prep:
    shorts: list[Short]
    normed: list[str]
    open: list[_Open]
    taken: set[str]
    weight: Weight
    common: set[str]


def _prepare(cards: Iterable[Any], shorts: Iterable[Short]) -> _Prep:
    """What compare() and probable_candidates() share: the open cards, the Shorts uploaded cards took, the weights."""
    cards = list(cards)
    shorts = [s for s in shorts if s.id]
    titles = [card_titles(c) for c in cards]
    every = [s.title for s in shorts] + [t for ts in titles for _, t in ts]
    p = _Prep(shorts, [norm(s.title) for s in shorts], [], set(), word_weights(every), common_emoji(every))
    uploaded_titles = []
    for c, ts in zip(cards, titles):
        channel = str(_field(c, "channel") or "")
        normed = [(k, t, norm(t)) for k, t in ts]
        up = _uploaded(c)
        if up:
            if isinstance(up, Mapping) and up.get("id"):
                p.taken.add(str(up["id"]))
            uploaded_titles += [(n, channel) for _, _, n in normed]
        else:
            p.open.append(_Open(c, normed, channel, _not_same(c), card_facts(c),
                                set().union(*(emoji_keys(t) for _, t in ts))))
    for s, ns in zip(p.shorts, p.normed):
        if ns and any(ns == t and _same_channel(ch, s.channel) for t, ch in uploaded_titles):
            p.taken.add(s.id)
    return p


def _hints(o: _Open, s: Short, ns: str, p: _Prep) -> tuple[set[str], float] | None:
    """(the emoji that say what the video is that both have, how alike the competitor's title is) -- None when
    neither hints that the Short is this card's video."""
    shared = (o.emoji & emoji_keys(s.title)) - p.common
    comp_title = o.facts[0]
    comp = _score(norm(comp_title), ns, p.weight) if comp_title and ns else 0.0
    return (shared, comp) if shared or comp >= COMPETITOR_SHARE else None


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


def _secs(x: float) -> str:
    return f"{x:.0f} s" if abs(x - round(x)) < 0.05 else f"{x:.1f} s"


def _span(td: timedelta) -> str:
    """'52 min', '3 h 5 min', '2 days'."""
    m = max(0, int(td.total_seconds() // 60))
    if m < 60:
        return f"{m} min"
    if m < 48 * 60:
        h, mm = divmod(m, 60)
        return f"{h} h {mm} min" if mm else f"{h} h"
    return f"{m // 1440} days"


def _probable(o: _Open, s: Short, ns: str, f: Facts, p: _Prep) -> tuple[float, str, str] | None:
    """(score, why, the card's title) when a Short of the card's channel is probably its upload: a hint (_hints), as
    long as the competitor and uploaded after the project was made; else None."""
    comp_title, comp_dur, made, edit_done = o.facts
    if not o.channel or comp_dur is None or made is None or f.duration is None or f.at is None:
        return None
    if f.at < made - timedelta(minutes=MADE_SLACK_MIN):
        return None
    if abs(f.duration - comp_dur) > max(DURATION_SLACK_S, DURATION_SLACK_SHARE * comp_dur):
        return None
    hint = _hints(o, s, ns, p)
    if hint is None:
        return None
    shared, comp = hint
    parts = [f"the same {' '.join(sorted(shared))}"] if shared else []
    if comp >= COMPETITOR_SHARE:
        parts.append(f"the competitor's title {_pct(comp)} alike")
    parts.append(f"{_secs(f.duration)} long like the competitor ({_secs(comp_dur)})")
    since, what = (edit_done, "the edit") if edit_done is not None and f.at >= edit_done else (made, "the project")
    parts.append(f"uploaded {_span(f.at - since)} after {what} was made")
    score = PROBABLE_SCORE + (0.05 if shared and comp >= COMPETITOR_SHARE else 0.0)
    return round(score, 4), ", ".join(parts), (o.titles[0][1] if o.titles else "")


def probable_candidates(cards: Iterable[Any], shorts: Iterable[Short], skip: Iterable[str] = ()) -> list[Short]:
    """The Shorts worth reading in full (video_facts) for the probable rule: of a card's channel, not taken, not said
    to be another video, with a hint (_hints), for a card whose competitor's length and making time are known.
    ``skip``: Short ids settled already (exact matches)."""
    p = _prepare(cards, shorts)
    skip = set(skip)
    out = []
    for s, ns in zip(p.shorts, p.normed):
        if s.id in p.taken or s.id in skip:
            continue
        for o in p.open:
            if (o.channel and _same_channel(o.channel, s.channel) and s.id not in o.not_same
                    and o.facts[1] is not None and o.facts[2] is not None and _hints(o, s, ns, p) is not None):
                out.append(s)
                break
    return out


def compare(cards: Iterable[Any], shorts: Iterable[Short], facts: Mapping[str, Facts] | None = None) -> Matches:
    """The cards (board.Card objects, Card, or dicts: key / folder, channel, title, upload_title, uploaded, not_same,
    data) against the Shorts read. A Short is compared only with the cards of its channel (a card without a channel:
    with every channel); cards already uploaded are skipped, and so are the Shorts they took (their uploaded id, or an
    exact title of the same channel) and the pairs in a card's not_same.

    Each card is matched by its planned YouTube title first, then by its headline (card_titles). exact: a normalised
    title is equal (moves the card by itself); near (asks first): not equal but similarity() >= NEAR_SHARE, the words
    weighted over every title given -- or, with ``facts`` (Short id -> video_facts), probable (_probable). One Short
    goes to one card at most and one card gets one Short at most: exact first, then near, then probable; the planned
    YouTube title before the headline; the most alike; then a card with a channel, the card given first, the Short
    higher on its tab (the newer). Quick (no yt-dlp)."""
    p = _prepare(cards, shorts)
    cands = []
    for si, (s, ns) in enumerate(zip(p.shorts, p.normed)):
        if s.id in p.taken:
            continue
        for ci, o in enumerate(p.open):
            if s.id in o.not_same or not _same_channel(o.channel, s.channel):
                continue
            best = None
            for kind, t, nt in (o.titles if ns else ()):
                up = kind == "upload"
                if ns == nt:
                    cand = (2, up, 1.0, t, "the same title" if up else "the same words as the headline")
                else:
                    sc = _score(ns, nt, p.weight)
                    if sc < NEAR_SHARE:
                        continue
                    cand = (1, up, sc, t, f"the titles are {_pct(sc)} alike" if up else
                            f"{_pct(sc)} alike the headline")
                if best is None or cand[:3] > best[:3]:
                    best = cand
            if best is None and facts and s.id in facts:
                pr = _probable(o, s, ns, facts[s.id], p)
                if pr is not None:
                    best = (0, False, pr[0], pr[2], pr[1])
            if best is not None:
                level, up, score, t, why = best
                cands.append(((level, up, score, bool(o.channel), -ci, -si), ci, s, t, why))
    cands.sort(key=lambda x: x[0], reverse=True)
    out = Matches()
    used_s, used_c = set(), set()
    for key, ci, s, t, why in cands:
        if s.id in used_s or ci in used_c:
            continue
        used_s.add(s.id)
        used_c.add(ci)
        level, score = key[0], key[2]
        m = Match(card_key(p.open[ci].card), s, level == 2, round(score, 4), t, None, why)
        (out.exact if level == 2 else out.near).append(m)
    order = {s.id: k for k, s in enumerate(p.shorts)}
    out.exact.sort(key=lambda m: order[m.short.id])
    out.near.sort(key=lambda m: (-m.score, order[m.short.id]))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the check: when, and all of it
# ---------------------------------------------------------------------------------------------------------------------

APPDATA_ENV = "MOVIERECAPS_APPDATA"


def app_data_dir() -> Path:
    """This computer's small state files: the MOVIERECAPS_APPDATA folder (the tests), else %APPDATA%\\MovieRecaps."""
    env = os.environ.get(APPDATA_ENV, "").strip()
    if env:
        return Path(env)
    base = os.environ.get("APPDATA", "").strip() or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "MovieRecaps"


def default_state_path() -> Path:
    """<app data>\\uploads_state.json (this computer's: app_data_dir())."""
    return app_data_dir() / STATE_NAME


def _local(dt: datetime | None) -> datetime:
    """Local time with its offset (now when None; a time without an offset is taken as local)."""
    return (dt or datetime.now()).astimezone()


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)).astimezone() if value else None
    except ValueError:
        return None


class UploadCheck:
    """The check's schedule and its runs. ``due(now)`` -- quick, from the GUI thread -- says whether a check should
    run; ``run(cards, now, cancel)`` -- slow (yt-dlp, a few seconds): in a worker thread -- reads every channel's
    Shorts tab, matches the Shorts with the cards and gets the upload time of each match (Match.at: no need to call
    upload_time again). The last check of each channel is kept in a small JSON (``state_path``; default
    app_data_dir()\\uploads_state.json: MOVIERECAPS_APPDATA, else %APPDATA%\\MovieRecaps), so the app
    checks at its start (call run() then) and every CHECK_EVERY_MIN minutes after it (call due() every minute or so);
    a check that found no internet is tried again after RETRY_OFFLINE_MIN minutes. ``tabs``: the (channel, url) list
    to read (default: channel_tabs() at each call)."""

    def __init__(self, state_path: str | os.PathLike | None = None,
                 tabs: Iterable[tuple[str, str]] | None = None):
        self.path = Path(state_path) if state_path is not None else default_state_path()
        self._tabs = list(tabs) if tabs is not None else None
        self._lock = threading.Lock()
        self._running = threading.Lock()
        self.state = self._load()

    def tabs(self) -> list[tuple[str, str]]:
        return list(self._tabs) if self._tabs is not None else channel_tabs()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("channels"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"version": STATE_VERSION, "channels": {}}

    def _save(self) -> None:
        with self._lock:
            text = json.dumps(self.state, ensure_ascii=False, indent=1)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:                      # the schedule is a convenience: the next check comes anyway
            log.info("uploads state not saved (%s): %s", self.path, e)
            try:
                tmp.unlink()
            except OSError:
                pass

    def _note(self, channel: str, now: datetime, result: str, error: str | None = None,
              count: int | None = None) -> None:
        with self._lock:
            st = self.state.setdefault("channels", {}).setdefault(channel, {})
            st.update(tried=now.isoformat(timespec="seconds"), result=result, error=error)
            if result == "ok":
                st.update(checked=st["tried"], shorts=count)

    def channel_state(self, channel: str) -> dict:
        """A copy of what the state file says about a channel: {tried, checked, result ('ok' | 'offline' | 'error'),
        error, shorts}."""
        with self._lock:
            return dict((self.state.get("channels") or {}).get(channel) or {})

    def last_check(self, channel: str | None = None) -> datetime | None:
        """The last good check of a channel (of every channel: the oldest of them; None while one was never
        checked)."""
        names = [channel] if channel else [c for c, _ in self.tabs()]
        times = [_parse(self.channel_state(c).get("checked")) for c in names]
        return None if not times or any(t is None for t in times) else min(times)

    def due(self, now: datetime | None = None, minutes: float = CHECK_EVERY_MIN) -> bool:
        """True when a channel was never tried, or its last try is ``minutes`` old (RETRY_OFFLINE_MIN after a try that
        found no internet; a clock that went back counts as due). Quick: reads the channels' channel.json only."""
        now = _local(now)
        for ch, _ in self.tabs():
            st = self.channel_state(ch)
            tried = _parse(st.get("tried"))
            if tried is None or tried > now + timedelta(minutes=1):
                return True
            wait = min(RETRY_OFFLINE_MIN, minutes) if st.get("result") == "offline" else minutes
            if now - tried >= timedelta(minutes=wait):
                return True
        return False

    # -- the Shorts' own facts (length, upload time), kept per Short -----------------------------------------------

    def _cached(self, vid: str) -> Facts | None:
        with self._lock:
            v = (self.state.get("videos") or {}).get(vid)
        if not isinstance(v, dict):
            return None
        return Facts(_num(v.get("duration")), _parse(v.get("at")))

    def _remember(self, vid: str, f: Facts, now: datetime) -> None:
        with self._lock:
            videos = self.state.setdefault("videos", {})
            videos[vid] = {"duration": f.duration, "at": f.at.isoformat(timespec="seconds") if f.at else None,
                           "read": now.isoformat(timespec="seconds")}
            if len(videos) > FACTS_KEEP:                     # the oldest reads go
                for old in sorted(videos, key=lambda k: str(videos[k].get("read") or ""))[:len(videos) - FACTS_KEEP]:
                    del videos[old]

    def facts(self, shorts: Iterable[Short], cancel: threading.Event | None = None,
              now: datetime | None = None) -> dict[str, Facts]:
        """{Short id: Facts} -- from the state file for a Short read before, else read now (video_facts, about 2 s
        each; at most FACTS_PER_RUN in one call, the rest next time). A Short that cannot be read is left out (tried
        again next time). Slow: a worker thread."""
        now = _local(now)
        out: dict[str, Facts] = {}
        read = 0
        for s in shorts:
            if s.id in out:
                continue
            f = self._cached(s.id)
            if f is None:
                if read >= FACTS_PER_RUN or (cancel is not None and cancel.is_set()):
                    continue
                read += 1
                f = video_facts(s, cancel=cancel)
                if f is None:
                    continue
                self._remember(s.id, f, now)
            out[s.id] = f
        if read:
            self._save()
        return out

    def run(self, cards: Iterable[Any], now: datetime | None = None, cancel: threading.Event | None = None,
            times: bool = True) -> CheckResult:
        """One check of every channel (seconds; a worker thread). Never raises: no internet -> offline=True (the
        channels not read yet are marked offline too, so the next try waits RETRY_OFFLINE_MIN); a channel that cannot
        be read -> its plain words in errors; Stop -> cancelled=True. The Shorts read are matched with ``cards``
        (compare). With ``times``: each match gets its upload time (Match.at; None when unknown), and the probable
        rule runs -- the Shorts it needs are read in full (facts(): about 2 s each the first time, kept after)."""
        t0 = time.time()
        res = CheckResult()
        if not self._running.acquire(blocking=False):
            res.errors.append("a check is running already")
            return res
        try:
            now = _local(now)
            cards = list(cards)
            tabs = self.tabs()
            for i, (ch, url) in enumerate(tabs):
                if cancel is not None and cancel.is_set():
                    res.cancelled = True
                    break
                try:
                    got = fetch_shorts(url, cancel=cancel, channel=ch)
                except D.Cancelled:
                    res.cancelled = True
                    break
                except Offline as e:
                    res.offline = True
                    for ch2, _ in tabs[i:]:
                        self._note(ch2, now, "offline", str(e))
                    break
                except CheckError as e:
                    res.errors.append(f"{ch}: {e.plain}")
                    self._note(ch, now, "error", e.plain)
                    continue
                except Exception as e:  # noqa: BLE001 - never an exception for the window
                    res.errors.append(f"{ch}: the check went wrong ({type(e).__name__}: {e})")
                    self._note(ch, now, "error", f"{type(e).__name__}: {e}")
                    continue
                res.shorts.extend(got)
                self._note(ch, now, "ok", None, len(got))
            self._save()
            if res.shorts and not res.cancelled:
                try:
                    m = compare(cards, res.shorts)
                    if times and not res.offline:
                        want = [x.short for x in m.exact + m.near]
                        want += probable_candidates(cards, res.shorts, skip={x.short.id for x in m.exact})
                        facts = self.facts(want, cancel, now)
                        if facts:
                            m = compare(cards, res.shorts, facts)

                        def timed(x: Match) -> Match:
                            f = facts.get(x.short.id)
                            return replace(x, at=f.at) if f is not None else x
                        m = Matches([timed(x) for x in m.exact], [timed(x) for x in m.near])
                except Exception as e:  # noqa: BLE001 - never an exception for the window
                    res.errors.append(f"the titles could not be compared ({type(e).__name__}: {e})")
                    m = Matches()
                res.exact, res.near = m.exact, m.near
                res.cancelled = res.cancelled or (cancel is not None and cancel.is_set())
        finally:
            self._running.release()
            res.seconds = round(time.time() - t0, 2)
        return res
