"""eta.py: how far a run is and how long it still takes (night 6) -- "S5.2 visual search: 40 % -- about 6 min left;
whole run: about 25 min left" in the log, at most every 30 s.

``RunClock`` is a logging filter on the package logger (installed by pipeline.run; no stage imports this module, so
no cache key depends on it). It follows the stage lines the pipeline logs ('== <stage>' at the start, '   <stage>:
12.34s' at the end) and adds the estimate to the stage's progress / heartbeat lines. Each stage's time is predicted
from the video lengths (the RAW's for the RAW-bound stages: conform, proxies, the visual search's index; the
competitor's for the rest), the machine (its GPU) and the stage times of earlier runs on it (``history``:
work/run_times.jsonl, one line per finished run; stages answered from the cache are left out of the prediction), else
from built-in rates measured on the laptop (RTX 4060 Laptop GPU, --fast) -- a desktop GPU counts as faster. While a
stage runs, its share done comes from its own counter ('search 1/2: 64/248 tasks done'), else from the time spent
against the prediction.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[3]
HISTORY = REPO / "work" / "run_times.jsonl"
EVERY_S = 30.0
RAW_BOUND = ("S2 probe+conform", "S3 audio", "S5.1 audio align", "S3 proxies", "S5.2 visual search",
             "S5.2 visual search (refined box)")
# built-in seconds per second of video (RAW-bound: per RAW second; the rest: per competitor second), --fast on the
# laptop (output\laptop010 / 013: a 2-hour movie, a 24 s competitor); a thorough run's factor below
DEFAULT_RATE = {"S0 env": 0.02, "S2 probe+conform": 0.015, "S3 audio": 0.0012, "S5.1 audio align": 0.0015,
                "S3 proxies": 0.017, "S4 layout": 0.13, "S5.2 visual search": 0.015, "S5.3 refine": 1.1,
                "S5.2 visual search (refined box)": 0.002, "S5.3 refine (refined box)": 0.6, "S5.5 full-res re-check": 0.0,
                "S5.4-S6 segments+cutlist": 0.4, "S6 --fast comparison": 0.0, "S6 no-broll": 0.01, "S7 AE project": 0.0,
                "S8 exports": 1.9, "S8 captions": 1.2, "S8 speed + frame": 0.4, "S9 verify": 1.2, "S10 report": 0.01}
THOROUGH = {"S3 proxies": 2.0, "S5.2 visual search": 3.0, "S5.3 refine": 6.0, "S5.3 refine (refined box)": 6.0,
            "S5.5 full-res re-check": 4.0, "S5.4-S6 segments+cutlist": 20.0, "S6 --fast comparison": 25.0,
            "S8 exports": 2.5, "S8 captions": 5.0, "S9 verify": 15.0}
DESKTOP = 0.6               # a desktop GPU against the laptop one (the built-in rates only)
ORDER = ("S0 env", "S2 probe+conform", "S3 audio", "S5.1 audio align", "S3 proxies", "S4 layout", "S5.2 visual search",
         "S5.3 refine", "S5.2 visual search (refined box)", "S5.3 refine (refined box)", "S5.5 full-res re-check",
         "S5.4-S6 segments+cutlist", "S6 --fast comparison", "S6 no-broll", "S7 AE project", "S8 exports",
         "S8 captions", "S8 speed + frame", "S9 verify", "S10 report")
NESTED = ("S8.preview", "S8.compare")          # inside 'S8 exports': not counted twice


def minutes(s: float) -> str:
    s = max(0.0, float(s))
    if s < 90:
        return "under 2 min" if s >= 45 else "under a minute"
    return f"about {int(round(s / 60.0))} min"


def machine() -> str:
    """The machine as its GPU's name ('cpu' without one)."""
    try:
        from . import gpu
        return str(gpu.device_name() or "cpu")
    except Exception:  # noqa: BLE001
        return "cpu"


def history(path: Path = HISTORY, limit: int = 400) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def predict(stage: str, comp_s: float, raw_s: float, fast: bool, mach: str, past: Sequence[dict]) -> float:
    """Seconds ``stage`` should take: the median rate of the earlier runs on this machine in the same mode where it was
    computed (not answered from the cache), else the built-in rate."""
    size = raw_s if stage in RAW_BOUND else comp_s
    rates = []
    for h in past:
        if h.get("machine") != mach or bool(h.get("fast")) != bool(fast):
            continue
        st = (h.get("stages") or {}).get(stage)
        hs = float(h.get("raw_s") if stage in RAW_BOUND else h.get("comp_s") or 0.0)
        if st is None or stage in (h.get("cached") or ()) or hs <= 0:
            continue
        rates.append(float(st) / hs)
    if rates:
        return statistics.median(rates[-12:]) * size
    r = DEFAULT_RATE.get(stage, 0.0) * (1.0 if fast else THOROUGH.get(stage, 1.0))
    if "laptop" not in mach.lower() and mach != "cpu":
        r *= DESKTOP
    return r * size


_COUNT = re.compile(r"(?:(\d+)/(\d+):\s*)?(\d+)/(\d+) (?:tasks|frames) done")


class RunClock(logging.Filter):
    """The run's clock (module docstring): add the estimate to a stage line at most every EVERY_S seconds."""

    def __init__(self, comp_s: float, raw_s: float, fast: bool, compare_fast: bool = False, premiere: bool = True,
                 mach: str | None = None, past: Sequence[dict] | None = None, every_s: float = EVERY_S):
        super().__init__()
        self.comp_s, self.raw_s, self.fast = float(comp_s), float(raw_s), bool(fast)
        self.mach = mach or machine()
        past = list(history() if past is None else past)
        self.n_past = sum(1 for h in past if h.get("machine") == self.mach and bool(h.get("fast")) == self.fast)
        self.pred = {s: predict(s, self.comp_s, self.raw_s, self.fast, self.mach, past) for s in ORDER}
        if fast:
            self.pred["S5.5 full-res re-check"] = 0.0
        if not compare_fast or fast:
            self.pred["S6 --fast comparison"] = 0.0
        if not premiere:
            self.pred["S8 speed + frame"] = 0.0
        self.done: dict[str, float] = {}
        self.cached: set[str] = set()
        self.labels: dict[str, set[str]] = {}       # the counters seen per stage (several: its phases)
        self.stack: list[tuple[str, float]] = []
        self.t0 = time.monotonic()
        self.last = 0.0
        self.every_s = float(every_s)
        self.lock = threading.Lock()

    # -- the estimate --------------------------------------------------------------------------------------------
    def total(self) -> float:
        return sum(self.pred.values())

    def remaining(self, frac: float | None = None) -> tuple[str, float | None, float | None, float]:
        """(the current stage, its share done, its seconds left, the whole run's seconds left)."""
        now = time.monotonic()
        cur = next((s for s, _ in reversed(self.stack) if s in self.pred), None)
        cur_t0 = next((t for s, t in reversed(self.stack) if s == cur), now)
        spent = now - cur_t0
        k = self.pace()
        left_cur, share = None, None
        if cur is not None:
            p = self.pred.get(cur, 0.0) * k
            if frac is not None and frac > 0.02:
                share = min(0.99, frac)
                left_cur = spent * (1.0 - share) / share
            elif p > 0 and spent < 0.9 * p:
                share, left_cur = spent / p, p - spent
            elif p > 0 or spent > 5.0:              # longer than predicted: a quarter of what it took so far
                share, left_cur = 0.8, 0.25 * spent
        after = [s for s in ORDER if s not in self.done and s != cur]
        if cur is not None and cur in ORDER:
            after = [s for s in ORDER[ORDER.index(cur) + 1:] if s not in self.done]
        rest = k * sum(self.pred.get(s, 0.0) for s in after) + (left_cur or 0.0)
        return cur or "", share, left_cur, rest

    def pace(self) -> float:
        """How this run goes against the prediction: the finished stages' real time / predicted (the stages that
        were computed and predicted to take 5 s or more; 0.33-4), 1 before any."""
        real = sum(v for s, v in self.done.items() if s not in self.cached and self.pred.get(s, 0.0) >= 5.0)
        want = sum(self.pred[s] for s in self.done if s not in self.cached and self.pred.get(s, 0.0) >= 5.0)
        return min(4.0, max(0.33, real / want)) if want > 0 and real > 0 else 1.0

    def text(self, frac: float | None = None) -> str:
        cur, share, left_cur, rest = self.remaining(frac)
        bits = []
        if cur and share is not None:
            bits.append(f"{cur}: {100 * share:.0f} %" + (f" -- {minutes(left_cur)} left" if left_cur is not None else ""))
        bits.append(f"whole run: {minutes(rest)} left")
        return "; ".join(bits)

    def start_line(self) -> str:
        how = (f"from {self.n_past} earlier run(s) on this machine" if self.n_past else
               "from built-in times (no earlier run on this machine yet)")
        return (f"time estimate: the whole run about {max(1, int(round(self.total() / 60.0)))} min ({self.mach}, "
                f"{'--fast' if self.fast else 'thorough'}, competitor {self.comp_s:.0f} s, RAW {self.raw_s / 60.0:.0f} "
                f"min; {how})")

    # -- the log -------------------------------------------------------------------------------------------------
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._see(record)
        except Exception:  # noqa: BLE001 - the clock never breaks the log
            pass
        return True

    def _see(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.INFO:
            return
        msg = record.getMessage()
        with self.lock:
            if msg.startswith("== "):
                self.stack.append((msg[3:].strip(), time.monotonic()))
                return
            m = re.match(r"^ {3}(.+): ([\d.]+)s$", msg)
            if m and self.stack and m.group(1) == self.stack[-1][0]:
                name, _ = self.stack.pop()
                if name not in NESTED:
                    self.done[name] = float(m.group(2))
                return
            if "cache hit" in msg and self.stack:
                self.cached.add(self.stack[-1][0])
            if not self.stack or time.monotonic() - self.last < self.every_s:
                return
            cur = self.stack[-1][0]
            if not msg.startswith(cur + ":") and not msg.startswith(cur + " ("):
                return
            frac = None
            c = _COUNT.search(msg)
            if c:
                n, m_ = int(c.group(3)), max(1, int(c.group(4)))
                label = re.sub(r"\d+/\d+", "#", msg[len(cur) + 1:c.start()]).strip(" :")
                seen = self.labels.setdefault(cur, set())
                seen.add(label)
                if c.group(1):
                    i, k = int(c.group(1)), max(1, int(c.group(2)))
                    frac = ((i - 1) + n / m_) / k
                elif len(seen) == 1:                 # one counter for the whole stage (a stage of several phases --
                    frac = n / m_                    # eval, measure, final -- restarts it: the time is used then)
            self.last = time.monotonic()
            record.msg = f"{msg} | {self.text(frac)}"
            record.args = ()

    def record(self, timings: dict, path: Path = HISTORY, extra: dict | None = None) -> None:
        """Append this run's stage times to the history (best effort; never from the unit tests)."""
        import os
        stages = {k: round(float(v), 2) for k, v in (timings or {}).items() if k in ORDER}
        if not stages or "PYTEST_CURRENT_TEST" in os.environ:
            return
        quick = {k for k, v in stages.items() if self.pred.get(k, 0.0) >= 5.0 and v < 0.15 * self.pred[k]}
        row = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": self.mach, "fast": self.fast,
               "comp_s": round(self.comp_s, 2), "raw_s": round(self.raw_s, 2), "stages": stages,
               "cached": sorted((self.cached | quick) & set(stages)), **(extra or {})}   # (a stage done in a moment:
        #                                                                               the cache answered it)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass


def estimate_run(comp_s: float, raw_s: float, fast: bool, mach: str | None = None,
                 past: Sequence[dict] | None = None) -> float:
    """Seconds a whole run of these lengths should take (batch: the time left over its videos)."""
    clock = RunClock(comp_s, raw_s, fast, mach=mach, past=past)
    return clock.total()


def install(cfg: Any, comp_path: str, raw_path: str) -> RunClock | None:
    """The run's clock on the package logger (pipeline.run), its first estimate logged; None when the lengths cannot
    be read."""
    from .common import log
    try:
        from .cli import quick_probe
        comp_s = float(quick_probe(comp_path)["duration"]) if comp_path else 0.0
        raw_s = float(quick_probe(raw_path)["duration"])
    except Exception:  # noqa: BLE001 - no estimate then
        return None
    clock = RunClock(comp_s, raw_s, bool(getattr(cfg, "fast", False)), bool(getattr(cfg, "compare_fast", False)),
                     bool(getattr(cfg, "premiere", False)))
    log.addFilter(clock)
    log.info("%s", clock.start_line())
    return clock


def uninstall(clock: RunClock | None, timings: dict | None = None) -> None:
    from .common import log
    if clock is None:
        return
    log.removeFilter(clock)
    if timings:
        clock.record(timings)
