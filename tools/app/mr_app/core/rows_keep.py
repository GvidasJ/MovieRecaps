"""THE LISTS STAY WHEN THE APP CLOSES (run 18, task 4).

Every row of the Match cuts queue and of the New video list -- running, paused, waiting, failed or stopped, finished
-- is written when the app closes (the pages' ``closing()``, from the main window's close) and comes back exactly as
it was when the app opens: its inputs (the RAW, the competitor, the headline, the YouTube title, the channel, Fast /
Thorough, 100 / 125 %), its state, its stop message and result (so its buttons -- Try again, Try with another RAW,
Try with another competitor -- are there again), its steps.

* the queue: %APPDATA%\\MovieRecaps\\queue_rows.json. A video being made when the app closed was paused by the close
  (night 15: it comes back PAUSED, Resume goes on); one that could not be paused (it had not started its run yet, or
  its run folder can not go on) comes back STOPPED, saying so -- Try again starts it again.
* New video: %APPDATA%\\MovieRecaps\\newvideo_rows.json. A step that was going (a download, a conversion, a
  template) was stopped by the close: the row comes back STOPPED at that step (Try again goes on from it). A row
  whose edit was in the queue finds its queue row again (the same project). A torrent download the torrent part
  continues gets its own row back as before (core.torrent's restore), not a second one.

Written whole (a temporary file, then replaced: a failed write never leaves half a list). MOVIERECAPS_KEEP_ROWS=0
switches it off (the app's tests: each test starts with empty lists unless it switches it on).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

QUEUE_FILE = "queue_rows.json"
NEWVIDEO_FILE = "newvideo_rows.json"
ENV = "MOVIERECAPS_KEEP_ROWS"
VERSION = 1
CLOSED_WHILE_RUNNING = "the app was closed while it ran -- Try again starts it again"
CLOSED_WHILE_WORKING = "stopped when the app was closed -- Try again goes on from this step"


def enabled() -> bool:
    return os.environ.get(ENV, "").strip() != "0"


def _path(name: str) -> Path:
    from .settings import app_dir
    return app_dir() / name


def _write(name: str, rows: list[dict]) -> bool:
    """The list written whole (build the text first, then a temporary file replaces it); never raises."""
    if not enabled():
        return False
    try:
        text = json.dumps({"version": VERSION, "saved": datetime.now().astimezone().isoformat(timespec="seconds"),
                           "rows": rows}, indent=1, ensure_ascii=False, default=_plain)
        p = _path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _read(name: str) -> list[dict] | None:
    """The saved rows; None when there is no list (or it is switched off), [] when it can't be read."""
    if not enabled():
        return None
    p = _path(name)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = d.get("rows") if isinstance(d, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _plain(v: Any) -> Any:
    """JSON for what json can't write itself: paths and dates as text, sets as lists, the rest as text."""
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (set, tuple, frozenset)):
        return list(v)
    return str(v)


def _date(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# the Match cuts queue (core.jobs.Job)
# ---------------------------------------------------------------------------------------------------------------------

def job_to_dict(job: Any) -> dict:
    from . import pause as PZ
    d = PZ.to_dict(job)
    d.update(state=str(getattr(job, "state", "") or ""), result=dict(getattr(job, "result", None) or {}),
             upload_title=str(getattr(job, "upload_title", "") or ""),
             added=getattr(job, "added", None), edited=getattr(job, "edited", None),
             progress=str(getattr(job, "progress_plain", "") or getattr(job, "progress", "") or ""))
    return d


def job_from_dict(d: dict) -> Any:
    """The queue row again (a core.jobs.Job): its inputs, state and result. RUNNING -> PAUSED when its run folder can
    go on, else STOPPED (module docstring)."""
    from . import jobs as J
    from . import pause as PZ
    job = PZ.from_dict(d)
    state = str(d.get("state") or J.READY)
    res = d.get("result") if isinstance(d.get("result"), dict) else {}
    if state in (J.RUNNING, J.PAUSED):
        if PZ.can_resume(job.run_dir):
            state, res = J.PAUSED, (res if state == J.PAUSED and res else {"label": PZ.LABEL})
        else:                            # a run stopped by the close itself: Try again on its row (stopped_by_itself)
            from . import results as R
            state, res = J.DONE, {"label": R.FAIL, "failures": [CLOSED_WHILE_RUNNING], "reason_kind": "app closed",
                                  "reason_line": CLOSED_WHILE_RUNNING,
                                  "run_dir": str(job.run_dir) if job.run_dir else None}
    job.state = state
    job.result = dict(res)
    job.upload_title = str(d.get("upload_title") or "")
    job.added = _date(d.get("added"))
    job.edited = _date(d.get("edited"))
    job.progress = "" if state != J.PAUSED else str(d.get("progress") or "")
    return job


def save_queue(jobs: Iterable[Any]) -> bool:
    return _write(QUEUE_FILE, [job_to_dict(j) for j in jobs])


def load_queue() -> list | None:
    """The queue's rows to show again, in their saved order; None when no list was ever saved (then night 15's
    paused.json is read, as before)."""
    rows = _read(QUEUE_FILE)
    if rows is None:
        return None
    out = []
    for d in rows:
        try:
            out.append(job_from_dict(d))
        except Exception:  # noqa: BLE001 - a broken entry is left out
            continue
    return out


# ---------------------------------------------------------------------------------------------------------------------
# New video (tools.download_tool.Item)
# ---------------------------------------------------------------------------------------------------------------------

ITEM_FIELDS = ("competitor_url", "raw_url", "raw_file", "channel", "title", "mode", "speed", "edit", "state",
               "project", "name", "headline", "message", "error", "upload_title", "kind", "raw_torrent", "comp_file",
               "added", "started", "gate", "failed_step", "post_title", "video_headline", "default_template",
               "result", "roles", "rename_note")
ITEM_PATHS = {"raw_file", "project", "comp_file"}


def item_to_dict(it: Any) -> dict:
    d = {k: getattr(it, k, None) for k in ITEM_FIELDS}
    for k in ITEM_PATHS:
        d[k] = str(d[k]) if d[k] is not None else None
    d["steps"] = {k: {"state": s.state, "share": float(s.share or 0.0), "text": s.text or "",
                      "finished": s.finished.isoformat() if isinstance(s.finished, datetime) else None}
                  for k, s in (getattr(it, "steps", None) or {}).items()}
    job = getattr(it, "job", None)
    d["job_run_dir"] = str(job.run_dir) if job is not None and getattr(job, "run_dir", None) else None
    return d


def item_fields(d: dict) -> dict:
    """The Item's keyword arguments and its steps' values (tools.download_tool makes the Item and its Steps)."""
    kw = {k: d.get(k) for k in ITEM_FIELDS if k in d}
    for k in ITEM_PATHS:
        kw[k] = Path(kw[k]) if kw.get(k) else None
    if kw.get("started"):
        kw["started"] = _date(kw["started"])
    for k in ("speed", "added"):
        if kw.get(k) is not None:
            try:
                kw[k] = float(kw[k])
            except (TypeError, ValueError):
                kw.pop(k)
    kw["roles"] = tuple(kw.get("roles") or ())
    kw["result"] = dict(kw.get("result") or {})
    steps = {k: {"state": str(v.get("state") or ""), "share": float(v.get("share") or 0.0),
                 "text": str(v.get("text") or ""), "finished": _date(v.get("finished"))}
             for k, v in (d.get("steps") or {}).items() if isinstance(v, dict)}
    return {"kw": kw, "steps": steps, "job_run_dir": d.get("job_run_dir")}


def save_newvideo(items: Iterable[Any]) -> bool:
    return _write(NEWVIDEO_FILE, [item_to_dict(i) for i in items])


def load_newvideo() -> list[dict] | None:
    """The New video rows' saved fields (item_fields of each), in their saved order; None when none were saved."""
    rows = _read(NEWVIDEO_FILE)
    if rows is None:
        return None
    out = []
    for d in rows:
        try:
            out.append(item_fields(d))
        except Exception:  # noqa: BLE001
            continue
    return out
