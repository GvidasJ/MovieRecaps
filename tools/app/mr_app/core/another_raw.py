"""Day 16, task 1: "Try with another RAW". A run that failed because of its RAW (WRONG PAIR, NOT IN THIS RAW, PICTURE
NOT FOUND, or a RAW that can't be read) keeps everything you typed or chose -- the YouTube title, the template's
text (the headline), the channel, the competitor clip, the mode and the speed -- so New video only asks for the new
RAW.

* ``failed_by_raw(result)``: a queue row's result dict (core.results) failed because of its RAW;
* ``edit_failed_by_raw(data)``: the same from a project's project.json (History, after a restart);
* ``kept_from_project(folder | data)`` / ``kept_from_job(job)``: what is kept (``Kept``), read from project.json first
  (it survives a restart), the queue row's own fields where the project has nothing.

Pure functions (``kept_from_project`` of a folder reads its project.json: call it from a worker thread on the share).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

RAW_LABELS = ("WRONG PAIR", "PICTURE NOT FOUND", "NOT IN THIS RAW")
BUTTON = "Try with another RAW"
TIP = ("Make this video again with another RAW: the YouTube title, the headline, the channel and the competitor are "
       "kept -- you only pick or download the new RAW")

# a RAW that can't be read: conform's "could not decode source frames ... by PTS" (the Ted 2 x265 runs), FFmpeg's
# damaged-file words (plain_reason's BROKEN_VIDEO), a RAW with no video
_UNREADABLE = re.compile(r"could not decode source frames|could not decode the raw|raw[^.;]{0,80}(?:can't|cannot|could "
                         r"not) be read|invalid data found when processing input|moov atom not found|"
                         r"not a video or it is damaged|error while decoding|could not find codec parameters", re.I)
_BROKEN_KIND = "damaged video"          # match_cuts.plain_reason.BROKEN_VIDEO


def _says_raw(text: str) -> bool:
    """The text names the RAW ('conform raw: ...', 'the RAW ...')."""
    return bool(re.search(r"\braw\b", text or "", re.I))


def unreadable_raw(text: str) -> bool:
    """A stop message / failure that says the RAW can't be read."""
    t = str(text or "")
    if "could not decode source frames" in t.lower():
        return True
    return bool(_UNREADABLE.search(t)) and _says_raw(t)


def failed_by_raw(result: Mapping | None) -> bool:
    """A queue row's result: it failed because of its RAW (the button shows)."""
    if not isinstance(result, Mapping):
        return False
    if str(result.get("label") or "") in RAW_LABELS:
        return True
    if str(result.get("label") or "") != "FAIL":
        return False
    texts = [str(result.get(k) or "") for k in ("stop", "reason", "reason_line", "reason_detail")]
    texts += [str(f) for f in (result.get("failures") or [])][:3]
    if str(result.get("reason_kind") or "") == _BROKEN_KIND and any(_says_raw(t) for t in texts):
        return True
    return any(unreadable_raw(t) for t in texts)


def edit_failed_by_raw(data: Mapping | None) -> bool:
    """project.json's edit step failed because of the RAW (WRONG PAIR ... or a RAW that can't be read)."""
    steps = (data or {}).get("steps") if isinstance((data or {}).get("steps"), Mapping) else {}
    e = steps.get("edit") if isinstance(steps.get("edit"), Mapping) else {}
    if not e or e.get("state") not in ("failed", "done"):
        return False
    result = str(e.get("result") or "")
    if result in RAW_LABELS:
        return True
    if e.get("state") != "failed":
        return False
    fails = e.get("failures") if isinstance(e.get("failures"), (list, tuple)) else []
    return any(unreadable_raw(str(t)) for t in [e.get("error") or "", *fails[:3]])


@dataclass
class Kept:
    """What "Try with another RAW" keeps."""
    title: str = ""                 # the template's text (the headline, two lines)
    upload_title: str = ""          # the YouTube title
    channel: str = ""
    competitor_file: Path | None = None     # the competitor clip, when its file is still there
    competitor_url: str = ""                # else (or as well) its link
    mode: str = ""
    speed: float = 100.0
    project: Path | None = None     # the failed project (its folder)
    raw_name: str = ""              # the RAW that failed (said in the form's message)
    why: str = ""                   # its result: WRONG PAIR ...

    @property
    def has_competitor(self) -> bool:
        return self.competitor_file is not None or bool(self.competitor_url)


def _data(p: Any) -> tuple[dict, Path | None]:
    if isinstance(p, Mapping):
        folder = p.get("_folder")
        return dict(p), (Path(folder) if folder else None)
    if isinstance(p, (str, os.PathLike)):
        from . import projects
        folder = Path(p)
        try:
            data = projects.read_json(folder / projects.FILE)
        except (OSError, ValueError):
            data = {}
        return (data if isinstance(data, dict) else {}), folder
    return {}, None


def kept_from_project(p: Any) -> Kept:
    """What a project keeps: ``p`` its folder, or project.json's data (with ``_folder``: its folder, as History's
    data carries it)."""
    data, folder = _data(p)
    comp = data.get("competitor") if isinstance(data.get("competitor"), Mapping) else {}
    raw = data.get("raw") if isinstance(data.get("raw"), Mapping) else {}
    steps = data.get("steps") if isinstance(data.get("steps"), Mapping) else {}
    e = steps.get("edit") if isinstance(steps.get("edit"), Mapping) else {}
    f = None
    if comp.get("file") and folder is not None and (folder / str(comp["file"])).is_file():
        f = folder / str(comp["file"])
    else:                                # the competitor as the edit was given it (its inputs), when still there
        ins = e.get("inputs") if isinstance(e.get("inputs"), Mapping) else {}
        c = ins.get("competitor") if isinstance(ins.get("competitor"), Mapping) else {}
        for key in ("path",):
            if c.get(key) and Path(str(c[key])).is_file():
                f = Path(str(c[key]))
        if f is None and comp.get("source_file") and Path(str(comp["source_file"])).is_file():
            f = Path(str(comp["source_file"]))
    try:
        speed = float(e.get("speed") or data.get("speed") or 100.0)
    except (TypeError, ValueError):
        speed = 100.0
    return Kept(title=str(data.get("title") or ""), upload_title=str(data.get("upload_title") or ""),
                channel=str(data.get("channel") or ""), competitor_file=f,
                competitor_url=str(comp.get("url") or ""), mode=str(e.get("mode") or ""), speed=speed,
                project=folder, raw_name=str(raw.get("file") or raw.get("title") or ""),
                why=str(e.get("result") or ""))


def kept_from_job(job: Any, data: Mapping | None = None) -> Kept:
    """What a queue row keeps: its project's project.json first (``data``: already read, else read now), then the
    row's own fields (a row without a project: its competitor file, channel, headline)."""
    project = getattr(job, "project", None)
    k = kept_from_project(dict(data, _folder=str(project)) if data is not None and project is not None
                          else project) if project is not None else Kept()
    if not k.title:
        k.title = str(getattr(job, "title", "") or "")
    if not k.upload_title:
        k.upload_title = str(getattr(job, "upload_title", "") or "")
    if not k.channel:
        k.channel = str(getattr(job, "channel", "") or "")
    comp = getattr(job, "competitor", None)
    if k.competitor_file is None and comp is not None and Path(comp).is_file():
        k.competitor_file = Path(comp)
    k.mode = str(getattr(job, "mode", "") or k.mode)
    try:
        k.speed = float(getattr(job, "speed", None) or k.speed)
    except (TypeError, ValueError):
        pass
    raw = getattr(job, "raw", None)
    if raw is not None:
        k.raw_name = Path(raw).name
    res = getattr(job, "result", None) or {}
    k.why = str(res.get("label") or k.why) if isinstance(res, Mapping) else k.why
    return k


def form_message(k: Kept) -> str:
    """The New video form's line after it was filled in."""
    kept = [x for x, ok in (("the YouTube title", k.upload_title), ("the headline", k.title),
                            ("the channel", k.channel), ("the competitor", k.has_competitor)) if ok]
    what = ", ".join(kept[:-1]) + (" and " if len(kept) > 1 else "") + kept[-1] if kept else "nothing"
    old = f" ({k.raw_name} gave {k.why})" if k.raw_name and k.why else ""
    return f"Try with another RAW{old}: {what} kept -- paste or pick only the new RAW, then Make the video"
