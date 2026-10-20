"""The problems list (day 12, section 2): every run of the Match cuts queue that ends FAIL or "OK with a note" -- picture
not found, a flash, sync / repeat notes, caption problems, the person speaking outside the window, a crash or a stop
with an exit code -- is written by itself to ``problems_queue.md`` in the hub (the shared projects folder, next to
learn_queue.md and requests.md), so a night run can re-run it, find the cause and fix it. Wrong input is not a tool
problem and is never written: WRONG PAIR, NOT IN THIS RAW, the input checks ("The inputs look wrong ..."), a project
another computer is working on, a run you stopped.

The file is Markdown a person (or a night run) reads and changes by hand -- one entry per problem, newest last::

    - [ ] **2026-10-19_video-by-mindculpt** · FAIL: XML PERSON -- the person speaking is outside the window ...
      - status: open
      - run: C:\\...\\2026-10-19_video-by-mindculpt\\edit
      - copy on the PC: D:\\Shared\\problems\\2026-10-19_video-by-mindculpt\\edit
      - date: 2026-10-19 02:44
      - computer: laptop
      - result: FAIL
      - log: C:\\...\\edit\\extras\\batch_console.log
      - key log lines:
        > 02:38:43 WARNING premiere export validation: 2 problem(s): XML PERSON ...

``status`` is ``open``, ``fixed <date>``, ``not fixed — <reason>`` or ``skipped (my mistake) <date>``; ``[x]`` = fixed
or skipped (a ``[x]`` without a status counts as fixed). Readers are lenient: unknown ``- key: value`` lines are kept.
Every change re-reads the file under its lock and replaces it atomically, only when something changes
(learn_hub._change), so the PC and the laptop never lose each other's entries. Nothing here raises; on a network
folder every call can wait: call them from a worker thread."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import learn_hub as H

FILE = "problems_queue.md"
HEAD = ("# Problems to fix\n\n"
        "Runs of the app that ended FAIL or \"OK with a note\", added by themselves (wrong input -- WRONG PAIR, the\n"
        "input checks -- is not added). One entry each, newest last. status: open / fixed <date> /\n"
        "not fixed — <reason> / skipped (my mistake) <date>; [x] = fixed or skipped. A night run re-runs each open\n"
        "one, finds the cause, fixes it, adds it as a test case and marks it. A path starting with .\\ is in this\n"
        "folder, ..\\ next to it (the same on the PC and the laptop).\n\n")

OPEN, FIXED, NOT_FIXED, SKIPPED = "open", "fixed", "not fixed", "skipped"
KEYS = ("status", "run", "copy on the PC", "date", "computer", "result", "log", "exit", "id")
PATH_KEYS = ("run", "log")
MAX_LINES = 10
MAX_LINE = 600


@dataclass
class Problem:
    """One problem: the video, the check that failed (or the note), where its run and log are, when and on which
    computer, the key lines of its log, its status."""
    name: str
    check: str = ""
    run: str = ""
    copy: str = ""
    date: str = ""
    computer: str = ""
    result: str = ""
    log: str = ""
    exit: str = ""
    status: str = OPEN
    lines: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    id: str = ""

    @property
    def state(self) -> str:
        """OPEN, FIXED, NOT_FIXED or SKIPPED, from the status text."""
        return state_of(self.status)

    @property
    def is_open(self) -> bool:
        """Still to fix: open, or tried and not fixed."""
        return self.state in (OPEN, NOT_FIXED)

    def key(self) -> str:
        """Its id (made from the run folder and the check when it has none)."""
        return self.id or make_id(self.run, self.name, self.check)

    def block(self, hub: str | os.PathLike | None = None) -> str:
        """The entry as its lines in problems_queue.md."""
        done = self.state in (FIXED, SKIPPED)
        head = f"- [{'x' if done else ' '}] **{_one(self.name) or 'video'}**"
        if self.check:
            head += f" · {_one(self.check)}"
        rows = [head]
        vals = {"status": self.status or OPEN, "run": self.run, "copy on the PC": self.copy, "date": self.date,
                "computer": self.computer, "result": self.result, "log": self.log, "exit": self.exit,
                **{k: v for k, v in self.extra.items() if k not in KEYS}, "id": self.key()}
        for k, v in vals.items():
            v = _one(v)
            if not v:
                continue
            if k in PATH_KEYS and hub is not None:
                v = H.portable(v, hub)
            rows.append(f"  - {k}: {v}")
        if self.lines:
            rows.append("  - key log lines:")
            rows += [f"    > {_one(ln)[:MAX_LINE]}" for ln in self.lines if _one(ln)]
        return "\n".join(rows)


def state_of(status: str) -> str:
    s = str(status or "").strip().lower()
    if not s or s.startswith("open"):
        return OPEN
    if s.startswith("not fixed") or s.startswith("not-fixed"):
        return NOT_FIXED
    if s.startswith("skip"):
        return SKIPPED
    if s.startswith("fixed") or s.startswith("done"):
        return FIXED
    return OPEN


def _one(v: Any) -> str:
    """One line."""
    return " ".join(str(v if v is not None else "").split())


def make_id(run: str, name: str, check: str) -> str:
    """A short stable id: the same run folder and the same check give the same id on both computers."""
    base = f"{H.for_pc(os.path.normpath(str(run)) if run else '').lower().rstrip(chr(92) + '/')}|{str(name).lower()}|{_one(check)[:120].lower()}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------------------------------------------------
# reading and writing problems_queue.md
# ---------------------------------------------------------------------------------------------------------------------

_HEAD = re.compile(r"^- \[(?P<done>[ xX])\]\s+(?P<body>.+)$")
_SUB = re.compile(r"^\s{1,8}[-*]\s+(?P<k>[^:]+?):\s?(?P<v>.*)$")
_QUOTE = re.compile(r"^\s{2,12}>\s?(?P<v>.*)$")


def path(folder: str | os.PathLike) -> Path:
    return Path(folder) / FILE


def parse(text: str, hub: str | os.PathLike | None = None) -> list[Problem]:
    """The entries of problems_queue.md's text, in the file's order (``hub``: relative paths resolved)."""
    out: list[Problem] = []
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        m = _HEAD.match(line)
        if m:
            body = m["body"].strip()
            nm = re.match(r"^\*\*(?P<n>.+?)\*\*\s*(?:·\s*(?P<c>.*))?$", body)
            if nm:
                name, check = nm["n"].strip(), (nm["c"] or "").strip()
            else:
                name, _sep, check = body.partition(" · ")
            p = Problem(name=name.strip(), check=check.strip())
            if m["done"].lower() == "x":
                p.status = FIXED
            p.extra["_ticked"] = m["done"].lower() == "x"
            out.append(p)
            continue
        if not out:
            continue
        p = out[-1]
        q = _QUOTE.match(raw)
        if q and q["v"].strip():
            p.lines.append(q["v"].strip())
            continue
        s = _SUB.match(raw)
        if s:
            k, v = s["k"].strip().lower(), s["v"].strip()
            if k == "key log lines":
                continue
            if k in PATH_KEYS and hub is not None:
                v = H.resolve(v, hub)
            if k == "status":
                p.status = v or (FIXED if p.extra.get("_ticked") else OPEN)
            elif k == "copy on the pc":
                p.copy = v
            elif k in ("run", "date", "computer", "result", "log", "exit", "id"):
                setattr(p, k, v)
            else:
                p.extra[s["k"].strip()] = v
    for p in out:
        p.extra.pop("_ticked", None)
    return out


def read(folder: str | os.PathLike, include_closed: bool = False) -> list[Problem]:
    """The problems in problems_queue.md, oldest first: the open ones (open or not fixed), ``include_closed``: all.
    [] when there is no file (or it cannot be read)."""
    text = H._read(path(folder))
    items = parse(text, folder) if text else []
    return items if include_closed else [p for p in items if p.is_open]


def newest_first(items: Iterable[Problem]) -> list[Problem]:
    """For the Learn tab: the newest date first (the file's order kept within a minute)."""
    items = list(items)
    order = {id(p): i for i, p in enumerate(items)}
    return sorted(items, key=lambda p: (str(p.date or ""), order[id(p)]), reverse=True)


def _split(text: str) -> tuple[list[str], list[list[str]]]:
    """The file's lines before the first entry, and each entry's lines (its head and everything up to the next)."""
    pre: list[str] = []
    blocks: list[list[str]] = []
    for raw in str(text or "").splitlines():
        if _HEAD.match(raw.rstrip()):
            blocks.append([raw.rstrip()])
        elif blocks:
            blocks[-1].append(raw.rstrip())
        else:
            pre.append(raw.rstrip())
    return pre, blocks


def _join(pre: list[str], blocks: list[list[str]]) -> str:
    out = list(pre)
    while out and not out[-1].strip():
        out.pop()
    out.append("")
    for b in blocks:
        while b and not b[-1].strip():
            b.pop()
        out += b + [""]
    return "\n".join(out).lstrip("\n") + "\n"


def add(folder: str | os.PathLike, p: Problem) -> str:
    """Add a problem at the end of problems_queue.md (made with its header when it is not there). An open entry with
    the same id (the same run and check) or the same run folder is replaced in place -- the newest lines win; an entry
    already fixed or skipped with the same id stays as it is (nothing is written). ``date`` / ``computer`` are filled
    in when empty. "" or the problem in plain words."""
    if not str(p.name or "").strip():
        return "the problem has no video name"
    it = Problem(**{**p.__dict__, "lines": list(p.lines), "extra": dict(p.extra)})
    it.date = it.date or H.stamp()
    it.computer = it.computer or H.this_computer()
    it.id = it.key()

    def change(old: str | None) -> str:
        if not old:
            return HEAD + it.block(folder) + "\n"
        pre, blocks = _split(old)
        for i, b in enumerate(blocks):
            got = parse("\n".join(b), folder)
            if not got:
                continue
            g = got[0]
            same_id = g.key() == it.id
            same_run = bool(g.run and it.run) and H._same_path(g.run, it.run)
            if same_id and not g.is_open:
                return old                                   # fixed / skipped already: left alone
            if (same_id or same_run) and g.is_open:
                it.status = g.status if g.state == OPEN else it.status
                blocks[i] = it.block(folder).split("\n")
                return _join(pre, blocks)
        blocks.append(it.block(folder).split("\n"))
        return _join(pre, blocks)
    return H._change(path(folder), change)


def set_status(folder: str | os.PathLike, pid: str, status: str) -> str:
    """Change one entry's status (by its id): ``skip`` / ``fixed`` / ``not_fixed`` use these. "" or the problem."""
    found = {"n": 0}

    def change(old: str | None) -> str:
        if not old:
            return old or ""
        pre, blocks = _split(old)
        for i, b in enumerate(blocks):
            got = parse("\n".join(b), folder)
            if got and got[0].key() == pid:
                g = got[0]
                g.status = status
                blocks[i] = g.block(folder).split("\n")
                found["n"] += 1
        return _join(pre, blocks) if found["n"] else old
    if H._read(path(folder)) is None:
        return f"there is no {FILE} in {folder} yet"
    err = H._change(path(folder), change)
    if err:
        return err
    return "" if found["n"] else f"that problem is not in {FILE} any more"


def skip(folder: str | os.PathLike, pid: str) -> str:
    """"Skip (my mistake)": the entry is closed -- ``skipped (my mistake) <date>``."""
    return set_status(folder, pid, f"skipped (my mistake) {H.stamp()}")


def fixed(folder: str | os.PathLike, pid: str, date: str | None = None) -> str:
    return set_status(folder, pid, f"fixed {date or H.stamp()[:10]}")


def not_fixed(folder: str | os.PathLike, pid: str, reason: str) -> str:
    return set_status(folder, pid, f"not fixed — {_one(reason) or 'no reason given'}")


# ---------------------------------------------------------------------------------------------------------------------
# from a finished run
# ---------------------------------------------------------------------------------------------------------------------

def is_tool_problem(res: dict | None) -> bool:
    """A finished run that goes into the list: FAIL, OK with a note, PICTURE NOT FOUND -- not PASS, not a run you
    stopped, not wrong input (WRONG PAIR, NOT IN THIS RAW, the input checks, a missing input file) and not a
    project another computer is working on."""
    from . import results as R
    if not isinstance(res, dict):
        return False
    label = str(res.get("label") or "")
    if label in (R.PASS, R.STOPPED, R.WRONG_PAIR, R.NOT_IN_RAW) or not label:
        return False
    if label not in (R.FAIL, R.OK_NOTE, R.PICTURE_NOT_FOUND):
        return False
    stop = str(res.get("stop") or "").strip().lower()
    if stop.startswith("the inputs look wrong") or stop.startswith("wrong pair"):
        return False
    if re.match(r"^(raw|competitor) file not found", stop) or "input file not found" in stop:
        return False
    first = str((res.get("failures") or [""])[0]).lower()
    if "is working on this project since" in first or "taking this project over" in first or \
            "keeps taking this project's lock" in first:
        return False
    return True


_NOISE = re.compile(r"(UserWarning|DeprecationWarning|FutureWarning|warnings\.warn|\[ WARN:|: pass \(\d)")
_DETAILS_IN = re.compile(r"^(?:match_cuts: )?details in \S")       # the log's path: the entry has it (log:)
_TS = re.compile(r"^(?:[\d-]+ )?\d\d:\d\d:\d\d(?:,\d+)? ")
_PRIORITY = (                                                  # (rank, pattern): the lower, the more it says
    (0, re.compile(r"(?i)^match_cuts result:")),
    (1, re.compile(r"\b(ERROR|Traceback)\b|^match_cuts: ")),
    (1, re.compile(r"XML [A-Z]{3,}|PICTURE NOT FOUND|NOT IN THIS RAW|WRONG PAIR")),
    (2, re.compile(r"^\s*(c\d|9\.\d)\b.*\bFAIL\b")),              # the summary's FAIL rows
    (3, re.compile(r"(?i)\bWARNING\b.*(flash|sync|repeat|caption|audio|person|validation|not found)")),
    (4, re.compile(r"\bWARNING\b")),
)


def _rank(s: str) -> int | None:
    for r, rx in _PRIORITY:
        if rx.search(s):
            return r
    return None


def key_lines(text: str, limit: int = MAX_LINES) -> list[str]:
    """A few lines of a run's log that say what went wrong, in the log's order: the result line, ERROR lines and a
    crash's last lines, an XML check's problem, the summary's FAIL rows, then warnings -- never a library warning (an
    OpenCV or PyTorch warning is not the reason). The same message at another time once; at most ``limit``."""
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    cands: list[tuple[int, int, str]] = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or _NOISE.search(s) or ln.startswith("  File ") or _DETAILS_IN.match(s):
            continue
        r = _rank(ln)
        if r is not None:
            cands.append((r, i, s))
    tb = [i for i, ln in enumerate(lines) if ln.startswith("Traceback")]
    if tb:                                                     # a crash: its last line says what it was
        tail = [(i, ln.strip()) for i, ln in enumerate(lines) if i > tb[-1] and ln.strip() and not ln[:1].isspace()]
        cands += [(1, i, s) for i, s in tail[:2]]
    seen: set[str] = set()
    picked: list[tuple[int, int, str]] = []
    for r, i, s in sorted(cands):
        k = _TS.sub("", s)[:200]
        if k in seen:
            continue
        seen.add(k)
        picked.append((r, i, s))
        if len(picked) >= limit:
            break
    return [s[:MAX_LINE] for _r, _i, s in sorted(picked, key=lambda t: t[1])]


def _read_log(p: str | os.PathLike | None) -> str:
    if not p:
        return ""
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(4_000_000)
    except OSError:
        return ""


def check_text(res: dict) -> str:
    """The failed check or the note, short: 'FAIL: XML PERSON ...', 'stopped (exit 3): ...'."""
    from . import results as R
    label = str(res.get("label") or "")
    first = ""
    if res.get("failures"):
        first = str(res["failures"][0])
    elif res.get("notes"):
        first = str(res["notes"][0])
    first = first.replace("every file is there, but a check of 1_edit.xml found something to look at in Premiere:",
                          "1_edit.xml check:")
    m = re.search(r"XML [A-Z][A-Z ]+?\b", first)
    word = label
    if res.get("exit") not in (None, 0, 1) and label == R.FAIL:
        word = f"stopped (exit {res.get('exit')})"
        first = re.sub(r"^the run stopped \(exit \d+\):\s*", "", first)
    elif m:
        word = f"{label}: {m.group(0).strip()}"
    return _one(f"{word} -- {first}" if first else word)[:300]


_TECHNICAL = re.compile(r"details in \S|\bfailed \(-?\d+\)|\w(?:Error|Exception):|\[(?:Errno|WinError) -?\d+\]|"
                        r"match_cuts_pool_")


def plain_check(p: Problem) -> str:
    """The entry's title in plain words for the Learn tab (day 13): an older entry whose title holds the tool's own
    technical line ("stopped (exit 2) -- details in ... match_cuts.log", "... ffmpeg failed (4294967268): ...") says
    why as match_cuts.plain_reason says it, read from the entry's own key log lines and exit code (text only:
    nothing is read). Other titles stay as they are."""
    check = str(p.check or p.result or "")
    m = re.match(r"^stopped \(exit (-?\d+)\) -- (.*)$", check)
    if not m or not _TECHNICAL.search(m.group(2)):
        return check
    try:
        from match_cuts import plain_reason as PL
        r = PL.explain(stderr="\n".join([*p.lines, m.group(2)]), code=int(m.group(1)), stop="")
    except Exception:  # noqa: BLE001 - the tool's module not importable here: as it is
        return check
    if r.kind == PL.TOOL_STOP:
        return check
    return _one(f"stopped (exit {m.group(1)}) -- {r.line()}")[:300]


def from_result(name: str, res: dict, computer: str | None = None) -> Problem:
    """The entry for a finished run (core.results.read_result's dict, plus the app's exit / log): its key log lines
    read from the run's log (batch_console.log, else match_cuts.log). Reads files: call it from a worker thread."""
    run = str(res.get("run_dir") or "")
    log = str(res.get("log") or "")
    text = _read_log(log)
    if not text and run:
        for cand in (Path(run) / "extras" / "batch_console.log", Path(run) / "extras" / "match_cuts.log"):
            text = _read_log(cand)
            if text:
                log = str(cand)
                break
    lines = key_lines(text)
    stop = _one(res.get("stop") or "")
    if stop and not any(stop[:80] in ln for ln in lines) and not _NOISE.search(stop):
        lines.append(f"stop message: {stop}")
    p = Problem(name=str(name or Path(run).parent.name or "video"), check=check_text(res), run=run, log=log,
                result=str(res.get("label") or ""), exit=str(res.get("exit") if res.get("exit") is not None else ""),
                computer=computer or H.this_computer(), date=H.stamp(), lines=lines)
    p.id = p.key()
    return p


def record(folder: str | os.PathLike, name: str, res: dict, computer: str | None = None) -> str:
    """The queue's hook: a finished run that is a tool problem (``is_tool_problem``) goes into problems_queue.md;
    anything else writes nothing. "" (done or nothing to do) or the problem in plain words."""
    try:
        if not is_tool_problem(res):
            return ""
        return add(folder, from_result(name, res, computer))
    except Exception as e:  # noqa: BLE001 - the hook never breaks the queue
        return f"the problem could not be written: {type(e).__name__}: {e}"


def open_runs(folder: str | os.PathLike) -> list[str]:
    """The run folders of the open problems -- Clean up keeps them (edit folders are never cleaned anyway; this is
    for anything that would delete a whole run)."""
    return [p.run for p in read(folder) if p.run]


def log_to_open(p: Problem, hub: str | os.PathLike | None = None) -> str:
    """The log to open for an entry: its log, else the run's batch_console.log / match_cuts.log, else the same run in
    the hub (a project moved to the shared folder when its edit was made: <hub>\\<project>\\<edit>), else the copy on
    the PC's -- the first that is there; "" when none is (the run is on the other computer)."""
    cands = [p.log]
    bases = [p.run]
    if hub is not None and p.run:
        r = Path(p.run)
        bases.append(str(Path(hub) / r.parent.name / r.name))
    for base in bases + [p.copy]:
        if base:
            cands += [str(Path(base) / "extras" / "batch_console.log"), str(Path(base) / "extras" / "match_cuts.log")]
    if p.copy and p.log:
        cands.append(str(Path(p.copy) / "extras" / Path(p.log).name))
    for c in cands:
        try:
            if c and os.path.isfile(c):
                return c
        except OSError:
            continue
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# "Copy tonight's prompt"
# ---------------------------------------------------------------------------------------------------------------------

FIX_RULES = (
    "Re-run each open problem with today's tool (the run folder, or the copy on the PC when the run is on the laptop).",
    "Find the cause and fix it; add the video as a test case.",
    "Run check-all. A fix must not make any other video worse; if it does, undo it and explain why in the report.",
    "Mark each entry in `problems_queue.md`: `status: fixed <date>` (and `[x]`), or `status: not fixed — <reason>`.",
)


def prompt_section(folder: str | os.PathLike, number: int) -> list[str]:
    """The prompt's "Problems to fix" section (lines), [] when nothing is open."""
    items = read(folder)
    if not items:
        return []
    hub = H.for_pc(folder)
    out = [f"## {number}. Problems to fix (from problems_queue.md)", "",
           f"These runs ended FAIL or \"OK with a note\" (`{hub}\\{FILE}`). For each one:", ""]
    out += [f"- {r}" for r in FIX_RULES] + [""]
    for i, p in enumerate(items, start=1):
        when = ", ".join(x for x in (H.nice_date(p.date) if p.date else "", f"on the {p.computer}"
                                     if p.computer in ("PC", "laptop") else p.computer) if x)
        out.append(f"{i}. **{p.name}** — {p.check or p.result}" + (f" ({when})" if when else ""))
        if p.run:
            out.append(f"   - Run folder: `{H.for_pc(p.run)}`")
        if p.copy:
            out.append(f"   - Copy on the PC: `{H.for_pc(p.copy)}`")
        if p.state == NOT_FIXED:
            out.append(f"   - Tried before: {p.status}")
        if p.lines:
            out.append("   - Key log lines:")
            out += [f"     > {ln}" for ln in p.lines[:MAX_LINES]]
    out.append("")
    return out
