"""Suggestions for me (day 12, section 9): ideas a night or day run writes at its end into ``suggestions.md`` in the hub
(next to requests.md). The Learn tab shows them; "I want this" moves one into requests.md as an open wish (so the next
night's prompt has it) and takes it out of suggestions.md; "No" only takes it out.

The format, one entry per idea, easy to write by hand::

    - **Re-frame onto the speaker by itself**
      - why: when the face is outside the window -- saves about 3 minutes per video
      - size: half a night
      - from: day 12 (2026-10-19)

``size`` is ``small``, ``half a night`` or ``a full night``. Ideas of one run are written most useful first; runs add
theirs at the end (newest last). Readers are lenient (a plain ``- title`` bullet works, unknown ``- key: value`` lines
are kept). Changes go through learn_hub._change: under the file's lock, atomic, only when something changes."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import learn_hub as H

FILE = "suggestions.md"
HEAD = ("# Suggestions\n\n"
        "Ideas to improve the app, written by the night and day runs (most useful first, newer runs at the end).\n"
        "The app's Learn tab shows them: \"I want this\" moves one into requests.md, \"No\" removes it.\n"
        "Each: - **title**, then  - why: ...  - size: small / half a night / a full night  - from: <run> (<date>)\n\n")
SIZES = ("small", "half a night", "a full night")


@dataclass
class Suggestion:
    title: str
    why: str = ""
    size: str = ""
    source: str = ""            # "from": which run wrote it, with its date
    extra: dict = field(default_factory=dict)

    @property
    def date(self) -> str:
        m = re.search(r"\d{4}-\d\d-\d\d", self.source or "")
        return m.group(0) if m else ""

    def block(self) -> str:
        rows = [f"- **{_one(self.title)}**"]
        for k, v in (("why", self.why), ("size", self.size), ("from", self.source), *self.extra.items()):
            if _one(v):
                rows.append(f"  - {k}: {_one(v)}")
        return "\n".join(rows)

    def wish_text(self) -> str:
        """How it goes into requests.md."""
        bits = [_one(self.title)]
        if self.why:
            bits.append(f"— {_one(self.why)}")
        if self.size:
            bits.append(f"(size: {_one(self.size)})")
        return " ".join(bits) + (f" [suggested by {_one(self.source)}]" if self.source else "")


def _one(v) -> str:
    return " ".join(str(v if v is not None else "").split())


_HEAD = re.compile(r"^[-*] (?:\[[ xX]\]\s+)?(?:\*\*(?P<b>.+?)\*\*(?P<rest>.*)|(?P<t>.+))$")
_SUB = re.compile(r"^\s{1,8}[-*]\s+(?P<k>[^:]+?):\s?(?P<v>.*)$")


def path(folder: str | os.PathLike) -> Path:
    return Path(folder) / FILE


def _blocks(text: str) -> tuple[list[str], list[list[str]]]:
    pre: list[str] = []
    blocks: list[list[str]] = []
    for raw in str(text or "").splitlines():
        if _HEAD.match(raw.rstrip()) and not raw[:1].isspace():
            blocks.append([raw.rstrip()])
        elif blocks:
            blocks[-1].append(raw.rstrip())
        else:
            pre.append(raw.rstrip())
    return pre, blocks


def parse(text: str) -> list[Suggestion]:
    """The ideas of suggestions.md's text, in the file's order."""
    out: list[Suggestion] = []
    for b in _blocks(text)[1]:
        m = _HEAD.match(b[0])
        title = (m["b"] or m["t"] or "").strip()
        rest = (m["rest"] or "").strip(" -—·:")
        s = Suggestion(title)
        if rest:
            s.why = rest
        for raw in b[1:]:
            sm = _SUB.match(raw)
            if not sm:
                if raw.strip() and out is not None:
                    s.why = f"{s.why} {raw.strip()}".strip()
                continue
            k, v = sm["k"].strip(), sm["v"].strip()
            if k.lower() == "why":
                s.why = v
            elif k.lower() == "size":
                s.size = v
            elif k.lower() == "from":
                s.source = v
            else:
                s.extra[k] = v
        if s.title:
            out.append(s)
    return out


def read(folder: str | os.PathLike) -> list[Suggestion]:
    text = H._read(path(folder))
    return parse(text) if text else []


def newest_first(items: list[Suggestion]) -> list[Suggestion]:
    """The newest run's ideas first; within one run the file's order (most useful first)."""
    order = {id(s): i for i, s in enumerate(items)}
    groups: dict[str, int] = {}
    for i, s in enumerate(items):                            # a run's ideas: one group, by where it starts
        groups.setdefault(s.source, i)
    return sorted(items, key=lambda s: (s.date, groups[s.source], -order[id(s)]), reverse=True)


def add(folder: str | os.PathLike, items: list[Suggestion]) -> str:
    """Add ideas at the end of suggestions.md (made with its header); an idea with the same title is not added twice."""
    def change(old: str | None) -> str:
        text = old if old else HEAD
        have = {s.title.lower() for s in parse(text)}
        new = [s for s in items if s.title.strip() and s.title.lower() not in have]
        if not new:
            return text
        if not text.endswith("\n"):
            text += "\n"
        return text + "".join(s.block() + "\n" for s in new)
    return H._change(path(folder), change)


def remove(folder: str | os.PathLike, title: str) -> str:
    """Take the idea called ``title`` out of suggestions.md; "" or the problem (an idea that is not there any more is
    not a problem)."""
    def change(old: str | None) -> str:
        if not old:
            return old or ""
        pre, blocks = _blocks(old)
        keep = []
        for b in blocks:
            got = parse("\n".join(b))
            if got and got[0].title.strip().lower() == str(title).strip().lower():
                continue
            keep.append(b)
        if len(keep) == len(blocks):
            return old
        out = list(pre)
        while out and not out[-1].strip():
            out.pop()
        out.append("")
        for b in keep:
            while b and not b[-1].strip():
                b.pop()
            out += b
        return "\n".join(out).lstrip("\n") + "\n"
    if H._read(path(folder)) is None:
        return ""
    return H._change(path(folder), change)


def want(folder: str | os.PathLike, s: Suggestion, computer: str | None = None) -> str:
    """"I want this": the idea goes into requests.md as an open wish, then out of suggestions.md (only when the wish
    was saved). "" or the problem in plain words."""
    err = H.add_wish(folder, s.wish_text(), computer)
    if err:
        return err
    return remove(folder, s.title)


def no(folder: str | os.PathLike, s: Suggestion) -> str:
    """"No": the idea is taken out of suggestions.md."""
    return remove(folder, s.title)
