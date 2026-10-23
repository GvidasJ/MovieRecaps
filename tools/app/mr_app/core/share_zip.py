"""A ZIP OF EVERY FINISHED PROJECT, to send to your friend (night 15, task 7; day 16, task 2: the WHOLE folder).

When a project's edit is done (flow.record_edit, in the background) its folder gets ``<project folder name>.zip``
(without emoji) -- made again when its files change later (Open in Premiere saved its .prproj); a sweep at the app's
start (and every 10 minutes) does the projects made before, and remakes a zip of the older kind (night 15's had only
the edit files).

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
    return Path(project) / f"{plain_name(Path(project))}.zip"


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


def members(project: Path, run_dir: Path | None = None) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """(the files to put in -- (file, name in the zip) --, the files left out -- (file, why))."""
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
            if not _skipped(f):
                files.append(f)
    played = {os.path.normcase(os.path.abspath(reach(m))) for m in plays}
    files.sort(key=lambda f: (os.path.normcase(os.path.abspath(f)) not in played,))      # the played copy first
    seen: dict = {}
    for f in files:
        k = _key(f)
        rel = f.relative_to(project).as_posix()
        if k is not None and k in seen:
            left.append((f, f"the same file as {seen[k]} (a hard link): copy that one here if you need it"))
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


def needs_zip(project: Path, run_dir: Path) -> bool:
    """No zip yet, a zip of the older kind (night 15: only the edit files), or a file changed after it was made
    (project.json alone does not count: the board changes it)."""
    z = zip_path(project)
    if not (Path(run_dir) / "1_edit.xml").is_file():
        return False
    if not z.is_file() or not _is_v2(z):
        return True
    t = z.stat().st_mtime
    put, _left = members(project, run_dir)
    for f, _a in put:
        if f.name.lower() in NO_TRIGGER and f.parent == Path(project):
            continue
        try:
            if f.stat().st_mtime > t:
                return True
        except OSError:
            continue
    return False


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


def make(project: Path, run_dir: Path | None = None) -> Path | None:
    """The project's zip (written whole); None without a finished edit or without the space. Never raises."""
    tmp = None
    try:
        project = Path(project)
        run_dir = Path(run_dir) if run_dir else project / "edit"
        if not (run_dir / "1_edit.xml").is_file():
            return None
        put, left = members(project, run_dir)
        z = zip_path(project)
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
        return z
    except Exception:  # noqa: BLE001 - a zip that can not be made never stops anything
        try:
            if tmp is not None:
                tmp.unlink()
        except Exception:  # noqa: BLE001
            pass
        return None


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
