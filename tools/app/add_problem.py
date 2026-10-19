"""Add a problem to problems_queue.md in the shared projects folder by hand (day 12, section 2) -- the app adds them by
itself from now on; this is for runs made before, or found another way.

    .venv\\Scripts\\python tools\\app\\add_problem.py day12                 (the two problems of day 12's prompt)
    .venv\\Scripts\\python tools\\app\\add_problem.py day12 --dry-run       (only print what would be written)
    .venv\\Scripts\\python tools\\app\\add_problem.py add --name VIDEO --run RUN_FOLDER --check "FAIL: ..."
          [--copy PC_COPY] [--computer laptop] [--result FAIL] [--date "2026-10-19 02:44"] [--line "log line" ...]
          [--from-log]   (the key log lines read from the run's log, or the copy's)

The folder is the app's "Save projects to" (D:\\Shared\\Projects on the PC), or --hub FOLDER. An entry with the same
run and check is not added twice. Exit code 0 when written, 1 when not (the reason in plain words)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools" / "match_cuts"))

from mr_app.core import problems as PR  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

DOWNLOADS = r"C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads"
PROBLEMS = r"D:\Shared\problems"


def day12() -> list[PR.Problem]:
    """The two problems of docs\\day12_prompt.md (section 2), with the key lines of their logs (read on the PC from
    the copies in D:\\Shared\\problems)."""
    mind = "2026-10-19_video-by-mindculpt"
    dark = "2026-10-19_video-by-darkhumor"
    return [
        PR.Problem(
            name=mind,
            check=("FAIL: XML PERSON -- the person speaking is outside the template window at 00:00:07:41 (clips "
                   "S06–S34) and at 00:00:27:03 (clips S35–S39); nothing was re-framed (Rush Hour)"),
            run=rf"{DOWNLOADS}\{mind}\edit", copy=rf"{PROBLEMS}\{mind}\edit", computer="laptop",
            date="2026-10-19 02:44", result="FAIL", log=rf"{DOWNLOADS}\{mind}\edit\extras\batch_console.log",
            lines=[
                "match_cuts result: FAIL",
                "02:38:43 WARNING premiere export validation: 2 problem(s): XML PERSON S06+...+S34 at 00:00:07:41: the "
                "person speaking is not in the window (x 20-1060, y 525-1739): face at x 433-1275, y 532-1586 (who is "
                "there: RAW 3378.43-3380.27 s); XML PERSON S35+S36+S37+S38+S39 at 00:00:27:03: the person speaking is "
                "not in the window (x 20-1060, y 525-1739): face at x 1067-1440, y 689-1245 (who is there: RAW "
                "3401.28-3403.30 s)",
                "report.md: The person speaking in the picture: 16 V1 clips checked (16 with the person speaking found "
                "by Light-ASD on cuda, 0 the biggest face, 0 nobody speaking, 0 nobody in the picture); 0 re-framed to "
                "show the person speaking; 2 PROBLEM(S) -- the run fails",
                "the app showed it as \"OK with a note\" (project.json steps.edit.result) although the tool said FAIL",
            ]),
        PR.Problem(
            name=dark,
            check=("stopped (exit 3) -- the app showed only an OpenCV warning as the reason (Family Guy S10E18)"),
            run=rf"{DOWNLOADS}\{dark}\edit", copy=rf"{PROBLEMS}\{dark}\edit", computer="laptop",
            date="2026-10-19 03:00", result="FAIL", exit="3",
            log=rf"{DOWNLOADS}\{dark}\edit\extras\batch_console.log",
            lines=[
                "match_cuts result: PASS (criterion 6 not verified: Premiere-only run (--premiere): no After Effects "
                "export)",
                "02:59:46 WARNING captions: 7 caption(s) start more than 2 frames from their first word: 00:00:00,367 "
                "'saved sir' (+7 frames); ... 00:00:10,483 'no' (+8 frames); 00:00:11,017 'not bring' (-7 frames)",
                "02:56:48 WARNING segment: PySceneDetect cross-check failed: No module named 'scenedetect.output'",
                "the app's message (stderr's last line): [ WARN:0@720.058] global net_impl_backend.cpp:345 "
                "cv::dnn::dnn5_v20260605::Net::Impl::setPreferableTarget Targets are not supported by the new graph "
                "engine for now",
                "note: exit 3 is pipeline.EXIT_NOT_VERIFIED (a PASS with a criterion not verified); "
                "core/results.label_of reads every exit code but 0 / 1 as a stop",
            ]),
    ]


def _hub(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    from mr_app.core import settings
    return settings.save_root()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Add a problem to problems_queue.md (the shared projects folder).")
    ap.add_argument("what", choices=("day12", "add"))
    ap.add_argument("--hub", help="the shared projects folder (default: the app's Save projects to)")
    ap.add_argument("--dry-run", action="store_true", help="only print the entries")
    ap.add_argument("--name")
    ap.add_argument("--run", default="")
    ap.add_argument("--copy", default="")
    ap.add_argument("--check", default="")
    ap.add_argument("--computer", default="")
    ap.add_argument("--result", default="FAIL")
    ap.add_argument("--date", default="")
    ap.add_argument("--line", action="append", default=[])
    ap.add_argument("--from-log", action="store_true", help="read the key log lines from the run's (or copy's) log")
    a = ap.parse_args(argv)
    if a.what == "day12":
        items = day12()
    else:
        if not a.name:
            print("add needs --name (the video's name)", file=sys.stderr)
            return 2
        p = PR.Problem(name=a.name, check=a.check, run=a.run, copy=a.copy, computer=a.computer, result=a.result,
                       date=a.date, lines=list(a.line))
        if a.from_log:
            for base in (a.run, a.copy):
                text = PR._read_log(Path(base) / "extras" / "batch_console.log") if base else ""
                if text:
                    p.lines = p.lines + PR.key_lines(text)
                    p.log = str(Path(base) / "extras" / "batch_console.log")
                    break
        items = [p]
    hub = _hub(a.hub)
    bad = 0
    for p in items:
        print(p.block(hub) + "\n")
        if a.dry_run:
            continue
        err = PR.add(hub, p)
        if err:
            print(f"NOT written: {err}", file=sys.stderr)
            bad += 1
    if not a.dry_run:
        print(f"{len(items) - bad} problem(s) in {hub / PR.FILE}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
