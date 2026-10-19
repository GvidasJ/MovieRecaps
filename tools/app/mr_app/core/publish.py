"""PUBLISH (night 11, section 2): a finished project goes to the shared folder -- Settings' "Save projects to": the
PC's D:\\Shared\\Projects, \\\\192.168.101.11\\Shared\\Projects from the laptop -- so both computers see it on the
board. Only the finished parts go: every finished edit folder (``edit``, ``edit-2`` ...: a run folder whose
extras\\report.md exists) with everything in it, template.png, logs\\, title.txt and, last, project.json. The
downloaded originals and the working files stay on this computer's disk (fast), and the local copy is never deleted.

* Each file is a hard link when the shared folder is on the same volume (instant, no space), else a copy: in 8 MB
  chunks to a .part file, its size checked, then moved into place (os.replace) with its times kept. A file already
  there with the same size and time is skipped, so publishing again is cheap; a file that is newer there (changed
  on the other computer since) is kept. Nothing in the shared folder is ever deleted.
* 1_edit.xml (also extras\\edit_*.xml and the copy Premiere opens, <project>_edit.xml) is written anew with its media
  paths pointing into the published copy's extras\\media -- as THIS computer sees the shared folder, with match_cuts'
  own path -> URL function (file://192.168.101.11/Shared/... for the network path). Only those <pathurl> texts
  change; every other byte stays. ``relink_edit`` does the same for the other computer, which sees the folder under
  another path (D:\\Shared\\... on the PC), e.g. before Premiere opens it there.
* project.json goes in last, so a half-published folder never looks like a project. When the shared folder has one
  already (published before -- the other computer may have moved the card since) the two are merged and the shared
  board state is never lost. Both copies record ``published: {to, at, computer}``. title.txt is written from the
  merged titles (the upload title when set, else the headline), not copied: a title.txt edited by hand on either
  side goes into its project.json first, and of two different titles the one changed last wins.
* The shared folder cannot be used (the PC asleep, no network, this Windows user not let in -- ``why_unreachable``
  says which): ``SharedUnreachable``; ``queue_pending`` keeps the project in <local root>\\_pending_publish.json and
  ``retry_pending`` publishes it once the folder answers again (``publish_or_queue`` does both).
* While it publishes, the shared copy holds the project's work lock (locks.py): two computers never write one
  project at once.

Only Python's own file operations are used (UNC paths work; nothing does ``cd`` into one). Everything here except
``reachable`` (it never takes longer than its timeout) may take long on a network folder: call it from a worker
thread. ``progress(fraction 0..1, text)``; ``cancel``: a threading.Event (``Cancelled`` is raised, the partial file
removed, project.json not written)."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape as _xml_escape
from xml.sax.saxutils import unescape as _xml_unescape

from . import locks
from . import machine as M
from . import paths
from . import projects as P
from .space import existing

Progress = Callable[[float, str], None]

REACH_TIMEOUT_S = 5.0             # reachable(): a sleeping PC makes a network path wait 20 s and more
CHUNK = 8 << 20
MTIME_SLACK_S = 2.0               # the same time within this (FAT / exFAT keep file times to 2 s)
PENDING_FILE = "_pending_publish.json"
EDIT_RE = re.compile(r"(?i)^(edit|cuts)(-\d+)?$")   # a project's run folders: edit, edit-2 ... (flow.EDIT_NAME);
#                                                    day 12: Just cuts' cuts, cuts-2 ... (flow.CUTS_NAME) too
REPORT = ("extras", "report.md")                  # a finished run has it
TITLE_TXT = "title.txt"
TOP_FILES = ("template.png",)                     # title.txt is written from the merged titles (with project.json)
TOP_DIRS = ("logs",)
SKIP_RE = re.compile(r"(?i)(\.part|\.tmp|\.lock|\.app\.stdout)$|^(thumbs\.db|desktop\.ini)$")   # never published
_PATHURL = re.compile(r"(<pathurl>)(.*?)(</pathurl>)", re.S)
_ENTITIES = {"&quot;": '"', "&apos;": "'"}


class PublishError(Exception):
    """Publishing did not work; the message says why in plain words."""


class SharedUnreachable(PublishError):
    """The shared folder does not answer (the PC asleep or off, no network): the project stays here for now."""


class NothingToPublish(PublishError):
    """The project has no finished edit yet."""


class Cancelled(Exception):
    """Stop was pressed (the cancel event was set)."""


@dataclass
class PublishResult:
    """What publish() did."""
    dest: Path
    files: int = 0                # files the published copy holds from this project (placed now or there already)
    linked: int = 0               # hard links (same volume: no copy, no space)
    copied: int = 0
    rewritten: int = 0            # XMLs written anew, their media paths pointing into the published copy
    skipped: int = 0              # there already: the same size and time
    kept: int = 0                 # newer in the shared folder (changed there since): kept, not overwritten
    bytes: int = 0                # bytes copied (links cost none)
    edits: list[str] = field(default_factory=list)
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """'published to D:\\Shared\\Projects\\x (edit): 41 files -- 39 linked, 1 written anew, 1 already there'."""
        parts = ((self.linked, "linked"), (self.copied, f"copied ({plain_size(self.bytes)})"),
                 (self.rewritten, "written anew"), (self.skipped, "already there"), (self.kept, "newer there, kept"))
        bits = [f"{n} {what}" for n, what in parts if n]
        edits = f" ({', '.join(self.edits)})" if self.edits else ""
        return f"published to {self.dest}{edits}: {self.files} files" + (f" -- {', '.join(bits)}" if bits else "")


def plain_size(n: float | int | None) -> str:
    """1288490189 -> '1.2 GB', 3400000 -> '3.2 MB', 512 -> '512 bytes' (the sizes Explorer shows)."""
    n = float(n or 0)
    for unit, k in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= k:
            v = n / k
            return f"{v:.1f} {unit}" if v < 100 else f"{v:.0f} {unit}"
    return f"{int(n)} bytes" if n != 1 else "1 byte"


def _clock(s: float) -> str:
    s = int(round(max(0.0, s)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------------------------------------------------
# reachable, never hanging
# ---------------------------------------------------------------------------------------------------------------------

def call_with_timeout(fn: Callable[..., Any], timeout_s: float, *args: Any, **kwargs: Any) -> Any:
    """``fn(*args)`` in a daemon thread: its value (its exception raised here), or TimeoutError after
    ``timeout_s`` -- the thread is then left to finish on its own (a file call on a dead network path can block 20 s
    and more, and cannot be stopped)."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - handed to the caller
            box["error"] = e
    t = threading.Thread(target=run, daemon=True, name=f"timeout {getattr(fn, '__name__', 'call')}")
    t.start()
    t.join(max(0.0, float(timeout_s)))
    if t.is_alive():
        raise TimeoutError(f"no answer within {timeout_s:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


_DENIED = ("this Windows user may not open {p} (access denied: the share lets another account in) -- open it once "
           "in Explorer, sign in with that account and tick 'Remember', or allow this user in the share's settings")


def _stat(p: Path) -> os.stat_result:
    return os.stat(p)


def _why_not(root: Path) -> str:
    """Why the folder (or the folder it goes in: it is made at the first publish) cannot be used, in plain words;
    "" when it can."""
    for p in (root, root.parent):
        try:
            st = _stat(p)
        except PermissionError:
            return _DENIED.format(p=p)
        except OSError as e:
            win = getattr(e, "winerror", None)
            if win in (51, 53, 67):                             # Python calls these FileNotFoundError too
                return f"{p} is not found on the network (the PC off or asleep, or the share's name changed)"
            if win in (1222, 1231, 1232):
                return f"the network does not reach {p} (no network on this computer?)"
            if isinstance(e, FileNotFoundError):
                continue
            return f"{p} cannot be opened: {e.strerror or e}"
        if stat.S_ISDIR(st.st_mode):
            return ""
        return f"{p} is a file, not a folder"
    return f"{root.parent} is not there"


def _answers(root: Path) -> bool:
    """The folder is there, or the folder it goes in (it is made at the first publish)."""
    return _why_not(root) == ""


def reachable(root: str | os.PathLike, timeout_s: float = REACH_TIMEOUT_S) -> bool:
    """True when the shared folder (or the folder it goes in) answers within ``timeout_s``; never waits longer --
    a network path to a sleeping PC can block for 20 s and more."""
    try:
        return bool(call_with_timeout(_answers, timeout_s, Path(root)))
    except Exception:  # noqa: BLE001 - TimeoutError, a broken path ...: not reachable
        return False


def why_unreachable(root: str | os.PathLike, timeout_s: float = REACH_TIMEOUT_S) -> str:
    """Why the shared folder cannot be used now, in plain words ("" when it can): not there, access denied for this
    Windows user, not found on the network, no answer within ``timeout_s`` (never waits longer)."""
    try:
        return str(call_with_timeout(_why_not, timeout_s, Path(root)) or "")
    except TimeoutError:
        return f"{root} does not answer (the PC asleep or off, or no network)"
    except Exception as e:  # noqa: BLE001 - a broken path
        return f"{root} cannot be opened: {e}"


# ---------------------------------------------------------------------------------------------------------------------
# the media paths in the XML
# ---------------------------------------------------------------------------------------------------------------------

def file_url(path: str) -> str:
    """The <pathurl> of a file: match_cuts' own function (export_xml_edl._file_url), so a published edit's URLs are
    exactly what the tool writes -- file://localhost/D%3A/... for a drive, file://192.168.101.11/Shared/... for a
    network path (the server stays the URL's host, which Premiere opens)."""
    try:
        from match_cuts.export_xml_edl import _file_url
    except ImportError:                          # not on sys.path in this Python: the tool's folder
        sys.path.append(str(paths.repo() / "tools" / "match_cuts"))
        from match_cuts.export_xml_edl import _file_url
    return _file_url(path)


def url_to_path(url: str) -> str | None:
    """A file URL back to a Windows path: file://localhost/C%3A/x -> C:\\x, file://server/share/x ->
    \\\\server\\share\\x; None for anything that is not a file URL."""
    u = (url or "").strip()
    if not u.lower().startswith("file:"):
        return None
    sp = urllib.parse.urlsplit(u)
    host, p = sp.netloc, urllib.parse.unquote(sp.path)
    if host and host.lower() != "localhost":
        return "\\\\" + host + p.replace("/", "\\")
    if p.startswith("//"):                               # file:////server/share/x
        return "\\\\" + p.lstrip("/").replace("/", "\\")
    if re.match(r"^/[A-Za-z]:", p):
        p = p[1:]
    return os.path.normpath(p)


def _media_rel(path: str, edit: Path) -> str | None:
    """The part of ``path`` inside the edit folder's extras\\media ('raw.mp4'), or None. Also when the project was
    moved since the run (or is seen from the other computer): '...\\<edit>\\extras\\media\\<file>' anywhere, with
    that file in this edit's media."""
    media = os.path.abspath(edit / "extras" / "media")
    p = os.path.normpath(path)
    if os.path.normcase(p).startswith(os.path.normcase(media) + os.sep):
        return os.path.relpath(p, media)
    parts = Path(p).parts
    low = [x.lower() for x in parts]
    want = [edit.name.lower(), "extras", "media"]
    for i in range(len(low) - 4, -1, -1):
        if low[i:i + 3] == want:
            rel = os.path.join(*parts[i + 3:])
            return rel if (Path(media) / rel).is_file() else None
    return None


def rewrite_xml(text: str, src_edit: Path, dest_edit: Path) -> tuple[str, int, list[str]]:
    """(the XML text with every <pathurl> into ``src_edit``'s extras\\media pointing into ``dest_edit``'s, how many
    changed, the media paths outside the edit folder). Only those texts change; everything else stays as it is."""
    dest_media = os.path.abspath(dest_edit / "extras" / "media")
    changed, outside = [0], []

    def sub(m: re.Match) -> str:
        url = _xml_unescape(m.group(2), _ENTITIES)
        path = url_to_path(url)
        if path is None:
            return m.group(0)
        rel = _media_rel(path, src_edit)
        if rel is None:
            if path not in outside:
                outside.append(path)
            return m.group(0)
        new = file_url(os.path.join(dest_media, rel))
        if new == url:
            return m.group(0)
        changed[0] += 1
        return m.group(1) + _xml_escape(new) + m.group(3)
    return _PATHURL.sub(sub, text), changed[0], outside


def _rewritten(xml: Path, src_edit: Path, dest_edit: Path) -> tuple[bytes | None, int, list[str]]:
    """(the new bytes of an XML file, or None when nothing in it changes; how many paths changed; paths outside)."""
    raw = xml.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, 0, []
    new, n, outside = rewrite_xml(text, src_edit, dest_edit)
    return (new.encode("utf-8") if n else None), n, outside


def _edit_xmls(edit: Path) -> list[Path]:
    """The XMLs Premiere imports from an edit folder: 1_edit.xml and its named copy (<project>_edit.xml) at the top,
    extras\\edit_*fps.xml (and any other XML) in extras."""
    out: list[Path] = []
    for d in (edit, edit / "extras"):
        try:
            out += sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".xml")
        except OSError:
            continue
    return out


def relink_edit(edit_folder: str | os.PathLike) -> int:
    """Make an edit folder's XMLs point at its own extras\\media where THIS computer sees it now (the other computer
    published it under its own path for the shared folder, or the folder was moved). The files' times are kept.
    Returns how many media paths changed (0: they were right)."""
    edit = Path(edit_folder)
    total = 0
    for xml in _edit_xmls(edit):
        try:
            new, n, _out = _rewritten(xml, edit, edit)
            if new is None:
                continue
            st = os.stat(xml)
            _write_bytes(xml, new, st)
            total += n
        except OSError:
            continue
    return total


# ---------------------------------------------------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------------------------------------------------

def finished_edits(project_folder: str | os.PathLike) -> list[Path]:
    """The project's finished edit folders (edit, edit-2 ...: extras\\report.md is there), in name order."""
    folder = Path(project_folder)
    try:
        subs = [p for p in folder.iterdir() if p.is_dir() and EDIT_RE.match(p.name)]
    except OSError:
        return []
    key = lambda p: (p.name.split("-")[0].lower(), int(p.name.split("-")[1]) if "-" in p.name else 1)  # noqa: E731
    return sorted((p for p in subs if p.joinpath(*REPORT).is_file()), key=key)


def same_volume(a: Path, b: Path) -> bool:
    """True when a hard link from ``a`` into ``b`` can work: one drive (the same letter and volume) or one network
    share."""
    da, db = (os.path.splitdrive(os.path.abspath(x))[0].lower() for x in (a, b))
    if not da or da != db:
        return False
    try:
        return os.stat(existing(a)).st_dev == os.stat(existing(b)).st_dev
    except OSError:
        return False


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _inside(child: Path, parent: Path) -> bool:
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    return c.startswith(p + os.sep)


def _replace(tmp: Path, dest: Path, tries: int = 40) -> None:
    """os.replace, tried again while a reader (Premiere, Explorer's preview, the other computer) has the old file."""
    for k in range(tries):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.05)


def _remove(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _write_bytes(dest: Path, data: bytes, times: os.stat_result | None = None) -> None:
    """A new file (never a link to another one): a temp file next to it, flushed, its times set, moved over."""
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if times is not None:
            os.utime(tmp, ns=(times.st_atime_ns, times.st_mtime_ns))
        _replace(tmp, dest)
    finally:
        _remove(tmp)


def _link(src: Path, dest: Path) -> None:
    """A hard link at ``dest`` (an older file there is replaced atomically)."""
    if not os.path.lexists(dest):
        os.link(src, dest)
        return
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.link.tmp")
    _remove(tmp)
    os.link(src, tmp)
    try:
        _replace(tmp, dest)
    finally:
        _remove(tmp)


def _copy(src: Path, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event | None) -> int:
    """Copy in chunks to <dest>.part, check its size, keep the times, move it into place. Returns the bytes."""
    st = os.stat(src)
    part = dest.with_name(dest.name + ".part")
    done = 0
    try:
        with open(src, "rb") as a, open(part, "wb") as b:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = a.read(CHUNK)
                if not chunk:
                    break
                b.write(chunk)
                done += len(chunk)
                on_bytes(len(chunk))
            b.flush()
            os.fsync(b.fileno())
        got = os.stat(part).st_size
        if got != st.st_size or done != st.st_size:
            raise PublishError(f"the copy of {src.name} came out {got} bytes instead of {st.st_size} (the file "
                               "changed while it was copied, or the network dropped): publish again")
        os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
        _replace(part, dest)
    except BaseException:
        _remove(part)
        raise
    return done


@dataclass
class _Item:
    src: Path
    dest: Path
    rel: str
    size: int
    mtime: float
    stat: os.stat_result
    edit: Path | None = None          # the edit folder an XML belongs to (its media paths are rewritten)


def _walk(top: Path, src_root: Path, dest_root: Path, edit: Path | None, xmls: set[str]) -> list[_Item]:
    out: list[_Item] = []
    for d, dirs, names in os.walk(top):
        dirs.sort()
        for n in sorted(names):
            if SKIP_RE.search(n):
                continue
            f = Path(d) / n
            try:
                st = os.stat(f)
            except FileNotFoundError:
                continue                                        # gone meanwhile (a temp file)
            rel = os.path.relpath(f, src_root)
            out.append(_Item(f, dest_root / rel, rel, st.st_size, st.st_mtime, st,
                             edit if os.path.normcase(str(f)) in xmls else None))
    return out


def _plan(src: Path, dest: Path, edits: list[Path]) -> list[_Item]:
    """The files to publish: the finished edit folders, logs\\, template.png -- the XMLs last (title.txt and
    project.json come after them, from _write_project_json)."""
    items: list[_Item] = []
    for e in edits:
        xmls = {os.path.normcase(str(x)) for x in _edit_xmls(e)}
        items += _walk(e, src, dest, e, xmls)
    for d in TOP_DIRS:
        if (src / d).is_dir():
            items += _walk(src / d, src, dest, None, set())
    for n in TOP_FILES:
        f = src / n
        if f.is_file():
            st = os.stat(f)
            items.append(_Item(f, dest / n, n, st.st_size, st.st_mtime, st))
    return sorted(items, key=lambda it: it.edit is not None)      # stable: the media before the XMLs using them


# ---------------------------------------------------------------------------------------------------------------------
# project.json, merged
# ---------------------------------------------------------------------------------------------------------------------

def _ts(text: Any) -> float | None:
    try:
        d = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.astimezone()).timestamp()


def _step_time(step: Any) -> float | None:
    if not isinstance(step, dict):
        return None
    return _ts(step.get("finished")) or _ts(step.get("started"))


def merge_board(local: Any, shared: Any) -> dict:
    """The board state of both copies: the shared copy's wins (the other computer may have moved the card there);
    the dates of both are kept, and every "not the same video" answer."""
    lb, sb = (b if isinstance(b, dict) else {} for b in (local, shared))
    out = {**lb, **sb}
    dates = {**(lb.get("dates") or {}), **(sb.get("dates") or {})}
    if dates:
        out["dates"] = dates
    seen: list = []
    for x in list(lb.get("not_same") or []) + list(sb.get("not_same") or []):
        if x not in seen:
            seen.append(x)
    if seen:
        out["not_same"] = seen
    return out


def _ids(d: dict) -> tuple:
    c, r = d.get("competitor") or {}, d.get("raw") or {}
    return (c.get("id") or c.get("url"), r.get("id") or r.get("url") or r.get("source_file"))


def same_project(a: dict | None, b: dict | None) -> bool:
    """Two project.json describe one project: made at the same second (a copy of the folder, or published before);
    without that stamp, the same competitor and RAW. Two computers can make two projects with one folder name
    (the same day, the same short name): those are never merged."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    ta, tb = _ts(a.get("created")), _ts(b.get("created"))
    if ta is not None and tb is not None:
        return abs(ta - tb) < 1.0
    return any(_ids(a)) and _ids(a) == _ids(b)


def _read_or_none(path: Path) -> dict | None:
    try:
        return P.read_json(path, tries=5)
    except (OSError, ValueError):
        return None


def destination(project_folder: str | os.PathLike, root: str | os.PathLike, local: dict | None = None) -> Path:
    """Where the project goes in ``root``: where it was published before; else <root>\\<its folder name> -- unless
    that folder holds another project (the other computer's, with the same name): then <name>-2, -3 ... A folder
    there without project.json is a publish that stopped half-way: it is continued."""
    src, root = Path(project_folder), Path(root)
    local = local if local is not None else (_read_or_none(src / P.FILE) or {})
    before = (local.get("published") or {}).get("to") if isinstance(local.get("published"), dict) else None
    if before and _norm(Path(before).parent) == _norm(root):
        there = _read_or_none(Path(before) / P.FILE)
        if there is None or same_project(local, there):
            return Path(before)
    k = 1
    while True:
        cand = root / (src.name if k == 1 else f"{src.name}-{k}")
        if not cand.exists() or not (cand / P.FILE).is_file():
            return cand
        if same_project(local, _read_or_none(cand / P.FILE)):
            return cand
        k += 1


def merge_project(local: dict, shared: dict | None) -> dict:
    """The published project.json: the local one (it has the newest edit) merged into the one the shared folder has
    from an earlier publish -- the shared board state is kept, fields only the shared copy has are kept, each step is
    the one that finished last, and for the other fields the copy changed last (its "updated") wins."""
    out = json.loads(json.dumps(local, ensure_ascii=False))
    if not isinstance(shared, dict):
        return out
    shared_newer = (_ts(shared.get("updated")) or 0.0) > (_ts(local.get("updated")) or 0.0)
    for k, v in shared.items():
        if k in ("updated", "name", "published"):
            continue
        if k not in out:
            out[k] = v
        elif k == "board":
            out[k] = merge_board(out.get(k), v)
        elif k == "steps" and isinstance(v, dict) and isinstance(out.get(k), dict):
            steps = dict(out[k])
            for name, st in v.items():
                mine = steps.get(name)
                if name not in steps or (_step_time(st) or 0.0) > (_step_time(mine) or 0.0):
                    steps[name] = st
            out[k] = steps
        elif k == "created":
            a, b = _ts(out.get(k)), _ts(v)
            if b is not None and (a is None or b < a):
                out[k] = v
        elif shared_newer:
            out[k] = v
    return out


def _moved_paths(data: dict, src: Path, dest: Path) -> None:
    """steps.edit.run_dir (and other paths into the project folder in the steps) -> the published copy's."""
    s, d = os.path.normcase(os.path.abspath(src)), os.path.abspath(dest)
    for st in (data.get("steps") or {}).values():
        if not isinstance(st, dict):
            continue
        for k in ("run_dir",):
            v = st.get(k)
            if isinstance(v, str) and os.path.normcase(os.path.abspath(v)).startswith(s + os.sep):
                st[k] = os.path.join(d, os.path.relpath(os.path.abspath(v), os.path.abspath(src)))


def read_title_txt(folder: str | os.PathLike) -> str | None:
    """title.txt of a project as you typed it (line breaks kept; a byte-order mark and the last line break an
    editor adds are dropped); None when there is none or it is empty."""
    try:
        t = (Path(folder) / TITLE_TXT).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    t = t.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return t if t.strip() else None


def _set(value: Any) -> bool:
    return bool(str(value or "").strip())


def title_txt_text(data: dict) -> str:
    """What title.txt holds: the upload title (project.json "upload_title", the title the Short is uploaded with)
    when it is set, else the headline (project.json "title", the template's)."""
    up = data.get("upload_title")
    return str(up) if _set(up) else str(data.get("title") or "")


def fold_title_txt(folder: str | os.PathLike, data: dict) -> float | None:
    """A title.txt you edited by hand goes into ``data``: into "upload_title" when one is set, else into "title"
    (title.txt holds that one). Returns title.txt's time (None when there is none)."""
    t = read_title_txt(folder)
    if t is None:
        return None
    key = "upload_title" if _set(data.get("upload_title")) else "title"
    if t != str(data.get(key) or ""):
        data[key] = t
    try:
        return os.stat(Path(folder) / TITLE_TXT).st_mtime
    except OSError:
        return None


def write_title_txt(folder: str | os.PathLike, data: dict) -> bool:
    """title.txt = ``title_txt_text(data)``, UTF-8, exactly as typed (a new file, never a link); nothing is made
    when the text is empty and there is no title.txt. True when it was written."""
    p = Path(folder) / TITLE_TXT
    text = title_txt_text(data)
    body = text.encode("utf-8")
    try:
        if p.read_bytes() == body:
            return False
    except FileNotFoundError:
        if not text.strip():
            return False
    _write_bytes(p, body)
    return True


def _merge_titles(data: dict, local: dict, shared: dict, local_t: float | None, shared_t: float | None) -> None:
    """The headline and the upload title after a merge: a title set on one side only is kept; set differently on
    both, the side whose title.txt changed last wins (every title change in the app rewrites title.txt)."""
    shared_newer = shared_t is not None and (local_t is None or shared_t > local_t + MTIME_SLACK_S)
    for k in ("title", "upload_title"):
        lv, sv = local.get(k), shared.get(k)
        if not _set(sv):
            if lv is not None:
                data[k] = lv
        elif not _set(lv):
            data[k] = sv
        elif lv != sv and (local_t is not None or shared_t is not None):
            data[k] = sv if shared_newer else lv


def _write_project_json(src: Path, dest: Path, published: dict, res: PublishResult) -> dict:
    """The shared copy's title.txt and project.json, merged with what is there (under projects.py's lock): the last
    files. title.txt is not copied but written from the merged titles (title_txt_text), after a title.txt edited by
    hand on either side was taken into its project.json."""
    try:
        local = P.read_json(src / P.FILE)
    except ValueError as e:
        raise PublishError(f"the project's project.json cannot be read: {e}") from e
    _moved_paths(local, src, dest)
    local_t = fold_title_txt(src, local)
    path = dest / P.FILE
    with P._locked(path):
        shared = None
        if path.is_file():
            try:
                shared = P.read_json(path, tries=10)
            except ValueError:
                bad = path.with_name(P.FILE + ".bad")
                try:
                    os.replace(path, bad)
                    res.notes.append(f"the shared copy's project.json could not be read: kept as {bad.name}")
                except OSError:
                    pass
        data = merge_project(local, shared)
        if isinstance(shared, dict):
            shared_t = fold_title_txt(dest, shared)
            _merge_titles(data, local, shared, local_t, shared_t)
        data["name"] = dest.name
        data["published"] = dict(published)
        data["updated"] = P.now()
        if write_title_txt(dest, data):
            res.rewritten += 1
        elif (dest / TITLE_TXT).is_file():
            res.skipped += 1
        if (dest / TITLE_TXT).is_file():
            res.files += 1
        P.write_json(path, data)
    return data


# ---------------------------------------------------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------------------------------------------------

_NETWORK_ERRORS = {51, 53, 55, 59, 64, 67, 121, 1222, 1231, 1232, 1236, 2250}   # WinError: the network path is gone
_SIGN_IN_ERRORS = {86, 1219, 1244, 1326}                                         # WinError: a user name / password


def _plain(e: OSError, root: Path) -> PublishError:
    """An OSError of the copy in plain words; the network gone -> SharedUnreachable."""
    win = getattr(e, "winerror", None)
    where = f" ({e.filename})" if getattr(e, "filename", None) else ""
    if win in _NETWORK_ERRORS:
        return SharedUnreachable(f"the shared folder {root} stopped answering (the network or the PC went away): "
                                 "the project stays on this computer and goes there later")
    if win in (39, 112) or getattr(e, "errno", None) == 28:
        return PublishError(f"the shared folder's disk is full{where}: free some space there and publish again")
    if win in _SIGN_IN_ERRORS:
        return PublishError(f"Windows wants a user name and password for {root}: open it once in Explorer, sign in "
                            "(tick 'Remember'), then publish again")
    if isinstance(e, PermissionError):
        return PublishError(f"this computer may not write to the shared folder {root}{where}: allow it in the "
                            "share's settings (or a file there is open in another program)")
    return PublishError(f"publishing did not work: {e.strerror or e}{where}")


def _sayer(progress: Progress | None) -> Progress:
    def say(f: float, text: str) -> None:
        if progress is not None:
            try:
                progress(max(0.0, min(1.0, float(f))), text)
            except Exception:  # noqa: BLE001 - the window's callback never stops the publish
                pass
    return say


def publish(project_folder: str | os.PathLike, root: str | os.PathLike, progress: Progress | None = None,
            cancel: threading.Event | None = None, *, timeout_s: float = REACH_TIMEOUT_S) -> PublishResult:
    """Publish a project's finished parts into ``root``\\<project folder name> (module docstring). Raises
    SharedUnreachable, NothingToPublish, PublishError (plain words), locks.LockedError (the other computer works on
    the shared copy) or Cancelled. Call it from a worker thread."""
    t0 = time.time()
    src, root = Path(project_folder), Path(root)
    say = _sayer(progress)
    if not (src / P.FILE).is_file():
        raise PublishError(f"{src} is not a project folder (it has no project.json)")
    say(0.0, "looking for the shared folder ...")
    why = why_unreachable(root, timeout_s)
    if why:
        raise SharedUnreachable(f"the shared folder cannot be reached: {why}. The project stays on this computer "
                                "and goes there later")
    if _same_folder(src, root / src.name):                      # the project is in the shared folder already
        res = PublishResult(dest=src, files=1, notes=["the project is in the shared folder already"])
        P.load(src).update(published=_stamp(src))
        res.seconds = round(time.time() - t0, 2)
        return res
    try:
        dest = destination(src, root)
    except OSError as e:
        raise _plain(e, root) from e
    res = PublishResult(dest=dest)
    if dest.name != src.name:
        res.notes.append(f"the shared folder has another project called {src.name}: this one goes to {dest.name}")
    if _inside(dest, src):
        raise PublishError("the shared folder is inside the project folder: choose another one in Settings")
    edits = finished_edits(src)
    if not edits:
        raise NothingToPublish(f"{src.name} has no finished edit yet (an edit folder with extras\\report.md): "
                               "nothing to publish")
    res.edits = [e.name for e in edits]
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise _plain(e, root) from e
    lock = locks.WorkLock(dest, "publish")
    try:
        lock.acquire()
    except OSError as e:
        raise _plain(e, root) from e
    try:
        if lock.taken_over:
            res.notes.append(lock.taken_over)
        _publish_files(src, dest, edits, res, say, cancel)
        say(0.99, "writing project.json ...")
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        published = _stamp(dest)
        _write_project_json(src, dest, published, res)
        res.files += 1
    except OSError as e:
        if not reachable(root, timeout_s):
            raise SharedUnreachable(f"the shared folder {root} stopped answering: the project stays on this "
                                    "computer and goes there later") from e
        raise _plain(e, root) from e
    finally:
        lock.release()
    P.load(src).update(published=published)
    res.seconds = round(time.time() - t0, 2)
    say(1.0, res.summary())
    return res


def _stamp(dest: Path) -> dict:
    """project.json's "published": where to, when, from which computer."""
    return {"to": str(dest), "at": P.now(), "computer": M.which(), "host": M.host()}


def _publish_files(src: Path, dest: Path, edits: list[Path], res: PublishResult, say: Progress,
                   cancel: threading.Event | None) -> None:
    items = _plan(src, dest, edits)
    link_ok = same_volume(src, dest)
    todo: list[tuple[_Item, str]] = []
    for it in items:                                            # what is there already
        try:
            ds = os.stat(it.dest)
        except FileNotFoundError:
            todo.append((it, "place"))
            continue
        if it.edit is not None:
            todo.append((it, "xml"))                            # decided on its content
        elif _samefile(it.src, it.dest) or (ds.st_size == it.size and abs(ds.st_mtime - it.mtime) <= MTIME_SLACK_S):
            todo.append((it, "skip"))
        elif ds.st_mtime > it.mtime + MTIME_SLACK_S:
            todo.append((it, "kept"))
        else:
            todo.append((it, "place"))
    total = sum(it.size for it, act in todo if act in ("place", "xml")) or 1
    done = [0]
    t_copy, copied_bytes = [time.time()], [0]
    last = [0.0]

    def tick(n: int, name: str, copying: bool) -> None:
        done[0] += n
        if copying:
            copied_bytes[0] += n
        if time.time() - last[0] < 0.25 and done[0] < total:
            return
        last[0] = time.time()
        text = f"publishing {done[0] * 100 // total} % -- {plain_size(done[0])} of {plain_size(total)}"
        dt = time.time() - t_copy[0]
        if copying and dt > 1.0 and copied_bytes[0]:
            speed = copied_bytes[0] / dt
            text += f", {plain_size(speed)}/s, about {_clock((total - done[0]) / max(1.0, speed))} left"
        say(min(0.98, done[0] / total), f"{text} ({name})")

    kept: list[str] = []
    try:
        _place_all(todo, dest, res, cancel, tick, kept, link_ok)
    finally:
        if kept:
            res.notes.append(f"kept {len(kept)} file(s) the shared folder has newer (changed there since): "
                             + ", ".join(kept[:5]) + (" ..." if len(kept) > 5 else ""))


def _place_all(todo: list[tuple[_Item, str]], dest: Path, res: PublishResult, cancel: threading.Event | None,
               tick: Callable[[int, str, bool], None], kept: list[str], link_ok: bool) -> None:
    """Each file into the published copy: skipped / kept / written anew (an XML) / linked / copied."""
    noted_link = False
    outside_seen: set[str] = set()
    for it, act in todo:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if act == "skip":
            res.skipped += 1
            res.files += 1
            continue
        if act == "kept":
            res.kept += 1
            res.files += 1
            kept.append(it.rel)
            continue
        it.dest.parent.mkdir(parents=True, exist_ok=True)
        if it.edit is not None:
            dest_edit = dest / it.edit.name
            new, n, outside = _rewritten(it.src, it.edit, dest_edit)
            for o in outside:
                if o not in outside_seen:
                    outside_seen.add(o)
                    res.notes.append(f"{it.rel} uses a file outside its edit folder, left as it is: {o}")
            if new is not None:
                try:
                    there = it.dest.read_bytes() if it.dest.is_file() else None
                except OSError:
                    there = None
                if there == new:
                    res.skipped += 1
                elif there is not None and os.stat(it.dest).st_mtime > it.mtime + MTIME_SLACK_S:
                    res.kept += 1
                    kept.append(it.rel)
                else:
                    _write_bytes(it.dest, new, it.stat)
                    res.rewritten += 1
                res.files += 1
                tick(it.size, it.rel, False)
                continue
            if act == "xml":                                    # nothing to rewrite: a plain file after all
                ds = os.stat(it.dest)
                if _samefile(it.src, it.dest) or (ds.st_size == it.size
                                                  and abs(ds.st_mtime - it.mtime) <= MTIME_SLACK_S):
                    res.skipped += 1
                    res.files += 1
                    tick(it.size, it.rel, False)
                    continue
        if link_ok:
            try:
                _link(it.src, it.dest)
                res.linked += 1
                res.files += 1
                tick(it.size, it.rel, False)
                continue
            except OSError as e:
                link_ok = False                                 # the rest is copied too
                if not noted_link:
                    noted_link = True
                    res.notes.append(f"copied, not linked: {e.strerror or e}")
        _copy(it.src, it.dest, lambda n, r=it.rel: tick(n, r, True), cancel)
        res.copied += 1
        res.files += 1
        res.bytes += it.size


def _samefile(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------------------------------
# the queue of projects waiting for the shared folder
# ---------------------------------------------------------------------------------------------------------------------

_queue_lock = threading.Lock()


def _pending_path(local_root: str | os.PathLike) -> Path:
    return Path(local_root) / PENDING_FILE


def _norm(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(p))


def _read_queue(local_root: str | os.PathLike) -> tuple[list[dict], bool]:
    """(the waiting projects, the file is there but broken)."""
    try:
        d = json.loads(_pending_path(local_root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], False
    except (OSError, ValueError):
        return [], True
    items = d.get("projects") if isinstance(d, dict) else d
    if not isinstance(items, list):
        return [], True
    return [x for x in items if isinstance(x, dict) and x.get("folder")], False


def pending(local_root: str | os.PathLike) -> list[dict]:
    """The projects waiting for the shared folder: [{folder, name, root, why, queued, tries, last_try}]."""
    return _read_queue(local_root)[0]


def _change_queue(local_root: str | os.PathLike, fn: Callable[[list[dict]], None]) -> list[dict]:
    """Re-read the queue, let ``fn`` change the list, write it back (atomically, locked; no file when empty). A
    broken file is kept as _pending_publish.json.bad."""
    path = _pending_path(local_root)
    with _queue_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with P._locked(path):
            items, broken = _read_queue(local_root)
            if broken:
                try:
                    os.replace(path, path.with_name(path.name + ".bad"))
                except OSError:
                    pass
            fn(items)
            if items:
                P.write_json(path, {"version": 1, "projects": items})
            else:
                _remove(path)
    return items


def queue_pending(local_root: str | os.PathLike, project_folder: str | os.PathLike, root: str | os.PathLike,
                  why: str) -> dict:
    """Keep a project for later: it goes to the shared folder when ``retry_pending`` finds the folder there again.
    A project already waiting is updated, not added twice."""
    key = _norm(project_folder)
    entry: dict = {}

    def add(items: list[dict]) -> None:
        nonlocal entry
        found = next((x for x in items if _norm(x["folder"]) == key), None)
        if found is None:
            found = {"folder": str(Path(project_folder)), "name": Path(project_folder).name, "queued": P.now(),
                     "tries": 0}
            items.append(found)
        found.update(root=str(root), why=str(why))
        entry = dict(found)
    _change_queue(local_root, add)
    return entry


def _drop(local_root: str | os.PathLike, project_folder: str | os.PathLike) -> None:
    """Take a project off the queue (nothing is written when it is not on it)."""
    key = _norm(project_folder)
    if not any(_norm(x["folder"]) == key for x in pending(local_root)):
        return

    def drop(items: list[dict]) -> None:
        items[:] = [x for x in items if _norm(x["folder"]) != key]
    _change_queue(local_root, drop)


def _tried(local_root: str | os.PathLike, project_folder: str | os.PathLike, why: str) -> None:
    key = _norm(project_folder)

    def note(items: list[dict]) -> None:
        for x in items:
            if _norm(x["folder"]) == key:
                x.update(tries=int(x.get("tries") or 0) + 1, last_try=P.now(), why=why)
    _change_queue(local_root, note)


def retry_pending(local_root: str | os.PathLike, root: str | os.PathLike, progress: Progress | None = None,
                  cancel: threading.Event | None = None) -> list[PublishResult]:
    """Publish the waiting projects into ``root`` (the setting now) once it answers; each one that went is taken off
    the list (one whose folder is gone too). Nothing happens while the folder does not answer. Call it at the start
    and every few minutes, from a worker thread."""
    items = pending(local_root)
    if not items or not reachable(root):
        return []
    out: list[PublishResult] = []
    for x in items:
        folder = Path(x["folder"])
        if not (folder / P.FILE).is_file():
            _drop(local_root, folder)                           # the project is not there any more
            continue
        try:
            res = publish(folder, root, progress, cancel)
        except Cancelled:
            raise
        except SharedUnreachable as e:
            _tried(local_root, folder, str(e))
            break                                               # gone again: the rest waits too
        except NothingToPublish:
            _drop(local_root, folder)
            continue
        except (PublishError, locks.LockedError, OSError) as e:
            _tried(local_root, folder, str(e))
            continue
        _drop(local_root, folder)
        out.append(res)
    return out


def publish_or_queue(project_folder: str | os.PathLike, root: str | os.PathLike,
                     local_root: str | os.PathLike | None = None, progress: Progress | None = None,
                     cancel: threading.Event | None = None) -> tuple[PublishResult | None, str]:
    """publish(); when that cannot be done now (the shared folder away, the other computer working on the shared
    copy, no permission ...) the project waits in the queue: (None, an amber note in plain words). (result, "") when
    it went. NothingToPublish and Cancelled are raised. ``local_root``: the project's parent by default."""
    local_root = Path(local_root) if local_root is not None else Path(project_folder).parent
    try:
        res = publish(project_folder, root, progress, cancel)
    except (Cancelled, NothingToPublish):
        raise
    except (PublishError, locks.LockedError) as e:
        queue_pending(local_root, project_folder, root, str(e))
        return None, f"saved on this computer for now -- {e}"
    _drop(local_root, project_folder)
    return res, ""
