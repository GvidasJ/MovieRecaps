"""learn.py: ``python -m match_cuts learn "<finished project>.prproj"`` -- what the user corrected in Premiere, kept
for next time (Task 6).

The project is a run's edit (1_edit.xml and 2_captions.srt imported into Premiere) as the user finished it. learn:

1. finds the run: the RAW the project's clips play lies in the run's media folder (``<run>/extras/media/``; the older
   flat layout ``<out>/media/`` too) -- 1_edit.xml points there by its absolute path -- or ``--run``. The run must
   play most of what the project's clips play (else it is not the run the project was made from);
2. captions: the user's caption track against 2_captions.srt, word by word -- aligned on the words, not the times (a
   moved cut shifts every caption after it) -- and only between anchors (CONTEXT words the same on both sides): a
   word the user wrote differently goes into the glossary (``caption_glossary.txt`` next to
   caption_allowlist.txt, ``heard -> written``). Next time a heard word with a glossary entry is replaced by it only
   where the audio fits (caption_recheck.glossary_readings: every speech model finds the written form at least as
   likely); the glossary is not given to the speech models as hot words (captions.caption_hints);
3. cuts and framing: the user's V1 clips against 1_edit.xml's, matched by the RAW they play -- each clip's start and
   end moved (RAW seconds), clips removed, clips added, the framing moved sideways / zoomed -- kept in the new test
   case's ``learned.json``; a kind of change the user makes on SEVERAL videos (every learned.json of the test
   library) becomes a suggested new default, printed, never applied;
4. a test case in ``tests/real/<name>/``: competitor.mp4 and raw.mp4 (over 100 MB: a smaller copy with the same size
   and frame rate, testcases.small_copy), answer.srt (the user's captions), answer_edit.json (the user's timeline:
   what the RAW plays where) and case.json -- check-all then tests every video the user ever corrected. A case of
   the same competitor is updated, not doubled;
5. prints a short summary and the exact git commands that push the new case.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import prproj as PR
from .run_folders import CAPTIONS_SRT, EDIT_XML, EXTRAS

OLD_EDIT_XML = "recreated_edit.xml"          # the flat layout of older runs
OLD_CAPTIONS = "captions.srt"
GLOSSARY_NAME = "caption_glossary.txt"
CONTEXT = 2                 # a word change counts only between this many unchanged words on each side
MAX_CHANGE = 3              # ... and of at most this many words on each side (more: a rewrite, not a word)
SIMILAR = 0.5               # the glossary: a changed word spelled this much like the heard one (letters, 0-1) --
                            # "zendeya" -> "Zendaya"; other changes are kept in learned.json only
TRIM_S = 0.05               # a clip edge moved this much (RAW s) is a trim
FRAMING_PX = 40.0           # a framing moved this far sideways (sequence px) is a re-frame
ZOOM_PCT = 3.0              # ... or zoomed by this many percent points
MIN_PLAYED = 0.5            # the run plays at least this share of what the project's clips play
TENDENCY = 0.3              # a video "does" a kind of change when this share of its clips does (at least 3 clips)
SEVERAL = 3                 # the same kind on this many videos: a suggested new default


class LearnError(Exception):
    """learn cannot go on; the message says why (nothing was written)."""


# ---------------------------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------------------------

def finished_run(d: str | Path) -> bool:
    """A run folder whose run has finished: its report (the last file a run writes) is there. 1_edit.xml and
    cutlist.json are written long before the captions, the checks and the report -- a run still going on (or one that
    stopped early) must not be read as a finished one (Task 8: video4's run read mid-run kept 1 change of 19 instead
    of 3)."""
    d = Path(d)
    return (d / EXTRAS / "report.md").is_file() or (d / OLD_EDIT_XML).is_file() and (d / "report.md").is_file()


def unfinished(d: str | Path) -> str:
    return (f"{d} is not a finished run (no {EXTRAS}/report.md: the run is still going on, or it stopped early) -- "
            "wait for it to finish, or give another run")


def run_files(d: Path) -> dict[str, Path] | None:
    """The files of a run folder: the numbered layout (1_edit.xml, extras/) or the older flat one."""
    d = Path(d)
    if (d / EDIT_XML).is_file():
        return {"dir": d, "edit": d / EDIT_XML, "captions": d / CAPTIONS_SRT, "cutlist": d / EXTRAS / "cutlist.json",
                "media": d / EXTRAS / "media"}
    if (d / OLD_EDIT_XML).is_file():
        return {"dir": d, "edit": d / OLD_EDIT_XML, "captions": d / OLD_CAPTIONS, "cutlist": d / "cutlist.json",
                "media": d / "media"}
    return None


def media_dirs(seq: PR.Sequence) -> list[Path]:
    """Run folders the project's media point into (a file in a ``media`` folder: its run is the folder above, past
    ``extras``), most used first."""
    count: dict[Path, int] = {}
    for it in seq.items:
        p = Path(str(it.media or "").replace("\\", "/"))
        if p.parent.name.lower() != "media":
            continue
        d = p.parent.parent
        if d.name.lower() == EXTRAS:
            d = d.parent
        count[d] = count.get(d, 0) + 1
    return sorted(count, key=lambda d: -count[d])


def media_mismatch(props: dict, cutlist: dict) -> str | None:
    """How the RAW the project plays (``props``: prproj Project.media) differs from the run's RAW (its cutlist), or
    None when they agree or one side does not say. Size, frame rate and length (a smaller copy keeps all three)."""
    r = cutlist.get("raw") or {}
    if not props or not r:
        return None
    from fractions import Fraction
    try:
        run_fps = float(Fraction(str(r.get("fps")))) if r.get("fps") else None
    except (ValueError, ZeroDivisionError):
        run_fps = None
    run_dur = float(r["duration_s"]) if r.get("duration_s") else None
    bad = []
    if props.get("width") and r.get("width") and (props["width"], props["height"]) != (int(r["width"]),
                                                                                       int(r["height"])):
        bad.append(f"{r['width']}x{r['height']} against {props['width']}x{props['height']}")
    if props.get("fps") and run_fps and abs(props["fps"] / run_fps - 1.0) > 1e-3:
        bad.append(f"{run_fps:.3f} fps against {props['fps']:.3f}")
    if props.get("duration") and run_dur and abs(props["duration"] - run_dur) > max(1.0, 0.01 * run_dur):
        bad.append(f"{run_dur:.1f} s against {props['duration']:.1f} s")
    return "; ".join(bad) or None


def find_run(seq: PR.Sequence, run: str | Path | None = None) -> dict[str, Path]:
    """The run the project was made from (``run``: given)."""
    if run:
        rf = run_files(Path(run))
        if rf is None:
            raise LearnError(f"{run}: no {EDIT_XML} (or {OLD_EDIT_XML}) there -- not a run folder")
        if not finished_run(run):
            raise LearnError(unfinished(run))
        return rf
    for d in media_dirs(seq):
        rf = run_files(d)
        if rf is not None:
            if not finished_run(d):
                raise LearnError(unfinished(d))
            return rf
    raise LearnError("the project's clips do not play a run's media (<run>/extras/media/...): give the run folder "
                     "with --run")


def load_cutlist(rf: dict[str, Path]) -> dict:
    p = rf["cutlist"]
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def run_media(rf: dict[str, Path], cutlist: dict, kind: str) -> Path | None:
    """The run's input ``kind`` ('competitor' / 'raw'): the original file when it still exists, else the run's
    copy in its media folder."""
    info = cutlist.get(kind) or {}
    for key in ("source_path", "file_abs"):
        p = info.get(key)
        if p and Path(p).is_file():
            return Path(p)
    rel = info.get("file") or info.get("file_rel")
    if rel and (rf["dir"] / EXTRAS / rel).is_file():
        return rf["dir"] / EXTRAS / rel
    if rel and (rf["dir"] / rel).is_file():
        return rf["dir"] / rel
    return None


# ---------------------------------------------------------------------------------------------------------------------
# captions: the words the user changed
# ---------------------------------------------------------------------------------------------------------------------

_WORD = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’\-][0-9A-Za-zÀ-ÖØ-öø-ÿ]+)*")


def words_of(caps: Sequence[str]) -> list[tuple[str, bool]]:
    """The words of a caption list in order: (word as written, first word of its caption)."""
    out = []
    for text in caps:
        for i, m in enumerate(_WORD.finditer(str(text).replace("’", "'"))):
            out.append((m.group(0), i == 0))
    return out


def _n(w: str) -> str:
    return w.lower().replace("’", "'")


@dataclass
class WordChange:
    heard: str                   # the tool's words
    written: str                 # the user's
    kind: str                    # 'words' (other words) | 'case' (the same words, the user's capitals)
    before: str = ""             # the unchanged words around it (for the summary)
    after: str = ""
    glossary: bool = True        # a correction the glossary keeps (else learned.json only)
    why: str = ""                # why not


def _letters(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


INFORMAL = {"gonna": "going to", "wanna": "want to", "gotta": "got to", "kinda": "kind of", "sorta": "sort of",
            "lemme": "let me", "gimme": "give me", "outta": "out of", "dunno": "don't know", "cause": "because",
            "'cause": "because", "cuz": "because", "ya": "you", "yeah": "yes", "nah": "no"}


def informal_pair(a: str, b: str) -> bool:
    """``a`` and ``b`` are one phrase in its spoken and its written form ("gonna" / "going to"): a style you choose
    per video (video4: "going to"; Deadpool: "gonna"), not a spelling."""
    x, y = " ".join(_n(a).split()), " ".join(_n(b).split())
    return INFORMAL.get(x) == y or INFORMAL.get(y) == x


def judge_change(c: WordChange) -> WordChange:
    """Whether a change is a glossary correction: a changed word spelled like the heard one (SIMILAR), or capitals
    a name or an acronym takes -- not a plain word in capitals for emphasis ("like" -> "LIKE") or at a caption's
    start (the style), nor a spoken form against its written one ("gonna" -> "going to": the video's style)."""
    from .caption_rules import lexicon
    if c.kind == "words" and informal_pair(c.heard, c.written):
        c.glossary, c.why = False, "a spoken form against its written one: your style for this video, not a spelling"
        return c
    lex = lexicon()
    if c.kind == "words":
        if _letters(c.heard) == _letters(c.written):
            return c              # the same letters: a spelling (a hyphen, an apostrophe, a space) -- the glossary
        words = [_n(w).strip("'") for w in (c.heard + " " + c.written).split()]
        if words and all(w in lex.lower for w in words):
            c.glossary, c.why = False, ("other ordinary words: what was heard there, not a spelling or a name "
                                        "(\"of\" -> \"to\" would change every \"of\" the audio allows)")
            return c
        r = difflib.SequenceMatcher(None, _letters(c.heard), _letters(c.written)).ratio()
        if r < SIMILAR:
            c.glossary, c.why = False, f"other words, not a spelling: similarity {r:.2f}"
        return c
    lw = _n(c.heard)
    if lw in lex.lower and c.written not in lex.forms.get(lw, set()):
        c.glossary, c.why = False, "an ordinary word: emphasis, not a name"
    return c


def caption_changes(tool: Sequence[str], user: Sequence[str]) -> tuple[list[WordChange], dict]:
    """The words the user changed (module docstring, 2) and counts {words, case, removed, added, rewritten}."""
    a, b = words_of(tool), words_of(user)
    na, nb = [_n(w) for w, _ in a], [_n(w) for w, _ in b]
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    ops = sm.get_opcodes()
    out: list[WordChange] = []
    counts = {"words": 0, "case": 0, "removed": 0, "added": 0, "rewritten": 0}
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            for (wa, _fa), (wb, fb) in zip(a[i1:i2], b[j1:j2]):
                # the user's capitals inside a caption (a name, an acronym), not a caption's first word
                if wa != wb and not fb and any(ch.isupper() for ch in wb):
                    out.append(judge_change(WordChange(wa, wb, "case")))
                    counts["case"] += 1
            continue
        if tag == "delete":
            counts["removed"] += i2 - i1
            continue
        if tag == "insert":
            counts["added"] += j2 - j1
            continue
        prev_ok = n > 0 and ops[n - 1][0] == "equal" and ops[n - 1][2] - ops[n - 1][1] >= CONTEXT
        next_ok = n + 1 < len(ops) and ops[n + 1][0] == "equal" and ops[n + 1][2] - ops[n + 1][1] >= CONTEXT
        if not (prev_ok and next_ok) or i2 - i1 > MAX_CHANGE or j2 - j1 > MAX_CHANGE:
            counts["rewritten"] += 1
            continue
        out.append(judge_change(WordChange(" ".join(w for w, _ in a[i1:i2]), " ".join(w for w, _ in b[j1:j2]), "words",
                                           " ".join(w for w, _ in a[max(0, i1 - 2):i1]),
                                           " ".join(w for w, _ in a[i2:i2 + 2]))))
        counts["words"] += 1
    return out, counts


def glossary_path() -> Path:
    from .caption_rules import ALLOWLIST_FILE
    return ALLOWLIST_FILE.with_name(GLOSSARY_NAME)


GLOSSARY_HEAD = ("# Caption glossary: the words you corrected in Premiere (python -m match_cuts learn), one per line:\n"
                 "#   heard -> written<TAB># the videos it came from\n"
                 "# The speech models are told to expect the written form, and a heard word is replaced by it only\n"
                 "# where the audio fits: both models find the written form about as likely as what they heard (at\n"
                 "# most 1 nat less). Edit or delete lines freely.\n")


def read_glossary(path: Path | None = None) -> list[tuple[str, str, list[str]]]:
    """[(heard, written, [videos])] of the glossary file."""
    p = path or glossary_path()
    out = []
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        body, _, note = line.partition("#")
        if " -> " not in body:
            continue
        heard, written = (x.strip() for x in body.split(" -> ", 1))
        if heard and written:
            vids = [v.strip() for v in note.split(",") if v.strip()] if note else []
            out.append((heard, written, vids))
    return out


def add_to_glossary(changes: Sequence[WordChange], video: str, path: Path | None = None) -> list[tuple[str, str]]:
    """The changes added to (or confirmed in) the glossary file; returns the (heard, written) pairs new to it."""
    p = path or glossary_path()
    rows = read_glossary(p)
    index = {(_n(h), w): i for i, (h, w, _v) in enumerate(rows)}
    new = []
    for c in changes:
        if not c.glossary:
            continue
        key = (_n(c.heard), c.written)
        if key in index:
            h, w, vids = rows[index[key]]
            if video not in vids:
                rows[index[key]] = (h, w, vids + [video])
            continue
        index[key] = len(rows)
        rows.append((c.heard, c.written, [video]))
        new.append((c.heard, c.written))
    text = GLOSSARY_HEAD + "".join(f"{h} -> {w}\t# {', '.join(v)}\n" for h, w, v in rows)
    p.write_text(text, encoding="utf-8", newline="\n")
    return new


# ---------------------------------------------------------------------------------------------------------------------
# cuts and framing
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Clip:
    """One clip of the RAW (a picture clip or a sound clip): sequence seconds [t0, t1), RAW seconds [r0, r1), and a
    picture clip's framing (sideways offset of the picture from the frame's centre in sequence px, scale %; None:
    keyframed, unknown, or a sound clip)."""
    t0: float
    t1: float
    r0: float
    r1: float
    dx: float | None = None
    scale: float | None = None


def source_widths(edit_xml: Path) -> dict[str, float]:
    """{clipitem id: the width of the media it plays} from 1_edit.xml's <file> entries (only a file's first clipitem
    carries the full <file>; the others name its id). Premiere reads a clip's Basic Motion <center> in units of its
    SOURCE size, not the sequence's (export_xml_edl.premiere_center)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(str(edit_xml)).getroot()
    except (OSError, ET.ParseError):
        return {}
    sizes: dict[str, float] = {}
    for f in root.iter("file"):
        w = f.findtext("media/video/samplecharacteristics/width")
        try:
            if f.get("id") and w and float(w) > 0:
                sizes[f.get("id")] = float(w)
        except ValueError:
            continue
    out: dict[str, float] = {}
    for ci in root.iter("clipitem"):
        f = ci.find("file")
        if ci.get("id") and f is not None and f.get("id") in sizes:
            out[ci.get("id")] = sizes[f.get("id")]
    return out


def tool_clips(edit_xml: Path, raw_width: float | None = None) -> tuple[list[Clip], list[Clip], float, int]:
    """The run's clips of the RAW in 1_edit.xml: (V1 picture clips with their framing -- the sideways offset in
    sequence px: <center> x the clip's source width, as Premiere shows it (source_widths; when the file does not say:
    ``raw_width``, the RAW's width from the run's cut list, and only then the sequence's) -- and A1 sound clips), the
    sequence frame rate and width."""
    from .export_xml_edl import parse_premiere_xml
    x = parse_premiere_xml(edit_xml)
    widths = source_widths(edit_xml)
    fps = float(x["timebase"]) * (1000.0 / 1001.0 if str(x.get("ntsc") or "").upper() == "TRUE" else 1.0)
    sound = [Clip(a["start"] / fps, a["end"] / fps, a["in"] / fps, a["out"] / fps) for a in x["audio"]
             if 0 <= a["start"] < a["end"] and abs(float(a.get("speed") or 1.0)) > 0]
    out = []
    for c in x["clips"]:
        if c["start"] < 0 or c["end"] <= c["start"]:
            continue
        cf = float(c.get("timebase") or fps) * (1000.0 / 1001.0 if str(c.get("ntsc") or "").upper() == "TRUE" else 1.0)
        m = c.get("motion") or {}
        keyed = bool(m.get("keys"))
        ctr = m.get("center")
        out.append(Clip(c["start"] / fps, c["end"] / fps, c["in"] / cf, c["out"] / cf,
                        None if keyed or ctr is None
                        else float(ctr[0]) * float(widths.get(c["id"]) or raw_width or x["width"]),
                        None if keyed or m.get("scale") is None else float(m["scale"])))
    return out, sound, fps, int(x["width"])


def user_clips(seq: PR.Sequence, raw_name: str) -> tuple[list[Clip], list[Clip]]:
    """The project's clips of the RAW: (the picture clips on the video track that plays it most -- none when the
    picture is something else, an After Effects comp say -- and the sound clips of the audio track that plays it
    most)."""
    sound = [Clip(p["start"], p["end"], p["src_in"], p["src_in"] + (p["end"] - p["start"]) * float(p["speed"] or 1.0))
             for p in PR.audio_pieces(seq, raw_name)] if any(
        it.kind == PR.AUDIO and PR.file_name(it.media) == raw_name for it in seq.items) else []
    mine = [it for it in seq.items if it.kind == PR.VIDEO and it.enabled and PR.file_name(it.media) == raw_name]
    if not mine:
        return [], sound
    per: dict[int, float] = {}
    for it in mine:
        per[it.track] = per.get(it.track, 0.0) + it.end - it.start
    seen = {k: v for k, v in per.items() if (PR.VIDEO, k) not in seq.hidden} or per      # a visible track first
    n = max(seen, key=seen.get)
    out = []
    for it in sorted((it for it in mine if it.track == n), key=lambda it: it.start):
        keyed = bool(it.keyframed)
        dx = None if keyed or it.position is None else (float(it.position[0]) - 0.5) * float(seq.width)
        out.append(Clip(it.start, it.end, it.src_in, it.src_in + (it.end - it.start) * float(it.speed or 1.0), dx,
                        None if keyed else it.scale))
    return out, sound


def _overlap(a: Clip, b: Clip) -> float:
    return max(0.0, min(a.r1, b.r1) - max(a.r0, b.r0))


def edit_changes(tool: Sequence[Clip], user: Sequence[Clip], tool_pic: Sequence[Clip] = (),
                 user_pic: Sequence[Clip] = ()) -> dict:
    """What the user did to the run's clips (module docstring, 3), the clips matched by the RAW they play: {starts,
    ends (RAW s: + = later), removed, added, played (the share of the user's RAW the run plays too)} from ``tool`` /
    ``user`` (the sound clips: A1 cuts where the edit cuts, in both edits), and {moved (px), zoomed (%), framing}
    from the picture clips ``tool_pic`` / ``user_pic`` when the project still has them."""
    starts, ends = [], []
    removed = 0
    for c in tool:
        mine = [u for u in user if _overlap(c, u) > 0.0]
        if not mine:
            removed += 1
            continue
        starts.append(round(min(u.r0 for u in mine) - c.r0, 3))
        ends.append(round(max(u.r1 for u in mine) - c.r1, 3))
    added = sum(1 for u in user if not any(_overlap(c, u) > 0.0 for c in tool))
    total = sum(u.r1 - u.r0 for u in user)
    played = sum(max((_overlap(c, u) for c in tool), default=0.0) for u in user) / total if total > 0 else 0.0
    moved, zoomed = [], []
    for c in tool_pic:
        mine = [u for u in user_pic if _overlap(c, u) > 0.0]
        if not mine:
            continue
        u = max(mine, key=lambda u: _overlap(c, u))
        if c.dx is not None and u.dx is not None:
            moved.append(round(u.dx - c.dx, 1))
        if c.scale is not None and u.scale is not None:
            zoomed.append(round(u.scale - c.scale, 2))
    framing = ("compared" if tool_pic and user_pic else
               "not compared: your picture is not the RAW's clips (an After Effects comp?)" if tool_pic else
               "not compared: the run's edit has no picture clips")
    return {"clips": len(tool), "starts": starts, "ends": ends, "removed": removed, "added": added, "moved": moved,
            "zoomed": zoomed, "framing": framing, "played": round(played, 3)}


# the kinds of change a suggestion may come from: (name, how to read a video's values, what to suggest)
KINDS = {
    "clips start earlier": ("starts", lambda v: v <= -TRIM_S),
    "clips start later": ("starts", lambda v: v >= TRIM_S),
    "clips end later": ("ends", lambda v: v >= TRIM_S),
    "clips end earlier": ("ends", lambda v: v <= -TRIM_S),
    "the picture moved sideways": ("moved", lambda v: abs(v) >= FRAMING_PX),
    "zoomed in": ("zoomed", lambda v: v >= ZOOM_PCT),
    "zoomed out": ("zoomed", lambda v: v <= -ZOOM_PCT),
}


def tendencies(ch: dict) -> dict[str, float]:
    """The kinds of change a video DOES (TENDENCY of its clips, at least 3) -> the median amount."""
    out = {}
    for name, (key, test) in KINDS.items():
        vals = [v for v in ch.get(key) or [] if test(v)]
        n = len(ch.get(key) or [])
        if len(vals) >= 3 and n and len(vals) >= TENDENCY * n:
            out[name] = statistics.median(vals)
    if ch.get("removed", 0) >= 3:
        out["clips removed"] = float(ch["removed"])
    return out


def suggestions(records: Sequence[dict], defaults: Any = None) -> list[str]:
    """A suggested new default for every kind of change on SEVERAL videos (records: every learned.json)."""
    from .config import Config
    cfg = defaults or Config()
    seen: dict[str, list[tuple[str, float]]] = {}
    for r in records:
        for name, amount in tendencies(r.get("edit") or {}).items():
            seen.setdefault(name, []).append((str(r.get("video") or "?"), float(amount)))
    out = []
    for name, rows in sorted(seen.items()):
        if len(rows) < SEVERAL:
            continue
        amt = statistics.median(a for _v, a in rows)
        vids = ", ".join(v for v, _a in rows)
        if name in ("clips end later", "clips end earlier"):
            new = max(0.0, float(cfg.pad_after) + amt)
            out.append(f"{name} on {len(rows)} videos ({vids}; median {amt:+.2f} s): --pad-after {new:.2f} "
                       f"instead of {cfg.pad_after:g}")
        elif name in ("clips start earlier", "clips start later"):
            new = max(0.0, float(cfg.pad_before) - amt)
            out.append(f"{name} on {len(rows)} videos ({vids}; median {amt:+.2f} s): --pad-before {new:.2f} "
                       f"instead of {cfg.pad_before:g}")
        elif name == "the picture moved sideways":
            out.append(f"{name} on {len(rows)} videos ({vids}; median {amt:+.0f} px): the framing rule may want a "
                       f"look (--min-move is {cfg.premiere_min_move:g} px)")
        else:
            out.append(f"{name} on {len(rows)} videos ({vids}; median {amt:+g}): no setting does this yet -- worth a "
                       "look")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the test case
# ---------------------------------------------------------------------------------------------------------------------

def slug(text: str) -> str:
    s = re.sub(r"[^0-9a-z]+", "-", str(text).lower()).strip("-")
    return s[:40] or "case"


def srt_of(caps: Sequence[PR.Item]) -> str:
    def ts(t: float) -> str:
        ms = int(round(max(0.0, t) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{i}\n{ts(c.start)} --> {ts(c.end)}\n{c.text.strip()}\n\n" for i, c in enumerate(caps, start=1))


def same_case(cases_dir: Path, comp_hash: str) -> Path | None:
    """A case of the test library whose competitor is this one (its file hash): updated, not doubled."""
    from .common import file_hash
    if not comp_hash or not cases_dir.is_dir():
        return None
    for d in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        c = d / "competitor.mp4"
        try:
            if c.is_file() and file_hash(c) == comp_hash:
                return d
        except OSError:
            continue
    return None


def case_name(cl: dict, comp: Path, project: Path, name: str | None) -> str:
    """The new case's folder name: given, else the competitor's original file name (the project's when that is the
    generic 'competitor')."""
    if name:
        return slug(name)
    stem = Path(str((cl.get("competitor") or {}).get("source_path") or comp)).stem
    return slug(project.stem if stem.lower() in ("competitor", "competitor_ref", "") else stem)


def _repo_path(p: Path) -> str:
    """A path for case.json: relative to the repository when inside it (the full-size original, on this machine)."""
    from .testcases import REPO
    q = Path(p).resolve()
    try:
        return q.relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return str(q)


def write_case(case_dir: Path, comp: Path, raw: Path, seq: PR.Sequence, raw_name: str, caps: Sequence[PR.Item],
               meta: dict, no_caps_why: str = "") -> dict[str, str]:
    """The test case's files (module docstring, 4); returns {file: what it is}. Without captions there is no
    answer.srt (an empty key would score every run against nothing): an old one is removed, ``no_caps_why`` says
    why."""
    from .testcases import small_copy
    case_dir.mkdir(parents=True, exist_ok=True)
    done: dict[str, str] = {}
    for src, fname in ((comp, "competitor.mp4"), (raw, "raw.mp4")):
        dst = case_dir / fname
        if dst.is_file() and dst.stat().st_size:
            done[fname] = "kept (already in the case)"
            continue
        info = small_copy(src, dst)
        done[fname] = (f"a smaller copy: {info['bytes'] / 1e6:.0f} MB ({src.stat().st_size / 1e6:.0f} MB before), "
                       f"{info.get('width')}x{info.get('height')} at {info.get('fps')} fps as before"
                       if info.get("reencoded") else f"copied ({info['bytes'] / 1e6:.0f} MB)")
        if info.get("reencoded"):                    # check-all --full-size runs on the original (testcases.py)
            meta = {**meta, f"full_{fname[:-4]}": _repo_path(src)}
    key = case_dir / "answer.srt"
    if caps:
        key.write_text(srt_of(caps), encoding="utf-8", newline="\n")
        done["answer.srt"] = f"your {len(caps)} captions (the answer key)"
    else:
        had = key.is_file()
        if had:
            key.unlink()
        done["answer.srt"] = (f"not written: {no_caps_why or 'the project has no captions'}"
                              + (" (the old one removed)" if had else ""))
    pieces = []
    for p in PR.audio_pieces(seq, raw_name):
        pieces.append({"start": p["start"], "end": p["end"], "kind": "raw", "src_in": p["src_in"], "speed": p["speed"]})
    (case_dir / "answer_edit.json").write_text(json.dumps({
        "what": "your finished edit (its Premiere project, the RAW audio under the clips): the timeline answer.srt is "
                "timed on", "fps": seq.fps, "audio": pieces}, indent=1), encoding="utf-8", newline="\n")
    done["answer_edit.json"] = f"your timeline ({len(pieces)} pieces of the RAW)"
    old = {}
    if (case_dir / "case.json").is_file():
        old = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    old.pop("timeline", None)
    (case_dir / "case.json").write_text(json.dumps({**old, "timeline": "edit", **meta}, indent=1), encoding="utf-8",
                                        newline="\n")
    done["case.json"] = "the case's notes"
    return done


def git_commands(case_dir: Path, glossary: Path | None) -> list[str]:
    """The git commands that push the case (and the glossary): run in the repository."""
    from .testcases import REPO

    def rel(p: Path) -> str:
        p = p.resolve()
        return p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else str(p)
    paths = [rel(case_dir)] + ([rel(glossary)] if glossary is not None and glossary.is_file() else [])
    return [f'cd "{REPO}"', "git add " + " ".join(f'"{p}"' for p in paths),
            f'git commit -m "Test case {case_dir.name}: learned from my finished edit"', "git push"]


# ---------------------------------------------------------------------------------------------------------------------
# finished folders (Task 8): what the user's finished video does with the tool's starting point
# ---------------------------------------------------------------------------------------------------------------------

FINAL, COMPETITOR, RAW_FILE, PROJECT, TOPAZ = "final.mp4", "competitor.mp4", "raw.mp4", "project.prproj", "topaz.mp4"
FROM_RAW = 0.5              # a video comes from the RAW: at least this share of its time shows it
SAME_STORY = 0.3            # the competitor plays at least this share of the RAW the finished video plays
ON_SCREEN = 0.6             # the project's captions are this video's: this share of them shows on its screen ...
SCREEN_S = 0.25             # ... starting within this of the project's time
SCREEN_TEXT = 0.75          # ... reading like it (letters, 0-1)


def finished_folders(path: str | Path) -> list[Path]:
    """The finished folders at ``path``: itself when it holds any of the files, else every subfolder that does."""
    path = Path(path)
    names = (FINAL, COMPETITOR, RAW_FILE, PROJECT)
    if any((path / n).is_file() for n in names):
        return [path]
    return [d for d in sorted(q for q in path.iterdir() if q.is_dir()) if any((d / n).is_file() for n in names)]


def missing_files(d: Path, final_only: bool = False) -> list[str]:
    """The files a finished folder lacks: final.mp4, competitor.mp4, raw.mp4 and project.prproj -- or, without
    final.mp4, the project alone holds your edit (project_edit): competitor.mp4, raw.mp4 and project.prproj."""
    if not final_only and not (d / FINAL).is_file() and (d / PROJECT).is_file():
        need = [COMPETITOR, RAW_FILE, PROJECT]
    else:
        need = [FINAL, COMPETITOR, RAW_FILE] + ([] if final_only else [PROJECT])
    return [n for n in need if not (d / n).is_file()]


def project_edit(project: Path, raw: dict, window: Sequence[float] | None = None) -> tuple[Any, Any, dict]:
    """Your edit as your project holds it -- a finished folder with no final.mp4: your cuts, audio cuts and framing are
    the project's own clips of the RAW. ``raw``: the RAW as a run of the tool measured it (its cut list's "raw": width,
    height, fps, duration_s). The RAW is the media the project's clips play most, its size, frame rate and length
    agreeing with ``raw`` (media_mismatch; one that has sound first, so the template's picture never wins); other
    footage -- the template overlay, graphics -- is not your edit of the RAW. Returns (the picture edit: the V1 clips
    with the part of the RAW the template ``window`` shows -- their Motion Position / Scale --, the sound edit: the A1
    clips, the timeline answer.srt is timed on, {"media", "sequence", "fps", "size", "picture_clips",
    "sound_clips", "framing"}). Rotation and anchor point are not read (0 and the centre)."""
    from fractions import Fraction
    from . import edit_score as ES
    from .export_xml_edl import premiere_settings
    pr = PR.read(project)
    seq = PR.main_sequence(pr)
    if seq is None:
        raise LearnError(f"skipped: {project.name} has no sequence")
    rw, rh = float(raw.get("width") or 0), float(raw.get("height") or 0)
    try:
        rfps = float(Fraction(str(raw.get("fps"))))
    except (ValueError, ZeroDivisionError, TypeError):
        rfps = 0.0
    wanted = {"raw": raw}
    played: dict[str, list[float]] = {}                        # media -> [seconds on audio, seconds on video]
    for it in seq.items:
        if it.media and it.enabled and it.kind in (PR.AUDIO, PR.VIDEO):
            t = played.setdefault(it.media, [0.0, 0.0])
            t[0 if it.kind == PR.AUDIO else 1] += it.end - it.start
    fits = [m for m in played if media_mismatch(pr.media.get(m) or {}, wanted) is None]
    if not fits:
        raise LearnError(f"skipped: no clip of {project.name} plays raw.mp4 (its media: "
                         + ", ".join(sorted(PR.file_name(m) for m in played)) + ")" if played else
                         f"skipped: {project.name} has no clip of a video file")
    media = max(fits, key=lambda m: (played[m][0] > 0, played[m][0] + played[m][1]))
    name = PR.file_name(media)
    fps = float(seq.fps or 0) or 60.0
    sound = [ES.Piece(p["start"], p["end"], p["src_in"], float(p["speed"] or 1.0))
             for p in PR.audio_pieces(seq, name)] if played[media][0] > 0 else []
    mine = [it for it in seq.items if it.kind == PR.VIDEO and it.enabled and it.media == media]
    per: dict[int, float] = {}
    for it in mine:
        per[it.track] = per.get(it.track, 0.0) + it.end - it.start
    st = premiere_settings(None)
    sw, sh = float(seq.width or st["size"][0]), float(seq.height or st["size"][1])
    win = [float(v) for v in (window or st["window"])]
    if window is None and (sw, sh) != tuple(float(v) for v in st["size"]):      # the template window, scaled
        kx, ky = sw / float(st["size"][0]), sh / float(st["size"][1])
        win = [win[0] * kx, win[1] * ky, win[2] * kx, win[3] * ky]
    picture, framed = [], 0
    seen = {k: v for k, v in per.items() if (PR.VIDEO, k) not in seq.hidden} or per      # a visible track first
    pick = max(seen, key=seen.get) if seen else None
    for it in sorted((it for it in mine if it.track == pick), key=lambda it: it.start):
        view = None
        if not it.keyframed and rw and rh:
            pos = it.position if it.position is not None else (0.5, 0.5)            # Premiere's defaults:
            s = (float(it.scale) if it.scale is not None else 100.0) / 100.0          # centred, 100 %
            px, py = float(pos[0]) * sw, float(pos[1]) * sh
            view = ES._view({"scale": s, "rotation_deg": 0.0, "tx": px - s * rw / 2.0, "ty": py - s * rh / 2.0},
                            win, rw, rh)
            framed += view is not None
        picture.append(ES.Piece(it.start, it.end, it.src_in, float(it.speed or 1.0), view))
    if not sound and not picture:
        raise LearnError(f"skipped: no clip of {project.name} plays {name}")
    info = {"media": media, "sequence": seq.name, "fps": fps, "size": [int(sw), int(sh)],
            "picture_clips": len(picture), "sound_clips": len(sound), "framing": framed}
    pic = ES.Edit(picture, fps, rfps or fps, "picture")
    snd = ES.Edit(sound, fps, rfps or fps, "sound") if sound else ES.Edit(list(picture), fps, rfps or fps, "picture")
    return pic, snd, info


def make_run(comp: Path, raw: Path, out: Path, work: Path, fast: bool = False, fresh: bool = False,
             log: Any = print) -> Path:
    """A run of the tool on ``comp`` + ``raw`` in ``out`` (its own process, like check-all; ``work``: its caches): the
    newest finished run there made from these two files (their hashes) is used again unless ``fresh``."""
    import os
    import subprocess
    from .common import file_hash
    from .run_folders import newest_run_dir, run_dirs
    hc, hr = file_hash(comp), file_hash(raw)
    if not fresh:
        for _n, d in sorted(run_dirs(out), reverse=True):
            cl = d / EXTRAS / "cutlist.json"
            if (d / EDIT_XML).is_file() and cl.is_file() and finished_run(d):
                ih = (json.loads(cl.read_text(encoding="utf-8")).get("provenance") or {}).get("input_hashes") or {}
                if ih.get("competitor") == hc and ih.get("raw") == hr:
                    return d
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "match_cuts", "--competitor", str(comp), "--raw", str(raw), "--out", str(out),
           "--work", str(work), "--premiere"] + (["--fast"] if fast else [])
    log(f"  a run of the tool on {comp.parent.name}/{comp.name} + {raw.name} (this takes a while) ...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(Path(__file__).resolve().parents[1]))
    (out / "learn-run.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
    d = newest_run_dir(out)
    if d is None or not (d / EDIT_XML).is_file() or not finished_run(d):
        raise LearnError(f"the run of {comp.name} + {raw.name} failed (exit {r.returncode}): see {out / 'learn-run.log'}")
    return d


def raw_share(e: Any) -> float:
    """The share of an edit's time that plays the RAW."""
    return sum(q.t1 - q.t0 for q in e.raw_pieces()) / e.duration if e.duration > 0 else 0.0


def _raw_spans(e: Any) -> list[tuple[float, float]]:
    spans = sorted((min(q.raw, q.raw_end), max(q.raw, q.raw_end)) for q in e.raw_pieces())
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def raw_overlap(a: Any, b: Any) -> float:
    """The share of the RAW seconds edit ``a`` plays that edit ``b`` plays too."""
    sa, sb = _raw_spans(a), _raw_spans(b)
    total = sum(y - x for x, y in sa)
    both = sum(max(0.0, min(y1, y2) - max(x1, x2)) for x1, y1 in sa for x2, y2 in sb)
    return both / total if total > 0 else 0.0


def hidden_captions_note(seq: PR.Sequence) -> str:
    """When a sequence shows no captions but hidden tracks hold some: which, and what to do; else ""."""
    hid = PR.hidden_text(seq)
    if not hid:
        return ""
    names = ", ".join(f"{PR.track_name(k, n)} ({c} {'captions' if k == PR.CAPTION else 'text graphics'})"
                      for k, n, c in hid)
    return (f"your captions are on {names}, hidden (its output off: the eye closed) -- not in the finished video; "
            "turn it on and save, then run learn again")


def project_no_captions(project: Path) -> str:
    """Why a project gives no captions: its captions on hidden tracks (named), or none at all."""
    seq = PR.main_sequence(PR.read(project))
    return (hidden_captions_note(seq) if seq else "") or "the project has no captions"


def project_captions(project: Path) -> list[tuple[float, float, str]]:
    """The finished captions of a project (its caption graphics): [(start, end, text)] in its sequence's seconds."""
    pr = PR.read(project)
    seq = PR.main_sequence(pr)
    return [(c.start, c.end, c.text.strip()) for c in (PR.captions_of(seq) if seq else []) if c.text.strip()]


def caption_rows(layout: dict) -> tuple[dict, bool]:
    """(the layout with its caption events chosen again, whether that changed them): the text events grouped by row
    (centres within 0.6 of their median height), the row with the MOST events taken as the captions -- captions
    change; a static overlay is one long event (video4's final: its @-handle under the picture had won the band by
    lasting 751 frames, over 19 captions in the middle of the picture)."""
    import statistics as st_
    evs = [dict(e) for e in layout.get("captions") or [] if str(e.get("type")) in ("captions", "text")]
    if not evs:
        return layout, False

    def cy(e: dict) -> float:
        return float(e["y"]) + float(e["h"]) / 2.0
    hmed = st_.median(float(e["h"]) for e in evs)
    rows: list[list[dict]] = []
    for e in sorted(evs, key=cy):
        if rows and abs(cy(e) - st_.mean(cy(x) for x in rows[-1])) <= 0.6 * hmed:
            rows[-1].append(e)
        else:
            rows.append([e])
    best = max(rows, key=lambda r: (len(r), sum(int(x["comp_out"]) - int(x["comp_in"]) for x in r)))
    ids = {id(x) for x in best}
    was = {(int(e["comp_in"]), int(e["y"])) for e in evs if str(e.get("type")) == "captions"}
    now = {(int(e["comp_in"]), int(e["y"])) for e in best}
    if was == now:
        return layout, False
    out = dict(layout)
    out["captions"] = [dict(e, type="captions" if id(e) in ids else "text") for e in evs]
    out["zones"] = [z for z in layout.get("zones") or [] if str(z.get("type")) != "captions"]
    return out, True


def screen_captions(run_dir: Path, video: Path) -> list[tuple[float, float, str]]:
    """The captions a video shows, as the run made of it read them from its screen (captions.json "screen"), or read
    again (an older run; or a band of the run's that is not the row of text that changes most: caption_rows):
    [(start, end, text)] in the video's seconds."""
    from fractions import Fraction
    cl = json.loads((run_dir / EXTRAS / "cutlist.json").read_text(encoding="utf-8"))
    fps = Fraction(str(cl["competitor"]["fps"]))
    cj = run_dir / EXTRAS / "debug" / "captions.json"
    layout, moved = caption_rows(cl.get("layout") or {})
    rows = json.loads(cj.read_text(encoding="utf-8")).get("screen") if cj.is_file() and not moved else None
    if rows is None:
        from . import caption_ocr
        c = cl["competitor"]
        got = caption_ocr.read_caption_spans(str(video), layout, (int(c["width"]), int(c["height"])), fps,
                                             int(c["frames"]))
        rows = [{"comp_in": d["comp_in"], "comp_out": d["comp_out"], "text": d.get("ocr") or ""}
                for d in got.get("spans") or []]
    return [(int(r["comp_in"]) / float(fps), int(r["comp_out"]) / float(fps), str(r["text"]).strip())
            for r in rows if str(r["text"]).strip()]


def on_screen(caps: Sequence[tuple[float, float, str]], screen: Sequence[tuple[float, float, str]]) -> float:
    """The share of captions ``caps`` a video shows: a screen caption starting within SCREEN_S that reads like it."""
    if not caps:
        return 0.0
    hit = 0
    for a, _b, text in caps:
        t = _letters(text)
        if any(abs(s0 - a) <= SCREEN_S and difflib.SequenceMatcher(None, t, _letters(st)).ratio() >= SCREEN_TEXT
               for s0, _s1, st in screen):
            hit += 1
    return hit / len(caps)


def topaz_of(topaz: Path, final: Path, raw: Path, user_cuts: Sequence[float]) -> dict:
    """What topaz.mp4 is, by its content: its size, frame rate, length and sound against final.mp4 and raw.mp4, and
    whether its picture cuts where final.mp4 does (``user_cuts``: the finished video's cut times, s)."""
    import numpy as np
    from .common import ffmpeg_bin
    from .testcases import probe

    def facts(p: Path) -> dict:
        d = probe(p)
        v = next((x for x in d.get("streams") or [] if x.get("codec_type") == "video"), {})
        from .model import parse_fps
        return {"width": v.get("width"), "height": v.get("height"),
                "fps": round(float(parse_fps(str(v.get("r_frame_rate") or "0/1"))), 3) if v.get("r_frame_rate") else None,
                "duration": round(float((d.get("format") or {}).get("duration") or 0.0), 3),
                "audio": any(x.get("codec_type") == "audio" for x in d.get("streams") or [])}
    t, f, r = facts(topaz), facts(final), facts(raw)
    out = {"topaz": t, "final": f, "raw": r}
    import subprocess
    w, h = 72, 128
    data = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(topaz), "-vf", f"scale={w}:{h},format=gray",
                           "-f", "rawvideo", "pipe:"], capture_output=True).stdout
    fr = np.frombuffer(data, np.uint8).reshape(-1, h, w).astype(np.float32) if data else np.zeros((0, h, w))
    if len(fr) > 2 and user_cuts and t.get("fps"):
        d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))            # d[i]: frame i -> i + 1
        med = float(np.median(d)) + 1e-6
        fps = float(t["fps"])
        hits = 0
        for c in user_cuts:
            k = int(round(c * fps))
            lo, hi = max(0, k - 2), min(len(d), k + 1)
            if hi > lo and float(d[lo:hi].max()) >= 4.0 * med:
                hits += 1
        out["cuts_seen"] = round(hits / len(user_cuts), 3)
    same_len = abs((t["duration"] or 0) - (f["duration"] or 0)) <= 0.2
    if same_len and out.get("cuts_seen", 0.0) >= 0.6:
        out["what"] = (f"your edited picture, not the RAW: the length of final.mp4 ({t['duration']:.2f} s against "
                       f"{f['duration']:.2f} s), and it cuts where final.mp4 cuts ({100 * out['cuts_seen']:.0f} % of "
                       f"your cuts); {t['width']}x{t['height']} at {t['fps']:g} fps"
                       f"{', no sound' if not t['audio'] else ''} -- the RAW is {r['width']}x{r['height']} at "
                       f"{r['fps']:g} fps, {r['duration']:.0f} s")
    elif abs((t["duration"] or 0) - (r["duration"] or 0)) <= max(1.0, 0.01 * (r["duration"] or 0)):
        out["what"] = (f"the RAW enhanced: {t['width']}x{t['height']} at {t['fps']:g} fps against "
                       f"{r['width']}x{r['height']} at {r['fps']:g} fps")
    else:
        out["what"] = (f"neither the RAW nor your finished video by its length ({t['duration']:.2f} s; final "
                       f"{f['duration']:.2f} s, RAW {r['duration']:.0f} s): not used")
    return out


SOUND_CORR = 0.8            # a piece's sound is measured: it correlates this well with the RAW (broll.py's
#                             verify_audio_strong_corr) -- under a cutaway, proof that the RAW's sound plays on
SOUND_SHIFT_FRAMES = 2.5    # a piece whose sound is at most this many RAW frames off its picture (past the video's
#                             own A/V offset) is the same take with its picture shifted (Topaz, After Effects): the
#                             sound says where it is; further off, the picture does


def _no_broll_record(run_dir: Path) -> dict | None:
    p = run_dir / EXTRAS / "debug" / "decisions.jsonl"
    rec = None
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if '"no_broll"' in line:
                d = json.loads(line)
                if d.get("decision") == "no_broll":
                    rec = d
    return rec


def _weighted_median(xs: Sequence[tuple[float, float]]) -> float:
    xs = sorted(xs)
    half, acc = 0.5 * sum(w for _x, w in xs), 0.0
    for x, w in xs:
        acc += w
        if acc >= half:
            return x
    return xs[-1][0]


def sound_edit(run_dir: Path, picture: Any) -> tuple[Any, dict]:
    """Your edit as you cut it, from the run made of final.mp4: its picture matched against the RAW frame by frame,
    each piece placed by its sound where the sound is measured, the way the tool reads a competitor's sound:

    - the video's own A/V offset (its render, the replaced sound file) is the pieces' weighted median sound-picture
      offset, and is taken out: the pieces are on the RAW's clock, as an edit cuts picture and sound together;
    - a piece whose sound is within SOUND_SHIFT_FRAMES of its picture is placed by its sound: a picture shifted by a
      frame or two (Topaz, After Effects) is no cut of yours;
    - a stretch whose picture shows something else -- a cutaway, an insert, a picture the match could not follow --
      plays the RAW of its sound where the run's cutaway check proved it (broll.py: the audio there correlates >=
      SOUND_CORR with the RAW within 10 ms); a stretch too short to hear, or only assumed to keep playing, keeps its
      picture.

    Returns (the edit, {"offset_ms": the video's A/V offset, "moved": pieces placed by their sound [{start, end,
    frames}], "given": cutaways given their sound [{start, end, raw, corr, showed}]}); the picture edit itself when
    nothing is measured."""
    from . import edit_score as ES
    cl = json.loads((run_dir / EXTRAS / "cutlist.json").read_text(encoding="utf-8"))
    fps, raw_fps = picture.fps, picture.raw_fps
    av = (cl.get("audio") or {}).get("av_offset") or {}
    g = float(av.get("lag_ms") or 0.0) / 1000.0 if av.get("status") == "measured" else 0.0
    own: dict[int, tuple[float, float]] = {}          # segment comp_in -> (sound - picture, its seconds)
    on_line: dict[int, tuple[float, float]] = {}      # a piece whose sound follows another line: (RAW at comp_in, speed)
    by_id: dict[int, dict] = {}
    for s in cl.get("segments") or []:
        by_id[int(s["id"])] = s
        a = s.get("audio") or {}
        if s.get("type") != "raw" or a.get("corr") is None or a.get("lag_ms") is None or float(a["corr"]) < SOUND_CORR \
                or s.get("raw_in_seconds") is None or s.get("time_remap_keys"):
            continue
        lag = g + float(a["lag_ms"]) / 1000.0
        ln = a.get("line")
        if ln and ln.get("raw_in_seconds") is not None:
            on_line[int(s["comp_in"])] = (float(ln["raw_in_seconds"]) + lag, float(ln.get("speed") or 1.0))
        else:
            own[int(s["comp_in"])] = (lag, (int(s["comp_out"]) - int(s["comp_in"])) / fps)
    if not own:
        return picture, {"offset_ms": None, "moved": [], "given": []}
    G = _weighted_median(list(own.values()))
    lim = SOUND_SHIFT_FRAMES / raw_fps
    shift = {k: lag - G for k, (lag, _d) in own.items() if abs(lag - G) <= lim}

    def placed(q: Any) -> Any:
        k = int(round(q.t0 * fps))
        if q.raw is None:
            return q
        if k in on_line:
            r, v = on_line[k]
            return ES.Piece(q.t0, q.t1, r - G, v, None)
        return ES.Piece(q.t0, q.t1, q.raw + shift.get(k, 0.0), q.speed, q.view)
    base = [placed(q) for q in picture.pieces]
    spans: list[tuple[float, float, float, float, float, str]] = []      # (t0, t1, RAW at t0, speed, corr, showed)
    rec = _no_broll_record(run_dir)
    for row in (rec or {}).get("replaced") or []:
        if row.get("bridged"):
            continue
        m = re.match(r"S(\d+) continued", str(row.get("line") or ""))
        anchor = by_id.get(int(m.group(1))) if m else None
        for q in row.get("parts") or [row]:
            corr = q.get("corr")
            if q.get("how", row.get("how")) != "audio" or corr is None or float(corr) < SOUND_CORR \
                    or q.get("raw_in_seconds") is None:
                continue
            if anchor is not None:          # the anchor's picture line: placed like the anchor
                r = float(q["raw_in_seconds"]) + shift.get(int(anchor["comp_in"]), 0.0)
                v = float(anchor.get("speed") or 1.0)
            else:                           # found by the audio: the RAW of the sound
                r, v = float(q["raw_in_seconds"]) - G, 1.0
            spans.append((int(q["comp_in"]) / fps, int(q["comp_out"]) / fps, r, v, float(corr),
                          str(row.get("showed"))))
    eps = 0.25 / fps
    edges = sorted({t for q in base for t in (q.t0, q.t1)} | {t for sp in spans for t in sp[:2]})
    pieces: list[Any] = []
    for a, b in zip(edges, edges[1:]):
        if b - a <= eps:
            continue
        mid = 0.5 * (a + b)
        sp = next((x for x in spans if x[0] - eps <= mid < x[1] + eps), None)
        if sp is not None:
            pieces.append(ES.Piece(a, b, sp[2] + (a - sp[0]) * sp[3], sp[3], None))
            continue
        q = next((x for x in base if x.t0 - eps <= mid < x.t1 - eps), None)
        if q is not None:
            pieces.append(ES.Piece(a, b, q.raw_at(a), q.speed, q.view))
    moved = [{"start": round(q.t0, 3), "end": round(q.t1, 3), "frames": round(shift[int(round(q.t0 * fps))] * raw_fps, 2)}
             for q in picture.pieces if q.raw is not None and abs(shift.get(int(round(q.t0 * fps)), 0.0)) * raw_fps >= 0.5]
    given = [{"start": round(t0, 3), "end": round(t1, 3), "raw": round(r, 3), "corr": c, "showed": w}
             for t0, t1, r, _v, c, w in sorted(spans)]
    return ES.Edit(pieces, fps, raw_fps, "sound"), {"offset_ms": round(G * 1000.0, 1), "moved": moved, "given": given}


def clips_of(e: Any) -> list[Clip]:
    return [Clip(q.t0, q.t1, min(q.raw, q.raw_end), max(q.raw, q.raw_end)) for q in e.raw_pieces()]


def framing_changes(user: Any, comp: Any) -> dict:
    """Where your picture looks into the RAW against the competitor's, piece by piece (matched by the RAW they
    play): the centre's move sideways / up-down (shares of the RAW's width / height: + = right / down) and the zoom
    (the competitor's width over yours: > 1 = you show less, zoomed in)."""
    dx, dy, zoom = [], [], []
    for u in user.raw_pieces():
        if u.view is None:
            continue
        cu = Clip(u.t0, u.t1, min(u.raw, u.raw_end), max(u.raw, u.raw_end))
        best = max(((_overlap(cu, Clip(c.t0, c.t1, min(c.raw, c.raw_end), max(c.raw, c.raw_end))), c)
                    for c in comp.raw_pieces() if c.view is not None), default=(0.0, None), key=lambda x: x[0])
        if best[1] is None or best[0] <= 0.0:
            continue
        cv = best[1].view
        dx.append(round(u.view[0] - cv[0], 4))
        dy.append(round(u.view[1] - cv[1], 4))
        if u.view[2] > 0:
            zoom.append(round(cv[2] / u.view[2], 3))
    med = (lambda v: round(statistics.median(v), 4) if v else None)
    return {"pieces": len(dx), "dx": dx, "dy": dy, "zoom": zoom, "dx_median": med(dx), "dy_median": med(dy),
            "zoom_median": med(zoom)}


def _minus(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of spans ``a`` outside spans ``b`` (longer than 0.02 s)."""
    out: list[tuple[float, float]] = []
    for x, y in a:
        cur = [(x, y)]
        for p, q in b:
            nxt = []
            for u, w in cur:
                if q <= u or p >= w:
                    nxt.append((u, w))
                    continue
                if p > u:
                    nxt.append((u, p))
                if q < w:
                    nxt.append((q, w))
            cur = nxt
        out += cur
    return [(u, w) for u, w in out if w - u > 0.02]


def trims_at_cuts(sc: Any) -> dict:
    """How you trim the cuts you and the tool both make (edit_score trims), as the clip changes ``tendencies`` reads:
    starts = where you come in (RAW s, + = later), ends = where you leave (+ = later). Your finished video is not an
    edit of the tool's clips, so its clips are not matched one by one (edit_changes): only these cuts are."""
    tr = sc.trims if hasattr(sc, "trims") else sc["trims"]
    return {"clips": len(tr), "starts": [t["into"] for t in tr if t["into"] is not None],
            "ends": [t["out"] for t in tr if t["out"] is not None], "removed": 0, "added": 0,
            "how": "at the cuts you and the tool both make (within 0.5 s): your RAW moment minus the tool's"}


def compare_edits(user: Any, comp: Any, tool: Any, user_pic: Any = None, comp_pic: Any = None) -> dict:
    """Your finished edit (as you cut it: sound_edit) against the competitor's (where you started) and the tool's:
    which cuts reproduce yours and how each of you trims the cuts you both make (edit_score), how long each edit is,
    the RAW only one of you plays, your framing against the competitor's (by the pictures)."""
    from . import edit_score as ES
    tool_sc, comp_sc = ES.score(user, tool), ES.score(user, comp)
    kept = ES.score(comp, user)                       # the competitor's cuts you kept
    only_tool, only_yours = _minus(_raw_spans(tool), _raw_spans(user)), _minus(_raw_spans(user), _raw_spans(tool))
    return {"tool": tool_sc.to_dict(), "competitor": comp_sc.to_dict(),
            "competitor_cuts": kept.cuts, "competitor_cuts_kept": kept.reproduced + kept.near,
            "length": {"yours": round(user.duration, 3), "tool": round(tool.duration, 3),
                       "competitor": round(comp.duration, 3)},
            "only_tool_s": round(sum(b - a for a, b in only_tool), 3),
            "only_tool": [[round(a, 3), round(b, 3)] for a, b in only_tool],
            "only_yours_s": round(sum(b - a for a, b in only_yours), 3),
            "only_yours": [[round(a, 3), round(b, 3)] for a, b in only_yours],
            "trims": trims_at_cuts(tool_sc), "framing": framing_changes(user_pic or user, comp_pic or comp)}


def _edit_json(e: Any, fps: float, what: str | None = None) -> dict:
    """answer_edit.json of a finished video: its pieces of the RAW (as you cut it: sound_edit), other footage as kind
    "other" (``what``: the file's description, when the edit is not final.mp4's)."""
    rows = []
    for q in e.pieces:
        if q.raw is None:
            rows.append({"start": round(q.t0, 6), "end": round(q.t1, 6), "kind": "other", "src_in": round(q.t0, 6),
                         "speed": 1.0})
        else:
            rows.append({"start": round(q.t0, 6), "end": round(q.t1, 6), "kind": "raw", "src_in": round(q.raw, 6),
                         "speed": round(q.speed, 6)})
    snd = e.what == "sound"
    return {"what": what or ("your finished video (final.mp4) matched against the RAW frame by frame, as the tool "
                             "matches a competitor" + (", each piece placed by its sound and the cutaways over the RAW's "
                                                       "sound given that sound (learn.sound_edit)" if snd else "") +
                             ": what the RAW plays where -- the timeline answer.srt is timed on, and your cuts"),
            "track": "sound" if snd else "picture", "fps": fps, "raw_fps": e.raw_fps, "audio": rows}


def write_media(case_dir: Path, comp: Path, raw: Path) -> dict[str, str]:
    """The test case's videos: the competitor and the RAW (a smaller copy over 100 MB: the same size, frame rate and
    frames); returns {file: what it is}."""
    from .testcases import small_copy
    case_dir.mkdir(parents=True, exist_ok=True)
    done: dict[str, str] = {}
    for src, fname in ((comp, "competitor.mp4"), (raw, "raw.mp4")):
        dst = case_dir / fname
        if dst.is_file() and dst.stat().st_size:
            done[fname] = "kept (already in the case)"
            continue
        info = small_copy(src, dst)
        done[fname] = (f"a smaller copy: {info['bytes'] / 1e6:.0f} MB ({src.stat().st_size / 1e6:.0f} MB before), "
                       f"{info.get('width')}x{info.get('height')} at {info.get('fps')} fps as before"
                       if info.get("reencoded") else f"copied ({info['bytes'] / 1e6:.0f} MB)")
    return done


def write_finished_case(case_dir: Path, caps: Sequence[tuple[float, float, str]] | None, user: Any,
                        meta: dict, what: str | None = None) -> dict[str, str]:
    """The answer keys and notes of a finished folder's test case (answer.srt when its captions could be read,
    answer_edit.json -- ``what``: its description, when the edit is not final.mp4's --, case.json); returns {file:
    what it is}."""
    done: dict[str, str] = {}
    srt = case_dir / "answer.srt"
    if caps:
        from types import SimpleNamespace
        srt.write_text(srt_of([SimpleNamespace(start=a, end=b, text=t) for a, b, t in caps]), encoding="utf-8",
                       newline="\n")
        done["answer.srt"] = f"your {len(caps)} captions (the caption answer key)"
    elif srt.is_file():
        srt.unlink()
        done["answer.srt"] = "removed: this video's captions could not be read"
    (case_dir / "answer_edit.json").write_text(json.dumps(_edit_json(user, user.fps, what), indent=1) + "\n",
                                               encoding="utf-8", newline="\n")
    n = len(user.cuts())
    done["answer_edit.json"] = (f"your timeline: {len(user.raw_pieces())} pieces of the RAW, "
                                f"{n} cut{'' if n == 1 else 's'} (the cut answer key)")
    (case_dir / "case.json").write_text(json.dumps({"timeline": "edit", **meta}, indent=1) + "\n", encoding="utf-8",
                                        newline="\n")
    done["case.json"] = "the case's notes"
    return done


def learn_folder(d: Path, runs: Path, cases_dir: Path | None = None, final_only: bool = False,
                 tool_run: str | Path | None = None, user_run: str | Path | None = None, fast: bool = False,
                 fresh: bool = False, glossary: str | Path | None = None, log: Any = print,
                 check_work: str | Path | None = None) -> dict:
    """One finished folder (module docstring): raises LearnError("skipped: ...") when a file is missing or the files do
    not belong together. The tool's run is made on the test case's own copies (the files check-all runs) with
    check-all's work folder ``check_work`` (default work/check-all/work), so check-all reuses its work; your edit is
    read from final.mp4 against the original raw.mp4."""
    from . import edit_score as ES
    from .captions import read_srt
    from .check_all import DEFAULT_OUT
    from .common import file_hash
    from .testcases import CASES_DIR
    d = Path(d)
    miss = missing_files(d, final_only)
    if miss:
        raise LearnError(f"skipped: no {', '.join(miss)}")
    from_project = not final_only and not (d / FINAL).is_file()     # no final.mp4: the project holds your edit
    yours = "your project's edit" if from_project else "final.mp4"
    cases = Path(cases_dir) if cases_dir else CASES_DIR
    case_dir = same_case(cases, file_hash(d / COMPETITOR))
    if case_dir is None:
        case_dir = cases / slug(d.name)
        i = 2
        while case_dir.exists():
            case_dir, i = cases / f"{slug(d.name)}-{i}", i + 1
    updated = any((case_dir / n).is_file() for n in ("answer_edit.json", "answer_edit.xml", "answer.srt"))
    user_dir = None if from_project else (
        Path(user_run) if user_run else make_run(d / FINAL, d / RAW_FILE, runs / "user" / case_dir.name,
                                                 runs / "work" / case_dir.name, fast, fresh, log))
    media = write_media(case_dir, d / COMPETITOR, d / RAW_FILE)
    cw = Path(check_work) if check_work else DEFAULT_OUT / "work"
    tool_dir = Path(tool_run) if tool_run else make_run(case_dir / "competitor.mp4", case_dir / "raw.mp4",
                                                       runs / "tool" / case_dir.name, cw / case_dir.name, fast, fresh,
                                                       log)
    for given in (tool_dir, user_dir):
        if given is not None and not finished_run(given):
            raise LearnError(unfinished(given))
    comp_cl = json.loads((tool_dir / EXTRAS / "cutlist.json").read_text(encoding="utf-8"))
    comp_pic = ES.Edit.from_cutlist(comp_cl)
    comp, comp_sound = sound_edit(tool_dir, comp_pic)
    if from_project:
        user_pic, user, proj = project_edit(d / PROJECT, comp_cl.get("raw") or {})
        user_sound = {"from": "project", **proj}
    else:
        user_cl = json.loads((user_dir / EXTRAS / "cutlist.json").read_text(encoding="utf-8"))
        user_pic = ES.Edit.from_cutlist(user_cl)
        user, user_sound = sound_edit(user_dir, user_pic)
    tool = ES.Edit.from_xml(tool_dir / EDIT_XML, "sound" if user.what == "sound" else "picture")
    belongs = {"competitor_from_raw": round(raw_share(comp_pic), 3), "final_from_raw": round(raw_share(user_pic), 3),
               "same_story": round(raw_overlap(user_pic, comp_pic), 3)}
    if belongs["final_from_raw"] < FROM_RAW:
        raise LearnError(f"skipped: {yours} does not come from raw.mp4 (only {100 * belongs['final_from_raw']:.0f} % "
                         "of it shows the RAW)")
    if belongs["competitor_from_raw"] < FROM_RAW:
        raise LearnError("skipped: competitor.mp4 does not come from raw.mp4 (only "
                         f"{100 * belongs['competitor_from_raw']:.0f} % of it shows the RAW)")
    if belongs["same_story"] < SAME_STORY:
        raise LearnError(f"skipped: {yours} and competitor.mp4 play different parts of raw.mp4 (only "
                         f"{100 * belongs['same_story']:.0f} % of the RAW your video plays is in the competitor's)")
    screen = screen_captions(user_dir, d / FINAL) if not from_project else []
    caps, source, why, shown = None, None, "", None
    if from_project:
        pcaps = project_captions(d / PROJECT)
        caps, source = pcaps, "project"
        why = (f"the project's {len(pcaps)} captions, as they are (no final.mp4: your final captions)" if pcaps else
               "not read: " + project_no_captions(d / PROJECT))
    elif final_only:
        caps, source = screen, "screen"
        why = f"read from final.mp4's screen ({len(screen)} captions): the project was not used"
    else:
        proj = project_captions(d / PROJECT)
        shown = round(on_screen(proj, screen), 3)
        if proj and shown >= ON_SCREEN:
            caps, source = proj, "project"
            why = f"the project's {len(proj)} caption graphics ({100 * shown:.0f} % of them show on final.mp4's screen)"
        else:
            why = (f"not read: the project's {len(proj)} captions are not this video's (only {100 * shown:.0f} % of them "
                   f"show on final.mp4's screen" + (f", which shows '{screen[0][2]}' first" if screen else "") + ")"
                   if proj else "not read: " + project_no_captions(d / PROJECT))
    if caps:
        caps = [(a, min(b, user.duration), t) for a, b, t in caps                     # inside the finished video:
                if b > 0 and a < user.duration]                                        # the last one ends with it
    tool_caps = [c["text"] for c in read_srt(tool_dir / CAPTIONS_SRT)] if (tool_dir / CAPTIONS_SRT).is_file() else []
    changes, ccount = caption_changes(tool_caps, [t for _a, _b, t in caps]) if caps else ([], {})
    edit = compare_edits(user, comp, tool, user_pic, comp_pic)
    topaz = (topaz_of(d / TOPAZ, d / FINAL, d / RAW_FILE, [c.t for c in user_pic.cuts()])
             if (d / TOPAZ).is_file() and (d / FINAL).is_file() else None)
    gpath = Path(glossary) if glossary else glossary_path()
    new_words = add_to_glossary(changes, case_dir.name, gpath) if changes else []
    run_paths = {"tool": str(tool_dir)} | ({} if user_dir is None else {"user": str(user_dir)})
    meta = {"notes": f"learned from {d} ({dt.date.today().isoformat()}): answer_edit.json is " + (
                "your edit as project.prproj holds it (no final.mp4): the A1 clips of the RAW, your audio cuts"
                if from_project else "your finished video (final.mp4) matched against the RAW") + "; answer.srt " + (
                "your captions read from its screen" if source == "screen" else
                "your project's captions" if source == "project" else "absent (" + why + ")"),
            "learned_from": str(d), "captions_from": source, "runs": run_paths}
    what = ("your edit as your Premiere project holds it (project.prproj, no final.mp4): the A1 clips of the RAW -- your "
            "audio cuts -- what the RAW plays where: the timeline answer.srt is timed on, and your cuts"
            if from_project else None)
    files = {**media, **write_finished_case(case_dir, caps, user, meta, what)}
    record = {"video": case_dir.name, "date": dt.date.today().isoformat(), "folder": str(d),
              "kind": "project" if from_project else "finished", "runs": run_paths, "belongs": belongs,
              "captions": {"source": source, "why": why, "count": len(caps or []), "on_screen": shown, **ccount,
                           "changes": [c.__dict__ for c in changes]},
              "edit": edit["trims"], "cuts": {k: v for k, v in edit.items() if k != "trims"}, "topaz": topaz,
              "sound": {"yours": user_sound, "competitor": comp_sound}}
    (case_dir / "learned.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    files["learned.json"] = "what you changed against the tool (the suggestions read every case's)"
    return {"folder": d, "case": case_dir, "updated": updated, "files": files, "belongs": belongs,
            "captions": {"source": source, "why": why, **ccount}, "changes": changes, "glossary": gpath,
            "glossary_new": new_words, "compare": edit, "topaz": topaz, "tendencies": tendencies(edit["trims"]),
            "runs": {"tool": tool_dir, "user": user_dir}, "sound": user_sound}


def learn_folders(path: str | Path, runs: str | Path, cases_dir: str | Path | None = None,
                  final_only: Sequence[str] | bool = (), fast: bool = False, fresh: bool = False,
                  tool_run: str | Path | None = None, user_run: str | Path | None = None,
                  glossary: str | Path | None = None, log: Any = print, check_work: str | Path | None = None) -> dict:
    """Every finished folder at ``path`` (``final_only``: True for all, or the folders' names): {done, skipped
    [(folder, why)], suggestions, git}."""
    folders = finished_folders(path)
    if not folders:
        raise LearnError(f"{path}: no finished folder ({FINAL}, {COMPETITOR}, {RAW_FILE}, {PROJECT})")
    if (tool_run or user_run) and len(folders) > 1:
        raise LearnError("--tool-run / --user-run go with one folder")
    done, skipped = [], []
    for d in folders:
        fo = final_only if isinstance(final_only, bool) else d.name in set(final_only)
        log(f"{d.name}:")
        try:
            done.append(learn_folder(d, Path(runs), cases_dir, fo, tool_run, user_run, fast, fresh, glossary, log,
                                     check_work))
        except LearnError as e:
            skipped.append((d.name, str(e)))
            log(f"  {e}")
    from .testcases import CASES_DIR
    cases = Path(cases_dir) if cases_dir else CASES_DIR
    records = []
    for q in sorted(cases.glob("*/learned.json")):
        try:
            records.append(json.loads(q.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    gl = Path(glossary) if glossary else glossary_path()
    git = []
    if done:
        git = git_commands(done[0]["case"], gl)
        if len(done) > 1:
            from .testcases import REPO
            paths = [r["case"].resolve().relative_to(REPO).as_posix() if r["case"].resolve().is_relative_to(REPO)
                     else str(r["case"]) for r in done]
            git[1] = "git add " + " ".join(f'"{x}"' for x in paths) + (
                f' "{gl.resolve().relative_to(REPO).as_posix()}"' if gl.is_file() and gl.resolve().is_relative_to(REPO)
                else "")
            git[2] = f'git commit -m "Test cases {", ".join(r["case"].name for r in done)}: learned from my finished videos"'
    return {"done": done, "skipped": skipped, "suggestions": suggestions(records), "git": git}


def folder_summary(res: dict) -> list[str]:
    """The summary learn prints for finished folders."""
    out = []
    for r in res["done"]:
        c, cmp_ = r["captions"], r["compare"]
        t, k = cmp_["tool"], cmp_["competitor"]
        ln = cmp_["length"]
        mine = r["runs"].get("user")
        out.append(f"{r['folder'].name}: learned (runs " + ", ".join(str(v) for v in r["runs"].values() if v is not None)
                   + ")" + ("" if mine is not None else " -- your edit read from project.prproj (no final.mp4)"))
        out.append(f"  Cuts: the tool reproduces {t['reproduced']}/{t['cuts']} of your cuts within 2 frames"
                   + (f" ({t['near']} more trimmed otherwise)" if t["near"] else "")
                   + f"; the competitor already had {k['reproduced'] + k['near']} of them; you kept "
                   f"{cmp_['competitor_cuts_kept']} of its {cmp_['competitor_cuts']}")
        out.append(f"  Length: yours {ln['yours']:.1f} s, the tool's {ln['tool']:.1f} s ({ln['tool'] - ln['yours']:+.1f} s), "
                   f"the competitor's {ln['competitor']:.1f} s")
        if t.get("trims"):
            om, im = t.get("out_median"), t.get("into_median")
            out.append(f"  At the {len(t['trims'])} cut(s) you and the tool both make: you leave "
                       + ("-" if om is None else f"{om:+.2f} s") + ", you come in "
                       + ("-" if im is None else f"{im:+.2f} s") + " against the tool (medians; + = later)")
        out.append(f"  RAW only the tool plays: {cmp_['only_tool_s']:.1f} s ({len(cmp_['only_tool'])} stretches); "
                   f"only you play: {cmp_['only_yours_s']:.1f} s ({len(cmp_['only_yours'])})")
        snd = r.get("sound") or {}
        if snd.get("given") or snd.get("moved"):
            given, moved = snd.get("given") or [], snd.get("moved") or []
            out.append(f"  Your sound: {len(given)} cutaway(s) over the RAW's sound "
                       f"({sum(g['end'] - g['start'] for g in given):.1f} s) given that sound; {len(moved)} piece(s) "
                       f"placed by their sound (picture off by up to {max([abs(m['frames']) for m in moved] or [0]):.1f} "
                       f"RAW frames); your video's A/V offset {snd.get('offset_ms')} ms")
        fr = cmp_["framing"]
        if fr["pieces"]:
            out.append(f"  Framing against the competitor's ({fr['pieces']} pieces): centre {100 * fr['dx_median']:+.1f} % "
                       f"of the RAW's width sideways, {100 * fr['dy_median']:+.1f} % up/down, zoom x{fr['zoom_median']:.2f}")
        out.append(f"  Captions: {c['why']}")
        if r["changes"]:
            kept = [x for x in r["changes"] if x.glossary]
            if kept:
                out.append("    to the glossary: " + "; ".join(f"'{x.heard}' -> '{x.written}'" for x in kept[:8]))
        if r["topaz"]:
            out.append(f"  topaz.mp4: {r['topaz']['what']}")
        if r["tendencies"]:
            out.append("  This video's habits: " + "; ".join(f"{k} ({v:g})" if k == "clips removed" else
                                                           f"{k} ({v:+.2f} s)" for k, v in r["tendencies"].items()))
        out.append(f"  Test case: {r['case']} ({'updated' if r['updated'] else 'new'})")
        for f, note in r["files"].items():
            out.append(f"    {f:18s} {note}")
    for name, why in res["skipped"]:
        out.append(f"{name}: {why}")
    if res["suggestions"]:
        out.append("Suggested new defaults (the same change on several videos -- nothing was changed):")
        out += [f"  {x}" for x in res["suggestions"]]
    if res["git"]:
        out.append("To push the new test cases:")
        out += [f"  {g}" for g in res["git"]]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------------------------------------------------

def learn(project: str | Path, run: str | Path | None = None, cases_dir: str | Path | None = None,
          name: str | None = None, glossary: str | Path | None = None) -> dict:
    """Everything above for one finished project; returns {case, updated, files, glossary, glossary_new, changes,
    captions, edit, tendencies, suggestions, run, git}."""
    from .common import file_hash
    from .testcases import CASES_DIR
    project = Path(project)
    if not project.is_file():
        raise LearnError(f"{project}: no such project")
    pr = PR.read(project)
    seq = PR.main_sequence(pr)
    if seq is None:
        raise LearnError(f"{project.name}: no sequence")
    rf = find_run(seq, run)
    cl = load_cutlist(rf)
    comp, raw = run_media(rf, cl, "competitor"), run_media(rf, cl, "raw")
    if comp is None or raw is None:
        raise LearnError(f"{rf['dir']}: the run's competitor or RAW cannot be found (moved or deleted?)")
    raw_name = PR.file_name(str((cl.get("raw") or {}).get("file") or raw.name))
    cands = {k: v for k, v in pr.media.items() if PR.file_name(k) == raw_name}
    here = rf["dir"].as_posix().lower()
    inside = [v for k, v in cands.items() if str(k).replace(chr(92), "/").lower().startswith(here)]
    props = (inside or list(cands.values()) or [{}])[0]
    bad = media_mismatch(props, cl)
    if bad:
        raise LearnError(f"{project.name}: the run's RAW ({rf['dir']}) is not the video your project plays ({bad}) -- "
                         "a later run in the same folder? Give the run it was made from with --run")
    tool_pic, tool_snd, _fps, _w = tool_clips(rf["edit"], float((cl.get("raw") or {}).get("width") or 0) or None)
    user_pic, user_snd = user_clips(seq, raw_name)
    if not user_pic and not user_snd:
        raise LearnError(f"{project.name}: no clip plays the run's RAW ({raw_name})")
    edit = edit_changes(tool_snd or tool_pic, user_snd or user_pic, tool_pic, user_pic)
    if edit["played"] < MIN_PLAYED:
        raise LearnError(f"{project.name}: run {rf['dir']} plays only {100 * edit['played']:.0f} % of what the "
                         "project plays -- not the run it was made from (a later run in the same folder?): give --run")
    from .captions import read_srt
    tool_caps = [c["text"] for c in read_srt(rf["captions"])] if rf["captions"].is_file() else []
    caps = PR.captions_of(seq)
    no_caps_why = "" if caps else (hidden_captions_note(seq) or "the project has no captions")
    changes, ccount = (caption_changes(tool_caps, [c.text for c in caps]) if caps
                       else ([], {"count": 0, "why": no_caps_why}))
    cases = Path(cases_dir) if cases_dir else CASES_DIR
    case_dir = same_case(cases, file_hash(comp))
    updated = case_dir is not None
    if case_dir is None:
        base = case_name(cl, comp, project, name)
        case_dir, i = cases / base, 2
        while case_dir.exists():
            case_dir, i = cases / f"{base}-{i}", i + 1
    video = case_dir.name
    gpath = Path(glossary) if glossary else glossary_path()
    new_words = add_to_glossary(changes, video, gpath) if changes else []
    meta = {"notes": f"learned from {project.name} ({dt.date.today().isoformat()}): answer.srt and answer_edit.json "
                     "are your finished edit", "learned_from": str(project), "run": str(rf["dir"])}
    files = write_case(case_dir, comp, raw, seq, raw_name, caps, meta, no_caps_why)
    record = {"video": video, "date": dt.date.today().isoformat(), "project": str(project), "run": str(rf["dir"]),
              "edit": edit, "captions": {**ccount, "changes": [c.__dict__ for c in changes]}}
    (case_dir / "learned.json").write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")
    files["learned.json"] = "what you changed (the suggestions read every case's)"
    records = []
    for p in sorted(cases.glob("*/learned.json")):
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return {"case": case_dir, "updated": updated, "files": files, "glossary": gpath, "glossary_new": new_words,
            "changes": changes, "captions": ccount, "edit": edit, "tendencies": tendencies(edit),
            "suggestions": suggestions(records), "run": rf["dir"], "git": git_commands(case_dir, gpath)}


def summary(res: dict) -> list[str]:
    """The short summary learn prints."""
    e, c = res["edit"], res["captions"]
    out = [f"Learned from your finished edit (run {res['run']}):"]
    if res["changes"]:
        kept = [x for x in res["changes"] if x.glossary]
        rest = [x for x in res["changes"] if not x.glossary]
        out.append(f"  Captions: {c['words']} word(s) you wrote differently, {c['case']} with your capitals")
        if kept:
            new = {(h, w) for h, w in res["glossary_new"]}
            out.append("    to the glossary: " + "; ".join(
                f"'{x.heard}' -> '{x.written}'" + (" (new)" if (x.heard, x.written) in new else "") for x in kept[:8])
                + (" ..." if len(kept) > 8 else ""))
        if rest:
            out.append("    kept in learned.json only: " + "; ".join(
                f"'{x.heard}' -> '{x.written}' ({x.why})" for x in rest[:6]) + (" ..." if len(rest) > 6 else ""))
    else:
        out.append(f"  Captions: not read -- {c['why']}" if c.get("why") else "  Captions: no word changed")
    if c.get("removed") or c.get("added") or c.get("rewritten"):
        out.append(f"    ({c['removed']} word(s) removed, {c['added']} added, {c['rewritten']} longer passage(s) "
                   "rewritten: kept in learned.json, not in the glossary)")
    moved = [v for v in e["starts"] if abs(v) >= TRIM_S] + [v for v in e["ends"] if abs(v) >= TRIM_S]
    out.append(f"  Cuts: {len(moved)} of {2 * len(e['starts'])} clip edges moved, {e['removed']} clip(s) removed, "
               f"{e['added']} added")
    re_f = [v for v in e["moved"] if abs(v) >= FRAMING_PX]
    re_z = [v for v in e["zoomed"] if abs(v) >= ZOOM_PCT]
    if e.get("framing", "compared") != "compared":
        out.append(f"  Framing: {e['framing']}")
    else:
        out.append(f"  Framing: {len(re_f)} clip(s) moved sideways (median {statistics.median(re_f):+.0f} px)"
                   if re_f else "  Framing: no clip moved sideways")
    if re_z:
        out.append(f"  Zoom: {len(re_z)} clip(s) zoomed (median {statistics.median(re_z):+.1f} %)")
    if res["tendencies"]:
        out.append("  This video's habits: " + "; ".join(f"{k} ({v:+g})" for k, v in res["tendencies"].items()))
    out.append(f"  Test case: {res['case']} ("
               + ("updated: the same competitor -- your new answer key replaces the old one; git diff shows what "
                  "changed" if res["updated"] else "new") + ")")
    for f, note in res["files"].items():
        out.append(f"    {f:18s} {note}")
    if res["suggestions"]:
        out.append("Suggested new defaults (the same change on several videos -- nothing was changed):")
        out += [f"  {s}" for s in res["suggestions"]]
    out.append("To push the new test case:")
    out += [f"  {g}" for g in res["git"]]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m match_cuts learn",
                                 description="Learn from your finished work: a folder of finished videos (final.mp4, "
                                             "competitor.mp4, raw.mp4, project.prproj), or a Premiere project made "
                                             "from a run -- a caption glossary, your cut and framing changes, test "
                                             "cases.")
    ap.add_argument("project", help="a finished folder, a folder of finished folders, or your finished project "
                                    "(.prproj) made from a run")
    ap.add_argument("--run", default=None, help="a project: the run folder it was made from (default: found from the "
                                                "project's media)")
    ap.add_argument("--final-only", nargs="*", default=None, metavar="FOLDER",
                    help="finished folders: your cuts and captions from final.mp4 alone (the captions read from its "
                         "screen), the project not used -- every folder, or the folders named")
    ap.add_argument("--runs", default=None, help="finished folders: where the runs of the tool go (default "
                                                 "work/learn under the repository); a finished run of the same files "
                                                 "there is used again")
    ap.add_argument("--fresh", action="store_true", help="finished folders: new runs even when there are some")
    ap.add_argument("--fast", action="store_true", help="finished folders: --fast runs (quicker, less thorough)")
    ap.add_argument("--tool-run", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--user-run", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--check-work", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--name", default=None, help="a project: the new test case's name (default: from the competitor's "
                                                 "file)")
    ap.add_argument("--cases-dir", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--glossary", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    target = Path(a.project)
    try:
        if target.is_dir():
            from .testcases import REPO
            fo = True if a.final_only is not None and not a.final_only else (a.final_only or ())
            res = learn_folders(target, Path(a.runs) if a.runs else REPO / "work" / "learn", a.cases_dir, fo, a.fast,
                                a.fresh, a.tool_run, a.user_run, a.glossary, check_work=a.check_work)
            print("\n".join(folder_summary(res)))
            return 0 if res["done"] else 2
        res = learn(a.project, a.run, a.cases_dir, a.name, a.glossary)
    except LearnError as e:
        print(f"learn: {e}", file=sys.stderr)
        return 2
    print("\n".join(summary(res)))
    return 0
