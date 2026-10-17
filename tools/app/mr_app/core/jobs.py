"""The queue's jobs and how dropped files become jobs.

* a RAW and a competitor dropped together -> one job (the RAW: a file named raw.*, else the longest video -- the batch's
  own rule, match_cuts.batch.split_folder; one click swaps them);
* one video dropped alone waits for the next one dropped alone: the two make a job;
* a folder of video folders (like input\\videos with video1, video2 ...) -> a batch: one job per subfolder (each of
  its competitors: match_cuts.batch.find_videos); a folder with the videos themselves in it -> its job(s).
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
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
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def complete(self) -> bool:
        return self.competitor is not None and self.raw is not None

    def swap(self) -> None:
        """The RAW and the competitor change places (the app guessed wrong)."""
        self.competitor, self.raw = self.raw, self.competitor
        self.comp_s, self.raw_s = self.raw_s, self.comp_s

    def problems(self) -> list[str]:
        """What looks wrong before the start (plain words): no RAW, the competitor longer than the RAW."""
        out = []
        if self.competitor is None or self.raw is None:
            out.append("only one video: drop its RAW (or its competitor) too")
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


def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTS


def videos_in(folder: Path) -> list[Path]:
    return sorted((f for f in folder.iterdir() if is_video(f)), key=lambda f: f.name.lower())


def pick_raw(vids: Sequence[Path], seconds: Callable[[Path], float | None]) -> tuple[Path | None, list[Path]]:
    """(the RAW, the competitors) of videos that belong together: raw.* is the RAW; else the longest (the batch's
    rule); the length unknown -> the bigger file."""
    vids = list(vids)
    if not vids:
        return None, []
    named = [v for v in vids if v.stem.lower() == "raw"]
    if len(named) == 1:
        return named[0], [v for v in vids if v != named[0]]
    if len(vids) == 1:
        return None, vids
    secs = {v: seconds(v) for v in vids}
    comp_named = [v for v in vids if v.stem.lower().startswith("competitor")]
    if any(s is None for s in secs.values()) and len(vids) == 2 and len(comp_named) == 1:
        return next(v for v in vids if v not in comp_named), comp_named     # a length unknown: the names say
    raw = max(vids, key=lambda v: (secs[v] if secs[v] is not None else -1.0, v.stat().st_size))
    return raw, [v for v in vids if v != raw]


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


@dataclass
class DropResult:
    jobs: list[Job]
    waiting: Path | None            # a single video waiting for its partner (None: none)
    notes: list[str]                # what was skipped and why (plain words)


def jobs_from_drop(paths: Sequence[Path], seconds: Callable[[Path], float | None], waiting: Path | None = None,
                   find_videos: Callable[[Path], tuple[list, list[str]]] | None = None,
                   speed_of: Callable[[Path], tuple[float, str]] | None = None) -> DropResult:
    """The jobs of one drop (module docstring). ``waiting``: a single video from an earlier drop (it pairs with a
    single video of this one). ``find_videos``: match_cuts.batch.find_videos (folder of folders); ``speed_of``:
    match_cuts.batch.video_speed of a competitor's folder."""
    jobs: list[Job] = []
    notes: list[str] = []
    loose: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            vids = videos_in(p)
            subs = [d for d in sorted(p.iterdir(), key=lambda d: d.name.lower()) if d.is_dir() and videos_in(d)]
            if subs and find_videos is not None:
                found, skipped = find_videos(p)
                for name, comp, raw in found:
                    jobs.append(Job(name, Path(comp), Path(raw), source=f"folder {p.name}"))
                notes += [f"skipped {s}" for s in skipped]
            if vids:
                raw, comps = pick_raw(vids, seconds)
                if raw is None:
                    notes.append(f"{p.name}: only one video ({vids[0].name}) -- it needs the RAW and a competitor")
                for c in comps if raw is not None else []:
                    jobs.append(Job(p.name if len(comps) == 1 else f"{p.name}_{c.stem}", c, raw,
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
        raw, comps = pick_raw(loose, seconds)
        for c in comps:
            jobs.append(Job(job_name(c, raw), c, raw))
    for j in jobs:
        j.comp_s = seconds(j.competitor) if j.competitor is not None else None
        j.raw_s = seconds(j.raw) if j.raw is not None else None
        if speed_of is not None and j.competitor is not None:
            j.speed, why = speed_of(j.competitor.parent)
            j.speed_from = why.strip("; ").replace("from ", "")
    return DropResult(jobs, waiting, notes)


def name_jobs(jobs: Sequence[Job], taken: Iterable[str]) -> None:
    """Every job a name no other job in the queue has (video4, video4-2 ...)."""
    used = list(taken)
    for j in jobs:
        j.name = unique_name(j.name, used)
        used.append(j.name)
