"""THE HISTORY TAB: a timeline of everything that happened to the projects, newest first, with the date and time,
the channel and the title -- built from the projects' project.json (nothing else is stored), plus a search box.

The events: "Link added" (the project was made), "Downloaded", "Converted", "Template made", "Edit made · PASS" /
"Edit made · OK with a note" / "Edit failed" (the first note or failure as its detail), "Fixed", "Rendered" and
"Uploaded" (the board's dates, and the Short the check found), and a failed download / convert / template. A step's
time is when it finished; a board date of a column that a step already stands for (Downloaded ...) is not repeated.
Times are local. ``search`` looks in the title, channel, project name, what and detail. ``when_text`` is the date as
the whole app shows it: '18 Oct 02:44'; ``date_text`` the slate's '18.10.2026'.

Quick for data dicts (the GUI thread is fine); given project folders, events() reads their project.json -- on the
share: from a worker thread."""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
COLUMN_LABELS = {"link": "Link added", "downloaded": "Downloaded", "converted": "Converted",
                 "template": "Template made", "edit": "Edit made", "fixed": "Fixed", "rendered": "Rendered",
                 "uploaded": "Uploaded"}
HOW = {"keep": "kept as it was", "rewrap": "put into MP4", "encode": "converted to H.264"}
UPLOAD_HOW = {"auto": "found on YouTube", "you": "marked by you"}
DETAIL_MAX = 220

# event kinds (the UI's colour of the event's mark)
OK, NOTE, FAIL, INFO = "ok", "note", "fail", "info"


@dataclass(frozen=True)
class Event:
    at: datetime            # local, with its offset
    what: str               # 'Downloaded', 'Edit made · PASS' ...
    name: str               # the project's folder name
    title: str = ""         # your title ('' until it is typed)
    channel: str = ""
    detail: str = ""
    kind: str = INFO        # ok / note / fail / info
    shown: str = ""         # day 13: the name History shows -- the video's title (names.display_name), '' = title / name


# ---------------------------------------------------------------------------------------------------------------------
# dates as the app shows them
# ---------------------------------------------------------------------------------------------------------------------

def _local(dt: datetime) -> datetime:
    """A time with an offset in this computer's time; one without: as it is."""
    return dt.astimezone() if dt.tzinfo is not None else dt


def when_text(dt: datetime) -> str:
    """'18 Oct 02:44' -- the date and time as the app shows them everywhere (English month, 24-hour clock)."""
    dt = _local(dt)
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt:%H:%M}"


def date_text(dt: datetime) -> str:
    """'18.10.2026' -- the date on a queue row's slate line."""
    return f"{_local(dt):%d.%m.%Y}"


def parse_time(value: Any) -> datetime | None:
    """project.json's time ('2026-10-17T09:15:48+03:00') -> local, with its offset; a time without an offset is taken
    as local; None for nothing or something that is not a time."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    return dt.astimezone()


# ---------------------------------------------------------------------------------------------------------------------
# the events
# ---------------------------------------------------------------------------------------------------------------------

def _data(p: Any) -> dict | None:
    """project.json's data of a dict, a projects.Project (its .data), or a project folder / project.json path (read
    now: maybe slow on the share -- call events() from a worker thread then)."""
    if isinstance(p, Mapping):
        return dict(p)
    data = getattr(p, "data", None)
    if isinstance(data, Mapping):
        return dict(data)
    if isinstance(p, (str, os.PathLike)):
        from . import projects
        path = Path(p)
        path = path / projects.FILE if path.is_dir() else path
        try:
            data = projects.read_json(path)
        except (OSError, ValueError):
            return None
        if isinstance(data, dict):
            data.setdefault("name", path.parent.name)
            return data
    return None


def _short(text: Any) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= DETAIL_MAX else t[:DETAIL_MAX - 3].rstrip() + "..."


def _first(items: Any) -> str:
    if isinstance(items, (list, tuple)) and items:
        return _short(items[0])
    return ""


def _source(data: Mapping, role: str) -> str:
    """The competitor's / RAW's title, else its file name, else its link."""
    d = data.get(role) if isinstance(data.get(role), Mapping) else {}
    for key in ("title", "file", "source_file", "url"):
        if d.get(key):
            return Path(str(d[key])).name if key == "source_file" else str(d[key])
    return ""


def _download_detail(data: Mapping) -> str:
    parts = [f"{label}: {_source(data, role)}" for role, label in (("competitor", "competitor"), ("raw", "RAW"))
             if _source(data, role)]
    return _short(" · ".join(parts))


def _convert_detail(step: Mapping) -> str:
    parts = []
    for role, label in (("competitor", "competitor"), ("raw", "RAW")):
        how = (step.get(role) or {}).get("how") if isinstance(step.get(role), Mapping) else None
        if how:
            parts.append(f"{label} {HOW.get(how, how)}")
    return " · ".join(parts)


def project_events(data: Mapping) -> list[Event]:
    """The events of one project (project.json's data), in the order they happened."""
    name = str(data.get("name") or "")
    title = str(data.get("title") or "")
    channel = str(data.get("channel") or "")
    try:                                     # day 13: the video's title (YouTube title, headline, post) is the name
        from .names import display_name
        shown_name = display_name(data, name)
    except Exception:  # noqa: BLE001
        shown_name = ""
    steps = data.get("steps") if isinstance(data.get("steps"), Mapping) else {}
    board = data.get("board") if isinstance(data.get("board"), Mapping) else {}
    dates = board.get("dates") if isinstance(board.get("dates"), Mapping) else {}
    out: list[Event] = []
    shown: set[str] = set()                  # the board's columns a step already stands for

    def add(when: Any, what: str, detail: str = "", kind: str = INFO, column: str | None = None) -> None:
        t = parse_time(when)
        if t is not None:
            out.append(Event(t, what, name, title, channel, _short(detail), kind, shown_name))
            if column:
                shown.add(column)

    def step(key: str) -> dict:
        s = steps.get(key)
        return dict(s) if isinstance(s, Mapping) else {}

    add(data.get("created"), "Link added", f"competitor: {_source(data, 'competitor')}"
        if _source(data, "competitor") else "", column="link")
    for key, done, failed, column in (("download", "Downloaded", "Download failed", "downloaded"),
                                      ("convert", "Converted", "Convert failed", "converted"),
                                      ("template", "Template made", "Template failed", "template")):
        s = step(key)
        if s.get("state") == "done":
            detail = (_download_detail(data) if key == "download" else _convert_detail(s) if key == "convert"
                      else "default template (no headline)" if s.get("default")
                      else (s.get("why") or "") if s.get("shrunk") else "")
            add(s.get("finished"), done, detail, NOTE if s.get("default") else INFO, column=column)
        elif s.get("state") == "failed":
            add(s.get("finished"), failed, s.get("error") or "", FAIL)
    jb = data.get("just_cuts_before")                   # Just cuts made before "Add a competitor" (newvideo)
    if isinstance(jb, Mapping) and jb.get("state") == "done":
        add(jb.get("finished"), f"Just cuts made · {jb.get('result') or 'PASS'}",
            f"{jb.get('cuts')} cuts" if jb.get("cuts") is not None else "", OK)
    e = step("edit")
    result = str(e.get("result") or "")
    made = "Just cuts made" if e.get("kind") == "just_cuts" else "Edit made"     # day 12: Just cuts
    cuts = f"{e.get('cuts')} cuts" if e.get("kind") == "just_cuts" and e.get("cuts") is not None else ""
    if e.get("state") == "done":
        if result == "OK with a note":
            add(e.get("finished"), f"{made} · OK with a note", _first(e.get("notes")), NOTE, "edit")
        else:
            add(e.get("finished"), f"{made} · {result}" if result else made, cuts, OK, "edit")
    elif e.get("state") == "failed":
        base = "Just cuts failed" if e.get("kind") == "just_cuts" else "Edit failed"
        what = base if result in ("", "FAIL") else f"{base} · {result}"
        add(e.get("finished"), what, _first(e.get("failures")) or e.get("error") or "", FAIL)
    up = board.get("uploaded") if isinstance(board.get("uploaded"), Mapping) else {}
    if up and parse_time(up.get("at")) is not None:
        detail = " · ".join(x for x in (str(up.get("title") or ""), UPLOAD_HOW.get(str(up.get("how") or ""), ""))
                            if x)
        add(up.get("at"), "Uploaded", detail, OK, "uploaded")
    for column, when in dates.items():
        if column in shown:
            continue
        label = COLUMN_LABELS.get(str(column), str(column).replace("_", " ").capitalize())
        add(when, label, "", OK if column == "uploaded" else INFO)
    out.sort(key=lambda ev: ev.at)
    return out


def events(projects: Iterable[Any], log: bool = True) -> list[Event]:
    """Every project's events, newest first (one moment: the later step first). ``projects``: project.json data dicts,
    projects.Project objects, or project folders (read now). ``log``: also what never got a project folder (day 12:
    a New video that failed before its folder was made -- a broken link, a private video), from LOG_FILE."""
    keyed = []
    for i, p in enumerate(projects):
        data = _data(p)
        if not data:
            continue
        for k, ev in enumerate(project_events(data)):
            keyed.append(((ev.at, k, -i), ev))
    if log:
        for k, ev in enumerate(logged_events()):
            keyed.append(((ev.at, k, 1), ev))
    keyed.sort(key=lambda x: x[0], reverse=True)
    return [ev for _, ev in keyed]


# ---------------------------------------------------------------------------------------------------------------------
# day 12: what never got a project folder
# ---------------------------------------------------------------------------------------------------------------------

LOG_FILE = "history_log.jsonl"          # in the app's folder on this computer (settings.app_dir())
LOG_KEEP = 500


def _log_path() -> Path:
    from .settings import app_dir
    return app_dir() / LOG_FILE


def log_event(what: str, name: str, detail: str = "", kind: str = INFO, title: str = "", channel: str = "",
              at: datetime | None = None) -> None:
    """One event that has no project.json to live in (it shows in History with its date); never raises."""
    import json
    rec = {"at": (at or datetime.now().astimezone()).isoformat(timespec="seconds"), "what": what, "name": name,
           "detail": _short(detail), "kind": kind, "title": title, "channel": channel}
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def logged_events() -> list[Event]:
    """The logged events (the last LOG_KEEP), oldest first; a broken line is skipped."""
    import json
    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()[-LOG_KEEP:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            d = json.loads(line)
            t = parse_time(d.get("at"))
        except (ValueError, AttributeError):
            continue
        if t is None:
            continue
        out.append(Event(t, str(d.get("what") or ""), str(d.get("name") or ""), str(d.get("title") or ""),
                         str(d.get("channel") or ""), str(d.get("detail") or ""), str(d.get("kind") or INFO)))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------------------------------------------------

def fold(text: Any) -> str:
    """Text as the search compares it: lower case, accents plain, emoji and punctuation a space, spaces collapsed."""
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(text or "")).casefold())
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Cf", "Cs"):
            continue
        out.append(ch if cat[0] in "LN" else " ")
    return " ".join("".join(out).split())


def search(evs: Iterable[Event], text: str) -> list[Event]:
    """The events whose title, channel, project name, what or detail has every word of ``text`` (anywhere in them) --
    any case, accents and emoji ignored ('crew mike' finds "The fact that nobody in the crew ..." of MikeMemesYou;
    '2026-10-17' the projects of that day, by their folder name). Empty text: all of them."""
    words = fold(text).split()
    evs = list(evs)
    if not words:
        return evs
    out = []
    for ev in evs:
        hay = fold(" ".join((ev.title, ev.channel, ev.name, ev.what, ev.detail, ev.shown)))
        if all(w in hay for w in words):
            out.append(ev)
    return out
