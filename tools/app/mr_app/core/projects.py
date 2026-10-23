"""One folder per video project: ``<root>\\<YYYY-MM-DD>_<short name>`` with the competitor, the RAW and
``project.json`` -- the project's state in plain JSON (the links, the files, the channel, the headline and the state
of each step), so any program can read it: tonight's app, and part 2's project board on the other computer (the
folders will move to D:\\Shared\\Projects; the root is ``paths.projects_root()``).

project.json is never half-written: it is written to a temp file and moved over the old one (os.replace), and every
change re-reads it first under a small lock (a ``project.json.lock`` file next to it, broken after 15 s when a
program died holding it), so two programs -- or two computers -- changing one project do not lose each other's
steps. Files are stored by name, relative to the project folder (the folder can move); only the dropped RAW's
original place (raw.source_file) is a full path. Each step's log goes to ``logs\\<step>_<role>.log``."""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlparse

from . import paths

VERSION = 1
FILE = "project.json"
LOGS = "logs"
ROLES = ("competitor", "raw")
STEPS = ("download", "convert", "template", "title_check", "edit")

# step states
WAITING = "waiting"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
STATES = (WAITING, RUNNING, DONE, FAILED, SKIPPED)

NAME_MAX = 40                  # the short name's length at most (characters)
LOCK_STALE_S = 15.0            # a lock file older than this was left by a program that died
LOCK_WAIT_S = 10.0             # wait at most this long for the lock, then write anyway (never hang)

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def now() -> str:
    """The local time with its offset, to the second: '2026-10-17T03:12:45+03:00'."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def computer() -> str:
    return socket.gethostname()


# ---------------------------------------------------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------------------------------------------------

_YT_ID = re.compile(r"[0-9A-Za-z_-]{11}")


def link_id(url: str | None) -> str | None:
    """The video id of a link: YouTube's 11 characters (watch?v=, youtu.be/, /shorts/, /live/, /embed/), else the
    link's last path part (TikTok, Instagram ...); None without one."""
    if not url:
        return None
    try:
        u = urlparse(url.strip() if "://" in url else "https://" + url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("youtu.be") and parts:
        cand = parts[0]
    elif "youtube" in host:
        v = parse_qs(u.query).get("v", [""])[0]
        cand = v or (parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v") else "")
    else:
        cand = parts[-1] if parts else ""
        cand = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", cand)          # video.mp4 -> video
    cand = re.sub(r"[^0-9A-Za-z_-]+", "", cand)[:NAME_MAX]
    return cand or None


def short_name(text: str | None, max_len: int = NAME_MAX) -> str:
    """'The Funniest Tough Guy of All Time 😂' -> 'the-funniest-tough-guy-of-all-time': lowercase words of plain
    letters and digits joined by '-' (accents made plain, emojis and signs dropped), cut at a word, at most
    ``max_len`` characters; '' when nothing is left."""
    t = unicodedata.normalize("NFKD", text or "")
    t = re.sub(r"['\u2019\u2018`]", "", t)                       # don't -> dont
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    out = ""
    for w in re.findall(r"[a-z0-9]+", t):
        cand = f"{out}-{w}" if out else w
        if len(cand) > max_len:
            if not out:
                out = w[:max_len]
            break
        out = cand
    return out


def folder_name(name: str | None, *, competitor_url: str | None = None, raw_url: str | None = None,
                raw_file: str | os.PathLike | None = None, day: str | None = None) -> str:
    """'<YYYY-MM-DD>_<short name>': the short name of ``name`` (the competitor's title), else the competitor's link
    id, else the RAW's link id or file name, else 'video'."""
    short = short_name(name)
    for cand in (link_id(competitor_url), link_id(raw_url), Path(raw_file).stem if raw_file else None):
        if not short:
            short = short_name(cand)
    return f"{day or datetime.now().strftime('%Y-%m-%d')}_{short or 'video'}"


# ---------------------------------------------------------------------------------------------------------------------
# project.json on disk
# ---------------------------------------------------------------------------------------------------------------------

def _empty_step() -> dict:
    return {"state": WAITING, "started": None, "finished": None, "error": None}


def _new_data(name: str, competitor_url: str | None, raw_url: str | None, raw_file: str | None) -> dict:
    t = now()
    return {
        "version": VERSION,
        "name": name,
        "created": t,
        "updated": t,
        "computer": computer(),
        "competitor": {"url": competitor_url, "title": None, "id": link_id(competitor_url), "file": None},
        "raw": {"url": raw_url, "source_file": raw_file, "title": None,
                "id": link_id(raw_url) if raw_url else None, "file": None},
        "channel": None,
        "title": None,
        "steps": {s: _empty_step() for s in STEPS},
    }


def _jsonable(o: Any) -> Any:
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, Fraction):
        return f"{o.numerator}/{o.denominator}"
    if is_dataclass(o) and not isinstance(o, type):
        return {k: v for k, v in asdict(o).items() if k != "info"}       # nested Paths: back here
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"{type(o).__name__} is not JSON")


def read_json(path: Path, tries: int = 20) -> dict:
    """project.json as a dict. A read that meets the moment another program replaces the file (Windows says
    'access denied' then) is tried again."""
    last: Exception | None = None
    for _ in range(tries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (PermissionError, json.JSONDecodeError) as e:
            last = e
            time.sleep(0.05)
    raise ValueError(f"{path} cannot be read: {last}")


def write_json(path: Path, data: dict, tries: int = 40) -> None:
    """Write atomically: a temp file next to it, flushed to the disk, then moved over the old file (retried while a
    reader on this or the other computer has the old one open)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False, default=_jsonable) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            tmp.unlink()                                       # e.g. the disk is full: no temp file left behind
        except OSError:
            pass
        raise
    for k in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == tries - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """This program's threads one at a time (a lock per project), other programs via ``project.json.lock``."""
    key = os.path.normcase(str(path.resolve()))
    with _thread_locks_guard:
        tl = _thread_locks.setdefault(key, threading.Lock())
    with tl:
        lock = path.with_name(path.name + ".lock")
        fd = None
        t0 = time.time()
        while fd is None:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{computer()} {os.getpid()} {now()}".encode("utf-8"))
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                        lock.unlink()                              # left by a program that died
                        continue
                except OSError:
                    pass
                if time.time() - t0 > LOCK_WAIT_S:
                    break                                          # never hang: write without the lock
                time.sleep(0.02)
            except OSError:
                break                                              # a folder without lock files: go on
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
                try:
                    lock.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------------------------------------------------
# the project
# ---------------------------------------------------------------------------------------------------------------------

class Project:
    """A project folder and its project.json. ``data`` is the state as last read or written; every change re-reads
    the file first (another program may have changed it) and writes it back at once."""

    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder)
        self.data: dict = read_json(self.json_path)

    def __repr__(self) -> str:
        return f"Project({str(self.folder)!r})"

    @property
    def json_path(self) -> Path:
        return self.folder / FILE

    @property
    def name(self) -> str:
        return self.data.get("name") or self.folder.name

    def reload(self) -> dict:
        self.data = read_json(self.json_path)
        return self.data

    def change(self, fn: Callable[[dict], None]) -> dict:
        """Re-read project.json, let ``fn`` change the dict, stamp 'updated', write it back (atomically, locked)."""
        with _locked(self.json_path):
            data = read_json(self.json_path)
            fn(data)
            data["updated"] = now()
            write_json(self.json_path, data)
        self.data = data
        return data

    def update(self, **fields: Any) -> dict:
        """Set top-level fields; a dict merges into the dict that is there (``update(raw={"file": "x.mp4"})`` keeps
        the RAW's link and title)."""
        def apply(d: dict) -> None:
            for k, v in fields.items():
                if isinstance(v, dict) and isinstance(d.get(k), dict):
                    d[k].update(v)
                else:
                    d[k] = v
        return self.change(apply)

    def set_step(self, step: str, state: str, **details: Any) -> dict:
        """A step's new state (+ details, e.g. ``error="private video"`` or the formats chosen). running: started
        now, on this computer; done / failed / skipped: finished now; the error is cleared unless given."""
        if step not in STEPS:
            raise ValueError(f"unknown step {step!r} (one of {', '.join(STEPS)})")
        if state not in STATES:
            raise ValueError(f"unknown state {state!r} (one of {', '.join(STATES)})")

        def apply(d: dict) -> None:
            s = d.setdefault("steps", {}).setdefault(step, _empty_step())
            t = now()
            if state == RUNNING:
                s.update(started=t, finished=None, computer=computer())
            elif state == WAITING:
                s.update(started=None, finished=None)
            else:
                if state != SKIPPED and not s.get("started"):
                    s["started"] = t
                s["finished"] = t
            s["state"] = state
            s["error"] = None
            s.update(json.loads(json.dumps(details, default=_jsonable)))
        return self.change(apply)

    def step(self, step: str) -> dict:
        """A copy of one step's state ({state, started, finished, error, ...})."""
        return dict(self.data.get("steps", {}).get(step) or _empty_step())

    def file(self, role: str) -> Path | None:
        """The competitor's / RAW's file in the folder (None until it is there)."""
        d = self.data.get(role) or {}
        name = d.get("file")
        if not name:
            return None
        here = self.folder / name
        dc = d.get("decode_copy")                    # day 16: the RAW re-encoded once because it can't be read
        if role == "raw" and dc and Path(str(dc)).is_file():
            return Path(str(dc))
        kept = d.get("kept_at")                      # day 16: the whole movie left in the folder it was downloaded
        if kept and not here.exists() and Path(str(kept)).is_file():     # to (flow.drop_whole_raw): read it there
            return Path(str(kept))
        return here

    def log_path(self, step: str, role: str | None = None) -> Path:
        """``logs\\<step>_<role>.log`` (``logs\\<step>.log`` without a role); the logs folder is made."""
        d = self.folder / LOGS
        d.mkdir(parents=True, exist_ok=True)
        return d / (f"{step}_{role}.log" if role else f"{step}.log")


def create(root: str | os.PathLike | None = None, *, competitor_url: str | None = None, raw_url: str | None = None,
           raw_file: str | os.PathLike | None = None, name: str | None = None, folder: str | None = None,
           names: dict | None = None) -> Project:
    """A new project folder ``<root>\\<YYYY-MM-DD>_<short name>`` (-2, -3 ... when taken) with its project.json.
    ``name``: the competitor's title when known (download.probe_link); else the competitor's link id is used.
    Day 13: ``folder`` -- the folder's exact name ('<date>_<title>', names.folder_base), (2), (3) ... when taken;
    ``names`` -- project.json's "names" (names.marker: the project's names follow its title)."""
    root = Path(root) if root is not None else paths.projects_root()
    root.mkdir(parents=True, exist_ok=True)
    base = folder or folder_name(name, competitor_url=competitor_url, raw_url=raw_url, raw_file=raw_file)
    k = 1
    while True:
        cand = root / (base if k == 1 else (f"{base} ({k})" if folder else f"{base}-{k}"))
        try:
            cand.mkdir()                                           # atomic: two programs never get one folder
            break
        except FileExistsError:
            k += 1
    folder = cand
    data = _new_data(folder.name, competitor_url, raw_url, str(raw_file) if raw_file else None)
    if names:
        data["names"] = dict(names)
    with _locked(folder / FILE):
        write_json(folder / FILE, data)
    return Project(folder)


def load(folder: str | os.PathLike) -> Project:
    """The project in ``folder`` (FileNotFoundError without a project.json)."""
    return Project(folder)


def list_projects(root: str | os.PathLike | None = None) -> list[Project]:
    """Every project under ``root`` (default ``paths.projects_root()``), newest first (the folder names start with
    the date). Folders without a readable project.json are left out."""
    root = Path(root) if root is not None else paths.projects_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower(), reverse=True):
        if (d / FILE).is_file():
            try:
                out.append(Project(d))
            except (OSError, ValueError):
                continue
    return out
