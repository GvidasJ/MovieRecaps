"""learn.py: ``python -m match_cuts learn "<finished project>.prproj"`` -- what the user corrected in Premiere, kept
for next time (Task 6).

The project is a run's edit (1_edit.xml and 2_captions.srt imported into Premiere) as the user finished it. learn:

1. finds the run: the RAW the project's clips play lies in the run's media folder (``<run>/extras/media/``; the older
   flat layout ``<out>/media/`` too) -- 1_edit.xml points there by its absolute path -- or ``--run``. The run must
   play most of what the project's clips play (else it is not the run the project was made from);
2. captions: the user's caption track against 2_captions.srt, word by word -- aligned on the words, not the times (a
   moved cut shifts every caption after it) -- and only between anchors (CONTEXT words the same on both sides): a
   word the user wrote differently goes into the glossary (``caption_glossary.txt`` next to
   caption_allowlist.txt, ``heard -> written``). Next time the written form goes to the speech models as a hot word,
   and a heard word with a glossary entry is replaced by it only where the audio fits (caption_recheck
   .glossary_readings: every speech model finds the written form at least as likely);
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
        return rf
    for d in media_dirs(seq):
        rf = run_files(d)
        if rf is not None:
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


def judge_change(c: WordChange) -> WordChange:
    """Whether a change is a glossary correction: a changed word spelled like the heard one (SIMILAR), or capitals
    a name or an acronym takes -- not a plain word in capitals for emphasis ("like" -> "LIKE") or at a caption's
    start (the style)."""
    from .caption_rules import lexicon
    if c.kind == "words":
        r = difflib.SequenceMatcher(None, _letters(c.heard), _letters(c.written)).ratio()
        if r < SIMILAR:
            c.glossary, c.why = False, f"other words, not a spelling: similarity {r:.2f}"
        return c
    lex = lexicon()
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


def tool_clips(edit_xml: Path) -> tuple[list[Clip], list[Clip], float, int]:
    """The run's clips of the RAW in 1_edit.xml: (V1 picture clips with their framing, A1 sound clips), the sequence
    frame rate and width."""
    from .export_xml_edl import parse_premiere_xml
    x = parse_premiere_xml(edit_xml)
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
                        None if keyed or ctr is None else float(ctr[0]) * float(x["width"]),
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
    n = max(per, key=per.get)
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


def write_case(case_dir: Path, comp: Path, raw: Path, seq: PR.Sequence, raw_name: str, caps: Sequence[PR.Item],
               meta: dict) -> dict[str, str]:
    """The test case's files (module docstring, 4); returns {file: what it is}."""
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
    (case_dir / "answer.srt").write_text(srt_of(caps), encoding="utf-8", newline="\n")
    done["answer.srt"] = f"your {len(caps)} captions (the answer key)"
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
    tool_pic, tool_snd, _fps, _w = tool_clips(rf["edit"])
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
    changes, ccount = caption_changes(tool_caps, [c.text for c in caps])
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
    files = write_case(case_dir, comp, raw, seq, raw_name, caps, meta)
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
        out.append("  Captions: no word changed")
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
                                 description="Learn from your finished Premiere project: a caption glossary, your cut "
                                             "and framing changes, a new test case.")
    ap.add_argument("project", help="your finished project (.prproj)")
    ap.add_argument("--run", default=None, help="the run folder the project was made from (default: found from the "
                                                "project's media)")
    ap.add_argument("--name", default=None, help="the new test case's name (default: from the competitor's file)")
    ap.add_argument("--cases-dir", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--glossary", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        res = learn(a.project, a.run, a.cases_dir, a.name, a.glossary)
    except LearnError as e:
        print(f"learn: {e}", file=sys.stderr)
        return 2
    print("\n".join(summary(res)))
    return 0
