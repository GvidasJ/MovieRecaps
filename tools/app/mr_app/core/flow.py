"""A video project's steps, each callable on its own (night 10, part 1 of 2): download -> convert -> title check ->
template -> edit. Every step writes its state into the project's project.json (projects.py: waiting / running / done /
failed / skipped, the time, the computer, the details, an error in plain words), so the app's tabs, ``steps.py`` on
the command line and part 2's project board (D:\\Shared\\Projects, both computers) all run the same code and read the
same status.

* download: the competitor's link and the RAW's link with yt-dlp (download.py; both at once), or the RAW file you
  dropped placed in the folder (a hard link on the same drive, else a copy);
* convert: both files made Premiere-friendly (convert.py: kept / rewrapped to MP4 / visually lossless H.264), each
  checked (frames and length) before its original is deleted; the longer video is the RAW (the tool's own rule);
* title check: the competitor's headline read from its video (title_check.py) and my title compared with it;
* template: the channel's template with my headline (channels.py), 1080x1920, ``template.png`` in the folder;
* edit: match cuts (the batch's command, run.py) with that template as the frame and the channel's watermark, into
  ``edit`` in the project folder (``edit-2`` ... when run again).

``progress(role, share 0..1, text)`` is called often by the long steps; ``cancel`` is a threading.Event."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import projects as P

Progress = Callable[[str, float, str], None]
EDIT_NAME = "edit"                 # the edit's run folder inside the project folder
TEMPLATE_PNG = "template.png"


class StepError(RuntimeError):
    """A step failed; the message is in plain words (it is also the step's error in project.json)."""


def _say(progress: Progress | None, role: str, share: float, text: str) -> None:
    if progress is not None:
        try:
            progress(role, max(0.0, min(1.0, float(share))), text)
        except Exception:  # noqa: BLE001 - the window's callback must never stop a step
            pass


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


# ---------------------------------------------------------------------------------------------------------------------
# a new project
# ---------------------------------------------------------------------------------------------------------------------

def new_project(competitor_url: str, raw_url: str | None = None, raw_file: str | Path | None = None,
                channel: str | None = None, title: str | None = None, root: str | Path | None = None,
                name: str | None = None) -> P.Project:
    """A new project folder for a competitor link and a RAW link or file (named after the competitor's title when
    ``name`` gives it, else its link)."""
    if not (competitor_url or "").strip():
        raise StepError("paste the competitor's link")
    if not (raw_url or "").strip() and not raw_file:
        raise StepError("paste the RAW's link or drop the RAW file")
    if raw_file and not Path(raw_file).is_file():
        raise StepError(f"the RAW file is not there: {raw_file}")
    p = P.create(root, competitor_url=competitor_url.strip(), raw_url=(raw_url or "").strip() or None,
                 raw_file=str(raw_file) if raw_file else None, name=name)
    p.update(channel=channel or None, title=title or None)
    return p


# ---------------------------------------------------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------------------------------------------------

def _share(a: Any, b: Any = None) -> float:
    """progress(done, total) or progress(share) -> share."""
    try:
        if b:
            return float(a) / float(b)
        return float(a)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def download_step(p: P.Project, progress: Progress | None = None, cancel: threading.Event | None = None) -> dict:
    """Both files into the project folder (download.download_project: the competitor first, then the RAW from its
    link or its dropped file; the step's state in project.json). yt-dlp is updated first, at most once a day."""
    from . import download as D
    try:
        note = D.update_ytdlp()                       # at most once a day; offline keeps the installed one
    except Exception as e:  # noqa: BLE001
        note = f"yt-dlp not updated ({type(e).__name__})"
    _say(progress, "competitor", 0.0, "starting ...")
    _say(progress, "raw", 0.0, "waits for the competitor's download")
    try:
        out = D.download_project(p, progress=progress, cancel=cancel)
    except D.Cancelled as e:
        raise StepError("stopped by you") from e
    except D.DownloadError as e:
        p.reload()                                     # its error names the file: "RAW: private video"
        raise StepError(p.step("download").get("error") or getattr(e, "plain", str(e))) from e
    p.update(ytdlp=note)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------------------------------------------------

def convert_step(p: P.Project, progress: Progress | None = None, cancel: threading.Event | None = None,
                 gpu: str = "auto") -> dict:
    """Both files made Premiere-friendly, one after the other (convert.convert_project: kept / rewrapped / encoded,
    checked, the original deleted after the check; the step in project.json, each file's details under its role).
    Then the longer video is the RAW (the tool's rule): swapped when the links were pasted the other way round."""
    from . import convert as C
    try:
        out = C.convert_project(p, progress=progress, cancel=cancel, gpu=gpu)
    except C.Cancelled as e:
        raise StepError("stopped by you") from e
    except C.ConvertError as e:
        p.reload()
        raise StepError(p.step("convert").get("error") or str(e)) from e
    _longer_is_raw(p)
    return out


def convert_text(r: dict) -> str:
    """One line for a converted file (convert_project's details of a role): 'ready -- kept as it is' ..."""
    how = r.get("how")
    if how == "keep":
        return "ready -- already H.264 MP4, kept as it is"
    if how == "rewrap":
        return "ready -- rewrapped to MP4 (no re-encode, lossless)"
    if how == "encode":
        enc = r.get("encoder") or "H.264"
        src = str(r.get("from") or r.get("from_codec") or "").upper()
        return f"ready -- {src} converted to visually lossless H.264 ({enc}), checked"
    return "ready"


def _swap_roles(d: dict) -> None:
    """project.json's two files change roles, and what the steps say about each of them."""
    d["competitor"], d["raw"] = d.get("raw"), d.get("competitor")
    for s in ("download", "convert"):
        st = (d.get("steps") or {}).get(s)
        if isinstance(st, dict) and ("competitor" in st or "raw" in st):
            st["competitor"], st["raw"] = st.get("raw"), st.get("competitor")


def chosen_roles(data: dict) -> dict | None:
    """Day 12: your choice of which file is the RAW, as project.json keeps it ({'raw': file name, 'competitor': file
    name, 'how': 'picked' | 'swapped', 'at': time}), or None when you have not chosen."""
    r = (data or {}).get("roles")
    return r if isinstance(r, dict) and r.get("chosen") and r.get("raw") else None


def set_roles(folder: str | Path, raw_name: str, how: str = "picked") -> bool:
    """Day 12: you said the file ``raw_name`` (a file of the project folder) is the RAW -- project.json's roles switch
    when needed (the files stay where they are: nothing is copied again) and your choice is kept ("roles"), so later
    steps never swap them back. True when the roles switched."""
    p = P.load(folder)
    out = {"swapped": False}

    def change(d: dict) -> None:
        comp = (d.get("competitor") or {}).get("file") if isinstance(d.get("competitor"), dict) else None
        raw = (d.get("raw") or {}).get("file") if isinstance(d.get("raw"), dict) else None
        if comp == raw_name and raw != raw_name:
            _swap_roles(d)
            out["swapped"] = True
            comp, raw = raw, comp
        elif raw != raw_name:
            raise StepError(f"{raw_name} is not one of the project's two videos")
        d["roles"] = {"chosen": True, "raw": raw, "competitor": comp, "how": how, "at": P.now()}
        d.pop("swapped", None)                         # the app's own swap note no longer applies
    p.change(change)
    return out["swapped"]


def _longer_is_raw(p: P.Project) -> bool:
    """The tool's rule: the longer video is the RAW. Swaps the two in project.json when the competitor's is longer
    (a link pasted in the other field); True when it swapped. Never when you chose the RAW yourself (day 12)."""
    if chosen_roles(p.data):
        return False
    try:
        from match_cuts.cli import quick_probe
        c, r = p.file("competitor"), p.file("raw")
        dc, dr = float(quick_probe(c)["duration"]), float(quick_probe(r)["duration"])
    except Exception:  # noqa: BLE001 - unknown lengths: as given
        return False
    if dc <= dr + 0.5:
        return False

    def swap(d: dict) -> None:                    # the two files, and what the steps say about each
        _swap_roles(d)
        d["swapped"] = (f"the competitor's link gave the longer video ({dc:.0f} s against {dr:.0f} s): it is the RAW "
                        "(the tool's rule), so the two were swapped")
    p.change(swap)
    return True


# ---------------------------------------------------------------------------------------------------------------------
# title check, template
# ---------------------------------------------------------------------------------------------------------------------

def title_check_step(p: P.Project, title: str | None = None, progress: Progress | None = None,
                     reread: bool = False) -> dict:
    """The competitor's headline (read once and kept in project.json) and my title compared with it."""
    from . import title_check as T
    st = p.step("title_check")
    headline = st.get("competitor_headline")
    if headline is None or reread:
        comp = p.file("competitor")
        if comp is None or not comp.is_file():
            raise StepError("the competitor's video is not in the folder yet")
        p.set_step("title_check", P.RUNNING)
        rd = T.read_headline(comp, progress=lambda a, b: _say(progress, "competitor", _share(a, b),
                                                               "reading the competitor's headline ..."))
        headline = rd.text if rd is not None else ""
        why = "" if rd is not None else T.last_reason()
        sure = bool(getattr(rd, "sure", True)) if rd is not None else True
        where = getattr(rd, "where", "") if rd is not None else ""
    else:
        why, sure, where = st.get("why_none", ""), st.get("sure", True), st.get("where", "")
    mine = title if title is not None else (p.data.get("title") or "")
    chk = T.check(mine, headline)
    res = {"competitor_headline": headline, "why_none": why, "sure": sure, "where": where, "title": mine,
           "share": round(chk.share, 3), "percent": chk.percent, "warn": chk.warn, "common": chk.common,
           "message": chk.message}
    p.set_step("title_check", P.DONE, **res)
    return res


def find_channel(name: str | None):
    """The channel (channels.py) of that name, or None."""
    if not name:
        return None
    from . import channels as Ch
    for c in Ch.channels():
        if c.name.lower() == str(name).lower():
            return c
    return None


def template_step(p: P.Project, channel: str | None = None, title: str | None = None) -> dict:
    """The channel's template with my headline: template.png (1080x1920) in the project folder."""
    from . import channels as Ch
    ch_name = channel or p.data.get("channel")
    mine = title if title is not None else p.data.get("title")
    ch = find_channel(ch_name)
    if ch is None:
        p.set_step("template", P.SKIPPED, error=f"no channel {ch_name!r}" if ch_name else "no channel chosen")
        raise StepError(f"no channel {ch_name!r}" if ch_name else "choose the channel first")
    if not (mine or "").strip():
        p.set_step("template", P.FAILED, error="type the title first")
        raise StepError("type the title first")
    p.update(channel=ch.name, title=mine)
    p.set_step("template", P.RUNNING)
    out = p.folder / TEMPLATE_PNG
    try:
        res = Ch.make_template(ch, mine, out)
    except Exception as e:  # noqa: BLE001
        p.set_step("template", P.FAILED, error=f"{type(e).__name__}: {e}")
        raise StepError(f"the template could not be made: {e}") from e
    info = template_info(res)
    p.set_step("template", P.DONE, png=TEMPLATE_PNG, channel=ch.name, title=mine, **info)
    return {"png": str(out), **info}


def template_info(res: Any) -> dict:
    """The parts of channels.make_template's result project.json keeps (lines, shrunk, scale, why, warnings)."""
    lay = getattr(res, "layout", None)
    lines = list(getattr(lay, "lines", None) or getattr(res, "lines", None) or [])
    return {"lines": [str(x) for x in lines], "shrunk": bool(getattr(res, "shrunk", False)),
            "scale_pct": getattr(res, "scale_pct", None), "why": getattr(res, "why", "") or "",
            "warnings": list(getattr(res, "warnings", None) or [])}


# ---------------------------------------------------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------------------------------------------------

def edit_job(p: P.Project, fast: bool = True, speed: float = 100.0):
    """The Match cuts job of a project (its run folder goes into the project folder)."""
    from .jobs import FAST, THOROUGH, Job
    c, r = p.file("competitor"), p.file("raw")
    if c is None or r is None or not c.is_file() or not r.is_file():
        raise StepError("the competitor and the RAW are not both in the folder (download and convert first)")
    j = Job(p.folder.name, c, r, mode=FAST if fast else THOROUGH, speed=float(speed), source=f"project {p.name}")
    j.project = p.folder
    j.out_dir = p.folder
    j.run_name = EDIT_NAME
    j.channel = p.data.get("channel") or ""
    j.title = p.data.get("title") or ""
    j.roles_chosen = chosen_roles(p.data) is not None      # day 12: you said which one is the RAW
    return j


def edit_step(p: P.Project, fast: bool = True, speed: float = 100.0,
              poll: Callable[[Any], None] | None = None) -> dict:
    """match cuts on the project (the batch's command) with its template and the channel's watermark; waits for it.
    Its result (PASS / OK with a note / FAIL ...) and run folder go into project.json."""
    from .run import JobRun
    job = edit_job(p, fast, speed)
    frame, watermark = template_for_job(job)
    run = JobRun(job, frame=frame, watermark=watermark)
    p.set_step("edit", P.RUNNING, run_dir=str(run.run_dir), mode=job.mode, speed=job.speed)
    run.start()
    res = run.wait(poll)
    record_edit(p, res)
    return res


def template_for_job(job: Any) -> tuple[Path | None, Path | None]:
    """(the frame PNG, the watermark PNG) of a job with a channel: the template with its title (made now into the
    project folder, or work\\app_templates for a job without a project); (None, None) without a channel (the
    tool's default frame, no watermark)."""
    from . import paths
    ch = find_channel(getattr(job, "channel", "") or "")
    if ch is None:
        return None, None
    from . import channels as Ch
    title = (getattr(job, "title", "") or "").strip()
    proj = getattr(job, "project", None)
    if proj is not None:
        out = Path(proj) / TEMPLATE_PNG
    else:
        out = paths.work_dir() / "app_templates" / f"{job.name}_{ch.name}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
    res = Ch.make_template(ch, title, out)
    if proj is not None:
        try:
            P.load(proj).set_step("template", P.DONE, png=TEMPLATE_PNG, channel=ch.name, title=title,
                                  **template_info(res))
        except Exception:  # noqa: BLE001 - the status is a convenience
            pass
    wm = Path(ch.watermark) if getattr(ch, "watermark", None) else None
    return out, (wm if wm is not None and wm.is_file() else None)


def record_edit(p: P.Project, res: dict) -> None:
    """The edit's result in project.json."""
    label = res.get("label", "FAIL")
    state = P.DONE if label in ("PASS", "OK with a note") else (P.FAILED if label != "STOPPED" else P.WAITING)
    p.set_step("edit", state, result=label, run_dir=res.get("run_dir"), seconds=res.get("seconds"),
               failures=list(res.get("failures") or [])[:5], notes=list(res.get("notes") or [])[:5],
               **({"error": (res.get("failures") or [label])[0]} if state == P.FAILED else {}))


# ---------------------------------------------------------------------------------------------------------------------
# all of it
# ---------------------------------------------------------------------------------------------------------------------

def prepare(p: P.Project, progress: Progress | None = None, cancel: threading.Event | None = None) -> dict:
    """download -> convert -> title check (-> template when a channel and a title are set): everything before the
    edit. Returns {'title_check': ..., 'template': ... or None}."""
    t0 = time.time()
    if p.step("download").get("state") != P.DONE:
        download_step(p, progress, cancel)
    if p.step("convert").get("state") != P.DONE:
        convert_step(p, progress, cancel)
    tc = None
    try:
        tc = title_check_step(p, progress=progress)
    except Exception as e:  # noqa: BLE001 - the check is a help, never a stop
        p.set_step("title_check", P.FAILED, error=f"{type(e).__name__}: {e}")
    tpl = None
    if p.data.get("channel") and (p.data.get("title") or "").strip():
        try:
            tpl = template_step(p)
        except Exception:  # noqa: BLE001 - the edit makes it again at its start
            tpl = None
    return {"title_check": tc, "template": tpl, "seconds": round(time.time() - t0, 1)}
