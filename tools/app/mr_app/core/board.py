"""THE BOARD (night 11, section 6): the model the Board tab draws -- one card per video project, in eight columns from
the link to the upload: Link -> Downloaded -> Converted -> Template -> Edit made -> Fixed -> Rendered -> Uploaded.

* A card goes by itself as far as its steps got (project.json's steps; a ``*fixed*.prproj`` in the project or its
  edit folders; the upload found on the channel). A card you move (``move``) stays where you put it -- until a step
  finishes after your move further right. Every column it reached keeps its date and time ("Downloaded 18 Oct
  02:44"); a pinned card (``pin``) stays at the top of its column.
* Both computers use one board: the shared folder's projects (the truth once published -- Fixed, Rendered and
  Uploaded happen there) and this computer's projects that are not published yet (``load_cards``). The state lives
  in each project.json's "board" dict: {column (a move by hand), moved_at, dates {column: time}, pinned, pinned_at,
  uploaded {at, id, url, title, how: "auto" | "you"}, not_same [video ids]} -- every change re-reads the file under
  its lock (projects.py), so the PC and the laptop never lose each other's moves.
* Two titles per video: the headline (``set_title`` / ``title_of``: project.json "title", the template's) and the
  title the Short is uploaded with (``set_upload_title``: "upload_title", often not the headline). title.txt holds
  the upload title when one is set, else the headline -- you may edit it by hand, and that edit wins; the Copy
  buttons copy ``copy_title`` (the upload title when set, else the headline).
* The uploaded check (another module) calls ``mark_uploaded`` for a Short it found, ``not_same`` when you said a
  near match is another video.
* Clean up (``cleanup_plan`` -> ask -> ``cleanup``): temp and partial files, old lock files and -- for cards that are
  rendered or uploaded -- downloaded originals the edit holds a real copy of. Edit folders (and everything in them),
  template.png, project.json and title.txt are never deleted: cleanup() itself refuses them.
* ``uploads(cards)``: (when, channel) of every uploaded Short, for the motivation strip and the weekly charts.

Reading the shared folder can wait for the network: call ``load_cards``, ``card_of`` and the cleanup from a worker
thread (``load_cards`` never waits longer than its timeout, and an unreachable folder is simply left out)."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import locks
from . import projects as P
from . import publish as PB

COLUMNS: list[tuple[str, str]] = [("link", "Link"), ("downloaded", "Downloaded"), ("converted", "Converted"),
                                  ("template", "Template"), ("edit", "Edit made"), ("fixed", "Fixed"),
                                  ("rendered", "Rendered"), ("uploaded", "Uploaded")]
KEYS = [k for k, _ in COLUMNS]
LABELS = dict(COLUMNS)
INDEX = {k: i for i, k in enumerate(KEYS)}
GOOD_EDITS = ("PASS", "OK with a note")        # an edit with one of these results is "made"
STEP_COLUMNS = (("download", "downloaded"), ("convert", "converted"), ("template", "template"))
TITLE_TXT = PB.TITLE_TXT
EDIT_RE = PB.EDIT_RE
LOAD_TIMEOUT_S = 30.0                           # load_cards: a folder that takes longer is left out this time
OLD_S = 3600.0                                  # a temp file untouched this long is not in use any more
SAMPLE = 1 << 20                                # the same video: the same size and the same bytes at 3 places
PROTECTED = ("project.json", "template.png", TITLE_TXT)
TEMP_KINDS = ((re.compile(r"(?i)\.part$"), "a partial file of a download or a copy that stopped"),
              (re.compile(r"(?i)\.ytdl$"), "the downloader's note of a download that stopped"),
              (re.compile(r"(?i)\.(tmp|temp)$"), "a temp file"),
              (re.compile(r"(?i)\.app\.stdout$"), "the console of an edit that stopped"),
              (re.compile(r"(?i)^\..*\.info\.json$"), "the downloader's info file of a download that stopped"),
              (re.compile(r"(?i)^\.movierecaps\.lock\.[0-9a-z]+\.claim\d+$"), "a note left by taking over a work lock"))

plain_size = PB.plain_size


# ---------------------------------------------------------------------------------------------------------------------
# times
# ---------------------------------------------------------------------------------------------------------------------

def _dt(text: Any) -> datetime | None:
    """An ISO time from project.json as a local, timezone-aware datetime (None when there is none)."""
    if isinstance(text, datetime):
        return text if text.tzinfo else text.astimezone()
    try:
        d = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.astimezone()


def _iso(now: Any = None) -> str:
    """project.json's way of writing a time: '2026-10-18T02:44:00+03:00'."""
    if now is None:
        return P.now()
    if isinstance(now, datetime):
        return (now if now.tzinfo else now.astimezone()).isoformat(timespec="seconds")
    d = _dt(now)
    return d.isoformat(timespec="seconds") if d is not None else P.now()


def when_text(d: datetime) -> str:
    """'18 Oct 02:44' -- how a card writes a step's date and time."""
    d = d.astimezone()
    return f"{d.day} {d:%b %H:%M}"


# ---------------------------------------------------------------------------------------------------------------------
# a card
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Card:
    """One project on the board."""
    folder: Path
    name: str
    channel: str = ""
    title: str = ""                          # the headline (the template's title)
    upload_title: str = ""                   # the title the Short is uploaded with ("" = the headline)
    column: str = "link"
    dates: dict[str, datetime] = field(default_factory=dict)   # every column reached -> when
    pinned: bool = False
    pinned_at: datetime | None = None
    where: str = "local"                     # "shared" (the shared folder's copy) or "local" (this computer's)
    published: bool = False
    edit_result: str = ""                    # "PASS", "OK with a note", "FAIL" ... of the last edit
    uploaded: dict | None = None             # board.uploaded {at, id, url, title, how}
    not_same: list = field(default_factory=list)              # video ids you said are not this one
    fixed: Path | None = None                # the newest *fixed*.prproj in the project or its edit folders
    lock: dict | None = None                 # who works on it now (locks.holder), None when nobody
    error: str = ""                          # a problem reading it, in plain words
    auto_column: str = "link"                # where its steps alone put it
    moved: bool = False                      # a move by hand decides its column
    data: dict = field(default_factory=dict, repr=False)      # its project.json as read

    @property
    def label(self) -> str:
        return LABELS.get(self.column, self.column)

    @property
    def date(self) -> datetime | None:
        """When it came into its column (else its newest date)."""
        return self.dates.get(self.column) or (max(self.dates.values()) if self.dates else None)

    def date_lines(self) -> list[str]:
        """'Downloaded 18 Oct 02:44' for every column it reached, left to right."""
        return [f"{LABELS[k]} {when_text(self.dates[k])}" for k in KEYS if k in self.dates]


def _board(data: dict) -> dict:
    b = data.get("board")
    return b if isinstance(b, dict) else {}


def _fixed_prproj(folder: Path) -> Path | None:
    """The newest ``*fixed*.prproj`` in the project folder or one of its edit folders (not deeper: Premiere's
    auto-save copies sit in their own folder)."""
    found: list[tuple[float, Path]] = []
    try:
        dirs = [folder] + [p for p in folder.iterdir() if p.is_dir() and EDIT_RE.match(p.name)]
    except OSError:
        return None
    for d in dirs:
        try:
            for f in d.iterdir():
                if f.suffix.lower() == ".prproj" and "fixed" in f.stem.lower() and f.is_file():
                    found.append((f.stat().st_mtime, f))
        except OSError:
            continue
    return max(found)[1] if found else None


def auto_dates(data: dict, fixed: Path | None = None) -> dict[str, datetime]:
    """The columns the project's steps reached by themselves -> when: link (made), downloaded, converted, template
    (each step done), edit (done with PASS / OK with a note), fixed (the fixed prproj's time), uploaded (found)."""
    out: dict[str, datetime] = {}
    made = _dt(data.get("created"))
    if made is not None:
        out["link"] = made
    steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
    for step, col in STEP_COLUMNS:
        st = steps.get(step) if isinstance(steps.get(step), dict) else {}
        if st.get("state") == P.DONE:
            d = _dt(st.get("finished")) or _dt(st.get("started"))
            if d is not None:
                out[col] = d
    ed = steps.get("edit") if isinstance(steps.get("edit"), dict) else {}
    if ed.get("state") == P.DONE and ed.get("result") in GOOD_EDITS:
        d = _dt(ed.get("finished")) or _dt(ed.get("started"))
        if d is not None:
            out["edit"] = d
    if fixed is not None:
        try:
            out["fixed"] = datetime.fromtimestamp(fixed.stat().st_mtime).astimezone()
        except OSError:
            pass
    up = _board(data).get("uploaded")
    if isinstance(up, dict):
        d = _dt(up.get("at")) or _dt((_board(data).get("dates") or {}).get("uploaded"))
        out["uploaded"] = d or made or datetime.now().astimezone()
    return out


def place(data: dict, fixed: Path | None = None) -> tuple[str, str, bool, dict[str, datetime]]:
    """(its column, its automatic column, a move by hand decides it, every column's date) -- the rules of
    ``card_of``."""
    auto = auto_dates(data, fixed)
    auto_col = max(auto, key=INDEX.__getitem__) if auto else "link"
    b = _board(data)
    col, moved = auto_col, False
    manual = b.get("column")
    if manual in INDEX:
        at = _dt(b.get("moved_at"))
        later = [c for c, d in auto.items() if INDEX[c] > INDEX[manual] and at is not None and d > at]
        col = max([manual, *later], key=INDEX.__getitem__)
        moved = col == manual
    dates = dict(auto)
    for k, v in (b.get("dates") or {}).items() if isinstance(b.get("dates"), dict) else ():
        d = _dt(v)
        if k in INDEX and d is not None:
            dates[k] = d
    return col, auto_col, moved, dates


def _data(folder: str | os.PathLike, data: dict | None) -> dict:
    if isinstance(data, dict):
        return data
    try:
        d = P.read_json(Path(folder) / P.FILE, tries=3)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def title_of(folder: str | os.PathLike, data: dict | None = None) -> str:
    """The headline (the template's title, project.json "title"). title.txt holds it while no upload title is set:
    then title.txt wins (you may edit it by hand)."""
    d = dict(_data(folder, data))
    if not PB._set(d.get("upload_title")):
        PB.fold_title_txt(folder, d)
    return str(d.get("title") or "")


def upload_title_of(folder: str | os.PathLike, data: dict | None = None) -> str:
    """The title the Short is uploaded with (project.json "upload_title"; title.txt holds it when it is set, and
    wins: you may edit it by hand); "" when none is set."""
    d = dict(_data(folder, data))
    if not PB._set(d.get("upload_title")):
        return ""
    PB.fold_title_txt(folder, d)
    return str(d.get("upload_title") or "")


def copy_title(card_or_folder: "Card | str | os.PathLike") -> str:
    """What the Copy buttons copy: the upload title when one is set, else the headline."""
    if isinstance(card_or_folder, Card):
        return card_or_folder.upload_title or card_or_folder.title
    return upload_title_of(card_or_folder) or title_of(card_or_folder)


def card_of(folder: str | os.PathLike, where: str | None = None) -> Card:
    """The card of a project folder (``where``: "shared" / "local"; by default "shared" when its project.json says
    it was published to this very folder). Never raises: a project.json that cannot be read gives a card in Link
    with ``error`` in plain words."""
    folder = Path(folder)
    card = Card(folder=folder, name=folder.name, where=where or "local")
    try:
        data = P.read_json(folder / P.FILE, tries=3)
    except FileNotFoundError:
        card.error = "this folder has no project.json"
        return card
    except (OSError, ValueError) as e:
        card.error = f"project.json cannot be read ({type(e).__name__}): fix or delete it"
        return card
    if not isinstance(data, dict):
        card.error = "project.json does not hold a project"
        return card
    card.data = data
    try:
        card.name = str(data.get("name") or folder.name)
        card.channel = str(data.get("channel") or "")
        card.title = title_of(folder, data)
        card.upload_title = upload_title_of(folder, data)
        card.fixed = _fixed_prproj(folder)
        card.column, card.auto_column, card.moved, card.dates = place(data, card.fixed)
        b = _board(data)
        card.pinned = bool(b.get("pinned"))
        card.pinned_at = _dt(b.get("pinned_at")) if card.pinned else None
        card.uploaded = dict(b["uploaded"]) if isinstance(b.get("uploaded"), dict) else None
        card.not_same = [str(x) for x in b["not_same"]] if isinstance(b.get("not_same"), list) else []
        pub = data.get("published") if isinstance(data.get("published"), dict) else {}
        here = bool(pub.get("to")) and PB._norm(pub["to"]) == PB._norm(folder)
        card.where = where or ("shared" if here else "local")
        card.published = bool(pub.get("to")) or card.where == "shared"
        ed = (data.get("steps") or {}).get("edit") if isinstance(data.get("steps"), dict) else None
        card.edit_result = str((ed or {}).get("result") or "") if isinstance(ed, dict) else ""
        h = locks.holder(folder)
        card.lock = h if h is not None and not h.get("stale") else None
    except Exception as e:  # noqa: BLE001 - a strange project.json never breaks the board
        card.error = f"project.json has something unexpected in it ({type(e).__name__}: {e})"
    return card


# ---------------------------------------------------------------------------------------------------------------------
# both folders
# ---------------------------------------------------------------------------------------------------------------------

def _scan(root: Path, where: str) -> list[Card]:
    if not root.is_dir():
        return []
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower(), reverse=True):
        if (d / P.FILE).is_file():
            out.append(card_of(d, where=where))
    return out


def _scan_safely(root: str | os.PathLike | None, where: str, timeout_s: float) -> list[Card]:
    """The cards of one root; [] when it is not there, does not answer or takes longer than ``timeout_s``."""
    if root is None or not str(root).strip():
        return []
    root = Path(root)
    if not PB.reachable(root, min(PB.REACH_TIMEOUT_S, timeout_s)):
        return []
    try:
        return PB.call_with_timeout(_scan, timeout_s, root, where) or []
    except Exception:  # noqa: BLE001 - TimeoutError, the network gone half-way ...
        return []


def load_cards(shared_root: str | os.PathLike | None, local_root: str | os.PathLike | None,
               timeout_s: float = LOAD_TIMEOUT_S) -> list[Card]:
    """Every project of the shared folder and of this computer's folder. A project in both is shown once: the shared
    copy (it is the truth once published) -- also when it went there under another name (-2); two different projects
    that only share a folder name are both shown. A root that is missing or does not answer is left out (no
    exception, never longer than ``timeout_s`` each)."""
    same = (shared_root is not None and local_root is not None
            and PB._norm(shared_root) == PB._norm(local_root))
    shared = _scan_safely(shared_root, "shared", timeout_s)
    local = [] if same else _scan_safely(local_root, "local", timeout_s)
    by_name = {c.folder.name.lower(): c for c in shared}
    there = {PB._norm(c.folder) for c in shared}
    out = list(shared)
    for c in local:
        to = (c.data.get("published") or {}).get("to") if isinstance(c.data.get("published"), dict) else None
        if to and PB._norm(to) in there:
            continue                                            # published: the shared copy is shown
        s = by_name.get(c.folder.name.lower())
        if s is not None and (s.error or c.error or PB.same_project(c.data, s.data)):
            continue
        out.append(c)
    return out


def ordered(cards: Iterable[Card]) -> dict[str, list[Card]]:
    """{column: its cards}: the pinned ones first (in the order they were pinned), then the newest first."""
    out: dict[str, list[Card]] = {k: [] for k in KEYS}
    for c in cards:
        out[c.column if c.column in out else "link"].append(c)

    def key(c: Card) -> tuple:
        d = c.date
        newest = -d.timestamp() if d is not None else 0.0
        pinned = c.pinned_at.timestamp() if c.pinned and c.pinned_at is not None else float("inf")
        return (0 if c.pinned else 1, pinned if c.pinned else 0.0, newest, c.name.lower())
    for k in out:
        out[k].sort(key=key)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# moving, pinning, titles
# ---------------------------------------------------------------------------------------------------------------------

def move(folder: str | os.PathLike, column: str, now: Any = None) -> Card:
    """Put the card in ``column`` by hand: board.column, moved_at, and the column's date (a date it had already is
    kept). Moved to Uploaded: board.uploaded = {at, how: "you"} unless the upload was found already. Moved out of
    Uploaded: it does not count as uploaded any more (an upload found automatically is remembered as "not the same
    video", so the check does not put it back)."""
    if column not in INDEX:
        raise ValueError(f"unknown column {column!r} (one of {', '.join(KEYS)})")
    folder = Path(folder)
    t = _iso(now)

    def apply(d: dict) -> None:
        b = d.get("board") if isinstance(d.get("board"), dict) else {}
        d["board"] = b
        dates = b.get("dates") if isinstance(b.get("dates"), dict) else {}
        b["dates"] = dates
        known = auto_dates(d, _fixed_prproj(folder)).get(column) or _dt(dates.get(column))
        if known is None or known > _dt(t):
            dates[column] = t
        b["column"] = column
        b["moved_at"] = t
        if column == "uploaded":
            if not isinstance(b.get("uploaded"), dict):
                b["uploaded"] = {"at": t, "how": "you"}
        elif isinstance(b.get("uploaded"), dict):
            up = b.pop("uploaded")
            dates.pop("uploaded", None)
            vid = up.get("id")
            if up.get("how") != "you" and vid:
                ns = b.get("not_same") if isinstance(b.get("not_same"), list) else []
                if vid not in ns:
                    ns.append(vid)
                b["not_same"] = ns
    P.load(folder).change(apply)
    return card_of(folder)


def pin(folder: str | os.PathLike, on: bool = True, now: Any = None) -> Card:
    """★ on (it stays at the top of its column; pinned again it keeps its place) or off."""
    t = _iso(now)

    def apply(d: dict) -> None:
        b = d.get("board") if isinstance(d.get("board"), dict) else {}
        d["board"] = b
        if on:
            if not b.get("pinned") or not b.get("pinned_at"):
                b["pinned_at"] = t
            b["pinned"] = True
        else:
            b["pinned"] = False
            b.pop("pinned_at", None)
    P.load(folder).change(apply)
    return card_of(folder)


def mark_uploaded(folder: str | os.PathLike, at: Any, video_id: str = "", url: str = "", title: str = "",
                  how: str = "auto") -> Card:
    """The Short is on the channel: board.uploaded = {at, id, url, title, how} ("auto": found by the check, "you":
    said by you), its date, and the card in Uploaded."""
    t_at = _iso(at)

    def apply(d: dict) -> None:
        b = d.get("board") if isinstance(d.get("board"), dict) else {}
        d["board"] = b
        dates = b.get("dates") if isinstance(b.get("dates"), dict) else {}
        b["dates"] = dates
        b["uploaded"] = {"at": t_at, "id": str(video_id or ""), "url": str(url or ""), "title": str(title or ""),
                         "how": str(how or "auto")}
        dates["uploaded"] = t_at
        b["column"] = "uploaded"
        b["moved_at"] = P.now()
    P.load(folder).change(apply)
    return card_of(folder)


def not_same(folder: str | os.PathLike, video_id: str) -> Card:
    """You said that upload is not this video: its id is remembered (board.not_same), so it is not asked again."""
    vid = str(video_id or "")

    def apply(d: dict) -> None:
        b = d.get("board") if isinstance(d.get("board"), dict) else {}
        d["board"] = b
        ns = b.get("not_same") if isinstance(b.get("not_same"), list) else []
        if vid and vid not in ns:
            ns.append(vid)
        b["not_same"] = ns
    P.load(folder).change(apply)
    return card_of(folder)


def _set_titles(folder: Path, key: str, text: str) -> Card:
    """project.json's ``key`` = ``text`` and title.txt rewritten (the upload title when set, else the headline) --
    a title.txt edited by hand first goes into project.json, so it is not lost. Under project.json's lock; title.txt
    is written before project.json, so a failure changes neither."""
    def apply(d: dict) -> None:
        PB.fold_title_txt(folder, d)
        d[key] = text
        PB.write_title_txt(folder, d)
    P.load(folder).change(apply)
    return card_of(folder)


def set_title(folder: str | os.PathLike, title: str) -> Card:
    """The headline as you typed it (line breaks kept): project.json's "title"; title.txt holds it unless an upload
    title is set."""
    return _set_titles(Path(folder), "title", str(title if title is not None else ""))


def set_upload_title(folder: str | os.PathLike, text: str) -> Card:
    """The title the Short is uploaded with (YouTube's, often not the headline): project.json's "upload_title" and
    title.txt; "" goes back to the headline."""
    return _set_titles(Path(folder), "upload_title", str(text if text is not None else ""))


def uploads(cards: Iterable[Card]) -> list[tuple[datetime, str]]:
    """(when, channel) of every card in Uploaded, oldest first -- for the motivation strip and the weekly charts."""
    out: list[tuple[datetime, str]] = []
    for c in cards:
        if c.column != "uploaded":
            continue
        t = _dt((c.uploaded or {}).get("at")) or c.dates.get("uploaded")
        if t is not None:
            out.append((t, c.channel))
    return sorted(out, key=lambda x: x[0])


# ---------------------------------------------------------------------------------------------------------------------
# clean up
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Junk:
    """A file Clean up would delete, and why (plain words)."""
    path: Path
    size: int
    why: str
    kind: str = "temp"                      # "temp", "lock" or "original"
    same_as: Path | None = None             # an original: the copy in an edit's extras\media with the same video

    @property
    def size_text(self) -> str:
        return plain_size(self.size)


def total(junk: Iterable[Junk]) -> str:
    """'12 files, 1.2 GB' -- for the question before Clean up."""
    items = list(junk)
    n = len(items)
    return f"{n} file{'s' if n != 1 else ''}, {plain_size(sum(j.size for j in items))}"


def _project_of(path: Path) -> Path | None:
    """The project folder a path is inside (the nearest folder above it with project.json)."""
    for anc in path.parents:
        try:
            if (anc / P.FILE).is_file():
                return anc
        except OSError:
            return None
    return None


def protected(path: str | os.PathLike) -> str:
    """Why Clean up must never delete this ("" when it may): an edit folder and everything in it, template.png,
    project.json, title.txt, a folder, anything outside a project folder."""
    p = Path(os.path.abspath(path))
    if p.name.lower() in PROTECTED:
        return f"{p.name} is never deleted"
    proj = _project_of(p)
    if proj is None:
        return "it is not inside a project folder"
    if any(EDIT_RE.match(x) for x in p.relative_to(proj).parts[:-1]) or (EDIT_RE.match(p.name) and p.is_dir()):
        return "it is in an edit folder (edit folders are never cleaned)"
    if p.is_dir():
        return "it is a folder"
    return ""


def _temp_why(path: Path, st: os.stat_result, now: float) -> tuple[str, str]:
    """(why it is junk, its kind) for temp / partial / lock files; ("", "") when it is not (or is in use)."""
    name = path.name.lower()
    age = now - st.st_mtime
    if name == "thumbs.db":
        return "Windows' picture cache (Thumbs.db)", "temp"
    if name == "project.json.lock":
        return ("an old project.json lock (left by a program that stopped)", "lock") if age > OLD_S else ("", "")
    if name == locks.LOCK_FILE:
        h = locks.holder(path.parent)
        if h is not None and h.get("stale"):
            return f"a work lock left by a program that stopped ({h.get('why_stale')})", "lock"
        return "", ""
    for rx, why in TEMP_KINDS:
        if rx.search(path.name):
            return (why, "temp") if age > OLD_S else ("", "")   # younger: maybe in use right now
    return "", ""


def _temp_junk(folder: Path, now: float) -> list[Junk]:
    out: list[Junk] = []
    for d, dirs, names in os.walk(folder):
        dirs[:] = [x for x in dirs if not EDIT_RE.match(x)]     # never into an edit folder
        for n in names:
            p = Path(d) / n
            try:
                st = os.stat(p)
            except OSError:
                continue
            why, kind = _temp_why(p, st, now)
            if why and not protected(p):
                out.append(Junk(p, int(st.st_size), why, kind))
    return out


def _same_video(a: Path, b: Path) -> bool:
    """The same file content: the same size, the same bytes at the start, the middle and the end."""
    try:
        sa, sb = os.stat(a).st_size, os.stat(b).st_size
        if sa != sb or sa == 0:
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            for off in sorted({0, max(0, sa // 2 - SAMPLE // 2), max(0, sa - SAMPLE)}):
                fa.seek(off)
                fb.seek(off)
                if fa.read(SAMPLE) != fb.read(SAMPLE):
                    return False
        return True
    except OSError:
        return False


def _linked(p: Path, other: Path | None = None) -> bool:
    """A hard link (deleting it frees nothing): more than one name for the file, or the very same file as
    ``other``."""
    try:
        if os.stat(p).st_nlink > 1:
            return True
        return other is not None and os.path.samefile(p, other)
    except OSError:
        return True


def _in_edit_media(f: Path) -> bool:
    """``f`` is a file in an edit folder's extras\\media of a project (<project>\\edit\\extras\\media\\f)."""
    try:
        media, extras, edit = f.parent, f.parent.parent, f.parent.parent.parent
        return (f.is_file() and media.name.lower() == "media" and extras.name.lower() == "extras"
                and bool(EDIT_RE.match(edit.name)) and (edit.parent / P.FILE).is_file())
    except OSError:
        return False


def _media_copy(original: Path, media_dirs: list[Path]) -> Path | None:
    for m in media_dirs:
        try:
            files = [f for f in m.iterdir() if f.is_file()]
        except OSError:
            continue
        for f in files:
            if _same_video(original, f) and not _linked(original, f):
                return f
    return None


def _original_junk(card: Card, local_root: Path | None) -> list[Junk]:
    """The downloaded originals of a rendered / uploaded project that the edit holds a real copy of (in its
    extras\\media, here or in the published copy) -- and only when the original is a separate file (not a hard
    link: deleting one of those frees nothing)."""
    folders: list[tuple[Path, dict]] = [(card.folder, card.data)]
    if local_root is not None:
        twin = Path(local_root) / card.folder.name
        if PB._norm(twin) != PB._norm(card.folder) and (twin / P.FILE).is_file():
            try:
                tdata = P.read_json(twin / P.FILE, tries=3)
            except (OSError, ValueError):
                tdata = None
            if PB.same_project(card.data, tdata):
                folders.append((twin, tdata))
    media = [e / "extras" / "media" for f, _ in folders for e in PB.finished_edits(f)]
    out: list[Junk] = []
    for f, data in folders:
        for role, word in (("competitor", "competitor"), ("raw", "RAW")):
            name = ((data or {}).get(role) or {}).get("file")
            if not name or Path(name).name != name:
                continue                                        # only a file right in the project folder
            orig = f / name
            try:
                if not orig.is_file() or _linked(orig):
                    continue
                size = int(os.stat(orig).st_size)
            except OSError:
                continue
            copy = _media_copy(orig, media)
            if copy is None:
                continue
            out.append(Junk(orig, size, f"the downloaded {word} -- the edit keeps its own copy of it "
                                        f"({copy.parent.parent.parent.name}\\extras\\media\\{copy.name})",
                            "original", copy))
    return out


def cleanup_plan(folders: Iterable[Card | str | os.PathLike], local_root: str | os.PathLike | None = None,
                 now: float | None = None) -> list[Junk]:
    """What Clean up would delete in these projects (Cards or folders), for the question first: temp and partial
    files untouched for an hour, Thumbs.db, old lock files (never an active work lock), and for cards in Rendered /
    Uploaded the downloaded originals the edit keeps a real copy of. ``local_root``: this computer's projects, where
    a published card's own downloads are. Projects someone works on now are left alone."""
    now = time.time() if now is None else now
    lroot = Path(local_root) if local_root is not None else None
    out: list[Junk] = []
    seen: set[str] = set()
    for item in folders:
        card = item if isinstance(item, Card) else card_of(item)
        if card.error or card.lock is not None:
            continue
        found = _temp_junk(card.folder, now)
        if lroot is not None:
            twin = lroot / card.folder.name
            if PB._norm(twin) != PB._norm(card.folder) and (twin / P.FILE).is_file():
                h = locks.holder(twin)
                if h is None or h.get("stale"):
                    found += _temp_junk(twin, now)
        if card.column in ("rendered", "uploaded"):
            found += _original_junk(card, lroot)
        for j in found:
            k = PB._norm(j.path)
            if k not in seen:
                seen.add(k)
                out.append(j)
    return out


def cleanup(junk: Iterable[Junk], now: float | None = None) -> tuple[int, list[str]]:
    """Delete what the plan listed -- each file checked again first: never anything protected (``protected``), never
    a file that is not junk now (in use again, an active lock, an original whose copy is gone or that became a
    link). Returns (bytes freed, problems in plain words)."""
    now = time.time() if now is None else now
    freed, errors = 0, []
    for j in junk:
        p = Path(j.path)
        why = protected(p)
        if why:
            errors.append(f"kept {p.name}: {why}")
            continue
        try:
            st = os.stat(p)
        except FileNotFoundError:
            continue                                            # gone already
        except OSError as e:
            errors.append(f"could not look at {p.name}: {e.strerror or e}")
            continue
        if j.kind == "original":
            copy = Path(j.same_as) if j.same_as else None
            if not (p.parent / P.FILE).is_file():
                errors.append(f"kept {p.name}: a downloaded original sits right in its project folder, this does not")
                continue
            if copy is None or not _in_edit_media(copy) or _linked(p, copy) or not _same_video(p, copy):
                errors.append(f"kept {p.name}: the edit's own copy of it is not there any more (or it is linked)")
                continue
        else:
            still, _kind = _temp_why(p, st, now)
            if not still:
                errors.append(f"kept {p.name}: it is not junk any more (in use again?)")
                continue
        try:
            p.unlink()
            freed += int(st.st_size)
        except FileNotFoundError:
            continue
        except OSError as e:
            errors.append(f"could not delete {p.name}: {e.strerror or e}")
    return freed, errors
