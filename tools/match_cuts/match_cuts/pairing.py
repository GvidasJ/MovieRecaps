"""pairing.py: which of the videos that belong together is the RAW (night 11) -- one rule for the batch's folders
(batch.split_folder), the two videos of the input folder (cli.resolve_inputs) and the app's drops (app jobs.py).

Names win over length:

1. a video named ``raw*`` (its name without the extension, any case) is the RAW and every other video a competitor;
   with no ``raw*`` video, the ``comp*`` / ``competitor*`` videos are competitors and the one other video is the RAW;
2. the names do not say: the longer video is the RAW when the two lengths are more than 20 % apart (the shorter one
   under 0.8 x the longer);
3. about as long (within 20 %): a portrait video next to a landscape one is the competitor (your competitors are
   9:16 Shorts, the RAWs landscape) -- said as a decision with its reason;
4. still not decided: ``ask`` -- the app asks "Which one is the RAW?"; the batch and the command line say why in plain
   words and skip that video ("name the RAW raw.mp4").

``pick_pair`` only reads what it is given (``seconds``: a video's length; ``size``: its (width, height) on screen);
without a ``size`` it reads the picture size itself (cli.quick_probe) when rule 3 needs it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

ABOUT_AS_LONG = 0.8          # the shorter one at least this share of the longer: "about as long" (the names decide)


@dataclass
class Pair:
    """The decision: the RAW and its competitors, why (plain words), or ``ask`` -- undecided between ``candidates``
    (``raw`` None then, ``why`` says why and what to do)."""
    raw: Path | None
    competitors: list[Path]
    why: str
    ask: bool = False
    candidates: list[Path] = field(default_factory=list)
    rule: str = ""               # "name" | "length" | "portrait" | "" (undecided)


def clock(s: float | None) -> str:
    """12.3 -> '0:12', 1281.5 -> '21:22', 4900 -> '1:21:40'; '?' unknown."""
    if s is None:
        return "?"
    s = int(round(float(s)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def named_raw(p: Path) -> bool:
    """A file named raw* (raw.mp4, RAW episode.mkv, raw_ae.mp4 ...)."""
    return Path(p).stem.lower().startswith("raw")


def named_competitor(p: Path) -> bool:
    """A file named comp* / competitor* (comp.mp4, Competitor 2.mov ...)."""
    return Path(p).stem.lower().startswith("comp")


def probe_size(p: Path) -> tuple[int, int] | None:
    """(width, height) as shown (rotation and pixel shape applied), None when it cannot be read."""
    try:
        from .cli import quick_probe
        d = quick_probe(p)
        return (int(d["width"]), int(d["height"])) if d.get("width") and d.get("height") else None
    except Exception:  # noqa: BLE001 - not a readable video
        return None


def _orientation(wh: tuple[int, int] | None) -> str:
    if not wh:
        return ""
    w, h = wh
    return "portrait" if h > w else "landscape" if w > h else ""


def _rest_are_competitors(raw: Path, vids: Sequence[Path], why: str, rule: str) -> Pair:
    return Pair(raw, [v for v in vids if v != raw], why, rule=rule)


def pick_pair(videos: Sequence[Path], seconds: Callable[[Path], float | None],
              size: Callable[[Path], tuple[int, int] | None] | None = None) -> Pair:
    """The RAW of ``videos`` (two or more that belong together) and its competitors, by the module's rule. One video
    or none: ``raw`` None and ``why`` says so (not an ``ask``)."""
    vids = [Path(v) for v in videos]
    if not vids:
        return Pair(None, [], "no video in it")
    if len(vids) == 1:
        return Pair(None, [], f"a raw video but no competitor ({vids[0].name})" if named_raw(vids[0]) else
                    f"only one video ({vids[0].name}): it needs the RAW and at least one competitor")
    # 1. the names
    raws = [v for v in vids if named_raw(v)]
    if len(raws) == 1:
        return _rest_are_competitors(raws[0], vids, f"{raws[0].name} is named raw", "name")
    if len(raws) > 1:
        exact = [v for v in raws if v.stem.lower() == "raw"]
        if len(exact) == 1:                     # raw.mp4 next to raw_stereo.mp4 ...: the exact name; the other raw*
            others = [v for v in vids if not named_raw(v)]          # files are no competitors
            if others:
                return Pair(exact[0], others, f"{exact[0].name} is named raw (also named raw*, not used: "
                            + ", ".join(v.name for v in raws if v != exact[0]) + ")", rule="name")
        return Pair(None, [], "can't tell which one is the RAW: " + ", ".join(v.name for v in raws)
                    + " are all named raw* -- keep the name raw for one of them", ask=True, candidates=raws)
    comps = [v for v in vids if named_competitor(v)]
    others = [v for v in vids if not named_competitor(v)]
    if comps and len(others) == 1:
        return Pair(others[0], comps, f"{', '.join(c.name for c in comps)} {'is' if len(comps) == 1 else 'are'} "
                    f"named comp*, so {others[0].name} is the RAW", rule="name")
    pool = others if comps and len(others) > 1 else vids     # comp* files stay competitors; the RAW is one of the rest
    # 2. the lengths
    secs = {v: seconds(v) for v in pool}
    unread = [v for v in pool if secs[v] is None]
    if unread:
        return Pair(None, [], f"cannot read the length of {', '.join(v.name for v in unread)} (name the RAW raw.mp4)",
                    ask=len(pool) >= 2, candidates=list(pool))
    order = sorted(pool, key=lambda v: (-float(secs[v]), v.name.lower()))
    longest, second = order[0], order[1]
    a, b = float(secs[longest]), float(secs[second])
    if b < ABOUT_AS_LONG * a:
        rest = [v for v in vids if v != longest]
        return Pair(longest, rest, f"the longer video is the RAW: {longest.name} ({clock(a)}) against "
                    f"{second.name} ({clock(b)})", rule="length")
    # 3. a portrait one next to a landscape one (the two about as long; three or more: not decided here)
    if len(pool) == 2:
        sz = size or probe_size
        o1, o2 = _orientation(sz(longest)), _orientation(sz(second))
        if {o1, o2} == {"portrait", "landscape"}:
            raw = longest if o1 == "landscape" else second
            comp = second if raw == longest else longest
            return Pair(raw, [v for v in vids if v != raw],
                        f"{comp.name} ({clock(secs[comp])}) and {raw.name} ({clock(secs[raw])}) are about as long and "
                        f"the names don't say: the portrait one ({comp.name}) is the competitor, the landscape one "
                        "the RAW", rule="portrait")
    # 4. undecided
    names = " and ".join(f"{v.name} ({clock(secs[v])})" for v in sorted(pool, key=lambda v: v.name.lower()))
    return Pair(None, [], f"can't tell which one is the RAW: {names} are about as long and the names don't say -- "
                f"name the RAW raw.mp4", ask=True, candidates=sorted(pool, key=lambda v: v.name.lower()))
