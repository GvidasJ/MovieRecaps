"""One queue job run as ``python -m match_cuts batch`` runs a video (match_cuts/batch.py main): the same command line
(--premiere, --fast unless Thorough, the video's --speed), its own run folder (output\\<name>, -2 when it exists), the
RAW a run of the same RAW converted reused, the console kept in extras\\batch_console.log, the result read with the
batch's checks. No window opens: the tool runs hidden, its progress read from its log."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from . import paths
from .jobs import FAST, Job, forced, short_raw

CREATE_NO_WINDOW = 0x08000000


def command(python: Path, comp: Path, raw: Path, run_dir: Path, work: Path, fast: bool, speed: float,
            frame: Path | None = None, watermark: Path | None = None, force_inputs: bool = False,
            no_swap: bool = False, allow_short_raw: bool = False, music: list[str] | None = None) -> list[str]:
    """The batch's command line for one video (match_cuts.batch.main); night 10: a channel's template as the frame
    and its watermark on top (``--frame`` / ``--watermark``) when the job has a channel; night 11: ``force_inputs``
    ("Run anyway" after a WRONG PAIR) skips the quick pair check. Day 12: ``no_swap`` -- you chose which file is the
    RAW, so the tool never swaps them (with --force-inputs alone it swaps two files that look reversed);
    ``allow_short_raw`` -- the New video form's RAW field chose it: only "almost as long as the RAW" is skipped.
    Night 15: ``music`` -- the channel's song switches (channels.Music.args: SceneProof's song on A2)."""
    return [str(python), "-m", "match_cuts", "--competitor", str(comp), "--raw", str(raw), "--run-dir", str(run_dir),
            "--work", str(work), "--premiere", *(["--fast"] if fast else []), "--speed", f"{speed:g}",
            *(["--frame", str(frame)] if frame else []), *(["--watermark", str(watermark)] if watermark else []),
            *(["--force-inputs"] if force_inputs else []), *(["--no-swap"] if no_swap else []),
            *(["--allow-short-raw"] if allow_short_raw else []), *(music or [])]


def channel_music(job: Job) -> list[str]:
    """The tool's --music switches of the job's channel (night 15: SceneProof's song), [] without one.

    28 Oct (your call): no song is added any more, on any channel -- [] unless MOVIERECAPS_MUSIC=1 (the channels'
    "music" settings are kept for that switch)."""
    if os.environ.get("MOVIERECAPS_MUSIC", "").strip() in ("", "0"):
        return []
    try:
        from .flow import find_channel
        ch = find_channel(getattr(job, "channel", "") or "")
    except Exception:  # noqa: BLE001 - no channel, no song
        return []
    m = getattr(ch, "music", None) if ch is not None else None
    return m.args() if m is not None else []


def kill_tree(pid: int) -> None:
    """Stop a process and everything it started (ffmpeg, the GPU workers): taskkill /T /F."""
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                       creationflags=CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001 - already gone
        pass


class JobRun:
    """Runs one job; ``converted``: RAW -> the copy an earlier run of the queue converted (shared by the queue)."""

    def __init__(self, job: Job, out_dir: Path | None = None, work_dir: Path | None = None,
                 python: Path | None = None, converted: dict | None = None, frame: Path | None = None,
                 watermark: Path | None = None):
        from match_cuts import batch
        self.job = job
        self.out = Path(getattr(job, "out_dir", None) or out_dir or paths.output_dir())
        self.work = Path(work_dir or paths.work_dir())
        self.python = Path(python or paths.python_exe())
        self.converted = converted if converted is not None else {}
        self.resuming = False
        from . import pause as PZ
        if getattr(job, "resume", False) and PZ.can_resume(getattr(job, "run_dir", None)):
            self.run_dir, self.resuming = Path(job.run_dir), True      # night 15: Resume -- the same run folder
        else:
            self.run_dir = batch.run_folder_for(self.out, getattr(job, "run_name", "") or job.name)
        raw = Path(job.raw)
        self.use_raw = self.converted.get(raw.resolve(), raw)
        self.frame, self.watermark = frame, watermark
        self.cmd = command(self.python, Path(job.competitor), self.use_raw, self.run_dir, self.work,
                           job.mode == FAST, float(job.speed), frame, watermark,
                           forced(job), bool(getattr(job, "roles_chosen", False)), short_raw(job), channel_music(job))
        if self.resuming:
            self.cmd.append("--resume")
        self.proc: subprocess.Popen | None = None
        self.stopped = False
        self.paused = False
        self.t0 = 0.0
        self._err: list[str] = []
        self._out_path = self.out / f".{self.run_dir.name}.app.stdout"
        self._fo = None
        self._th: threading.Thread | None = None

    def start(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        self._fo = self._out_path.open("w", encoding="utf-8", errors="replace")
        self.t0 = time.time()
        self.proc = subprocess.Popen(self.cmd, cwd=str(paths.repo()), stdout=self._fo, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", env=env,
                                     creationflags=CREATE_NO_WINDOW)
        self._th = threading.Thread(target=lambda: self._err.append(self.proc.stderr.read() if self.proc.stderr
                                                                    else ""), daemon=True)
        self._th.start()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def progress_text(self) -> str:
        from match_cuts import batch
        try:
            return batch.latest_progress(self.run_dir)
        except Exception:  # noqa: BLE001 - the log is being written
            return ""

    def elapsed(self) -> float:
        return time.time() - self.t0 if self.t0 else 0.0

    def activity(self) -> tuple[object, float | None]:
        """(what the run wrote last: its log's size and its stdout's, the CPU seconds of its processes -- None when
        they cannot be read) for the "seems stuck" check (night 15)."""
        from match_cuts import run_folders
        sizes = []
        for p in (self.run_dir / run_folders.EXTRAS / "match_cuts.log", self._out_path):
            try:
                sizes.append(p.stat().st_size)
            except OSError:
                sizes.append(-1)
        cpu = None
        if self.proc is not None and self.proc.poll() is None:
            try:
                import psutil
                root = psutil.Process(self.proc.pid)
                cpu = 0.0
                for p in [root, *root.children(recursive=True)]:
                    try:
                        t = p.cpu_times()
                        cpu += float(t.user + t.system)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:  # noqa: BLE001 - no psutil / the process just ended: output only
                cpu = None
        return tuple(sizes), cpu

    def reading(self) -> tuple[float, float, float] | None:
        """What the run's ffmpeg is converting (night 15, the console): (input seconds read, the input's seconds,
        x real time) -- from the bytes the ffmpeg process has read of its ``-i`` file against that file's size; None
        when no ffmpeg runs, its input is small (< 50 MB) or its length is unknown."""
        if self.proc is None or self.proc.poll() is not None:
            return None
        try:
            import psutil
            kids = psutil.Process(self.proc.pid).children(recursive=True)
        except Exception:  # noqa: BLE001
            return None
        for p in kids:
            try:
                if not p.name().lower().startswith("ffmpeg"):
                    continue
                args = p.cmdline()
                src = Path(args[args.index("-i") + 1]) if "-i" in args else None
                if src is None or not src.is_file() or src.stat().st_size < 50_000_000:
                    continue
                total = self._length(src)
                if not total:
                    continue
                frac = min(1.0, p.io_counters().read_bytes / float(src.stat().st_size))
                took = max(1.0, time.time() - p.create_time())
                return frac * total, total, frac * total / took
            except Exception:  # noqa: BLE001 - it just ended / no access: the next one
                continue
        return None

    def _length(self, f: Path) -> float | None:
        cache = self.__dict__.setdefault("_lengths", {})
        if str(f) not in cache:
            try:
                from match_cuts.cli import quick_probe
                cache[str(f)] = float(quick_probe(f)["duration"])
            except Exception:  # noqa: BLE001
                cache[str(f)] = None
        return cache[str(f)]

    def pause(self, step: str = "", plain: str = "") -> None:
        """Night 15: stop the run so that Resume goes on from this step (core.pause): the note in its folder first."""
        from . import pause as PZ
        self.paused = True
        try:
            PZ.mark(self.run_dir, step, plain)
        except OSError:
            pass
        self.stop()

    def stop(self) -> None:
        self.stopped = True
        if self.proc is not None and self.proc.poll() is None:
            kill_tree(self.proc.pid)

    def wait(self, poll: Callable[["JobRun"], None] | None = None, every_s: float = 2.0) -> dict:
        """Until the run ends (``poll`` called every ``every_s``); then its result (core.results.read_result)."""
        from match_cuts import batch, run_folders
        from .results import read_result
        assert self.proc is not None
        while True:
            try:
                self.proc.wait(timeout=every_s)
                break
            except subprocess.TimeoutExpired:
                if poll is not None:
                    poll(self)
        if self._th is not None:
            self._th.join(5.0)
        if self._fo is not None:
            self._fo.close()
        code, stderr = self.proc.returncode, "".join(self._err)
        try:
            stdout = self._out_path.read_text(encoding="utf-8", errors="replace")
            self._out_path.unlink()
        except OSError:
            stdout = ""
        extras = self.run_dir / run_folders.EXTRAS
        log_dir = extras if extras.is_dir() else self.out
        log = log_dir / ("batch_console.log" if log_dir == extras else f"{self.run_dir.name}.batch.log")
        try:
            log.write_text(" ".join(self.cmd) + "\n\n" + stdout + "\n" + stderr, encoding="utf-8")
        except OSError:
            pass
        if batch.finished(code, stdout, self.run_dir) and not self.stopped:     # finished (results.FINISHED)
            raw = Path(self.job.raw).resolve()
            if raw not in self.converted:
                cr = batch.converted_raw(self.run_dir)
                if cr is not None:
                    self.converted[raw] = cr
        res = read_result(self.run_dir, code, stdout, stderr, self.stopped)
        if self.paused:
            from . import pause as PZ
            # stopped by the pause, not at its end: no "match_cuts result:" line (exit code 1 alone can be either)
            if PZ.can_resume(self.run_dir) and batch.RESULT_LINE not in stdout:
                res.update(label=PZ.LABEL, paused=True, run_dir=str(self.run_dir))
            else:                                   # it ended by itself just before the pause: what it gave
                try:
                    PZ.mark_path(self.run_dir).unlink()
                except OSError:
                    pass
                res = read_result(self.run_dir, code, stdout, stderr, False)
        res.update(exit=code, seconds=round(self.elapsed(), 1), log=str(log), command=" ".join(self.cmd))
        try:                                    # day 13: what the run was given (a stop can be compared with a run)
            from . import inputs as IN
            res["inputs"] = IN.given(self.job, Path(self.job.competitor), Path(self.use_raw))
            if res.get("label") == "WRONG PAIR" and getattr(self.job, "project", None) is not None:
                hint = IN.copies_hint(self.job.project)          # the project's copies are not your files?
                if hint:
                    res["failures"] = list(res.get("failures") or []) + [hint]
        except Exception:  # noqa: BLE001 - a help, never a stop
            pass
        return res
