"""ONE RAW PER PROJECT (run 19, task 1) -- the Board's Clean up for projects made before run 19.

Until run 19 a run that analysed a piece of the movie (match_cuts.excerpt: a film or an episode whose place the sound
check found) and put the channel's song on A2 (SceneProof since night 15, every channel since run 18) could not be
moved onto the movie at its end: the song counted as "doubled audio" with A1. Its 1_edit.xml plays only the piece
(``extras\\media\\raw.mov``) and the link of the movie the move had made (``raw.mp4``) sits next to it, unused -- on
this computer a hard link (no space), in the shared folder a real copy (7.1 GB for a film), and both in the zip.

* ``find(cards, local_root)``: every finished edit folder (the board's projects and this computer's twin copies) whose
  extras\\media holds a RAW video file that no XML of the edit folder plays:
  - **a piece** (the run's decision log has its piece; 1_edit.xml plays that piece and the movie is the unused file):
    the edit is moved onto the movie the way a run does it since run 19 (match_cuts.excerpt.move_run: every RAW clip
    at its frame + the piece's first frame in the movie, A1's sound file made to the movie's frames, checked against
    the movie at three clips), then the piece goes -- the project then holds its RAW once, the whole movie, and
    1_edit.xml plays it;
  - **an unused copy** otherwise: it goes.
* ``check(item)``: the files are what they should be (frame counts), a dry run of the move (made aside and checked,
  nothing swapped in), and no Premiere project plays a file that goes -- the project folder's .prproj files and the
  projects Premiere opened lately (its recent list): only read, never changed. Anything else: kept, and why is said.
* ``remove(items)`` -- only after you said yes: each one checked again first, then done; a project in the shared
  folder gets its zip made again (with the RAW once).

A run folder someone works on now (its lock), the day-12 / day-13 kinds (raw_stereo copies: core.rawfiles /
core.rawkeep) and runs that never finished are left out. Everything here reads disks and runs ffprobe: call it from a
worker thread."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable

from . import paths
from . import publish as PB
from . import rawfiles as RF

RAW_RE = re.compile(r"(?i)^raw(\.movie|_ae|_piece|_premiere)?\.(mp4|mov|m4v|mkv)$")   # the run's RAW video files
DECISIONS_LINES = 400            # the piece's record is among the decision log's first lines (stage "inputs")
Say = Callable[[str], None]


@dataclass
class ExtraRaw:
    """A finished edit folder that holds a RAW file nothing plays."""
    edit: Path                        # the edit (run) folder
    project: Path                     # its project folder
    kind: str                         # "piece": the edit is moved onto the movie, the piece goes | "unused": it goes
    gone: list[Path]                  # the files that go
    plays: Path | None = None         # the RAW file 1_edit.xml plays now
    movie: Path | None = None         # "piece": the movie in the media, which the edit plays afterwards
    record: dict = field(default_factory=dict)   # "piece": the run's piece (its decision log)
    size: int = 0                     # the bytes of the files that go
    grows: int = 0                    # "piece": what A1's sound file grows by (exact silence up to the piece)
    freed: int = 0                    # what it frees (a file another name keeps: nothing) minus ``grows``
    checked: bool = False
    ok: bool = False
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tag: str = ""                     # "this computer" / "shared folder" when a project and its twin are both listed
    linked: bool = False

    @property
    def where(self) -> str:
        return f"{self.project.name}\\{self.edit.name}" + (f" ({self.tag})" if self.tag else "")

    def line(self) -> str:
        """One line of the question, in plain words."""
        if self.kind == "piece":
            m = _size(self.movie)
            return (f"{PB.plain_size(max(0, self.freed))}  {self.where}: 1_edit.xml plays only the piece "
                    f"{self.record.get('where_text', '')} ({self.plays.name if self.plays else '?'}, "
                    f"{PB.plain_size(self.size)}), the whole movie ({self.movie.name if self.movie else '?'}, "
                    f"{PB.plain_size(m)}) is unused next to it -- the edit will play the whole movie, the piece goes"
                    + (f" (A1's sound file grows {PB.plain_size(self.grows)}: silence up to the piece)"
                       if self.grows else ""))
        names = ", ".join(f"{p.name} ({PB.plain_size(_size(p))})" for p in self.gone)
        extra = " -- another name of the same file stays (a hard link): frees nothing" if self.size and not self.freed \
            else ""
        return (f"{PB.plain_size(self.freed)}  {self.where}: {names} -- nothing plays it "
                f"(1_edit.xml plays {self.plays.name if self.plays else 'another file'}){extra}")


def tool() -> tuple[Any, Any, Any]:
    """match_cuts' excerpt, export_xml_edl and stereo modules (the repo's tool folder when this Python lacks it)."""
    try:
        from match_cuts import excerpt, export_xml_edl, stereo
    except ImportError:
        sys.path.append(str(paths.repo() / "tools" / "match_cuts"))
        from match_cuts import excerpt, export_xml_edl, stereo
    return excerpt, export_xml_edl, stereo


def _size(p: Path | None) -> int:
    try:
        return int(os.stat(p).st_size) if p is not None else 0
    except OSError:
        return 0


# ---------------------------------------------------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------------------------------------------------

def _played(xml: Path) -> set[str]:
    """The media file names (lower case) an XML plays."""
    try:
        text = xml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {urllib.parse.unquote(u.strip()).replace("\\", "/").rsplit("/", 1)[-1].lower()
            for u in re.findall(r"<pathurl>(.*?)</pathurl>", text, re.S)}


def _v1_file(xml: Path) -> str | None:
    """The file name V1's RAW clips play (the <file id="file-raw"> with a path)."""
    try:
        text = xml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'<file id="file-raw">.*?<pathurl>(.*?)</pathurl>', text, re.S)
    return urllib.parse.unquote(m.group(1).strip()).replace("\\", "/").rsplit("/", 1)[-1] if m else None


def piece_record(edit: Path) -> dict | None:
    """The piece the run analysed (its decision log's "excerpt" record), None for a run on the whole RAW."""
    log = Path(edit) / "extras" / "debug" / "decisions.jsonl"
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= DECISIONS_LINES:
                    break
                if '"excerpt"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("decision") == "excerpt" and d.get("k0") is not None and d.get("frames"):
                    return d
    except OSError:
        return None
    return None


def _where_text(rec: dict) -> str:
    E, _X, _S = tool()
    try:
        return E.where_text(_excerpt(rec, "movie.mp4", "piece.mov"))
    except Exception:  # noqa: BLE001
        return ""


def _excerpt(rec: dict, movie: str, piece: str) -> Any:
    E, _X, _S = tool()
    return E.Excerpt(piece=str(piece), movie=str(movie), k0=int(rec["k0"]), frames=int(rec["frames"]),
                     fps=str(rec["fps"]), movie_frames=int(rec["movie_frames"]),
                     movie_seconds=float(rec.get("movie_seconds") or 0.0), places=list(rec.get("places") or []),
                     cut_seconds=float(rec.get("cut_seconds") or 0.0), sound=str(rec.get("sound") or ""))


def _one(edit: Path, folder: Path) -> ExtraRaw | None:
    media = edit / "extras" / "media"
    try:
        vids = sorted(f for f in media.iterdir() if f.is_file() and RAW_RE.match(f.name))
    except OSError:
        return None
    if not vids or any((media / n).is_file() for n in RF.SINGLE_NAMES):   # (day 12 / 13: core.rawfiles, rawkeep)
        return None
    played: set[str] = set()
    for xml in PB._edit_xmls(edit):
        played |= _played(xml)
    unused = [f for f in vids if f.name.lower() not in played]
    if not unused:
        return None
    v1 = _v1_file(edit / "1_edit.xml")
    plays = media / v1 if v1 and (media / v1).is_file() else None
    E, _X, _S = tool()
    rec = piece_record(edit)
    if rec is not None and plays is not None and E.run_info(edit) is None:
        rec = dict(rec, where_text=_where_text(rec))
        movies = sorted((f for f in unused if f.suffix.lower() in (".mp4", ".mov", ".m4v")),
                        key=lambda f: (f.stem.lower() != "raw", f.name.lower()))   # (the move's link: raw.<ext>)
        if movies:
            it = ExtraRaw(edit, folder, "piece", [plays], plays=plays, movie=movies[0], record=rec,
                          size=_size(plays))
            fps = Fraction(str(rec["fps"]))
            if (media / "raw_stereo.wav").is_file():
                it.grows = int(round(int(rec["k0"]) * 48000 / float(fps))) * 4
            return it
    return ExtraRaw(edit, folder, "unused", unused, plays=plays, size=sum(_size(f) for f in unused))


def _published_twins(lroot: Path | None) -> dict[str, list[Path]]:
    """This computer's projects by the shared folder they were published to (project.json ``published.to``): a twin
    can have another name there (renamed after its title, or "(2)" when the name was taken)."""
    out: dict[str, list[Path]] = {}
    if lroot is None:
        return out
    try:
        folders = [p for p in lroot.iterdir() if (p / "project.json").is_file()]
    except OSError:
        return out
    for f in folders:
        try:
            to = (json.loads((f / "project.json").read_text(encoding="utf-8")).get("published") or {}).get("to")
        except (OSError, ValueError, AttributeError):
            continue
        if to:
            out.setdefault(PB._norm(to), []).append(f)
    return out


def find(items: Iterable[Any], local_root: str | os.PathLike | None = None) -> list[ExtraRaw]:
    """The finished edit folders of these projects (cards or folders, with their twins under ``local_root``) that hold
    a RAW file nothing plays. A project someone works on now is left out."""
    lroot = Path(local_root) if local_root is not None else None
    twins = _published_twins(lroot)
    out: list[ExtraRaw] = []
    seen: set[str] = set()
    for item in items:
        if getattr(item, "error", "") or getattr(item, "lock", None) is not None:
            continue
        folders = RF._folders(item, lroot)
        folders += [t for t in twins.get(PB._norm(Path(getattr(item, "folder", item))), [])
                    if PB._norm(t) not in {PB._norm(f) for f in folders}]
        for folder in folders:
            if RF._busy(folder):
                continue
            for edit in PB.finished_edits(folder):
                k = PB._norm(edit)
                if k in seen:
                    continue
                seen.add(k)
                it = _one(edit, folder)
                if it is not None:
                    out.append(it)
    _count_freed(out)
    for it in out:                                   # (a twin may have another name: always say where it is)
        it.tag = "this computer" if lroot is not None and RF._under(it.project, lroot) else "shared folder"
    return out


def _count_freed(items: list[ExtraRaw]) -> None:
    """A file's size counts only when every one of its names goes (a hard link elsewhere keeps it), once."""
    names: dict[tuple, list[tuple[ExtraRaw, int, int]]] = {}
    for it in items:
        it.freed = 0
        for f in it.gone:
            try:
                st = os.stat(f)
            except OSError:
                continue
            names.setdefault((st.st_dev, st.st_ino) if st.st_ino else (str(f),), []).append(
                (it, int(st.st_size), int(st.st_nlink)))
    for group in names.values():
        it, size, nlink = group[0]
        if len(group) >= max(1, nlink):
            it.freed += size
        for other, _s, _n in group[1:]:
            if other is not it:
                other.linked = True
    for it in items:
        it.freed -= it.grows


# ---------------------------------------------------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------------------------------------------------

def _moved_by_the_move(edit: Path, xml: Path) -> bool:
    """1_edit.xml and extras\\edit_*.xml: the XMLs match_cuts.excerpt.move_run moves onto the movie."""
    return PB._norm(xml) == PB._norm(edit / "1_edit.xml") or (
        PB._norm(xml.parent) == PB._norm(edit / "extras") and xml.name.lower().startswith("edit_"))


def _plays_it(text: str, project: Path, edit: Path, name: str) -> bool:
    t = urllib.parse.unquote(text).replace("\\", "/").lower()
    return f"{project.name.lower()}/{edit.name.lower()}/extras/media/{name.lower()}" in t


def recent_projects() -> list[Path]:
    """The Premiere projects Premiere opened lately (its recent list): read, never changed."""
    try:
        from .renames import _prefs_projects
        return [p for p in dict.fromkeys(_prefs_projects()) if p.is_file()]
    except Exception:  # noqa: BLE001 - no recent list: the project folder's own projects are still read
        return []


def users(item: ExtraRaw, recent: Iterable[Path] | None = None) -> list[str]:
    """Why a file that would go must stay: an XML of the edit folder or a Premiere project (the project folder's own,
    the recent ones) that plays it."""
    out: list[str] = []
    names = [f.name for f in item.gone]
    for xml in PB._edit_xmls(item.edit):
        if item.kind == "piece" and _moved_by_the_move(item.edit, xml):
            continue                                 # (the move makes it play the movie)
        got = _played(xml)
        for n in names:
            if n.lower() in got:
                out.append(f"{xml.name} plays {n}")
    for pp in list(RF._prprojs(item.project)) + [p for p in (recent if recent is not None else recent_projects())]:
        try:
            text = RF._read(pp)
        except Exception as e:  # noqa: BLE001 - unreadable: it might play it
            out.append(f"I can't read the Premiere project {pp.name} ({type(e).__name__}) -- it might play "
                       f"{names[0]}")
            continue
        for n in names:
            if _plays_it(text, item.project, item.edit, n):
                out.append(f"your Premiere project {pp.name} plays {n} -- kept (relink it in Premiere first)")
    return out


def _dry_run(item: ExtraRaw) -> list[str]:
    """The move onto the movie made aside and checked (nothing swapped in): [] when it would work, else why not."""
    E, X, S = tool()
    rec = item.record
    probs: list[str] = []
    try:
        mf, _md = S._frames(item.movie)
        pf, _pd = S._frames(item.plays)
    except Exception as e:  # noqa: BLE001
        return [f"the RAW files can't be read ({type(e).__name__}: {e})"]
    if int(mf) != int(rec["movie_frames"]):
        probs.append(f"{item.movie.name} has {mf} frames, the movie {rec['movie_frames']}: not the movie")
    if int(pf) != int(rec["frames"]):
        probs.append(f"{item.plays.name} has {pf} frames, the run's piece {rec['frames']}: not that piece")
    wav = item.edit / "extras" / "media" / "raw_stereo.wav"
    wav_frames = None
    if wav.is_file():
        try:
            from match_cuts.edit_media import wav_info
            n = int(wav_info(wav).get("samples") or 0)
        except Exception as e:  # noqa: BLE001
            return probs + [f"raw_stereo.wav can't be read ({type(e).__name__}: {e})"]
        per = 48000 / float(Fraction(str(rec["fps"])))
        if abs(n / per - int(rec["frames"])) > 2:
            probs.append(f"raw_stereo.wav is {n / per:.0f} frames long, not the piece's {rec['frames']}")
        wav_frames = int(rec["k0"]) + int(n // per)
    if probs:
        return probs
    ex = _excerpt(rec, str(item.movie), str(item.plays))
    with tempfile.TemporaryDirectory(prefix="mr_onecopy_") as td:
        tmp = Path(td) / "1_edit.xml"
        try:
            E.shift_xml(item.edit / "1_edit.xml", tmp, ex, item.movie, item.movie.name, wav_frames)
            bad = X.premiere_item_problems(tmp)
            if bad:
                return [f"the moved 1_edit.xml would have problems: {'; '.join(bad[:2])}"]
            chk = E.check_moved(item.edit / "1_edit.xml", tmp, ex, str(item.plays))
            if not chk["ok"]:
                return [f"the movie's frames are not the piece's: {chk['why']}"]
            item.notes.append("moved aside and checked: " + ", ".join(
                f"{r['clip']} movie frame {r['movie_frame']} = piece frame {r['piece_frame']} (SSIM {r['ssim']:.3f})"
                for r in chk["rows"]))
        except Exception as e:  # noqa: BLE001 - said in plain words
            return [f"the move could not be tried ({type(e).__name__}: {e})"]
    return []


def check(item: ExtraRaw, say: Say | None = None, recent: Iterable[Path] | None = None) -> ExtraRaw:
    """Check ``item`` (module docstring): ``item.ok`` when it can be done. Never deletes or changes anything."""
    say = say or (lambda _t: None)
    item.problems, item.notes = [], []
    say(f"checking {item.where}")
    if item.kind == "piece":
        item.problems += _dry_run(item)
    say(f"checking {item.where}: the XMLs and Premiere projects")
    item.problems += users(item, recent)
    item.checked = True
    item.ok = not item.problems
    return item


def plan(items: Iterable[Any], local_root: str | os.PathLike | None = None, say: Say | None = None
         ) -> list[ExtraRaw]:
    """find + check every one (``say``: progress in plain words)."""
    say = say or (lambda _t: None)
    found = find(items, local_root)
    recent = recent_projects() if found else []
    for i, it in enumerate(found, start=1):
        check(it, lambda t, i=i: say(f"One RAW per project ({i} of {len(found)}): {t}"), recent)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# remove (only after you said yes)
# ---------------------------------------------------------------------------------------------------------------------

def _safe(f: Path, item: ExtraRaw) -> str:
    """Why ``f`` must not go ("" when it may): only a RAW video file in the run's own extras\\media."""
    if not RAW_RE.match(f.name):
        return f"{f.name} is not a RAW copy of the run"
    if PB._norm(f.parent) != PB._norm(item.edit / "extras" / "media"):
        return f"{f.name} is not in the run's extras\\media"
    if item.kind == "unused" and item.plays is not None and PB._norm(f) == PB._norm(item.plays):
        return f"1_edit.xml plays {f.name}"
    return ""


def remove(items: Iterable[ExtraRaw], say: Say | None = None) -> tuple[int, list[str]]:
    """Do the checked items -- each checked again first. Returns (bytes freed, problems in plain words)."""
    say = say or (lambda _t: None)
    E, _X, _S = tool()
    freed, errors = 0, []
    todo = [it for it in items if it.checked and it.ok]
    recent = recent_projects() if todo else []
    zips: list[tuple[Path, Path]] = []
    for i, it in enumerate(todo, start=1):
        say(f"One RAW per project ({i} of {len(todo)}): {it.where}")
        again = users(it, recent)
        if again:
            errors.append(f"kept {it.where}: " + "; ".join(again[:2]))
            continue
        if RF._busy(Path(it.project)):
            errors.append(f"kept {it.where}: someone is working on this project now")
            continue
        bad = [w for w in (_safe(f, it) for f in it.gone) if w]
        if bad:
            errors.append(f"kept {it.where}: " + "; ".join(bad))
            continue
        before = {f: os.stat(f) for f in it.gone if f.exists()}
        if it.kind == "piece":
            wav = it.edit / "extras" / "media" / "raw_stereo.wav"
            w0 = _size(wav)
            try:
                E.move_run(_excerpt(it.record, str(it.movie), str(it.plays)), it.edit, it.edit / "extras",
                           it.edit / "extras" / "media", it.plays)
            except Exception as e:  # noqa: BLE001 - nothing changed (move_run's promise)
                errors.append(f"kept {it.where}: the edit could not be moved onto the movie ({e})")
                continue
            freed -= max(0, _size(wav) - w0)
        else:
            for f in it.gone:
                try:
                    f.unlink()
                except FileNotFoundError:
                    continue
                except OSError as e:
                    errors.append(f"could not delete {it.where}\\{f.name}: {e.strerror or e}")
        for f, st in before.items():
            if not f.exists() and st.st_nlink <= 1:
                freed += int(st.st_size)
        zips.append((it.project, it.edit))
    for project, edit in zips:                      # the shared folder's zip: made again, with the RAW once
        try:
            from . import share_zip
            if share_zip.drive_ok(project):
                share_zip.make_later(project, edit)
        except Exception:  # noqa: BLE001 - the zip sweep makes it later anyway
            pass
    return freed, errors
