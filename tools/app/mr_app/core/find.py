"""SEARCH (night 15, task 11): what a search box's text means, the same everywhere.

* an episode, written any usual way -- ``S09E05``, ``s9e5``, ``9x05``, ``ep 5``, ``episode 5``, ``e05`` -- finds the
  file of that episode (any season when you gave none: "ep 5" finds S01E05 and S09E05);
* anything else: every word in the text, in any order, upper or lower case ("dad school" finds "American Dad After
  School Special").
"""
from __future__ import annotations

import html
import re

_QUERY_EP = [
    re.compile(r"^\s*s(\d{1,2})\s*[\s._-]?\s*e(\d{1,3})\s*$", re.I),            # S09E05, s9 e5
    re.compile(r"^\s*(\d{1,2})x(\d{1,3})\s*$", re.I),                            # 9x05
    re.compile(r"^\s*(?:ep|episode|e)\.?\s*(\d{1,3})\s*$", re.I),                # ep 5, episode 5, e05
]
_NAME_EP = [
    re.compile(r"(?<![a-z0-9])s(\d{1,2})[\s._-]?e(\d{1,3})(?!\d)", re.I),        # S09E05, S9.E5
    re.compile(r"(?<![0-9])(\d{1,2})x(\d{1,3})(?![0-9])", re.I),                 # 9x05
    re.compile(r"(?<![a-z])(?:episode|ep)[\s._-]*(\d{1,3})(?!\d)", re.I),        # Episode 5, Ep05
]
_TAG = re.compile(r"<[^>]+>")


def episode_query(text: str) -> tuple[int | None, int] | None:
    """(season or None, episode) when the text asks for an episode; else None."""
    for i, rx in enumerate(_QUERY_EP):
        m = rx.match(text or "")
        if m:
            if i == 2:
                return None, int(m.group(1))
            return int(m.group(1)), int(m.group(2))
    return None


def episode_of(name: str) -> tuple[int | None, int] | None:
    """The (season, episode) a file name says, None when it says none."""
    for i, rx in enumerate(_NAME_EP):
        m = rx.search(name or "")
        if m:
            if i == 2:
                return None, int(m.group(1))
            return int(m.group(1)), int(m.group(2))
    return None


def matches(query: str, text: str) -> bool:
    """Does ``text`` (a file name, a row's words) answer the search ``query``? An empty search matches everything."""
    q = (query or "").strip()
    if not q:
        return True
    ep = episode_query(q)
    if ep is not None:
        have = episode_of(text)
        if have is None:
            return False
        season, number = ep
        return have[1] == number and (season is None or have[0] is None or have[0] == season)
    low = (text or "").lower()
    return all(w in low for w in q.lower().split())


def plain_text(text: str) -> str:
    """A label's text without its rich-text tags (what you read on the screen)."""
    return html.unescape(_TAG.sub(" ", text or "")).replace("\xa0", " ")


def count_line(shown: int, total: int) -> str:
    """'12 of 24' (every one: '24 files')."""
    return f"{total} file{'s' if total != 1 else ''}" if shown == total else f"{shown} of {total}"
