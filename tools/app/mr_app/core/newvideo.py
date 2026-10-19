"""The "New video" form's logic (day 12, 6a / 6b / 6d / 6e), kept out of the window so it is tested on its own:

* ``kind_of(text)``: what was pasted or dropped -- a YouTube link, a magnet link / .torrent (TORRENT), a video file,
  another site's link (Instagram, TikTok ...), or nothing; ``LABELS`` the small label the form shows;
* ``will_make(raw, competitor)``: what the form will make before anything is pressed -- the full Match cuts edit,
  "Just cuts" (every scene change; only a RAW), or nothing yet (a torrent without its competitor);
* ``parse_batch(text)``: many pairs, one per line "RAW link | competitor link";
* the remembered choices (Automatic on/off, the last channel) in the app's settings file (settings.json keeps keys it
  does not know, so no other part of the app is touched);
* ``plain_error``: any failure in plain words (a broken link, a private / removed video, no peers, a full disk);
* ``order``: newest on top -- running first, then waiting (newest first), then finished (newest first).
"""
from __future__ import annotations

import errno
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

YOUTUBE, TORRENT, FILE, LINK, NONE = "youtube", "torrent", "file", "link", ""
LABELS = {YOUTUBE: "YOUTUBE", TORRENT: "TORRENT", FILE: "FILE", LINK: "LINK", NONE: "EMPTY"}

FULL, JUST_CUTS, NEEDS_COMP = "full", "just_cuts", "needs_comp"
WILL_MAKE = {FULL: "Full Match cuts edit", JUST_CUTS: "Just cuts (every scene change)",
             NEEDS_COMP: "Needs a competitor (a torrent RAW is always matched to one)", None: "—"}

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts", ".m2ts", ".ts", ".wmv", ".flv", ".mpg",
              ".mpeg"}

# the remembered choices (keys of settings.json; the Settings class keeps keys it does not know)
PREF_AUTOMATIC = "newvideo_automatic"
PREF_CHANNEL = "newvideo_channel"


def _clean(text: str | None) -> str:
    return (text or "").strip().strip('"').strip("'").strip()


def kind_of(text: str | None) -> str:
    """What the text is: TORRENT (magnet:? or a .torrent file / link), YOUTUBE (a youtube / youtu.be link), LINK
    (another web address: Instagram, TikTok ... -- yt-dlp reads those too), FILE (a path to a video file), else
    NONE."""
    t = _clean(text)
    if not t:
        return NONE
    low = t.lower()
    if low.startswith("magnet:?") or low.startswith("magnet:") or low.split("?")[0].endswith(".torrent"):
        return TORRENT
    if re.match(r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\b", low) or \
            re.match(r"^https?://[^/]*youtu", low):
        return YOUTUBE
    if re.match(r"^https?://", low) or re.match(r"^www\.", low):
        return LINK
    if re.match(r"^[a-z]:[\\/]", low) or low.startswith("\\\\") or low.startswith("/") or "\\" in t:
        return FILE
    if Path(t).suffix.lower() in VIDEO_EXTS:
        return FILE
    if "." in low and " " not in low and "/" in low:        # 'instagram.com/reel/...' without https://
        return LINK
    return NONE


def is_link(kind: str) -> bool:
    return kind in (YOUTUBE, LINK)


def will_make(raw_kind: str, comp_kind: str) -> str | None:
    """FULL (a RAW and a competitor), JUST_CUTS (a RAW alone: a link or a file), NEEDS_COMP (a torrent RAW
    without its competitor -- not allowed: the form asks for it), None (no RAW yet)."""
    if not raw_kind:
        return None
    if comp_kind:
        return FULL
    if raw_kind == TORRENT:
        return NEEDS_COMP
    return JUST_CUTS


def will_make_text(raw_text: str | None, comp_text: str | None) -> str:
    return WILL_MAKE[will_make(kind_of(raw_text), kind_of(comp_text))]


@dataclass
class Pair:
    raw: str
    competitor: str = ""
    line: int = 0


def parse_batch(text: str | None) -> tuple[list[Pair], list[str]]:
    """Many pairs, one per line: "RAW link | competitor link" (a tab or ' , ' works too; the competitor may be left
    out: then that line makes Just cuts). Returns (pairs, problems in plain words, each naming its line)."""
    pairs: list[Pair] = []
    probs: list[str] = []
    for n, line in enumerate((text or "").splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"\s*\|\s*|\t+|\s+,\s+", s) if p.strip()]
        if len(parts) > 2:
            probs.append(f"line {n}: more than two things on it -- write 'RAW link | competitor link'")
            continue
        raw = parts[0]
        comp = parts[1] if len(parts) > 1 else ""
        rk, ck = kind_of(raw), kind_of(comp) if comp else NONE
        if not rk:
            probs.append(f"line {n}: '{raw[:50]}' is not a link, a magnet or a video file")
            continue
        if comp and not ck:
            probs.append(f"line {n}: '{comp[:50]}' is not a link or a video file")
            continue
        if rk == TORRENT and not comp:
            probs.append(f"line {n}: a torrent needs its competitor -- add ' | competitor link'")
            continue
        pairs.append(Pair(raw, comp, n))
    return pairs, probs


# ---------------------------------------------------------------------------------------------------------------------
# remembered choices
# ---------------------------------------------------------------------------------------------------------------------

def get_pref(key: str, default: Any = None) -> Any:
    try:
        from . import settings as S
        return S.load().extra.get(key, default)
    except Exception:  # noqa: BLE001 - a broken settings file: the default
        return default


def set_pref(key: str, value: Any) -> None:
    try:
        from . import settings as S
        s = S.load()
        s.extra[key] = value
        S.save(s)
    except Exception:  # noqa: BLE001 - not remembered this time; never a crash
        pass


def automatic() -> bool:
    """Automatic ON/OFF (OFF by default, remembered)."""
    return get_pref(PREF_AUTOMATIC, False) is True


def set_automatic(on: bool) -> None:
    set_pref(PREF_AUTOMATIC, bool(on))


def last_channel(names: Sequence[str]) -> str:
    """The channel chosen last time (when it still exists), else the first one."""
    c = get_pref(PREF_CHANNEL, "")
    for n in names:
        if isinstance(c, str) and n.lower() == c.lower():
            return n
    return names[0] if names else ""


def set_last_channel(name: str) -> None:
    set_pref(PREF_CHANNEL, name or "")


# ---------------------------------------------------------------------------------------------------------------------
# the headline box
# ---------------------------------------------------------------------------------------------------------------------

def headline_text(typed: str | None) -> str:
    """The two-line box's text as the template takes it: the break you typed is kept (line 1 / line 2; a third line
    joins line 2, as the renderer does), spaces tidied; no break: one line (the renderer splits it like night 10's)."""
    lines = [" ".join(x.split()) for x in (typed or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [x for x in lines if x]
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return "\n".join(lines)


def one_line(text: str | None) -> str:
    """A headline as one line (the YouTube title, a row's text): the lines joined by a space."""
    return " ".join(x.strip() for x in (text or "").splitlines() if x.strip())


# ---------------------------------------------------------------------------------------------------------------------
# errors in plain words
# ---------------------------------------------------------------------------------------------------------------------

DISK_FULL = "the disk is full: free some space and press Try again"
NO_PEERS = "nobody is sharing this torrent right now (no peers for a long time): try again later or use another one"
BROKEN_LINK = "the link is broken: there is no video at it (check that you copied the whole link)"
PRIVATE = "this video is private: only its owner can watch it"
REMOVED = "this video was removed or made unavailable"
NO_NET = "no internet, or the connection dropped: check it and press Try again"

_RULES: list[tuple[str, str]] = [
    (r"no space left|not enough space|disk is full|disk full|errno 28\b|winerror 112\b|not enough free space", DISK_FULL),
    (r"no peers|no seeders|0 seeders|stalled", NO_PEERS),
    (r"\bprivate\b", PRIVATE),
    (r"removed|unavailable|no longer available|terminated", REMOVED),
    (r"the link is wrong|unsupported url|404|broken link|invalid url|not a web address", BROKEN_LINK),
    (r"getaddrinfo|no internet|connection (?:dropped|reset|refused)|timed out|network", NO_NET),
]

_LIBRARY = re.compile(r"^\s*(\[[^\]]*\]\s*)?(warning|warn|info|debug)\b|opencv|cv2\.|global .*\.cpp|ffmpeg version",
                      re.I)


def plain_error(err: Any) -> str:
    """Any failure in plain words. Already plain words (core.download / convert / flow say them so) stay as they are;
    an OSError of a full disk, a library's warning line or a bare exception name become a sentence."""
    if isinstance(err, OSError) and getattr(err, "errno", None) == errno.ENOSPC:
        return DISK_FULL
    if isinstance(err, BaseException):
        text = str(err) or type(err).__name__
    else:
        text = str(err or "")
    text = text.strip()
    if not text:
        return "it stopped without saying why -- press Try again; if it happens again, open the folder's logs"
    lines = [x for x in text.splitlines() if x.strip()]
    real = [x for x in lines if not _LIBRARY.search(x)]       # a library's warning is never the reason
    text = (real[-1] if real else lines[-1]).strip()
    low = text.lower()
    if re.search(_RULES[0][0], low):
        return DISK_FULL
    if re.match(r"^[A-Za-z_]+(Error|Exception)$", text):           # a bare exception name
        return f"something went wrong ({text}) -- press Try again"
    return text[:300]


def classify(text: str) -> str | None:
    """Which of the plain cases a message is (the words above), or None."""
    low = (text or "").lower()
    for pat, words in _RULES:
        if re.search(pat, low):
            return words
    return None


# ---------------------------------------------------------------------------------------------------------------------
# newest on top
# ---------------------------------------------------------------------------------------------------------------------

RUNNING, WAITING, FINISHED = 0, 1, 2


def order(rows: Iterable[Any], group=lambda r: r.group(), added=lambda r: r.added) -> list[Any]:
    """Running rows first, then waiting rows (newest first), then finished rows (newest first). The run order is
    not this (it stays the order the videos were added)."""
    return sorted(rows, key=lambda r: (group(r), -float(added(r))))
