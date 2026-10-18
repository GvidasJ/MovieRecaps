"""The Learn tab's shared files (night 11), all in one folder -- the hub. The caller passes it: the shared projects
root (D:\\Shared\\Projects on the PC, \\\\192.168.101.11\\Shared\\Projects on the laptop) or the local fallback, so the
PC and the laptop read and write the same three files:

* ``standing_rules.md`` -- rules that always apply (the Standing rules box): ``read_rules`` / ``save_rules``;
* ``requests.md`` -- my wishes for future nights, one dated checkbox bullet each, newest last (the Wishes box):
  ``add_wish`` / ``read_wishes``; a new file starts with night 11's two "Later" items;
* ``learn_queue.md`` -- the projects waiting to be learned, one checkbox line each: its name, my fixed project, the
  tool's run, the RAW, the competitor, the date and the computer -- ``queue_add`` / ``queue_items`` / ``queue_done``.

``tonight_prompt`` builds a ready-to-paste Claude Code prompt for the next night from them, in the style of
docs\\night11_prompt.md.

They are plain Markdown a person can read and change by hand; the readers are lenient. Every change re-reads its
file under a small lock (projects._locked: a .lock file next to it) and replaces it atomically, so the two computers
writing at once lose nothing. A path inside the hub or next to it (in D:\\Shared) is written relative to the hub
(``.\\x``, ``..\\x``), so the same line is right on the PC and on the laptop. Nothing here raises: a write returns ""
or what went wrong in plain words, a read returns what it could read. On a network folder every call can wait for
the network: call them from a worker thread."""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from . import paths

RULES_FILE = "standing_rules.md"
WISHES_FILE = "requests.md"
QUEUE_FILE = "learn_queue.md"

RULES_HEAD = ("# Standing rules\n\n"
              "Rules that always apply, every night, whatever the night's list says. Written in the app's Learn tab\n"
              "(Standing rules); the \"Copy tonight's prompt\" button puts them into the night's prompt.\n\n")
WISHES_HEAD = ("# Wishes for future nights\n\n"
               "My ideas for future nights, newest last: [ ] open, [x] done. Added in the app's Learn tab (Wishes);\n"
               "the \"Copy tonight's prompt\" button puts the open ones into the night's prompt.\n\n")
QUEUE_HEAD = ("# Learn queue\n\n"
              "Projects waiting to be learned, one line each: [ ] waiting, [x] learned. The app's Learn tab adds a line\n"
              "when you learn from a fix; the \"Copy tonight's prompt\" button puts the waiting ones into the night's\n"
              "prompt. A path starting with .\\ is in this folder, ..\\ next to it (the same on the PC and the laptop).\n\n")

# night 11's "Later (NOT tonight, just keep in requests.md)", word for word (docs\night11_prompt.md)
SEED_DATE = "2026-10-18"
SEED_WHO = "night 11's list, Later"
SEED_WISHES = (
    "A **Stats** tab with channel analytics: first public stats with yt-dlp (views, likes, subscribers, "
    "fastest-growing Shorts), later private YouTube Studio stats through a Google login.",
    "Automating the Topaz hand-off between the PC and laptop.",
)

PC_SHARE = r"\\192.168.101.11\Shared"      # the PC's D:\Shared as the laptop sees it (machine.py)
PC_LOCAL = r"D:\Shared"
SEP = " | "                                # between a queue line's fields (no path or name can hold a "|")


# ---------------------------------------------------------------------------------------------------------------------
# small helpers: time, this computer, files
# ---------------------------------------------------------------------------------------------------------------------

def stamp(t: datetime | None = None) -> str:
    """'2026-10-18 21:05' -- the local time, to the minute."""
    return (t or datetime.now()).strftime("%Y-%m-%d %H:%M")


def nice_date(text: str) -> str:
    """'2026-10-18 21:05' -> '18 Oct 21:05'; '2026-10-18' -> '18 Oct 2026'; anything else as it is."""
    for fmt, out in (("%Y-%m-%d %H:%M", "{d.day} {d:%b %H:%M}"), ("%Y-%m-%d", "{d.day} {d:%b %Y}")):
        try:
            d = datetime.strptime(str(text).strip(), fmt)
        except ValueError:
            continue
        return out.format(d=d)
    return str(text)


def this_computer() -> str:
    """'PC' or 'laptop' (machine.py: both computers are called "Gvidas"), else the host name."""
    try:
        from . import machine as M
        return M.label(M.which())
    except Exception:  # noqa: BLE001 - machine.py missing or Windows not asked: the host name
        import socket
        try:
            return socket.gethostname()
        except OSError:
            return "this computer"


def reachable(folder: str | os.PathLike, timeout: float = 3.0) -> bool:
    """The folder is there -- asked in a thread and given up after ``timeout`` s, so a network folder of a computer
    that is off never hangs the caller (Windows waits about 20 s for an unreachable share)."""
    got: dict[str, bool] = {}

    def probe() -> None:
        try:
            got["ok"] = os.path.isdir(folder)
        except OSError:
            got["ok"] = False
    t = threading.Thread(target=probe, daemon=True, name="folder check")
    t.start()
    t.join(timeout)
    return bool(got.get("ok"))


def hub_status(folder: str | os.PathLike, timeout: float = 3.0) -> str:
    """"" when the hub folder can be used (it is there, or its parent is and it can be made), else why not, in plain
    words."""
    f = Path(folder)
    if reachable(f, timeout):
        return ""
    if reachable(f.parent, timeout):
        return ""                                          # made with the first file written
    return (f"the folder {f} cannot be reached (the computer that shares it is off, or the network is down) -- "
            "the Learn files are there, so they can't be read or saved right now")


def _read_text(path: Path, tries: int = 10) -> str | None:
    """The file's text (a BOM from Notepad dropped); None when there is no file. A read that meets the moment
    another program replaces the file (Windows says 'access denied' then) is tried again."""
    for k in range(tries):
        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except FileNotFoundError:
            return None
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.05)
    return None


def _write_text(path: Path, text: str, tries: int = 40) -> None:
    """Write atomically: a temp file next to it, flushed to the disk, moved over the old file (retried while a reader
    on this or the other computer has the old one open)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for k in range(tries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if k == tries - 1:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _problem(what: str, path: Path, e: BaseException) -> str:
    """An OSError in plain words."""
    if isinstance(e, PermissionError):
        why = "access denied (is it open in another program, or is the folder read-only?)"
    elif isinstance(e, FileNotFoundError):
        why = "the folder is not there (is the shared folder reachable?)"
    elif isinstance(e, OSError) and getattr(e, "winerror", None) in (53, 64, 67, 1231, 1232):
        why = "the network folder cannot be reached (is the PC on?)"
    elif isinstance(e, OSError) and e.strerror:
        why = e.strerror
    else:
        why = f"{type(e).__name__}: {e}"
    return f"{what} {path}: {why}"


def _change(path: Path, fn: Callable[[str | None], str]) -> str:
    """Re-read ``path`` under its lock, write ``fn(old text or None)`` back atomically; "" or the problem."""
    from .projects import _locked
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _locked(path):
            _write_text(path, fn(_read_text(path)))
        return ""
    except OSError as e:
        return _problem("could not save", path, e)
    except Exception as e:  # noqa: BLE001 - never into the window
        return f"could not save {path}: {type(e).__name__}: {e}"


def _read(path: Path) -> str | None:
    try:
        return _read_text(path)
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# paths that are right on both computers
# ---------------------------------------------------------------------------------------------------------------------

def _clean(p: str | os.PathLike) -> str:
    """A path without Windows' long-path prefix (\\\\?\\C:\\..., \\\\?\\UNC\\server\\share\\...)."""
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    if s.startswith("\\\\?\\"):
        return s[4:]
    return s


def _letter_root(p: str) -> bool:
    drive, rest = os.path.splitdrive(p)
    return len(drive) == 2 and rest.strip("\\/") == ""


def portable(path: str | os.PathLike, hub: str | os.PathLike) -> str:
    """``path`` as written in the hub's files: ``.\\x`` inside the hub, ``..\\x`` next to it (in D:\\Shared -- unless
    the hub's parent is a bare drive like D:\\), else the full path."""
    if not str(path).strip():
        return ""
    p = os.path.normpath(_clean(path))
    h = os.path.normpath(_clean(hub))
    bases = [(".", h)]
    parent = os.path.dirname(h)
    if parent and parent != h and not _letter_root(parent):
        bases.append(("..", parent))
    for mark, base in bases:
        try:
            r = os.path.relpath(p, base)
        except ValueError:                                     # another drive or share
            continue
        if r == ".":
            return mark
        if not r.startswith(".."):
            return f"{mark}\\{r}"
    return p


def resolve(text: str, hub: str | os.PathLike) -> str:
    """A path from the hub's files on this computer (``.\\x`` and ``..\\x`` against the hub folder)."""
    t = str(text or "").strip()
    if not t:
        return ""
    if t in (".", "..") or t.startswith((".\\", "./", "..\\", "../")):
        return os.path.normpath(os.path.join(_clean(hub), t))
    return t


def for_pc(path: str | os.PathLike) -> str:
    """A path as the PC sees it: the laptop's \\\\192.168.101.11\\Shared\\... is the PC's own D:\\Shared\\... (the night
    runs on the PC)."""
    s = _clean(path)
    if s.lower().startswith(PC_SHARE.lower()) and (len(s) == len(PC_SHARE) or s[len(PC_SHARE)] in "\\/"):
        return PC_LOCAL + s[len(PC_SHARE):]
    return s


# ---------------------------------------------------------------------------------------------------------------------
# standing rules
# ---------------------------------------------------------------------------------------------------------------------

def rules_path(folder: str | os.PathLike) -> Path:
    return Path(folder) / RULES_FILE


def read_rules(folder: str | os.PathLike) -> str:
    """The standing rules as typed (the file's header left out); "" when there are none."""
    text = _read(rules_path(folder))
    if not text:
        return ""
    if text.startswith(RULES_HEAD):
        text = text[len(RULES_HEAD):]
    return text.strip("\n")


def save_rules(folder: str | os.PathLike, text: str) -> str:
    """Save the Standing rules box (the whole text; the header is added); "" or the problem in plain words."""
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return _change(rules_path(folder), lambda _old: RULES_HEAD + (body + "\n" if body else ""))


# ---------------------------------------------------------------------------------------------------------------------
# wishes (requests.md)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Wish:
    """One wish: what I want, when I wrote it ('2026-10-18 21:05'), on which computer, done (ticked) or not."""
    text: str
    date: str = ""
    computer: str = ""
    done: bool = False

    def line(self) -> str:
        """The wish as its bullet in requests.md (a text of several lines: the next lines indented)."""
        body = str(self.text).replace("\r\n", "\n").replace("\r", "\n").strip()
        first, *rest = body.split("\n") if body else [""]
        head = f"- [{'x' if self.done else ' '}] {self.date or stamp()} · {self.computer or '?'}: {first}"
        return "\n".join([head] + [f"  {r}" if r.strip() else "" for r in rest])


_WISH = re.compile(r"^- \[(?P<done>[ xX])\]\s+(?P<date>\d{4}-\d\d-\d\d(?: \d\d:\d\d)?)\s+·\s+(?P<who>[^:]*?):\s?"
                   r"(?P<text>.*)$")
_BULLET = re.compile(r"^[-*] (?:\[(?P<done>[ xX])\]\s+)?(?P<text>.+)$")


def wishes_path(folder: str | os.PathLike) -> Path:
    return Path(folder) / WISHES_FILE


def _seed() -> str:
    return WISHES_HEAD + "".join(Wish(t, SEED_DATE, SEED_WHO).line() + "\n" for t in SEED_WISHES)


def parse_wishes(text: str) -> list[Wish]:
    """The wishes of requests.md's text, in the file's order (a bullet I wrote by hand without a date counts too;
    indented lines after a bullet belong to it)."""
    out: list[Wish] = []
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        m = _WISH.match(line)
        if m:
            out.append(Wish(m["text"].strip(), m["date"], m["who"].strip(), m["done"].lower() == "x"))
            continue
        b = _BULLET.match(line)
        if b:
            out.append(Wish(b["text"].strip(), done=(b["done"] or " ").lower() == "x"))
            continue
        if out and raw[:1] in (" ", "\t") and line.strip():
            out[-1].text = f"{out[-1].text}\n{line.strip()}"
    return out


def read_wishes(folder: str | os.PathLike) -> list[Wish]:
    """Every wish in requests.md, oldest first (the file starts with night 11's two "Later" items); [] when the file
    is not there yet."""
    text = _read(wishes_path(folder))
    return parse_wishes(text) if text else []


def add_wish(folder: str | os.PathLike, text: str, computer: str | None = None) -> str:
    """Add a wish at the end of requests.md (made with its header and night 11's two "Later" items when it is not
    there yet); "" or the problem in plain words."""
    body = str(text or "").strip()
    if not body:
        return "the wish is empty: type it first"
    w = Wish(body, stamp(), computer or this_computer())

    def add(old: str | None) -> str:
        base = old if old else _seed()
        if not base.endswith("\n"):
            base += "\n"
        return base + w.line() + "\n"
    return _change(wishes_path(folder), add)


def ensure_wishes(folder: str | os.PathLike) -> str:
    """Make requests.md with its header and night 11's two "Later" items when it is not there; "" or the problem."""
    return _change(wishes_path(folder), lambda old: old if old else _seed())


# ---------------------------------------------------------------------------------------------------------------------
# the learn queue (learn_queue.md)
# ---------------------------------------------------------------------------------------------------------------------

QUEUE_KEYS = ("prproj", "run", "RAW", "competitor", "date", "computer", "copies", "learned", "note")
PATH_KEYS = ("prproj", "run")


@dataclass
class QueueItem:
    """One project waiting to be learned. ``prproj`` / ``run``: my fixed project and the tool's run it was made from,
    as this computer sees them; ``raw`` / ``competitor``: their file names (the files are in the run's extras\\media);
    ``copies``: where the app copied them for the nightly LEARN step (finished\\<name>\\..., output\\laptop_<name>)."""
    name: str
    prproj: str = ""
    run: str = ""
    raw: str = ""
    competitor: str = ""
    date: str = ""
    computer: str = ""
    done: bool = False
    copies: str = ""
    learned: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)

    def line(self, hub: str | os.PathLike | None = None) -> str:
        """The item as its line in learn_queue.md (``hub``: paths in it or next to it written relative to it)."""
        vals = {"prproj": self.prproj, "run": self.run, "RAW": self.raw, "competitor": self.competitor,
                "date": self.date, "computer": self.computer, "copies": self.copies, "learned": self.learned,
                "note": self.note, **{k: v for k, v in self.extra.items() if k not in QUEUE_KEYS}}
        parts = [_field_text(self.name) or "video"]
        for k, v in vals.items():
            v = str(v or "").strip()
            if not v:
                continue
            if k in PATH_KEYS and hub is not None:
                v = portable(v, hub)
            parts.append(f"{k}: {_field_text(v)}")
        return f"- [{'x' if self.done else ' '}] " + SEP.join(parts)


def _field_text(v: str) -> str:
    """One line, no field separator in it."""
    return " ".join(str(v).split()).replace("|", "/")


_QUEUE = re.compile(r"^- \[(?P<done>[ xX])\]\s+(?P<body>.+)$")


def parse_queue(text: str, hub: str | os.PathLike | None = None) -> list[QueueItem]:
    """The items of learn_queue.md's text in the file's order (``hub``: relative paths resolved against it). Lines
    that are not items are skipped; unknown fields are kept in ``extra``."""
    out = []
    for raw in str(text or "").splitlines():
        m = _QUEUE.match(raw.strip())
        if not m:
            continue
        parts = re.split(r"\s*\|\s*", m["body"].strip())          # typed by hand: spaces around | or not
        it = QueueItem(name=parts[0].strip(), done=m["done"].lower() == "x")
        for p in parts[1:]:
            k, sep, v = p.partition(":")
            if not sep or not k.strip():
                if p.strip():
                    it.note = f"{it.note} {p.strip()}".strip()
                continue
            k, v = k.strip(), v.strip()
            key = {"raw": "raw", "competitor": "competitor"}.get(k.lower(), k.lower())
            if key in PATH_KEYS and hub is not None:
                v = resolve(v, hub)
            if key in ("prproj", "run", "raw", "competitor", "date", "computer", "copies", "learned", "note"):
                setattr(it, key, v)
            else:
                it.extra[k] = v
        out.append(it)
    return out


def queue_path(folder: str | os.PathLike) -> Path:
    return Path(folder) / QUEUE_FILE


def queue_items(folder: str | os.PathLike, include_done: bool = False) -> list[QueueItem]:
    """The projects in learn_queue.md, in its order: the waiting ones (``include_done``: the learned ones too); paths
    as this computer sees them. [] when there is no file (or it cannot be read)."""
    text = _read(queue_path(folder))
    items = parse_queue(text, folder) if text else []
    return items if include_done else [i for i in items if not i.done]


def _same_path(a: str, b: str) -> bool:
    return bool(a and b) and os.path.normcase(os.path.normpath(_clean(a))) == os.path.normcase(
        os.path.normpath(_clean(b)))


def queue_add(folder: str | os.PathLike, item: QueueItem) -> str:
    """Add a project to learn_queue.md (made with its header when it is not there): a waiting item with the same name
    or the same fixed project is replaced in place (a fix dropped again), a learned one stays and the new line goes
    at the end. ``date`` / ``computer`` are filled in when empty. "" or the problem in plain words."""
    if not str(item.name or "").strip():
        return "the project has no name: type one first"
    it = QueueItem(**{**item.__dict__, "extra": dict(item.extra)})
    it.date = it.date or stamp()
    it.computer = it.computer or this_computer()

    def add(old: str | None) -> str:
        text = old if old else QUEUE_HEAD
        lines = text.splitlines()
        new = it.line(folder)
        for i, raw in enumerate(lines):
            got = parse_queue(raw, folder)
            if got and not got[0].done and (got[0].name.lower() == it.name.lower()
                                            or _same_path(got[0].prproj, it.prproj)):
                lines[i] = new
                break
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            if lines and _QUEUE.match(lines[-1].strip()) is None:
                lines.append("")
            lines.append(new)
        return "\n".join(lines) + "\n"
    return _change(queue_path(folder), add)


def queue_done(folder: str | os.PathLike, name: str, note: str = "") -> str:
    """Tick the waiting project(s) called ``name`` in learn_queue.md (learned now); "" or the problem."""
    found = {"n": 0}

    def tick(old: str | None) -> str:
        lines = (old or "").splitlines()
        for i, raw in enumerate(lines):
            got = parse_queue(raw)                 # the line's own text: paths stay as they are written
            if got and not got[0].done and got[0].name.lower() == str(name).strip().lower():
                g = got[0]
                g.done, g.learned = True, stamp()
                if note:
                    g.note = note
                lines[i] = g.line()
                found["n"] += 1
        return "\n".join(lines) + "\n" if lines else (old or "")
    if _read(queue_path(folder)) is None:
        return f"there is no {QUEUE_FILE} in {folder} yet"
    err = _change(queue_path(folder), tick)
    if err:
        return err
    return "" if found["n"] else f"no waiting project called {name} in {QUEUE_FILE}"


# ---------------------------------------------------------------------------------------------------------------------
# "Copy tonight's prompt"
# ---------------------------------------------------------------------------------------------------------------------

INTRO = ("You have the whole night. I'm away and can't answer questions or approve anything. Never stop to ask me. "
         "When something is unclear, choose the safest sensible option, write it down in the report, and keep going.")

LEARN_RULES = (
    "Learn **only**: captions (words, timing, look), cuts, and video position/scale.",
    "**Ignore:** the headline PNG (it's the same in all videos), memes, music, all audio, pitch, and my trims at cut "
    "points and at the ending (I do those by hand).",
    "Check each new rule against my earlier fixes first, and make each fix a test case.",
    "Re-run each project after the changes and report how close the tool now gets to my fixed version "
    "(before → after numbers).",
    "If a project or one of its files is missing, write that in the report and continue with the next one.",
)

_NIGHT = re.compile(r"(?i)^night(\d+)\.md$")


def next_night(repo: str | os.PathLike | None = None) -> int:
    """The next night's number: one after the newest reports\\nightN.md (1 when there is none)."""
    d = Path(repo or paths.repo()) / "reports"
    nums = []
    try:
        for e in os.scandir(d):
            m = _NIGHT.match(e.name)
            if m and e.is_file():
                nums.append(int(m.group(1)))
    except OSError:
        pass
    return max(nums, default=0) + 1


def current_branch(repo: str | os.PathLike | None = None) -> str:
    """The checked-out git branch, read from .git\\HEAD (no git command); "" when it cannot be told."""
    try:
        head = (Path(repo or paths.repo()) / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""


def _numbered(lines: Iterable[str]) -> str:
    return "\n".join(f"{i}. {t}" for i, t in enumerate(lines, start=1))


def _item_block(n: int, it: QueueItem) -> str:
    when = " ".join(x for x in (f"queued {nice_date(it.date)}" if it.date else "",
                                f"on the {it.computer}" if it.computer in ("PC", "laptop") else
                                (f"on {it.computer}" if it.computer else "")) if x)
    rows = [f"{n}. **{it.name}**" + (f" ({when})" if when else "")]
    if it.prproj:
        rows.append(f"   - My fixed project: `{for_pc(it.prproj)}`")
    if it.run:
        rows.append(f"   - The tool's run it was made from: `{for_pc(it.run)}`")
    media = [f"RAW `{it.raw}`" if it.raw else "", f"competitor `{it.competitor}`" if it.competitor else ""]
    media = [m for m in media if m]
    if media:
        rows.append(f"   - {' · '.join(media)} (both are in the run's `extras\\media`)")
    if it.copies:
        rows.append(f"   - Copied for the LEARN step to {', '.join(f'`{c.strip()}`' for c in it.copies.split(',') if c.strip())}"
                    + (f" (on the {it.computer})" if it.computer in ("PC", "laptop") else ""))
    if it.note:
        rows.append(f"   - Note: {it.note}")
    return "\n".join(rows)


def tonight_prompt(folder: str | os.PathLike, repo: str | os.PathLike | None = None, hours: int = 14) -> str:
    """A complete Claude Code prompt for the next night, in the style of docs\\night11_prompt.md: the start of the night
    (a backup shutdown, the last report, commit and push after each section, the tests), the standing rules, LEARN
    for every waiting project of learn_queue.md with its exact files and the learning rules, the open wishes of
    requests.md, and the finish (all tests, reports\\nightN.md in plain English with the laptop steps, commit and push,
    the shutdown). Paths as the PC sees them (the night runs on the PC). Never raises."""
    try:
        return _tonight_prompt(Path(folder), Path(repo or paths.repo()), int(hours))
    except Exception as e:  # noqa: BLE001 - never into the window: a prompt that says what went wrong
        return f"# The prompt could not be built\n\n{type(e).__name__}: {e}\n"


def _tonight_prompt(folder: Path, repo: Path, hours: int) -> str:
    n = next_night(repo)
    branch = current_branch(repo)
    rules = read_rules(folder).strip()
    items = queue_items(folder)
    wishes = [w for w in read_wishes(folder) if not w.done]
    hub = for_pc(folder)
    backup = (hours + 1) * 3600
    out = [f"# Night {n} — MovieRecaps ({hours} hours, PC)", "", INTRO, "",
           f"Repo: `{repo}`." + (f" Branch: `{branch}`." if branch else ""), ""]
    last = f"reports\\night{n - 1}.md" if n > 1 and (repo / "reports" / f"night{n - 1}.md").is_file() else ""
    start = [f"Run `shutdown /a`, then `shutdown /s /t {backup}`. That's a {hours + 1}-hour backup, so the PC turns off "
             "even if you get stuck.",
             (f"Read `{last}` so you know where things stand." if last else
              "Read the newest report in `reports\\` so you know where things stand."),
             "Make a task list from the sections below and work **in this order**. If time runs short, the top items "
             "matter most.",
             "Commit and push after every finished section, with a clear message. Never leave the repo broken.",
             "Keep all existing tests passing. Add tests for new logic."]
    out += ["## 0. Start of the night (do first)", "", _numbered(start), ""]
    k = 1
    if rules:
        out += [f"## {k}. Standing rules (they always apply)", "", rules, ""]
        k += 1
    if items:
        out += [f"## {k}. Learn from my fixes", "",
                f"These projects wait in `learn_queue.md` (`{hub}\\{QUEUE_FILE}`). For each one, compare my fixed "
                "project with the tool's run it was made from and learn from what I changed (`python -m match_cuts "
                "learn`, as on nights 7-9).", ""]
        out += [_item_block(i, it) for i, it in enumerate(items, start=1)]
        out += [""] + [f"- {r}" for r in LEARN_RULES] + [
            f"- When a project is learned, tick it in `learn_queue.md` (`- [x]`).", ""]
        k += 1
    if wishes:
        out += [f"## {k}. My wishes (from requests.md)", "",
                "Do these after the learning, in this order. If one is too big for one night, do a careful first part "
                f"and say in the report what is left. When one is done, tick it in `requests.md` (`{hub}\\{WISHES_FILE}`, "
                "`- [x]`).", ""]
        for i, w in enumerate(wishes, start=1):
            first, *rest = w.text.split("\n")
            when = ", ".join(x for x in (nice_date(w.date) if w.date else "", w.computer) if x)
            out.append(f"{i}. {first}" + (f" ({when})" if when else ""))
            out += [f"   {r}" for r in rest]
        out.append("")
        k += 1
    finish = ["Run all tests.",
              f"Write `reports\\night{n}.md` in plain, simple English:\n"
              "   - what's done\n"
              "   - what's not done and why\n" +
              ("   - before → after numbers for every project you learned from\n" if items else "") +
              "   - anything I must check by hand\n"
              "   - **laptop steps** (exact commands)",
              "Commit and push.",
              "Run `shutdown /a`, then `shutdown /s /t 120`. Do this even if you finish early."]
    out += [f"## {k}. Finish", "", _numbered(finish), ""]
    return "\n".join(out)
