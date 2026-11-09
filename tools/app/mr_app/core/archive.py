"""7 Nov (your call): A FINISHED PROJECT BECOMES ONE ZIP, and it keeps only the scene of the RAW.

In the "Save projects to" folder on this computer (the PC's D:\\Shared\\Projects -- the laptop leaves it to the PC: a
network folder would be slow), in the background, one project at a time, and never while this computer makes a video
(an edit, a New video step or Topaz -- ``stop`` says so between two projects):

1. **Only the scene of the RAW** (Settings -> Projects -> "Keep only the scene", on): the finished edit plays the
   RAW's piece around its clips, +- SCENE_MARGIN_S (match_cuts.keep_piece: the very packets, cut by stream copy, every
   clip checked against the movie before anything changes); then the whole movie, a downloaded RAW and an older piece
   no edit plays leave the folder, and so do the two check videos (preview_recreation.mp4, compare.mp4). A RAW
   shorter than KEEP_MIN_RAW_S (the piece made for Topaz: your enhanced footage) stays as it is.
2. **Its zip** (core.share_zip) is next to the folders: D:\\Shared\\Projects\\<name>.zip -- the one to send.
3. **One zip per project** (Settings -> Projects -> "One zip per project", on): when its Short is uploaded (the
   board's upload check) or ARCHIVE_AFTER_DAYS after its edit, the folder goes and only the zip stays: the zip is
   made again first when anything changed, then checked (every file of the folder is in it at the same size, and
   every file reads back), the project's line goes into zipped.json (the motivation strip still counts its upload),
   and only then is the folder moved aside and deleted. A file the zip does not hold (another zip, a .rar ...) keeps
   the folder as it is.

Never touched: a project whose edit is not finished (or failed -- unless its Short is uploaded: you fixed it by hand;
it becomes its zip, nothing is cut), one a row of the app works on, one another computer holds (its lock), one
Premiere has open (renames.in_use), one whose enhanced piece is still with Topaz. A project still in the works is never
changed: a new edit plays only its scene from the start (core.run.scene_env), an older one is slimmed when it is
finished for good (``due``), right before it becomes its zip.
Everything here reads disks and runs ffmpeg: call it from a worker thread."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import projects as P

SCENE_MARGIN_S = 10.0              # the scene +- this (your choice, 7 Nov)
KEEP_MIN_RAW_S = 120.0             # a shorter RAW (the piece made for Topaz) stays whole
KEEP_SHARE = 0.95                  # the piece may be up to this share of the RAW
ARCHIVE_AFTER_DAYS = 20 / 1440      # 9 Nov (your call: "it only needs to leave the zip"): the folder becomes its zip
                                    #   20 min after its edit is finished (was 7 days) -- once the zip holds everything
RESTORED_DAYS = 2.0                 # a project you unzipped again is left as a folder this long
INDEX = "zipped.json"              # the projects that are only a zip now (in the Save folder)
TRASH = ".zipped_trash"            # a folder is moved here first, then deleted (a left-over is deleted next time)
CHECK_VIDEOS = ("preview_recreation.mp4", "compare.mp4")
DEBUG_CHECK = "recreation_check.mp4"
GOOD = ("PASS", "OK with a note")
_LOCK = threading.Lock()


def _say(lines: list[str], text: str) -> None:
    lines.append(text)


def _runs(folder: Path) -> list[Path]:
    """The project's edit folders (edit, edit-2 ..., cuts): those with a 1_edit.xml."""
    return sorted(x.parent for x in folder.glob("*/1_edit.xml"))


def _xml_names(folder: Path) -> tuple[set[str], set]:
    """(the file names every XML of the project plays, their (device, inode) keys) -- 1_edit.xml and every other
    .xml of its edit folders (the planning copies, 1_edit_normal.xml, a copy named after the title)."""
    from .share_zip import _key, reach, xml_media
    names: set[str] = set()
    keys: set = set()
    for run in _runs(folder):
        for x in list(run.glob("*.xml")) + list((run / "extras").glob("*.xml")):
            for m in xml_media(x):
                names.add(m.name.lower())
                k = _key(reach(m))
                if k is not None:
                    keys.add(k)
    return names, keys


# ---------------------------------------------------------------------------------------------------------------------
# 1. only the scene
# ---------------------------------------------------------------------------------------------------------------------

def _keep_scene(run: Path, lines: list[str]) -> dict:
    """match_cuts.keep_piece on one finished edit folder (the scene +- SCENE_MARGIN_S), and the whole movie it played
    removed when nothing plays it any more. A copy of 1_edit.xml named after the title follows the new one."""
    info = run / "extras" / "keep_piece.json"
    if info.is_file():
        return {"kept": True, "why": "the edit plays only its scene already"}
    final = run / "1_edit.xml"
    try:
        before = final.read_bytes()
    except OSError:
        return {"kept": False, "why": "no 1_edit.xml"}
    from . import learnkit
    learnkit.tool()                                        # match_cuts importable (the app's own copy of the tool)
    from match_cuts import keep_piece as KP
    movie = KP._media_of(final, "file-raw")
    out = KP.keep_run(run, [], margin_s=SCENE_MARGIN_S, share=KEEP_SHARE, min_raw_s=KEEP_MIN_RAW_S)
    if not out.get("kept"):
        return out
    for x in run.glob("*.xml"):                            # a copy of the old 1_edit.xml (the title's name)
        if x.name != final.name:
            try:
                if x.read_bytes() == before:
                    shutil.copyfile(final, x)
            except OSError:
                pass
    if movie is not None and movie.is_file():
        names, keys = _xml_names(run.parent)
        from .share_zip import _key
        if movie.name.lower() not in names and _key(movie) not in keys:
            size = movie.stat().st_size
            try:
                movie.unlink()
                _say(lines, f"the whole movie left the edit ({movie.name}, {size / 1024 ** 2:.0f} MB)")
            except OSError as e:
                _say(lines, f"{movie.name} could not be removed: {e}")
    if out.get("line"):
        _say(lines, out["line"])
    return out


def drop_check_videos(folder: Path) -> int:
    """The two check videos of every edit (and the framed run's competitor-timed one): bytes freed."""
    freed = 0
    for run in _runs(folder):
        for f in [run / "extras" / n for n in CHECK_VIDEOS] + [run / "extras" / "debug" / DEBUG_CHECK]:
            try:
                if f.is_file():
                    n = f.stat().st_size
                    f.unlink()
                    freed += n
            except OSError:
                pass
    return freed


def drop_unplayed(folder: Path, data: dict) -> list[str]:
    """The big videos outside the edit folders that no edit plays -- the downloaded RAW, its conversion, an older
    piece -- leave the folder (never the competitor, never a file of an edit folder, never a hard link of a file an
    edit plays: it costs nothing). project.json says what went. Returns their names."""
    from .share_zip import BIG_BYTES, VIDEO_EXT, _key
    names, keys = _xml_names(folder)
    if not names:
        return []                                         # no edit read: nothing to compare with
    runs = {r.name.lower() for r in _runs(folder)}
    comp = str(((data.get("competitor") or {}) if isinstance(data.get("competitor"), dict) else {}).get("file") or "")
    gone: list[str] = []
    for f in [*folder.iterdir(), *((folder / "enhance").iterdir() if (folder / "enhance").is_dir() else [])]:
        try:
            if not f.is_file() or f.suffix.lower() not in VIDEO_EXT or f.name.lower() in names:
                continue
            if f.parent == folder and f.name == comp:
                continue
            if f.relative_to(folder).parts[0].lower() in runs:
                continue
            if _key(f) in keys or f.stat().st_size <= BIG_BYTES:
                continue
            f.unlink()
            gone.append(f.name)
        except OSError:
            continue
    if gone:
        try:
            raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
            P.load(folder).update(raw={"left_folder": P.now(), "removed": sorted(set(gone) | set(raw.get("removed")
                                                                                              or []))})
        except Exception:  # noqa: BLE001
            pass
    return gone


def slim(folder: Path, data: dict) -> list[str]:
    """Step 1 on one finished project (module text). Returns what was done, in plain words."""
    lines: list[str] = []
    for run in _runs(folder):
        try:
            r = _keep_scene(run, lines)
            if not r.get("kept") and r.get("why"):
                _say(lines, f"{run.name}: the RAW stays whole ({r['why']})")
        except Exception as e:  # noqa: BLE001 - nothing changed for that edit
            _say(lines, f"{run.name}: the RAW stays whole ({type(e).__name__}: {e})")
    freed = drop_check_videos(folder)
    if freed:
        _say(lines, f"the check videos went ({freed / 1024 ** 2:.0f} MB)")
    gone = drop_unplayed(folder, data)
    if gone:
        _say(lines, f"{', '.join(gone)} left the folder (no edit plays it)")
    try:
        P.load(folder).update(slim={"at": P.now(), "margin_s": SCENE_MARGIN_S, "lines": lines[:12]})
    except Exception:  # noqa: BLE001
        pass
    return lines


# ---------------------------------------------------------------------------------------------------------------------
# 3. one zip per project
# ---------------------------------------------------------------------------------------------------------------------

def index_path(root: Path) -> Path:
    return Path(root) / INDEX


def read_index(root: Path) -> list[dict]:
    try:
        d = json.loads(index_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("projects") if isinstance(d, dict) else None
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _add_index(root: Path, entry: dict) -> None:
    with _LOCK:
        items = [x for x in read_index(root) if x.get("folder") != entry.get("folder")] + [entry]
        p = index_path(root)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": 1, "projects": items}, indent=1, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)


def zipped_uploads(root: str | os.PathLike | None) -> list[tuple[datetime, str]]:
    """(when, channel) of every Short whose project is only a zip now -- the motivation strip counts them too."""
    if not root:
        return []
    out = []
    for x in read_index(Path(root)):
        try:
            t = datetime.fromisoformat(str(x.get("uploaded_at"))) if x.get("uploaded_at") else None
        except ValueError:
            t = None
        if t is not None:
            out.append((t if t.tzinfo else t.astimezone(), str(x.get("channel") or "")))
    return out


def zip_of_gone(folder: str | os.PathLike | None) -> Path | None:
    """The zip a project folder became (None while the folder is there, or when there is no zip)."""
    if folder is None:
        return None
    folder = Path(folder)
    try:
        if folder.exists():
            return None
        root = folder.parent
        for x in read_index(root):
            if x.get("folder") == folder.name and x.get("zip") and (root / str(x["zip"])).is_file():
                return root / str(x["zip"])
        from .share_zip import plain_name
        z = root / f"{plain_name(folder)}.zip"
        return z if z.is_file() else None
    except OSError:
        return None


def reveal_gone(folder: str | os.PathLike | None) -> str:
    """A project folder that became its zip: the zip shown in Explorer; what to say ('' = no such zip)."""
    z = zip_of_gone(folder)
    if z is None:
        return ""
    try:
        import subprocess
        subprocess.Popen(["explorer", "/select,", str(z)])  # noqa: S603,S607 - Windows' own Explorer
    except OSError:
        pass
    return f"This project is one zip now ({z.name}): unzip it, then open edit\\1_edit.xml in Premiere"


def _disposable(f: Path, folder: Path) -> bool:
    """A file the zip may leave out and the folder may lose: the old zip made inside it, a half-written file, a lock,
    Windows' own little files, a big video no edit plays (your choice: only the scene stays)."""
    from . import share_zip as Z
    n = f.name.lower()
    if f.parent == folder and f.name == Z.HOW_TO:
        return True                                        # (an unzipped project's: the zip has a new one)
    if n in Z.NEVER_NAMES or n.endswith(".zip.tmp") or ".part" in f.suffixes or f.suffix.lower() in (".tmp",
                                                                                                    ".part"):
        return True
    if f.suffix.lower() == ".zip" and f.parent == folder:
        try:
            with zipfile.ZipFile(f) as zf:
                return zf.comment == Z.MARK                 # the project's own (older) zip
        except (OSError, zipfile.BadZipFile):
            return False
    return False


def check_zip(folder: Path, z: Path, run: Path | None = None, leave_unplayed: bool = True) -> str:
    """'' when ``z`` holds every file of the folder (the same size) and every member reads back; else why not.
    ``leave_unplayed``: a big video no edit plays may stay out of it (only the scene is kept)."""
    from . import share_zip as Z
    try:
        put, left = Z.members(folder, run, leave_unplayed)
    except OSError as e:
        return f"the folder cannot be read ({e})"
    unplayed = {f for f, why in left if "does not play" in why}
    linked = {f for f, why in left if "hard link" in why}
    want = {arc: f for f, arc in put}
    wanted = set(want.values())
    light = Z.zip_info(z).get("files") or {}
    try:
        with zipfile.ZipFile(z) as zf:
            have = {i.filename: i for i in zf.infolist()}
            for arc, f in want.items():
                i = have.get(arc)
                if i is None:
                    return f"{f.name} is not in the zip"
                if i.file_size != f.stat().st_size and \
                        (light.get(arc) or {}).get("from_bytes") != f.stat().st_size:   # (7 Nov: a lighter copy)
                    return f"{f.name} changed after the zip was made"
            bad = zf.testzip()
            if bad is not None:
                return f"{bad} cannot be read back from the zip"
    except (OSError, zipfile.BadZipFile) as e:
        return f"the zip cannot be read ({e})"
    for dirpath, _dirs, files in os.walk(folder):
        for n in files:
            f = Path(dirpath) / n
            if f in wanted or f in unplayed or f in linked or _disposable(f, folder):
                continue
            return f"{f.relative_to(folder)} would be lost (it is not in the zip)"
    return ""


def archive(folder: Path, data: dict, root: Path, scene_only: bool = True) -> str:
    """Step 3: the folder becomes its zip (``scene_only`` off: with every video in it, the whole RAW too). Returns what
    happened in plain words; raises nothing."""
    from . import board as B
    from . import share_zip as Z
    run = Z.run_dir_of(folder, (data.get("steps") or {}).get("edit") or {})
    leave = bool(scene_only)
    try:
        z = Z.zip_path(folder)
        why = check_zip(folder, z, run, leave) if z.is_file() and not Z.needs_zip(folder, run, leave) else \
            "it has no zip yet"
        if why and ("no zip" in why or "changed after" in why or "is not in the zip" in why):
            if Z.make(folder, run, leave) is None:         # made again: older, or project.json changed since
                return f"{folder.name}: kept as a folder (its zip could not be made -- room?)"
            z = Z.zip_path(folder)
            why = check_zip(folder, z, run, leave)
        if why:
            return f"{folder.name}: kept as a folder ({why})"
        col, _a, _m, dates = B.place(data)
        up = dates.get("uploaded") if col == "uploaded" else None
        ed = (data.get("steps") or {}).get("edit") or {}
        trash = root / TRASH
        trash.mkdir(exist_ok=True)
        dest = trash / f"{folder.name}.{int(time.time())}"
        try:
            os.replace(folder, dest)                       # Windows says no while Premiere holds a file in it
        except OSError as e:
            return f"{folder.name}: kept as a folder for now (in use: {e.strerror or e})"
        _add_index(root, {"folder": folder.name, "zip": z.name, "zipped_at": P.now(),
                          "channel": str(data.get("channel") or ""), "title": str(data.get("title") or ""),
                          "upload_title": str(data.get("upload_title") or ""), "created": data.get("created"),
                          "edit": {"result": ed.get("result"), "finished": ed.get("finished")},
                          "uploaded_at": up.isoformat(timespec="seconds") if up is not None else None,
                          "bytes": z.stat().st_size})
        shutil.rmtree(dest, ignore_errors=True)
        _empty_trash(root)
        return f"{folder.name}: now only {z.name} ({z.stat().st_size / 1024 ** 3:.2f} GB)"
    except Exception as e:  # noqa: BLE001 - the folder stays as it is
        return f"{folder.name}: kept as a folder ({type(e).__name__}: {e})"


def _empty_trash(root: Path) -> None:
    t = root / TRASH
    if t.is_dir():
        for d in t.iterdir():
            shutil.rmtree(d, ignore_errors=True)
        try:
            t.rmdir()                                      # (only when it is empty)
        except OSError:
            pass


def log(root: str | os.PathLike, lines: list[str]) -> None:
    """What a round did, in this computer's archive.log (%APPDATA%\\MovieRecaps) -- the Save folder keeps only zips."""
    try:
        from . import settings as S
        p = S.app_dir() / "archive.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("".join(f"{P.now()}  {x}\n" for x in lines))
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------------------------------
# the round
# ---------------------------------------------------------------------------------------------------------------------

def _finished(data: dict) -> datetime | None:
    ed = (data.get("steps") or {}).get("edit") if isinstance(data.get("steps"), dict) else None
    if not isinstance(ed, dict) or ed.get("state") != P.DONE or ed.get("result") not in GOOD:
        return None
    try:
        t = datetime.fromisoformat(str(ed.get("finished") or ed.get("started") or ""))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def _uploaded(data: dict) -> bool:
    b = data.get("board") if isinstance(data.get("board"), dict) else {}
    return isinstance(b.get("uploaded"), dict)


def _has_edit(data: dict, folder: Path) -> bool:
    """An edit was made (also a FAIL whose 1_edit.xml is there: you may have fixed it by hand and uploaded it)."""
    ed = (data.get("steps") or {}).get("edit") if isinstance(data.get("steps"), dict) else None
    if not isinstance(ed, dict) or ed.get("state") not in (P.DONE, P.FAILED):
        return False
    from .share_zip import run_dir_of
    return (run_dir_of(folder, ed) / "1_edit.xml").is_file()


def why_not(folder: Path, data: dict) -> str:
    """Why the project is left alone now ('' = it may be tidied)."""
    if _finished(data) is None and not (_uploaded(data) and _has_edit(data, folder)):
        return "its edit is not finished"
    rec = data.get("enhance") if isinstance(data.get("enhance"), dict) else {}
    if rec.get("state") == "sent":
        return "its piece is with Topaz"
    try:
        from . import renames
        return renames.in_use(folder, data)
    except Exception:  # noqa: BLE001
        return ""


def _restored_lately(folder: Path, now: datetime, days: float) -> bool:
    """A project that was only its zip and is a folder again (you unzipped it there): unzipped, or a file of it
    changed, less than ``days`` ago -- it is not zipped away again under your hands."""
    try:
        newest = folder.stat().st_ctime                    # (Windows: when the folder was made -- unzipped)
        for dirpath, _dirs, files in os.walk(folder):
            for n in files:
                newest = max(newest, os.stat(os.path.join(dirpath, n)).st_mtime)
    except OSError:
        return True
    return now.timestamp() - newest < days * 86400


def due(data: dict, now: datetime, days: float = ARCHIVE_AFTER_DAYS) -> bool:
    """Its Short is uploaded, or its edit is ``days`` old: the project is finished for good."""
    fin = _finished(data)
    return _uploaded(data) or (fin is not None and now - fin >= timedelta(days=days))


def tidy(root: str | os.PathLike, *, stop: Callable[[], bool] | None = None, scene_only: bool = True,
         one_zip: bool = True, now: datetime | None = None, days: float = ARCHIVE_AFTER_DAYS,
         budget_s: float = 8 * 60.0) -> list[str]:
    """One round over the Save folder's projects, oldest first (module text); ``stop()`` is asked between two
    projects, the round ends after ``budget_s`` (the next round goes on). A project still in the works is never
    changed -- only its zip to send is made (again, when its files changed); one made before 7 Nov keeps its zip
    inside the folder until then. A new edit plays only its scene from the start (core.run.scene_env); an older one
    is slimmed once it is finished for good (``due``). Returns what was done."""
    root = Path(root)
    stop = stop or (lambda: False)
    now = now or datetime.now().astimezone()
    t0 = time.monotonic()
    out: list[str] = []
    from . import share_zip as Z
    _empty_trash(root)
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and (p / P.FILE).is_file())
    except OSError:
        return out
    zipped_before = {str(x.get("folder")) for x in read_index(root)}
    for folder in folders:
        if stop() or time.monotonic() - t0 > budget_s:
            break
        try:
            data = P.read_json(folder / P.FILE)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or why_not(folder, data):
            continue
        run = Z.run_dir_of(folder, (data.get("steps") or {}).get("edit") or {})
        if folder.name in zipped_before and _restored_lately(folder, now, RESTORED_DAYS):
            continue                                      # you unzipped it to work on it: left alone for a while
        if not due(data, now, days):
            if Z.old_zip_path(folder).is_file() and not Z.zip_path(folder).is_file():
                continue                                  # made before 7 Nov: its zip inside stays until then
            if Z.needs_zip(folder, run):
                z = Z.make(folder, run)
                if z is not None:
                    out.append(f"{folder.name}: zip made to send ({z.name})")
            continue
        if scene_only and _finished(data) is not None and not data.get("slim"):
            out += [f"{folder.name}: {x}" for x in slim(folder, data)]
            try:
                data = P.read_json(folder / P.FILE)
            except (OSError, ValueError):
                continue
            if stop():
                break
        if one_zip:
            out.append(archive(folder, data, root, scene_only))
        elif Z.needs_zip(folder, run):
            z = Z.make(folder, run)
            if z is not None:
                out.append(f"{folder.name}: zip made ({z.name})")
    return out
