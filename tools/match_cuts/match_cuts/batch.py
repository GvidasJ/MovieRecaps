"""``python -m match_cuts batch <folder>``: every video of a folder, one after another, unattended.

``<folder>`` holds one subfolder per video, each with a competitor and a RAW (``competitor.mp4`` and ``raw.mp4``;
any video extension). Each runs as its own ``python -m match_cuts --premiere --fast`` process (a crash or a failed
check never stops the batch; one heavy job at a time) into its own run folder named after the subfolder:
``<out>/<name>/`` (``<name>-2`` ... when that exists), with 1_edit.xml, 2_captions.srt and extras/ as in any run.

Each run folder works on its own: its RAW and competitor are placed in its ``extras/media/`` (a hard link -- no copy,
no extra space -- on the same drive, else a copy; conform.py), and 1_edit.xml points there, so you can move or delete
the input folder afterwards. The summary checks that: every media file 1_edit.xml uses lies inside the run folder.

At the end ``<out>/batch_summary.md`` (and ``.json``): per video the run folder, the run time, PASS / FAIL (the
Premiere deliverables' hard checks -- 1_edit.xml's item / gap / speech / flash / link / person ... checks and the
captions file -- and coverage; the recreation criteria c2-c5 are not part of it, as in check-all), why it failed,
and what to check by hand (the run's own end summary). Exit code: 0 when every video passed, 1 otherwise, 2 when
there was nothing to run.
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


def find_videos(folder: Path) -> tuple[list[tuple[str, Path, Path]], list[str]]:
    """([(name, competitor, raw)] in name order, [why a subfolder was skipped])."""
    out, skipped = [], []
    for d in sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        files = {f.stem.lower(): f for f in d.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS}
        comp, raw = files.get("competitor"), files.get("raw")
        if comp is None or raw is None:
            skipped.append(f"{d.name}: no " + " and no ".join(n + ".mp4" for n, f in (("competitor", comp),
                                                                                       ("raw", raw)) if f is None))
            continue
        out.append((d.name, comp, raw))
    return out, skipped


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


def checks_of(run_dir: Path | None) -> dict:
    """The run's hard checks (check_all.read_run): {'pass', 'failures', 'row'}."""
    from .check_all import read_run
    row = read_run(types.SimpleNamespace(has_key=False, has_cut_key=False, name=""), run_dir)
    ok = lambda k: str((row.get(k) or {}).get("status")) in ("pass", "pass_with_exceptions")   # noqa: E731
    fails = []
    if "error" in row:
        fails.append(row["error"])
    for k in ("deliverables", "coverage"):
        if not ok(k):
            c = row.get(k) or {}
            fails.append(f"{k}: {c.get('status', 'not run')} -- {c.get('summary', '')}".strip(" -"))
            fails += [f"  {f}" for f in (c.get("failures") or [])[:8]]
    if row.get("not_checked"):
        fails.append("1_edit.xml hard checks not run: " + "; ".join(row["not_checked"][:5]))
    if (row.get("inputs") or {}).get("status") == "fail":
        fails.append("an input changed during the run")
    return {"pass": not fails, "failures": fails, "row": row}


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
    n_ok = sum(1 for r in rows if r["pass"])
    out = [f"# Batch: {folder}", "",
           f"Started {started}, {len(rows)} video(s) in {_fmt_s(total_s)}: **{n_ok} passed, {len(rows) - n_ok} "
           f"failed**.", "",
           "| video | speed | run folder | run time | result | 1_edit.xml media |", "|---|---|---|---|---|---|"]
    for r in rows:
        media = "inside the run folder" if not r["media_outside"] else f"OUTSIDE: {len(r['media_outside'])} file(s)"
        out.append(f"| {r['name']} | {float(r.get('speed') or 100):g} % | `{r['run_dir']}` | {_fmt_s(r['seconds'])} | "
                   f"{'PASS' if r['pass'] else 'FAIL'} (exit {r['exit']}) | {media} |")
    if skipped:
        out += ["", "Skipped:"] + [f"- {s}" for s in skipped]
    for r in rows:
        out += ["", f"## {r['name']}: {'PASS' if r['pass'] else 'FAIL'}", "",
                f"- run folder: `{r['run_dir']}` (import `{run_folders.EDIT_XML}` and `{run_folders.CAPTIONS_SRT}`)",
                f"- run time: {_fmt_s(r['seconds'])}; log: `{r['log']}`"]
        if r["failures"]:
            out.append("- why it failed:")
            out += [f"  - {f.strip()}" for f in r["failures"]]
        if r["media_outside"]:
            out.append("- 1_edit.xml uses media outside the run folder (keep it where it is): "
                       + ", ".join(f"`{m}`" for m in r["media_outside"]))
        if r["hand"]:
            out += ["- check by hand:", "", "```"] + r["hand"] + ["```"]
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m match_cuts batch",
                                 description="Run every video of a folder (one subfolder per video with competitor.mp4 "
                                             "and raw.mp4) with --premiere --fast, one after another, each into its "
                                             "own run folder named after the subfolder; then batch_summary.md.")
    ap.add_argument("folder", help="the folder of video subfolders")
    ap.add_argument("--out", default="./output", help="where the run folders go (default ./output)")
    ap.add_argument("--work", default="./work", help="the stage caches (default ./work)")
    ap.add_argument("--thorough", action="store_true", help="thorough runs instead of --fast (much slower)")
    ap.add_argument("--python", default=sys.executable, help=argparse.SUPPRESS)
    ap.epilog = ("More options for every run go after --, e.g.: batch videos -- --frame input/frame.png --keep-silence. "
                 "A speed.txt in a video's folder (\"125\") sets that video's --speed (default 100)")
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    a = ap.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    folder, out = Path(a.folder), Path(a.out)
    if not folder.is_dir():
        print(f"match_cuts batch: not a folder: {folder}", file=sys.stderr)
        return 2
    videos, skipped = find_videos(folder)
    for s in skipped:
        print(f"match_cuts batch: skipped {s}", file=sys.stderr)
    if not videos:
        print(f"match_cuts batch: no subfolder of {folder} has a competitor and a raw video", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    started, t_all = _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), time.time()
    rows: list[dict] = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    for k, (name, comp, raw) in enumerate(videos, 1):
        run_dir = run_folder_for(out, name)
        speed, why = video_speed(comp.parent, extra)
        print(f"[{k}/{len(videos)}] {name} -> {run_dir} (speed {speed:g} %{why})", flush=True)
        cmd = [a.python, "-m", "match_cuts", "--competitor", str(comp), "--raw", str(raw), "--run-dir", str(run_dir),
               "--work", str(a.work), "--premiere", *([] if a.thorough else ["--fast"]), *without_speed(extra),
               "--speed", f"{speed:g}"]
        t = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            code, stdout, stderr = p.returncode, p.stdout, p.stderr
        except Exception as e:  # noqa: BLE001 - the next video still runs
            code, stdout, stderr = -1, "", f"{type(e).__name__}: {e}"
        secs = time.time() - t
        log_dir = run_dir / run_folders.EXTRAS if (run_dir / run_folders.EXTRAS).is_dir() else out
        log = log_dir / (f"{name}.batch.log" if log_dir == out else "batch_console.log")
        log.write_text(" ".join(cmd) + "\n\n" + stdout + "\n" + stderr, encoding="utf-8")
        chk = checks_of(run_dir if (run_dir / run_folders.EXTRAS).is_dir() else None)
        failures = list(chk["failures"])
        if code not in (0, 1) and not failures:           # 1: a recreation criterion failed (real videos: always)
            failures.append(f"the run stopped (exit {code}): " + (stderr.strip().splitlines() or ["see the log"])[-1])
        outside = media_outside(run_dir)
        row = {"name": name, "competitor": str(comp), "raw": str(raw), "run_dir": str(run_dir), "exit": code,
               "speed": speed, "speed_from": why.strip("; ") or "default",
               "seconds": round(secs, 1), "pass": not failures and code in (0, 1), "failures": failures,
               "media_outside": outside, "hand": hand_checks(stdout), "log": str(log)}
        rows.append(row)
        print(f"    {'PASS' if row['pass'] else 'FAIL'} in {_fmt_s(secs)}" + (f": {failures[0]}" if failures else ""),
              flush=True)
        md = summary_md(rows, folder, skipped, started, time.time() - t_all)     # written after every video
        (out / f"{SUMMARY}.md").write_text(md, encoding="utf-8")
        (out / f"{SUMMARY}.json").write_text(json.dumps({"folder": str(folder), "started": started, "rows": rows},
                                                         indent=1, ensure_ascii=False), encoding="utf-8")
    n_ok = sum(1 for r in rows if r["pass"])
    print(f"match_cuts batch: {n_ok} of {len(rows)} passed in {_fmt_s(time.time() - t_all)}; "
          f"summary: {out / (SUMMARY + '.md')}")
    return 0 if n_ok == len(rows) else 1
