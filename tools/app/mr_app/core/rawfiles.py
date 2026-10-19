"""One RAW file per run (day 12) -- the Board's Clean up for runs made before it.

A RAW with 5.1 (any non-stereo) sound: 1_edit.xml plays ``extras\\media\\raw_stereo.mp4`` for V1 and A1 (night 6:
the RAW's picture copied as it is, ``-c:v copy``, and one stereo AAC track). Since day 12 that is the run's ONE RAW
file -- the tool removes its analysis copy (``raw.mp4``: a hard link / copy of the RAW, or ``raw_ae.mp4``: its
conform) when the run is done (match_cuts.stereo.one_raw_file). Older runs still hold both:

* ``find(cards, local_root)``: the finished edit folders (in the board's projects and this computer's twin copies)
  whose extras\\media holds raw_stereo.<ext> AND an analysis copy (raw.<ext> / raw_ae.<ext>);
* ``check(item)``: the stereo file must open as the same video -- ffprobe: the same number of video frames and the
  same length as each old file, two-channel sound that starts with the picture -- no XML of the edit folder may play
  an old file, and no Premiere project of the project folder (the run's own <name>_edit.prproj made by Open in
  Premiere, your *fixed*.prproj -- only ever read, never changed) may play one. Anything else: the files are kept,
  and why is said;
* only after you said yes: ``remove(items)`` -- each item checked again first, then the old copies deleted.

``freed`` counts what deleting really frees: an old copy that is a hard link of the downloaded RAW (another name of
the same file) frees nothing. Everything here reads disks and runs ffprobe: call it from a worker thread."""
from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import paths
from . import projects as P
from . import publish as PB

SINGLE_NAMES = ("raw_stereo.mp4", "raw_stereo.mov")
OLD_RE = re.compile(r"(?i)^raw(_ae)?\.(mp4|mov|m4v)$")       # the analysis copy: raw.<ext> or raw_ae.<ext>
PRPROJ_DEPTH = 4                                              # how deep a project folder is searched for .prproj
Say = Callable[[str], None]


@dataclass
class TwoFiles:
    """An old run folder that holds the RAW twice."""
    edit: Path                        # the edit (run) folder
    project: Path                     # its project folder (project.json)
    single: Path                      # extras\\media\\raw_stereo.<ext>: kept, the run's one RAW file
    old: list[Path]                   # the analysis copies to remove
    size: int = 0                     # their bytes
    freed: int = 0                    # what deleting them frees (a hard link: nothing)
    checked: bool = False
    ok: bool = False                  # checked and safe to remove
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def where(self) -> str:
        return f"{self.project.name}\\{self.edit.name}"

    def line(self) -> str:
        """'2.1 GB  family-guy\\edit: raw_ae.mp4 (1_edit.xml plays raw_stereo.mp4)' -- for the question."""
        names = ", ".join(p.name for p in self.old)
        extra = " -- a hard link of the downloaded RAW, frees no space" if self.size and not self.freed else ""
        return f"{PB.plain_size(self.freed)}  {self.where}: {names} (1_edit.xml plays {self.single.name}){extra}"


def tool() -> Any:
    """match_cuts.stereo (ffprobe helpers), from the repo's tool folder when this Python does not have it."""
    try:
        from match_cuts import stereo
    except ImportError:
        sys.path.append(str(paths.repo() / "tools" / "match_cuts"))
        from match_cuts import stereo
    return stereo


# ---------------------------------------------------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------------------------------------------------

def _edit_media(edit: Path) -> tuple[Path | None, list[Path]]:
    media = edit / "extras" / "media"
    try:
        files = [f for f in media.iterdir() if f.is_file()]
    except OSError:
        return None, []
    single = next((media / n for n in SINGLE_NAMES if (media / n).is_file()), None)
    if single is None:
        return None, []
    return single, sorted(f for f in files if OLD_RE.match(f.name))


def _folders(item: Any, local_root: Path | None) -> list[Path]:
    """The project folder of a card (or a folder) and this computer's twin copy of a published one."""
    folder = Path(getattr(item, "folder", item))
    out = [folder]
    if local_root is not None:
        twin = Path(local_root) / folder.name
        if PB._norm(twin) != PB._norm(folder) and (twin / P.FILE).is_file():
            out.append(twin)
    return out


def _busy(folder: Path) -> bool:
    try:
        from . import locks
        h = locks.holder(folder)
        return h is not None and not h.get("stale")
    except Exception:  # noqa: BLE001 - cannot tell: leave it alone
        return True


def find(items: Iterable[Any], local_root: str | os.PathLike | None = None) -> list[TwoFiles]:
    """The finished edit folders of these projects (Cards or folders, with their twins under ``local_root``) that
    hold raw_stereo.<ext> and an analysis copy of the RAW. A project someone works on now is left out."""
    lroot = Path(local_root) if local_root is not None else None
    out: list[TwoFiles] = []
    seen: set[str] = set()
    for item in items:
        if getattr(item, "error", "") or getattr(item, "lock", None) is not None:
            continue
        for folder in _folders(item, lroot):
            if _busy(folder):
                continue
            for edit in PB.finished_edits(folder):
                k = PB._norm(edit)
                if k in seen:
                    continue
                seen.add(k)
                single, old = _edit_media(edit)
                if single is None or not old:
                    continue
                size = freed = 0
                for f in old:
                    try:
                        st = os.stat(f)
                    except OSError:
                        continue
                    size += int(st.st_size)
                    freed += 0 if st.st_nlink > 1 else int(st.st_size)
                out.append(TwoFiles(edit, folder, single, old, size, freed))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------------------------------------------------

def probe(path: Path) -> dict:
    """{frames, duration (video), channels, a_start - v_start (s)} by ffprobe (no decode)."""
    st = tool()
    frames, dur = st._frames(path)
    lay = st.audio_layout(path)
    from match_cuts.common import ffprobe_bin
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,start_time", "-of", "json",
                        str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts: dict[str, float] = {}
    for s in (json.loads(r.stdout or "{}").get("streams") or []) if r.returncode == 0 else []:
        t = s.get("codec_type")
        if t in ("video", "audio") and t not in starts:
            try:
                starts[t] = float(s.get("start_time") or 0.0)
            except ValueError:
                starts[t] = 0.0
    off = (starts["audio"] - starts["video"]) if "audio" in starts and "video" in starts else None
    return {"frames": int(frames), "duration": float(dur), "channels": int(lay.get("channels") or 0), "offset": off}


def _url_names(text: str) -> set[str]:
    """'<edit>/extras/media/<file>' (lower case) of every media path in a project / XML text (paths or file URLs)."""
    import urllib.parse
    t = urllib.parse.unquote(text).replace("\\", "/").lower()
    return set(re.findall(r"[^/<>\"']+/extras/media/[^/<>\"'\s]+", t))


def _mentions(text: str, edit: Path, name: str) -> bool:
    return f"{edit.name.lower()}/extras/media/{name.lower()}" in _url_names(text)


def _read(p: Path) -> str:
    data = p.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


def _prprojs(project: Path) -> list[Path]:
    out: list[Path] = []
    base = len(project.parts)
    for d, dirs, names in os.walk(project):
        if len(Path(d).parts) - base >= PRPROJ_DEPTH:
            dirs[:] = []
        dirs[:] = [x for x in dirs if x.lower() not in ("media", "debug", "auto-save", "adobe premiere pro auto-save")]
        out += [Path(d) / n for n in names if n.lower().endswith(".prproj")]
    return sorted(out)


def users(item: TwoFiles) -> list[str]:
    """Why the old files must stay: the XMLs of the edit folder and the Premiere projects of the project folder that
    play one of them (the projects are only read)."""
    out: list[str] = []
    for xml in PB._edit_xmls(item.edit):
        try:
            text = xml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for f in item.old:
            if _mentions(text, item.edit, f.name):
                out.append(f"{xml.name} plays {f.name} (made before the stereo copy) -- run it again for a one-file "
                           "edit")
    for pp in _prprojs(item.project):
        try:
            text = _read(pp)
        except (OSError, EOFError, gzip.BadGzipFile) as e:
            out.append(f"I can't read {pp.name} ({e}) -- it might play {item.old[0].name}")
            continue
        for f in item.old:
            if _mentions(text, item.edit, f.name):
                own = PB._norm(pp.parent) == PB._norm(item.edit) and "fixed" not in pp.name.lower()
                out.append(f"{'its Premiere project' if own else 'your project'} {pp.name} plays {f.name}"
                           + (" -- open it in Premiere and relink it, or delete it and use Open in Premiere again"
                              if own else " -- relink it in Premiere to raw_stereo first"))
    return out


def check(item: TwoFiles, say: Say | None = None) -> TwoFiles:
    """Check ``item`` (module docstring): ``item.ok`` when its old copies can go. Never deletes or changes anything."""
    say = say or (lambda _t: None)
    item.problems, item.notes = [], []
    say(f"checking {item.where}: {item.single.name}")
    try:
        one = probe(item.single)
    except Exception as e:  # noqa: BLE001 - said in plain words
        item.problems.append(f"{item.single.name} does not open ({type(e).__name__}: {e})")
        one = None
    if one is not None:
        if one["frames"] <= 0:
            item.problems.append(f"{item.single.name} has no video frames")
        if one["channels"] != 2:
            item.problems.append(f"{item.single.name}'s sound has {one['channels']} channels, not stereo")
        if one["offset"] is not None and abs(one["offset"]) > 0.05:
            item.problems.append(f"{item.single.name}'s sound starts {1000 * one['offset']:+.0f} ms off the picture")
        for f in item.old:
            say(f"checking {item.where}: {f.name}")
            try:
                o = probe(f)
            except Exception as e:  # noqa: BLE001
                item.problems.append(f"{f.name} does not open ({type(e).__name__}: {e}) -- kept to be safe")
                continue
            if o["frames"] != one["frames"] or abs(o["duration"] - one["duration"]) > 0.1:
                item.problems.append(f"{item.single.name} is not the same video as {f.name} ({one['frames']} frames, "
                                     f"{one['duration']:.2f} s against {o['frames']} frames, {o['duration']:.2f} s)")
    say(f"checking {item.where}: the XMLs and Premiere projects")
    item.problems += users(item)
    item.checked = True
    item.ok = not item.problems
    if item.ok:
        item.notes.append(f"{item.single.name} opens: {one['frames']} frames, {one['duration']:.2f} s, stereo -- the "
                          "same video as " + ", ".join(f.name for f in item.old))
    return item


def plan(items: Iterable[Any], local_root: str | os.PathLike | None = None, say: Say | None = None
         ) -> list[TwoFiles]:
    """find + check every one (``say``: progress in plain words)."""
    say = say or (lambda _t: None)
    found = find(items, local_root)
    for i, it in enumerate(found, start=1):
        check(it, lambda t, i=i: say(f"One RAW file per run ({i} of {len(found)}): {t}"))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# remove (only after you said yes)
# ---------------------------------------------------------------------------------------------------------------------

def _safe_to_delete(f: Path, item: TwoFiles) -> str:
    """Why ``f`` must not be deleted ("" when it may): only an analysis copy right in the edit folder's
    extras\\media, never the stereo file, never a project."""
    if not OLD_RE.match(f.name):
        return f"{f.name} is not a RAW copy of the run"
    if PB._norm(f.parent) != PB._norm(item.edit / "extras" / "media"):
        return f"{f.name} is not in the run's extras\\media"
    if PB._norm(f) == PB._norm(item.single):
        return f"{f.name} is the run's one RAW file"
    return ""


def remove(items: Iterable[TwoFiles], say: Say | None = None) -> tuple[int, list[str]]:
    """Delete the old copies of the checked items -- each item checked again first (the stereo file there, nothing
    plays an old file). Returns (bytes freed, problems in plain words)."""
    say = say or (lambda _t: None)
    freed, errors = 0, []
    todo = [it for it in items if it.checked and it.ok]
    for i, it in enumerate(todo, start=1):
        say(f"One RAW file per run ({i} of {len(todo)}): {it.where}")
        if not it.single.is_file():
            errors.append(f"kept {it.where}'s files: {it.single.name} is not there any more")
            continue
        again = users(it)
        if again:
            errors.append(f"kept {it.where}'s files: " + "; ".join(again))
            continue
        for f in it.old:
            why = _safe_to_delete(f, it)
            if why:
                errors.append(f"kept {f.name}: {why}")
                continue
            try:
                st = os.stat(f)
                f.unlink()
            except FileNotFoundError:
                continue
            except OSError as e:
                errors.append(f"could not delete {it.where}\\{f.name}: {e.strerror or e}")
                continue
            freed += 0 if st.st_nlink > 1 else int(st.st_size)
    return freed, errors
