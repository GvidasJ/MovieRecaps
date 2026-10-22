"""A ZIP OF EVERY FINISHED PROJECT, to send to your friend (night 15, task 7).

When a project's edit is done (flow.record_edit) its folder gets ``<project folder name>.zip`` (without emoji) -- made again when its
files change later (Open in Premiere saved its .prproj): a sweep at the app's start does the projects made before.

In it, under one folder named after the project:

* ``1_edit.xml`` and ``2_captions.srt`` (the edit and its captions), the run's Premiere project(s) (``*.prproj``,
  Open in Premiere's), the project's ``title.txt``;
* ``media\\`` -- every file the XML plays that is small enough to send (the frame, the watermark, the song, the
  competitor's copy ...);
* ``HOW TO OPEN.txt`` -- how to open it in Premiere, and what was LEFT OUT and where it is: a file the XML plays from
  the shared drive that is over BIG_BYTES (the movie, its sound) stays there -- the XML finds it on the share; a big
  file that is NOT on the shared drive is left out too, said with its place (send it yourself).

Nothing is ever deleted or moved: the zip is written next to them (a temporary file renamed into place).
"""
from __future__ import annotations

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

BIG_BYTES = 200 * 1024 ** 2          # a media file this big is not put in the zip (the movie, its WAV)
HOW_TO = "HOW TO OPEN.txt"


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


def members(project: Path, run_dir: Path) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """(the files to put in -- (file, name in the zip) --, the files left out -- (file, why))."""
    top = plain_name(project)
    put: list[tuple[Path, str]] = []
    left: list[tuple[Path, str]] = []
    for name in ("1_edit.xml", "2_captions.srt"):
        f = run_dir / name
        if f.is_file():
            put.append((f, f"{top}/{name}"))
    for f in sorted(run_dir.glob("*.prproj")):
        put.append((f, f"{top}/{f.name}"))
    if (project / "title.txt").is_file():
        put.append((project / "title.txt", f"{top}/title.txt"))
    seen: set[str] = set()
    for where in xml_media(run_dir / "1_edit.xml"):        # (``where``: as the edit names it -- your friend's path)
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
        arc = m.name
        k = 2
        while arc.lower() in seen:
            arc = f"{m.stem}-{k}{m.suffix}"
            k += 1
        seen.add(arc.lower())
        put.append((m, f"{top}/media/{arc}"))
    return put, left


def how_to(project: Path, put: list[tuple[Path, str]], left: list[tuple[Path, str]]) -> str:
    lines = [f"{project.name}", "", "HOW TO OPEN", "",
             "1. Unzip this anywhere.",
             "2. In Premiere: File > Import, pick 1_edit.xml (or open the .prproj when there is one).",
             "3. The edit plays its files from where they were made. When Premiere asks where a file is, point it at",
             "   the media folder of this zip (the frame, the watermark, the song ...) -- and at the shared drive for",
             "   the files below.", ""]
    if left:
        lines += ["LEFT OUT (not in this zip):"]
        for f, why in left:
            lines.append(f"  - {f.name}: {why}")
            lines.append(f"      {f}")
        lines.append("")
    lines += ["IN THIS ZIP:"] + [f"  - {arc.split('/', 1)[1]}" for _f, arc in put] + [""]
    return "\r\n".join(lines)


def zip_path(project: Path) -> Path:
    return project / f"{plain_name(project)}.zip"


def needs_zip(project: Path, run_dir: Path) -> bool:
    """No zip yet, or one of its files changed after it was made."""
    z = zip_path(project)
    if not (run_dir / "1_edit.xml").is_file():
        return False
    if not z.is_file():
        return True
    t = z.stat().st_mtime
    put, _left = members(project, run_dir)
    return any(f.stat().st_mtime > t for f, _a in put if f.exists())


def make(project: Path, run_dir: Path | None = None) -> Path | None:
    """The project's zip (written whole); None without a finished edit. Never raises."""
    try:
        project = Path(project)
        run_dir = Path(run_dir) if run_dir else project / "edit"
        if not (run_dir / "1_edit.xml").is_file():
            return None
        put, left = members(project, run_dir)
        z = zip_path(project)
        tmp = z.with_name(z.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f, arc in put:
                zf.write(f, arc)
            zf.writestr(f"{plain_name(project)}/{HOW_TO}", how_to(project, put, left))
        os.replace(tmp, z)
        return z
    except Exception:  # noqa: BLE001 - a zip that can not be made never stops anything
        try:
            tmp.unlink()                                    # type: ignore[possibly-undefined]
        except Exception:  # noqa: BLE001
            pass
        return None


def sweep(root: Path) -> list[Path]:
    """The finished projects under ``root`` (project.json: the edit done) whose zip is missing or old: made."""
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
        run_dir = folder / "edit"
        if step.get("run_dir") and Path(step["run_dir"]).name and (folder / Path(step["run_dir"]).name).is_dir():
            run_dir = folder / Path(step["run_dir"]).name
        if needs_zip(folder, run_dir):
            z = make(folder, run_dir)
            if z is not None:
                made.append(z)
    return made
