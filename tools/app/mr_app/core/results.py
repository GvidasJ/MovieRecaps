"""A finished run -> its result in the queue, read the way ``python -m match_cuts batch`` reads it
(match_cuts.batch.checks_of / verdict / stop_message), plus the two stops the tool names itself:

* PASS / OK with a note (every file there, a check of 1_edit.xml found something to look at) / FAIL;
* WRONG PAIR -- the quick check before the long run (seconds) found that the competitor does not come from this RAW
  (night 11: "This competitor doesn't seem to come from this RAW"; red, like PICTURE NOT FOUND);
* PICTURE NOT FOUND -- the competitor's sound is in the RAW but its picture is not (night 8);
* NOT IN THIS RAW -- neither its picture nor its sound (night 6: "the competitor's scenes are not in this RAW");
* STOPPED -- you pressed Stop.
"""
from __future__ import annotations

from pathlib import Path

PASS, OK_NOTE, FAIL = "PASS", "OK with a note", "FAIL"
PICTURE_NOT_FOUND, NOT_IN_RAW, STOPPED = "PICTURE NOT FOUND", "NOT IN THIS RAW", "STOPPED"
WRONG_PAIR = "WRONG PAIR"
GOOD = (PASS, OK_NOTE)
RED = (FAIL, WRONG_PAIR, PICTURE_NOT_FOUND, NOT_IN_RAW)      # the labels shown red
# a run that went to its end (match_cuts.batch.FINISHED): 0 / 1 / 3 -- 3 = nothing failed, a criterion not verified
# (every Premiere-only run: no After Effects check); day 12: darkhumor passed everything, exited 3, showed as FAIL
FINISHED = (0, 1, 3)


def label_of(code: int | None, stop: str, checks: dict | None, stopped: bool = False,
             finished: bool | None = None) -> str:
    """The queue's result word from the run's exit code, its stop message and its hard checks (``finished``: the
    run went to its end -- match_cuts.batch.finished; None = by the exit code alone)."""
    if stopped:
        return STOPPED
    low = (stop or "").lower()
    if not (code in FINISHED if finished is None else finished):
        if low.lstrip().startswith("wrong pair"):
            return WRONG_PAIR
        if "picture not found" in low:
            return PICTURE_NOT_FOUND
        if "not in this raw" in low:
            return NOT_IN_RAW
        return FAIL
    if checks is None or not checks.get("pass"):
        return FAIL
    return OK_NOTE if checks.get("notes") else PASS


def read_result(run_dir: Path, code: int | None, stdout: str, stderr: str, stopped: bool = False) -> dict:
    """{label, failures, notes, hand, doubtful, stop, run_dir, report, log} of a finished run (the batch's checks).
    A run that stopped also gets ``reason`` / ``todo`` / ``reason_line`` / ``reason_kind``: why it stopped and what
    to do in plain words (day 13: match_cuts.plain_reason -- its console, its match_cuts.log, its exit code; never
    just "details in ... match_cuts.log"). Reads the run's files: call it from a worker thread."""
    from match_cuts import batch, run_folders
    extras = run_dir / run_folders.EXTRAS
    try:
        chk = batch.checks_of(run_dir if extras.is_dir() else None) if not stopped else None
    except Exception as e:  # noqa: BLE001 - a run that wrote nothing readable
        chk = {"pass": False, "failures": [f"the run's files could not be read ({type(e).__name__}: {e})"],
               "notes": []}
    done = batch.finished(code, stdout, run_dir)       # exit 3 only with its result line / verify.json
    stop = batch.stop_message(stderr, code=code) if not done and not stopped else ""
    why = None
    if not done and not stopped:
        try:
            why = batch.stop_reason(stderr, code, run_dir, stop)
        except Exception:  # noqa: BLE001 - an older tool without plain_reason: its stop message as it is
            why = None
    failures = list((chk or {}).get("failures") or [])
    if not done and not stopped:
        failures = [f"the run stopped (exit {code}): {why.line() if why is not None else stop}"] + \
            [f for f in failures if "no verify.json" not in f]
    label = label_of(code, stop, chk, stopped, finished=done)
    caps = batch.captions_of(run_dir) if extras.is_dir() else {"written": False, "why": "", "doubtful": []}
    out = {"label": label, "failures": failures if label not in GOOD else [],
           "notes": list((chk or {}).get("notes") or []) if label in GOOD else [],
           "hand": batch.hand_checks(stdout or ""), "doubtful": list(caps.get("doubtful") or []),
           "captions": bool(caps.get("written")), "stop": stop, "run_dir": str(run_dir),
           "report": str(extras / "report.md"), "log": str(extras / "batch_console.log")}
    if why is not None and label not in GOOD:
        out.update(reason=why.text, todo=why.todo, reason_line=why.line(), reason_kind=why.kind,
                   reason_detail=why.detail)
    return out


def stopped_by_itself(result: dict | None) -> bool:
    """A run that stopped before its end for a reason that is not its input (a full disk, a crash, out of
    memory ...) -- the queue row offers Try again for it (WRONG PAIR and the input checks have their own buttons)."""
    if not isinstance(result, dict) or result.get("label") != FAIL:
        return False
    kind = str(result.get("reason_kind") or "")
    return bool(kind) and kind != "tool stop"


def short(result: dict) -> str:
    """One line under the result word: why it failed (a stop: why in plain words and what to do), or the note."""
    if result.get("label") == STOPPED:
        return "stopped by you"
    if result.get("label") == WRONG_PAIR:              # the stop message without "WRONG PAIR:" (the pill says it)
        s = str(result.get("stop") or "").strip()
        s = s[len(WRONG_PAIR):].lstrip(" :-") if s.upper().startswith(WRONG_PAIR) else s
        return (s or "This competitor doesn't seem to come from this RAW")[:220]
    if result.get("reason_line") and result.get("label") not in GOOD:
        return str(result["reason_line"])[:400]
    if result.get("failures"):
        return str(result["failures"][0])[:220]
    if result.get("notes"):
        return str(result["notes"][0])[:220]
    if not result.get("captions", True):
        return "no 2_captions.srt"
    return "every check passed"
