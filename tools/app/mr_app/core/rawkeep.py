"""Keep the original RAW (day 13) -- the Board's Clean up for runs made before it.

Before day 13 a run whose RAW needed a conversion or had 5.1 sound played ``extras\\media\\raw_stereo.mp4`` on V1 and
A1: the tool's converted picture (H.264 CRF 12 veryfast -- Borat's 3.7 GB x265 became 14 GB) with one stereo track.
Since day 13 a run plays the ORIGINAL RAW and a small stereo sound file (match_cuts.edit_media). For older runs:

* ``find(cards, local_root)``: the finished edit folders (the board's projects and this computer's twin copies) whose
  extras\\media holds raw_stereo.<ext>;
* ``check(item)``: the original RAW must still be on this computer and be the VERY file the run was made from (the
  same content hash as the run's cut list says: source_hash) -- looked for where the run read it, then by its name in
  the project folder, the local projects folder and the shared folder; Premiere must be able to play it frame for
  frame as it is (the run's own probe of it: no VFR, start time, rotation ...; .mp4 / .mov / .m4v; H.264, HEVC, ProRes);
  and no Premiere project -- the project folder's, the Desktop's, the shared folder's (only ever read, never changed)
  -- may play raw_stereo, since deleting it would leave that project offline. Anything else: kept, and why is said;
* only after you said yes: ``replace(items)`` -- each item checked again, then the original hard-linked into
  extras\\media as raw.<ext> (a copy when it is on another drive), raw_stereo.mp4's own sound decoded into
  raw_stereo.wav (sample for sample: the same A1), the tool's OWN XMLs (1_edit.xml, extras\\edit_*.xml) rewritten to
  play the two files -- only their file references change, and they must pass the tool's XML checks as well as before
  -- and only then raw_stereo.mp4 deleted. Something going wrong leaves the run as it was.

Everything here reads disks, hashes films and runs ffmpeg: call it from a worker thread."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import paths
from . import publish as PB
from . import rawfiles as RF

SINGLE_NAMES = RF.SINGLE_NAMES            # raw_stereo.mp4 / raw_stereo.mov
SOUND_NAME = "raw_stereo.wav"
WAV_BYTES_PER_S = 48000 * 2 * 2           # 16-bit stereo 48 kHz
SEARCH_DEPTH = 2                          # folders searched for the original by its name
PROJECT_DEPTH = 3                         # extra places searched for Premiere projects (the Desktop, the shared folder)
Say = Callable[[str], None]


@dataclass
class BigCopy:
    """An older run folder that plays the big raw_stereo copy."""
    edit: Path                        # the edit (run) folder
    project: Path                     # its project folder (project.json)
    single: Path                      # extras\\media\\raw_stereo.<ext>: replaced, then deleted
    size: int = 0                     # its bytes
    seconds: float = 0.0              # the RAW's length (the WAV's size)
    original: Path | None = None      # the original RAW (found and checked)
    original_stat: tuple = ()         # (size, mtime) when it was checked: replace() checks it is still that file
    channels: int = 0                 # the original's sound channels (the XML says them)
    copy_bytes: int = 0               # 0: hard-linked (one drive); else the original's size is copied
    freed: int = 0                    # what replacing frees (raw_stereo - the WAV - a copy; a hard link: nothing)
    checked: bool = False
    ok: bool = False
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    xmls: list[Path] = field(default_factory=list)       # the tool's own XMLs that play raw_stereo (rewritten)
    tag: str = ""

    @property
    def where(self) -> str:
        return f"{self.project.name}\\{self.edit.name}" + (f" ({self.tag})" if self.tag else "")

    @property
    def wav_bytes(self) -> int:
        return int(self.seconds * WAV_BYTES_PER_S)

    def line(self) -> str:
        """'13.0 GB  2026-10-20_borat\\edit: raw_stereo.mp4 (14.0 GB) -> the original RAW (a hard link) + ...'."""
        how = "a copy, it is on another drive" if self.copy_bytes else "a hard link: no space"
        return (f"{PB.plain_size(self.freed)}  {self.where}: {self.single.name} ({PB.plain_size(self.size)}) -> the "
                f"original {self.original.name if self.original else 'RAW'} ({how}) + {SOUND_NAME} "
                f"({PB.plain_size(self.wav_bytes)})")


def tool() -> tuple[Any, Any, Any, Any]:
    """(match_cuts.edit_media, export_xml_edl, conform, common) from the repo's tool folder when this Python lacks it."""
    try:
        from match_cuts import common, conform, edit_media, export_xml_edl
    except ImportError:
        sys.path.append(str(paths.repo() / "tools" / "match_cuts"))
        from match_cuts import common, conform, edit_media, export_xml_edl
    return edit_media, export_xml_edl, conform, common


# ---------------------------------------------------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------------------------------------------------

def _single(edit: Path) -> Path | None:
    media = edit / "extras" / "media"
    return next((media / n for n in SINGLE_NAMES if (media / n).is_file()), None)


def find(items: Iterable[Any], local_root: str | os.PathLike | None = None) -> list[BigCopy]:
    """The finished edit folders of these projects (Cards or folders, with their twins under ``local_root``) that
    play raw_stereo.<ext>. A project someone works on now is left out."""
    lroot = Path(local_root) if local_root is not None else None
    out: list[BigCopy] = []
    seen: set[str] = set()
    for item in items:
        if getattr(item, "error", "") or getattr(item, "lock", None) is not None:
            continue
        for folder in RF._folders(item, lroot):
            if RF._busy(folder):
                continue
            for edit in PB.finished_edits(folder):
                k = PB._norm(edit)
                if k in seen:
                    continue
                seen.add(k)
                single = _single(edit)
                if single is None:
                    continue
                try:
                    size = int(os.stat(single).st_size)
                except OSError:
                    continue
                out.append(BigCopy(edit, folder, single, size))
    names = [it.where for it in out]
    for it in out:
        if names.count(it.where) > 1:
            it.tag = "this computer" if lroot is not None and RF._under(it.project, lroot) else "shared folder"
    return out


# ---------------------------------------------------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------------------------------------------------

def run_raw(edit: Path) -> dict:
    """The run's RAW as its cut list describes it (extras\\cutlist.json 'raw': source_path, source_hash,
    source_issues, frames, fps, size ...); {} without one."""
    try:
        return json.loads((edit / "extras" / "cutlist.json").read_text(encoding="utf-8")).get("raw") or {}
    except (OSError, ValueError):
        return {}


def default_roots(project: Path) -> list[Path]:
    """Where the original RAW is looked for by its name: the project folder, this computer's projects folder (the
    downloads) and the shared folder the project is in (D:\\Shared)."""
    out = [Path(project), paths.projects_root()]
    try:
        out.append(Path(project).resolve().parents[1])            # ...\\Shared\\Projects\\<project> -> ...\\Shared
    except (IndexError, OSError):
        pass
    seen, res = set(), []
    for p in out:
        k = PB._norm(p)
        if k not in seen and Path(p).is_dir():
            seen.add(k)
            res.append(Path(p))
    return res


def project_roots() -> list[Path]:
    """Where Premiere projects that could play an older run's media are looked for besides its project folder: your
    Desktop and the shared folder (D:\\Shared). Only ever read."""
    out = []
    for p in (Path.home() / "Desktop", Path(r"D:\Shared")):
        if p.is_dir():
            out.append(p)
    return out


def _walk(root: Path, depth: int, want: Callable[[str], bool], skip: tuple = ()) -> list[Path]:
    out: list[Path] = []
    base = len(Path(root).parts)
    try:
        for d, dirs, names in os.walk(root):
            if len(Path(d).parts) - base >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if x.lower() not in skip]
            out += [Path(d) / n for n in names if want(n)]
    except OSError:
        pass
    return out


def candidates(raw: dict, roots: Iterable[Path]) -> list[Path]:
    """The files that may be the run's original RAW: where the run read it, then files of its name in ``roots``."""
    src = str(raw.get("source_path") or "")
    out: list[Path] = []
    if src and Path(src).is_file():
        out.append(Path(src))
    name = Path(src.replace("\\", "/")).name.lower() if src else ""
    if name:
        for r in roots:
            for p in _walk(Path(r), SEARCH_DEPTH, lambda n, name=name: n.lower() == name,
                           ("media", "debug", "extras", "auto-save", "adobe premiere pro auto-save")):
                if all(PB._norm(p) != PB._norm(q) for q in out):
                    out.append(p)
    return out


def _probe_lite(path: Path) -> dict:
    """{container, vcodec, pix_fmt, frames, fps, width, height, duration, channels} by ffprobe (no decode)."""
    import subprocess
    _em, _ex, _cf, common = tool()
    r = subprocess.run([common.ffprobe_bin(), "-v", "error", "-show_entries",
                        "format=format_name:stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate,nb_frames,"
                        "duration,channels", "-of", "json", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    d = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    v = next((s for s in d.get("streams") or [] if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams") or [] if s.get("codec_type") == "audio"), {})
    ext = path.suffix.lower().lstrip(".")
    fmt = str((d.get("format") or {}).get("format_name") or "")
    container = ext if ("mp4" in fmt or "mov" in fmt) and ext in ("mp4", "mov", "m4v") else (fmt.split(",")[0] or ext)
    try:
        frames = int(v.get("nb_frames") or 0)
    except ValueError:
        frames = 0
    return {"container": container, "vcodec": str(v.get("codec_name") or ""), "pix_fmt": str(v.get("pix_fmt") or ""),
            "frames": frames, "fps": str(v.get("r_frame_rate") or ""), "width": int(v.get("width") or 0),
            "height": int(v.get("height") or 0), "duration": float(v.get("duration") or 0.0),
            "channels": int(a.get("channels") or 0)}


def users(item: BigCopy, extra_roots: Iterable[Path] | None = None, cache: dict | None = None) -> list[str]:
    """The Premiere projects that play the run's raw_stereo file (the project folder's, and those in ``extra_roots``:
    the Desktop, the shared folder) -- they would go offline without it. Only read."""
    cache = cache if cache is not None else {}
    found = list(RF._prprojs(item.project))
    for r in (project_roots() if extra_roots is None else extra_roots):
        found += _walk(Path(r), PROJECT_DEPTH, lambda n: n.lower().endswith(".prproj"),
                       ("media", "debug", "auto-save", "adobe premiere pro auto-save", "adobe after effects auto-save"))
    out, seen = [], set()
    for pp in found:
        k = PB._norm(pp)
        if k in seen:
            continue
        seen.add(k)
        if k not in cache:
            try:
                cache[k] = RF._read(pp)
            except (OSError, EOFError) as e:
                cache[k] = None
                out.append(f"I can't read {pp.name} ({e}) -- it might play {item.single.name}")
                continue
        text = cache[k]
        if text is not None and plays(text, item):
            out.append(f"your project {pp.name} plays {item.single.name} -- it is kept for that project (relink the "
                       "project in Premiere to the original RAW first, then Clean up can replace it)")
    return out


def plays(text: str, item: BigCopy) -> bool:
    """A project / XML text names THIS run's raw_stereo file: '<project>/<edit>/extras/media/<file>' (every run
    folder is called edit, so the project folder's name is part of it; a path or a file URL, any drive or share)."""
    import urllib.parse
    t = urllib.parse.unquote(text).replace("\\", "/").lower()
    return f"{item.project.name.lower()}/{item.edit.name.lower()}/extras/media/{item.single.name.lower()}" in t


def tool_xmls(item: BigCopy) -> list[Path]:
    """The tool's own XMLs of the edit folder that play the raw_stereo file (they are rewritten)."""
    from match_cuts import stereo
    key = "media/" + item.single.name.lower()
    return [x for x in PB._edit_xmls(item.edit) if key in stereo.xml_media_names(x)]


def check(item: BigCopy, say: Say | None = None, roots: Iterable[Path] | None = None,
          extra_roots: Iterable[Path] | None = None, cache: dict | None = None) -> BigCopy:
    """Check ``item`` (module docstring): ``item.ok`` when its raw_stereo can be replaced. Changes nothing."""
    say = say or (lambda _t: None)
    em, _ex, _cf, common = tool()
    item.problems, item.notes = [], []
    raw = run_raw(item.edit)
    item.seconds = float(raw.get("duration_s") or 0.0)
    if not raw:
        item.problems.append("the run has no extras\\cutlist.json: I can't tell which RAW it was made from")
    picture = [i for i in (raw.get("source_issues") or []) if em.issue_kind(str(i)) == "picture"]
    if raw and picture:
        item.problems.append("Premiere can't play the original RAW frame for frame as it is ("
                             + "; ".join(str(i) for i in picture[:3]) + ") -- the converted copy stays")
    if raw and not item.problems:
        say(f"looking for the original RAW of {item.where}")
        want = str(raw.get("source_hash") or "")
        for cand in candidates(raw, roots if roots is not None else default_roots(item.project)):
            say(f"checking {item.where}: {cand.name}")
            info = _probe_lite(cand)
            if int(raw.get("frames") or 0) and info["frames"] and info["frames"] != int(raw["frames"]):
                continue
            try:
                same = bool(want) and common.file_hash(cand) == want
            except OSError:
                continue
            if not same:
                continue
            if info["container"] not in em.PREMIERE_CONTAINERS or not em.codec_ok(info["vcodec"], info["pix_fmt"]):
                item.problems.append(f"the original {cand.name} ({info['vcodec']} {info['pix_fmt']}, "
                                     f".{info['container']}) is not a file Premiere plays as it is -- the converted "
                                     "copy stays")
                break
            st = os.stat(cand)
            item.original, item.original_stat, item.channels = cand, (st.st_size, st.st_mtime_ns), info["channels"]
            break
        if item.original is None and not item.problems:
            item.problems.append("the original RAW is not on this computer any more (or not the same file) -- "
                                 f"{item.single.name} is the only copy of the picture: kept")
    if item.original is not None:
        say(f"checking {item.where}: the XMLs and Premiere projects")
        item.problems += users(item, extra_roots, cache)
        item.xmls = tool_xmls(item)
        if not item.xmls:
            item.problems.append(f"no XML of the edit plays {item.single.name}: nothing to replace")
        media = item.single.parent
        dst = media / ("raw" + (item.original.suffix.lower() or ".mp4"))
        if dst.exists():
            try:
                if not os.path.samefile(dst, item.original):
                    item.problems.append(f"extras\\media\\{dst.name} is another file -- run Clean up again after it "
                                         "is gone")
            except OSError:
                item.problems.append(f"extras\\media\\{dst.name} cannot be read")
        item.copy_bytes = 0 if dst.exists() or PB.same_volume(item.original, media) else int(item.original_stat[0])
    try:
        nlink = os.stat(item.single).st_nlink
    except OSError:
        nlink = 1
    item.freed = max(0, (item.size if nlink <= 1 else 0) - item.wav_bytes - item.copy_bytes)
    if not item.problems and item.freed <= 0:
        item.problems.append("replacing it would not free any space")
    item.checked = True
    item.ok = not item.problems
    if item.ok:
        item.notes.append(f"the original {item.original.name} is the very file the run was made from")
    return item


def plan(items: Iterable[Any], local_root: str | os.PathLike | None = None, say: Say | None = None,
         roots: Iterable[Path] | None = None, extra_roots: Iterable[Path] | None = None) -> list[BigCopy]:
    """find + check every one (``say``: progress in plain words)."""
    say = say or (lambda _t: None)
    found = find(items, local_root)
    cache: dict = {}
    for i, it in enumerate(found, start=1):
        check(it, lambda t, i=i: say(f"Keep the original RAW ({i} of {len(found)}): {t}"), roots, extra_roots, cache)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# replace (only after you said yes)
# ---------------------------------------------------------------------------------------------------------------------

def _xml_checks(xml: Path) -> dict[str, int]:
    """How many problems each of the tool's hard XML checks finds (ITEM, LINK, SYNC incl. the sound file, AUDIO)."""
    _em, X, _cf, _c = tool()
    out: dict[str, int] = {}
    for name, fn in (("item", X.premiere_item_problems), ("link", lambda p: X.premiere_link_problems(p)[0]),
                     ("sync", lambda p: X.premiere_sync_problems(p)[0]), ("audio", X.premiere_audio_problems)):
        try:
            out[name] = len(fn(xml))
        except Exception:  # noqa: BLE001 - a check that cannot run: counted as a problem
            out[name] = 10 ** 6
    return out


def replace_one(item: BigCopy, extra_roots: Iterable[Path] | None = None) -> tuple[int, str]:
    """Replace one checked item (module docstring). Returns (bytes freed, '' or why it was kept as it was)."""
    em, X, cf, _c = tool()
    if not (item.checked and item.ok and item.original is not None):
        return 0, "not checked"
    if not item.single.is_file():
        return 0, f"{item.single.name} is not there any more"
    try:
        st = os.stat(item.original)
    except OSError:
        return 0, f"the original {item.original.name} is not there any more"
    if (st.st_size, st.st_mtime_ns) != tuple(item.original_stat):
        return 0, f"the original {item.original.name} changed since it was checked"
    again = users(item, extra_roots)
    if again:
        return 0, "; ".join(again)
    if RF._busy(Path(item.project)):
        return 0, "someone is working on this project now"
    media = item.single.parent
    dst = media / ("raw" + (item.original.suffix.lower() or ".mp4"))
    wav = media / SOUND_NAME
    made: list[Path] = []
    try:
        if not dst.exists():
            cf._link_or_copy(item.original, dst)
            made.append(dst)
        if not wav.exists():
            em.sound_from_stereo_copy(item.single, wav)
            made.append(wav)
        new_texts: dict[Path, str] = {}
        for xml in item.xmls:
            text = em.retarget_xml(xml, item.single.name, dst, wav, item.channels)
            tmp = xml.with_name(xml.name + ".rawkeep.tmp")
            tmp.write_text(text, encoding="utf-8")
            try:
                before, after = _xml_checks(xml), _xml_checks(tmp)
                worse = [k for k in after if after[k] > before.get(k, 0)]
                snd = X.sound_file_problems(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            if worse or snd:
                raise RuntimeError(f"{xml.name} would not pass the XML checks as before ("
                                   + ", ".join(worse + snd[:2]) + ")")
            new_texts[xml] = text
    except Exception as e:  # noqa: BLE001 - the run stays as it was
        for p in made:
            try:
                p.unlink()
            except OSError:
                pass
        return 0, f"kept as it was: {type(e).__name__}: {e}"
    for xml, text in new_texts.items():
        tmp = xml.with_name(xml.name + ".rawkeep.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, xml)
    try:
        s = os.stat(item.single)
        item.single.unlink()
    except OSError as e:
        return 0, f"the XMLs play the original now, but {item.single.name} could not be deleted: {e.strerror or e}"
    try:
        with (item.edit / "extras" / "report.md").open("a", encoding="utf-8") as f:
            f.write(f"\n\n_Clean up (day 13): {item.single.name} replaced by the original RAW (extras/media/{dst.name}"
                    f") on V1 and {SOUND_NAME} on A1 -- the same frames and sound; {', '.join(x.name for x in item.xmls)}"
                    " now play the two files._\n")
    except OSError:
        pass
    wav_size = wav.stat().st_size if wav.exists() else 0
    copied = dst.stat().st_size if dst in made and dst.stat().st_nlink <= 1 else 0
    return max(0, (int(s.st_size) if s.st_nlink <= 1 else 0) - int(wav_size) - int(copied)), ""


def replace(items: Iterable[BigCopy], say: Say | None = None,
            extra_roots: Iterable[Path] | None = None) -> tuple[int, list[str]]:
    """Replace every checked item (each checked again first). Returns (bytes freed, problems in plain words)."""
    say = say or (lambda _t: None)
    todo = [it for it in items if it.checked and it.ok]
    freed, errors = 0, []
    for i, it in enumerate(todo, start=1):
        say(f"Keep the original RAW ({i} of {len(todo)}): {it.where}")
        got, why = replace_one(it, extra_roots)
        freed += got
        if why:
            errors.append(f"{it.where}: {why}")
    return freed, errors
