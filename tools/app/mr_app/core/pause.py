"""PAUSE / RESUME (night 15, task 6): a running video paused goes on later from the step it was at, not from the start
-- also after the app was closed and opened again.

Pause stops the tool's process (the same as Stop: every result the tool keeps is written whole or not at all -- a
temporary file renamed into place -- so a stop never leaves a broken cache). The run folder stays as it was, with a
note in it (extras\\paused.json: match_cuts.cli.PAUSED_FILE), and the video goes into this computer's list of paused
videos (%APPDATA%\\MovieRecaps\\paused.json): the queue shows it again, paused, when the app opens.

Resume runs the tool again INTO THE SAME run folder (``--run-dir <it> --resume``): the steps the run had finished come
back from the tool's caches in seconds (S2's converted RAW, the sound search, the picture search ...), the step it was
at starts again, its log goes on where it was. Closing the app while a video is made pauses it (instead of losing it).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

PAUSED = "paused"                    # the job state (core.jobs)
LABEL = "PAUSED"                     # the result word on the row
MARK = "paused.json"                 # in the run's extras\ (match_cuts.cli.PAUSED_FILE)
LIST_FILE = "paused.json"            # in the app's folder: the paused videos of this computer

# what a paused job keeps (core.jobs.Job's fields; paths as text)
FIELDS = ("name", "competitor", "raw", "mode", "speed", "speed_from", "comp_s", "raw_s", "source", "channel", "title",
          "headline", "project", "out_dir", "run_name", "force_inputs", "roles_chosen", "roles_how", "take", "run_dir")
PATHS = {"competitor", "raw", "project", "out_dir", "run_dir"}


def mark_path(run_dir: Path) -> Path:
    from match_cuts import run_folders
    return Path(run_dir) / run_folders.EXTRAS / MARK


def can_resume(run_dir: Path | None) -> bool:
    """A paused run folder that can go on: its note there and no finished edit."""
    if run_dir is None:
        return False
    return mark_path(run_dir).is_file() and not (Path(run_dir) / "1_edit.xml").is_file()


def mark(run_dir: Path, step: str = "", plain: str = "") -> None:
    """The note in the run folder (the tool's --resume needs it): when and at which step it was paused."""
    p = mark_path(run_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"paused": datetime.now().isoformat(timespec="seconds"), "step": step,
                               "plain": plain}, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def read_mark(run_dir: Path | None) -> dict:
    try:
        return json.loads(mark_path(run_dir).read_text(encoding="utf-8")) if run_dir is not None else {}
    except (OSError, ValueError):
        return {}


def to_dict(job) -> dict:
    d = {}
    for k in FIELDS:
        v = getattr(job, k, None)
        d[k] = str(v) if (k in PATHS and v is not None) else v
    d["paused_step"] = getattr(job, "paused_step", "")
    return d


def from_dict(d: dict):
    """A paused job again (core.jobs.Job, state PAUSED)."""
    from .jobs import Job
    kw = {}
    for k in FIELDS:
        if k in d and k != "run_dir":
            v = d[k]
            kw[k] = Path(v) if (k in PATHS and v) else (None if k in PATHS else v)
    job = Job(**{k: v for k, v in kw.items() if k != "take"})
    job.take = int(d.get("take") or 1)
    job.run_dir = Path(d["run_dir"]) if d.get("run_dir") else None
    job.state = PAUSED
    job.paused_step = str(d.get("paused_step") or "")
    job.result = {"label": LABEL}
    return job


def list_path() -> Path:
    from .settings import app_dir
    return app_dir() / LIST_FILE


def save(jobs) -> None:
    """This computer's paused videos (the queue's paused rows), written whole."""
    p = list_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps([to_dict(j) for j in jobs], indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def load() -> list:
    """The paused videos to show again (those whose run folder can still go on)."""
    try:
        items = json.loads(list_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for d in items if isinstance(items, list) else []:
        try:
            j = from_dict(d)
        except Exception:  # noqa: BLE001 - a broken entry is left out
            continue
        if can_resume(j.run_dir):
            out.append(j)
    return out


def line(job) -> str:
    """The row's line: 'paused at Finding shots -- Resume goes on from there'."""
    step = getattr(job, "paused_step", "") or read_mark(getattr(job, "run_dir", None)).get("plain", "")
    return (f"paused at {step}" if step else "paused") + " -- Resume goes on from there"
