"""The queue's jobs and how dropped files become jobs.

* a RAW and a competitor dropped together -> one job. Which one is the RAW: the tool's own rule (night 11,
  match_cuts.pairing -- the batch and the command line use it too): names win over length -- a file named raw* is the
  RAW, comp* / competitor* files are competitors; else the longer one when the lengths are more than 20 % apart; else
  a portrait one next to a landscape one is the competitor (said in the notes); else the drop gives a question
  (``DropResult.ask``: "Which one is the RAW?" -- ``Ask.jobs(raw)`` makes the job once you picked). One click swaps
  them anyway;
* one video dropped alone waits for the next one dropped alone: the two make a job;
* a folder of video folders (like input\\videos with video1, video2 ...) -> a batch: one job per subfolder (each of
  its competitors: match_cuts.batch.find_videos); a folder with the videos themselves in it -> its job(s).
"""
from __future__ import annotations

import inspect
import itertools
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".flv", ".wmv"}
FAST, THOROUGH = "fast", "thorough"

# job states
WAITING = "waiting"          # one video: waits for its partner (no RAW yet)
READY = "ready"
RUNNING = "running"
DONE = "done"
STOPPED = "stopped"

_ids = itertools.count(1)


@dataclass
class Job:
    name: str
    competitor: Path | None
    raw: Path | None
    mode: str = FAST
    speed: float = 100.0
    speed_from: str = ""            # "speed.txt" when the folder says so
    state: str = READY
    comp_s: float | None = None     # lengths (s), when known
    raw_s: float | None = None
    run_dir: Path | None = None
    result: dict = field(default_factory=dict)
    progress: str = ""
    percent: float = 0.0
    source: str = "drop"            # "drop" | "folder <name>" | "project <name>"
    channel: str = ""               # night 10: the channel's template (its headline: ``title``) as the frame and its
    title: str = ""                 #   watermark on top; "" = the default template, no watermark
    headline: str | None = None     # the competitor's headline (title check), once read
    project: Path | None = None     # a project folder (Download tab): the run goes into it, its status is written
    out_dir: Path | None = None     # where its run folder goes (None: the app's output folder)
    run_name: str = ""              # the run folder's name ("" = the job's name)
    force_inputs: bool = False      # night 11: run it although the quick check said WRONG PAIR (--force-inputs)
    roles_chosen: bool = False      # day 12: YOU said which file is the RAW (the check card, or Swap): never swapped
    #                                 back, and a pair about as long runs with the input checks skipped (forced())
    take: int = 1
    added: datetime | None = None   # when it joined the queue (the newest-first order of waiting rows)
    edited: datetime | None = None  # when its run ended (the newest-first order of finished rows)
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def complete(self) -> bool:
        return self.competitor is not None and self.raw is not None

    def swap(self) -> None:
        """The RAW and the competitor change places (the app guessed wrong)."""
        self.competitor, self.raw = self.raw, self.competitor
        self.comp_s, self.raw_s = self.raw_s, self.comp_s

    def problems(self) -> list[str]:
        """What looks wrong before the start (plain words): no RAW, the competitor longer than the RAW (not said
        once you chose the RAW of two videos about as long)."""
        out = []
        if self.competitor is None or self.raw is None:
            out.append("only one video: drop its RAW (or its competitor) too")
        elif self.roles_chosen and about_as_long(self.comp_s, self.raw_s):
            pass
        elif self.comp_s is not None and self.raw_s is not None and self.comp_s > self.raw_s + 0.5:
            out.append(f"the competitor ({clock(self.comp_s)}) is longer than the RAW ({clock(self.raw_s)}) -- "
                       "swapped? (the swap button changes them)")
        for p in (self.competitor, self.raw):
            if p is not None and not p.is_file():
                out.append(f"{p.name} is not there any more")
        return out


def clock(s: float | None) -> str:
    """12.3 -> '0:12', 1281.5 -> '21:22', 4900 -> '1:21:40'."""
    if s is None:
        return "?"
    s = int(round(float(s)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ---------------------------------------------------------------------------------------------------------------------
# day 12: RAW vs competitor is always your choice
# ---------------------------------------------------------------------------------------------------------------------

CLOSE = 0.7         # the app's rule: the shorter at least 70 % of the longer ("within 30 %") = about as long


def _named_raw(p: Path | None) -> bool:
    return p is not None and Path(p).stem.lower().startswith("raw")


def _named_comp(p: Path | None) -> bool:
    return p is not None and Path(p).stem.lower().startswith("comp")


def names_say(job: Job) -> bool:
    """The file names say which one is which: one of them is named raw* or comp* / competitor* (the tool's names
    rule, match_cuts.pairing)."""
    return any(_named_raw(p) or _named_comp(p) for p in (job.competitor, job.raw))


def about_as_long(a: float | None, b: float | None, close: float = CLOSE) -> bool:
    """Two lengths within 30 % of each other (the shorter at least ``close`` x the longer); False when one is not
    known."""
    if a is None or b is None:
        return False
    lo, hi = sorted((float(a), float(b)))
    return hi > 0 and lo >= close * hi


def needs_check(job: Job) -> bool:
    """The row must wait for YOU (the amber "Check which is the RAW" card): both videos there, their lengths known
    and within 30 % of each other, the names not saying, and you have not chosen yet. Such a row never starts by
    itself -- not with Automatic ON, not with Start."""
    return (job.complete and not job.roles_chosen and job.state == READY and not names_say(job)
            and about_as_long(job.comp_s, job.raw_s))


def forced(job: Job) -> bool:
    """Run with ``--force-inputs``: "Run anyway" was pressed, or you chose the RAW of a pair about as long (night 6's
    input check would stop a 47 s RAW for a 45 s Short as "almost as long as the RAW" -- your choice counts as Run
    anyway)."""
    return bool(job.force_inputs) or (job.roles_chosen and about_as_long(job.comp_s, job.raw_s))


def choose_raw(job: Job, raw: Path) -> bool:
    """You said ``raw`` is the RAW: the two change places when needed, and the choice is remembered on the job
    (``roles_chosen``). True when they changed places."""
    raw = Path(raw)
    if job.complete and Path(job.competitor) == raw:
        job.swap()
        swapped = True
    elif job.complete and Path(job.raw) == raw:
        swapped = False
    else:
        raise ValueError(f"{raw.name} is not one of this row's videos")
    job.roles_chosen = True
    return swapped


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', 23 -> '23rd'."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def runnable(job: Job) -> bool:
    """Waiting, both videos there, its files placed, its lengths read, and nothing for you to check."""
    return (job.state == READY and job.complete and not getattr(job, "preparing", "")
            and not getattr(job, "reading_lengths", False) and not needs_check(job))


def run_order(jobs: Sequence[Job]) -> list[Job]:
    """The waiting jobs in the order they will run: the queue's order (as added, or as you dragged them)."""
    return [j for j in jobs if j.state in (READY, WAITING)]


def display_order(jobs: Sequence[Job]) -> list[Job]:
    """Newest on top (day 12) -- the ORDER SHOWN, never the order run:

    1. running rows at the very top;
    2. then the waiting rows, newest first: the run order upside down -- so the next one to run is the lowest waiting
       row, and a row you drag stays exactly where you dropped it (its run place changes with it; each waiting row
       says "RUNS 1ST", "RUNS 2ND" ...);
    3. then the finished rows, the one finished last on top."""
    running = [j for j in jobs if j.state == RUNNING]
    waiting = list(reversed(run_order(jobs)))
    done = [j for j in jobs if j.state in (DONE, STOPPED)]
    order = {j.id: k for k, j in enumerate(jobs)}
    done.sort(key=lambda j: (j.edited or j.added or datetime.min, order[j.id]), reverse=True)
    return running + waiting + done


def run_places(jobs: Sequence[Job]) -> dict[int, int]:
    """{job id: 1, 2, ...}: when each waiting job runs (1 = next); jobs waiting for your check are left out."""
    out: dict[int, int] = {}
    for j in run_order(jobs):
        if j.state == READY and not needs_check(j):
            out[j.id] = len(out) + 1
    return out


def move_in_display(jobs: Sequence[Job], job_id: int, slot: int) -> list[Job]:
    """The queue's new run order after a waiting row was dropped at ``slot`` among the waiting rows AS SHOWN (0 = the
    top of the waiting rows): the waiting rows shown are the run order upside down, so the dragged row lands where it
    was dropped. Running and finished jobs keep their places in the list (only the waiting ones are re-filled)."""
    jobs = list(jobs)
    shown = list(reversed(run_order(jobs)))
    j = next((x for x in shown if x.id == job_id), None)
    if j is None:
        return jobs
    old = shown.index(j)
    slot = max(0, min(int(slot), len(shown)))
    if slot > old:
        slot -= 1
    shown.pop(old)
    shown.insert(slot, j)
    new_run = iter(reversed(shown))
    return [next(new_run) if x.state in (READY, WAITING) else x for x in jobs]


def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTS


def videos_in(folder: Path) -> list[Path]:
    return sorted((f for f in folder.iterdir() if is_video(f)), key=lambda f: f.name.lower())


def pick_pair(vids: Sequence[Path], seconds: Callable[[Path], float | None],
              size: Callable[[Path], tuple[int, int] | None] | None = None):
    """match_cuts.pairing.pick_pair: the RAW and its competitors of videos that belong together, why, or ``ask``."""
    from match_cuts.pairing import pick_pair as _pick
    return _pick(list(vids), seconds, size)


def pick_raw(vids: Sequence[Path], seconds: Callable[[Path], float | None],
             size: Callable[[Path], tuple[int, int] | None] | None = None) -> tuple[Path | None, list[Path]]:
    """(the RAW, the competitors) of videos that belong together, by the tool's rule (pick_pair; night 11: names win
    over length); (None, the video) for one video; (None, []) when it cannot tell (the drop asks you)."""
    vids = list(vids)
    if not vids:
        return None, []
    if len(vids) == 1:
        return (None, vids) if not vids[0].stem.lower().startswith("raw") else (vids[0], [])
    p = pick_pair(vids, seconds, size)
    return p.raw, list(p.competitors)


GENERIC = {"desktop", "downloads", "videos", "input", "documents", "new folder", "temp", "tmp", "shared"}


def job_name(comp: Path, raw: Path | None) -> str:
    """The run folder's name: the folder both videos are in (video4 for input\\videos\\video4\\comp.mp4), else the
    competitor's file name."""
    if raw is not None and comp.parent == raw.parent and comp.parent.name.lower() not in GENERIC \
            and comp.parent.name and not re.fullmatch(r"[A-Za-z]:\\?", comp.parent.name):
        return comp.parent.name
    return comp.stem


def unique_name(name: str, taken: Iterable[str]) -> str:
    taken = {t.lower() for t in taken}
    if name.lower() not in taken:
        return name
    k = 2
    while f"{name}-{k}".lower() in taken:
        k += 1
    return f"{name}-{k}"


def _fill(jobs: Sequence[Job], seconds: Callable[[Path], float | None] | None,
          speed_of: Callable[[Path], tuple[float, str]] | None) -> None:
    """Each job's lengths (when ``seconds`` is given) and its speed from its folder (``speed_of``)."""
    for j in jobs:
        if seconds is not None:
            j.comp_s = seconds(j.competitor) if j.competitor is not None else None
            j.raw_s = seconds(j.raw) if j.raw is not None else None
        if speed_of is not None and j.competitor is not None:
            j.speed, why = speed_of(j.competitor.parent)
            j.speed_from = why.strip("; ").replace("from ", "")


@dataclass
class Ask:
    """Videos where the app cannot tell which one is the RAW (match_cuts.pairing: the names don't say and the two are
    about as long): the question card "Which one is the RAW?" shows ``videos`` (two, rarely more) to click and ``why``;
    ``jobs(the clicked one)`` makes the job (one per competitor). ``name``: the folder's name when they came from one
    (the job is named after it), else ''."""
    videos: list[Path]
    why: str
    name: str = ""
    source: str = "drop"

    @property
    def pair(self) -> tuple[Path, Path]:
        """The first two candidates (video_a, video_b)."""
        return self.videos[0], self.videos[1]

    def jobs(self, raw: Path, seconds: Callable[[Path], float | None] | None = None,
             speed_of: Callable[[Path], tuple[float, str]] | None = None) -> list[Job]:
        """The job(s) once you said which video is the RAW: every other video is its competitor."""
        raw = Path(raw)
        if raw not in self.videos:
            raise ValueError(f"{raw.name} is not one of the videos asked about")
        comps = [v for v in self.videos if v != raw]
        out = [Job((self.name if len(comps) == 1 else f"{self.name}_{c.stem}") if self.name else job_name(c, raw),
                   c, raw, source=self.source) for c in comps]
        _fill(out, seconds, speed_of)
        return out


@dataclass
class DropResult:
    jobs: list[Job]
    waiting: Path | None            # a single video waiting for its partner (None: none)
    notes: list[str]                # what was skipped and why, and decisions worth saying (plain words)
    ask: list[Ask] = field(default_factory=list)    # night 11: "Which one is the RAW?" -- one question per pair


def _find(find_videos: Callable, folder: Path, notes: list[str], asks: list) -> tuple[list, list[str]]:
    """find_videos(folder) -- with the batch's own (match_cuts.batch.find_videos) also its decisions and the
    subfolders it cannot decide (asked instead of skipped)."""
    try:
        params = inspect.signature(find_videos).parameters
    except (TypeError, ValueError):
        params = {}
    if "asks" in params and "notes" in params:
        return find_videos(folder, notes=notes, asks=asks)
    return find_videos(folder)


def jobs_from_drop(paths: Sequence[Path], seconds: Callable[[Path], float | None], waiting: Path | None = None,
                   find_videos: Callable[[Path], tuple[list, list[str]]] | None = None,
                   speed_of: Callable[[Path], tuple[float, str]] | None = None,
                   size: Callable[[Path], tuple[int, int] | None] | None = None) -> DropResult:
    """The jobs of one drop (module docstring). ``waiting``: a single video from an earlier drop (it pairs with a
    single video of this one). ``find_videos``: match_cuts.batch.find_videos (folder of folders); ``speed_of``:
    match_cuts.batch.video_speed of a competitor's folder; ``size``: a video's (width, height) (default: read with the
    tool's quick probe when the rule needs it). Videos it cannot pair go into ``ask`` (the question card)."""
    jobs: list[Job] = []
    notes: list[str] = []
    asks: list[Ask] = []
    loose: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            vids = videos_in(p)
            subs = [d for d in sorted(p.iterdir(), key=lambda d: d.name.lower()) if d.is_dir() and videos_in(d)]
            if subs and find_videos is not None:
                found_asks: list = []
                found, skipped = _find(find_videos, p, notes, found_asks)
                for name, comp, raw in found:
                    jobs.append(Job(name, Path(comp), Path(raw), source=f"folder {p.name}"))
                notes += [f"skipped {s}" for s in skipped]
                asks += [Ask([Path(v) for v in cands], why, name, source=f"folder {p.name}")
                         for name, cands, why in found_asks]
            if vids:
                if len(vids) == 1:
                    notes.append(f"{p.name}: only one video ({vids[0].name}) -- it needs the RAW and a competitor")
                else:
                    pk = pick_pair(vids, seconds, size)
                    if pk.raw is None:
                        if pk.ask and len(pk.candidates) >= 2:
                            asks.append(Ask(list(pk.candidates), pk.why, p.name, source=f"folder {p.name}"))
                        else:
                            notes.append(f"{p.name}: {pk.why}")
                    else:
                        if pk.rule == "portrait":
                            notes.append(f"{p.name}: {pk.why}")
                        for c in pk.competitors:
                            jobs.append(Job(p.name if len(pk.competitors) == 1 else f"{p.name}_{c.stem}", c, pk.raw,
                                            source=f"folder {p.name}"))
            if not subs and not vids:
                notes.append(f"{p.name}: no video in it")
        elif is_video(p):
            loose.append(p)
        elif p.exists():
            notes.append(f"{p.name}: not a video")
    if waiting is not None and waiting.is_file() and waiting not in loose:
        loose = [waiting] + loose
    waiting = None
    if len(loose) == 1:
        waiting = loose[0]
    elif loose:
        pk = pick_pair(loose, seconds, size)
        if pk.raw is None:
            if pk.ask and len(pk.candidates) >= 2:
                asks.append(Ask(list(pk.candidates), pk.why))
            else:
                notes.append(pk.why)
        else:
            if pk.rule == "portrait":
                notes.append(pk.why)
            for c in pk.competitors:
                jobs.append(Job(job_name(c, pk.raw), c, pk.raw))
    _fill(jobs, seconds, speed_of)
    notes += [(f"{q.name}: " if q.name else "") + q.why for q in asks]    # said too (a window without the card)
    return DropResult(jobs, waiting, notes, asks)


def name_jobs(jobs: Sequence[Job], taken: Iterable[str]) -> None:
    """Every job a name no other job in the queue has (video4, video4-2 ...)."""
    used = list(taken)
    for j in jobs:
        j.name = unique_name(j.name, used)
        used.append(j.name)


# ---------------------------------------------------------------------------------------------------------------------
# night 11: a dropped RAW + competitor always get a project folder with both files in it
# ---------------------------------------------------------------------------------------------------------------------

def project_of(job: Job) -> Path | None:
    """The project folder both of a job's videos are in already (the folder holds a project.json: a Download tab
    project dropped on Match cuts), else None."""
    if job.competitor is None or job.raw is None:
        return None
    a, b = Path(job.competitor).parent, Path(job.raw).parent
    try:
        same = a.resolve() == b.resolve()
    except OSError:
        same = False
    return a if same and (a / "project.json").is_file() else None


def project_for_job(job: Job, root: Path | None = None, progress: Callable[[str, float, str], None] | None = None,
                    cancel=None, link: bool = False) -> Path:
    """A project folder for a job made of dropped files (a RAW + a competitor dropped on Match cuts -- not the batch's
    folder-of-folders drops), like the Download tab's: ``<root>\\<date>_<short name>\\`` (root: the projects folder,
    paths.projects_root()) with project.json, and BOTH videos in it: a copy of its own (download.take_local,
    ``link=False``: your files stay where they are, and replacing or deleting them later never touches the project --
    a hard link would be the very same file, changed with it when a new file is copied over the old name, e.g. the
    next raw.mp4 in Downloads); ``link=True``: a hard link on the same drive (no space; else a copy). The copy's
    progress goes to ``progress(role, share, text)``; ``cancel`` (a threading.Event) stops it. The job then points
    at the project: its competitor and RAW are the project's files, its run folder is ``<project>\\edit`` (edit-2 ...
    when run again), its result goes into project.json. Videos that are both in a project folder already: that project
    is used. Returns the project folder. Slow for a big RAW: call it off the window's thread."""
    from . import download as D
    from . import flow
    from . import projects as P
    if job.competitor is None or job.raw is None:
        raise ValueError("the job needs both videos: the RAW and the competitor")
    comp, raw = Path(job.competitor), Path(job.raw)
    for p in (comp, raw):
        if not p.is_file():
            raise FileNotFoundError(f"{p.name} is not there any more")
    have = Path(job.project) if job.project is not None else project_of(job)
    if have is not None and (have / P.FILE).is_file():
        pj = P.load(have)
        placed = {"competitor": comp, "raw": raw}
        if pj.file("competitor") is None or pj.file("raw") is None:
            pj.update(competitor={"file": comp.name}, raw={"file": raw.name})
        chosen = flow.chosen_roles(pj.data)            # day 12: you chose the RAW of this project before
        if chosen and chosen.get("raw") in (comp.name, raw.name):
            if chosen["raw"] == comp.name:
                placed = {"competitor": raw, "raw": comp}
                job.comp_s, job.raw_s = job.raw_s, job.comp_s
            job.roles_chosen = True
        elif job.roles_chosen:                         # chosen on the row before the project was made
            flow.set_roles(pj.folder, raw.name, "picked")
    else:
        pj = P.create(root, raw_file=raw, name=job.name or comp.stem)
        pj.set_step("download", P.RUNNING)
        placed, details = {}, {}
        try:
            for role, src in (("competitor", comp), ("raw", raw)):
                say = (lambda f, t, r=role: progress(r, f, t)) if progress else None
                log = pj.log_path("download", role)
                dst = D.take_local(src, pj.folder, progress=say, cancel=cancel, log_path=log, link=link)
                placed[role] = dst
                how = "linked" if _same_file(src, dst) else "copied"
                st = dst.stat()
                pj.update(**{role: {"file": dst.name, "title": src.stem, "source_file": str(src)}})
                details[role] = {"file": dst.name, "from": str(src), "how": how, "size": st.st_size,
                                 "mtime_ns": st.st_mtime_ns}
        except BaseException as e:
            pj.set_step("download", P.FAILED, error=f"the dropped files could not be placed: {e}", **details)
            raise
        pj.set_step("download", P.DONE, dropped=True, **details)
        if job.roles_chosen:                           # day 12: you chose the RAW on the row: remembered
            flow.set_roles(pj.folder, placed["raw"].name, "picked")
            pj.reload()
    pj.update(channel=job.channel or pj.data.get("channel") or None, title=job.title or pj.data.get("title") or None)
    job.project = pj.folder
    job.out_dir = pj.folder
    job.run_name = flow.EDIT_NAME
    job.competitor, job.raw = Path(placed["competitor"]), Path(placed["raw"])
    job.source = f"project {pj.folder.name}"
    return pj.folder


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False
