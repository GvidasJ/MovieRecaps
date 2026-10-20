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
        if twin is None and to and hub is not None:   # renamed on the other computer since it was published here:
            try:                                      # the shared copy that has its old name among its old names
                moved = PB.renamed_copy(hub, data)
                if moved is not None and _norm(moved) != _norm(folder):
                    d = _read(moved)
                    if d is not None:
                        twin = (moved, d)
            except OSError:
                pass
    elif local_root is not None and _norm(local_root) != _norm(hub):
        names = {_fold_name(folder.name), *(_fold_name(str(x)) for x in (data.get("old_names") or []) if x)}
        cands = [local_root / folder.name]
        try:
            cands += [d for d in local_root.iterdir() if d.is_dir() and d.name != folder.name]
        except OSError:
            pass
        for c in cands:
            try:
                if (c / P.FILE).is_file():
                    d = _read(c)
                    pub = (d or {}).get("published") if isinstance((d or {}).get("published"), dict) else {}
                    to = str(pub.get("to") or "")
                    # linked: this computer's folder says it was published to this shared copy (now or under one
                    # of its old names) -- the same "created" second alone is not enough (a copy made in Explorer,
                    # two drops prepared at once)
                    if d is not None and to and _fold_name(Path(to).name) in names and PB.same_project(data, d):
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
            f = _plain_path(f)
            if f.lower().endswith(".prproj"):
                out.append(Path(f))
    return out


def _plain_path(text: str) -> str:
    """A path as Premiere writes it in XML: its &amp; ... read back, the long-path prefix off (\\\\?\\UNC\\server\\x ->
    \\\\server\\x, \\\\?\\D:\\x -> D:\\x)."""
    import html
    f = html.unescape(str(text or "")).strip()
    if f[:8].upper() == "\\\\?\\UNC\\":
        return "\\\\" + f[8:]
    if f.startswith("\\\\?\\"):
        return f[4:]
    return f


def _fold_name(name: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", name or "").casefold()


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
                for part in re.split(r"[\\/]", _plain_path(m.group(0)))[:-1]:
                    if part:
                        names.add(_fold_name(part))
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
        name = _fold_name(folder.name)
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


COPIES = "copies"                   # project.json "names": {"copies": ["edit/<name>_edit.xml", ...]}: the XML copies
#                                     the app wrote -- the only ones a rename ever removes


def remember_copy(run: str | os.PathLike, name: str) -> None:
    """The app wrote the XML copy ``name`` in this run folder of a title-named project: on its list (never raises)."""
    run = Path(run)
    key = f"{run.name}/{name}"
    try:
        p = P.load(run.parent)
    except (OSError, ValueError):
        return
    n = p.data.get(NM.KEY)
    if not isinstance(n, dict) or key.lower() in [str(x).lower() for x in (n.get(COPIES) or [])]:
        return

    def apply(d: dict) -> None:
        m = d.get(NM.KEY)
        if isinstance(m, dict):
            c = [str(x) for x in (m.get(COPIES) or [])]
            if key.lower() not in [x.lower() for x in c]:
                m[COPIES] = (c + [key])[-50:]
    try:
        p.change(apply)
    except (OSError, ValueError):
        pass


def _forget_copy(proj: Path, key: str) -> None:
    def apply(d: dict) -> None:
        m = d.get(NM.KEY)
        if isinstance(m, dict) and m.get(COPIES):
            m[COPIES] = [x for x in m[COPIES] if str(x).lower() != key.lower()]
    try:
        P.load(proj).change(apply)
    except (OSError, ValueError):
        pass


def app_copy_names(run: Path, data: dict | None) -> set[str]:
    """The XML file names (lower case) in this run folder that the app wrote: the ones on the project's list, and
    night 10's own copy names (<project>_<run>.xml -- also under the project's old names -- and <run>_edit.xml)."""
    d = data or {}
    n = d.get(NM.KEY) if isinstance(d.get(NM.KEY), dict) else {}
    out = {str(x).split("/", 1)[1].lower() for x in (n.get(COPIES) or [])
           if "/" in str(x) and str(x).split("/", 1)[0].lower() == run.name.lower()}
    for o in [run.parent.name, str(d.get("name") or ""), *[str(x) for x in (d.get("old_names") or [])]]:
        if o:
            out.add(f"{o}_{run.name}.xml".lower())
    out.add(f"{run.name}_edit.xml".lower())
    return out


def sync_run_files(run: Path, data: dict | None) -> list[str]:
    """The app's files in a run folder follow the project's names (a project whose names follow its title): its XML
    copy is ``<title>_edit.xml`` (made now when missing, the sequence called after the title); an older copy THE APP
    WROTE (``app_copy_names``, and still the same edit: 1_edit.xml with another sequence name) is removed, with its
    empty Premiere project renamed to ``<title>.prproj`` -- both stay when you saved that project (it holds the edit).
    1_edit.xml and your own files (a backup of the XML, any other name) are never touched. Returns what was done."""
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
    ours = app_copy_names(run, _read(run.parent) or data)
    new_proj = run / NM.project_name(out)
    for f in sorted(run.iterdir()):
        if f.suffix.lower() != ".xml" or f.name.lower() in ("1_edit.xml", fname.lower()) or not f.is_file():
            continue
        if f.name.lower() not in ours:
            continue                                  # not a copy the app wrote: yours, never touched
        try:
            text = f.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _same_edit(text, base):
            continue                                  # (changed since: not the app's copy of this edit any more)
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
            _forget_copy(run.parent, f"{run.name}/{f.name}")
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
    cps = copies(folder, data, hub, local_root)
    same = merged_titles(cps, folder)                 # (each title: the copy that changed it wins)
    d = {**titles(folder, data), **same}
    t, src = NM.title_of(d)
    if not t:
        return Result(NO_TITLE, folder, "It has no title yet, so it keeps its name")
    base = NM.folder_base(t, NM.day_of(folder.name) or None, NM.root_len(folder.parent))
    mark = {**(data.get(NM.KEY) if isinstance(data.get(NM.KEY), dict) else NM.marker(src)), "rule": NM.RULE,
            "from": src}
    mark.pop("kept", None)
    todo = [(c, dd, sh) for c, dd, sh in cps if not NM.has_base(c.name, base)]
    used = next((w for w in (in_use(c, dd, busy, hub) for c, dd, _sh in cps) if w), "")
    if not todo:
        if explicit and not NM.uses_titles(data):
            for c, _dd, _sh in cps:
                P.load(c).update(**{NM.KEY: mark})
        files: list[str] = []
        if not used:                                  # (nothing is touched while something uses it)
            _share_titles(cps, same)
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
                _share_titles(cps, same)
                files = [f for c2, _d2, _s2 in cps for r in run_dirs(c2) for f in sync_run_files(r, _read(c2))]
            return Result(KEPT, folder, f"It keeps its name: {why}", files=files)
    _share_titles(cps, same)                          # both copies hold the same titles before they are renamed
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
    shared_c = next((c for c, _dd, sh in cps if sh), None)
    shared_now = next((n for o, n in moved if shared_c is not None and _norm(o) == _norm(shared_c)), shared_c)
    for c, _dd, sh in cps:
        now_at = next((n for o, n in moved if _norm(o) == _norm(c)), c)
        _fix_json(now_at, moved, shared_pair, mark, shared_now)
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


def _fix_json(folder: Path, moved: list[tuple[Path, Path]], shared_pair: tuple[Path, Path] | None,
              mark: dict, shared_now: Path | None = None) -> None:
    """project.json of a copy after the move: its name and old names, the steps' run folders, published.to (the
    shared copy's name now -- written as that computer sees the shared folder; ``shared_now``: the shared copy, also
    when the other computer renamed it before), the names record (rule and source; its copies list and synced titles
    kept, a "kept" note dropped)."""
    from . import publish as PB

    def apply(d: dict) -> None:
        for old, new in moved:
            if _norm(new) == _norm(folder):
                d["name"] = new.name
                olds = [str(x) for x in (d.get("old_names") or []) if x]
                if old.name not in olds:
                    olds.append(old.name)
                d["old_names"] = olds
                PB._moved_paths(d, old, new)                                       # noqa: SLF001
        pub = d.get("published")
        if isinstance(pub, dict) and pub.get("to"):
            to = Path(str(pub["to"]))
            if shared_pair is not None and to.name.lower() == shared_pair[0].name.lower():
                pub["to"] = str(to.parent / shared_pair[1].name)
            elif shared_now is not None and _norm(shared_now) != _norm(folder) and \
                    to.name.lower() != shared_now.name.lower():
                pub["to"] = str(to.parent / shared_now.name)
        n = dict(d.get(NM.KEY)) if isinstance(d.get(NM.KEY), dict) else {}
        n.update(rule=NM.RULE, **({"from": mark["from"]} if mark.get("from") is not None else {}))
        n.setdefault("at", mark.get("at") or P.now())
        n.pop("kept", None)
        d[NM.KEY] = n
    try:
        P.load(folder).change(apply)
    except (OSError, ValueError):
        pass


SYNCED = "synced"                   # project.json "names": {"synced": {"title", "upload_title", "at"}}: the titles both
#                                     copies held when they were last made the same -- the base a change is seen from


def merged_titles(cps: list[tuple[Path, dict, bool]], asked: Path | None = None) -> dict:
    """The headline and the YouTube title both copies should hold, per title (the board's view: a title.txt you
    edited by hand counts):

    * the copies agree: that;
    * one copy changed it since they were last made the same (its "synced" base) -- also a title cleared to empty:
      that copy's;
    * both changed it: the copy whose title was just changed (``asked``), else the one synced last;
    * neither changed it (one was changed on the other computer and synced with its own folder there): the copy
      synced last;
    * a copy without a base (never synced yet): publishing's rule for that title -- set on one copy only: kept; set
      differently on both: the copy whose title.txt changed last."""
    from . import publish as PB
    rows = []
    for c, dd, _sh in cps:
        n = dd.get(NM.KEY) if isinstance(dd.get(NM.KEY), dict) else {}
        base = n.get(SYNCED) if isinstance(n.get(SYNCED), dict) else None
        try:
            txt = os.stat(c / PB.TITLE_TXT).st_mtime
        except OSError:
            txt = 0.0
        rows.append({"t": titles(c, dd), "base": base, "at": _ts((base or {}).get("at")) or 0.0, "txt": txt,
                     "asked": asked is not None and _norm(c) == _norm(asked)})
    out: dict = {}
    for k in ("title", "upload_title"):
        vals = [(r, str(r["t"].get(k) or "")) for r in rows]
        if len({v for _r, v in vals}) <= 1:
            out[k] = vals[0][1] if vals else ""
            continue
        known = [(r, v) for r, v in vals if r["base"] is not None]
        changed = [(r, v) for r, v in known if v != str(r["base"].get(k) or "")]
        if any(r["base"] is None for r, _v in vals):  # never synced: publishing's rule, per title
            have = [(r, v) for r, v in vals if v.strip()]
            if len({v for _r, v in have}) == 1:
                out[k] = have[0][1]                   # set on one copy only (or alike): kept
            else:
                out[k] = max(have or vals, key=lambda x: (x[0]["txt"], x[0]["asked"]))[1]
        elif len(changed) == 1:
            out[k] = changed[0][1]
        elif changed:
            mine = [x for x in changed if x[0]["asked"]]
            out[k] = (mine[0] if mine else max(changed, key=lambda x: x[0]["at"]))[1]
        else:
            out[k] = max(vals, key=lambda x: x[0]["at"])[1]
    return out


def _share_titles(cps: list[tuple[Path, dict, bool]], same: dict) -> None:
    """Every copy holds the merged titles in its project.json (a title.txt edited by hand taken in first: the names
    follow what project.json holds), its title.txt says them, and both remember them as their new base (``SYNCED``).
    Nothing is written to a copy that has them all already."""
    from . import publish as PB
    stamp = P.now()
    for c, _dd, _sh in cps:
        def apply(d: dict, c=c) -> None:
            PB.fold_title_txt(c, d)
            changed = False
            for k, v in same.items():
                if str(d.get(k) or "") != str(v or ""):
                    d[k] = v if v else (None if d.get(k) is None else "")
                    changed = True
            n = d.get(NM.KEY)
            if isinstance(n, dict):
                n[SYNCED] = {"title": same.get("title") or "", "upload_title": same.get("upload_title") or "",
                             "at": stamp}
            if changed:
                try:
                    PB.write_title_txt(c, d)
                except OSError:
                    pass
        try:
            cur = _read(c) or {}
            n = cur.get(NM.KEY) if isinstance(cur.get(NM.KEY), dict) else {}
            base = n.get(SYNCED) if isinstance(n.get(SYNCED), dict) else {}
            raw_same = all(str(cur.get(k) or "") == str(v or "") for k, v in same.items())
            view_same = all(str(titles(c, cur).get(k) or "") == str(v or "") for k, v in same.items())
            base_same = all(str(base.get(k) or "") == str(v or "") for k, v in same.items())
            if not (raw_same and view_same and base_same):
                P.load(c).change(apply)
        except (OSError, ValueError):
            pass


def _replace_paths(text: str, pairs: Iterable[tuple[str, str]]) -> str:
    """Every path inside an old folder (the folder itself at a line's end, or followed by \\ or /) -> the new folder.
    Never a longer name that starts like it: 'The Truth (2)', 'The Truth About', "The dog's revenge" (names have
    spaces and apostrophes now)."""
    for old, new in pairs:
        if old and old in text:
            text = re.sub(re.escape(old) + r"(?=[\\/\r]|$)", lambda _m, n=new: n, text, flags=re.M)
    return text


def _fix_outside(moved: list[tuple[Path, Path]], hub: Path | None, local_root: Path | None) -> None:
    """What points into the moved folders: the Learn waiting cards, the projects waiting for the shared folder,
    problems_queue.md's paths."""
    pairs = [(str(o), str(n)) for o, n in moved]
    if hub is not None:
        try:
            from . import waiting
            files = {}
            for o, n in moved:
                d = _read(n) or {}
                files[str(o)] = tuple(str(((d.get(r) or {}) if isinstance(d.get(r), dict) else {}).get("file") or "")
                                      for r in ("raw", "competitor"))
            waiting.relink(hub, pairs, files)
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
            try:
                from . import publish as PB
                gone = PB.reachable(folder.parent, 3.0) and not folder.exists()
            except Exception:  # noqa: BLE001
                gone = False
            if gone:                                  # really gone (renamed or removed meanwhile): off the list
                _set_pending(folder, drop=True)
            continue                                  # (else the shared folder does not answer now: kept)
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
