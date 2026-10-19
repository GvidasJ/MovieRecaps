"""``python -m match_cuts batch <folder>``: every video of a folder, one after another, unattended.

``<folder>`` holds one subfolder per RAW: the RAW and one or more competitors cut from it, any names and any video
extension (.mp4 / .mov / .mkv ...). Names win over length (night 11, pairing.py): a video named ``raw*`` is the RAW
(``comp*`` / ``competitor*`` videos are competitors); else the longer video when the lengths are more than 20 % apart;
else a portrait video next to a landscape one is the competitor (said in the console); else the folder is skipped with
why ("can't tell which one is the RAW ... -- name the RAW raw.mp4"). Every other video is a competitor and gets its
own edit (run folder ``<subfolder>`` -- with several competitors ``<subfolder>_<competitor name>``). A competitor
that does not come from its RAW at all is stopped by the run's quick check within seconds (pair_check.py) and listed
as WRONG PAIR. The RAW is analysed once: the stage caches are shared, and a RAW the first run
had to convert is taken from that run's media folder for the next competitors (night 6). Before anything runs every
pair gets the wrong-input check (inputs_check.py) and every problem is listed at the start (those pairs are not
run); while a video runs the console shows its progress and the whole batch's time left every 30 s. Each runs as its own ``python -m match_cuts --premiere --fast`` process (a crash or a failed
check never stops the batch; one heavy job at a time) into its own run folder named after the subfolder:
``<out>/<name>/`` (``<name>-2`` ... when that exists), with 1_edit.xml, 2_captions.srt and extras/ as in any run.

Each run folder works on its own: its RAW and competitor are placed in its ``extras/media/`` (a hard link -- no copy,
no extra space -- on the same drive, else a copy; conform.py), and 1_edit.xml points there, so you can move or delete
the input folder afterwards. The summary checks that: every media file 1_edit.xml uses lies inside the run folder.

At the end ``<out>/batch_summary.md`` (and ``.json``): per video the run folder, the run time, PASS / OK (with a
note) / FAIL (the Premiere deliverables' hard checks -- 1_edit.xml's item / gap / speech / flash / link / person ...
checks and the captions file -- and coverage; the recreation criteria c2-c5 are not part of it, as in check-all; night
7: every file there and only 1_edit.xml's checks finding something is OK with a note, not FAIL), why it failed, a
missing 2_captions.srt said in capitals, the caption lines written although doubtful, and what to check by hand (the
run's own end summary). Exit code: 0 when every video passed (OK with a note included), 1 otherwise, 2 when there was
nothing to run.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
import types
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

from . import run_folders

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".flv", ".wmv"}
SUMMARY = "batch_summary"


def find_videos(folder: Path, notes: list[str] | None = None,
                asks: list[tuple[str, list[Path], str]] | None = None) -> tuple[list[tuple[str, Path, Path]], list[str]]:
    """([(name, competitor, raw)] in name order, [why a subfolder was skipped]): every subfolder's RAW and each of its
    competitors (one job per competitor; module docstring). ``notes``: gets the decisions worth saying (a portrait
    video taken as the competitor of a RAW about as long); ``asks``: gets the subfolders where it cannot tell which
    video is the RAW, as (name, the candidates, why) -- instead of skipping them (the app asks you)."""
    out, skipped = [], []
    for d in sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        vids = sorted((f for f in d.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS),
                      key=lambda f: f.name.lower())
        p = folder_pair(vids)
        if p.raw is None and p.ask and asks is not None and len(p.candidates) >= 2:
            asks.append((d.name, list(p.candidates), p.why))
            continue
        raw, comps = p.raw, p.competitors
        if raw is None or not comps:
            skipped.append(f"{d.name}: {p.why if raw is None else 'a raw video but no competitor'}")
            continue
        if p.rule == "portrait" and notes is not None:
            notes.append(f"{d.name}: {p.why}")
        for c in comps:
            out.append((d.name if len(comps) == 1 else f"{d.name}_{c.stem}", c, raw))
    return out, skipped


_PROBES: dict[tuple, dict | None] = {}


def _probe(f: Path) -> dict | None:
    """cli.quick_probe of a video (its length and picture size), read once per file version; None: not readable."""
    try:
        st = f.stat()
        key = (str(f), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key not in _PROBES:
        try:
            from .cli import quick_probe
            _PROBES[key] = quick_probe(f)
        except Exception:  # noqa: BLE001 - not a readable video
            _PROBES[key] = None
    return _PROBES[key]


def _seconds(f: Path) -> float | None:
    d = _probe(f)
    return float(d["duration"]) if d and d.get("duration") else None


def _size(f: Path) -> tuple[int, int] | None:
    d = _probe(f)
    return (int(d["width"]), int(d["height"])) if d and d.get("width") and d.get("height") else None


def folder_pair(vids: Sequence[Path]):
    """pairing.pick_pair of one subfolder's videos (names first, then the lengths, then a portrait one next to a
    landscape one; night 11)."""
    from .pairing import pick_pair
    return pick_pair(vids, _seconds, _size)


def split_folder(vids: Sequence[Path]) -> tuple[Path | None, list[Path], str]:
    """(the RAW, the competitors, why not) of one subfolder's videos by pairing.py's rule (night 11: names win over
    length -- raw* is the RAW, comp* / competitor* are competitors; else the longer one when the lengths are more
    than 20 % apart; else a portrait one next to a landscape one is the competitor; else not decided: why not says
    so and the folder is skipped)."""
    p = folder_pair(vids)
    if p.raw is None:
        return None, [], p.why
    return p.raw, p.competitors, "" if p.competitors else "a raw video but no competitor"


def input_problems(jobs: Sequence[tuple[str, Path, Path]], allow_short_raw: Sequence[str] = ()) -> dict[str, list[str]]:
    """The wrong-input check (inputs_check) of every (competitor, RAW) pair before anything runs: {name: problems}
    (a pair whose lengths cannot be read is left to the run itself). The names in ``allow_short_raw`` run with
    ``--allow-short-raw`` (not told "almost as long as the RAW")."""
    from . import inputs_check
    from .cli import quick_probe
    out: dict[str, list[str]] = {}
    for name, comp, raw in jobs:
        try:
            pc, pr = quick_probe(comp), quick_probe(raw)
        except Exception:  # noqa: BLE001 - not readable here: the run says what is wrong
            continue
        found = inputs_check.problems(pc, pr, name in allow_short_raw)
        if found:
            out[name] = found + [f"competitor {inputs_check.describe(pc)}, RAW {inputs_check.describe(pr)}"]
    return out


def estimate_s(comp: Path, raw: Path, fast: bool) -> float:
    """A whole run's seconds for this pair (eta.py: the lengths, this machine, the earlier runs); 0 when unknown."""
    try:
        from . import eta
        from .cli import quick_probe
        return eta.estimate_run(float(quick_probe(comp)["duration"]), float(quick_probe(raw)["duration"]), fast)
    except Exception:  # noqa: BLE001
        return 0.0


PROGRESS_TAIL = 4 << 20        # bytes of the run's log searched for its newest progress line: its debug lines between
#                                two progress lines run far past 64 KB (the demo batch: "running" every time)
STALE_S = 60.0                 # an older progress line: only its 'whole run' time left, counted down since


_STAGE_LINE = re.compile(r" match_cuts == (S\S* .+?)\s*$")


def latest_progress(run_dir: Path, now: float | None = None) -> str:
    """The run's newest progress line with its time estimate (eta.RunClock: '... | S5.2 visual search: 40 % -- ...');
    else -- a stage that logs no counter, a progress line STALE_S old or from a stage before -- the stage it is in
    ('S8 export', from its '== S8 export' line) with that line's 'whole run' time left counted down by the time since;
    '' before the first stage. (The demo batch said "running" every time: short runs log no progress line.)"""
    p = run_dir / run_folders.EXTRAS / "match_cuts.log"
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - PROGRESS_TAIL))
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    stage, stage_at, prog, prog_at = "", -1, "", -1
    for k in range(len(tail) - 1, -1, -1):
        ln = tail[k]
        if stage_at < 0:
            m = _STAGE_LINE.search(ln)
            if m:
                stage, stage_at = m.group(1), k
        if prog_at < 0 and " | " in ln and "whole run:" in ln:
            prog, prog_at = ln, k
        if stage_at >= 0 and prog_at >= 0:
            break
    if prog_at < 0:
        return stage
    text = prog.split(" | ", 1)[1].strip()
    left = left_s(text)
    try:
        age = (time.time() if now is None else now) - _dt.datetime.strptime(prog[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        age = 0.0
    if left is None or (age < STALE_S and prog_at > stage_at):
        return text
    return (f"{stage} -- " if stage else "") + f"whole run: {duration_text(max(0.0, left - age))} left"


# the exit codes of a run that went to its end (cli.py): 0 every criterion passed, 1 something failed, 3 nothing
# failed but a criterion could not be checked -- every Premiere-only run (--premiere: no After Effects export) whose
# other criteria all pass (day 12: the laptop's darkhumor run passed everything, exited 3 and was shown as a stop)
FINISHED = (0, 1, 3)
RESULT_LINE = "match_cuts result:"          # cli.py's summary headline (stdout): the run reached its end
ABORT_WORDS = "the tool stopped by itself (exit code 3 without a result: an abort or a fatal Python error)"


def finished(code: int | None, stdout: str = "", run_dir: str | Path | None = None) -> bool:
    """The run went to its end (FINISHED). Exit code 3 also comes from abort() / "Fatal Python error" on Windows, so
    3 counts only when the run printed its "match_cuts result:" line or wrote extras/verify.json."""
    if code not in FINISHED:
        return False
    if code != 3:
        return True
    if RESULT_LINE in (stdout or ""):
        return True
    return run_dir is not None and (Path(run_dir) / run_folders.EXTRAS / "verify.json").is_file()

_LOG_LINE = re.compile(r"^\d\d:\d\d:\d\d(?:,\d+)? (?:DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
# a library's own console noise, never the reason a run stopped (day 12: a laptop run that crashed while closing was
# shown as "[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::...": OpenCV's warning, its last stderr line)
_NOISE_LINE = re.compile(r"^\s*(?:\[ ?(?:WARN|INFO|DEBUG)\b|\S+\.py:\d+: \w*Warning:|warnings\.warn\(|"
                         r"\S*Warning: |W\d{4} |I\d{4} )")
# Windows' exit codes of a process that crashed (no Python error to show)
CRASH_CODES = {
    -1073741819: "the tool crashed (a memory access error inside a program library, 0xC0000005)",
    3221225477: "the tool crashed (a memory access error inside a program library, 0xC0000005)",
    -1073740791: "the tool crashed (a program library stopped it, 0xC0000409)",
    3221226505: "the tool crashed (a program library stopped it, 0xC0000409)",
    -1073741571: "the tool crashed (it ran out of stack memory, 0xC00000FD)",
    3221225725: "the tool crashed (it ran out of stack memory, 0xC00000FD)",
    -1073741801: "the tool ran out of memory (0xC0000017)",
    3221225495: "the tool ran out of memory (0xC0000017)",
}


def crash_words(code: int | None) -> str:
    """A crash's exit code in plain words ('' for an ordinary exit code)."""
    return CRASH_CODES.get(code, "") if code is not None else ""


_DUMP_LINE = re.compile(r"^(\s|$|Thread 0x|Current thread 0x|Extension modules:|Windows fatal exception|"
                        r"<no Python frame>|Stack \(most recent call first\))")


def _without_fatal_dump(lines: list[str]) -> list[str]:
    """The lines without each "Fatal Python error: ..." line and the thread dump Python prints under it."""
    out: list[str] = []
    dump = False
    for ln in lines:
        if ln.startswith("Fatal Python error:"):
            dump = True
            continue
        if dump and _DUMP_LINE.match(ln):
            continue
        dump = False
        out.append(ln)
    return out


def stop_message(stderr: str, limit: int = 600, code: int | None = None) -> str:
    """What a stopped run said in plain words: its last message that is not a log line ("the competitor's scenes are
    not in this RAW: ...", "The inputs look wrong ..."), a crash's last line, else its last ERROR line -- not its
    whole console (the demo batch's summary held the run's log from its first line). A library's warning (OpenCV's
    "[ WARN:...]", Python's "...Warning:") is never the message: a crash (``code``, crash_words) says what happened
    instead."""
    lines = [ln.rstrip() for ln in (stderr or "").splitlines()]
    fatal = next((ln.strip() for ln in reversed(lines) if ln.startswith("Fatal Python error:")), "")
    if fatal:                                    # the thread dump under it is never the message (day 12 review 2:
        lines = _without_fatal_dump(lines)       # nor does it hide a real stop message -- WRONG PAIR stays WRONG PAIR)
    block: list[str] = []                      # raw lines, the message's own, in order
    for ln in reversed(lines):
        if _NOISE_LINE.match(ln):                # a warning: it and the indented source line Python prints under it
            if block and block[0].startswith((" ", "\t")):
                block.pop(0)
            if block:
                break
            continue
        if not ln.strip() or ln.startswith("Run folder") or _LOG_LINE.match(ln):
            if block or (fatal and _LOG_LINE.match(ln)):
                break                            # a crash: only a message printed right before it (no log line
            continue                             # between them) is the reason, not an older line (review 3)
        block.insert(0, ln)
    block = [b.strip() for b in block]
    crash = crash_words(code)
    if block:
        text = block[-1] if block[0].startswith("Traceback") else " ".join(block)
    elif fatal:                                  # nothing better: the crash's own line
        text = fatal
    else:
        errs = [_LOG_LINE.sub("", ln) for ln in lines if _LOG_LINE.match(ln) and " ERROR " in ln[:30]]
        text = errs[-1].strip() if errs else (crash or (ABORT_WORDS if code == 3 else "see the log"))
    if crash and text != crash and not block and not (fatal and text == fatal):
        text = f"{crash}; its last error: {text}"
    text = text.replace("match_cuts: ", "", 1)
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def left_s(progress: str) -> float | None:
    """The 'whole run: about N min left' of a progress text, in seconds."""
    m = re.search(r"whole run: (?:about (\d+) min|under (a minute|2 min))", progress or "")
    if not m:
        return None
    return 60.0 * float(m.group(1)) if m.group(1) else (45.0 if m.group(2) == "a minute" else 90.0)


def duration_text(s: float) -> str:
    s = max(0.0, float(s))
    if s < 90:
        return "about 1 min"
    m = int(round(s / 60.0))
    return f"about {m // 60} h {m % 60:02d} min" if m >= 60 else f"about {m} min"


def run_folder_for(out: Path, name: str) -> Path:
    """<out>/<name>, or <name>-2, -3 ... when that folder exists already (a run never overwrites another)."""
    safe = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "video"
    p, k = out / safe, 1
    while p.exists():
        k += 1
        p = out / f"{safe}-{k}"
    return p


def media_outside(run_dir: Path) -> list[str]:
    """The media files 1_edit.xml uses that are not inside the run folder (they break when the input moves)."""
    xml = run_dir / run_folders.EDIT_XML
    if not xml.is_file():
        return []
    root = run_dir.resolve()
    bad = []
    for el in ET.parse(xml).getroot().iter("pathurl"):
        u = urllib.parse.urlparse(el.text or "")
        p = urllib.parse.unquote(u.path)
        if re.match(r"^/[A-Za-z]:", p):
            p = p[1:]
        try:
            Path(p).resolve().relative_to(root)
        except ValueError:
            if p not in bad:
                bad.append(p)
    return bad


def hand_checks(stdout: str) -> list[str]:
    """The 'Check by hand:' block of the run's end summary (its indented lines)."""
    lines = stdout.splitlines()
    try:
        i = next(k for k, l in enumerate(lines) if l.strip() == "Check by hand:")
    except StopIteration:
        return []
    out = []
    for l in lines[i + 1:]:
        if not l.startswith(" "):
            break
        out.append(l.rstrip())
    return out


VALIDATION_FAIL = "XML/EDL re-parse validation did not pass"     # verify.check_deliverables' "exports NOT validated"


def checks_of(run_dir: Path | None) -> dict:
    """The run's hard checks (check_all.read_run): {'pass', 'failures', 'notes', 'row'}. Night 7: when every
    deliverable is there and the only problem is "exports NOT validated" (a hard check of 1_edit.xml -- a flash frame,
    say), the video is OK with a note (``notes``: what the check found, to look at in Premiere), not FAIL."""
    from .check_all import read_run
    row = read_run(types.SimpleNamespace(has_key=False, has_cut_key=False, name=""), run_dir)
    ok = lambda k: str((row.get(k) or {}).get("status")) in ("pass", "pass_with_exceptions")   # noqa: E731
    fails, notes = [], []
    if "error" in row:
        fails.append(row["error"])
    for k in ("deliverables", "coverage"):
        if not ok(k):
            c = row.get(k) or {}
            fl = [str(f) for f in (c.get("failures") or [])]
            if k == "deliverables" and fl and all(f.startswith(VALIDATION_FAIL) for f in fl) and \
                    "MISSING" not in str(c.get("summary", "")) and not any("XML EMPTY" in f for f in fl):
                # (night 8: an edit with no clip is a FAIL, never "OK with a note")
                notes.append("every file is there, but a check of 1_edit.xml found something to look at in "
                             "Premiere: " + "; ".join(f[len(VALIDATION_FAIL):].strip(" :") for f in fl)[:900])
                continue
            fails.append(f"{k}: {c.get('status', 'not run')} -- {c.get('summary', '')}".strip(" -"))
            fails += [f"  {f}" for f in fl[:8]]
    if row.get("not_checked"):
        fails.append("1_edit.xml hard checks not run: " + "; ".join(row["not_checked"][:5]))
    if (row.get("inputs") or {}).get("status") == "fail":
        fails.append("an input changed during the run")
    return {"pass": not fails, "failures": fails, "notes": notes, "row": row}


WRONG_PAIR = "WRONG PAIR"      # night 11: the run's quick check found the competitor does not come from this RAW


def is_wrong_pair(stop: str) -> bool:
    """A stopped run's message (stop_message) is the quick check's WRONG PAIR stop (pipeline.WrongPair)."""
    return str(stop or "").lstrip().upper().startswith(WRONG_PAIR)


def verdict(r: dict) -> str:
    """PASS, OK (with a note: night 7), FAIL -- or WRONG PAIR (night 11)."""
    if r.get("wrong_pair"):
        return WRONG_PAIR
    return ("OK (with a note)" if r.get("notes") else "PASS") if r["pass"] else "FAIL"


SPEED_FILE = "speed.txt"


def video_speed(folder: Path, extra: Sequence[str] = ()) -> tuple[float, str]:
    """(the speed of a video's run in percent, where it came from): ``speed.txt`` in its folder (one number: "125"),
    else --speed after --, else 100. A speed.txt that cannot be read is said and the default used."""
    p = Path(folder) / SPEED_FILE
    if p.is_file():
        m = re.search(r"\d+(?:[.,]\d+)?", p.read_text(encoding="utf-8", errors="replace"))
        v = float(m.group(0).replace(",", ".")) if m else None
        if v is not None and 10.0 <= v <= 1000.0:
            return v, f"; from {SPEED_FILE}"
        print(f"match_cuts batch: {p}: not a speed between 10 and 1000 -- 100 % used", file=sys.stderr)
    ex = list(extra)
    for i, x in enumerate(ex):
        if x == "--speed" and i + 1 < len(ex):
            try:
                return float(str(ex[i + 1]).rstrip("%")), "; from --speed"
            except ValueError:
                break
        if x.startswith("--speed="):
            try:
                return float(x.split("=", 1)[1].rstrip("%")), "; from --speed"
            except ValueError:
                break
    return 100.0, ""


def without_speed(extra: Sequence[str]) -> list[str]:
    """The options after -- without a --speed (each video's own speed is added)."""
    out, skip = [], False
    for x in extra:
        if skip:
            skip = False
            continue
        if x == "--speed":
            skip = True
            continue
        if x.startswith("--speed="):
            continue
        out.append(x)
    return out


def _fmt_s(s: float) -> str:
    return f"{int(s // 60)}m{int(s % 60):02d}s" if s >= 60 else f"{s:.0f}s"


def summary_md(rows: Sequence[dict], folder: Path, skipped: Sequence[str], started: str, total_s: float) -> str:
    """batch_summary.md, in plain words: where each video's files are, what passed, what failed and why, what did not
    run; then each video's details (its checks to do by hand)."""
    n_ok = sum(1 for r in rows if r["pass"])
    n_note = sum(1 for r in rows if r["pass"] and r.get("notes"))
    n_wrong = sum(1 for r in rows if r.get("wrong_pair"))
    out = [f"# Batch: {folder}", "",
           f"Started {started}, {len(rows)} video(s) in {_fmt_s(total_s)}: **{n_ok} passed"
           + (f" ({n_note} with a note)" if n_note else "") + f", {len(rows) - n_ok} failed**"
           + (f" ({n_wrong} {WRONG_PAIR})" if n_wrong else "")
           + (f", {len(skipped)} not run" if skipped else "") + ".", "",
           "## Where your edits are", ""]
    for k, r in enumerate(rows, 1):
        caps = "" if r.get("captions", True) else (f" **NO CAPTIONS: `{run_folders.CAPTIONS_SRT}` was not written** "
                                                     "-- see below.")
        if r["pass"]:
            out.append(f"{k}. **{r['name']}** -- done. Your files: `{r['run_dir']}` -- import "
                       f"`{run_folders.EDIT_XML}` and `{run_folders.CAPTIONS_SRT}` into Premiere."
                       + (f" Note: {r['notes'][0]}" if r.get("notes") else "") + caps)
        elif r.get("wrong_pair"):
            why = str(r.get("stop") or (r["failures"] or ["see the log"])[0]).strip()
            why = why[len(WRONG_PAIR):].lstrip(" :-") if why.upper().startswith(WRONG_PAIR) else why
            out.append(f"{k}. **{r['name']}** -- **{WRONG_PAIR}**: {why} (log: `{r['log']}`).")
        else:
            why = (r["failures"] or ["see the log"])[0].strip()
            out.append(f"{k}. **{r['name']}** -- FAILED: {why}. Its folder: `{r['run_dir']}` (log: `{r['log']}`)."
                       + caps)
    if skipped:
        out += ["", "## Not run", ""] + [f"- {s_}" for s_ in skipped]
    out += ["", "## In short", "",
            "| video | speed | run folder | run time | result | captions | 1_edit.xml media |",
            "|---|---|---|---|---|---|---|"]
    for r in rows:
        media = "inside the run folder" if not r["media_outside"] else f"OUTSIDE: {len(r['media_outside'])} file(s)"
        dl = r.get("doubtful") or []
        capc = ("-- (not run)" if r.get("wrong_pair") else "**MISSING**" if not r.get("captions", True) else
                f"{len(dl)} doubtful line(s)" if dl else "written")
        out.append(f"| {r['name']} | {float(r.get('speed') or 100):g} % | `{r['run_dir']}` | {_fmt_s(r['seconds'])} | "
                   f"{verdict(r)} (exit {r['exit']}) | {capc} | {media} |")
    for r in rows:
        out += ["", f"## {r['name']}: {verdict(r)}", "",
                f"- competitor: `{r.get('competitor', '')}`; RAW: `{r.get('raw', '')}`",
                f"- run folder: `{r['run_dir']}` (import `{run_folders.EDIT_XML}` and `{run_folders.CAPTIONS_SRT}`)",
                f"- run time: {_fmt_s(r['seconds'])}; log: `{r['log']}`"]
        if not r.get("captions", True) and not r.get("wrong_pair"):
            out.append(f"- **NO CAPTIONS**: `{run_folders.CAPTIONS_SRT}` was not written -- "
                       + (r.get("captions_why") or "see the log"))
        if r.get("notes"):
            out.append("- note:")
            out += [f"  - {n_}" for n_ in r["notes"]]
        if r.get("doubtful"):
            out.append(f"- doubtful caption lines (written as they are -- check them): {len(r['doubtful'])}")
            out += [f"  - {d}" for d in r["doubtful"]]
        if r["failures"]:
            out.append("- why it failed:")
            out += [f"  - {f.strip()}" for f in r["failures"]]
        if r["media_outside"]:
            out.append("- 1_edit.xml uses media outside the run folder (keep it where it is): "
                       + ", ".join(f"`{m}`" for m in r["media_outside"]))
        if r["hand"]:
            out += ["- check by hand:", "", "```"] + r["hand"] + ["```"]
    return "\n".join(out) + "\n"


def converted_raw(run_dir: Path) -> Path | None:
    """The RAW a finished run converted (extras/media/raw_ae.*: conform.py) -- the next competitor of the same RAW
    starts from it, so the RAW is converted and analysed once (its caches are keyed by its content)."""
    media = run_dir / run_folders.EXTRAS / "media"
    for ext in (".mp4", ".mov"):
        p = media / f"raw_ae{ext}"
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m match_cuts batch",
                                 description="Run every video of a folder (one subfolder per RAW: the RAW and one or "
                                             "more competitors, any names) with --premiere --fast, one after "
                                             "another, each competitor into its own run folder; then "
                                             "batch_summary.md.")
    ap.add_argument("folder", help="the folder of video subfolders")
    ap.add_argument("--out", default="./output", help="where the run folders go (default ./output)")
    ap.add_argument("--work", default="./work", help="the stage caches (default ./work)")
    ap.add_argument("--thorough", action="store_true", help="thorough runs instead of --fast (much slower)")
    ap.add_argument("--progress-s", type=float, default=30.0, help=argparse.SUPPRESS)
    ap.add_argument("--python", default=sys.executable, help=argparse.SUPPRESS)
    ap.epilog = ("More options for every run go after --, e.g.: batch videos -- --frame input/frame.png --remove-silence "
                 "(every run uses templates/default.png unless --frame / --no-frame). A speed.txt in a video's folder "
                 "(\"125\") sets that video's --speed (default 100)")
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    a = ap.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    folder, out = Path(a.folder), Path(a.out)
    if not folder.is_dir():
        print(f"match_cuts batch: not a folder: {folder}", file=sys.stderr)
        return 2
    decided: list[str] = []
    videos, skipped = find_videos(folder, decided)
    for s_ in skipped:
        print(f"match_cuts batch: skipped {s_}", file=sys.stderr)
    for d_ in decided:                                   # night 11: a portrait one taken as the competitor -- said
        print(f"match_cuts batch: {d_}", file=sys.stderr)
    if not videos:
        print(f"match_cuts batch: no subfolder of {folder} has a RAW and a competitor video", file=sys.stderr)
        return 2
    fast = not a.thorough
    force = "--force-inputs" in extra
    wrong = {} if force else input_problems(videos, [n for n, _, _ in videos] if "--allow-short-raw" in extra else ())
    if wrong:
        print("match_cuts batch: THESE VIDEOS WILL NOT RUN -- the inputs look wrong (fix them and start the batch "
              "again; the others run now):", file=sys.stderr, flush=True)
        for name, probs in wrong.items():
            print(f"  {name}:", file=sys.stderr)
            for p_ in probs:
                print(f"    - {p_}", file=sys.stderr)
        skipped = list(skipped) + [f"{n}: the inputs look wrong -- {'; '.join(p[:-1] or p)}" for n, p in wrong.items()]
    jobs = [v for v in videos if v[0] not in wrong]
    if not jobs:
        print("match_cuts batch: nothing left to run", file=sys.stderr)
        return 2
    est = {name: estimate_s(comp, raw, fast) for name, comp, raw in jobs}
    print(f"match_cuts batch: {len(jobs)} video(s) to run" + (f", {duration_text(sum(est.values()))} in all"
                                                                if sum(est.values()) > 0 else "") + ":", flush=True)
    for k, (name, comp, raw) in enumerate(jobs, 1):
        print(f"  {k}. {name}: {comp.name} from {raw.name}" + (f" ({duration_text(est[name])})" if est[name] else ""),
              flush=True)
    out.mkdir(parents=True, exist_ok=True)
    started, t_all = _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), time.time()
    rows: list[dict] = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    converted: dict[Path, Path] = {}             # a RAW -> the copy its first run converted (the next ones start there)
    took, guessed = 0.0, 0.0                     # the finished videos' real and estimated seconds (to correct estimates)
    for k, (name, comp, raw) in enumerate(jobs, 1):
        run_dir = run_folder_for(out, name)
        speed, why = video_speed(comp.parent, extra)
        use_raw = converted.get(raw.resolve(), raw)
        left = sum(est[n] for n, _, _ in jobs[k - 1:]) * (took / guessed if guessed > 0 and took > 0 else 1.0)
        print(f"[video {k} of {len(jobs)}] {name} -> {run_dir} (speed {speed:g} %{why})"
              + (f"; the RAW as the first run converted it ({use_raw.name})" if use_raw != raw else "")
              + (f" -- whole batch: {duration_text(left)} left" if left > 0 else ""), flush=True)
        cmd = [a.python, "-m", "match_cuts", "--competitor", str(comp), "--raw", str(use_raw), "--run-dir",
               str(run_dir), "--work", str(a.work), "--premiere", *([] if a.thorough else ["--fast"]),
               *without_speed(extra), "--speed", f"{speed:g}"]
        t = time.time()
        later = sum(est[n] for n, _, _ in jobs[k:])
        scale = took / guessed if guessed > 0 and took > 0 else 1.0
        tmp_out = out / f".{name}.batch.stdout"
        try:
            with tmp_out.open("w", encoding="utf-8", errors="replace") as fo:
                p = subprocess.Popen(cmd, stdout=fo, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     errors="replace", env=env)
                err_chunks: list[str] = []
                import threading
                th = threading.Thread(target=lambda: err_chunks.append(p.stderr.read() if p.stderr else ""),
                                      daemon=True)
                th.start()
                while True:
                    try:
                        p.wait(timeout=max(1.0, float(a.progress_s)))
                        break
                    except subprocess.TimeoutExpired:
                        prog = latest_progress(run_dir)
                        mine = left_s(prog)
                        if mine is None:
                            mine = max(0.0, est[name] * scale - (time.time() - t))
                        rest = mine + later * scale
                        print(f"  video {k} of {len(jobs)} ({name}): " + (prog or "running") +
                              f" -- whole batch: {duration_text(rest)} left", flush=True)
                th.join(5.0)
            code, stderr = p.returncode, "".join(err_chunks)
            stdout = tmp_out.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - the next video still runs
            code, stdout, stderr = -1, "", f"{type(e).__name__}: {e}"
        try:
            tmp_out.unlink()
        except OSError:
            pass
        secs = time.time() - t
        done = finished(code, stdout, run_dir)
        if est[name] > 0 and done:
            took, guessed = took + secs, guessed + est[name]
        if done and raw.resolve() not in converted:
            cr = converted_raw(run_dir)
            if cr is not None:
                converted[raw.resolve()] = cr
        log_dir = run_dir / run_folders.EXTRAS if (run_dir / run_folders.EXTRAS).is_dir() else out
        log = log_dir / (f"{name}.batch.log" if log_dir == out else "batch_console.log")
        log.write_text(" ".join(cmd) + "\n\n" + stdout + "\n" + stderr, encoding="utf-8")
        chk = checks_of(run_dir if (run_dir / run_folders.EXTRAS).is_dir() else None)
        failures = list(chk["failures"])
        stop = stop_message(stderr, code=code) if not done else ""
        if not done:                                      # 1: a recreation criterion failed (real videos: always)
            failures = [f"the run stopped (exit {code}): {stop}"] + \
                [f for f in failures if "no verify.json" not in f]
        outside = media_outside(run_dir)
        cap = captions_of(run_dir)
        row = {"name": name, "competitor": str(comp), "raw": str(raw), "run_dir": str(run_dir), "exit": code,
               "speed": speed, "speed_from": why.strip("; ") or "default", "stop": stop,
               "wrong_pair": code == 2 and is_wrong_pair(stop),
               "seconds": round(secs, 1), "pass": not failures and done, "failures": failures,
               "notes": list(chk.get("notes") or []) if not failures and done else [],
               "captions": cap["written"], "captions_why": cap["why"], "doubtful": cap["doubtful"],
               "media_outside": outside, "hand": hand_checks(stdout), "log": str(log)}
        rows.append(row)
        print(f"    {verdict(row)} in {_fmt_s(secs)}" + (f": {failures[0]}" if failures else
                                                       f": {row['notes'][0][:300]}" if row["notes"] else ""),
              flush=True)
        if not cap["written"] and not row["wrong_pair"]:
            print(f"    NO CAPTIONS: {run_folders.CAPTIONS_SRT} was not written -- {cap['why']}", flush=True)
        elif cap["doubtful"]:
            print(f"    captions: {len(cap['doubtful'])} doubtful line(s), written as they are -- check them:", flush=True)
            for d in cap["doubtful"][:20]:
                print(f"      {d}", flush=True)
        md = summary_md(rows, folder, skipped, started, time.time() - t_all)     # written after every video
        (out / f"{SUMMARY}.md").write_text(md, encoding="utf-8")
        (out / f"{SUMMARY}.json").write_text(json.dumps({"folder": str(folder), "started": started, "rows": rows,
                                                         "skipped": list(skipped)}, indent=1, ensure_ascii=False),
                                             encoding="utf-8")
    n_ok = sum(1 for r in rows if r["pass"])
    print(f"match_cuts batch: {n_ok} of {len(rows)} passed in {_fmt_s(time.time() - t_all)}"
          + (f"; {len(wrong)} not run (wrong inputs)" if wrong else "") + f"; summary: {out / (SUMMARY + '.md')}")
    for r in rows:
        print(f"  {r['name']}: {verdict(r)} -- {r['run_dir']}"
              + ("" if r.get("captions", True) or r.get("wrong_pair") else
                 f" -- NO CAPTIONS ({run_folders.CAPTIONS_SRT} missing)"))
    return 0 if n_ok == len(rows) and not wrong else 1


def captions_of(run_dir: Path) -> dict:
    """{'written': 2_captions.srt is in the run folder, 'why': why not (the run's captions report), 'doubtful': the
    lines written although they break a caption rule (night 7)}."""
    srt = Path(run_dir) / run_folders.CAPTIONS_SRT
    out = {"written": srt.is_file(), "why": "", "doubtful": []}
    cj = Path(run_dir) / run_folders.EXTRAS / "debug" / "captions.json"
    try:
        d = json.loads(cj.read_text(encoding="utf-8")) if cj.is_file() else {}
    except (OSError, ValueError):
        d = {}
    out["doubtful"] = [f"{x.get('line')}: rule {x.get('rule')} ({x.get('name')})" for x in d.get("doubtful") or []]
    if not out["written"]:
        out["why"] = str(d.get("error") or next((w for w in d.get("warnings") or [] if "not written" in str(w)), "")
                         or ("the run stopped before its captions" if not d else "the captions stage wrote no file"))
    return out
