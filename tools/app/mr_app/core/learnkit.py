"""Learn by drag and drop.

Night 9: your fixed Premiere project and the tool's run folder it was made from are copied to the names the nightly
LEARN item reads -- finished\\<name>\\<name>_fixed.prproj and output\\laptop_<name> -- and the LEARN line for the next
night's list is written for you to copy (sort_drop, name_from, plan, copy, learn_line: the Learn box uses them).

Night 11, the simpler Learn tab -- you drop only the fixed .prproj:

* ``find_run`` / ``search_run``: the run the project was made from, found by itself, best first -- (a) the media
  inside the project: the files its sequences play lie in a run's extras\\media; a path of the other computer, or a
  run moved to D:\\Shared since, is matched by the folder's name, by the cut list's own note of where the run was
  made, and by the RAW (its frame size, rate and length, as Premiere measured it); (b) a project.json in the
  .prproj's folder or above it (a fixed.prproj saved in a project folder -> its edit); (c) the project's name
  (video11_fixed -> output\\laptop_video11, output\\video11, <projects>\\*video11*\\edit). Every candidate is checked:
  it must hold the RAW your project plays, and its edit must play at least half of what your project plays;
* ``run_of_folder`` / ``prproj_of_folder``: a dropped project folder -> its newest finished edit (and its fix);
* ``new_fixed`` / ``mark_seen``: a new fixed.prproj (or *_fixed.prproj) in a project -> "Learn from this?";
* ``next_free_name`` / ``auto_name``: video13, video14 ... after what exists (finished, output, tests\\real, the
  learn queue);
* ``LearnForm`` / ``new_form`` / ``rebuild``: everything the form shows for one drop, made new for every drop;
* ``copy`` (then the learn queue) / ``copy_and_queue`` (the same, never raising).

Everything slow here (reading a .prproj, scanning folders, comparing edits) is meant for a worker thread, and the
night-11 functions never raise into the window: they return what they found, with the problems in plain words."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import learn_hub as H
from . import paths

BS = "\\"
STEREO_NAMES = ("raw_stereo.mp4", "raw_stereo.mov")   # a 5.1 RAW's one file in a run's extras\\media (day 12)


def is_run_folder(p: Path) -> bool:
    """A run folder of the tool: 1_edit.xml or extras\\cutlist.json in it."""
    return p.is_dir() and ((p / "1_edit.xml").is_file() or (p / "extras" / "cutlist.json").is_file())


def name_from(project: Path | None, run: Path | None) -> str:
    """'video11' from video11_fixed.prproj (also 'video11 fixed', 'video11-fixed2'); else the run folder's name
    without 'laptop_'; else 'video'."""
    if project is not None:
        n = re.sub(r"(?i)[ _-]+fix(?:ed)?\d*$", "", project.stem).strip(" _-")
        if n and n.lower() not in ("untitled", "project", "new project"):
            return safe(n)
    if run is not None:
        base = run
        if re.fullmatch(r"(?i)edit(-\d+)?", run.name) and (run.parent / "project.json").is_file():
            base = run.parent                            # night 10: a project's edit -> the project's name
        n = re.sub(r"(?i)^laptop_", "", base.name)
        n = re.sub(r"-\d+$", "", n)
        if n:
            return safe(n)
    return "video"


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name.strip()).strip("._") or "video"


@dataclass
class Plan:
    name: str
    project: Path
    run: Path
    project_to: Path
    run_to: Path

    def problems(self) -> list[str]:
        out = []
        if not self.project.is_file():
            out.append(f"{self.project.name}: not there")
        elif self.project.suffix.lower() != ".prproj":
            out.append(f"{self.project.name}: not a Premiere project (.prproj)")
        if not is_run_folder(self.run):
            out.append(f"{self.run.name}: not a run folder of the tool (no 1_edit.xml)")
        if self.project_to.exists() and self.project_to.resolve() != self.project.resolve():
            out.append(f"{rel(self.project_to)} is there already: it will be replaced")
        if self.run_to.exists() and self.run_to.resolve() != self.run.resolve():
            out.append(f"{rel(self.run_to)} is there already: it will be replaced")
        return out


def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(paths.repo()))
    except ValueError:
        return str(p)


def plan(project: Path, run: Path, name: str | None = None, root: Path | None = None) -> Plan:
    root = Path(root or paths.repo())
    n = safe(name) if name else name_from(project, run)
    return Plan(n, Path(project), Path(run), root / "finished" / n / f"{n}_fixed.prproj",
                root / "output" / f"laptop_{n}")


def sort_drop(items: Sequence[Path]) -> tuple[Path | None, Path | None, list[str]]:
    """(the .prproj, the run folder, notes) among dropped files / folders (a folder holding one .prproj counts as it
    -- a finished\\<name> folder)."""
    project = run = None
    notes = []
    for p in map(Path, items):
        if p.is_file() and p.suffix.lower() == ".prproj":
            project = p
        elif is_run_folder(p):
            run = p
        elif p.is_dir():
            pr = sorted(p.glob("*.prproj"))
            if len(pr) == 1:
                project = pr[0]
            else:
                notes.append(f"{p.name}: not a run folder (no 1_edit.xml) and not one project")
        else:
            notes.append(f"{p.name}: not a .prproj or a run folder")
    return project, run, notes


def tree_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _is_media(run: Path, f: Path) -> bool:
    """A file of the run's own media folder (extras\\media): never changed, so a hard link can stand for a copy."""
    try:
        parts = [x.lower() for x in f.relative_to(run).parts]
    except ValueError:
        return False
    return len(parts) >= 3 and parts[0] == "extras" and parts[1] == "media"


def _link(src: Path, dst: Path) -> bool:
    """A hard link dst -> src (the same drive: no space, instant); False when it cannot be made (then copy)."""
    try:
        if dst.exists():
            dst.unlink()
        os.link(src, dst)
        return True
    except OSError:
        return False


def copy(pl: Plan, progress: Callable[[int, int], None] | None = None, hub: str | os.PathLike | None = None,
         item: Any = None) -> str:
    """Copy the project and the run folder (bytes copied / all, to ``progress``); the same place: nothing to do. The
    run's media (extras\\media: the RAW can be gigabytes) are hard-linked when the copy is on the same drive, else
    copied. Then, with ``hub`` (the folder of learn_queue.md), the project goes into the learn queue (``item``: a
    learn_hub.QueueItem, default queue_item(pl)); returns "" or the queue's problem in plain words. A failed copy
    raises OSError, as before (copy_and_queue says it in plain words instead)."""
    total = (pl.project.stat().st_size if pl.project.is_file() else 0) + tree_size(pl.run)
    done = 0

    def tick(n: int) -> None:
        nonlocal done
        done += n
        if progress is not None:
            progress(done, total)
    if pl.project.resolve() != pl.project_to.resolve():
        pl.project_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pl.project, pl.project_to)
    tick(pl.project.stat().st_size)
    if pl.run.resolve() == pl.run_to.resolve():
        tick(tree_size(pl.run))
        return _queue(pl, hub, item)
    if pl.run_to.exists():
        shutil.rmtree(pl.run_to)
    for f in sorted(pl.run.rglob("*")):
        t = pl.run_to / f.relative_to(pl.run)
        if f.is_dir():
            t.mkdir(parents=True, exist_ok=True)
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        if _is_media(pl.run, f) and _link(f, t):
            tick(f.stat().st_size)
            continue
        with f.open("rb") as a, t.open("wb") as b:
            while True:
                chunk = a.read(8 << 20)
                if not chunk:
                    break
                b.write(chunk)
                tick(len(chunk))
        shutil.copystat(f, t)
    return _queue(pl, hub, item)


def learn_line(names: Sequence[str]) -> str:
    """The nightly list's LEARN item for these fixes (the wording of night 9's list)."""
    names = [n for n in names if n]
    if not names:
        return ""
    pairs = [f"finished{BS}{n}{BS}{n}_fixed.prproj and output{BS}laptop_{n}" for n in names]
    if len(pairs) == 1:
        what = f"if {pairs[0]} exist, compare them"
    else:
        what = "if " + "; ".join(pairs) + " exist, compare each pair"
    return (f"LEARN (quick): {what} and learn as on nights 7-8 (check each new rule against my earlier fixes first; "
            "make it a test case). Ignore audio levels, pitch, hand-moved audio, trims at cut points and the ending. "
            "If they're missing, skip this and say so.")


# =====================================================================================================================
# night 11: the simpler Learn tab
# =====================================================================================================================

NO_RUN = "I can't find the run for this project — pick it here (the tool's output folder your edit was made from)."
NO_PRPROJ = "Drop your fixed Premiere project (.prproj) -- or the project folder it is saved in."
UNFINISHED = ("it has not finished (no extras\\report.md): it is still running, or it stopped early -- wait for it, "
              "or pick another run")

HOW = {"exact": 100, "origin": 95, "project": 90, "source": 85, "folder": 80, "name": 60}
UNFINISHED_PENALTY = 40     # a run that has not finished (no report.md) is listed this much lower
NO_CUTLIST_SCORE = 50       # a run without a cut list cannot be checked: at most this
MIN_PLAYED = 0.5            # the run's edit plays at least this share of what the project plays (learn.MIN_PLAYED)
GENERIC = {"fixed", "fix", "untitled", "project", "new project", "copy", "edit", "raw", "competitor", "video",
           "final", "premiere"}
PICTURES = {".png", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".srt", ".mp3", ".wav",
            ".aif", ".aiff", ".m4a", ".aac", ".mogrt", ".prproj", ".aep"}
EDIT_RE = re.compile(r"(?i)^edit(?:-(\d+))?$")
NUM_RE = re.compile(r"^\d{3,}$")
FIXED_RE = re.compile(r"(?i)(?:^|[ _\-.])fixed\d*\.prproj$")


def tool() -> tuple[Any, Any]:
    """(match_cuts.prproj, match_cuts.learn): the tool's own readers of a Premiere project and of a run (its folder is
    put on the path when this Python does not have it installed)."""
    try:
        from match_cuts import learn as L, prproj as PR
    except ImportError:
        sys.path.append(str(paths.repo() / "tools" / "match_cuts"))
        from match_cuts import learn as L, prproj as PR
    return PR, L


def show(p: str | os.PathLike | None, repo: str | os.PathLike | None = None) -> str:
    """A path for the window: relative to the project folder (MovieRecaps) when inside it, else in full."""
    if p is None or not str(p):
        return ""
    s = os.path.normpath(H._clean(p))
    base = os.path.normpath(str(repo or paths.repo()))
    if os.path.normcase(s).startswith(os.path.normcase(base) + os.sep):
        return s[len(base) + 1:]
    return s


def _parts(p: str | os.PathLike) -> list[str]:
    s = H._clean(str(p)).replace("/", "\\")
    return [x.lower() for x in s.split("\\") if x and x != "."]


def _tail_eq(a: str | os.PathLike, b: str | os.PathLike, n: int) -> bool:
    pa, pb = _parts(a), _parts(b)
    return len(pa) >= n and len(pb) >= n and pa[-n:] == pb[-n:]


def _key(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(H._clean(p)))


def _file_name(p: str) -> str:
    return Path(H._clean(str(p)).replace("\\", "/")).name


def _is_video(p: str) -> bool:
    return Path(_file_name(p)).suffix.lower() not in PICTURES and not _file_name(p).isdigit()


def is_finished(run: Path) -> bool:
    """The run has finished: its report (the last file a run writes) is there (learn.finished_run)."""
    return (Path(run) / "extras" / "report.md").is_file()


def _finished_at(run: Path) -> float:
    try:
        return (Path(run) / "extras" / "report.md").stat().st_mtime
    except OSError:
        return 0.0


def _edit_no(name: str) -> int | None:
    m = EDIT_RE.match(name)
    return None if m is None else int(m.group(1) or 1)


def _project_of(run: Path) -> Path | None:
    """The project folder a run is the edit of (<project>\\edit, edit-2 ... with project.json above), else None."""
    run = Path(run)
    if _edit_no(run.name) is not None and (run.parent / "project.json").is_file():
        return run.parent
    return None


def _runs_in(folder: Path) -> list[Path]:
    """The run folders inside a folder (a project's edit, edit-2 ...; a batch's 001, 002 ...), newest first: the
    highest number first, finished ones before unfinished ones."""
    out = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for e in entries:
        n = _edit_no(e.name)
        if n is None and NUM_RE.match(e.name):
            n = int(e.name)
        if n is None:
            continue
        try:
            if e.is_dir() and is_run_folder(Path(e.path)):
                out.append((n, Path(e.path)))
        except OSError:
            continue
    out.sort(key=lambda t: (not is_finished(t[1]), -t[0]))
    return [p for _n, p in out]


# ---------------------------------------------------------------------------------------------------------------------
# what a run's cut list and a project's media say
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class RunMeta:
    """What a run's cut list says about it. ``digest``: the cut list's own hash -- two folders with the same one hold
    the same run (a copy); ``made_in``: the run folders its media paths name (where it was made, on that computer)."""
    dir: Path
    raw: dict
    competitor: dict
    provenance: dict
    digest: str
    made_in: list[str]


_META: dict[str, tuple[tuple, RunMeta]] = {}
_META_LOCK = threading.Lock()


def _made_in(cl: dict) -> list[str]:
    out = []
    for role in ("raw", "competitor"):
        p = str((cl.get(role) or {}).get("file_abs") or "")
        parts = H._clean(p).replace("/", "\\").split("\\")
        if len(parts) >= 4 and parts[-2].lower() == "media" and parts[-3].lower() == "extras":
            d = "\\".join(parts[:-3])
            if d and d not in out:
                out.append(d)
    return out


def run_meta(run_dir: str | os.PathLike) -> RunMeta | None:
    """The run's cut list (raw, competitor, provenance without the analysis parameters), cached by the file's size and
    time; None when there is none or it cannot be read."""
    p = Path(run_dir) / "extras" / "cutlist.json"
    try:
        st = os.stat(p)
    except OSError:
        return None
    key, sig = _key(p), (st.st_size, st.st_mtime_ns)
    with _META_LOCK:
        hit = _META.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        data = p.read_bytes()
        cl = json.loads(data.decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(cl, dict):
        return None
    prov = dict(cl.get("provenance") or {})
    prov.pop("analysis_params", None)
    meta = RunMeta(Path(run_dir), dict(cl.get("raw") or {}), dict(cl.get("competitor") or {}), prov,
                   hashlib.sha1(data).hexdigest(), _made_in(cl))
    with _META_LOCK:
        _META[key] = (sig, meta)
    return meta


def project_media(prproj: str | os.PathLike) -> dict[str, list[str]]:
    """{every file the project knows, by the path Premiere plays it from (what prproj.read names it): every path the
    project has for it -- that one, FilePath, and the MediaFileHistory paths (where the file was before you relinked
    it: the laptop's path of a run moved to D:\\Shared since)}."""
    data = Path(prproj).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    out: dict[str, list[str]] = {}
    for el in root:
        if el.tag != "Media":
            continue
        actual, fp = el.findtext("ActualMediaFilePath") or "", el.findtext("FilePath") or ""
        key = actual or fp
        if not key:
            continue
        names = [key, fp] + [c.text or "" for c in el if c.tag.startswith("MediaFileHistory")]
        out[key] = list(dict.fromkeys(H._clean(a.strip()) for a in names if a and a.strip()))
    return out


def _played(pr: Any) -> dict[str, float]:
    """{media path: seconds} of the files the project's sequences play (enabled video and audio clips)."""
    PR, _L = tool()
    out: dict[str, float] = {}
    for s in pr.sequences:
        for it in s.items:
            if it.media and it.enabled and it.kind in (PR.VIDEO, PR.AUDIO):
                out[it.media] = out.get(it.media, 0.0) + max(0.0, it.end - it.start)
    return out


def _hints(played: Iterable[str], aliases: dict[str, list[str]]) -> list[tuple[str, str]]:
    """[(the run folder a played file points into, that file)]: a file in a run's extras\\media (or an older run's
    media) -- every path the project has for it (learn.media_dirs, with the relinked paths)."""
    out: list[tuple[str, str]] = []
    for m in played:
        for a in aliases.get(m) or [H._clean(m)]:
            parts = H._clean(a).replace("/", "\\").split("\\")
            if len(parts) < 3 or parts[-2].lower() != "media":
                continue
            d = parts[:-2]
            if d and d[-1].lower() == "extras":
                d = d[:-1]
            h = "\\".join(d)
            if h and (h, m) not in out:
                out.append((h, m))
    return out


def _close_props(props: dict, raw: dict) -> bool:
    """The file Premiere measured is the run's RAW by its frame size, rate and length alone (a renamed copy): all
    three known and the same (the length within half a second -- two episodes of a show can be 10 s apart)."""
    try:
        from fractions import Fraction
        fps = float(Fraction(str(raw.get("fps"))))
        return (int(props["width"]), int(props["height"])) == (int(raw["width"]), int(raw["height"])) and \
            abs(float(props["fps"]) / fps - 1.0) < 1e-3 and abs(float(props["duration"]) - float(raw["duration_s"])) <= 0.5
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def project_raw(pr: Any, raw: dict) -> tuple[str, Any, bool] | None:
    """(the media path, the sequence, found by its name) of the run's RAW in the project ``pr`` (prproj.read):
    the file named like the run's RAW (raw.mp4, or the original's name -- a project that plays the RAW from where it
    was downloaded) whose size, rate and length agree (learn.media_mismatch), else a video with exactly the RAW's
    size, rate and length under another name; the sequence that plays it the longest. None when the project does not
    play it."""
    PR, L = tool()
    names = {_file_name(str(raw.get(k) or "")).lower() for k in ("file", "file_rel", "file_abs", "source_path")}
    names.discard("")
    names.update(STEREO_NAMES)        # a 5.1 RAW: 1_edit.xml plays its one file, raw_stereo.mp4 (day 12)
    want = {"raw": raw}
    best, rank = None, None
    for s in pr.sequences:
        secs: dict[str, float] = {}
        for it in s.items:
            if it.media and it.enabled and it.kind in (PR.VIDEO, PR.AUDIO):
                secs[it.media] = secs.get(it.media, 0.0) + max(0.0, it.end - it.start)
        for m, t in secs.items():
            props = pr.media.get(m) or {}
            byname = _file_name(m).lower() in names
            if byname:
                if L.media_mismatch(props, want) is not None:
                    continue
            elif not (_is_video(m) and _close_props(props, raw)):
                continue
            r = (byname, t)
            if rank is None or r > rank:
                best, rank = (m, s, byname), r
    return best


def sequence_text(pr: Any, seq: Any) -> str:
    """"'Recreated Edit (Premiere)' (3 of 3), 1080x1920 at 30 fps, 24.1 s" -- which sequence of the project."""
    n = len(pr.sequences)
    i = next((k for k, s in enumerate(pr.sequences) if s is seq), 0)
    where = f" ({i + 1} of {n})" if n > 1 else ""
    size = f", {seq.width}x{seq.height}" if seq.width else ""
    rate = f" at {seq.fps:.3f}".rstrip("0").rstrip(".") + " fps" if seq.fps else ""
    return f"'{seq.name}'{where}{size}{rate}, {seq.duration:.1f} s"


def played_share(pr_seq: Any, raw_media: str, run_dir: Path, raw_width: float | None = None) -> float | None:
    """The share of the RAW moments the project's sequence plays that the run's edit (1_edit.xml) plays too
    (learn.edit_changes' "played"; the pieces of one take joined first); None when it cannot be told."""
    PR, L = tool()
    rf = L.run_files(Path(run_dir))
    if rf is None:
        return None
    try:
        tp, ts, _fps, _w = L.tool_clips(rf["edit"], raw_width or None)
    except Exception:  # noqa: BLE001 - an edit the tool cannot read back: cannot tell
        return None
    up, us = L.user_clips(pr_seq, PR.file_name(raw_media))
    t, u = (tp or ts), (up or us)
    if not t or not u:
        return None
    return float(L.edit_changes(L.join_splits(t), L.join_splits(u))["played"])


# ---------------------------------------------------------------------------------------------------------------------
# where to look
# ---------------------------------------------------------------------------------------------------------------------

def default_roots(shared: str | os.PathLike | None = None, repo: str | os.PathLike | None = None) -> list[Path]:
    """Where runs are looked for: the project folder's output\\, the local projects root (paths.projects_root()), the
    shared projects root (``shared``; default: Settings' "Save projects to" -- D:\\Shared\\Projects on the PC,
    \\\\192.168.101.11\\Shared\\Projects on the laptop) and the folder it is in (D:\\Shared, one level deep). Nothing is
    read here: a root that is not there is skipped by the search."""
    repo = Path(repo or paths.repo())
    if shared is None:
        try:
            from . import settings as S
            shared = S.save_root()
        except Exception:  # noqa: BLE001 - settings.py missing or broken: the PC's default
            shared = Path(r"D:\Shared\Projects")
    shared = Path(shared)
    out = [repo / "output", paths.projects_root(), shared]
    parent = shared.parent
    if parent != shared and not H._letter_root(str(parent)):
        out.append(parent)
    seen, uniq = set(), []
    for r in out:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            uniq.append(Path(r))
    return uniq


def _root_ok(root: Path, notes: list[str], timeout: float) -> bool:
    if str(root).startswith("\\\\"):
        if not H.reachable(root, timeout):
            notes.append(f"{root} cannot be reached now (is the PC on, is the network there?) -- not searched")
            return False
        return True
    return root.is_dir()


def run_index(roots: Iterable[str | os.PathLike], timeout: float = 3.0) -> tuple[list[tuple[Path, Path | None]],
                                                                                list[str]]:
    """Every run folder under the roots, one level deep: [(run folder, its project folder or None)] -- a root's
    folders that are runs, a project's edit / edit-N, a batch folder's 001, 002 ... -- and the roots that could not be
    searched (plain words)."""
    out: list[tuple[Path, Path | None]] = []
    notes: list[str] = []
    seen: set[str] = set()

    def add(p: Path, project: Path | None) -> None:
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append((p, project))
    for root in map(Path, roots):
        if not _root_ok(root, notes, timeout):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError as e:
            notes.append(f"{root} could not be read ({e.strerror or e}) -- not searched")
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
                inner = {x.name.lower(): x for x in os.scandir(e.path)}
            except OSError:
                continue
            d = Path(e.path)
            if "1_edit.xml" in inner or ("extras" in inner and (d / "extras" / "cutlist.json").is_file()):
                add(d, None)
            project = d if "project.json" in inner else None
            for low, x in inner.items():
                if EDIT_RE.match(low) is None and NUM_RE.match(low) is None:
                    continue
                try:
                    if x.is_dir() and is_run_folder(Path(x.path)):
                        add(Path(x.path), project if EDIT_RE.match(low) else None)
                except OSError:
                    continue
    return out, notes


def _projects_above(prproj: Path, levels: int = 3) -> list[Path]:
    """The project folder (project.json) the .prproj is saved in or under (a fixed.prproj in <project>\\ or
    <project>\\edit\\), nearest first."""
    out = []
    d = Path(prproj).parent
    for _ in range(levels):
        try:
            if (d / "project.json").is_file():
                out.append(d)
                break
        except OSError:
            break
        if d.parent == d:
            break
        d = d.parent
    return out


def _name_token(name: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", text.lower()) is not None


# ---------------------------------------------------------------------------------------------------------------------
# finding the run
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class RunCandidate:
    """A run folder the fixed project may have been made from. ``how``: how it was found, in plain words; ``score``:
    how sure (100 = your project plays this very folder's media); ``project_folder``: the project (project.json) the
    run is the edit of; ``sequence``: the project's sequence that plays its RAW; ``same_as``: other folders holding the
    very same run (copies); ``ways``: every way it was found (exact, origin, project, source, folder, name)."""
    run_dir: Path
    how: str
    score: float
    project_folder: Path | None = None
    sequence: str = ""
    finished: bool = True
    notes: list[str] = field(default_factory=list)
    same_as: list[Path] = field(default_factory=list)
    ways: list[str] = field(default_factory=list)
    played: float | None = None


@dataclass
class RunSearch:
    """find_run's whole answer: the candidates (best first), why nothing was found or what could not be searched
    (``problems``), runs that looked right but are not (``rejected``), and how long it took."""
    prproj: Path
    candidates: list[RunCandidate] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0


def find_run(prproj: str | os.PathLike, roots: Iterable[str | os.PathLike] | None = None, *, name: str | None = None,
             repo: str | os.PathLike | None = None) -> list[RunCandidate]:
    """The runs the fixed project was made from, best first ([] when none is found: search_run says why).
    ``roots``: where to look (default_roots()); ``name``: the name the fix will be learned under (a copy already at
    output\\laptop_<name> comes first among copies of one run)."""
    return search_run(prproj, roots, name=name, repo=repo).candidates


def search_run(prproj: str | os.PathLike, roots: Iterable[str | os.PathLike] | None = None, *,
               name: str | None = None, repo: str | os.PathLike | None = None) -> RunSearch:
    """find_run with the reasons (module docstring: the media inside the project, a project.json above it, its
    name). Never raises."""
    t0 = time.time()
    res = RunSearch(Path(prproj))
    try:
        _search(res, roots, name, Path(repo or paths.repo()))
    except Exception as e:  # noqa: BLE001 - never into the window
        res.problems.append(f"the search stopped: {type(e).__name__}: {e}")
    if not res.candidates:
        res.problems.insert(0, NO_RUN)
    res.seconds = round(time.time() - t0, 3)
    return res


_PROJECTS: dict[str, tuple[tuple, tuple[Any, dict[str, list[str]]]]] = {}
_PROJECTS_LOCK = threading.Lock()
PROJECTS_KEPT = 6           # projects read lately, kept (the search, then the check card, read the same one)


def _read_project(prproj: Path, res: RunSearch | None = None) -> tuple[Any, dict[str, list[str]]] | None:
    """(prproj.read's Project, project_media) of a .prproj -- read again only when the file changed (its size and
    time); None with the problem in ``res`` when it cannot be read."""
    PR, _L = tool()
    try:
        st = os.stat(prproj)
        key, sig = _key(prproj), (st.st_size, st.st_mtime_ns)
        with _PROJECTS_LOCK:
            hit = _PROJECTS.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        got = (PR.read(prproj), project_media(prproj))
        with _PROJECTS_LOCK:
            _PROJECTS[key] = (sig, got)
            while len(_PROJECTS) > PROJECTS_KEPT:
                _PROJECTS.pop(next(iter(_PROJECTS)))
        return got
    except PermissionError:
        why = "it is open in another program, or being saved -- try again in a moment"
    except OSError as e:
        why = f"it cannot be read ({e.strerror or e})"
    except Exception as e:  # noqa: BLE001 - not gzip XML, cut short ...
        why = f"it is not a Premiere project, or it is damaged ({type(e).__name__})"
    if res is not None:
        res.problems.append(f"I can't read {prproj.name}: {why}")
    return None


def _search(res: RunSearch, roots: Iterable[str | os.PathLike] | None, name: str | None, repo: Path) -> None:
    prproj = res.prproj
    if not prproj.is_file():
        res.problems.append(f"{prproj.name}: not there")
        return
    PR, L = tool()
    got = _read_project(prproj, res)
    if got is None:
        return
    pr, aliases = got
    played = _played(pr)
    hints = _hints(played, aliases)
    sources = [(m, a) for m in played if _is_video(m) for a in (aliases.get(m) or [H._clean(m)])
               if not _parts(a)[-2:-1] == ["media"]]
    nm = name_from(prproj, None)
    nm = nm if nm.lower() not in GENERIC else ""
    roots = list(roots) if roots is not None else default_roots(repo=repo)
    index, notes = run_index(roots)
    res.notes += notes
    cands: dict[str, dict] = {}

    def add(run: Path, way: str, text: str, project: Path | None = None) -> None:
        c = cands.setdefault(_key(run), {"run": Path(run), "ways": {}, "project": project})
        c["ways"].setdefault(way, text)
        if project is not None and c["project"] is None:
            c["project"] = project
    local: dict[str, bool] = {}
    shares: dict[str, bool] = {}                                        # a network share: asked once (2 s at most)
    for h, m in hints:                                                  # (a) the very folder the media lie in
        if h not in local:
            share = os.path.splitdrive(h)[0].lower() if h.startswith("\\\\") else ""
            if share and share not in shares:
                shares[share] = H.reachable(share + "\\", 2.0)
            local[h] = (shares[share] if share else True) and os.path.isdir(h) and is_run_folder(Path(h))
        if local[h]:
            add(Path(h), "exact", f"your project plays its media ({_file_name(m)} in its extras\\media)",
                _project_of(Path(h)))
    for run, project in index:                                          # (a) moved, copied, the other computer's
        meta = run_meta(run)
        mine = _parts(run)[-2:]
        for h, _m in hints:
            ht = _parts(h)[-2:]
            if len(ht) < 2:
                continue
            if meta is not None and any(_parts(x)[-2:] == ht for x in meta.made_in):
                add(run, "origin", f"it was made in {h} -- where your project's media point -- and copied or moved "
                                   "here since", project)
            if mine == ht:
                add(run, "folder", f"its folder has the name of the one your project's media point to "
                                   f"({BS.join(Path(H._clean(h)).parts[-2:])})", project)
        if meta is not None:
            for _m, a in sources:
                if any(meta.raw.get(k) and _tail_eq(meta.raw[k], a, 2) for k in ("source_path", "file_abs")):
                    add(run, "source", f"your project plays the RAW it was made from ({_file_name(a)})", project)
                    break
        if nm and (_name_token(nm, run.name) or (project is not None and _name_token(nm, project.name))):
            add(run, "name", f"its name matches your project's ({nm})", project)
    for proj in _projects_above(prproj):                                # (b) the project folder it is saved in
        for run in _runs_in(proj):
            add(run, "project", f"your .prproj is saved in its project folder ({proj.name})", proj)
    _judge(res, pr, cands, name or nm, repo)
    if not res.candidates:
        for h, _m in hints[:2]:
            if not local.get(h) and not any(_parts(r)[-2:] == _parts(h)[-2:] for r, _p in index):
                res.notes.append(f"your project's media point to {h} -- that folder is not on this computer (the "
                                 "other computer's?): copy it here, or pick the run")


def _judge(res: RunSearch, pr: Any, cands: dict[str, dict], name: str, repo: Path) -> None:
    """Check every candidate (its RAW in the project, its edit playing what the project plays), score it, put copies
    of one run together (a copy already at output\\laptop_<name> first), best first."""
    PR, _L = tool()
    found: list[tuple[RunCandidate, str]] = []
    for c in cands.values():
        run, ways = c["run"], c["ways"]
        order = sorted(ways, key=lambda w: -HOW[w])
        score = HOW[order[0]] + 2 * (len(order) - 1)
        rc = RunCandidate(run, ways[order[0]], float(score), c["project"] or _project_of(run), ways=order)
        meta = run_meta(run)
        if meta is None:
            rc.score = float(min(score, NO_CUTLIST_SCORE))
            rc.notes.append("it has no extras\\cutlist.json, so I can't check that it holds your project's RAW")
            group = _key(run)
        else:
            got = project_raw(pr, meta.raw)
            if got is None:
                res.rejected.append(f"{show(run, repo)}: {_other_raw(pr, meta.raw)}")
                continue
            m, seq, byname = got
            rc.sequence = sequence_text(pr, seq)
            if not byname:
                rc.notes.append(f"your project plays its RAW under another name ({_file_name(m)})")
            share = played_share(seq, m, run, float(meta.raw.get("width") or 0) or None)
            rc.played = share
            if share is not None and share < MIN_PLAYED:
                res.rejected.append(f"{show(run, repo)}: its edit plays only {100 * share:.0f} % of what your project "
                                    "plays -- another video from the same RAW, or a later run in the same folder")
                continue
            group = meta.digest
        rc.finished = is_finished(run)
        if not rc.finished:
            rc.score -= UNFINISHED_PENALTY
            rc.notes.append(UNFINISHED)
        found.append((rc, group))
    place = _key(repo / "output" / f"laptop_{safe(name)}") if name else ""
    groups: dict[str, list[RunCandidate]] = {}
    for rc, g in found:
        groups.setdefault(g, []).append(rc)
    ranked = []
    for members in groups.values():
        best = max(m.score for m in members)
        newest = max(_finished_at(m.run_dir) for m in members)
        members.sort(key=lambda m: (_key(m.run_dir) != place, "exact" not in m.ways, -m.score, show(m.run_dir)))
        for m in members:
            m.score = best
            m.same_as = [x.run_dir for x in members if x is not m]
            if m.same_as:
                m.notes.append("the same run is also in " + ", ".join(show(x, repo) for x in m.same_as))
        if len(members) > 1 and _key(members[0].run_dir) == place:
            members[0].notes.insert(0, "a copy already where the nightly LEARN step reads it: nothing to copy")
        ranked.append((best, newest, members))
    ranked.sort(key=lambda t: (-t[0], -t[1]))
    res.candidates = [m for _b, _n, ms in ranked for m in ms]


def _dur(s: float) -> str:
    s = float(s)
    if s < 60:
        return f"{s:.1f} s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m // 60}:{m % 60:02d}:{sec:02d}" if m >= 60 else f"{m}:{sec:02d}"


def _other_raw(pr: Any, raw: dict) -> str:
    """Why a run is not the project's: its RAW against the videos the project plays."""
    try:
        from fractions import Fraction
        fps = f"{float(Fraction(str(raw.get('fps')))):.3f}".rstrip("0").rstrip(".")
    except (ValueError, ZeroDivisionError, TypeError):
        fps = "?"
    mine = f"{raw.get('width')}x{raw.get('height')} at {fps} fps, {_dur(float(raw.get('duration_s') or 0))}"
    vids = []
    for m in _played(pr):
        p = pr.media.get(m) or {}
        if _is_video(m) and p.get("width") and p.get("duration") and (p.get("duration") or 0) < 40000:
            vids.append(f"{_file_name(m)} ({p['width']}x{p['height']}, {_dur(p['duration'])})")
    return (f"another video -- its RAW is {mine}; your project plays " + (", ".join(vids[:3]) if vids else
                                                                         "no video of that size and length"))


# ---------------------------------------------------------------------------------------------------------------------
# a dropped folder
# ---------------------------------------------------------------------------------------------------------------------

def prproj_of_folder(folder: str | os.PathLike) -> Path | None:
    """The fixed project in a dropped folder: the newest fixed.prproj / *_fixed.prproj in it or in its edit
    folders, else its only .prproj; None when there is none (or several and none is called fixed)."""
    d = Path(folder)
    fixed, every = [], []
    for sub in [d] + [d / e.name for e in _scandir(d) if _edit_no(e.name) is not None and _isdir(e)]:
        for e in _scandir(sub):
            if not e.name.lower().endswith(".prproj"):
                continue
            try:
                if not e.is_file():
                    continue
                t = e.stat().st_mtime
            except OSError:
                continue
            (fixed if FIXED_RE.search(e.name) and not _own_project(e.path) else every).append((t, Path(e.path)))
    if fixed:
        return max(fixed)[1]
    top = [p for _t, p in every if p.parent == d]
    return top[0] if len(top) == 1 else None


def _own_project(path: str | os.PathLike) -> bool:
    """Day 13: the app's own '<title>.prproj' (next to its '<title>_edit.xml') is never "your fix" because of its name
    -- a title may end with "fixed"."""
    p = Path(path)
    if p.stem.lower().endswith("_edit"):
        return False                                  # (night 10's own <name>_edit.prproj never matched FIXED_RE)
    try:
        from .premiere import is_own_project
        return is_own_project(p)
    except Exception:  # noqa: BLE001
        return False


def _scandir(d: Path) -> list[os.DirEntry]:
    try:
        return list(os.scandir(d))
    except OSError:
        return []


def _isdir(e: os.DirEntry) -> bool:
    try:
        return e.is_dir()
    except OSError:
        return False


def run_of_folder(folder: str | os.PathLike, roots: Iterable[str | os.PathLike] | None = None) -> Path | None:
    """The run a dropped folder stands for: a run folder -> itself; a project folder (or a batch folder) -> its newest
    finished edit / edit-N (001, 002 ...; the newest one when none has finished); a folder holding one .prproj (a
    finished\\<name> folder) -> that project's run (find_run); else None. Never raises."""
    try:
        d = Path(folder)
        if is_run_folder(d):
            return d
        if not d.is_dir():
            return None
        runs = _runs_in(d)
        if runs:
            return runs[0]
        prs = [Path(e.path) for e in _scandir(d) if e.name.lower().endswith(".prproj")]
        if len(prs) == 1:
            c = find_run(prs[0], roots)
            return c[0].run_dir if c else None
    except Exception:  # noqa: BLE001 - never into the window
        return None
    return None


# ---------------------------------------------------------------------------------------------------------------------
# watching for a new fixed.prproj
# ---------------------------------------------------------------------------------------------------------------------

_SEEN_LOCK = threading.Lock()


def default_state_path() -> Path:
    """Where this computer remembers the fixes it has offered: next to the app's settings
    (%APPDATA%\\MovieRecaps\\learn_seen.json)."""
    try:
        from . import settings as S
        return S.path().with_name("learn_seen.json")
    except Exception:  # noqa: BLE001 - settings.py missing: %APPDATA% itself
        base = os.environ.get("APPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "MovieRecaps" / "learn_seen.json"


def _load_seen(state_path: Path) -> dict:
    try:
        d = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return d.get("seen") or {} if isinstance(d, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def new_fixed(roots: Iterable[str | os.PathLike], state_path: str | os.PathLike, *,
              queue_folder: str | os.PathLike | None = None, settle_s: float = 10.0,
              timeout: float = 3.0) -> list[tuple[Path, Path]]:
    """[(project folder, fixed .prproj)] that appeared since they were last offered: fixed.prproj or *_fixed.prproj in
    <root>\\<project>\\ or <root>\\<project>\\edit*\\, not seen before (mark_seen) or changed since, not already in
    the learn queue (``queue_folder``), untouched for ``settle_s`` seconds (Premiere has finished saving it); newest
    first. Only folder listings -- cheap enough for every minute (about 20 ms for D:\\Shared and output\\ here).
    Never raises."""
    try:
        return _new_fixed(roots, Path(state_path), queue_folder, settle_s, timeout)
    except Exception:  # noqa: BLE001 - a watcher never stops the window: nothing new this time
        return []


def _new_fixed(roots: Iterable[str | os.PathLike], state_path: Path, queue_folder: str | os.PathLike | None,
               settle_s: float, timeout: float) -> list[tuple[Path, Path]]:
    seen = _load_seen(state_path)
    queued = set()
    if queue_folder is not None:
        queued = {_key(it.prproj) for it in H.queue_items(queue_folder, include_done=True) if it.prproj}
    now = time.time()
    out: list[tuple[float, Path, Path]] = []
    notes: list[str] = []
    for root in map(Path, roots):
        if not _root_ok(root, notes, timeout):
            continue
        for e in _scandir(root):
            if not _isdir(e):
                continue
            proj = Path(e.path)
            subs = [proj] + [proj / x.name for x in _scandir(proj) if _edit_no(x.name) is not None and _isdir(x)]
            for sub in subs:
                for f in _scandir(sub):
                    if not FIXED_RE.search(f.name) or _own_project(f.path):
                        continue
                    try:
                        if not f.is_file():
                            continue
                        st = f.stat()
                    except OSError:
                        continue
                    if now - st.st_mtime < settle_s:
                        continue                                # still being saved: the next look offers it
                    k = _key(f.path)
                    rec = seen.get(k) or {}
                    if rec.get("size") == st.st_size and rec.get("mtime_ns") == st.st_mtime_ns:
                        continue
                    if k in queued:
                        continue
                    out.append((st.st_mtime, proj, Path(f.path)))
    out.sort(key=lambda t: -t[0])
    return [(p, f) for _t, p, f in out]


def mark_seen(state_path: str | os.PathLike, prproj: str | os.PathLike) -> str:
    """Remember that this fix was offered ("Learn from this?" answered either way): new_fixed offers it again only
    when it changes. "" or the problem in plain words."""
    try:
        st = os.stat(prproj)
    except OSError as e:
        return f"{Path(prproj).name}: {e.strerror or e}"
    p = Path(state_path)
    with _SEEN_LOCK:
        seen = _load_seen(p)
        seen[_key(prproj)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "seen": H.stamp()}
        try:
            H._write_text(p, json.dumps({"version": 1, "seen": seen}, indent=1, ensure_ascii=False) + "\n")
        except OSError as e:
            return H._problem("could not save", p, e)
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------------------------------------------------

def _number(name: str, prefix: str) -> tuple[int, int] | None:
    """(12, 0) from video12, laptop_video12, video12-2, video12-fixed; (17, 3) from video017 -- a zero-padded number
    (the batch's three-digit run numbers: tests\\real\\video017-fixed) is another series; None for other names."""
    m = re.match(rf"(?i)^(?:laptop_)?{re.escape(prefix)}(\d+)(?:$|[-_ .])", name)
    if not m or not int(m.group(1)):
        return None
    digits = m.group(1)
    return int(digits), (len(digits) if digits.startswith("0") else 0)


def next_free_name(repo: str | os.PathLike | None = None, queue_folder: str | os.PathLike | None = None,
                   prefix: str = "video") -> str:
    """The next free name: 'video13' when video1 ... video12 are taken -- the highest number in finished\\videoN,
    output\\laptop_videoN and output\\videoN, the test cases tests\\real\\videoN-fixed (also
    tools\\match_cuts\\tests\\real, if it ever exists) and the learn queue's names, plus one. Zero-padded numbers
    (video017: the batch's) count only when nothing else has the prefix (mike005 -> mike006). Never raises."""
    repo = Path(repo or paths.repo())
    names = []
    for d in (repo / "finished", repo / "output", repo / "tests" / "real", repo / "tools" / "match_cuts" / "tests" / "real"):
        names += [e.name for e in _scandir(d)]
    if queue_folder is not None:
        names += [it.name for it in H.queue_items(queue_folder, include_done=True)]
    plain, padded = [], []
    for name in names:
        got = _number(name, prefix)
        if got is not None:
            (padded if got[1] else plain).append(got)
    if plain or not padded:
        return f"{prefix}{max((n for n, _w in plain), default=0) + 1}"
    n, width = max(n for n, _w in padded) + 1, max(w for _n, w in padded)
    return f"{prefix}{n:0{width}d}"


def auto_name(prproj: str | os.PathLike | None, run: str | os.PathLike | None = None, *,
              repo: str | os.PathLike | None = None, queue_folder: str | os.PathLike | None = None) -> str:
    """The name a fix is learned under: the one it already has -- finished\\<name>\\<name>_fixed.prproj, a project in
    the learn queue, a project named like your earlier fixes (video11_fixed, laptop013_fixed, mike005_fixed), a run
    at output\\laptop_<name> -- else the next free one (video13, video14 ...). Never raises."""
    repo = Path(repo or paths.repo())
    try:
        if prproj is not None:
            p = Path(prproj)
            if _key(p.parent.parent) == _key(repo / "finished") and p.name.lower() == f"{p.parent.name}_fixed.prproj".lower():
                return p.parent.name
            if queue_folder is not None:
                for it in H.queue_items(queue_folder, include_done=True):
                    if it.prproj and _key(it.prproj) == _key(p):
                        return it.name
            n = name_from(p, None)
            if re.fullmatch(r"(?i)[a-z]+[_-]?\d+", n) and n.lower() not in GENERIC:
                return n
        if run is not None:
            r = Path(run)
            if _key(r.parent) == _key(repo / "output") and r.name.lower().startswith("laptop_") and len(r.name) > 7:
                return r.name[7:]
    except Exception:  # noqa: BLE001 - a name is never worth an error
        pass
    return next_free_name(repo, queue_folder)


# ---------------------------------------------------------------------------------------------------------------------
# the form: one per drop
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class LearnForm:
    """Everything the Learn form shows for one drop -- made new for every drop, so nothing of the last one sticks:
    what was dropped, the fixed project, the run (and how it was found, and the other candidates), the project folder,
    the name (chosen by the app, or typed), the copy plan, the check card (learn_check.CheckCard), and plain-word
    problems (they stop learning) and notes (worth knowing)."""
    dropped: list[Path] = field(default_factory=list)
    prproj: Path | None = None
    run: Path | None = None
    run_how: str = ""
    candidates: list[RunCandidate] = field(default_factory=list)
    project_folder: Path | None = None
    name: str = ""
    name_auto: bool = True
    plan: Plan | None = None
    card: Any = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ready(self) -> bool:
        """Everything is there and nothing stops learning: the Learn button can be on."""
        return self.prproj is not None and self.run is not None and self.plan is not None and not self.problems


def new_form(items: Iterable[str | os.PathLike], roots: Iterable[str | os.PathLike] | None = None, *,
             repo: str | os.PathLike | None = None, queue_folder: str | os.PathLike | None = None,
             name: str | None = None, check: bool = True) -> LearnForm:
    """A new form for a drop (files and folders): the fixed .prproj (also from a dropped project folder), its run --
    a dropped run folder, else found (search_run; a dropped project folder's newest finished edit when the search
    finds nothing), the name (``name``, else auto_name), the copy plan and, with ``check``, the check card
    (learn_check.check; a few seconds). For a worker thread; never raises."""
    t0 = time.time()
    f = LearnForm(dropped=[Path(x) for x in items or []])
    try:
        _fill(f, roots, Path(repo or paths.repo()), queue_folder, name, check)
    except Exception as e:  # noqa: BLE001 - never into the window
        f.problems.append(f"something went wrong while reading the drop: {type(e).__name__}: {e}")
    f.seconds = round(time.time() - t0, 3)
    return f


def _fill(f: LearnForm, roots: Iterable[str | os.PathLike] | None, repo: Path,
          queue_folder: str | os.PathLike | None, name: str | None, check: bool) -> None:
    dropped_run = folder_run = None
    projects = []
    for p in f.dropped:
        if p.is_file() and p.suffix.lower() == ".prproj":
            if f.prproj is not None and _key(f.prproj) != _key(p):
                f.notes.append(f"two projects dropped: {p.name} is used, {f.prproj.name} is not")
            f.prproj = p
        elif p.is_dir():
            if is_run_folder(p):
                dropped_run = p
                continue
            if (p / "project.json").is_file():
                projects.append(p)
            pp = prproj_of_folder(p)
            if pp is not None and f.prproj is None:
                f.prproj = pp
            rr = _runs_in(p)
            if rr and folder_run is None:
                folder_run = rr[0]
            if pp is None and not rr:
                f.problems.append(f"{p.name}: no fixed .prproj and no run of the tool in it")
        elif p.exists():
            f.problems.append(f"{p.name}: not a Premiere project (.prproj) or a folder")
        else:
            f.problems.append(f"{p.name}: not there (moved or deleted?)")
    if f.prproj is None:
        f.problems.insert(0, NO_PRPROJ)
    if dropped_run is not None:
        f.run, f.run_how = dropped_run, "you dropped it"
    elif f.prproj is not None:
        s = search_run(f.prproj, roots, name=name, repo=repo)
        f.candidates = s.candidates
        f.notes += s.notes
        mine = [c for c in s.candidates if folder_run is not None and _key(c.run_dir) == _key(folder_run)]
        best = mine[0] if mine and mine[0].score >= s.candidates[0].score else (s.candidates[0] if s.candidates
                                                                                  else None)
        if best is not None:
            f.run, f.run_how = best.run_dir, best.how
            f.notes += best.notes
        elif folder_run is not None:
            f.run, f.run_how = folder_run, "the newest finished edit of the folder you dropped"
            f.notes += [p for p in s.problems if p != NO_RUN] + s.rejected[:3]
        else:
            f.problems += [p for p in s.problems if p not in f.problems]
            f.notes += s.rejected[:3]
    elif folder_run is not None:
        f.run, f.run_how = folder_run, "the newest finished edit of the folder you dropped"
    if f.run is not None:
        f.project_folder = _project_of(f.run) or (projects[0] if projects else None)
    elif projects:
        f.project_folder = projects[0]
    _finish(f, repo, queue_folder, name, check)


def _finish(f: LearnForm, repo: Path, queue_folder: str | os.PathLike | None, name: str | None,
            check: bool) -> None:
    """The name, the plan and the check card of a form whose project and run are known."""
    f.name_auto = not (name and name.strip())
    f.name = safe(name) if not f.name_auto else auto_name(f.prproj, f.run, repo=repo, queue_folder=queue_folder)
    if f.prproj is None or f.run is None:
        return
    f.plan = plan(f.prproj, f.run, f.name, root=repo)
    for p in f.plan.problems():
        (f.notes if "will be replaced" in p else f.problems).append(p)
    if check:
        from . import learn_check
        f.card = learn_check.check(f.prproj, f.run)
        f.problems += [p for p in f.card.problems if p not in f.problems]
        f.notes += [n for n in f.card.notes if n not in f.notes]


def rebuild(form: LearnForm, *, run: str | os.PathLike | None = None, name: str | None = None, check: bool = True,
            repo: str | os.PathLike | None = None, queue_folder: str | os.PathLike | None = None) -> LearnForm:
    """A new form from ``form`` with the run you picked (``run``) and/or the name you typed (``name``): planned and
    checked again. For a worker thread; never raises."""
    t0 = time.time()
    f = LearnForm(dropped=list(form.dropped), prproj=form.prproj, candidates=list(form.candidates),
                  project_folder=form.project_folder)
    try:
        repo = Path(repo or paths.repo())
        if run is not None:
            f.run, f.run_how = Path(run), "you picked it"
            f.project_folder = _project_of(f.run) or form.project_folder
            if f.prproj is None:
                f.problems.append(NO_PRPROJ)
        else:
            f.run, f.run_how = form.run, form.run_how
            if f.prproj is None:
                f.problems.append(NO_PRPROJ)
            elif f.run is None:
                f.problems.append(NO_RUN)
        typed = name if name is not None else (None if form.name_auto else form.name)
        _finish(f, repo, queue_folder, typed, check)
    except Exception as e:  # noqa: BLE001 - never into the window
        f.problems.append(f"something went wrong: {type(e).__name__}: {e}")
    f.seconds = round(time.time() - t0, 3)
    return f


# ---------------------------------------------------------------------------------------------------------------------
# learning: the copies, then the learn queue
# ---------------------------------------------------------------------------------------------------------------------

def _source_name(info: dict) -> str:
    """The original file name of a run's input (the cut list's source_path), else its file in the run."""
    for k in ("source_path", "file_abs", "file"):
        v = str(info.get(k) or "")
        if v:
            return _file_name(v)
    return ""


def queue_item(pl: Plan, card: Any = None, computer: str | None = None) -> H.QueueItem:
    """The learn queue's line for a plan: its name, my fixed project and the run as they were found (on the shared
    folder they are right on both computers), the RAW's and the competitor's file names, now, this computer, and the
    copies for the nightly LEARN step."""
    details = getattr(card, "details", None) or {}
    raw, comp = str(details.get("raw_name") or ""), str(details.get("competitor_name") or "")
    if not raw or not comp:
        meta = run_meta(pl.run)
        if meta is not None:
            raw, comp = raw or _source_name(meta.raw), comp or _source_name(meta.competitor)
    return H.QueueItem(pl.name, prproj=str(pl.project), run=str(pl.run), raw=raw, competitor=comp, date=H.stamp(),
                       computer=computer or H.this_computer(),
                       copies=f"finished{BS}{pl.name}{BS}{pl.name}_fixed.prproj, output{BS}laptop_{pl.name}")


def _queue(pl: Plan, hub: str | os.PathLike | None, item: Any) -> str:
    if hub is None:
        return ""
    return H.queue_add(hub, item if item is not None else queue_item(pl))


def _need(pl: Plan) -> int:
    """Bytes the copy writes: the project (unless it is in place) and the run (unless in place; its media not when
    they can be hard-linked -- the same drive)."""
    from . import space
    n = 0
    if pl.project.is_file() and _key(pl.project) != _key(pl.project_to):
        n += pl.project.stat().st_size
    if _key(pl.run) == _key(pl.run_to):
        return n
    link = space.same_drive(pl.run, pl.run_to.parent)
    for f in pl.run.rglob("*"):
        if f.is_file() and not (link and _is_media(pl.run, f)):
            n += f.stat().st_size
    return n


def copy_and_queue(pl: Plan, progress: Callable[[int, int], None] | None = None,
                   hub: str | os.PathLike | None = None, item: Any = None) -> str:
    """copy (then the learn queue) for the window: "" when done, else what went wrong in plain words -- never raises.
    Checks the room on the drive first."""
    from . import space
    try:
        bad = [p for p in pl.problems() if "will be replaced" not in p]
        if bad:
            return "; ".join(bad) + " -- nothing was copied"
        need, free = _need(pl), space.free_bytes(pl.run_to.parent)
        if free is not None and need > free:
            return (f"not enough room on {space.drive_name(pl.run_to)} for the copy: it needs {need / 1e9:.1f} GB, "
                    f"{free / 1e9:.1f} GB are free -- make room, then try again")
        return copy(pl, progress, hub, item)
    except FileNotFoundError as e:
        return f"{e.filename or 'a file'} is not there any more (moved or deleted?) -- nothing was learned"
    except PermissionError as e:
        return f"cannot write {e.filename or 'a file'}: access denied (is it open in Premiere?)"
    except OSError as e:
        return f"the copy stopped: {e.strerror or e}" + (f" ({e.filename})" if getattr(e, "filename", None) else "")
    except Exception as e:  # noqa: BLE001 - never into the window
        return f"the copy stopped: {type(e).__name__}: {e}"
