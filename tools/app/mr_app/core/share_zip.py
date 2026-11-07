"""A ZIP OF EVERY FINISHED PROJECT, to send to your friend (night 15, task 7; day 16, task 2: the WHOLE folder).

When a project's edit is done (flow.record_edit, in the background) it gets ``<project folder name>.zip`` (without
emoji) -- 7 Nov: NEXT TO its folder (D:\\Shared\\Projects\\<name>.zip: the one file that stays when the folder goes,
core.archive) -- made again when its files change later (Open in Premiere saved its .prproj); the PC's round at the
app's start (and every 10 minutes, core.archive.tidy) does the projects made before, and remakes a zip of the older
kind (night 15's had only the edit files; one inside the folder goes once the new one is made).

In it, under one folder named after the project (without emoji), **everything in the project folder with the same
folders inside**: ``edit\\`` (1_edit.xml, the captions, extras\\media -- the RAW or its piece, the WAV, the frame ...),
``logs\\``, the competitor clip, project.json, template.png, title.txt, titles_found.txt ... and ``HOW TO OPEN.txt``.

* Videos, sounds and pictures are STORED (not compressed again: fast and lossless); text is compressed.
* Never in it: a zip (itself, an older one, any .zip / .7z / .rar), a half-written file (.tmp / .part / .aria2), the
  work locks. One file that is the same file twice (a hard link: the RAW and edit\\extras\\media\\raw.mp4) goes in
  once -- the copy the edit plays -- and HOW TO OPEN says where the other one was.
* A file the XML plays from OUTSIDE the project folder: put in ``outside\\`` when it is small (under BIG_BYTES), else
  left out and said (on the shared drive the edit finds it there; elsewhere: send it yourself).
* The zip is not made when it would leave its drive under MIN_FREE free (the zip is about as big as the folder).
* Day 17: the zip is made only for a project on ANOTHER drive than Windows' own (D:\\Shared\\Projects, a network
  folder), never in input\\downloads on C: (5 automatic zips there filled 15.7 GB of C: in 1.5 hours).

Nothing is ever deleted or moved: the zip is written next to them (a temporary file renamed into place).
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

BIG_BYTES = 200 * 1024 ** 2          # a file from OUTSIDE the project this big is not put in (the movie, its WAV)
MIN_FREE = 40 * 1024 ** 3            # the drive keeps at least this much free after the zip
HOW_TO = "HOW TO OPEN.txt"
MARK = b"MovieRecaps project zip v2 (day 16): the whole project folder"
STORE = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".flv", ".wmv", ".mxf",
         ".wav", ".m4a", ".aac", ".mp3", ".flac", ".ogg", ".opus", ".png", ".jpg", ".jpeg", ".webp", ".gif",
         ".psd", ".zip", ".7z", ".gz", ".prproj"}       # (.prproj is gzip inside)
NEVER = {".zip", ".7z", ".rar", ".tmp", ".part", ".aria2", ".crdownload"}
NEVER_NAMES = {".movierecaps.lock", "project.json.lock", "thumbs.db", "desktop.ini"}
NO_TRIGGER = {"project.json"}        # changes often (the board's moves): no reason alone to make the zip again
STALE_TMP_S = 3600


def _path_of_url(u: str) -> Path | None:
    """file://host/Share/a%20b.mp4 -> \\\\host\\Share\\a b.mp4; file:///D:/x.mp4 -> D:\\x.mp4."""
    if not u:
        return None
    p = urllib.parse.urlparse(u)
    path = urllib.parse.unquote(p.path)
    if p.netloc and p.netloc.lower() != "localhost":
        return Path("//" + p.netloc + path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return Path(path) if path else None


def xml_media(xml: Path) -> list[Path]:
    """Every file the XML plays (its pathurls), once each, in order."""
    out: list[Path] = []
    try:
        root = ET.parse(xml).getroot()
    except (OSError, ET.ParseError):
        return out
    for el in root.iter("pathurl"):
        p = _path_of_url((el.text or "").strip())
        if p is not None and p not in out:
            out.append(p)
    return out


def reach(p: Path) -> Path:
    """The file as this computer reaches it: the PC's own \\\\192.168.101.11\\Shared\\... is its D:\\Shared\\...
    (learn_hub.for_pc) when the network path itself can not be read here."""
    try:
        if p.exists():
            return p
        from .learn_hub import for_pc
        q = Path(for_pc(p))
        return q if q.exists() else p
    except Exception:  # noqa: BLE001
        return p


def on_share(p: Path) -> bool:
    """A file your friend reaches over the network: a \\\\server\\share path, or the shared folder D:\\Shared."""
    s = str(p).replace("/", "\\").lower()
    return s.startswith("\\\\") or s.startswith("d:\\shared\\")


def plain_name(project: Path) -> str:
    """The zip's name and its folder's: the project's without emoji (Premiere's XML import fails in a folder named
    with emoji: names.plain)."""
    try:
        from .names import plain
        return plain(project.name) or "project"
    except Exception:  # noqa: BLE001
        return project.name


def zip_path(project: Path) -> Path:
    """7 Nov: the zip sits NEXT TO the project folder -- D:\\Shared\\Projects\\<name>.zip -- the one file that stays when
    the folder goes (core.archive). project.json's "zip" names it when the plain name was taken by another project."""
    project = Path(project)
    try:
        name = ((_project_data(project).get("zip") or {}).get("file") or "").strip()
    except Exception:  # noqa: BLE001
        name = ""
    return project.parent / (name or f"{plain_name(project)}.zip")


def old_zip_path(project: Path) -> Path:
    """Where the zip was before 7 Nov: inside the project folder."""
    return Path(project) / f"{plain_name(Path(project))}.zip"


def _project_data(project: Path) -> dict:
    from . import projects as P
    d = P.read_json(Path(project) / P.FILE, tries=2)
    return d if isinstance(d, dict) else {}


def _owner(z: Path) -> str:
    """The project folder a zip was made from (the first line of its HOW TO OPEN), '' when it cannot be read."""
    try:
        with zipfile.ZipFile(z) as zf:
            how = next((n for n in zf.namelist() if n.endswith("/" + HOW_TO)), None)
            return zf.read(how).decode("utf-8", "replace").splitlines()[0].strip() if how else ""
    except (OSError, zipfile.BadZipFile, IndexError, KeyError):
        return ""


def _free_name(project: Path) -> Path:
    """The zip's place next to the folder: <plain name>.zip, or (2), (3) ... when another project's zip has it."""
    base = plain_name(project)
    for k in range(1, 50):
        z = project.parent / (f"{base}.zip" if k == 1 else f"{base} ({k}).zip")
        if not z.exists() or _owner(z) in ("", project.name):
            return z
    return project.parent / f"{base} ({int(time.time())}).zip"


def _skipped(f: Path) -> bool:
    n = f.name.lower()
    return n in NEVER_NAMES or f.suffix.lower() in NEVER or n.endswith(".zip.tmp") or ".part" in f.suffixes


def _inside(p: Path, folder: Path) -> Path | None:
    """``p``'s path inside ``folder`` (None when it is not in it), compared as this computer reaches both."""
    for a in (p, reach(p)):
        try:
            return Path(os.path.abspath(a)).relative_to(Path(os.path.abspath(folder)))
        except ValueError:
            continue
    return None


def _key(f: Path):
    try:
        st = f.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".flv", ".wmv", ".mxf"}


def _unplayed_video(f: Path, rel: str, played_names: set, runs: set) -> bool:
    """7 Nov: a big video OUTSIDE the edit folders that no edit plays -- the downloaded RAW, its conversion, an older
    piece -- stays out of the zip (the PC now makes its projects right in D:\\Shared\\Projects: the RAW is in the folder
    too). Never a file of an edit folder (edit, edit-2, cuts ...: everything there goes in, as before), never a file
    whose name the edit plays, never when no edit could be read (nothing to compare with)."""
    if not played_names or f.suffix.lower() not in VIDEO_EXT:
        return False
    first = rel.split("/", 1)[0].lower()
    if "/" in rel and (first in runs or first.startswith(("edit", "cuts"))):
        return False
    if f.name.lower() in played_names:
        return False
    try:
        return f.stat().st_size > BIG_BYTES
    except OSError:
        return False


def _normal_picture(f: Path, played_names: set) -> bool:
    """7 Nov (your call: only the enhanced picture stays): in a project whose edit plays the Topaz picture, a big video
    the edit does not play -- edit\\extras\\media\\raw.mp4, the normal RAW's piece -- stays out of the zip, in the edit
    folders too."""
    if f.suffix.lower() not in VIDEO_EXT or f.name.lower() in played_names:
        return False
    try:
        return f.stat().st_size > BIG_BYTES
    except OSError:
        return False


def members(project: Path, run_dir: Path | None = None,
            leave_unplayed: bool = True) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """(the files to put in -- (file, name in the zip) --, the files left out -- (file, why)). ``leave_unplayed``
    (7 Nov): a big video outside the edit folders that no edit plays stays out (False: the zip a folder becomes when
    you keep the whole RAW -- Settings -> Projects -> Keep only the scene off)."""
    project = Path(project)
    top = plain_name(project)
    put: list[tuple[Path, str]] = []
    left: list[tuple[Path, str]] = []
    plays: list[Path] = []                       # what the edits play (their copy goes in first of two hard links)
    for xml in sorted(project.glob("edit*/1_edit.xml")) + ([Path(run_dir) / "1_edit.xml"] if run_dir else []):
        for m in xml_media(xml):
            if m not in plays:
                plays.append(m)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames.sort()
        for n in sorted(filenames):
            f = Path(dirpath) / n
            if not _skipped(f) and not (n == HOW_TO and Path(dirpath) == project):   # (an unzipped one's: made new)
                files.append(f)
    played = {os.path.normcase(os.path.abspath(reach(m))) for m in plays}
    files.sort(key=lambda f: (os.path.normcase(os.path.abspath(f)) not in played,))      # the played copy first
    played_names = {m.name.lower() for m in plays}
    runs = {x.parent.name.lower() for x in project.glob("*/1_edit.xml")} | ({Path(run_dir).name.lower()}
                                                                             if run_dir else set())
    try:
        rec = _project_data(project).get("enhance")
        enhanced = isinstance(rec, dict) and rec.get("state") == "used" and any(
            "enhanced" in n for n in played_names)
    except Exception:  # noqa: BLE001
        enhanced = False
    seen: dict = {}
    for f in files:
        k = _key(f)
        rel = f.relative_to(project).as_posix()
        if k is not None and k in seen:
            left.append((f, f"the same file as {seen[k]} (a hard link): copy that one here if you need it"))
            continue
        if leave_unplayed and enhanced and _normal_picture(f, played_names):
            left.append((f, "the normal picture: the edit plays the enhanced one (it stays in the project folder "
                            "while it is there)"))
            continue
        if leave_unplayed and _unplayed_video(f, rel, played_names, runs):
            left.append((f, "a video the edit does not play (the downloaded RAW, its conversion or an older piece): "
                            "it stays in the project folder"))
            continue
        if k is not None:
            seen[k] = rel
        put.append((f, f"{top}/{rel}"))
    names = {a.lower() for _f, a in put}
    for where in plays:                          # a file the edit plays from outside the project folder
        if _inside(where, project) is not None:
            continue
        m = reach(where)
        try:
            size = m.stat().st_size
        except OSError:
            left.append((where, "not found where the edit points"))
            continue
        if size > BIG_BYTES:
            left.append((where, "on the shared drive (the edit finds it there)" if on_share(where) else
                         f"too big to send in the zip ({size / 1024 ** 3:.1f} GB) -- send it yourself"))
            continue
        arc, k = f"{top}/outside/{m.name}", 2
        while arc.lower() in names:
            arc = f"{top}/outside/{m.stem}-{k}{m.suffix}"
            k += 1
        names.add(arc.lower())
        put.append((m, arc))
    return put, left


def how_to(project: Path, put: list[tuple[Path, str]], left: list[tuple[Path, str]],
           run_dir: Path | None = None) -> str:
    run = Path(run_dir).name if run_dir else "edit"          # (a Just cuts project's run folder is "cuts")
    lines = [f"{project.name}", "", "HOW TO OPEN", "",
             "1. Unzip this anywhere (it keeps the project's own folders: edit, logs ...).",
             f"2. In Premiere: File > Import, pick {run}\\1_edit.xml (or open a .prproj when there is one).",
             f"3. Every file the edit plays is in this folder ({run}\\extras\\media: the RAW or its piece, its sound,",
             "   the frame, the watermark, the song). When Premiere asks where one is, point it at that folder.", ""]
    if left:
        lines += ["LEFT OUT (not in this zip):"]
        for f, why in left:
            lines.append(f"  - {f.name}: {why}")
            lines.append(f"      {f}")
        lines.append("")
    lines += [f"IN THIS ZIP: {len(put)} files"] + [f"  - {arc.split('/', 1)[1]}" for _f, arc in put] + [""]
    return "\r\n".join(lines)


def _is_v2(z: Path) -> bool:
    try:
        with zipfile.ZipFile(z) as zf:
            return zf.comment == MARK
    except (OSError, zipfile.BadZipFile):
        return False


def needs_zip(project: Path, run_dir: Path, leave_unplayed: bool = True) -> bool:
    """No zip yet, a zip of the older kind (night 15: only the edit files), or a file changed after it was made
    (project.json alone does not count: the board changes it)."""
    z = zip_path(project)
    if not (Path(run_dir) / "1_edit.xml").is_file() or not drive_ok(project):
        return False
    if not z.is_file() or not _is_v2(z):
        return True
    t = z.stat().st_mtime
    put, _left = members(project, run_dir, leave_unplayed)
    try:                                         # 7 Nov: a file in the zip that should not be there any more (the
        want = {a for _f, a in put}               #   normal RAW, put in a moment before it left the folder)
        with zipfile.ZipFile(z) as zf:
            had = [i for i in zf.infolist() if not i.filename.endswith("/" + HOW_TO) and not i.is_dir()]
        if any(i.filename not in want and i.file_size > BIG_BYTES for i in had):
            return True
    except (OSError, zipfile.BadZipFile):
        return True
    for f, _a in put:
        if f.name.lower() in NO_TRIGGER and f.parent == Path(project):
            continue
        try:
            if f.stat().st_mtime > t:
                return True
        except OSError:
            continue
    return False


def drive_ok(project: Path) -> bool:
    """Day 17: a zip only for a project NOT on Windows' own drive (C:): D:\\Shared\\Projects, another drive or a
    \\\\server\\share. (MOVIERECAPS_ZIP_ANY_DRIVE=1 lets the app's tests zip their temp folders on C:.)"""
    if os.environ.get("MOVIERECAPS_ZIP_ANY_DRIVE") == "1":
        return True
    s = os.path.abspath(str(project)).replace("/", "\\")
    if s.startswith("\\\\"):
        return True
    drive = os.path.splitdrive(s)[0].upper()
    return bool(drive) and drive != (os.environ.get("SystemDrive") or "C:").upper()


def _free_after(folder: Path, need: int) -> int:
    try:
        return shutil.disk_usage(folder).free - need
    except OSError:
        return MIN_FREE


def _clean_tmp(z: Path) -> None:
    tmp = z.with_name(z.name + ".tmp")
    try:
        if tmp.is_file() and time.time() - tmp.stat().st_mtime > STALE_TMP_S:
            tmp.unlink()                                # a zip cut short (the app closed while it was written)
    except OSError:
        pass


_MAKING: set = set()
_MAKING_LOCK = threading.Lock()


def make(project: Path, run_dir: Path | None = None, leave_unplayed: bool = True) -> Path | None:
    """The project's zip (written whole); None without a finished edit or without the space -- or while another
    thread makes the same one. 7 Nov: next to the folder (zip_path); the older one inside the folder then goes.
    Never raises."""
    tmp = None
    key = os.path.normcase(os.path.abspath(str(project)))
    with _MAKING_LOCK:
        if key in _MAKING:
            return None
        _MAKING.add(key)
    try:
        project = Path(project)
        run_dir = Path(run_dir) if run_dir else project / "edit"
        if not (run_dir / "1_edit.xml").is_file() or not drive_ok(project):
            return None
        put, left = members(project, run_dir, leave_unplayed)
        z = zip_path(project)
        if z.exists() and _owner(z) not in ("", project.name):
            z = _free_name(project)                     # another project's zip has this name
        _clean_tmp(z)
        need = sum(f.stat().st_size for f, _a in put if f.exists())
        if _free_after(project, need) < MIN_FREE:
            return None
        tmp = z.with_name(z.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
            for f, arc in put:
                store = f.suffix.lower() in STORE
                zf.write(f, arc, compress_type=zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED)
            zf.writestr(f"{plain_name(project)}/{HOW_TO}", how_to(project, put, left, run_dir))
            zf.comment = MARK
        os.replace(tmp, z)
        try:
            if z.name != f"{plain_name(project)}.zip" or (_project_data(project).get("zip") or {}).get("file"):
                from . import projects as P
                P.load(project).update(zip={"file": z.name})
        except Exception:  # noqa: BLE001
            pass
        old = old_zip_path(project)
        try:                                             # the older zip inside the folder: the same project twice
            if old.is_file() and os.path.normcase(str(old)) != os.path.normcase(str(z)):
                with zipfile.ZipFile(old) as zf:
                    mine = zf.comment == MARK
                if mine:
                    old.unlink()
        except (OSError, zipfile.BadZipFile):
            pass
        return z
    except Exception:  # noqa: BLE001 - a zip that can not be made never stops anything
        try:
            if tmp is not None:
                tmp.unlink()
        except Exception:  # noqa: BLE001
            pass
        return None
    finally:
        with _MAKING_LOCK:
            _MAKING.discard(key)


def make_later(project: Path, run_dir: Path | None = None) -> threading.Thread:
    """``make`` in a thread of its own (a whole project with its RAW takes a while: the queue never waits for it)."""
    t = threading.Thread(target=make, args=(project, run_dir), name="share_zip", daemon=True)
    t.start()
    return t


def size_text(n: int | None) -> str:
    """'1.6 GB', '230 MB', '0.4 MB'; '' for None."""
    if n is None:
        return ""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    return f"{n / 1024 ** 2:.0f} MB" if n >= 10 * 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB"


def zip_size(project: Path) -> int | None:
    """The size of the project's zip (None: none yet)."""
    try:
        return zip_path(Path(project)).stat().st_size
    except OSError:
        return None


def run_dir_of(folder: Path, step: dict) -> Path:
    run_dir = folder / "edit"
    if step.get("run_dir") and Path(step["run_dir"]).name and (folder / Path(step["run_dir"]).name).is_dir():
        run_dir = folder / Path(step["run_dir"]).name
    return run_dir


def sweep(root: Path) -> list[Path]:
    """The finished projects under ``root`` (project.json: the edit done) whose zip is missing, old or of the older
    kind: made."""
    from . import projects as P
    made: list[Path] = []
    try:
        folders = [p for p in Path(root).iterdir() if (p / P.FILE).is_file()]
    except OSError:
        return made
    for folder in sorted(folders):
        try:
            step = (P.read_json(folder / P.FILE).get("steps") or {}).get("edit") or {}
        except Exception:  # noqa: BLE001
            continue
        if step.get("state") != P.DONE:
            continue
        run_dir = run_dir_of(folder, step)
        if needs_zip(folder, run_dir):
            z = make(folder, run_dir)
            if z is not None:
                made.append(z)
    return made
