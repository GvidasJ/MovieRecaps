"""File names = the video's title (day 13, section 4).

A project made from day 13 on is named after its video's title: its folder ``<date>_<title>``, the edit's XML copy
``<title>_edit.xml`` next to 1_edit.xml (its sequence named after the title, so the render Premiere makes from it is
called after the title too) and the Premiere project Open in Premiere makes, ``<title>.prproj``. 1_edit.xml itself
stays exactly as the tool wrote it (every check, check-all and Learn find runs through it).

* The title (``title_of``): the YouTube title when one is set (project.json "upload_title"), else the headline (its two
  lines joined by a space), else the title from the competitor's post (titles.post_title: cleaned; "Video by x" says
  nothing), else the headline read on the competitor's video (day 12's folder names came from it).
* Windows-safe (``safe_name``): no \\ / : * ? " < > | (\\ / | become a space, the others go) and no control
  characters, emoji kept, spaces tidied, no space or dot at the end, never a device name (CON, NUL, COM1 ...), at
  most 100 characters, cut at a word and never inside an emoji.
* Short enough for Windows' 260-character paths (``fit``): the deepest file the tool writes in a run folder and
  ``<root>\\<date>_<title> (99)\\edit-99\\.<title>_edit.xml.<pid>.<tid>.tmp`` (the temp file the copy is written
  through) must stay under ``PATH_LIMIT`` -- so with the usual projects folder a title longer than about 65
  characters is cut (at a word) for the folder; the files' name (``file_stem``) also leaves room for what Premiere
  writes next to <title>.prproj (its Auto-Save and Video Previews folders: ~95 more characters), so a title longer
  than ~25-40 characters (the longer the folder, the shorter) is cut there; the sequence keeps up to 100 characters.
* A folder name that is taken gets (2), (3) ... (``unique_folder``).
* project.json "names": {"rule": "title", "from": "youtube" | "headline" | "post" | "video" | "", "at": time} marks a
  project whose names follow its title. Projects made before (no "names") keep their names -- and their old
  ``<folder>_edit.xml`` / ``<folder>_edit.prproj`` -- until "Rename to title" is pressed on their board card.

Pure functions: nothing here reads the disk except ``unique_folder``. The renaming itself is in renames.py."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

RULE = "title"
KEY = "names"                       # project.json's key
MAX_LEN = 100                       # the title part of a name: at most this many characters
PATH_LIMIT = 250                    # Windows' MAX_PATH is 260 UTF-16 units: 10 kept back
ROOT_LEN = 60                       # a projects folder's length at least (the PC's and the laptop's input\downloads:
#                                     54; the shared folder \\192.168.101.11\Shared\Projects: 32) -- both computers
#                                     make the same names
SUFFIX_ROOM = len(" (99)")          # a taken name's (2), (3) ...
RUN_TAIL = len("\\edit-99\\extras\\debug\\verify_failures\\k00544.png")   # the deepest file of a run folder (day 13)
TEMP_ROOM = len(".") + len(".123456.1234567890.tmp")                     # the temp file an XML is written through
#                                                                          (publish._write_bytes: .<name>.<pid>.<tid>.tmp)
COPY_TAIL = len("\\edit-99\\") + len("_edit.xml") + TEMP_ROOM             # <run>\.<stem>_edit.xml.<pid>.<tid>.tmp
COPY_SUFFIX = "_edit.xml"
PROJECT_SUFFIX = ".prproj"
SOURCES = (("upload_title", "youtube"), ("title", "headline"), ("post_title", "post"), ("video_headline", "video"))
SOURCE_WORDS = {"youtube": "the YouTube title", "headline": "the headline", "post": "the title from the post",
                "video": "the headline on the competitor's video"}

_FORBIDDEN_GONE = set(':*?"<>')                   # removed
_FORBIDDEN_SPACE = set("\\/|")                    # became a space (they part words: "AC/DC", "this | that")
_INVISIBLE = {0x200B, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF,
              0x00AD}                              # zero-width spaces, direction marks, the soft hyphen
_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3]|conin\$|conout\$)$")
_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


# ---------------------------------------------------------------------------------------------------------------------
# a Windows-safe name
# ---------------------------------------------------------------------------------------------------------------------

def utf16_len(text: str) -> int:
    """How long Windows counts a text in a path (UTF-16 units: an emoji is 2)."""
    return len((text or "").encode("utf-16-le", "surrogatepass")) // 2


def _regional(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF                # a flag is two of these


def _attaches(ch: str) -> bool:
    """A character that belongs to the one before it (a cut must not fall before it): an accent, a joiner, an emoji's
    variation / skin tone / tag."""
    cp = ord(ch)
    return (unicodedata.category(ch) in ("Mn", "Me", "Mc") or cp in (0x200D, 0xFE0E, 0xFE0F, 0x20E3)
            or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F or 0xFE00 <= cp <= 0xFE0F)


def _tidy_end(text: str) -> str:
    """No space, dot or lone joiner at the end (Windows drops a final dot or space: the folder would not be found),
    none at the start."""
    t = text.strip()
    while t and (t[-1] in " .\u200d" or t[-1].isspace() or unicodedata.category(t[-1]) == "Cf" and t[-1] != "\ufe0f"):
        t = t[:-1].rstrip()
    while t and (t[0].isspace() or t[0] == "\u200d"):
        t = t[1:]
    return t


def cut(text: str, max_len: int, max_units: int | None = None) -> str:
    """``text`` at most ``max_len`` characters (and ``max_units`` UTF-16 units): cut at a word when the cut keeps
    more than half of it, never inside an emoji or before an accent; the end tidied (``_tidy_end``)."""
    t = text or ""
    n = len(t)
    if n <= max_len and (max_units is None or utf16_len(t) <= max_units):
        return _tidy_end(t)
    i = min(n, max(0, int(max_len)))
    if max_units is not None:
        while i > 0 and utf16_len(t[:i]) > max_units:
            i -= 1
    while 0 < i < n and (_attaches(t[i]) or t[i - 1] == "\u200d"):
        i -= 1                                        # (an emoji sequence or a letter with its accent stays whole)
    if 0 < i < n and _regional(t[i - 1]) and _regional(t[i]):
        k = i
        while k > 0 and _regional(t[k - 1]):
            k -= 1
        if (i - k) % 2:                               # the cut would split a flag: before it
            i -= 1
    head = t[:i]
    sp = head.rfind(" ")
    if i < n and not t[i].isspace() and sp > len(head) * 0.5:
        head = head[:sp]
    return _tidy_end(head)


def _not_reserved(name: str) -> str:
    """CON, NUL, COM1 ... (also 'CON.x', 'Con. Air': the part before the first dot) are Windows' devices, never
    files: '_' after that part."""
    first, dot, rest = name.partition(".")
    if _RESERVED.match(first.rstrip()):
        return first.rstrip() + "_" + (dot + rest if dot else "")
    return name


def safe_name(text: Any, max_len: int = MAX_LEN, max_units: int | None = None) -> str:
    """A title as a Windows-safe name (module docstring): '' when nothing is left."""
    t = unicodedata.normalize("NFC", str(text or ""))
    out = []
    for ch in t:
        cp = ord(ch)
        if ch in _FORBIDDEN_GONE or cp in _INVISIBLE or 0xD800 <= cp <= 0xDFFF:
            continue                                  # (a half emoji -- a lone surrogate -- is no character)
        if ch in _FORBIDDEN_SPACE or ch in "\t\r\n\v\f":
            out.append(" ")
        elif cp < 32 or 0x7F <= cp < 0xA0:
            continue                                  # control characters
        else:
            out.append(ch)
    t = " ".join("".join(out).split())
    t = cut(t, max_len, max_units)
    t = _not_reserved(t) if t else t
    return t


def one_line(text: Any) -> str:
    """A headline as one line: its lines joined by a space."""
    return " ".join(x.strip() for x in str(text or "").splitlines() if x.strip())


# ---------------------------------------------------------------------------------------------------------------------
# which title
# ---------------------------------------------------------------------------------------------------------------------

def _useless(text: str) -> bool:
    try:
        from .titles import useless
        return useless(text)
    except Exception:  # noqa: BLE001 - without the titles module: only an empty text says nothing
        return not text.strip()


def title_of(data: Mapping | None) -> tuple[str, str]:
    """(the title the names follow, as one line; where it came from: "youtube" / "headline" / "post" / "video") --
    the YouTube title, else the headline, else the post's title, else the headline read on the competitor's video;
    ("", "") when there is none yet. A post / video title that says nothing ("Video by x") does not count."""
    d = data or {}
    for key, src in SOURCES:
        t = one_line(d.get(key) or "")
        if not t or not safe_name(t):
            continue
        if src in ("post", "video") and _useless(t):
            continue
        return t, src
    return "", ""


def display_name(data: Mapping | None, folder_name: str = "") -> str:
    """What a queue row, a board card and History show as the project's name: its title (``title_of``), else its
    name (the folder's)."""
    t, _src = title_of(data)
    return t or str((data or {}).get("name") or folder_name or "")


def uses_titles(data: Mapping | None) -> bool:
    """The project's names follow its title (made from day 13 on, or "Rename to title" was pressed)."""
    n = (data or {}).get(KEY)
    return isinstance(n, Mapping) and n.get("rule") == RULE


def marker(source: str = "", at: str | None = None) -> dict:
    """project.json's "names" for a project whose names follow its title."""
    return {"rule": RULE, "from": source or "", "at": at or datetime.now().astimezone().isoformat(timespec="seconds")}


def day_of(folder_name: str) -> str:
    """'2026-10-20' of '2026-10-20_some name' ('' without a date in front)."""
    m = _DAY.match(folder_name or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------------------------------------------------
# names that fit
# ---------------------------------------------------------------------------------------------------------------------

def root_len(*roots: Any) -> int:
    """The length to plan for: the longest of the projects folders given, at least ROOT_LEN."""
    out = ROOT_LEN
    for r in roots:
        if r:
            out = max(out, utf16_len(os.path.abspath(str(r))))
    return out


def fit(title: Any, roots_len: int = ROOT_LEN) -> str:
    """The title part of the names (the folder's and the files'): safe_name, at most MAX_LEN characters, and short
    enough that ``<root>\\<date>_<title> (99)\\edit-99\\<title>_edit.xml`` and the deepest file of a run folder stay
    under PATH_LIMIT (the title is in that path twice)."""
    fixed = roots_len + 1 + len("2026-10-20_") + SUFFIX_ROOM
    twice = (PATH_LIMIT - fixed - COPY_TAIL) // 2
    once = PATH_LIMIT - fixed - RUN_TAIL
    return safe_name(title, MAX_LEN, max(8, min(twice, once)))


PREMIERE_LIMIT = 259                # the longest path Windows takes (260 with its end mark)
PREMIERE_TAIL = len("\\Adobe Premiere Pro Auto-Save\\") + len("--") + 36 + len("-2026-10-20_18-04-39") + \
    len(".prproj")                  # Premiere's auto-save next to a project: <stem>--<id>-<date_time>.prproj (95; its
#                                     video previews, <stem>.PRV\Rendered - <id>.mxf, are 91)
STEM_MIN = 24                       # the files' name is never cut shorter than this


def file_stem(title_part: str, roots_len: int = ROOT_LEN) -> str:
    """The name of the files the app makes in a run folder (<stem>_edit.xml, <stem>.prproj): the folder's title part,
    cut further (at a word) when Premiere's own files next to <stem>.prproj -- its Auto-Save, Video Previews (it puts
    them next to the project) -- would pass Windows' path limit; never under STEM_MIN characters."""
    t = title_part or ""
    run_len = roots_len + 1 + len("2026-10-20_") + utf16_len(t) + SUFFIX_ROOM + len("\\edit-99")
    room = max(STEM_MIN, PREMIERE_LIMIT - run_len - PREMIERE_TAIL)
    return cut(t, MAX_LEN, room) if utf16_len(t) > room else t


def no_emoji(text: Any) -> str:
    """The text without emoji (day 14: Premiere shows them as empty boxes in a sequence's name, and the render is
    named after it): pictographs and other symbols (Unicode 'So'), everything outside the Basic Multilingual Plane,
    emoji joiners, variation selectors, keycaps; the spaces tidied. Letters of every language stay."""
    out = []
    for ch in str(text or ""):
        cp = ord(ch)
        if cp > 0xFFFF or 0xD800 <= cp <= 0xDFFF or cp in (0x200D, 0x20E3) or 0xFE00 <= cp <= 0xFE0F or \
                unicodedata.category(ch) == "So":
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def sequence_name(title: Any) -> str:
    """The sequence's name in 1_edit.xml (Premiere names the render after it): the title without emoji (no_emoji),
    Windows-safe (the render's file name), at most MAX_LEN characters -- not cut for paths."""
    return safe_name(no_emoji(title), MAX_LEN)


def folder_base(title: Any, day: str | None = None, roots_len: int = ROOT_LEN) -> str:
    """'<date>_<title>' ('' without a title)."""
    t = fit(title, roots_len)
    if not t:
        return ""
    return f"{day or datetime.now().strftime('%Y-%m-%d')}_{t}"


def has_base(folder_name: str, base: str) -> bool:
    """The folder is named ``base`` already (or ``base (2)``, ``base (3)`` ...: the name was taken) -- capitals aside,
    as Windows compares names (a title changed only in its capitals does not rename the folder)."""
    if not base:
        return False
    a, b = unicodedata.normalize("NFC", folder_name or "").casefold(), unicodedata.normalize("NFC", base).casefold()
    if a == b:
        return True
    return re.fullmatch(re.escape(b) + r" \(\d+\)", a) is not None


def unique_folder(root: str | os.PathLike, base: str, current: str | os.PathLike | None = None) -> Path:
    """``root\\base`` -- or ``base (2)``, ``base (3)`` ... when that is taken (by another folder than ``current``)."""
    root = Path(root)
    k = 1
    while True:
        cand = root / (base if k == 1 else f"{base} ({k})")
        if current is not None and os.path.normcase(os.path.abspath(cand)) == \
                os.path.normcase(os.path.abspath(current)):
            return cand
        if not os.path.lexists(cand):
            return cand
        k += 1


@dataclass(frozen=True)
class Names:
    """A project's names now: ``base`` the folder's ('<date>_<title>', '' = keep the name it has), ``stem`` its
    files' (<stem>_edit.xml, <stem>.prproj), ``sequence`` the sequence's name (None: the tool's own, "Recreated Edit
    (Premiere)" -- a project from before day 13), ``title`` and ``source`` what they came from."""
    base: str
    stem: str
    sequence: str | None
    title: str = ""
    source: str = ""


def names_of(data: Mapping | None, folder: str | os.PathLike, roots_len: int = ROOT_LEN) -> Names:
    """The names a project folder should have now (``data``: its project.json). A project from before day 13 keeps
    its folder's name, and its files the old ones (``<folder>_edit``)."""
    folder = Path(folder)
    old = Names("", f"{folder.name}_edit", None)
    if not uses_titles(data):
        return old
    t, src = title_of(data)
    if not t:
        return old                                    # no title yet: as it is (the first title names it)
    part = fit(t, roots_len)
    return Names(f"{day_of(folder.name) or day_of(str((data or {}).get('name') or '')) or _today()}_{part}",
                 file_stem(part, roots_len), sequence_name(t), t, src)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def copy_name(run_dir: str | os.PathLike, data: Mapping | None = None) -> tuple[str, str | None]:
    """(the file name of the run's XML copy next to its 1_edit.xml, the sequence name in it -- None: as the tool
    wrote it). A project's run (``<project>\\edit``, ``edit-2``, ``cuts`` ...): ``<title>_edit.xml`` with the title as
    the sequence when the project's names follow its title, else night 10's ``<project>_<run>.xml``; any other run
    folder: ``<run>_edit.xml``."""
    run = Path(run_dir)
    proj = run.parent
    if data is None and re.fullmatch(r"(?i)(edit|cuts)(-\d+)?", run.name):
        data = _read(proj / "project.json")
    if data is not None and re.fullmatch(r"(?i)(edit|cuts)(-\d+)?", run.name):
        if uses_titles(data):
            n = names_of(data, proj, root_len(proj.parent))
            if n.sequence:
                return f"{n.stem}{COPY_SUFFIX}", n.sequence
        if re.fullmatch(r"(?i)edit(-\d+)?", run.name):
            return f"{proj.name}_{run.name}.xml", None    # night 10: 2026-10-17_a-lot-to-catch-up-on_edit(-2).xml
    return f"{run.name}_edit.xml", None


def project_name(copy: str | os.PathLike) -> str:
    """The Premiere project made next to a title-named XML copy: '<title>.prproj' for '<title>_edit.xml' (night 10's
    copies, '<name>_edit.xml', keep '<name>_edit.prproj': premiere.project_for decides)."""
    name = Path(copy).name
    return name[:-len(COPY_SUFFIX)] + PROJECT_SUFFIX if name.lower().endswith(COPY_SUFFIX) else \
        Path(name).with_suffix(PROJECT_SUFFIX).name


def _read(path: Path) -> dict | None:
    try:
        from .projects import read_json
        d = read_json(path, tries=3)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001 - not a project
        return None
