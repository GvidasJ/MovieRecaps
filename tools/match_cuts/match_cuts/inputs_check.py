"""inputs_check.py: stop at once when the two inputs look wrong (night 6), before anything is converted.

On 12 October a 109-minute copy of the movie went in as the competitor and the tool spent 20+ minutes converting it.
``problems`` reads only what ffprobe says (cli.quick_probe: length and picture size) and the file sizes, and names in
plain words what looks wrong: a competitor much longer than a Short, one almost as long as the RAW (a copy of the
movie), one longer than the RAW, the two files the wrong way round, or the same video twice. ``message`` is what the
user reads; ``--force-inputs`` skips the check.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

SHORT_MAX_S = 181.0          # a Short is at most 3 minutes (a second's slack for the container)
SAME_LENGTH = 0.9            # a competitor this share of the RAW's length or more: a copy of the RAW, not a Short


def mmss(s: float) -> str:
    s = max(0.0, float(s))
    h, m, sec = int(s // 3600), int(s % 3600 // 60), int(round(s % 60))
    if sec == 60:
        m, sec = m + 1, 0
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def size_text(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def describe(p: dict) -> str:
    """'name (2:13, 1080x1920, 24 MB)'."""
    try:
        size = size_text(os.path.getsize(p["path"]))
    except OSError:
        size = "size unknown"
    return f"{Path(p['path']).name} ({mmss(p['duration'])}, {p['width']}x{p['height']}, {size})"


def problems(comp: dict, raw: dict, allow_short_raw: bool = False) -> list[str]:
    """What looks wrong about a (competitor, RAW) pair of cli.quick_probe results, in plain words ([] = fine).
    ``allow_short_raw`` (``--allow-short-raw``, day 12: you said which file is the RAW) skips only the "almost as long
    as the RAW" check -- a 30 s RAW for a 28 s Short runs; every other check stays."""
    out: list[str] = []
    dc, dr = float(comp.get("duration") or 0.0), float(raw.get("duration") or 0.0)
    try:
        same = os.path.samefile(comp["path"], raw["path"])
    except OSError:
        same = False
    try:
        same = same or (os.path.getsize(comp["path"]) == os.path.getsize(raw["path"]) and abs(dc - dr) < 0.05)
    except OSError:
        pass
    portrait_c, portrait_r = comp["height"] > comp["width"], raw["height"] > raw["width"]
    if same:
        out.append("the competitor and the RAW are the same video")
        return out
    if dc > dr + 0.5 and (portrait_r and not portrait_c):
        out.append("the two files look swapped: the competitor is the long landscape video and the RAW the short "
                   "portrait one")
        return out
    if dc > SHORT_MAX_S:
        out.append(f"the competitor is {mmss(dc)} long -- much longer than a Short (at most 3 minutes): is it the "
                   "movie / episode itself?")
    if dc > dr + 0.5:
        out.append(f"the competitor ({mmss(dc)}) is longer than the RAW ({mmss(dr)}): the RAW must be the full "
                   "episode or movie the competitor was cut from")
    elif dr > 0 and dc >= SAME_LENGTH * dr and not allow_short_raw:
        out.append(f"the competitor ({mmss(dc)}) is almost as long as the RAW ({mmss(dr)}): it looks like a copy of "
                   "the RAW, not a Short cut from it")
    return out


def message(comp: dict, raw: dict, found: Sequence[str], option: str = "--force-inputs") -> str:
    """The stop message: what looks wrong, the two files, what to do."""
    lines = ["The inputs look wrong, so I stopped before converting anything:"]
    lines += [f"  - {p}" for p in found]
    lines += [f"  competitor: {describe(comp)}", f"  RAW:        {describe(raw)}",
              "What to do: give the competitor's Short (under 3 minutes, usually portrait) as the competitor and the "
              "full episode or movie it was cut from as the RAW (--competitor / --raw, or the two files in the input "
              f"folder). If these really are the right files, run again with {option}."]
    return "\n".join(lines)
