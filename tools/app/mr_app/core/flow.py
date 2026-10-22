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
                name: str | None = None, just_cuts: bool = False, competitor_file: str | Path | None = None,
                folder: str | None = None, names: dict | None = None) -> P.Project:
    """A new project folder for a competitor link and a RAW link or file (named after the competitor's title when
    ``name`` gives it, else its link). Day 12: ``just_cuts`` -- a RAW alone ("Just cuts": no competitor);
    ``competitor_file`` -- the competitor as a file (placed in the folder like a dropped RAW). Day 13: ``folder`` --
    the folder's exact name ('<date>_<title>', names.py), ``names`` -- project.json's "names" (its names follow its
    title from now on)."""
    if not (competitor_url or "").strip() and not competitor_file and not just_cuts:
        raise StepError("paste the competitor's link")
    if not (raw_url or "").strip() and not raw_file:
        raise StepError("paste the RAW's link or drop the RAW file")
    if raw_file and not Path(raw_file).is_file():
        raise StepError(f"the RAW file is not there: {raw_file}")
    if competitor_file and not Path(competitor_file).is_file():
        raise StepError(f"the competitor's file is not there: {competitor_file}")
    p = P.create(root, competitor_url=(competitor_url or "").strip() or None, raw_url=(raw_url or "").strip() or None,
                 raw_file=str(raw_file) if raw_file else None, name=name, folder=folder or None, names=names)
    p.update(channel=channel or None, title=title or None)
    if just_cuts:
        p.update(kind="just_cuts")
    if competitor_file:
        p.update(competitor={"source_file": str(competitor_file)})
    return p


def roles_of(p: P.Project) -> tuple[str, ...]:
    """The files a project has: ("competitor", "raw"), or ("raw",) for Just cuts (no competitor yet)."""
    c = p.data.get("competitor") or {}
    if c.get("url") or c.get("source_file") or c.get("file"):
        return ("competitor", "raw")
    return ("raw",)


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


def download_step(p: P.Project, progress: Progress | None = None, cancel: threading.Event | None = None,
                  roles: tuple[str, ...] | None = None) -> dict:
    """Both files into the project folder (download.download_project: the competitor first, then the RAW from its
    link or its dropped file; the step's state in project.json). yt-dlp is updated first, at most once a day.
    ``roles``: only these (day 12: ("raw",) for Just cuts; default: the files the project has)."""
    from . import download as D
    roles = tuple(roles) if roles else roles_of(p)
    links = [r for r in roles if (p.data.get(r) or {}).get("url")]
    note = ""
    if links:
        try:
            note = D.update_ytdlp()                       # at most once a day; offline keeps the installed one
        except Exception as e:  # noqa: BLE001
            note = f"yt-dlp not updated ({type(e).__name__})"
    if "competitor" in roles:
        _say(progress, "competitor", 0.0, "starting ...")
        _say(progress, "raw", 0.0, "waits for the competitor's download")
    else:
        _say(progress, "raw", 0.0, "starting ...")
    try:
        out = D.download_project(p, progress=progress, cancel=cancel, roles=roles)
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
                 gpu: str = "auto", roles: tuple[str, ...] | None = None) -> dict:
    """Both files made Premiere-friendly, one after the other (convert.convert_project: kept / rewrapped / encoded,
    checked, the original deleted after the check; the step in project.json, each file's details under its role).
    Then the longer video is the RAW (the tool's rule): swapped when the links were pasted the other way round."""
    from . import convert as C
    try:
        out = C.convert_project(p, progress=progress, cancel=cancel, gpu=gpu, roles=roles)
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
        lines = list(getattr(rd, "lines", None) or []) if rd is not None else []
    else:
        why, sure, where = st.get("why_none", ""), st.get("sure", True), st.get("where", "")
        lines = list(st.get("lines") or [])
    mine = title if title is not None else (p.data.get("title") or "")
    chk = T.check(mine, headline)
    res = {"competitor_headline": headline, "lines": lines, "why_none": why, "sure": sure, "where": where,
           "title": mine,
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
    j.roles_how = str((chosen_roles(p.data) or {}).get("how") or "")     # "form": the New video form's RAW field
    return j


def fill_lengths(job):
    """The job's two lengths when you chose its RAW: they decide --force-inputs / --allow-short-raw (jobs.forced /
    short_raw), as the queue's own thread reads them -- the command line's project step did not (day 12 review 3)."""
    if job.roles_chosen and (job.comp_s is None or job.raw_s is None):
        from match_cuts.cli import quick_probe
        for attr, path in (("comp_s", job.competitor), ("raw_s", job.raw)):
            if getattr(job, attr) is None:
                try:
                    setattr(job, attr, float(quick_probe(Path(path))["duration"]))
                except Exception:  # noqa: BLE001 - unknown: as before (the tool's own checks decide)
                    pass
    return job


def edit_step(p: P.Project, fast: bool = True, speed: float = 100.0,
              poll: Callable[[Any], None] | None = None) -> dict:
    """match cuts on the project (the batch's command) with its template and the channel's watermark; waits for it.
    Its result (PASS / OK with a note / FAIL ...) and run folder go into project.json."""
    from .run import JobRun
    job = fill_lengths(edit_job(p, fast, speed))
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
    wm = Path(ch.watermark) if getattr(ch, "watermark", None) else None
    wm = wm if wm is not None and wm.is_file() else None
    if proj is not None and not title:                 # day 12: no headline -- the channel's default template as it is
        png = Path(proj) / TEMPLATE_PNG
        try:
            if not png.is_file() or not P.load(proj).step("template").get("default"):
                default_template_step(P.load(proj), ch.name)
        except Exception:  # noqa: BLE001 - made below with an empty headline instead
            pass
        if png.is_file():
            return png, wm
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
    state = P.DONE if label in ("PASS", "OK with a note") else (P.FAILED if label not in ("STOPPED", "PAUSED") else
                                                                P.WAITING)          # (night 15: paused = waiting)
    p.set_step("edit", state, result=label, run_dir=res.get("run_dir"), seconds=res.get("seconds"),
               failures=list(res.get("failures") or [])[:5], notes=list(res.get("notes") or [])[:5],
               **({"inputs": res["inputs"]} if res.get("inputs") else {}),          # day 13: the two files it read
               # day 13: a stop's error is why in plain words and what to do (results.read_result's reason_line)
               **({"error": res.get("reason_line") or (res.get("failures") or [label])[0]}
                  if state == P.FAILED else {}))
    if state == P.DONE and res.get("run_dir"):
        title_copy(p, Path(res["run_dir"]))
        try:                                     # night 15: the zip to send (core.share_zip; never stops anything)
            from . import share_zip
            share_zip.make(Path(p.folder), Path(res["run_dir"]))
        except Exception:  # noqa: BLE001
            pass


def title_copy(p: P.Project, run_dir: Path) -> Path | None:
    """A finished edit of a project named after its title: the sequence in its 1_edit.xml called after the title
    without emoji, at once (premiere.edit_for_premiere -- day 14: no XML copy any more, Open in Premiere imports
    1_edit.xml itself; Premiere names the render after the sequence). Projects from before get nothing new here.
    Never raises."""
    try:
        from . import names as NM
        if not NM.uses_titles(p.data) or not (Path(run_dir) / "1_edit.xml").is_file():
            return None
        from .premiere import named_copy
        return named_copy(Path(run_dir) / "1_edit.xml")
    except Exception:  # noqa: BLE001 - Open in Premiere makes it anyway
        return None


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


# ---------------------------------------------------------------------------------------------------------------------
# day 12: the default template, Change headline, Just cuts
# ---------------------------------------------------------------------------------------------------------------------

def default_template(ch: Any, out_png: str | Path) -> Path:
    """The channel's template exactly as its template.psd shows it (its example headline kept: "the default template
    as it is"), 1080x1920, written to ``out_png``. A channel with a template.png (night 15): its example.png, else the
    template.png itself."""
    from PIL import Image

    from . import channels as Ch
    if Path(ch.psd).suffix.lower() == ".png":
        ex = Path(ch.folder) / Ch.EXAMPLE_NAME
        with Image.open(ex if ex.is_file() else ch.psd) as im:
            img = im.convert("RGBA")
    else:
        from psd_tools import PSDImage
        psd = PSDImage.open(ch.psd)
        img = psd.topil() or psd.composite()
    img = Ch.downscale(img.convert("RGBA"), Ch.SEQUENCE)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def default_template_step(p: P.Project, channel: str | None = None) -> dict:
    """No headline and Automatic ON (day 12): the channel's default template as it is -- template.png in the folder,
    the template step done and marked ``default`` (the row and the card say "default template")."""
    ch = find_channel(channel or p.data.get("channel"))
    if ch is None:
        p.set_step("template", P.SKIPPED, error="no channel chosen")
        raise StepError("choose the channel first")
    p.set_step("template", P.RUNNING)
    try:
        out = default_template(ch, p.folder / TEMPLATE_PNG)
    except Exception as e:  # noqa: BLE001
        p.set_step("template", P.FAILED, error=f"the default template could not be made: {e}")
        raise StepError(f"the channel's default template could not be made: {e}") from e
    p.update(channel=ch.name)
    p.set_step("template", P.DONE, png=TEMPLATE_PNG, channel=ch.name, title="", default=True,
               why="no headline: the channel's default template as it is")
    return {"png": str(out), "default": True}


CUTS_NAME = "cuts"                 # Just cuts' run folder inside the project folder


def run_dirs_of(p: P.Project) -> list[Path]:
    """The project's run folders (edit, edit-2 ..., cuts, cuts-2 ...) that hold a 1_edit.xml."""
    out = []
    for d in sorted(p.folder.glob(f"{EDIT_NAME}*")) + sorted(p.folder.glob(f"{CUTS_NAME}*")):
        if d.is_dir() and (d / "1_edit.xml").is_file():
            out.append(d)
    return out


def change_headline(p: P.Project, title: str, channel: str | None = None) -> dict:
    """Change headline (day 12): only template.png is made again with the new headline and swapped into the edits
    already made (each run's extras\\media\\frame.png: the file 1_edit.xml points at) -- no new run."""
    import shutil
    res = template_step(p, channel, title)
    src = p.folder / TEMPLATE_PNG
    swapped = []
    for d in run_dirs_of(p):
        fr = d / "extras" / "media" / "frame.png"
        if fr.is_file():
            shutil.copyfile(src, fr)
            swapped.append(d.name)
    try:
        from . import board
        board.set_title(p.folder, title)
    except Exception:  # noqa: BLE001 - the title is in project.json anyway
        pass
    p.set_step("template", P.DONE, png=TEMPLATE_PNG, channel=p.data.get("channel"), title=title, default=False,
               swapped_into=swapped, **{k: v for k, v in res.items() if k in ("lines", "shrunk", "scale_pct", "why",
                                                                            "warnings")})
    return {**res, "swapped_into": swapped}


def just_cuts_command(python: Path, raw: Path, run_dir: Path, work: Path, frame: Path | None = None,
                      watermark: Path | None = None) -> list[str]:
    """The tool's Just cuts command (match_cuts.just_cuts): the RAW cut at every scene change into 1_edit.xml."""
    return [str(python), "-m", "match_cuts.just_cuts", "--raw", str(raw), "--run-dir", str(run_dir),
            "--work", str(work), *(["--frame", str(frame)] if frame else []),
            *(["--watermark", str(watermark)] if watermark else [])]


def just_cuts_result(run_dir: Path, code: int | None, stderr: str = "", stopped: bool = False) -> dict:
    """{label, failures, notes, cuts, clips, run_dir} of a finished Just cuts run (its extras\\just_cuts.json)."""
    import json
    if stopped:
        return {"label": "STOPPED", "failures": [], "notes": [], "run_dir": str(run_dir)}
    try:
        info = json.loads((Path(run_dir) / "extras" / "just_cuts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    xml_ok = (Path(run_dir) / "1_edit.xml").is_file()
    errors = [str(e) for e in (info.get("errors") or [])]
    if code == 0 and xml_ok:
        label = "PASS"
    elif code in (0, 1) and xml_ok:
        label = "OK with a note"
    else:
        label = "FAIL"
    stop = ""
    why = None
    if label == "FAIL":
        from .newvideo import plain_error
        lines = [x.strip() for x in (stderr or "").splitlines() if x.strip()]
        mine = [x for x in lines if x.lower().startswith("match_cuts:") and " details in " not in f" {x} "]
        stop = (mine[-1].split(":", 1)[1].strip() if mine else
                plain_error("\n".join(lines)) if lines else f"the cuts were not made (exit {code})")
        try:                                  # day 13: a full disk, a crash ... in plain words with what to do --
            from match_cuts import plain_reason          # only for a technical line (its own plain words stay)
            from .newvideo import _TECHNICAL
            why = plain_reason.explain(stderr=stderr or "", code=code, stop="")
            if why.kind in (plain_reason.UNKNOWN, plain_reason.TOOL_STOP) or not (
                    _TECHNICAL.search(stop) or plain_reason.crash_words(code)):
                why = None
        except Exception:  # noqa: BLE001 - the tool's module not importable here: its own line
            why = None
        if why is not None:
            stop = why.line()
    out = {"label": label, "failures": ([stop] if stop else []) + (errors if label == "FAIL" else []),
           "notes": errors if label == "OK with a note" else [], "cuts": info.get("cuts"),
           "clips": info.get("clips"), "run_dir": str(run_dir)}
    if why is not None:
        out.update(reason=why.text, todo=why.todo, reason_line=why.line(), reason_kind=why.kind,
                   reason_detail=why.detail)
    return out


def just_cuts_step(p: P.Project, progress: Progress | None = None, cancel: threading.Event | None = None,
                   python: Path | None = None, work: Path | None = None) -> dict:
    """Just cuts (day 12): the whole RAW cut at every scene change into a Premiere edit in ``cuts`` (cuts-2 ...) in
    the project folder -- the sequence at the RAW's own rate, the audio chain on every audio clip, the channel's
    template when a headline is given (else the tool's default frame) and its watermark. No captions, no competitor.
    The result goes into project.json's edit step (``kind`` just_cuts)."""
    import os
    import subprocess

    from . import paths
    from .run import CREATE_NO_WINDOW, kill_tree
    raw = p.file("raw")
    if raw is None or not raw.is_file():
        raise StepError("the RAW is not in the folder (download and convert first)")
    frame = watermark = None
    ch = find_channel(p.data.get("channel"))
    title = (p.data.get("title") or "").strip()
    if ch is not None:
        wm = Path(ch.watermark) if getattr(ch, "watermark", None) else None
        watermark = wm if wm is not None and wm.is_file() else None
        if title:
            _say(progress, "raw", 0.01, "making your channel's template ...")
            template_step(p, ch.name, title)
            frame = p.folder / TEMPLATE_PNG
    k = 1
    while (p.folder / (CUTS_NAME if k == 1 else f"{CUTS_NAME}-{k}")).exists():
        k += 1
    run_dir = p.folder / (CUTS_NAME if k == 1 else f"{CUTS_NAME}-{k}")
    cmd = just_cuts_command(Path(python or paths.python_exe()), raw, run_dir, Path(work or paths.work_dir()),
                            frame, watermark)
    p.set_step("edit", P.RUNNING, run_dir=str(run_dir), kind="just_cuts")
    _say(progress, "raw", 0.02, "finding the scene changes ...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1",
               PYTHONPATH=os.pathsep.join(x for x in (str(paths.repo() / "tools" / "match_cuts"),
                                                      os.environ.get("PYTHONPATH", "")) if x))
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(paths.repo()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=env, creationflags=CREATE_NO_WINDOW)
    err: list[str] = []
    out: list[str] = []
    readers = [threading.Thread(target=lambda: err.append(proc.stderr.read() or ""), daemon=True),
               threading.Thread(target=lambda: out.append(proc.stdout.read() or ""), daemon=True)]
    for r in readers:
        r.start()
    stopped = False
    while proc.poll() is None:
        if _cancelled(cancel):
            stopped = True
            kill_tree(proc.pid)
            break
        took = time.time() - t0
        _say(progress, "raw", min(0.95, 0.05 + took / (took + 90.0)), f"cutting at every scene change ... "
             f"{int(took)} s")
        time.sleep(0.5)
    proc.wait()
    for r in readers:
        r.join(5.0)
    try:
        (run_dir / "extras").mkdir(parents=True, exist_ok=True)
        (run_dir / "extras" / "just_cuts_console.log").write_text(
            " ".join(cmd) + "\n\n" + "".join(out) + "\n" + "".join(err), encoding="utf-8")
    except OSError:
        pass
    res = just_cuts_result(run_dir, proc.returncode, "".join(err), stopped)
    res.update(seconds=round(time.time() - t0, 1), exit=proc.returncode)
    record_edit(p, res)
    p.change(lambda d: d.setdefault("steps", {}).setdefault("edit", {}).update(kind="just_cuts", cuts=res.get("cuts")))
    if stopped:
        raise StepError("stopped by you")
    return res


# ---------------------------------------------------------------------------------------------------------------------
# day 12, section 7: "Use as my headline" on a Match cuts queue row / a board card
# ---------------------------------------------------------------------------------------------------------------------

USE_SET, USE_RUNNING, USE_CHANGE = "set", "running", "change"


def headline_action(data: dict | None) -> str:
    """What "Use as my headline" does to a project (its project.json dict):

    * ``USE_SET`` -- no edit made yet (none started, or the last one failed / stopped): the project's headline is
      simply set; the edit's start makes template.png from it anyway (template_for_job), and a template.png made
      before is made again at once (set_headline);
    * ``USE_RUNNING`` -- the edit is running now: nothing is changed (the row says to wait for its end);
    * ``USE_CHANGE`` -- an edit is made: after a question, Change headline (only template.png again, swapped into the
      edit -- no new run)."""
    edit = ((data or {}).get("steps") or {}).get("edit") or {}
    state = edit.get("state") if isinstance(edit, dict) else None
    if state == P.RUNNING:
        return USE_RUNNING
    if state == P.DONE:
        return USE_CHANGE
    return USE_SET


def set_headline(folder: str | Path, title: str) -> dict:
    """``USE_SET``: the project's headline = ``title`` (project.json + title.txt, line break kept); when template.png
    was already made (and the channel is known) it is made again with it. {'title', 'template': bool, 'error'}."""
    from . import board
    board.set_title(Path(folder), title)
    p = P.load(folder)
    out = {"title": title, "template": False, "error": ""}
    if p.step("template").get("state") == P.DONE and (title or "").strip() \
            and find_channel(p.data.get("channel")) is not None:
        try:
            template_step(p, None, title)
            out["template"] = True
        except StepError as e:
            out["error"] = str(e)
    return out
