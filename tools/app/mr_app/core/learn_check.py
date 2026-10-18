"""The check card before learning (night 11): what was found, and what differs between the tool's version (the run)
and my fixed version (the .prproj) -- a dry run of match_cuts' learn with its own readers and comparisons
(prproj.read, learn.tool_clips / user_clips / join_splits / edit_changes and its rules for a trim, a re-frame and a
zoom; the caption words aligned in order as learn aligns them, but only words near each other in the RAW): nothing
is written, no test case is made.

* RAW, competitor: the run's inputs (their original names, frame size, rate, length, the run's own copies);
* the tool's version: the run folder, when it was made and on which computer (the graphics card its log names; the
  PC and the laptop have the same host name), Fast or Thorough, the tool's own result, the tool's version and code
  fingerprint (extras\\cutlist.json provenance) -- this computer's tool now, or other code;
* my fixed version: the .prproj, when it was saved, the sequence that plays the run's RAW, its clips and captions;
* what differs, in plain words: captions "6 of 21 changed (4 words, 2 timings)", cuts "2 moved, 1 added, 0 removed",
  position "3 of 9 clips moved or zoomed" -- leaving out what you always do by hand: all audio (music, levels,
  pitch, audio moved by hand), the headline PNG and memes (other files on other tracks), trims at cut points (a cut
  moved less than edit_score's 0.5 s: the same cut, trimmed) and the ending.

The cuts and the position are compared on the picture (V1: your audio is moved by hand), the sound clips only when
your picture is not the RAW's clips; the pieces of one take (scene splits) are joined first, as learn does. A caption
edge counts as moved by the smaller of its move on the timeline and its move in the RAW (through each edit's clips),
so a cut moved earlier in the edit does not make every later caption "moved".

``check`` takes 0.05-0.3 s after search_run has read the project, about a second on its own (NOTES.md has the
times): call it from a worker thread. It never raises: what stops learning is in ``problems``, what is worth knowing
in ``notes``."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from . import learnkit as K
from . import paths

NEAR_S = 0.5                # edit_score.NEAR_S: a cut within this is the same cut, trimmed otherwise (by hand)
TRIM_S = 0.05               # learn.TRIM_S: a clip edge moved this much (RAW s) is a trim
FRAMING_PX = 40.0           # learn.FRAMING_PX: a clip moved this far sideways (sequence px) is re-framed
ZOOM_PCT = 3.0              # learn.ZOOM_PCT: ... or zoomed by this many percent points
TIMING_S = 0.08             # a caption whose start or end moved more than this (s; 2 frames at 24 fps) is retimed
WORD_WINDOW_S = 2.0         # two captions' words pair only this near each other in the RAW (a chorus repeats later)
MIN_PLAYED = 0.5            # learn.MIN_PLAYED: the run plays at least this share of what the project plays

_WORD = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’\-][0-9A-Za-zÀ-ÖØ-öø-ÿ]+)*")


@dataclass
class CheckCard:
    """What the check card shows (each a line of plain words), what stops learning (``problems``), what is worth
    knowing (``notes``), what is not compared because you do it by hand (``ignored``), the numbers behind the lines
    (``details``) and how long the check took."""
    raw: str = ""
    competitor: str = ""
    tool_version: str = ""
    fixed_version: str = ""
    captions: str = ""
    cuts: str = ""
    position: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        """The card as text lines (a plain view, a log)."""
        rows = [("RAW", self.raw), ("Competitor", self.competitor), ("The tool's version", self.tool_version),
                ("My fixed version", self.fixed_version), ("Captions", self.captions), ("Cuts", self.cuts),
                ("Position", self.position)]
        out = [f"{k}: {v}" for k, v in rows if v]
        if self.ignored:
            out.append("Not compared (you do these by hand): " + "; ".join(self.ignored))
        out += [f"Problem: {p}" for p in self.problems] + [f"Note: {n}" for n in self.notes]
        return out


def check(prproj: str | Path, run_dir: str | Path) -> CheckCard:
    """The check card of a fixed project and the run it was made from (module docstring). Never raises."""
    t0 = time.time()
    card = CheckCard()
    try:
        _check(Path(prproj), Path(run_dir), card)
    except Exception as e:  # noqa: BLE001 - never into the window
        card.problems.append(f"the check stopped: {type(e).__name__}: {e}")
    card.seconds = round(time.time() - t0, 2)
    return card


# ---------------------------------------------------------------------------------------------------------------------
# the lines
# ---------------------------------------------------------------------------------------------------------------------

def when(t: float | datetime | None) -> str:
    """'17 Oct 06:23' (the board's style)."""
    if t is None:
        return "?"
    d = t if isinstance(t, datetime) else datetime.fromtimestamp(float(t))
    return f"{d.day} {d:%b %H:%M}"


def _fps(v: Any) -> str:
    try:
        from fractions import Fraction
        return f"{float(Fraction(str(v))):.3f}".rstrip("0").rstrip(".")
    except (ValueError, ZeroDivisionError, TypeError):
        return "?"


def _size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def _input_line(info: dict, run_file: Path | None) -> str:
    """'_222FIXEDThe.Office...mp4 -- 1920x1080, 23.976 fps, 22:11 (the run's copy: extras\\media\\raw.mp4, 824 MB)'."""
    if not info:
        return "not in the run's cut list"
    name = K._source_name(info) or "?"
    facts = f"{info.get('width')}x{info.get('height')}, {_fps(info.get('fps'))} fps, " \
            f"{K._dur(float(info.get('duration_s') or 0))}"
    where = ""
    if run_file is not None:
        try:
            where = f" (the run's copy: {K.show(run_file)}, {_size(run_file.stat().st_size)})"
        except OSError:
            where = f" (the run's copy: {K.show(run_file)})"
    return f"{name} — {facts}{where}"


def _head(p: Path, n: int = 65536) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(n)
    except OSError:
        return ""


def log_facts(run: Path) -> dict:
    """What a run's logs say about how it was made: {started (datetime), gpu, fast (bool or None), speed, result,
    made_in (the run folder on the computer that made it)} -- whatever they hold."""
    out: dict[str, Any] = {}
    log = _head(run / "extras" / "match_cuts.log")
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", log)
    if m:
        try:
            out["started"] = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    est = re.search(r"time estimate: [^(\n]*\((?P<gpu>[^,\n]+),\s*(?P<mode>[^,\n]+),", log)
    if est:
        out["gpu"] = est.group("gpu").strip()
        out["fast"] = "--fast" in est.group("mode")
    con = _head(run / "extras" / "batch_console.log", 32768)
    first = con.splitlines()[0] if con else ""
    if " -m match_cuts" in first:
        out["fast"] = "--fast" in first.split()
        sp = re.search(r"--speed\s+(\S+)", first)
        if sp:
            out["speed"] = sp.group(1)
        rd = re.search(r"--run-dir\s+(.+?)(?:\s+--\w|$)", first)
        if rd:
            out["made_in"] = rd.group(1).strip()
    res = re.search(r"^match_cuts result:\s*(\S+)", con, re.M)
    if res:
        out["result"] = res.group(1)
    if "gpu" not in out:
        g = re.search(r"on the GPU \(([^)]+)\)", con) or re.search(r"on the GPU \(([^)]+)\)", log)
        if g:
            out["gpu"] = g.group(1).strip()
    return out


@lru_cache(maxsize=1)
def tool_code_hash() -> str:
    """This computer's tool code fingerprint, the way a run records it (pipeline.code_hash: match_cuts\\*.py and
    ae_mock\\*.js); "" when the tool's folder cannot be found."""
    try:
        import match_cuts
        root = Path(match_cuts.__file__).resolve().parent
    except ImportError:
        root = paths.repo() / "tools" / "match_cuts" / "match_cuts"
    if not root.is_dir():
        return ""
    h = hashlib.blake2b(digest_size=10)
    try:
        for p in sorted(list(root.glob("*.py")) + list((root / "ae_mock").glob("*.js"))):
            h.update(p.name.encode())
            h.update(p.read_bytes())
    except OSError:                                  # a file changing under us (a git pull): cannot tell
        return ""
    return h.hexdigest()


def _computer(run: Path, facts: dict) -> str:
    """'the laptop (NVIDIA GeForce RTX 4060 Laptop GPU)' -- project.json's computer when it says pc / laptop, else the
    graphics card in the run's log (both computers are called "Gvidas")."""
    proj = K._project_of(run)
    kind = ""
    if proj is not None:
        try:
            import json
            d = json.loads((proj / "project.json").read_text(encoding="utf-8"))
            kind = str(((d.get("steps") or {}).get("edit") or {}).get("computer") or "").lower()
        except (OSError, ValueError, AttributeError):
            kind = ""
    gpu = facts.get("gpu") or ""
    if kind not in ("pc", "laptop") and gpu:
        kind = "laptop" if "laptop" in gpu.lower() else "pc"
    if not kind:
        return "on a computer its log does not name"
    who = "the laptop" if kind == "laptop" else "the PC"
    return f"on {who}" + (f" ({gpu})" if gpu else "")


def _tool_line(run: Path, meta: Any) -> str:
    facts = log_facts(run)
    fin = K._finished_at(run) or None
    made = facts.get("started")
    span = (f"made {when(made)}" + (f"-{datetime.fromtimestamp(fin):%H:%M}" if fin and made and
                                     datetime.fromtimestamp(fin).date() == made.date() else
                                     (f", finished {when(fin)}" if fin else ""))) if made else \
        (f"finished {when(fin)}" if fin else "not finished")
    bits = [K.show(run), span, _computer(run, facts)]
    if facts.get("fast") is not None:
        bits.append("Fast" if facts["fast"] else "Thorough")
    if facts.get("speed") and str(facts["speed"]) not in ("100", "100.0"):
        bits.append(f"speed {facts['speed']} %")
    if facts.get("result"):
        bits.append(f"its own checks: {facts['result']}")
    prov = (meta.provenance if meta is not None else {}) or {}
    if prov.get("code_hash"):
        now = tool_code_hash()
        same = (" -- the same code as this computer's tool now" if now and now == prov["code_hash"] else
                " -- other code than this computer's tool now" if now else "")
        bits.append(f"tool {prov.get('version') or '?'}, code {str(prov['code_hash'])[:8]}{same}")
    return ", ".join(b for b in bits if b)


def _fixed_line(prproj: Path, pr: Any, seq: Any, clips: int | None = None, caps: int | None = None) -> str:
    try:
        saved = f"saved {when(prproj.stat().st_mtime)}"
    except OSError:
        saved = ""
    bits = [K.show(prproj), saved]
    if seq is not None:
        bits.append(f"sequence {K.sequence_text(pr, seq)}")
    if clips is not None:
        bits.append(f"{clips} clip{'' if clips == 1 else 's'} of the RAW")
    if caps is not None:
        bits.append(f"{caps} caption{'' if caps == 1 else 's'}")
    return ", ".join(b for b in bits if b)


# ---------------------------------------------------------------------------------------------------------------------
# what differs
# ---------------------------------------------------------------------------------------------------------------------

def _overlap(a: Any, b: Any) -> float:
    return max(0.0, min(a.r1, b.r1) - max(a.r0, b.r0))


def _shared(c: Any, u: Any) -> bool:
    """The same piece of the RAW: more than a sliver of the shorter one (learn.edit_changes' rule)."""
    return _overlap(c, u) > min(0.1, 0.5 * min(abs(c.r1 - c.r0), abs(u.r1 - u.r0)))


def cut_changes(tool: Sequence[Any], user: Sequence[Any]) -> dict:
    """The cuts of the tool's edit against yours (both: learn.Clip lists, one take's pieces joined). First the clips
    one side has and the other does not (learn.edit_changes' rule: no piece of the RAW in common) -- clips_removed /
    clips_added, with their RAW seconds; then the cuts between the clips both have, matched by the RAW on both sides
    of each cut: {same, trimmed (moved less than NEAR_S: by hand), moved, removed (yours plays on there), added (a cut
    of yours the tool does not make: inside a take), start / end (your first clip's start and last clip's end against
    the tool's, RAW s, + = later), length (your edit's against the tool's, s), shifts (each matched cut's move, RAW
    s)}. The edit's start counts as a cut point; the ending never does (you trim it by hand)."""
    t_all = sorted(tool, key=lambda c: c.t0)
    u_all = sorted(user, key=lambda c: c.t0)
    tool = [c for c in t_all if any(_shared(c, u) for u in u_all)]
    user = [u for u in u_all if any(_shared(c, u) for c in t_all)]
    out = {"same": 0, "trimmed": 0, "moved": 0, "removed": 0, "added": 0, "shifts": [], "start": None, "end": None,
           "length": None, "clips_removed": len(t_all) - len(tool), "clips_added": len(u_all) - len(user),
           "removed_s": round(sum(abs(c.r1 - c.r0) for c in t_all if c not in tool), 3),
           "added_s": round(sum(abs(u.r1 - u.r0) for u in u_all if u not in user), 3)}
    used: set[int] = set()
    for k in range(len(tool) - 1):
        a, b = tool[k], tool[k + 1]
        hit = next((j for j in range(len(user) - 1) if j not in used and _shared(a, user[j])
                    and _shared(b, user[j + 1])), None)
        if hit is None:
            out["removed"] += 1
            continue
        used.add(hit)
        d = max(abs(user[hit].r1 - a.r1), abs(user[hit + 1].r0 - b.r0))
        out["shifts"].append(round(d, 3))
        out["moved" if d > NEAR_S else "trimmed" if d >= TRIM_S else "same"] += 1
    out["added"] = max(0, len(user) - 1 - len(used))
    if tool and user:
        if _shared(tool[0], user[0]):
            out["start"] = d = round(user[0].r0 - tool[0].r0, 3)          # the first cut-in counts as a cut point
            if abs(d) > NEAR_S:
                out["moved"] += 1
            elif abs(d) >= TRIM_S:
                out["trimmed"] += 1
        if _shared(tool[-1], user[-1]):
            out["end"] = round(user[-1].r1 - tool[-1].r1, 3)              # the ending: by hand, never counted
    if t_all and u_all:
        out["length"] = round((u_all[-1].t1 - u_all[0].t0) - (t_all[-1].t1 - t_all[0].t0), 3)
    return out


def position_changes(tool_pic: Sequence[Any], user_pic: Sequence[Any]) -> dict:
    """Each picture clip of the tool's edit against your clip that plays the most of its RAW: {clips (compared),
    changed (moved sideways >= FRAMING_PX or zoomed >= ZOOM_PCT), moved, zoomed, unknown (keyframed: not
    compared), dx / dz (each clip's move, px / %)}."""
    out = {"clips": 0, "changed": 0, "moved": 0, "zoomed": 0, "unknown": 0, "dx": [], "dz": []}
    for c in tool_pic:
        mine = [u for u in user_pic if _overlap(c, u) > 0.0]
        if not mine:
            continue
        u = max(mine, key=lambda u: _overlap(c, u))
        dx = None if c.dx is None or u.dx is None else round(float(u.dx) - float(c.dx), 1)
        dz = None if c.scale is None or u.scale is None else round(float(u.scale) - float(c.scale), 2)
        if dx is None and dz is None:
            out["unknown"] += 1
            continue
        out["clips"] += 1
        mv, zm = dx is not None and abs(dx) >= FRAMING_PX, dz is not None and abs(dz) >= ZOOM_PCT
        out["moved"] += mv
        out["zoomed"] += zm
        out["changed"] += mv or zm
        out["dx"].append(dx)
        out["dz"].append(dz)
    return out


def _words(caps: Sequence[tuple[float, float, str]]) -> tuple[list[str], list[tuple[int, int]]]:
    """All the captions' words (lower case, curly apostrophes made straight) and each caption's span in them."""
    words: list[str] = []
    spans = []
    for _a, _b, text in caps:
        i0 = len(words)
        words += [w.lower() for w in _WORD.findall(str(text).replace("’", "'"))]
        spans.append((i0, len(words)))
    return words, spans


def _raw_at(clips: Sequence[Any], t: float, end: bool = False) -> float | None:
    """Timeline second ``t`` as a RAW second through an edit's clips (``end``: a caption's end -- the clip that ends
    there); None off the clips."""
    for c in clips:
        if (c.t0 - 1e-3 < t <= c.t1 + 1e-3) if end else (c.t0 - 1e-3 <= t < c.t1 - 1e-3):
            return c.r0 + (t - c.t0) * ((c.r1 - c.r0) / (c.t1 - c.t0) if c.t1 > c.t0 else 1.0)
    return None


def _word_times(caps: Sequence[tuple[float, float, str]], spans: Sequence[tuple[int, int]],
                clips: Sequence[Any]) -> list[tuple[float | None, float]]:
    """Each word's moment: (RAW second through the edit's clips or None, timeline second) -- the words spread evenly
    over their caption."""
    out: list[tuple[float | None, float]] = []
    for (a, b, _t), (c, d) in zip(caps, spans):
        n = d - c
        for k in range(n):
            t = a + (b - a) * (k + 0.5) / n
            out.append((_raw_at(clips, t), t))
    return out


def _near(x: tuple[float | None, float], y: tuple[float | None, float], window: float) -> bool:
    if x[0] is not None and y[0] is not None:
        return abs(x[0] - y[0]) <= window
    return abs(x[1] - y[1]) <= window


def align_words(tw: Sequence[str], tt: Sequence[tuple[float | None, float]], uw: Sequence[str],
                ut: Sequence[tuple[float | None, float]], window: float = WORD_WINDOW_S) -> list[int | None]:
    """For each of your words, the tool's word it is (or None): the longest common run of the same words in order,
    as learn's difflib alignment -- but two words pair only within ``window`` seconds of the RAW, so a song's chorus
    pairs with the same chorus, not with its repeat 20 s later (chocolate)."""
    n, m = len(tw), len(uw)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            if tw[i - 1] == uw[j - 1] and _near(tt[i - 1], ut[j - 1], window):
                row[j] = prev[j - 1] + 1
            else:
                row[j] = row[j - 1] if row[j - 1] > prev[j] else prev[j]
    out: list[int | None] = [None] * m
    i, j = n, m
    while i > 0 and j > 0:
        if tw[i - 1] == uw[j - 1] and _near(tt[i - 1], ut[j - 1], window) and dp[i][j] == dp[i - 1][j - 1] + 1:
            out[j - 1] = i - 1
            i, j = i - 1, j - 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return out


def caption_changes(tool: Sequence[tuple[float, float, str]], user: Sequence[tuple[float, float, str]],
                    tool_map: Sequence[Any] = (), user_map: Sequence[Any] = (), tool_end: float | None = None,
                    tol: float = TIMING_S) -> dict:
    """Your captions against the tool's, caption by caption, the words aligned in order (align_words: learn's word
    alignment, kept to words near each other in the RAW): {captions (yours), changed, words (other words: a word
    changed, added or removed), grouping (the same words split or joined otherwise), timing (the same caption, its
    start or end moved more than ``tol``), spelling (the same words, other capitals or punctuation), removed (the
    tool's captions you deleted)}. Times are compared as RAW moments through each edit's clips (``tool_map`` /
    ``user_map``; the timeline when a moment is off the clips); the end of the tool's last caption -- and of one at
    the edit's end (``tool_end``) -- is not compared: you trim the ending by hand."""
    tw, ts = _words(tool)
    uw, us = _words(user)
    u2t = align_words(tw, _word_times(tool, ts, tool_map), uw, _word_times(user, us, user_map))
    by_span = {span: i for i, span in enumerate(ts) if span[1] > span[0]}
    blank = [i for i, (c, d) in enumerate(ts) if d == c]                 # wordless captions: *...*, a music note
    out = {"captions": len(user), "changed": 0, "words": 0, "grouping": 0, "timing": 0, "spelling": 0, "removed": 0,
           "examples": []}
    hit = {x for x in u2t if x is not None}
    word_cap = [i for i, (c, d) in enumerate(ts) for _k in range(c, d)]          # the tool's word -> its caption
    edited = {i for i, (c, d) in enumerate(ts) if any(k not in hit for k in range(c, d))}   # lost a word

    def delta(t_tool: float, t_user: float, end: bool) -> float:
        """How far a caption edge moved: the smaller of the move on the timeline and the move in the RAW -- a cut
        moved earlier in the edit shifts the timeline only, an edge moved across a cut the RAW only."""
        d_tl = abs(t_user - t_tool)
        r_t, r_u = _raw_at(tool_map, t_tool, end), _raw_at(user_map, t_user, end)
        return d_tl if r_t is None or r_u is None else min(d_tl, abs(r_u - r_t))

    def moved(i: int, a: float, b: float) -> bool:
        ta, tb, _t = tool[i]
        if delta(ta, a, False) > tol:
            return True
        if i == len(tool) - 1 or (tool_end is not None and tb >= tool_end - tol):
            return False                                                 # the ending: by hand
        return delta(tb, b, True) > tol
    for j, (a, b, text) in enumerate(user):
        c, d = us[j]
        if d == c:
            norm = " ".join(str(text).split())
            i = next((i for i in blank if " ".join(str(tool[i][2]).split()) == norm), None)
            if i is None:
                kind = "words"
            else:
                blank.remove(i)
                kind = "timing" if moved(i, a, b) else ""
        else:
            mapped = u2t[c:d]
            if any(x is None for x in mapped) or any(word_cap[x] in edited for x in mapped):
                kind = "words"                                           # a word of yours, or one you took out
            elif mapped != list(range(mapped[0], mapped[-1] + 1)) or (mapped[0], mapped[-1] + 1) not in by_span:
                kind = "grouping"
            else:
                i = by_span[(mapped[0], mapped[-1] + 1)]
                if moved(i, a, b):
                    kind = "timing"
                elif " ".join(str(tool[i][2]).split()) != " ".join(str(text).split()):
                    kind = "spelling"
                else:
                    kind = ""
        if kind:
            out[kind] += 1
            out["changed"] += 1
            if len(out["examples"]) < 4 and kind in ("words", "spelling"):
                out["examples"].append(" ".join(str(text).split()))
    out["removed"] = sum(1 for c, d in ts if d > c and not any(i in hit for i in range(c, d)))
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def caption_line(cc: dict, look: str) -> str:
    """'6 of 21 changed (4 words, 2 timings); 1 of the tool's removed; look: ...'."""
    if not cc["captions"]:
        return "none in your project"
    kinds = [_plural(cc[k], label, many) for k, label, many in
             (("words", "word", "words"), ("grouping", "grouping", "groupings"), ("timing", "timing", "timings"),
              ("spelling", "spelling", "spellings")) if cc[k]]
    line = f"{cc['changed']} of {cc['captions']} changed" + (f" ({', '.join(kinds)})" if kinds else "")
    if cc["removed"]:
        line += f"; {cc['removed']} of the tool's removed"
    return line + (f"; look: {look}" if look else "")


def cut_line(cc: dict, key: str) -> str:
    """'2 moved, 1 added, 0 removed; 1 clip of the tool's left out (0.5 s of the RAW)'."""
    line = f"{cc['moved']} moved, {cc['added']} added, {cc['removed']} removed"
    if cc.get("clips_removed"):
        line += (f"; {_plural(cc['clips_removed'], 'clip')} of the tool's left out ({cc['removed_s']:.1f} s of the "
                 "RAW)")
    if cc.get("clips_added"):
        line += f"; {_plural(cc['clips_added'], 'clip')} of yours added ({cc['added_s']:.1f} s of the RAW)"
    if key != "picture":
        line += " (compared on the sound: your picture is not the RAW's clips)"
    return line


def position_line(pc: dict) -> str:
    """'3 of 9 clips moved or zoomed (2 moved sideways, 1 zoomed)'."""
    if not pc["clips"]:
        return "not compared" + (f" ({_plural(pc['unknown'], 'clip')} keyframed, or without a known position)"
                                 if pc["unknown"] else ": no picture clip of the RAW on both sides")
    line = f"{pc['changed']} of {pc['clips']} clips moved or zoomed"
    if pc["changed"]:
        line += f" ({pc['moved']} moved sideways, {pc['zoomed']} zoomed)"
    if pc["unknown"]:
        line += f"; {_plural(pc['unknown'], 'clip')} not compared (keyframed, or without a known position)"
    return line


# ---------------------------------------------------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------------------------------------------------

def _check(prproj: Path, run: Path, card: CheckCard) -> None:
    t0 = time.time()
    try:
        PR, L = K.tool()
    except ImportError as e:
        card.problems.append(f"the tool (tools\\match_cuts) cannot be loaded: {e}")
        return
    rf = L.run_files(run)
    if rf is None:
        card.problems.append(f"{K.show(run)} is not a run folder of the tool (no 1_edit.xml)")
        return
    meta = K.run_meta(run)
    cl = {"raw": meta.raw, "competitor": meta.competitor} if meta is not None else {}
    files = {}
    for role in ("raw", "competitor"):
        try:
            files[role] = L.run_media(rf, cl, role) if cl else None
        except Exception:  # noqa: BLE001 - a probe that failed: not found
            files[role] = None
    card.raw = _input_line(cl.get("raw") or {}, files["raw"])
    card.competitor = _input_line(cl.get("competitor") or {}, files["competitor"])
    card.details.update(raw_name=K._source_name(cl.get("raw") or {}),
                        competitor_name=K._source_name(cl.get("competitor") or {}))
    card.tool_version = _tool_line(run, meta)
    if not K.is_finished(run):
        card.problems.append(f"the run {K.show(run)}: {K.UNFINISHED}")
    if meta is None:
        card.problems.append(f"the run {K.show(run)} has no extras\\cutlist.json: it cannot be compared")
        return
    for role, label in (("raw", "RAW"), ("competitor", "competitor")):
        if files.get(role) is None:
            card.problems.append(f"the run's {label} is not in its extras\\media any more (moved or deleted?): "
                                 "learning needs it")
    if not prproj.is_file():
        card.problems.append(f"{prproj.name}: not there")
        return
    got = K._read_project(prproj)
    if got is None:
        card.problems.append(f"I can't read {prproj.name}: it is not a Premiere project, or it is damaged")
        return
    pr, _aliases = got
    found = K.project_raw(pr, meta.raw)
    if found is None:
        card.fixed_version = _fixed_line(prproj, pr, None)
        card.problems.append(f"your project does not play this run's RAW: {K._other_raw(pr, meta.raw)} -- is it the "
                             "right run?")
        return
    media, seq, byname = found
    if not byname:
        card.notes.append(f"your project plays the RAW under another name ({K._file_name(media)})")
    flips = L.project_flips(prproj)
    tool_pic, tool_snd, _fps, _w = L.tool_clips(rf["edit"], float(meta.raw.get("width") or 0) or None,
                                                unflip=flips == 0)
    user_pic, user_snd = L.user_clips(seq, PR.file_name(media))
    card.details["read_s"] = round(time.time() - t0, 3)
    # cuts and position: the picture (your audio is moved by hand); the sound only when the picture is not the RAW's
    key = "picture" if user_pic and tool_pic else "sound"
    tk = L.join_splits(tool_pic if key == "picture" else (tool_snd or tool_pic))
    uk = L.join_splits(user_pic if key == "picture" else (user_snd or user_pic))
    if not tk or not uk:
        card.problems.append(f"no clip of {'your project' if not uk else 'the run'} plays the RAW: nothing to compare")
        return
    e = L.edit_changes(tk, uk, tool_pic, user_pic)
    card.details["learn"] = {k: e[k] for k in ("clips", "removed", "added", "inner", "played", "framing")}
    if e["played"] < MIN_PLAYED:
        card.problems.append(f"the run's edit plays only {100 * e['played']:.0f} % of what your project plays: it is "
                             "probably not the run your project was made from -- pick another")
    cc = cut_changes(tk, uk)
    card.cuts = cut_line(cc, key)
    card.details["cuts"] = {**cc, "key": key}
    pc = position_changes(tool_pic, user_pic)
    card.position = position_line(pc)
    card.details["position"] = pc
    if e.get("framing") != "compared":
        card.notes.append(f"framing {e['framing']}")
    # captions
    from match_cuts.captions import read_srt
    tool_caps = [(c["start_ms"] / 1000.0, c["end_ms"] / 1000.0, c["text"]) for c in read_srt(rf["captions"])] \
        if rf["captions"].is_file() else []
    ucaps = PR.captions_of(seq)
    user_caps = [(c.start, c.end, c.text) for c in ucaps if str(c.text).strip()]
    look = ""
    if ucaps:
        k0, n0 = ucaps[0].kind, ucaps[0].track
        look = (f"text graphics on V{n0} (styled by you)" if k0 == PR.VIDEO else f"the caption track C{n0}")
    elif L.hidden_captions_note(seq):
        card.notes.append(L.hidden_captions_note(seq))
    tool_end = max((c.t1 for c in (tool_snd or tool_pic)), default=None)
    capc = caption_changes(tool_caps, user_caps, tool_snd or tool_pic, user_snd or user_pic, tool_end,
                           max(TIMING_S, 1.5 / float(seq.fps or 60.0)))
    card.captions = caption_line(capc, look) if tool_caps or user_caps else "none on either side"
    if not tool_caps:
        card.notes.append("the run has no 2_captions.srt: your captions are compared with nothing")
    card.details["captions"] = {**capc, "tool": len(tool_caps), "look": look}
    card.fixed_version = _fixed_line(prproj, pr, seq, len(user_pic or user_snd), len(user_caps))
    # what is not compared: you do it by hand
    others = sorted({PR.file_name(it.media) for it in seq.items if it.kind == PR.VIDEO and it.enabled and it.media
                     and it.media != media and not PR.file_name(it.media).isdigit()})   # digits: Premiere's own text
    card.ignored.append("all audio (music, levels, pitch, audio you moved by hand)")
    if others:
        card.ignored.append(f"the headline PNG and memes ({_plural(len(others), 'other file')} on the picture tracks"
                            f": {', '.join(others[:3])}{' ...' if len(others) > 3 else ''})")
    if cc["trimmed"]:
        card.ignored.append(f"{_plural(cc['trimmed'], 'trim')} at cut points (moved less than {NEAR_S:g} s)")
    if cc["end"] is not None and abs(cc["end"]) >= TRIM_S:
        card.ignored.append(f"the ending (yours ends {cc['end']:+.2f} s against the tool's)")
    card.details["check_s"] = round(time.time() - t0, 3)
