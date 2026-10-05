"""``python -m match_cuts check-all``: run every test video of the library (tests/real, testcases.py) end to end and
print one scorecard.

Each case runs as its own ``python -m match_cuts --premiere`` process (``--out <out>/<case>``, ``--work
<work>/<case>``: the stage caches are kept, so a second check-all only redoes what changed). The scorecard shows per
video: the run time, the hard checks of the Premiere deliverables (9.8: the XML item / gap / audio / link / person /
... checks and the captions file), determinism (9.7) and coverage (c1), and -- for a video with an answer key -- the
caption score (caption_score.py: the user's captions reproduced exactly, word errors, rule breaks, the remaining
differences by type) and, where the key is the user's own edit, the cut score (edit_score.py: the user's cut points
reproduced within 2 frames, and how much longer or shorter the tool's edit is). The criteria c2-c5 (a frame-exact After Effects recreation) are not part of it: real videos
fail them by design.

The scorecard is saved as ``<out>/scorecard.json`` and appended to ``<out>/history.jsonl``; the previous one is
shown next to it (+/-). Exit code: 0 when every video's hard checks pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from . import testcases

DEFAULT_OUT = testcases.REPO / "work" / "check-all"


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(testcases.REPO), timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def run_case(case: testcases.Case, out_root: Path, work_root: Path, extra: Sequence[str] = (),
             python: str = sys.executable, cwd: str | None = None) -> dict:
    """One case through the tool (its own process); returns its scorecard row."""
    from . import run_folders
    out, work = out_root / case.name, work_root / case.name
    out.mkdir(parents=True, exist_ok=True)
    cmd = [python, "-m", "match_cuts", "--competitor", str(case.competitor), "--raw", str(case.raw),
           "--out", str(out), "--work", str(work), "--premiere", *case.options, *extra]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env)
    secs = time.time() - t
    run_dir = run_folders.newest_run_dir(out)
    (out / "check-all.log").write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
    row = {"case": case.name, "exit": p.returncode, "seconds": round(secs, 1),
           "run_dir": str(run_dir) if run_dir else None}
    return dict(row, **read_run(case, run_dir))


def read_run(case: testcases.Case, run_dir: Path | None) -> dict:
    """The scorecard row of a finished run folder: its checks and (with an answer key) its caption score."""
    from . import caption_score
    from .run_folders import EXTRAS
    row: dict = {}
    if run_dir is None:
        return {"error": "no run folder"}
    vf = run_dir / EXTRAS / "verify.json"
    if vf.is_file():
        v = json.loads(vf.read_text(encoding="utf-8"))
        checks, crit = v.get("checks") or {}, v.get("criteria") or {}
        for key, name in (("s9_8_deliverables", "deliverables"), ("s9_7_determinism", "determinism")):
            c = checks.get(key) or {}
            row[name] = {"status": c.get("status") or "not run", "summary": c.get("summary") or "",
                         "failures": list(c.get("failures") or [])[:20]}
        c1 = crit.get("c1_coverage") or {}
        row["coverage"] = {"status": c1.get("status") or "not run", "summary": c1.get("summary") or ""}
    else:
        row["error"] = "no verify.json (the run stopped early: see check-all.log)"
    cj = run_dir / EXTRAS / "debug" / "captions.json"
    if cj.is_file():
        c = json.loads(cj.read_text(encoding="utf-8"))
        row["captions_mode"] = c.get("mode")
        row["captions_count"] = c.get("count")
        row["transcriber"] = c.get("transcriber")
    if case.has_key:
        try:
            sc = caption_score.score_run(case.name, case.answer_srt, testcases.answer_timeline(case), run_dir)
            row["score"] = sc.to_dict()
        except Exception as e:  # noqa: BLE001 - a broken run scores nothing; the card says why
            row["score_error"] = f"{type(e).__name__}: {e}"
    if case.has_cut_key:
        try:
            from . import edit_score
            row["cut_score"] = edit_score.score_run(case.answer_edit, run_dir).to_dict()
        except Exception as e:  # noqa: BLE001
            row["cut_score_error"] = f"{type(e).__name__}: {e}"
    return row


def hard_ok(row: dict) -> bool:
    ok = lambda k: str((row.get(k) or {}).get("status")) in ("pass", "pass_with_exceptions")    # noqa: E731
    return ok("deliverables") and ok("determinism") and ok("coverage") and "error" not in row


def _fmt_s(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{int(s // 60)}m{int(s % 60):02d}s" if s >= 60 else f"{s:.0f}s"


def scorecard(rows: Sequence[dict], prev: dict | None = None) -> str:
    before = {r["case"]: r for r in (prev or {}).get("rows") or []}
    lines = [f"{'video':<20} {'time':>7}  {'hard checks':<12} {'captions exactly':<22} {'word errors':<16} "
             f"{'rule breaks':<12} {'cuts reproduced':<20} {'length':<16} differences"]
    for r in rows:
        hc = "PASS" if hard_ok(r) else "FAIL"
        sc = r.get("score")
        b = (before.get(r["case"]) or {}).get("score")
        if sc:
            ex = f"{sc['exact']}/{sc['key']} ({sc['exact_pct']:.0f} %)"
            if b:
                ex += f" {sc['exact'] - b['exact']:+d}"
            we = f"{sc['wer']:.1f} %" + (f" {sc['wer'] - b['wer']:+.1f}" if b else "")
            rb = f"{len(sc['rule_breaks'])}" + (f" {len(sc['rule_breaks']) - len(b['rule_breaks']):+d}" if b else "")
            diff = ", ".join(f"{k} {v}" for k, v in (sc.get("by_type") or {}).items())
        else:
            ex, we, rb, diff = ("no caption key" if "score_error" not in r else "score failed"), "-", "-", ""
        cs, cb = r.get("cut_score"), (before.get(r["case"]) or {}).get("cut_score")
        if cs:
            cu = f"{cs['reproduced']}/{cs['cuts']} ({cs['reproduced_pct']:.0f} %)" + (
                f" {cs['reproduced'] - cb['reproduced']:+d}" if cb else "")
            le = f"{cs['length_diff']:+.1f} s ({cs['length_diff_pct']:+.0f} %)"
        else:
            cu, le = ("no cut key" if "cut_score_error" not in r else "score failed"), "-"
        lines.append(f"{r['case']:<20} {_fmt_s(r.get('seconds')):>7}  {hc:<12} {ex:<22} {we:<16} {rb:<12} {cu:<20} "
                     f"{le:<16} {diff}")
        if not hard_ok(r):
            for k in ("deliverables", "determinism", "coverage"):
                st = (r.get(k) or {})
                if str(st.get("status")) not in ("pass", "pass_with_exceptions"):
                    lines.append(f"{'':<30}{k}: {st.get('status')} -- {'; '.join(st.get('failures') or [])[:300]}")
            if r.get("error"):
                lines.append(f"{'':<30}{r['error']}")
    keyed = [r["score"] for r in rows if r.get("score")]
    if keyed:
        n = sum(s["key"] for s in keyed)
        ex = sum(s["exact"] for s in keyed)
        w = sum(s["words"] for s in keyed)
        e = sum(s["subs"] + s["ins"] + s["dels"] for s in keyed)
        lines.append(f"{'all caption keys':<20} {'':>7}  {'':<12} {ex}/{n} ({100 * ex / max(1, n):.0f} %){'':<6} "
                     f"{100 * e / max(1, w):.1f} %{'':<10} {sum(len(s['rule_breaks']) for s in keyed)}")
    cut = [r["cut_score"] for r in rows if r.get("cut_score")]
    if cut:
        n = sum(s["cuts"] for s in cut)
        ex = sum(s["reproduced"] for s in cut)
        k = sum(s["length_key"] for s in cut)
        d = sum(s["length_diff"] for s in cut)
        lines.append(f"{'all cut keys':<20} {'':>7}  {'':<12} {'':<22} {'':<16} {'':<12} "
                     f"{f'{ex}/{n} ({100 * ex / max(1, n):.0f} %)':<20} {d:+.1f} s ({100 * d / max(1e-9, k):+.0f} %)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m match_cuts check-all",
                                 description="Run every test video (tests/real) and print one scorecard.")
    ap.add_argument("--cases", default="", help="comma-separated case names (default: every case)")
    ap.add_argument("--cases-dir", default=str(testcases.CASES_DIR), help="the test library (default tests/real)")
    ap.add_argument("--out", default=str(DEFAULT_OUT / "runs"), help="run folders (one sub-folder per case)")
    ap.add_argument("--work", default=str(DEFAULT_OUT / "work"), help="stage caches (one sub-folder per case)")
    ap.add_argument("--rescore", action="store_true", help="score each case's newest run again without running it")
    ap.add_argument("--fast", action="store_true", help="pass --fast to every run (quick, less thorough); "
                                                         "otherwise every run gets --compare-fast")
    ap.add_argument("--python", default=sys.executable, help=argparse.SUPPRESS)
    ap.add_argument("--cwd", default=None, help=argparse.SUPPRESS)
    a, extra = ap.parse_known_args(argv)
    if a.fast:
        extra = ["--fast", *extra]
    elif "--compare-fast" not in extra:
        extra = ["--compare-fast", *extra]          # the scorecard runs say what the thoroughness changed
    if "--check-determinism" not in extra:
        extra = ["--check-determinism", *extra]     # the hard check 9.7: the cut list re-assembled from the caches
    names = [n for n in a.cases.split(",") if n]
    cases = testcases.cases(names or None, Path(a.cases_dir))
    out_root, work_root = Path(a.out), Path(a.work)
    card = out_root.parent / "scorecard.json"
    prev = json.loads(card.read_text(encoding="utf-8")) if card.is_file() else None
    print(f"check-all: {len(cases)} video(s): {', '.join(c.name for c in cases)}", flush=True)
    rows = []
    t0 = time.time()
    for c in cases:
        if a.rescore:
            from . import run_folders
            run_dir = run_folders.newest_run_dir(out_root / c.name)
            row = dict({"case": c.name, "exit": None, "seconds": None, "run_dir": str(run_dir) if run_dir else None},
                       **read_run(c, run_dir))
        else:
            print(f"  {c.name} ...", flush=True)
            row = run_case(c, out_root, work_root, extra, a.python, a.cwd)
            print(f"  {c.name}: {_fmt_s(row['seconds'])}, exit {row['exit']}", flush=True)
        rows.append(row)
    total = time.time() - t0
    print()
    print(scorecard(rows, prev))
    print(f"\nTotal {_fmt_s(total)}. Run folders: {out_root}")
    doc = {"when": _dt.datetime.now().isoformat(timespec="seconds"), "git": _git_head(), "seconds": round(total, 1),
           "extra": list(extra), "rows": rows}
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    with (card.parent / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: doc[k] for k in ("when", "git", "seconds", "extra")} |
                           {"rows": [{k: r.get(k) for k in ("case", "seconds", "exit")} |
                                     {"hard": hard_ok(r), "score": {k: (r.get("score") or {}).get(k) for k in
                                                                    ("exact", "key", "wer", "words")} |
                                      {"rule_breaks": len((r.get("score") or {}).get("rule_breaks") or [])}}
                                     for r in rows]}) + "\n")
    return 0 if all(hard_ok(r) for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
