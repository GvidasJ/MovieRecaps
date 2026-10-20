"""A project renamed after its title (day 13, section 4): when you change the YouTube title or the headline -- or press
"Rename to title" on a board card -- the project's folder and the files the app made follow the title (names.py),
with everything kept linked. Call everything here from a worker thread (it reads folders, maybe on the network).

What is renamed:

* the project's folder on this computer and its copy in the shared folder (the same project: its "created" stamp) --
  the shared copy first;
* in each run folder (edit, edit-2, cuts ...): the app's XML copy, ``<title>_edit.xml`` (its sequence called after the
  title), and the app's own Premiere project while it is still empty (``<title>.prproj``). 1_edit.xml keeps its name
  and every byte except its media paths, which are pointed at the new folder (as publishing does for the shared
  copy);
* what points into the folder: project.json (name, old_names, published.to, the steps' run_dir), the Learn waiting
  card (learn_waiting.json), the projects waiting for the shared folder (_pending_publish.json), the paths in
  problems_queue.md, and the app's own rows (the pages' ``moved(old, new)``).

It waits (``request`` puts it on the ``pending`` list; the window tries again every minute) while something uses the
folder: a work lock (an edit, a copy to the shared folder, Clean up ...), a step running in project.json, a row of
this app (``BUSY``: the queue, the New video rows), Premiere with a project open that is saved in it, or Windows
refusing the rename (a file inside is open: a player, Explorer's preview, Premiere's render).

It keeps its name -- said in plain words, remembered in project.json ("names": {"kept": ...}) -- when a Premiere
project plays its files: the folder's own media would go "offline" in it (your fixed projects are saved on the
Desktop and play the RAW from the shared folder by its full path). Seen from: a project saved in the folder with an
edit in it, the edit opened in Premiere by the app (project.json "premiere"), the card in Fixed / Rendered /
Uploaded, a fix of it in the learn queue, and a .prproj in Premiere's recent list, on the Desktop or in a folder next
to the shared one (D:\\Shared\\Copied_*) that plays a file from it (only read). The app's own XML copy still follows
the title then (your projects never use it). A project from before day 13 is renamed only by "Rename to title"
(``explicit``)."""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from . import names as NM
from . import projects as P

RENAMED, WAIT, KEPT, SAME, NO_TITLE, OLD, GONE = "renamed", "wait", "kept", "same", "no title", "old", "gone"
PENDING_FILE = "renames_pending.json"
RETRY_S = 60.0                      # the window tries the waiting renames this often
RUNNING_FRESH_S = 6 * 3600.0        # a step "running" in project.json since less than this: someone is on it (older:
#                                     a program that stopped half-way -- it does not hold the name for ever)
SCAN_MAX_BYTES = 200 << 20          # a .prproj bigger than this is not read (yours are 0.1 - 5 MB)
SCAN_FILES_MAX = 80
STEP_WORDS = {"download": "download", "convert": "conversion", "template": "template", "title_check": "title check",
              "edit": "edit"}
_RUN_RE = re.compile(r"(?i)^(edit|cuts)(-\d+)?$")

BUSY: list[Callable[[], Any]] = []         # the app's pages (weak refs): why one of their rows uses a folder ('' = none)
_LOCK = threading.Lock()                   # one rename at a time in this program
_SCAN: dict[str, tuple[tuple[int, int], frozenset[str]]] = {}     # .prproj -> ((size, mtime), its paths' folder names)


@dataclass
class Result:
    """What ``rename`` did: ``state`` (RENAMED, WAIT, KEPT, SAME, NO_TITLE, OLD, GONE), the project's folder now,
    plain words, every folder moved ((old, new): this computer's and the shared copy), the files the app renamed."""
    state: str
    folder: Path
    text: str = ""
    moved: list[tuple[Path, Path]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    @property
    def renamed(self) -> bool:
        return self.state == RENAMED


def register_busy(fn: Callable[[Path], str]) -> None:
    """A page says which project folders its rows use (``fn(folder)`` -> why, '' when none). A page's method is kept
    weakly (a closed page is forgotten)."""
    import weakref
    unregister_busy(fn)
    try:
        BUSY.append(weakref.WeakMethod(fn))                      # type: ignore[arg-type]
    except TypeError:
        BUSY.append(lambda f=fn: f)


def unregister_busy(fn: Callable[[Path], str]) -> None:
    BUSY[:] = [r for r in BUSY if r() is not None and r() != fn]


def _busy_why(folder: Path) -> str:
    for ref in list(BUSY):
        fn = ref()
        if fn is None:
            continue
        try:
            why = fn(folder)
        except Exception:  # noqa: BLE001 - a page that cannot tell: not busy
            why = ""
        if why:
            return str(why)
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------

def _norm(p: str | os.PathLike) -> str:
    s = str(p)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(s)))


def _inside(child: str | os.PathLike, parent: str | os.PathLike) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _ts(text: Any) -> float | None:
    try:
        d = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.astimezone()).timestamp()


def _read(folder: Path) -> dict | None:
    try:
        d = P.read_json(folder / P.FILE, tries=5)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def run_dirs(folder: str | os.PathLike) -> list[Path]:
    """The project's run folders (edit, edit-2, cuts ...) that hold a 1_edit.xml."""
    try:
        return sorted(d for d in Path(folder).iterdir()
                      if d.is_dir() and _RUN_RE.match(d.name) and (d / "1_edit.xml").is_file())
    except OSError:
        return []


def _has_runs(folder: Path) -> bool:
    try:
        return any(d.is_dir() and _RUN_RE.match(d.name) for d in folder.iterdir())
    except OSError:
        return True                                   # cannot tell: as if it had


def titles(folder: Path, data: dict) -> dict:
    """project.json's titles as the board reads them (a title.txt you edited by hand wins)."""
    try:
        from . import board as B
        return {**data, "title": B.title_of(folder, data), "upload_title": B.upload_title_of(folder, data)}
    except Exception:  # noqa: BLE001
        return dict(data)


def _roots(hub: Any, local_root: Any) -> tuple[Path | None, Path | None]:
    if hub is None:
        try:
            from . import settings as S
            hub = S.save_root()
        except Exception:  # noqa: BLE001
            hub = None
    if local_root is None:
        from . import paths
        local_root = paths.projects_root()
    return (Path(hub) if hub else None), (Path(local_root) if local_root else None)


# ---------------------------------------------------------------------------------------------------------------------
# the copies of one project
# ---------------------------------------------------------------------------------------------------------------------

def copies(folder: Path, data: dict, hub: Path | None, local_root: Path | None) -> list[tuple[Path, dict, bool]]:
    """[(folder, its project.json, in the shared folder)] -- the shared copy first: ``folder`` itself, and its twin --
    the shared copy it was published to (published.to, the same project), or for a shared copy this computer's own
    folder of it (the same "created" stamp; its name may differ)."""
    from . import publish as PB
    me_shared = hub is not None and _inside(folder.parent, hub) and _norm(folder.parent) == _norm(hub)
    out = [(folder, data, me_shared)]
    twin: tuple[Path, dict] | None = None
    if not me_shared:
        to = (data.get("published") or {}).get("to") if isinstance(data.get("published"), dict) else None
        cands = [Path(to)] if to else []
        if hub is not None and to:
            cands.append(hub / Path(to).name)
        for c in cands:
            try:
                if _norm(c) != _norm(folder) and (c / P.FILE).is_file():
                    d = _read(c)
                    if d is not None and PB.same_project(data, d):
                        twin = (c, d)
                        break
            except OSError:
                continue
    elif local_root is not None and _norm(local_root) != _norm(hub):
        cands = [local_root / folder.name]
        try:
            cands += [d for d in local_root.iterdir() if d.is_dir() and d.name != folder.name]
        except OSError:
            pass
        for c in cands:
            try:
                if (c / P.FILE).is_file():
                    d = _read(c)
                    if d is not None and PB.same_project(data, d):
                        twin = (c, d)
                        break
            except OSError:
                continue
    if twin is not None:
        out.append((twin[0], twin[1], not me_shared))
    out.sort(key=lambda x: not x[2])                  # the shared copy first
    return out


# ---------------------------------------------------------------------------------------------------------------------
# in use (wait) and used by a Premiere project (keep the name)
# ---------------------------------------------------------------------------------------------------------------------

def in_use(folder: Path, data: dict, busy: bool = True, hub: Path | None = None) -> str:
    """Why the folder cannot be renamed right now, in plain words ('' when it can): a work lock, a step running, a row
    of this app (``busy``: False for the row that asks itself), Premiere with a project open that is saved in it.
    ``hub``: the shared folder -- a first copy there (publish locks <hub>\\<name> while it copies from here) counts."""
    try:
        from . import locks
        h = locks.holder(folder)
        if h is not None and not h.get("stale"):
            return locks.describe(h).replace("this project", "it")
        there = hub / folder.name if hub is not None and _norm(folder.parent) != _norm(hub) else None
        if there is not None and there.is_dir():
            h = locks.holder(there)
            if h is not None and not h.get("stale") and h.get("what") == "publish":
                return "it is being copied to the shared folder"
    except Exception:  # noqa: BLE001 - cannot tell: wait
        return "its work lock cannot be read"
    now = time.time()
    for name, st in (data.get("steps") or {}).items() if isinstance(data.get("steps"), dict) else ():
        if isinstance(st, dict) and st.get("state") == P.RUNNING:
            t = _ts(st.get("started"))
            if t is None or now - t < RUNNING_FRESH_S:
                return f"its {STEP_WORDS.get(name, name)} is running"
    why = _busy_why(folder) if busy else ""
    if why:
        return why
    try:
        from . import premiere
        st = premiere.state()
        if st.project is not None and _inside(st.project, folder):
            return f"Premiere has {st.project.name} open"
    except Exception:  # noqa: BLE001 - Premiere cannot be asked: the rename itself fails if it holds a file
        pass
    return ""


def saved_projects(folder: Path) -> list[Path]:
    """.prproj files in the project folder and its run folders that hold an edit (saved with a sequence in them:
    yours, or the app's own after you saved it). Premiere's auto-save folder is not looked into."""
    from .premiere import holds_edit
    out = []
    try:
        dirs = [folder] + [d for d in folder.iterdir() if d.is_dir() and _RUN_RE.match(d.name)]
    except OSError:
        return out
    for d in dirs:
        try:
            for f in d.iterdir():
                if f.suffix.lower() == ".prproj" and f.is_file() and holds_edit(f):
                    out.append(f)
        except OSError:
            continue
    return out


def _prefs_projects() -> list[Path]:
    """The projects Premiere opened lately (its recent list, every version's prefs), as files."""
    out: list[Path] = []
    base = Path.home() / "Documents" / "Adobe" / "Premiere Pro"
    try:
        prefs = list(base.glob("*/Profile-*/Adobe Premiere Pro Prefs"))
    except OSError:
        return out
    for pf in prefs:
        try:
            text = pf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.findall(r"<BE\.Prefs\.MRU\.Document\.\d+>([^<]+)</", text)
        m = re.search(r"<MZ\.Prefs\.ProjectsOpenAtLastShutdown>([^<]+)</", text)
        if m:
            try:
                found += [str(x) for x in (json.loads(m.group(1).replace("&quot;", '"')).get("Projects") or [])]
            except (ValueError, AttributeError):
                pass
        for f in found:
            f = f.strip()
            if f.startswith("\\\\?\\"):
                f = f[4:]
            if f.lower().endswith(".prproj"):
                out.append(Path(f))
    return out


def scan_places(hub: Path | None) -> list[Path]:
    """Where your Premiere projects are looked for (only read): Premiere's recent projects, the Desktop, and the
    folders next to the shared projects folder (D:\\Shared\\Copied_*\\*.prproj, one level)."""
    out: list[Path] = list(_prefs_projects())
    tops = [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"]
    if hub is not None:
        tops.append(hub.parent)
        try:
            tops += [d for d in hub.parent.iterdir() if d.is_dir() and _norm(d) != _norm(hub)]
        except OSError:
            pass
    for d in tops:
        try:
            out += [f for f in d.iterdir() if f.suffix.lower() == ".prproj" and f.is_file()]
        except OSError:
            continue
    seen, uniq = set(), []
    for f in out:
        k = _norm(f)
        if k not in seen and "auto-save" not in k:
            seen.add(k)
            uniq.append(f)
    return uniq[:SCAN_FILES_MAX]


_PATH_RX = re.compile(r"(?:[A-Za-z]:|\\\\[^\\<>\"|]+)\\[^<>\"|\r\n]+")


def folder_names_in(prproj: Path) -> frozenset[str]:
    """The folder names (lower case) of every path a .prproj holds -- read once per version of the file."""
    try:
        st = os.stat(prproj)
    except OSError:
        return frozenset()
    sig = (int(st.st_size), int(st.st_mtime_ns))
    hit = _SCAN.get(_norm(prproj))
    if hit is not None and hit[0] == sig:
        return hit[1]
    names: set[str] = set()
    if st.st_size <= SCAN_MAX_BYTES:
        try:
            data = Path(prproj).read_bytes()
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            text = data.decode("utf-8", "replace")
            for m in _PATH_RX.finditer(text):
                for part in re.split(r"[\\/]", m.group(0))[:-1]:
                    if part:
                        names.add(part.lower())
        except (OSError, EOFError, gzip.BadGzipFile, ValueError):
            names = set()
    out = frozenset(names)
    _SCAN[_norm(prproj)] = (sig, out)
    return out


def premiere_users(folder: Path, data: dict, hub: Path | None, scan: bool = True) -> str:
    """Why a Premiere project may play files of the folder, in plain words ('' when none can): see the module."""
    for f in saved_projects(folder):
        return f"your Premiere project {f.name} is saved in it with the edit"
    pr = data.get("premiere") if isinstance(data.get("premiere"), dict) else None
    if pr and pr.get("opened"):
        return ("its edit was opened in Premiere -- a project saved anywhere may play its files (a new name would make "
                "them offline)")
    try:
        from . import board as B
        c = B.card_of(folder)
        for col in ("uploaded", "rendered", "fixed"):
            if c.column == col or col in (c.dates or {}):
                return f"it is {B.LABELS.get(col, col).lower()} already (your Premiere project plays its files)"
    except Exception:  # noqa: BLE001
        pass
    if hub is not None:
        try:
            from . import learn_hub as H
            for q in H.queue_items(hub, include_done=True):
                if q.run and _inside(H.resolve(q.run, hub), folder):
                    return "your fix of it is in the learn queue (its project plays the files)"
        except Exception:  # noqa: BLE001
            pass
    if scan:
        name = folder.name.lower()
        for f in scan_places(hub):
            if name in folder_names_in(f):
                return f"your Premiere project {f.name} ({f.parent}) plays files from it"
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# the app's files in a run folder
# ---------------------------------------------------------------------------------------------------------------------

def _same_edit(a: str, b: str) -> bool:
    """Two XML texts are the same edit (the sequence's name aside)."""
    from .premiere import rename_sequence
    try:
        return rename_sequence(a, "-") == rename_sequence(b, "-")
    except ValueError:
        return a == b


def sync_run_files(run: Path, data: dict | None) -> list[str]:
    """The app's files in a run folder follow the project's names (a project whose names follow its title): its XML
    copy is ``<title>_edit.xml`` (made now when missing, the sequence called after the title); an older copy of the
    app's (the same edit: 1_edit.xml with another sequence name) is removed with its empty Premiere project renamed
    to ``<title>.prproj`` -- both stay when you saved that project (it holds the edit). 1_edit.xml and your own files
    are never touched. Returns what was done (file names)."""
    from . import premiere
    xml = run / "1_edit.xml"
    if not xml.is_file() or not NM.uses_titles(data):
        return []
    fname, seq = NM.copy_name(run, data)
    if seq is None:
        return []
    done: list[str] = []
    out = run / fname
    had = out.is_file()
    try:
        premiere.named_copy(xml)
    except (OSError, ValueError) as e:
        return [f"{fname} could not be written ({e})"]
    if not had:
        done.append(fname)
    try:
        base = xml.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return done
    new_proj = run / NM.project_name(out)
    for f in sorted(run.iterdir()):
        if f.suffix.lower() != ".xml" or f.name.lower() in ("1_edit.xml", fname.lower()) or not f.is_file():
            continue
        try:
            text = f.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _same_edit(text, base):
            continue                                  # not the app's copy of this edit: yours
        partners = [p for p in {run / NM.project_name(f), f.with_suffix(".prproj")} if p.is_file()]
        if any(premiere.holds_edit(p) for p in partners):
            continue                                  # you saved that project: it and its copy stay as they are
        for p in partners:
            if not new_proj.exists():
                try:
                    os.replace(p, new_proj)
                    done.append(f"{p.name} -> {new_proj.name}")
                except OSError:
                    pass
        try:
            f.unlink()
            done.append(f"{f.name} removed (now {fname})")
        except OSError:
            pass
    return done


# ---------------------------------------------------------------------------------------------------------------------
# renaming
# ---------------------------------------------------------------------------------------------------------------------

def rename(folder: str | os.PathLike, *, hub: str | os.PathLike | None = None,
           local_root: str | os.PathLike | None = None, explicit: bool = False, scan: bool = True,
           busy: bool = True) -> Result:
    """Rename a project after its title now, when nothing uses it (module docstring). ``explicit``: "Rename to
    title" (also a project from before day 13; it then follows its title from now on); ``scan``: look for Premiere
    projects that play its files; ``busy``: ask the app's rows (False: the row that asks, about its own project).
    Never raises."""
    with _LOCK:
        try:
            return _rename(Path(folder), *_roots(hub, local_root), explicit, scan, busy)
        except Exception as e:  # noqa: BLE001 - said, never a crash
            return Result(WAIT, Path(folder), f"It could not be renamed now ({type(e).__name__}: {e}) -- tried again "
                                              "later")


def _rename(folder: Path, hub: Path | None, local_root: Path | None, explicit: bool, scan: bool,
            busy: bool = True) -> Result:
    data = _read(folder)
    if data is None:
        return Result(GONE, folder, "The project folder is not there any more")
    if not explicit and not NM.uses_titles(data):
        return Result(OLD, folder, "A project from before: it keeps its name (Rename to title on its card renames it)")
    d = titles(folder, data)
    t, src = NM.title_of(d)
    if not t:
        return Result(NO_TITLE, folder, "It has no title yet, so it keeps its name")
    base = NM.folder_base(t, NM.day_of(folder.name) or None, NM.root_len(folder.parent))
    mark = {**(data.get(NM.KEY) if isinstance(data.get(NM.KEY), dict) else NM.marker(src)), "rule": NM.RULE,
            "from": src}
    mark.pop("kept", None)
    cps = copies(folder, data, hub, local_root)
    same = {"title": d.get("title"), "upload_title": d.get("upload_title")}
    todo = [(c, dd, sh) for c, dd, sh in cps if not NM.has_base(c.name, base)]
    used = next((w for w in (in_use(c, dd, busy, hub) for c, dd, _sh in cps) if w), "")
    if not todo:
        if explicit and not NM.uses_titles(data):
            for c, _dd, _sh in cps:
                P.load(c).update(**{NM.KEY: mark})
        files: list[str] = []
        if not used:                                  # (nothing is touched while something uses it)
            _share_titles(cps, folder, same)
            files = [f for c, _dd, _sh in cps for r in run_dirs(c) for f in sync_run_files(r, _read(c))]
        return Result(SAME, folder, f"It is named after {NM.SOURCE_WORDS.get(src, 'its title')} already", files=files)
    if used:
        return Result(WAIT, folder, f"Renamed later, when it is free: {used}")
    for c, dd, _sh in cps:
        if not (_has_runs(c) or dd.get("published") or dd.get("premiere")):
            continue                                  # no edit yet: no Premiere project can use it
        why = premiere_users(c, dd, hub, scan)
        if why:
            for c2, _d2, _s2 in cps:
                def keep(x: dict, why=why) -> None:
                    n = x.get(NM.KEY) if isinstance(x.get(NM.KEY), dict) else ({} if not explicit else dict(mark))
                    if n or explicit:
                        n = dict(n)
                        n["kept"] = {"for": base, "why": why, "at": P.now()}
                        x[NM.KEY] = n
                try:
                    P.load(c2).change(keep)
                except (OSError, ValueError):
                    pass
            files = []
            if NM.uses_titles(_read(folder) or {}):
                _share_titles(cps, folder, same)
                files = [f for c2, _d2, _s2 in cps for r in run_dirs(c2) for f in sync_run_files(r, _read(c2))]
            return Result(KEPT, folder, f"It keeps its name: {why}", files=files)
    moved: list[tuple[Path, Path]] = []
    shared_pair: tuple[Path, Path] | None = None
    problem = ""
    for c, _dd, sh in todo:
        new = NM.unique_folder(c.parent, base, current=c)
        if _norm(new) == _norm(c):
            continue
        try:
            from . import locks
            h = locks.holder(c)
            if h is not None and not h.get("stale"):
                problem = locks.describe(h).replace("this project", "it")
                break
        except Exception:  # noqa: BLE001
            pass
        try:
            os.rename(c, new)
        except OSError as e:
            problem = (f"a file in it is open in another program ({e.strerror or e}: Premiere, a player, Explorer's "
                       "preview ...)")
            break
        moved.append((c, new))
        if sh:
            shared_pair = (c, new)
    if problem and not moved:                         # nothing moved: nothing is touched, tried again later
        return Result(WAIT, folder, f"Renamed later, when it is free: {problem}")
    files = []
    for c, _dd, sh in cps:
        now_at = next((n for o, n in moved if _norm(o) == _norm(c)), c)
        _fix_json(now_at, moved, shared_pair, mark, None if _norm(c) == _norm(folder) else same)
        for r in run_dirs(now_at):
            try:
                from . import publish as PB
                PB.relink_edit(r)
            except Exception:  # noqa: BLE001 - Open in Premiere relinks too
                pass
            files += sync_run_files(r, _read(now_at))
    if moved:
        _fix_outside(moved, hub, local_root)
    here = next((n for o, n in moved if _norm(o) == _norm(folder)), folder)
    if problem:
        return Result(WAIT, here, f"Renamed later, when it is free: {problem}", moved, files)
    return Result(RENAMED, here, f"Renamed to {here.name}", moved, files)


def _fix_json(folder: Path, moved: list[tuple[Path, Path]], shared_pair: tuple[Path, Path] | None, mark: dict,
              same_titles: dict | None = None) -> None:
    """project.json of a copy after the move: its name and old names, the steps' run folders, published.to (the
    shared copy's new name -- written as that computer sees the shared folder), the names marker; ``same_titles``:
    the twin of the copy whose title changed takes its titles (and title.txt), as the next publish would merge them
    -- its files are then named after the same title."""
    from . import publish as PB

    def apply(d: dict) -> None:
        if same_titles is not None:
            changed = False
            for k, v in same_titles.items():
                if (d.get(k) or "") != (v or ""):
                    d[k] = v
                    changed = True
            if changed:
                try:
                    PB.write_title_txt(folder, d)
                except OSError:
                    pass
        for old, new in moved:
            if _norm(new) == _norm(folder):
                d["name"] = new.name
                olds = [str(x) for x in (d.get("old_names") or []) if x]
                if old.name not in olds:
                    olds.append(old.name)
                d["old_names"] = olds
                PB._moved_paths(d, old, new)                                       # noqa: SLF001
        pub = d.get("published")
        if shared_pair is not None and isinstance(pub, dict) and pub.get("to"):
            to = Path(str(pub["to"]))
            if to.name.lower() == shared_pair[0].name.lower():
                pub["to"] = str(to.parent / shared_pair[1].name)
        d[NM.KEY] = dict(mark)
    try:
        P.load(folder).change(apply)
    except (OSError, ValueError):
        pass


def _share_titles(cps: list[tuple[Path, dict, bool]], folder: Path, same: dict) -> None:
    """The copies other than ``folder`` take its titles (and title.txt): the files of both follow one title."""
    from . import publish as PB
    for c, _dd, _sh in cps:
        if _norm(c) == _norm(folder):
            continue

        def apply(d: dict) -> None:
            changed = False
            for k, v in same.items():
                if (d.get(k) or "") != (v or ""):
                    d[k] = v
                    changed = True
            if changed:
                try:
                    PB.write_title_txt(c, d)
                except OSError:
                    pass
        try:
            P.load(c).change(apply)
        except (OSError, ValueError):
            pass


def _replace_paths(text: str, pairs: Iterable[tuple[str, str]]) -> str:
    for old, new in pairs:
        if old and old in text:
            text = re.sub(re.escape(old) + r"(?=[\\/\s)\],;]|$)", lambda _m, n=new: n, text, flags=re.M)
    return text


def _fix_outside(moved: list[tuple[Path, Path]], hub: Path | None, local_root: Path | None) -> None:
    """What points into the moved folders: the Learn waiting cards, the projects waiting for the shared folder,
    problems_queue.md's paths."""
    pairs = [(str(o), str(n)) for o, n in moved]
    if hub is not None:
        try:
            from . import waiting
            waiting.relink(hub, pairs)
        except Exception:  # noqa: BLE001 - a card that does not follow is found again by its folder name
            pass
        try:
            from . import learn_hub as H
            from . import problems
            pf = hub / problems.FILE
            if pf.is_file():
                rel = [(f".\\{o.name}", f".\\{n.name}") for o, n in moved if _norm(o.parent) == _norm(hub)]

                def change(old: str | None) -> str:          # only the paths: each entry keeps its heading and id
                    return _replace_paths(old or "", pairs + rel)
                H._change(pf, change)                                              # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
    if local_root is not None:
        try:
            from . import publish as PB
            if PB.pending(local_root):
                def fix(items: list[dict]) -> None:
                    for x in items:
                        for o, n in moved:
                            if _norm(x.get("folder") or "") == _norm(o):
                                x["folder"], x["name"] = str(n), n.name
                PB._change_queue(local_root, fix)                                  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------------------------------------------------
# after a title change; the renames that wait
# ---------------------------------------------------------------------------------------------------------------------

def pending_path() -> Path:
    from . import settings as S
    return S.app_dir() / PENDING_FILE


def pending() -> list[dict]:
    """The renames that wait: [{folder, why, since, tries, explicit}]."""
    try:
        d = json.loads(pending_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("items") if isinstance(d, dict) else None
    return [x for x in items or [] if isinstance(x, dict) and x.get("folder")]


def _write_pending(items: list[dict]) -> None:
    f = pending_path()
    if not items:
        try:
            f.unlink()
        except OSError:
            pass
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    P.write_json(f, {"version": 1, "items": items})


_PENDING_LOCK = threading.Lock()


def _set_pending(folder: Path, why: str = "", explicit: bool = False, drop: bool = False,
                 new: Path | None = None) -> None:
    with _PENDING_LOCK:
        items = pending()
        k = _norm(folder)
        found = next((x for x in items if _norm(x["folder"]) == k), None)
        if drop:
            items = [x for x in items if _norm(x["folder"]) != k]
        elif found is None:
            items.append({"folder": str(new or folder), "why": why, "since": P.now(), "tries": 0,
                          "explicit": bool(explicit)})
        else:
            found.update(folder=str(new or folder), why=why, tries=int(found.get("tries") or 0) + 1,
                         explicit=bool(found.get("explicit") or explicit))
        _write_pending(items)


def request(folder: str | os.PathLike, *, hub: str | os.PathLike | None = None,
            local_root: str | os.PathLike | None = None, explicit: bool = False, scan: bool = True) -> Result:
    """After a title change (or Rename to title): rename now; when it has to wait, it is put on the pending list
    (``retry_pending`` every minute)."""
    res = rename(folder, hub=hub, local_root=local_root, explicit=explicit, scan=scan)
    try:
        if res.state == WAIT:
            _set_pending(Path(folder), res.text, explicit, new=res.folder)
            if _norm(res.folder) != _norm(folder):
                _set_pending(Path(folder), drop=True)
                _set_pending(res.folder, res.text, explicit)
        else:
            _set_pending(Path(folder), drop=True)
    except OSError:
        pass
    return res


def retry_pending(*, hub: str | os.PathLike | None = None, local_root: str | os.PathLike | None = None) -> list[Result]:
    """Try the waiting renames again; each one done (or no longer wanted, or whose folder is gone) leaves the list."""
    out: list[Result] = []
    for x in pending():
        folder = Path(x["folder"])
        if not (folder / P.FILE).is_file():
            _set_pending(folder, drop=True)
            continue
        res = rename(folder, hub=hub, local_root=local_root, explicit=bool(x.get("explicit")))
        if res.state == WAIT:
            _set_pending(folder, res.text, bool(x.get("explicit")), new=res.folder)
        else:
            _set_pending(folder, drop=True)
        out.append(res)
    return out


def waiting_for(folder: str | os.PathLike) -> str:
    """Why the folder's rename waits ('' when it does not)."""
    k = _norm(folder)
    for x in pending():
        if _norm(x["folder"]) == k:
            return str(x.get("why") or "it waits")
    return ""


def note_premiere(xml: str | os.PathLike) -> None:
    """Open in Premiere on a project's run: remembered in its project.json ("premiere": {opened, computer, run}) --
    from then on a project saved anywhere may play its files, so the folder keeps its name."""
    run = Path(xml).parent
    proj = run.parent
    if not _RUN_RE.match(run.name) or not (proj / P.FILE).is_file():
        return
    try:
        from . import machine as M
        who = M.which()
    except Exception:  # noqa: BLE001
        who = ""
    try:
        P.load(proj).update(premiere={"opened": P.now(), "computer": who, "run": run.name})
    except (OSError, ValueError):
        pass
