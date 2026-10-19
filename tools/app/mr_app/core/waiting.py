"""Learn waits by itself (day 12, section 8): as soon as an edit of the Match cuts queue finishes (PASS or OK with a
note), a card waits in the Learn tab under "Waiting for your fix" with the run, the RAW, the competitor, the channel and
the next free name (video16 ...) filled in. You only drop your fixed .prproj on it -- or save a .prproj into the
project's folder (``find_fixes``, the Learn tab's one-minute watcher) -- and the check card shows with the run chosen.
When the project's board card reaches Rendered, the card turns amber: "Rendered — add your project file".

The list lives in the hub (the shared projects folder) as ``learn_waiting.json``, so the PC and the laptop see the same
cards: {"version": 1, "items": [{id, name, project, run, run_in_project, raw, competitor, channel, title, result,
finished, computer, state ("waiting" / "skipped" / "learned"), changed}]}. Paths in or next to the hub are written
relative to it (learn_hub.portable), like learn_queue.md. Every change re-reads the file under its lock and replaces it
atomically, only when something changes (learn_hub._change). Nothing raises; call it from a worker thread."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import learn_hub as H

FILE = "learn_waiting.json"
WAITING, SKIPPED, LEARNED = "waiting", "skipped", "learned"
OWN_PROJECT_S = 600.0     # the app's own <name>_edit.prproj counts as your fix once saved 10 min after it was made
SETTLE_S = 10.0           # a .prproj untouched this long is saved (Premiere has finished writing it)
KEEP_CLOSED = 200         # skipped / learned entries kept (so a skipped run never comes back), the oldest dropped


@dataclass
class Entry:
    name: str
    project: str = ""
    run: str = ""
    run_in_project: str = ""      # the run folder inside the project ("edit", "edit-2"): found again after a move
    raw: str = ""
    competitor: str = ""
    channel: str = ""
    title: str = ""
    result: str = ""
    finished: str = ""
    computer: str = ""
    state: str = WAITING
    changed: str = ""
    id: str = ""

    def key(self) -> str:
        return self.id or make_id(self.run or self.project or self.name)

    def to_json(self, hub: str | os.PathLike | None) -> dict:
        d = dict(self.__dict__)
        d["id"] = self.key()
        if hub is not None:
            d["project"] = H.portable(self.project, hub) if self.project else ""
            d["run"] = H.portable(self.run, hub) if self.run else ""
        return {k: v for k, v in d.items() if v not in ("", None)}

    @classmethod
    def from_json(cls, d: dict, hub: str | os.PathLike | None) -> "Entry":
        keys = set(cls.__dataclass_fields__)
        e = cls(**{k: str(v) for k, v in d.items() if k in keys and v is not None})
        if hub is not None:
            e.project, e.run = H.resolve(e.project, hub), H.resolve(e.run, hub)
        e.id = e.key()
        return e


def make_id(text: str) -> str:
    t = H.for_pc(os.path.normpath(str(text))).lower().rstrip("\\/")
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]


def path(folder: str | os.PathLike) -> Path:
    return Path(folder) / FILE


def _load(text: str | None, hub: str | os.PathLike | None) -> list[Entry]:
    if not text:
        return []
    try:
        d = json.loads(text)
    except ValueError:
        return []
    items = d.get("items") if isinstance(d, dict) else d if isinstance(d, list) else []
    out = []
    for it in items or []:
        if isinstance(it, dict) and it.get("name"):
            try:
                out.append(Entry.from_json(it, hub))
            except TypeError:
                continue
    return out


def _dump(items: list[Entry], hub: str | os.PathLike | None) -> str:
    closed = [e for e in items if e.state != WAITING]
    drop = {id(e) for e in closed[:max(0, len(closed) - KEEP_CLOSED)]}
    keep = [e for e in items if id(e) not in drop]
    return json.dumps({"version": 1, "items": [e.to_json(hub) for e in keep]}, indent=1, ensure_ascii=False) + "\n"


def read(folder: str | os.PathLike, include_closed: bool = False) -> list[Entry]:
    """The entries, oldest first (only the waiting ones unless ``include_closed``)."""
    items = _load(H._read(path(folder)), folder)
    return items if include_closed else [e for e in items if e.state == WAITING]


def _change(folder: str | os.PathLike, fn) -> str:
    def change(old: str | None) -> str:
        items = _load(old, folder)
        fn(items)
        new = _dump(items, folder)
        if old and _load(old, folder) == _load(new, folder) and old.strip():
            return old                                        # nothing changed: not written
        return new
    return H._change(path(folder), change)


def add(folder: str | os.PathLike, e: Entry) -> str:
    """A finished edit waits for its fix. The same run again: replaced in place (a skipped or learned one stays as it
    is); another, newer run of the same project replaces the project's waiting card (the newest edit is the one you
    fix). "" or the problem."""
    it = Entry(**e.__dict__)
    it.finished = it.finished or H.stamp()
    it.computer = it.computer or H.this_computer()
    it.state, it.changed = WAITING, it.finished
    it.id = it.key()

    def apply(items: list[Entry]) -> None:
        for i, x in enumerate(items):
            if x.key() == it.id:
                if x.state == WAITING:
                    items[i] = it
                return
        for i, x in enumerate(items):
            if x.state == WAITING and x.project and it.project and _same_project(x, it):
                items[i] = it
                return
        items.append(it)
    return _change(folder, apply)


def _same_project(a: Entry, b: Entry) -> bool:
    return H._same_path(a.project, b.project) or (Path(a.project).name.lower() == Path(b.project).name.lower()
                                                   and bool(Path(a.project).name))


def set_state(folder: str | os.PathLike, eid: str, state: str) -> str:
    found = {"n": 0}

    def apply(items: list[Entry]) -> None:
        for x in items:
            if x.key() == eid and x.state != state:
                x.state, x.changed = state, H.stamp()
                found["n"] += 1
    err = _change(folder, apply)
    return err


def skip(folder: str | os.PathLike, eid: str) -> str:
    """"Skip": the card goes (it never comes back for this run)."""
    return set_state(folder, eid, SKIPPED)


def learned(folder: str | os.PathLike, run: str | os.PathLike | None = None,
            project: str | os.PathLike | None = None) -> str:
    """A fix of this run (or project) went into the learn queue: its waiting card goes."""
    def apply(items: list[Entry]) -> None:
        for x in items:
            if x.state != WAITING:
                continue
            if (run and x.run and H._same_path(x.run, str(run))) or \
                    (project and x.project and H._same_path(x.project, str(project))):
                x.state, x.changed = LEARNED, H.stamp()
    return _change(folder, apply)


def from_job(job: Any, res: dict) -> Entry:
    """The entry of a finished queue job (core.jobs.Job + its result)."""
    project = getattr(job, "project", None)
    run = str(res.get("run_dir") or getattr(job, "run_dir", "") or "")
    rel = ""
    if project is not None and run:
        try:
            rel = os.path.relpath(run, str(project))
            if rel.startswith(".."):
                rel = ""
        except ValueError:
            rel = ""
    name = Path(project).name if project is not None else str(getattr(job, "name", "") or Path(run).name)
    raw, comp = getattr(job, "raw", None), getattr(job, "competitor", None)
    return Entry(name=name, project=str(project or ""), run=run, run_in_project=rel,
                 raw=Path(raw).name if raw else "", competitor=Path(comp).name if comp else "",
                 channel=str(getattr(job, "channel", "") or ""), title=str(getattr(job, "title", "") or ""),
                 result=str(res.get("label") or ""))


def record(folder: str | os.PathLike, job: Any, res: dict) -> str:
    """The queue's hook: an edit that finished PASS or OK with a note waits for its fix; anything else: nothing."""
    try:
        from . import results as R
        if str((res or {}).get("label") or "") not in R.GOOD:
            return ""
        return add(folder, from_job(job, res))
    except Exception as e:  # noqa: BLE001 - the hook never breaks the queue
        return f"the waiting card could not be saved: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------------------------------------------------
# what the Learn tab shows
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Card:
    """A waiting card as the Learn tab shows it: the entry, its project and run as found on this computer now, the
    name it will be learned under, rendered (amber) or not, and a fix found in its folder."""
    entry: Entry
    project: Path | None = None
    run: Path | None = None
    name: str = ""
    rendered: bool = False
    fix: Path | None = None

    @property
    def id(self) -> str:
        return self.entry.key()


def _where(e: Entry, hub: Path) -> tuple[Path | None, Path | None]:
    """The project and the run where they are now: as written, else in the hub (a project moved to the shared folder
    when its edit was made keeps its name)."""
    proj = None
    for cand in (e.project, str(hub / Path(e.project).name) if e.project else "",
                 str(hub / e.name) if e.name else ""):
        if cand and os.path.isdir(cand):
            proj = Path(cand)
            break
    run = None
    for cand in (e.run, str(proj / e.run_in_project) if proj is not None and e.run_in_project else ""):
        if cand and os.path.isdir(cand):
            run = Path(cand)
            break
    return proj, run


def _rendered(project: Path | None) -> bool:
    if project is None:
        return False
    try:
        from . import board as B
        c = B.card_of(project)
        return not c.error and (B.INDEX.get(c.column, 0) >= B.INDEX["rendered"] or "rendered" in c.dates)
    except Exception:  # noqa: BLE001
        return False


def cards(folder: str | os.PathLike, repo: str | os.PathLike | None = None) -> list[Card]:
    """The waiting cards, newest first: entries already in the learn queue left out, each with the next free name
    (the oldest waiting gets the lowest number: video16, video17 ...) and rendered or not. Reads the disk."""
    hub = Path(folder)
    items = read(hub)
    queued_runs = [q.run for q in H.queue_items(hub, include_done=True) if q.run]
    out: list[Card] = []
    for e in items:
        proj, run = _where(e, hub)
        if any(H._same_path(q, str(run or e.run)) for q in queued_runs):
            continue
        out.append(Card(e, proj, run, rendered=_rendered(proj)))
    try:
        from . import learnkit
        first = learnkit.next_free_name(repo, hub)
        m = re.match(r"^(\D+)(\d+)$", first)
        for k, c in enumerate(out):
            c.name = f"{m.group(1)}{int(m.group(2)) + k}" if m else first
    except Exception:  # noqa: BLE001 - a name is never worth an error
        pass
    order = {id(c): i for i, c in enumerate(out)}
    out.sort(key=lambda c: (c.entry.finished, order[id(c)]), reverse=True)
    return out


_PRPROJ_SKIP = re.compile(r"(?i)(auto-save|adobe premiere pro auto-save|backup)")


def _prproj_files(folder: Path) -> list[Path]:
    """.prproj files right in the project folder and its edit folders (Premiere's auto-saves are in their own folder,
    not looked into)."""
    from . import publish as PB
    out: list[Path] = []
    try:
        dirs = [folder] + [p for p in folder.iterdir() if p.is_dir() and PB.EDIT_RE.match(p.name)]
    except OSError:
        return out
    for d in dirs:
        try:
            for f in d.iterdir():
                if f.suffix.lower() == ".prproj" and not _PRPROJ_SKIP.search(f.name) and f.is_file():
                    out.append(f)
        except OSError:
            continue
    return out


def _stamp_time(text: str) -> float:
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError, TypeError):
        return 0.0


def is_fix(f: Path, finished: float, now: float | None = None) -> bool:
    """A .prproj that is your fix: saved after the edit was made and untouched for SETTLE_S; the app's own
    <name>_edit.prproj (made empty by Open in Premiere) only once it was saved OWN_PROJECT_S after it was made."""
    now = time.time() if now is None else now
    try:
        st = f.stat()
    except OSError:
        return False
    if now - st.st_mtime < SETTLE_S or st.st_mtime < finished - 60:
        return False
    from . import learnkit
    if learnkit.FIXED_RE.search(f.name):
        return True
    made = getattr(st, "st_birthtime", None) or st.st_ctime
    if f.stem.lower().endswith("_edit"):
        return st.st_mtime - made >= OWN_PROJECT_S
    return True


def find_fixes(found: Iterable[Card], state_path: str | os.PathLike | None = None,
               now: float | None = None) -> list[tuple[Card, Path]]:
    """[(waiting card, the .prproj you saved into its project)] -- the newest per card, not offered before
    (learnkit.mark_seen) or changed since. Folder listings only: cheap enough for every minute. Never raises."""
    out: list[tuple[Card, Path]] = []
    try:
        from . import learnkit
        seen = learnkit._load_seen(Path(state_path)) if state_path else {}
        for c in found:
            if c.project is None:
                continue
            t0 = _stamp_time(c.entry.finished)
            best: tuple[float, Path] | None = None
            for f in _prproj_files(c.project):
                if not is_fix(f, t0, now):
                    continue
                st = f.stat()
                rec = seen.get(learnkit._key(f)) or {}
                if rec.get("size") == st.st_size and rec.get("mtime_ns") == st.st_mtime_ns:
                    continue
                if best is None or st.st_mtime > best[0]:
                    best = (st.st_mtime, f)
            if best is not None:
                c.fix = best[1]
                out.append((c, best[1]))
    except Exception:  # noqa: BLE001 - a watcher never stops the window
        pass
    return out
